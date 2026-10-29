"""`embabel graph migrate --to embabel|postgres|oracle`: an appliance's knowledge graph moved to
another store, verified value for value, and the appliance switched over — with the store it came
from left exactly as it was, so going back is the old .env and `embabel up`.

TWO KINDS OF MOVE, ONE SHAPE.
  - From Neo4j: the server reads Neo4j over Bolt as its engine opens (GRAPH_EMBABEL_IMPORT_NEO4J_*)
    and writes the graph into whatever store the engine is configured with — SQLite, Postgres or
    Oracle alike, because the import writes through the engine.
  - Between the engine's own stores: the server restores the current store into its heap as a boot
    would, writes it whole into the empty new store with that store's full checkpoint — ids and the
    id counter kept — and compares the new store's rows with the heap and with the old store's rows
    (GRAPH_EMBABEL_IMPORT_STORE_*).
It has to be the server, as its engine opens: the server's own boot writes into its graph — the store
identity each world is stamped with, schema markers, reference data, the users a sign-in checks — so
an engine that had opened empty would already disagree with the graph arriving.

AROUND IT, in order: stops the app (a source server keeps running); keeps the old .env and
secrets.env beside the new; makes the target ready (a SQLite path set aside, never deleted; a bundled
Postgres or Oracle started, with a password generated into secrets.env); writes the new store into
.env; boots the app once with the import switched on and follows it to a verified report; boots it
again without; stops the source server cleanly. Anything short of a verified report puts both files
back, sets aside or removes what the attempt made, and starts the appliance on the store it had.
"""

from __future__ import annotations
import datetime
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from . import graphstores as stores
from .backup import BACKUP_HELPER_IMAGE
from .colour import TICK, dim, warn
from .core import MODE_SERVICE, SetupError
from .dockerlib import _compose, _docker, container_status, service_container
from .settings import (
    GRAPH_TYPE_VAR, embabel_command, env_file_value, env_path, graph_engine, set_env_var, volume_exists,
    volume_name,
)

DATA_VOLUME = "embabel_assistant_data"
GRAPH_DIR = "/data/graph"
NEO4J_REPORT = "neo4j-import.json"
STORE_REPORT = "store-import.json"
# What a SQLite store is on disk; set aside, never deleted, when SQLite is the target.
SQLITE_FILES = ("graph.sqlite", "graph.sqlite-wal", "graph.sqlite-shm", "memory.journal")
NEO4J_VARS = ("GRAPH_EMBABEL_IMPORT_NEO4J_URL", "GRAPH_EMBABEL_IMPORT_NEO4J_USERNAME",
              "GRAPH_EMBABEL_IMPORT_NEO4J_PASSWORD")
STORE_VARS = ("GRAPH_EMBABEL_IMPORT_STORE_KIND", "GRAPH_EMBABEL_IMPORT_STORE_PATH")
# The Neo4j source as the app container reaches it: the compose service, on the project's network.
NEO4J_URL = "bolt://neo4j:7687"
# A server with the importer writes its report within seconds of its engine opening; one that is up
# and answering with no report has no importer, and must not be left running on an empty engine.
NO_REPORT_GRACE_SECONDS = 240
IMPORT_TIMEOUT_SECONDS = 6 * 3600
POLL_SECONDS = 5


class MigrationFailed(SetupError):
    """The migration stopped; the appliance is back on the store it had and the message says why."""


@dataclass
class Move:
    """One migration, decided before anything changes."""
    mode: str
    target: str                       # sqlite, postgres or oracle
    source: str                       # neo4j, or the engine's current store
    url: str | None = None
    username: str | None = None
    counts: dict = field(default_factory=dict)
    neo4j: str | None = None          # the Neo4j container, when that is the source
    neo4j_password: str = ""

    @property
    def service(self) -> str:
        return MODE_SERVICE[self.mode]

    @property
    def report(self) -> str:
        return f"{GRAPH_DIR}/{NEO4J_REPORT if self.source == 'neo4j' else STORE_REPORT}"

    def describe(self) -> str:
        source = "Neo4j" if self.source == "neo4j" else f"the engine's {self.source} store"
        where = f" at {self.url}" if self.url else (" (bundled)" if self.target in stores.SERVER_SETTINGS else "")
        return f"from {source} into the engine's {self.target} store{where}"


