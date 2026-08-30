#!/usr/bin/env python3
"""Share a tour the way a person does, and check it survives the appliance restarting.

    python3 scripts/drive-tour-share.py --password <yours>
    python3 scripts/drive-tour-share.py --no-restart      # quick pass, skips the slow half

WHAT THIS IS FOR. Somebody likes what their world does, exports the walk that shows
it, and sends the file to a colleague who imports it. Every part of that is an
HTTP call this script makes in order, so a break anywhere in the chain fails here
rather than in front of the colleague.

WHY THE RESTART IS THE POINT. A tour that imports fine and vanishes on the next
`embabel restart` is worse than one that never imported: the user has already told
somebody it worked. Import writes to `config/tours/saved-tours.yml` in the world
directory, and the loader re-reads it at startup — a claim with two halves, and only
a restart tests the second one. It is slow, so `--no-restart` exists; CI should not
use it.

THE READ-ONLY ASSERTION IS A SECURITY TEST, not a feature test. `doneWhen` is Cypher
that runs as the acting user, and an imported tour is a file a stranger wrote. If a
change ever lets that surface write, a shared tour becomes a way to mutate somebody's
graph before they have pressed anything. The property holds today. This is here so it
keeps holding.
"""

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

# The tour every world has, used as the thing being shared: shipped by a realm, so the
# script does not depend on the world already containing somebody's saved work.
SOURCE_TOUR = "realm-uk-streets_streets_StreetLensFirstLook"
SHARED_ID = "SharedByAFriend"

# Where import must land it. Not an implementation detail worth hiding: "it is a file in
# your world directory" is the durability claim, and the test should read the file.
SAVED = "config/tours/saved-tours.yml"


class Appliance:
    def __init__(self, base, user, password):
        self.base = base.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def call(self, path, body=None, method=None, raw=False):
        req = urllib.request.Request(f"{self.base}{path}", method=method or ("POST" if body else "GET"))
        req.add_header("Authorization", self.auth)
        data = None
        if body is not None:
            req.add_header("Content-Type", "application/json")
            data = json.dumps(body).encode()
        with urllib.request.urlopen(req, data, timeout=30) as r:
            text = r.read().decode()
        return text if raw else json.loads(text)

    def tours(self):
        """The list endpoint wraps its array, so unwrap it once here rather than at every call."""
        got = self.call("/api/v1/tours")
        return got["tours"] if isinstance(got, dict) else got

    def find(self, fragment):
        return next((t for t in self.tours() if fragment in t["id"]), None)

    def up(self, timeout=300):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.tours()
                return True
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
                time.sleep(5)
        return False


def check(condition, message):
    print(f"  {'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        sys.exit(1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:11043")
    p.add_argument("--username", default="rod")
    p.add_argument("--password", required=True, help="the appliance password; never defaulted, this repo is public")
    p.add_argument("--container", default="embabel-appliance-worlds-1")
    p.add_argument("--world-dir", default="/data/embabel/assistant/users/{user}/default")
    p.add_argument("--no-restart", action="store_true")
    args = p.parse_args()

    me = Appliance(args.base, args.username, args.password)
    saved_file = args.world_dir.format(user=args.username) + "/" + SAVED

    # Leave nothing behind from a previous run, so a stale copy cannot make a broken
    # import look like a working one.
    existing = me.find(SHARED_ID)
    if existing:
        me.call(f"/api/v1/tours/{existing['id']}", method="DELETE")

    print("\n1. export — what the sharer sends")
    yaml = me.call(f"/api/v1/tours/{SOURCE_TOUR}/export", raw=True)
    check("id:" in yaml, f"exported {len(yaml)} bytes of tour file")

    print("\n2. import — the recipient's side")
    # Renamed, because a tour arriving from outside must not collide with the realm's own.
    # The rename is also what proves the import is reading the FILE rather than matching ids.
    yaml = yaml.replace("StreetLensFirstLook", SHARED_ID, 1).replace(
        "Street Lens, first look", "Shared by a friend", 1)
    stored = me.call("/api/v1/tours/import", {"yaml": yaml})
    tours = stored["tours"] if isinstance(stored, dict) else stored
    check(any(SHARED_ID in t["id"] for t in tours), "import reported the tour")

    got = me.find(SHARED_ID)
    check(got is not None, "listed alongside the world's own tours")
    check(got["userSaved"], "marked as the user's own, not a realm's")
    check(got["deletable"], "deletable — an imported tour must be removable")
    check(len(got["steps"]) > 0, f"carries its steps ({len(got['steps'])})")

    print("\n3. persisted to disk, not just held in memory")
    on_disk = subprocess.run(
        ["docker", "exec", args.container, "grep", "-c", SHARED_ID, saved_file],
        capture_output=True, text=True)
    check(on_disk.returncode == 0 and on_disk.stdout.strip() != "0",
          f"written to {SAVED}")

    print("\n4. a shared tour cannot write to your graph")
    # An imported tour is a file a stranger wrote, and `doneWhen` runs as you. The surface
    # refuses writes before executing them; UNKNOWN then means "run the step", which is the
    # right fail-soft. A CONFIRMED status here would mean the CREATE went through.
    probe = me.call("/api/v1/tours/import", {"yaml": (
        "- id: ReadOnlyProbe\n"
        "  name: Probe\n"
        "  steps:\n"
        "    - say: probe\n"
        "      doneWhen: |\n"
        "        CREATE (x:TourWroteThis) RETURN x\n")})
    probe_id = (probe["tours"] if isinstance(probe, dict) else probe)[0]["id"]
    try:
        verdict = me.call(f"/api/v1/tours/{probe_id}/steps/0/status", {"params": {}})
        check(verdict["status"] != "DONE", f"write rejected (status {verdict['status']})")
        check("READ-ONLY" in (verdict.get("detail") or "").upper(),
              "and rejected BEFORE it ran, not after")
    finally:
        me.call(f"/api/v1/tours/{probe_id}", method="DELETE")

    if not args.no_restart:
        print("\n5. RESTART the appliance")
        subprocess.run(["docker", "restart", args.container], check=True, capture_output=True)
        check(me.up(), "came back up")
        after = me.find(SHARED_ID)
        check(after is not None, "the shared tour survived the restart")
        check(after and len(after["steps"]) == len(got["steps"]), "with all its steps")

        print("\n6. and it still runs")
        guarded = next((i for i, s in enumerate(after["steps"]) if s.get("watchable")), 0)
        verdict = me.call(f"/api/v1/tours/{after['id']}/steps/{guarded}/status", {"params": {}})
        check(verdict.get("status") in ("DONE", "PENDING", "UNKNOWN"),
              f"step {guarded} evaluates after the restart ({verdict.get('status')})")
    else:
        print("\n5. restart skipped (--no-restart)")

    print("\n7. the recipient changes their mind")
    me.call(f"/api/v1/tours/{me.find(SHARED_ID)['id']}", method="DELETE")
    check(me.find(SHARED_ID) is None, "deleted, and gone from the list")

    print("\nAll passed.\n")


if __name__ == "__main__":
    main()
