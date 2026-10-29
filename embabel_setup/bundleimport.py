"""`embabel import <file>`: a bundle (see bundle.py) taken into this appliance, whatever it keeps its graph in.

WHAT IT REPLACES. The graph and the worlds — they belong together: each world directory is stamped with the graph
it was made with, and the server refuses to start on a world stamped by another. This appliance's own settings
(.env, secrets.env, the admin directory with its credentials and provider keys) are kept: a bundle never carries
them. What it replaces is set aside, never deleted, and comes back if the import does not verify.

HOW THE GRAPH COMES IN. Through the server's own import at open — the store copy `embabel graph migrate` uses —
with the bundle's graph file as the source: the server restores it, writes it whole into this appliance's empty
store (SQLite, Postgres, Oracle; a Neo4j appliance moves onto the engine for it, Neo4j kept), and compares what it
wrote with the bundle, value for value. Before that, bundle-tool holds the file to the manifest's checksums, so a
damaged bundle is refused before anything here changes.

SIGNING IN AFTERWARDS. A bundle carries no passwords, and the imported graph's users are the bundle's. `--login
USER` sets a password for one of them here (asked for, or EMBABEL_IMPORT_PASSWORD), through the server's own
credential store, so whoever imported it can sign in as that user and see their world.
"""

from __future__ import annotations
import datetime
import getpass
import json
import os
import shutil
import subprocess
import tarfile

from . import graphstores as stores
from . import storetables
from .bundle import FORMAT, SHARED_SETUP, STAGING, WORLD_BASE, BundleError, decompressor, _bundle_tool, _helper, _helper_argv
from .core import MODE_SERVICE, SetupError
from .dockerlib import _compose, _docker, service_container
from .graphmigrate import SQLITE_FILES, STORE_REPORT, STORE_VARS, Move, _follow, _keep, _set_aside
from .settings import embabel_command, GRAPH_TYPE_VAR, env_file_value, env_path, graph_engine, set_env_var
from .versions import mode_image

# Replaced whole by an import: the worlds and their attachments, and what every world shares (bundle.SHARED_SETUP).
WORLD_DIRS = ("users", "chat-attachments", *SHARED_SETUP)


def read_manifest(path: str) -> dict:
    """The manifest, read from the front of the bundle without unpacking the rest."""
    try:
        with decompressor(path) as raw, tarfile.open(fileobj=raw, mode="r|") as tar:
            for member in tar:
                if member.name == "manifest.json":
                    return json.load(tar.extractfile(member))
    except (tarfile.TarError, OSError, EOFError) as e:
        raise BundleError(f"{path} is not an appliance bundle ({e}).")
    raise BundleError(f"{path} has no manifest — it is not an appliance bundle.")


def check_compatible(manifest: dict) -> None:
    if manifest.get("format") != FORMAT:
        raise BundleError(f"This bundle is format {manifest.get('format')}; this appliance reads format {FORMAT}. "
                          f"Upgrade whichever appliance is older (`{embabel_command('upgrade')}`) and export again.")


def import_bundle(path: str, mode: str, login: str | None = None, confirm=None) -> dict:
    """Take the bundle at [path] into this appliance; returns the server's verified report, with what else was done."""
    manifest = read_manifest(path)
    check_compatible(manifest)
    if confirm and not confirm(manifest):
        raise BundleError("Nothing was changed.")
    image = mode_image(mode)
    password = _ask_password(login) if login else None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = f"{STAGING}/import-{stamp}"
    try:
        _unpack(image, path, staging)
        intact = _bundle_tool(image, "graph", "check", "--bundle", f"{staging}/graph")
        if intact.returncode != 0:
            raise BundleError("The bundle's graph does not match its manifest — it was damaged or altered on the way. "
                              f"Nothing was changed. ({intact.stdout.decode(errors='replace').strip()[-300:]})")
        report = _take(mode, image, staging, stamp, manifest, lambda: _set_login(image, login, password) if login else None)
    finally:
        _helper(image, f"rm -rf {staging}")
    report["embedding"] = _embedding_note(manifest)
    return report


