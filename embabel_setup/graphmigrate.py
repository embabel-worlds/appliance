"""`embabel graph migrate --to embabel`: an appliance's knowledge graph, moved from Neo4j into the
Embabel engine, verified, and the appliance switched over — with the Neo4j graph left exactly where
it was, so going back is one line of .env.

WHO COPIES. The server does, as its engine opens: told where the old Neo4j is
(GRAPH_EMBABEL_IMPORT_NEO4J_*), it reads every node and relationship over Bolt and writes them into
the empty engine before anything else in the server runs, then reads the engine back and compares it
with what it read, group by group, every value to the bit. It has to be the server and it has to be
then. The server's own boot writes into its graph — the store identity each world is stamped with,
schema markers, reference data, the user records a sign-in checks — so an engine it had opened empty
would already disagree with the graph arriving, and nobody could even sign in to load it: the users
are in the graph being moved.

WHAT THIS DOES AROUND IT, in order: counts the Neo4j graph; stops the app (Neo4j keeps running — it
is the source); sets aside anything already on the engine's path; writes the Embabel engine and its
store into .env, keeping the old .env beside it; boots the app once with the import switched on and
follows it to a verified report; boots it again without the import; stops Neo4j and checks the
report's counts against its own. Anything short of a verified report puts .env back, sets the
half-made store aside, and starts the appliance on Neo4j again.
"""

from __future__ import annotations
import datetime
import json
import os
import shutil
import subprocess
import time

from .backup import BACKUP_HELPER_IMAGE
from .colour import TICK, dim, warn
from .core import MODE_SERVICE, SetupError
from .dockerlib import _compose, _docker, container_status, service_container
from .settings import (
    GRAPH_TYPE_VAR, embabel_command, env_path, env_file_value, graph_engine, set_env_var, volume_exists,
    volume_name,
)

DATA_VOLUME = "embabel_assistant_data"
GRAPH_DIR = "/data/graph"
REPORT = f"{GRAPH_DIR}/neo4j-import.json"
# What the engine keeps on its path; anything there before a migration is set aside, never deleted.
STORE_FILES = ("graph.sqlite", "graph.sqlite-wal", "graph.sqlite-shm", "memory.journal", "neo4j-import.json")
IMPORT_VARS = ("GRAPH_EMBABEL_IMPORT_NEO4J_URL", "GRAPH_EMBABEL_IMPORT_NEO4J_USERNAME",
               "GRAPH_EMBABEL_IMPORT_NEO4J_PASSWORD")
# The source as the app container reaches it: the compose service, on the project's network.
SOURCE_URL = "bolt://neo4j:7687"
# A server with the importer writes its report within seconds of its engine opening; one that is up
# and answering with no report has no importer, and must not be left running on an empty engine.
NO_REPORT_GRACE_SECONDS = 240
IMPORT_TIMEOUT_SECONDS = 6 * 3600
POLL_SECONDS = 5


class MigrationFailed(SetupError):
    """The migration stopped; the appliance is back on Neo4j and the message says why."""


def migrate_to_embabel(mode: str, confirm) -> dict:
    """Move this appliance's graph from Neo4j into the Embabel engine. [confirm] is asked once,
    with the plan, and may refuse; returns the verified report."""
    if graph_engine() == "EMBABEL":
        raise SetupError("This appliance already runs the Embabel engine — there is nothing to migrate.")
    if graph_engine() != "NEO4J":
        raise SetupError(f"This appliance runs {graph_engine()}; only a Neo4j graph can be migrated.")
    neo4j = service_container("neo4j", stopped=True)
    if not neo4j or not volume_exists("embabel_appliance_neo4j_data"):
        raise SetupError("No Neo4j graph found for this appliance — is it installed here?")
    if container_status(neo4j) != "running":
        _docker("start", neo4j, timeout=120)
    password = _neo4j_password(neo4j)
    source = _source_counts(neo4j, password)
    if not confirm(source):
        raise SetupError("Nothing was changed.")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    env_backup = f"{env_path()}.before-graph-migrate-{stamp}"
    shutil.copyfile(env_path(), env_backup)
    service = MODE_SERVICE[mode]
    print(f"  Stopping the app — Neo4j stays up as the source ({source['nodes']:,} nodes, "
          f"{source['relationships']:,} relationships).")
    _compose(mode, "stop", service, capture=True)
    try:
        _set_aside(f"before-migrate-{stamp}")
        _write_engine_pins()
        report = _import(mode, service, password)
        _check(report, source)
    except SetupError as failure:
        _roll_back(mode, service, env_backup, stamp)
        raise MigrationFailed(f"{failure}\n  The appliance is back on Neo4j; its graph was never changed.") from failure

    print("  Restarting without the import, on the Embabel engine alone.")
    _compose(mode, "up", "-d", service, capture=True)
    _docker("stop", neo4j, timeout=180)
    report["envBackup"] = env_backup
    return report


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


