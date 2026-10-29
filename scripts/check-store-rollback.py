#!/usr/bin/env python3
"""A failed import or migration into a Postgres or Oracle server takes away the tables it wrote — and nothing else.

WHY THIS EXISTS. `embabel import` and `embabel graph migrate` write the arriving graph into a server store under a
table prefix. A failure rolls the appliance back, and used to leave that graph in the server with a message naming it.
Now the rollback drops it (storetables.py) — but only a prefix the attempt found empty, because a prefix that held a
graph before is someone's graph. This drives both rollbacks with the server and Docker patched out, and holds them
to that: dropped when it held nothing, untouched when it held something, named in the message when the drop could
not be made. The drop itself, against a real Postgres, is the engine's GraphPrefixDropIT.

    python3 scripts/check-store-rollback.py
"""
import os
import sys
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from embabel_setup import bundleimport, graphmigrate, storetables  # noqa: E402
from embabel_setup.bundle import BundleError  # noqa: E402
from embabel_setup.core import SetupError  # noqa: E402

failures = []


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


class Server:
    """The bundle-tool's `tables` and `drop` against one prefix: what it held before, and whether a drop works."""

    def __init__(self, before, drop_works=True):
        self.before = before
        self.drop_works = drop_works
        self.calls = []

    def run(self, image, command, store, prefix):
        self.calls.append((command, store, prefix))
        if command == "tables":
            return self.before
        return [f"{prefix}_nodes", f"{prefix}_rels"] if self.drop_works else None


def import_fails(server: Server) -> str:
    """An import into Postgres whose server import never verifies; returns the message the operator reads."""
    with patch.object(bundleimport, "_keep", return_value=("env.bak", None)), \
         patch.object(bundleimport, "_compose"), \
         patch.object(bundleimport, "graph_engine", return_value="EMBABEL"), \
         patch.object(bundleimport, "_target_store", return_value="postgres"), \
         patch.object(bundleimport, "_prepare_store"), \
         patch.object(bundleimport, "_swap_worlds"), \
         patch.object(bundleimport, "_follow", return_value={"status": "failed", "message": "a value differs"}), \
         patch.object(bundleimport, "_roll_back"), \
         patch.object(storetables, "_run", side_effect=server.run):
        try:
            bundleimport._take("me", "image", "/data/staging", "20261029-101010", {"graph": {}}, lambda: None)
        except BundleError as e:
            return str(e)
    return ""


def check_import() -> None:
    prefix = "embabel_graph_20261029_101010"
    empty = Server(before=[])
    message = import_fails(empty)
    check(("drop", "postgres", prefix) in empty.calls, f"a failed import did not drop the prefix it wrote: {empty.calls}")
    check("were dropped" in message and prefix in message, f"the import's message does not say the tables were dropped: {message}")

    held = Server(before=[f"{prefix}_nodes"])
    import_fails(held)
    check(not any(c[0] == "drop" for c in held.calls), "a failed import dropped a prefix that held a graph before it")

    failing = Server(before=[], drop_works=False)
    message = import_fails(failing)
    check("can be dropped" in message and prefix in message, f"a drop that failed is not followed by where the tables are: {message}")

    unknown = Server(before=None)
    message = import_fails(unknown)
    check(not any(c[0] == "drop" for c in unknown.calls), "a prefix that could not be asked about was dropped")
    check("can be dropped" in message, f"a prefix that could not be asked about is not named: {message}")


def migrate_fails(server: Server, created: bool) -> str:
    move = graphmigrate.Move("me", "postgres", "sqlite")
    with patch.object(graphmigrate, "_keep", return_value=("env.bak", None)), \
         patch.object(graphmigrate, "_compose"), \
         patch.object(graphmigrate.stores, "volume_held", return_value=not created), \
         patch.object(graphmigrate.stores, "stop_bundled"), \
         patch.object(graphmigrate.stores, "remove_bundled"), \
         patch.object(graphmigrate, "_prepare_target"), \
         patch.object(graphmigrate, "mode_image", return_value="image"), \
         patch.object(graphmigrate, "_import", return_value={"status": "failed", "message": "a value differs"}), \
         patch.object(graphmigrate.shutil, "copyfile"), \
         patch.object(storetables, "prefix_of", return_value="embabel_graph"), \
         patch.object(storetables, "_run", side_effect=server.run):
        try:
            graphmigrate.migrate(move)
        except graphmigrate.MigrationFailed as e:
            return str(e)
    return ""


def check_migrate() -> None:
    empty = Server(before=[])
    message = migrate_fails(empty, created=False)
    check(("drop", "postgres", "embabel_graph") in empty.calls, f"a failed migration did not drop what it wrote: {empty.calls}")
    check("were dropped" in message, f"the migration's message does not say the tables were dropped: {message}")

    held = Server(before=["embabel_graph_nodes"])
    migrate_fails(held, created=False)
    check(not any(c[0] == "drop" for c in held.calls), "a failed migration dropped a graph the server held before it")

    created = Server(before=[])
    migrate_fails(created, created=True)
    check(not created.calls, "a server the migration created was asked about tables; it is removed whole")


check_import()
check_migrate()

if failures:
    for f in failures:
        print(f"  FAIL {f}")
    sys.exit(1)
print("  ok: a failed import or migration drops the tables it wrote into a server, and only those")