def _unpack(image: str, path: str, staging: str) -> None:
    """The bundle decompressed on the way in, straight into a staging directory on the data volume."""
    proc = subprocess.Popen(_helper_argv(image, f"mkdir -p {staging} && tar -xf - -C {staging}", stdin=True),
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    with decompressor(path) as src:
        while chunk := src.read(1 << 20):
            proc.stdin.write(chunk)
    proc.stdin.close()
    if proc.wait(timeout=3600) != 0:
        raise BundleError(f"The bundle could not be unpacked: {proc.stderr.read().decode(errors='replace').strip()[-300:]}")


def _take(mode: str, image: str, staging: str, stamp: str, manifest: dict, login) -> dict:
    """The graph and the worlds swapped in, under one rollback."""
    service = MODE_SERVICE[mode]
    backups = _keep(stamp)
    was_neo4j = graph_engine() == "NEO4J"
    print("  Stopping the app.")
    _compose(mode, "stop", service, capture=True)
    move = Move(mode, _target_store(), "bundle")
    prefix = _import_prefix(stamp)
    before = None
    try:
        _prepare_store(move.target, stamp, was_neo4j)
        if move.target != "sqlite":
            before = storetables.tables_under(image, move.target, prefix)
        _swap_worlds(image, staging, stamp)
        os.environ.update(dict(zip(STORE_VARS, ("sqlite", f"{staging}/graph/graph.sqlite"))))
        try:
            print("  Starting the app with the import switched on.")
            if _compose(mode, "up", "-d", "--no-deps", "--force-recreate", service).returncode != 0:
                raise SetupError("docker compose up failed — see the output above.")
        finally:
            for var in STORE_VARS:
                os.environ.pop(var, None)
        report = _follow(move)
        _check(report, manifest)
    except SetupError as failure:
        _roll_back(mode, service, image, backups, stamp, move.target)
        left = "" if move.target == "sqlite" else storetables.after_rollback(image, move.target, prefix, before)
        raise BundleError(f"{failure}\n  Nothing was kept: this appliance is back as it was.{left}") from failure
    # the credential is written while the app is down, so the server reads it at its next start
    _compose(mode, "stop", service, capture=True)
    report["login"] = login()
    print("  Restarting without the import.")
    _compose(mode, "up", "-d", service, capture=True)
    if was_neo4j:
        _stop_neo4j()
    report["setAside"] = f"*.before-import-{stamp}"
    return report


def _stop_neo4j() -> None:
    """A Neo4j appliance now runs on the engine: its Neo4j stops cleanly, its volume kept as the way back."""
    neo4j = service_container("neo4j", stopped=True)
    if neo4j:
        _docker("stop", "--time", "120", neo4j, timeout=240)


def _target_store() -> str:
    """Where the bundle's graph goes: the engine's store this appliance runs, or SQLite for a Neo4j appliance."""
    return "sqlite" if graph_engine() != "EMBABEL" else ("sqlite" if stores.current_store() == "snapshot" else stores.current_store())


def _prepare_store(target: str, stamp: str, was_neo4j: bool) -> None:
    """An empty store for the bundle: SQLite's files set aside; a server store given a table prefix of its own,
    so the tables it held stay where they are; a Neo4j or snapshot appliance moved onto the engine's SQLite."""
    _set_aside([STORE_REPORT, *(SQLITE_FILES if target == "sqlite" else ())], f"before-import-{stamp}")
    if was_neo4j:
        set_env_var(GRAPH_TYPE_VAR, "EMBABEL", ("# Written by `embabel import`; GRAPH_TYPE=NEO4J goes back to the graph that was here.",))
    if target == "sqlite":
        stores.configure("sqlite", None, None)
    else:
        if stores.bundled_service(target):
            stores.ensure_running(target)
        prefix_var = f"GRAPH_EMBABEL_{target.upper()}_PREFIX"
        set_env_var(prefix_var, _import_prefix(stamp),
                    (f"# Written by `embabel import`; the tables before it are under {env_file_value(prefix_var) or 'embabel_graph'}.",))


def _import_prefix(stamp: str) -> str:
    """The table prefix of its own an import writes a server store's graph under."""
    return f"{storetables.DEFAULT_PREFIX}_{stamp.replace('-', '_')}"


def _swap_worlds(image: str, staging: str, stamp: str) -> None:
    """This appliance's worlds set aside, the bundle's put in their place: a world stamped by another graph would stop
    the server, so the two never mix."""
    moves = " && ".join(
        f"{{ [ ! -e {d} ] || mv {d} {d}.before-import-{stamp}; }} && {{ [ ! -e {staging}/data/{d} ] || mv {staging}/data/{d} {d}; }}"
        for d in WORLD_DIRS)
    run = _helper(image, f"cd {WORLD_BASE} && {moves}")
    if run.returncode != 0:
        raise SetupError(f"The worlds could not be put in place: {run.stderr.decode(errors='replace').strip()[-300:]}")


def _check(report: dict, manifest: dict) -> None:
    if report.get("status") != "verified":
        raise SetupError(f"The import did not verify: {report.get('message') or report.get('differences') or report.get('status')}")
    graph = manifest.get("graph", {})
    if report.get("nodes") != graph.get("nodes") or report.get("relationships") != graph.get("relationships"):
        raise SetupError(f"The bundle holds {graph.get('nodes')} nodes and {graph.get('relationships')} relationships; "
                         f"the import wrote {report.get('nodes')} and {report.get('relationships')}.")


def _roll_back(mode: str, service: str, image: str, backups, stamp: str, target: str) -> None:
    print("  ! Rolling back.")
    _compose(mode, "stop", service, capture=True)
    if target == "sqlite":
        _set_aside(list(SQLITE_FILES), f"failed-import-{stamp}")
    undo = " ; ".join(f"{{ [ ! -e {d} ] || mv {d} {d}.failed-import-{stamp}; }} ; {{ [ ! -e {d}.before-import-{stamp} ] || mv {d}.before-import-{stamp} {d}; }}"
                      for d in WORLD_DIRS)
    _helper(image, f"cd {WORLD_BASE} && {undo}")
    shutil.copyfile(backups[0], env_path())
    if backups[1]:
        shutil.copyfile(backups[1], stores.secrets_path())
    _compose(mode, "up", "-d", capture=True)


def _ask_password(user: str) -> str:
    """Asked before anything stops, so an import never waits on a prompt with the app down."""
    password = os.environ.get("EMBABEL_IMPORT_PASSWORD") or getpass.getpass(f"  A password for {user} on this appliance: ")
    if not password:
        raise BundleError("No password given for --login; nothing was changed.")
    return password


def _set_login(image: str, user: str, password: str) -> str:
    """A password for one imported user, through the server's credential store; never echoed, never in a file here."""
    run = _bundle_tool(image, "credential", "--user", user, env={"EMBABEL_NEW_PASSWORD": password})
    if run.returncode != 0:
        return f"no password was set for {user}: {run.stderr.decode(errors='replace').strip()[-200:]}"
    return f"{user} can sign in here with the password just given"


def _embedding_note(manifest: dict) -> str | None:
    """Vectors made by one model searched with another answer nonsense, so a difference is said, with the repair."""
    theirs = (manifest.get("embedding") or {}).get("model")
    ours = env_file_value("ASSISTANT_EMBEDDING_MODEL")
    if not theirs or theirs == ours:
        return None
    return (f"The bundle's vectors were made by {theirs}; this appliance embeds with {ours or 'no model'}. Searches over "
            "them will be wrong until they are re-embedded: sign in as an administrator and POST "
            "/api/v1/admin/embedding/reindex with {\"force\": true}, or run "
            f"`{embabel_command('embeddings use ' + theirs)}` to search them with the model that made them.")
