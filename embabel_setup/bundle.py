"""`embabel export <file>` and `embabel import <file>`: an appliance's state — its graph, its worlds and its
setup — in one compressed file that any other appliance can take, whatever either of them keeps its graph in.

WHAT IS IN A BUNDLE.
  manifest.json      what made it, what it holds (counts and checksums per label group and relationship
                     type, vectors and their widths, the embedding model), and what was LEFT OUT and why
  graph/graph.sqlite the graph as the Embabel engine stores it: checkpointed, its journal empty. It is the
                     engine's own format, so every appliance reads it the same way, from any store to any store
  data/…             the worlds: each user's world directories, minus secrets (always) and private content
                     (with --no-private)
The graph is written by the server image's own copy paths (bundle-tool, which runs the phase-1 Neo4j import or
the store copy), so an export is checked value for value as it is made, and an import is checked again against
the manifest and against the bundle's own rows.

NEVER A SECRET. The credential file, provider keys, API keys, tokens, the setup token, per-world secrets.env —
selected out by name (bundlesecrets.py), and every file that IS selected is then scanned for the shapes
credentials take; a match stops the export and names the file, never the value.

LIVE OR COLD. An appliance on the engine's SQLite store (every new one) exports LIVE: SQLite's online backup is a
consistent copy of the graph as of one instant, so the app never stops. Any other graph — Neo4j, the snapshot
journal, Postgres, Oracle — is read across many statements, and a write landing between two of them would export a
graph that never existed, so the app stops for the graph's read (its database stays up) and starts again before
the worlds are copied.
"""

from __future__ import annotations
import datetime
import gzip
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time

from . import bundlesecrets
from . import graphstores as stores
from .core import MODE_SERVICE, SetupError
from .dockerlib import _compose, _docker, compose_project, container_status, service_container
from .graphmigrate import DATA_VOLUME, GRAPH_DIR, _neo4j_password
from .settings import embabel_command, env_file_value, graph_engine, volume_name
from .versions import mode_image

FORMAT = 1
WORLD_BASE = "/data/embabel/assistant"
STAGING = "/data/.bundle"
SUFFIX = ".tar.gz"
# Inside a world, what is never data worth sending: caches and spools the server rebuilds.
ALWAYS_LEFT_OUT = ("*/.embabel/codemode/*",)
# Beside the worlds, the setup every world on an appliance shares, carried by a whole-appliance bundle: which realms'
# code the owner admitted (no secret: grants name wallet entries, never their values), the code admitted, and custom
# themes. A bundle scoped to one world leaves them out — they speak for every world.
SHARED_SETUP = ("admin/realm-admissions-v1.json", "admin/realm-artifacts-v1", "admin/themes")
# Inside a world, what --no-private leaves out on disk; the graph's private content is BundleScope's.
PRIVATE_PATHS = ("*/data/notebooks/*", "*/data/tool-traces/*")


class BundleError(SetupError):
    """A bundle could not be made or taken; nothing on either appliance was changed by it."""


def _helper(image: str, script: str, *, env: dict | None = None, stdin=None, stdout=None, timeout=3600):
    """A one-shot container from the appliance's own image: its data volume mounted, on its network."""
    args = ["docker", "run", "--rm", "-i" if stdin else "", "--network", f"{compose_project()}_default",
            "-v", f"{volume_name(DATA_VOLUME)}:/data", "--entrypoint", "sh"]
    for key in (env or {}):
        args += ["-e", key]
    args = [a for a in args if a] + [image, "-c", script]
    return subprocess.run(args, stdin=stdin, stdout=stdout if stdout is not None else subprocess.PIPE,
                          stderr=subprocess.PIPE, env={**os.environ, **(env or {})}, timeout=timeout)


def _bundle_tool(image: str, *argv: str, env: dict | None = None) -> subprocess.CompletedProcess:
    script = "bundle-tool " + " ".join(shlex.quote(a) for a in argv)
    run = _helper(image, script, env=env)
    if run.returncode == 127 or b"bundle-tool: not found" in run.stderr:
        raise BundleError(f"This appliance's server image cannot write or read a bundle yet. Run `{embabel_command('upgrade')}` first.")
    return run