def _source_counts(container: str, password: str) -> dict:
    return {
        "nodes": int(_cypher(container, password, "MATCH (n) RETURN count(n)")),
        "relationships": int(_cypher(container, password, "MATCH ()-[r]->() RETURN count(r)")),
    }


def _in_data_volume(script: str, timeout: int = 300) -> subprocess.CompletedProcess | None:
    return _docker("run", "--rm", "-v", f"{volume_name(DATA_VOLUME)}:/data", BACKUP_HELPER_IMAGE,
                   "sh", "-c", script, timeout=timeout)


def _set_aside(suffix: str) -> None:
    """Whatever is on the engine's path moves to `<name>.<suffix>`: the import needs an empty store,
    and nothing anybody might want is deleted to get one."""
    moves = " ".join(f'[ -e "{f}" ] && mv "{f}" "{f}.{suffix}";' for f in STORE_FILES)
    _in_data_volume(f'mkdir -p {GRAPH_DIR} && cd {GRAPH_DIR} && {{ {moves} true; }}')


def _write_engine_pins() -> None:
    """GRAPH_TYPE and the store, as setup writes them for a new appliance (pins.py), so `embabel up`
    afterwards comes up on the engine and nothing a release changes can move it."""
    from .pins import EMBABEL_JOURNAL, EMBABEL_SQLITE, _store_the_server_has
    store = _store_the_server_has() or "sqlite"
    note = ("# Written by `embabel graph migrate --to embabel`; the Neo4j graph this appliance ran on "
            "is still in its volume, and GRAPH_TYPE=NEO4J goes back to it.",)
    set_env_var(GRAPH_TYPE_VAR, "EMBABEL", note)
    set_env_var("VC_EMBABEL_STORE", store)
    set_env_var("VC_EMBABEL_JOURNAL", EMBABEL_JOURNAL)
    set_env_var("GRAPH_EMBABEL_SQLITE_PATH", EMBABEL_SQLITE)


def _import(mode: str, service: str, password: str) -> dict:
    """The one boot that copies: the import switched on for this compose run only, followed to its report."""
    os.environ.update(dict(zip(IMPORT_VARS, (SOURCE_URL, "neo4j", password))))
    try:
        print("  Starting the app with the import switched on.")
        run = _compose(mode, "up", "-d", "--no-deps", "--force-recreate", service)
        if run.returncode != 0:
            raise SetupError("docker compose up failed — see the output above.")
    finally:
        for var in IMPORT_VARS:
            os.environ.pop(var, None)
    return _follow(service)


def _report() -> dict | None:
    run = _in_data_volume(f"cat {REPORT} 2>/dev/null", timeout=60)
    if not run or run.returncode != 0 or not run.stdout.strip():
        return None
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError:
        return None


def _follow(service: str) -> dict:
    """Wait for the report to say verified or failed, printing the server's progress as it goes."""
    started = time.monotonic()
    container = service_container(service, stopped=True)
    since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen = 0
    while time.monotonic() - started < IMPORT_TIMEOUT_SECONDS:
        time.sleep(POLL_SECONDS)
        seen = _print_progress(container, since, seen)
        report = _report()
        if report and report.get("status") in ("verified", "failed"):
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


def _check(report: dict, source: dict) -> None:
    """The server's verdict, and the CLI's own: the counts Neo4j gave before the copy began."""
    if report.get("status") != "verified":
        groups = report.get("differences") or []
        detail = "; ".join(f"{d['group']}: {d['source']} in Neo4j, {d['target']} copied"
                           + (" (values differ)" if d.get("contentDiffers") else "") for d in groups[:8])
        raise SetupError(f"The import did not verify: {report.get('message') or detail or 'see ' + REPORT}")
    if report.get("nodes") != source["nodes"] or report.get("relationships") != source["relationships"]:
        raise SetupError(f"Neo4j held {source['nodes']:,} nodes and {source['relationships']:,} relationships; "
                         f"the import copied {report.get('nodes'):,} and {report.get('relationships'):,}.")


def _roll_back(mode: str, service: str, env_backup: str, stamp: str) -> None:
    print(f"  {warn('!')} Rolling back to Neo4j.")
    _compose(mode, "stop", service, capture=True)
    _set_aside(f"failed-migrate-{stamp}")
    shutil.copyfile(env_backup, env_path())
    _compose(mode, "up", "-d", capture=True)


def summary(report: dict) -> str:
    secs = report.get("seconds") or {}
    took = ", ".join(f"{k} {v:.0f}s" for k, v in secs.items())
    return (f"  {TICK} Migrated and verified: {report['nodes']:,} nodes in {report['labelGroups']} label groups, "
            f"{report['relationships']:,} relationships of {report['relationshipTypes']} types, "
            f"{report['vectors']:,} vectors, every value compared ({took}).\n"
            f"    The appliance now runs the Embabel engine. Neo4j is stopped and its graph untouched.\n"
            f"    To go back: set GRAPH_TYPE=NEO4J in {os.path.basename(env_path())} (the old file is "
            f"{os.path.basename(report['envBackup'])}), then `{embabel_command('up')}`.")