def plan(mode: str, to: str, url: str | None, username: str | None) -> Move:
    """What `--to` asks of this appliance, refused before anything is touched when it cannot be done."""
    target = stores.TARGETS[to]
    if graph_engine() == "NEO4J":
        neo4j = service_container("neo4j", stopped=True)
        if not neo4j or not volume_exists("embabel_appliance_neo4j_data"):
            raise SetupError("No Neo4j graph found for this appliance — is it installed here?")
        if container_status(neo4j) != "running":
            _docker("start", neo4j, timeout=120)
        password = _neo4j_password(neo4j)
        return Move(mode, target, "neo4j", url, username, _neo4j_counts(neo4j, password), neo4j, password)
    if graph_engine() != "EMBABEL":
        raise SetupError(f"This appliance runs {graph_engine()}; a graph moves from Neo4j or between the Embabel engine's stores.")
    current = stores.current_store()
    if current == target:
        raise SetupError(f"The engine already keeps its graph in {target} — there is nothing to migrate. "
                         "To move it to another server of the same kind, move it to another store first.")
    return Move(mode, target, current, url, username)


def migrate(move: Move) -> dict:
    """Carry out [move]; returns the verified report, or rolls back and raises [MigrationFailed]."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backups = _keep(stamp)
    print("  Stopping the app" + (" — Neo4j stays up as the source." if move.source == "neo4j" else "."))
    _compose(move.mode, "stop", move.service, capture=True)
    created = move.target in stores.SERVER_SETTINGS and not stores.volume_held(move.target)
    try:
        _prepare_target(move, stamp)
        report = _import(move)
        _check(report, move)
    except SetupError as failure:
        _roll_back(move, backups, stamp, created)
        raise MigrationFailed(f"{failure}\n  The appliance is back on {move.source}; its graph was never changed.") from failure
    print(f"  Restarting without the import, on the {move.target} store alone.")
    _compose(move.mode, "up", "-d", move.service, capture=True)
    _stop_source(move)
    report["envBackup"] = backups[0]
    return report


def _keep(stamp: str) -> tuple[str, str | None]:
    """The old .env and secrets.env, kept beside the new ones: they are the way back."""
    env_backup = f"{env_path()}.before-graph-migrate-{stamp}"
    shutil.copyfile(env_path(), env_backup)
    secrets_backup = None
    if os.path.exists(stores.secrets_path()):
        secrets_backup = f"{stores.secrets_path()}.before-graph-migrate-{stamp}"
        shutil.copyfile(stores.secrets_path(), secrets_backup)
    return env_backup, secrets_backup


def _prepare_target(move: Move, stamp: str) -> None:
    """The target empty and reachable, and .env saying it is the engine's store."""
    _set_aside([move.report.rsplit("/", 1)[1], *(SQLITE_FILES if move.target == "sqlite" else ())], f"before-migrate-{stamp}")
    if move.source == "neo4j":
        note = ("# Written by `embabel graph migrate`; the Neo4j graph this appliance ran on is still in its "
                "volume, and GRAPH_TYPE=NEO4J goes back to it.",)
        set_env_var(GRAPH_TYPE_VAR, "EMBABEL", note)
    if stores.configure(move.target, move.url, move.username):
        stores.start_bundled(move.mode, move.target)
    if move.source in stores.SERVER_SETTINGS and stores.bundled_service(move.source):
        stores.ensure_running(move.source)


def _import_env(move: Move) -> dict:
    """The settings that switch the import on, for this one compose run."""
    if move.source == "neo4j":
        return dict(zip(NEO4J_VARS, (NEO4J_URL, "neo4j", move.neo4j_password)))
    from .pins import EMBABEL_JOURNAL, EMBABEL_SQLITE
    path = {"sqlite": env_file_value("GRAPH_EMBABEL_SQLITE_PATH") or EMBABEL_SQLITE,
            "snapshot": env_file_value("VC_EMBABEL_JOURNAL") or EMBABEL_JOURNAL}.get(move.source, "")
    return dict(zip(STORE_VARS, (move.source, path)))