# ── export ────────────────────────────────────────────────────────────────────────────────────────────


def graph_source() -> tuple[list[str], bool, dict]:
    """The bundle-tool arguments that read this appliance's graph, whether that can be done live, and the
    environment that carries the source's password (never on a command line)."""
    if graph_engine() == "NEO4J":
        neo4j = service_container("neo4j", stopped=True)
        if not neo4j:
            raise BundleError("This Neo4j appliance has no Neo4j container to read the graph from.")
        if container_status(neo4j) != "running":
            _docker("start", neo4j, timeout=120)
        return ["--neo4j", "bolt://neo4j:7687"], False, {"EMBABEL_SOURCE_PASSWORD": _neo4j_password(neo4j)}
    store = stores.current_store()
    if store == "sqlite":
        path = env_file_value("GRAPH_EMBABEL_SQLITE_PATH") or f"{GRAPH_DIR}/graph.sqlite"
        return ["--store", "sqlite", "--path", path, "--live"], True, {}
    if store == "snapshot":
        return ["--store", "snapshot", "--path", env_file_value("VC_EMBABEL_JOURNAL") or f"{GRAPH_DIR}/memory.journal"], False, {}
    url_var, user_var, password_var = stores.SERVER_SETTINGS[store]
    prefix = env_file_value(f"GRAPH_EMBABEL_{store.upper()}_PREFIX") or "embabel_graph"
    return (["--store", store, "--url", env_file_value(url_var) or "", "--user", env_file_value(user_var) or "embabel",
             "--prefix", prefix], False, {"EMBABEL_SOURCE_PASSWORD": stores.secret(password_var) or ""})


def _export_graph(mode: str, image: str, staging: str, scope: list[str]) -> tuple[dict, str]:
    """The graph into [staging]/graph, by bundle-tool, with the app stopped only when it has to be."""
    args, live, env = graph_source()
    service = MODE_SERVICE[mode]
    started = time.monotonic()
    if not live:
        print("  Stopping the app while the graph is read — it is not one this appliance can copy live.")
        _compose(mode, "stop", service, capture=True)
    try:
        run = _bundle_tool(image, "graph", "export", "--out", f"{staging}/graph", *args, *scope, env=env)
    finally:
        if not live:
            _compose(mode, "up", "-d", service, capture=True)
    down = 0 if live else time.monotonic() - started
    if run.returncode != 0:
        raise BundleError(f"The graph could not be exported: {run.stderr.decode(errors='replace').strip()[-400:]}")
    manifest = _read_json(image, f"{staging}/graph/graph.json")
    return manifest, ("live — the app kept running" if live else f"cold — the app was stopped for {down:.0f}s")


def _read_json(image: str, path: str) -> dict:
    run = _helper(image, f"cat {shlex.quote(path)}")
    if run.returncode != 0:
        raise BundleError(f"{path} could not be read")
    return json.loads(run.stdout)


