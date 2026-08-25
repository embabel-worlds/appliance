"""Scenarios: put the world in a named state, from wherever it happens to be.

A scenario DECLARES what should be loaded, rather than listing steps to get there:

    {"name": "pipeline-at-risk",
     "wants":   ["accounts-base", "deals-at-risk"],
     "without": ["pipeline-healthy"]}

Running it works out the difference from what is loaded now and does the minimum —
adds what is missing, removes what should not be there, leaves the rest alone. Running
it twice does nothing the second time.

WHY DECLARED AND NOT SCRIPTED. `sample add X && sample remove Y` in a shell script does
the same job right up until somebody is watching: then a question from the room means a
scene gets skipped, or re-shown, or half-applied, and every later step in a script of
CHANGES assumes a state its predecessor no longer produced. A declaration asserts the
state instead, so jumping straight to the fourth scenario from anywhere lands correctly.

This is not only for demos, which is why the verb is `scenario` and not `demo`. The same
move puts a world into a fixed state to evaluate a realm, to reproduce a support case, or
to start a test from somewhere known.
"""

from __future__ import annotations
import json
import os

from .colour import ARROW, MIDDOT, TICK, bold, dim
from .core import APPLIANCE_DIR, SetupError
from .samples import read_set, samples_api

# Where scenarios are looked for, in order. A directory beside the appliance keeps a
# team's own scenarios out of the product's checkout; the checkout's own is where any
# shipped ones would live.
SCENARIO_DIRS = ("scenarios", os.path.join(APPLIANCE_DIR, "scenarios"))


def scenario_files() -> list[str]:
    """Every scenario file on this machine, nearest first."""
    found, seen = [], set()
    for directory in [os.environ.get("EMBABEL_SCENARIOS")] + list(SCENARIO_DIRS):
        if not directory or not os.path.isdir(directory):
            continue
        for entry in sorted(os.listdir(directory)):
            if entry.endswith((".json", ".yml", ".yaml")) and entry not in seen:
                seen.add(entry)
                found.append(os.path.join(directory, entry))
    return found


def load_scenarios() -> list[dict]:
    """Every scenario, in the order they should be walked.

    `order` when a scenario declares one, filename otherwise — so a set of files named
    `1-healthy.json`, `2-at-risk.json` walks correctly without anybody declaring
    anything, and a scenario that needs to sit in a particular place can say so.
    """
    scenarios = []
    for path in scenario_files():
        scenario = read_set(path)  # same reader: JSON, and YAML when PyYAML is present
        if not scenario.get("name"):
            scenario["name"] = os.path.splitext(os.path.basename(path))[0]
        scenario["_path"] = path
        scenarios.append(scenario)
    scenarios.sort(key=lambda s: (s.get("order", 1_000_000), s["name"]))
    return scenarios


def find_scenario(name: str) -> dict:
    for scenario in load_scenarios():
        if scenario["name"] == name:
            return scenario
    known = ", ".join(s["name"] for s in load_scenarios()) or "none found"
    raise SetupError(f"No scenario '{name}'. Known: {known}")


def loaded_names(base: str, auth: str) -> set:
    return {entry.get("name") for entry in (samples_api(base, auth).get("sets") or [])}


def matches(scenario: dict, loaded: set) -> bool:
    """Is the world already in this state?"""
    wants = set(scenario.get("wants") or [])
    without = set(scenario.get("without") or [])
    return wants <= loaded and not (without & loaded)


def current_scenario(scenarios: list, loaded: set) -> dict | None:
    """Which scenario the world is in, by looking at the world.

    NO SAVED POSITION, deliberately. A remembered "you are on step 3" is a second source
    of truth that goes wrong the moment somebody loads a set by hand, and it goes wrong
    silently. The loaded sets are the state; `next` reads them like everybody else.
    """
    for scenario in scenarios:
        if matches(scenario, loaded):
            return scenario
    return None