def _import(move: Move) -> dict:
    """The one boot that copies: the import switched on for this compose run only, followed to its report."""
    switched = _import_env(move)
    os.environ.update(switched)
    try:
        print("  Starting the app with the import switched on.")
        run = _compose(move.mode, "up", "-d", "--no-deps", "--force-recreate", move.service)
        if run.returncode != 0:
            raise SetupError("docker compose up failed — see the output above.")
    finally:
        for var in switched:
            os.environ.pop(var, None)
    return _follow(move)


def _stop_source(move: Move) -> None:
    """The source stopped cleanly and kept. Neo4j and a bundled server get the time a clean shutdown
    takes: killed at docker's default ten seconds, they recover from their logs on the next start, and
    the graph kept for going back should not need to."""
    if move.neo4j:
        _docker("stop", "--time", "120", move.neo4j, timeout=240)
    elif move.source in stores.SERVER_SETTINGS:
        stores.stop_bundled(move.source)


def _roll_back(move: Move, backups: tuple[str, str | None], stamp: str, created: bool) -> None:
    print(f"  {warn('!')} Rolling back to {move.source}.")
    _compose(move.mode, "stop", move.service, capture=True)
    if move.target == "sqlite":
        _set_aside(list(SQLITE_FILES), f"failed-migrate-{stamp}")
    elif created:
        stores.remove_bundled(move.target)
    else:
        stores.stop_bundled(move.target)
    shutil.copyfile(backups[0], env_path())
    if backups[1]:
        shutil.copyfile(backups[1], stores.secrets_path())
    _compose(move.mode, "up", "-d", capture=True)


def _neo4j_password(container: str) -> str:
    """The password the Neo4j container was created with: its NEO4J_AUTH, `neo4j/<password>`."""
    run = _docker("inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", container)
    for line in (run.stdout.splitlines() if run and run.returncode == 0 else []):
        if line.startswith("NEO4J_AUTH=neo4j/"):
            return line.split("/", 1)[1]
    return env_file_value("NEO4J_PASSWORD") or "embabel-assistant"


def _cypher(container: str, password: str, statement: str) -> str:
    """One statement through the Neo4j container's own cypher-shell; the password goes by environment."""
    run = subprocess.run(
        ["docker", "exec", "-e", "NEO4J_PASSWORD", container, "cypher-shell", "-u", "neo4j",
         "--format", "plain", statement],
        capture_output=True, text=True, env={**os.environ, "NEO4J_PASSWORD": password}, timeout=600,
    )
    if run.returncode != 0:
        raise SetupError(f"Neo4j did not answer: {(run.stderr or run.stdout).strip()[:300]}")
    return run.stdout.strip().splitlines()[-1]


def _neo4j_counts(container: str, password: str) -> dict:
    return {
        "nodes": int(_cypher(container, password, "MATCH (n) RETURN count(n)")),
        "relationships": int(_cypher(container, password, "MATCH ()-[r]->() RETURN count(r)")),
    }


def _in_data_volume(script: str, timeout: int = 300) -> subprocess.CompletedProcess | None:
    return _docker("run", "--rm", "-v", f"{volume_name(DATA_VOLUME)}:/data", BACKUP_HELPER_IMAGE,
                   "sh", "-c", script, timeout=timeout)


def _set_aside(names: list[str], suffix: str) -> None:
    """Each of [names] on the engine's path moves to `<name>.<suffix>`: nothing anybody might want is
    deleted to make room."""
    moves = " ".join(f'[ -e "{f}" ] && mv "{f}" "{f}.{suffix}";' for f in names)
    _in_data_volume(f'mkdir -p {GRAPH_DIR} && cd {GRAPH_DIR} && {{ {moves} true; }}')


def _report(path: str) -> dict | None:
    run = _in_data_volume(f"cat {path} 2>/dev/null", timeout=60)
    if not run or run.returncode != 0 or not run.stdout.strip():
        return None
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError:
        return None