def _select_data(image: str, staging: str, world: str | None, private: bool) -> tuple[list[dict], dict]:
    """The world files a bundle carries, written as a list in [staging], and what was left out by rule. Secrets
    by name are never listed; then every listed file is scanned, and one that holds a credential stops the export."""
    roots = [f"users/{world}"] if world else ["users", *SHARED_SETUP]
    if private:
        roots += [f"chat-attachments/{_user_id(world)}" if world else "chat-attachments"]
    excludes = list(bundlesecrets.SECRET_NAMES) + list(ALWAYS_LEFT_OUT) + ([] if private else list(PRIVATE_PATHS))
    names = " ".join(f"! -name {shlex.quote(n)}" for n in bundlesecrets.SECRET_NAMES)
    list_file = f"{staging}/data.list"
    script = f"""
        set -e
        cd {WORLD_BASE}
        : > {list_file}
        for r in {' '.join(shlex.quote(r) for r in roots)}; do
          [ -e "$r" ] && find "$r" -type f {names} {' '.join(f"! -path {shlex.quote(p)}" for p in excludes)} >> {list_file} || true
        done
        while IFS= read -r f; do printf '%s\\t%s\\n' "$(stat -c %s "$f")" "$f"; done < {list_file}
    """
    run = _helper(image, script)
    if run.returncode != 0:
        raise BundleError(f"The worlds could not be listed: {run.stderr.decode(errors='replace').strip()[-300:]}")
    files = []
    for line in run.stdout.decode(errors="replace").splitlines():
        size, _, path = line.partition("\t")
        if path:
            files.append({"path": path, "bytes": int(size)})
    _refuse_secrets(image, list_file)
    left_out = {
        "secrets": "never: the credential file, provider and API keys, tokens, the setup token, per-world secrets.env, "
                   "and any file named like a key or a credential (" + ", ".join(bundlesecrets.SECRET_NAMES) + ")",
        "caches": "rebuilt by the server: " + ", ".join(ALWAYS_LEFT_OUT),
        "appliance settings": "this machine's .env, secrets.env and the admin directory stay with the appliance",
    }
    if not private:
        left_out["private files"] = ", ".join(PRIVATE_PATHS) + ", chat attachments"
    if world:
        left_out["other worlds"] = f"only {world} was exported"
    return files, left_out


def _refuse_secrets(image: str, list_file: str) -> None:
    """The content scan: any selected file holding a credential's shape stops the export, by name only."""
    run = _helper(image, bundlesecrets.scan_script(WORLD_BASE, list_file))
    found = [line for line in run.stdout.decode(errors="replace").splitlines() if "\t" in line]
    if found:
        named = "\n".join(f"    {f.split(chr(9), 1)[1]}  ({f.split(chr(9), 1)[0]})" for f in found[:20])
        raise BundleError(
            "Nothing was written: these files would have put a credential in the bundle.\n" + named +
            "\n  Move the secret into the world's data/secrets.env (which is never exported), or leave the world out.")


def _user_id(world: str | None) -> str:
    """The id the graph scopes a user by, from `user` or `user/world`: `<user>_assistant`."""
    return f"{(world or '').split('/', 1)[0]}_assistant"


def export_bundle(path: str, mode: str, world: str | None = None, private: bool = True, confirm=None) -> dict:
    """Write the bundle to [path]; returns its manifest."""
    image = mode_image(mode)
    if not image:
        raise BundleError("No server image found for this appliance — is it installed here?")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = f"{STAGING}/export-{stamp}"
    _helper(image, f"mkdir -p {staging}")
    try:
        scope = (["--world", _user_id(world)] if world else []) + ([] if private else ["--no-private"])
        graph, how = _export_graph(mode, image, staging, scope)
        files, left_out = _select_data(image, staging, world, private)
        manifest = _manifest(graph, files, left_out, how, world, private, mode)
        _write_text(image, f"{staging}/manifest.json", json.dumps(manifest, indent=2))
        if confirm and not confirm(manifest):
            raise BundleError("Nothing was written.")
        _stream_out(image, staging, path)
        manifest["compressedBytes"] = os.path.getsize(path)
        return manifest
    finally:
        _helper(image, f"rm -rf {staging}")


def _manifest(graph: dict, files: list[dict], left_out: dict, how: str, world, private, mode) -> dict:
    graph_bytes = graph.get("graphBytes", 0)
    return {
        "format": FORMAT,
        "kind": "full",
        "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "appliance": {"mode": mode, "engine": graph_engine(), "store": stores.current_store() if graph_engine() == "EMBABEL" else "neo4j",
                      "image": mode_image(mode)},
        "export": how,
        "embedding": _embedding(graph),
        "scope": {"world": world, "private": private},
        "graph": {k: graph[k] for k in ("format", "nodes", "relationships", "vectors", "labelGroups", "relationshipTypes", "source",
                                        "notCopied", "relationshipsNotCopied") if k in graph}
                 | {"leftOut": graph.get("scope", {}).get("leftOut", {})},
        "data": {"files": len(files), "bytes": sum(f["bytes"] for f in files), "worlds": sorted({"/".join(f["path"].split("/")[1:3]) for f in files if f["path"].startswith("users/")})},
        "includesSetup": "every world's own setup travels inside it: configuration, apps, views, actions, skills, agents, "
                         "schedules and preferences in the world files; realm references in each world's realms.yml (cloned "
                         "again as the server starts); saved lenses and watches in the graph"
                         + ("" if world else "; which realms' code was admitted, and custom themes"),
        "leftOut": left_out,
        "rawBytes": graph_bytes + sum(f["bytes"] for f in files),
    }