def plan(scenario: dict, loaded: set) -> tuple[list, list]:
    """What running this scenario would add and remove. The minimum, in both directions."""
    wants = [name for name in (scenario.get("wants") or []) if name not in loaded]
    remove = [name for name in (scenario.get("without") or []) if name in loaded]
    return wants, remove


def apply_scenario(base: str, auth: str, scenario: dict, loaded: set, dry_run: bool = False) -> None:
    """Bring the world to the scenario's declared state."""
    to_add, to_remove = plan(scenario, loaded)
    if not to_add and not to_remove:
        print(f"  {TICK} Already in {bold(scenario['name'])}.")
        return

    for name in to_remove:
        print(f"  {ARROW} remove {name}" + (dim("  (dry run)") if dry_run else ""))
        if not dry_run:
            samples_api(base, auth, f"/{name}", method="DELETE")
    for name in to_add:
        print(f"  {ARROW} add {name}" + (dim("  (dry run)") if dry_run else ""))
        if not dry_run:
            samples_api(base, auth, "", set_for(name, scenario))

    if not dry_run:
        print(f"  {TICK} {bold(scenario['name'])}"
              + (f" — {scenario['description']}" if scenario.get("description") else ""))


def set_for(name: str, scenario: dict) -> dict:
    """The sample set a scenario means by [name].

    Looked for beside the scenario first — a scenario and the data it needs travel
    together — then by the ordinary rules, which resolve a bare name inside the org.
    """
    here = os.path.dirname(scenario.get("_path") or "")
    for candidate in (
        os.path.join(here, "sets", f"{name}.json"),
        os.path.join(here, f"{name}.json"),
    ):
        if os.path.exists(candidate):
            return read_set(candidate)
    return read_set(name)


def describe_scenarios(scenarios: list, loaded: set) -> None:
    if not scenarios:
        print(f"  {MIDDOT} " + dim("No scenarios found. Put .json files in ./scenarios."))
        return
    here = current_scenario(scenarios, loaded)
    width = max(len(s["name"]) for s in scenarios)
    for scenario in scenarios:
        mark = TICK if here and scenario["name"] == here["name"] else " "
        line = f"  {mark} {scenario['name'].ljust(width)}"
        if scenario.get("description"):
            line += "  " + dim(scenario["description"])
        print(line)


def capture_scenario(name: str, loaded: set, scenarios: list,
                     description: str | None = None, force: bool = False) -> str:
    """Write the world's current state out as a scenario.

    THIS IS HOW SCENARIOS ACTUALLY GET MADE. Nobody writes one first: they load a set,
    load another, remove the one that was wrong, look at the screen, and only then know
    what they wanted. Capture turns that arrangement into something repeatable, which is
    the difference between a demo that worked once and a demo you can give again.

    `without` is the part worth understanding. It is every set the OTHER scenarios name
    that is not loaded here — so this scenario knows what to clear away when somebody
    arrives from one of its siblings. Recording only `wants` would produce a scenario
    that adds correctly and never removes anything, which shows up as yesterday's data
    still on screen halfway through a demo.
    """
    known = {
        entry
        for scenario in scenarios
        for entry in (scenario.get("wants") or []) + (scenario.get("without") or [])
    }
    scenario = {
        "name": name,
        # After everything that exists, so a captured scenario appends to the walk rather
        # than silently inserting itself in the middle of somebody's sequence.
        "order": max([s.get("order", 0) for s in scenarios] or [0]) + 1,
        "wants": sorted(loaded),
        "without": sorted(known - loaded),
    }
    if description:
        scenario["description"] = description

    directory = next((d for d in ([os.environ.get("EMBABEL_SCENARIOS")] + list(SCENARIO_DIRS))
                      if d and os.path.isdir(d)), "scenarios")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{name}.json")
    if os.path.exists(path) and not force:
        raise SetupError(f"{path} already exists. Use --force to replace it.")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scenario, f, indent=2)
        f.write("\n")
    return path
