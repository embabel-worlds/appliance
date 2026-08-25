"""What the world knows: realms, sample data, scenarios, and coding agents.

The verbs that change what is IN the world rather than whether it is running. Sample
data is the only thing any of them deletes — removing a realm leaves its records, and
so does deleting a world.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys

from .cli import _sample_target, current_mode, run_setup, s
from .core import prompt

def cmd_realms(args) -> int:
    if args.realms_command == "link":
        return run_setup("worlds", "--realms", args.directory)
    # list: what the appliance can actually see, which is the question worth asking.
    realms = os.environ.get("EMBABEL_REALMS_DIR")
    env_path = s.env_path()
    if not realms and os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("EMBABEL_REALMS_DIR="):
                    realms = line.split("=", 1)[1].strip()
    if not realms:
        print("  No realm checkouts linked.  embabel realms link <directory>")
        return 0
    path, found, notes = s.inspect_realms_dir(realms)
    if not path:
        for note in notes:
            print(f"  {note}")
        return 1
    s.announce_realms(path, found, notes)
    return 0


def cmd_agents(args) -> int:
    """Point Claude Code and Codex at THIS appliance, again.

    WIRING ONLY EVER HAPPENED ONCE, during first-run setup, and the setup API
    closes permanently when setup completes — so there was no second chance. Every
    way that goes wrong left the operator editing client config by hand:

      · an appliance installed before the port rebase left a registration aimed at
        4242, and the next install could not replace it (`claude mcp add` refuses a
        name that exists, and this printed the refusal rather than acting on it)
      · a second appliance, or a reinstall, mints a new token and the old one in the
        client stops authenticating
      · somebody declined at setup and changed their mind

    The token is not re-minted here, and cannot be: the server returns it exactly
    once. It is READ BACK from the data volume, which is where the appliance keeps
    it for its own use — so this re-registers the credential that is actually live
    rather than inventing a rival one.
    """
    mode = current_mode() or s.configured_mode()
    if not mode:
        print("  Nothing is running.  `embabel up` starts it.")
        return 1
    container = s.find_mode_container(mode)
    base = s.container_base_url(container) if container else None
    if not container or not base:
        print("  The appliance is not up.  `embabel up` starts it.")
        return 1

    run = s._docker("exec", container, "sh", "-c",
                    "cat /data/embabel/assistant/admin/providers.env 2>/dev/null")
    token = user = None
    for line in (run.stdout if run and run.returncode == 0 else "").splitlines():
        if line.startswith("EMBABEL_SETUP_MCP_TOKEN="):
            token = line.split("=", 1)[1].strip()
        elif line.startswith("EMBABEL_SETUP_MCP_TOKEN_USER="):
            user = line.split("=", 1)[1].strip()
    if not token:
        print("  This appliance has no MCP token — it was never minted, or MCP was declined.")
        print("  " + s.dim("A token is minted during first-run setup and cannot be re-issued afterwards."))
        return 1

    print(f"  Wiring agents to {base}/mcp" + (f" as {user}" if user else ""))
    s.wire_coding_agents({"token": token, "url": f"{base}/mcp", "user": user})
    return 0


def cmd_sample(args) -> int:
    """Fictional records: load them, see them, take them away, hand them on.

    The appliance marks everything loaded here so that removing it is exact. Nothing
    else this product does deletes anything, which is what makes `remove` and `clear`
    safe to run without reading the manual first.
    """
    base, auth = _sample_target(args)
    if not base:
        return 1

    if args.sample_command == "list":
        s.describe_sets(s.samples_api(base, auth))
        return 0

    if args.sample_command == "add":
        payload = s.read_set(args.source)
        name = payload.get("name") or args.source
        print(f"  Loading {s.bold(name)}" + s.dim(f" into realm {payload.get('realm', '?')}…"))
        result = s.samples_api(base, auth, "", payload)
        print(f"  {s.TICK} Loaded {result.get('nodes', 0)} node(s), {result.get('edges', 0)} edge(s).")
        print("  " + s.dim(f"Remove it with: embabel sample remove {name}"))
        return 0

    if args.sample_command == "remove":
        result = s.samples_api(base, auth, f"/{args.name}", method="DELETE")
        print(f"  {s.TICK} Removed {result.get('nodes', 0)} node(s) from '{args.name}'.")
        return 0

    if args.sample_command == "clear":
        # Asked, not assumed. It is a small action with a wide blast radius, and the
        # person running it before a customer demo is in a hurry.
        if not args.yes:
            answer = prompt("  Remove ALL sample data from this world? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                print("  Left alone.")
                return 0
        result = s.samples_api(base, auth, "", method="DELETE")
        print(f"  {s.TICK} Removed {result.get('nodes', 0)} node(s).")
        return 0

    if args.sample_command == "export":
        if not args.labels and not args.source:
            print("  Name a --source set or at least one --label.")
            print("  " + s.dim("An export with no selector is the whole world."))
            return 1
        payload = {
            "name": args.name,
            "realm": args.realm,
            "labels": [l.strip() for l in (args.labels or "").split(",") if l.strip()],
            "source": args.source,
            "limit": args.limit,
            # SHAPE_ONLY unless asked otherwise, and the appliance defaults the same way.
            # Stated here as well so the request says what it means.
            "mode": "WITH_VALUES" if args.with_values else "SHAPE_ONLY",
        }
        result = s.samples_api(base, auth, "/export", payload)
        text = json.dumps(result, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print(f"  {s.TICK} Wrote {len(result.get('nodes', []))} node(s) to {s.bold(args.output)}")
            # LOUDLY. A caller who asked for 500 and got 500 cannot tell a complete
            # answer from a truncated one, and an export that looks finished and is not
            # is worse than one that refused — somebody sends it and wonders why the
            # reproduction does not reproduce.
            if result.get("truncated"):
                print("  " + s.warn(
                    f"TRUNCATED: {result.get('matched', 0)} record(s) matched, "
                    f"{len(result.get('nodes', []))} written."))
                print("  " + s.dim("Narrow it with --source <set>, or raise --limit."))
            if not args.with_values:
                print("  " + s.dim("Shape only: labels, property keys and edges. No values, no ids."))
            else:
                print("  " + s.warn("Contains real values — read it before sending it anywhere."))
            print("  " + s.dim(f"Load it elsewhere with: embabel sample add {args.output}"))
        else:
            print(text)
        return 0

    print("  embabel sample add | remove | list | clear | export")
    return 1


def cmd_scenario(args) -> int:
    """Put the world in a named state, from wherever it is now.

    Declared rather than scripted: a scenario says what should be loaded, and running it
    works out the difference. That is what makes it safe to jump straight to the fourth
    one when somebody in the room asks to see it.
    """
    if args.scenario_command == "list" and not s.scenario_files():
        print("  " + s.dim("No scenarios found. Put .json files in ./scenarios."))
        return 0

    base, auth = _sample_target(args)
    if not base:
        return 1
    scenarios = s.load_scenarios()
    loaded = s.loaded_names(base, auth)

    if args.scenario_command == "list":
        s.describe_scenarios(scenarios, loaded)
        return 0

    if args.scenario_command == "run":
        s.apply_scenario(base, auth, s.find_scenario(args.name), loaded, dry_run=args.dry_run)
        return 0

    if args.scenario_command == "capture":
        if not loaded:
            print("  " + s.warn("Nothing is loaded — this will capture an empty world."))
        path = s.capture_scenario(args.name, loaded, scenarios,
                                  description=args.description, force=args.force)
        scenario = s.read_set(path)
        print(f"  {s.TICK} Wrote {s.bold(path)}")
        print(f"     wants:   {', '.join(scenario['wants']) or '(nothing)'}")
        if scenario["without"]:
            print(f"     without: {', '.join(scenario['without'])}")
            print("  " + s.dim("(what the other scenarios load, so this one clears it away)"))
        print("  " + s.dim(f"Replay it with: embabel scenario run {args.name}"))
        return 0

    if args.scenario_command == "next":
        if not scenarios:
            print("  " + s.dim("No scenarios found."))
            return 1
        here = s.current_scenario(scenarios, loaded)
        if here is None:
            # Nothing matches, so start at the beginning rather than guess.
            following = scenarios[0]
        else:
            index = next(i for i, x in enumerate(scenarios) if x["name"] == here["name"])
            if index + 1 >= len(scenarios):
                print(f"  {s.MIDDOT} " + s.dim(f"'{here['name']}' is the last one."))
                return 0
            following = scenarios[index + 1]
        s.apply_scenario(base, auth, following, loaded, dry_run=args.dry_run)
        return 0

    print("  embabel scenario list | run | next")
    return 1