def _embedding(graph: dict) -> dict:
    """The model the graph records its vectors as made by — name, provider, width — as the server wrote it beside them.
    A graph from before that record names only the configured model, which may be a role (`hosted`) rather than a model,
    so `recorded` says which of the two this is."""
    recorded = graph.get("embeddingModel") or {}
    return {
        "model": recorded.get("model") or env_file_value("ASSISTANT_EMBEDDING_MODEL"),
        "provider": recorded.get("provider"),
        "dimensions": recorded.get("dimensions"),
        "recorded": bool(recorded.get("model")),
        "complete": recorded.get("complete", True) if recorded else None,
        "widths": graph.get("vectorWidths", {}),
    }


def _write_text(image: str, path: str, text: str) -> None:
    with tempfile.TemporaryFile() as f:
        f.write(text.encode())
        f.seek(0)
        run = _helper(image, f"cat > {shlex.quote(path)}", stdin=f)
    if run.returncode != 0:
        raise BundleError(f"{path} could not be written")


def _stream_out(image: str, staging: str, path: str) -> None:
    """The bundle, as one tar stream out of the container, compressed on the way to [path]: nothing is copied
    uncompressed on either side but the graph file bundle-tool writes."""
    script = (f"cd {WORLD_BASE} && tar -cf - -C {staging} manifest.json -C {staging} graph/graph.sqlite graph/graph.json "
              f"-C {WORLD_BASE} --transform 's,^\\(users\\|chat-attachments\\|admin\\)/,data/\\1/,' "
              f"--verbatim-files-from -T {staging}/data.list")
    stream_tar(image, script, path)


def stream_tar(image: str, script: str, path: str) -> None:
    """[script]'s tar on stdout, compressed on the host on its way to [path]; a failure leaves no file behind."""
    tmp = f"{path}.partial"
    proc = subprocess.Popen(_helper_argv(image, script), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with open(tmp, "wb") as raw, compressor(raw) as out:
            shutil.copyfileobj(proc.stdout, out, 1 << 20)
        proc.wait(timeout=3600)
        if proc.returncode != 0:
            raise BundleError(f"The bundle could not be written: {proc.stderr.read().decode(errors='replace').strip()[-300:]}")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _helper_argv(image: str, script: str, stdin: bool = False) -> list[str]:
    return ["docker", "run", "--rm", *(["-i"] if stdin else []), "--network", f"{compose_project()}_default", "-v", f"{volume_name(DATA_VOLUME)}:/data",
            "--entrypoint", "sh", image, "-c", script]


def compressor(raw):
    """gzip, level 6, from Python's own zlib.

    Measured on a real appliance's bundle (2.26 GB raw — a 2.2 GB graph, a third of it 768-wide vectors, which
    compress poorly — and 520 world files): gzip -6 wrote 527 MB in 46 s; gzip -1 601 MB in 13 s; xz -6 390 MB in
    about 8 minutes; zstd -3 508 MB in under a second and zstd -19 402 MB in 113 s, both multi-threaded. xz's
    quarter off the size costs ten times the wait, and zstd needs a tool that neither the CLI's Python (before 3.14)
    nor the server image has, and that the archive tools built into macOS and Windows do not open. A .tar.gz opens
    everywhere — Finder, Windows' own tar, any Linux — and is written with nothing but the standard library."""
    return gzip.open(raw, "wb", compresslevel=6)


def decompressor(path: str):
    return gzip.open(path, "rb")