def _follow(move: Move) -> dict:
    """Wait for the report to say verified, failed or skipped, printing the server's progress as it goes."""
    started = time.monotonic()
    container = service_container(move.service, stopped=True)
    since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen = 0
    while time.monotonic() - started < IMPORT_TIMEOUT_SECONDS:
        time.sleep(POLL_SECONDS)
        seen = _print_progress(container, since, seen)
        report = _report(move.report)
        if report and report.get("status") in ("verified", "failed", "skipped"):
            return report
        if report is None and time.monotonic() - started > NO_REPORT_GRACE_SECONDS:
            raise SetupError("The server never started an import — this server image has no importer. "
                             f"Run `{embabel_command('upgrade')}`, then migrate again.")
        if container and container_status(container) in ("exited", "dead"):
            raise SetupError(f"The app stopped during the import — see `{embabel_command('logs')}`.")
    raise SetupError(f"The import did not finish within {IMPORT_TIMEOUT_SECONDS // 3600} hours.")


def _print_progress(container: str | None, since: str, seen: int) -> int:
    if not container:
        return seen
    run = _docker("logs", "--since", since, container, timeout=60)
    lines = [l for l in ((run.stdout or "") + (run.stderr or "")).splitlines() if "[graph-import]" in l] if run else []
    for line in lines[seen:]:
        print("    " + dim(line.split("[graph-import]", 1)[1].strip()))
    return max(seen, len(lines))


def _check(report: dict, move: Move) -> None:
    """The server's verdict, and for Neo4j the CLI's own: the counts Neo4j gave before the copy began."""
    if report.get("status") == "skipped":
        raise SetupError(f"The {move.target} store already holds a graph, so nothing was copied into it. "
                         "Point --url at an empty one, or empty it first.")
    if report.get("status") != "verified":
        groups = report.get("differences") or []
        detail = "; ".join(f"{d['group']}: {d['source']} in the source, {d['target']} copied"
                           + (" (values differ)" if d.get("contentDiffers") else "") for d in groups[:8])
        raise SetupError(f"The import did not verify: {report.get('message') or detail or 'see ' + move.report}")
    if move.source != "neo4j":
        return
    # What the engine does not keep across a restart (its query log) is counted, not copied: the
    # report says how much, and the totals must still come to what Neo4j held.
    skipped_nodes = sum((report.get("notCopied") or {}).values())
    skipped_rels = report.get("relationshipsNotCopied") or 0
    source = move.counts
    if (report.get("nodes", 0) + skipped_nodes != source["nodes"]
            or report.get("relationships", 0) + skipped_rels != source["relationships"]):
        raise SetupError(f"Neo4j held {source['nodes']:,} nodes and {source['relationships']:,} relationships; "
                         f"the import copied {report.get('nodes'):,} and {report.get('relationships'):,}, "
                         f"and left out {skipped_nodes:,} and {skipped_rels:,}.")


def summary(report: dict, move: Move) -> str:
    secs = report.get("seconds") or {}
    took = ", ".join(f"{k} {v:.0f}s" for k, v in secs.items())
    back = (f"set GRAPH_TYPE=NEO4J in {os.path.basename(env_path())}" if move.source == "neo4j"
            else f"put back {os.path.basename(report['envBackup'])} as {os.path.basename(env_path())}")
    return (f"  {TICK} Migrated and verified: {report['nodes']:,} nodes in {report['labelGroups']} label groups, "
            f"{report['relationships']:,} relationships of {report['relationshipTypes']} types, "
            f"{report['vectors']:,} vectors, every value compared ({took}).\n"
            + _not_copied(report) +
            f"    The engine now keeps its graph in {move.target}. The {move.source} graph is stopped and untouched.\n"
            f"    To go back: {back} (the old file is {os.path.basename(report['envBackup'])}), "
            f"then `{embabel_command('up')}`.")


def _not_copied(report: dict) -> str:
    """What was left behind because the engine never keeps it past a restart, said out loud."""
    skipped = report.get("notCopied") or {}
    if not skipped:
        return ""
    groups = ", ".join(f"{count:,} {group.strip(':')}" for group, count in sorted(skipped.items()))
    return (f"    Not copied: {groups} — the engine keeps these only while it runs (its query log is not\n"
            f"    durable), so they stay where they were rather than vanish at the next restart.\n")
