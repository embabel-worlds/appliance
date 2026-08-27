"""Data contracts from the command line: draft one for a view, review it, adopt it.

A thin client over `/api/v1/admin/kg/views/{view}/contract`. Every rule about what may
be promised — what counts as evidence, what is only a suggestion, when a projection is
too ambiguous to describe — lives in the appliance, for the same reason `samples.py`
gives: a CLI that reimplemented any of it would be a second opinion nobody asked for,
drifting from the one the runtime actually enforces.

The verb is deliberately undramatic by default. `embabel contract generate --view X`
reads the view's declaration, runs nothing, writes nothing, and prints YAML for a human
to read. Sampling, saving and binding are three separate flags because they are three
separate decisions, and the last of them is the one that puts a promise in front of
whoever queries that view next.
"""

from __future__ import annotations
import json
import urllib.error
import urllib.parse
import urllib.request

from .colour import TICK, bold, dim
from .core import SetupError


def contract_api(base: str, auth: str, view: str, payload: dict):
    """Draft a contract for one view. Errors come back in the appliance's own words."""
    request = urllib.request.Request(
        f"{base}/api/v1/admin/kg/views/{urllib.parse.quote(view, safe='')}/contract",
        data=json.dumps(payload).encode(),
        method="POST",
    )
    request.add_header("Authorization", auth)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 401:
            raise SetupError("The appliance did not accept that password.")
        if e.code == 404:
            raise SetupError(f"This appliance has no view called '{view}'.")
        # A refusal names what about the view made it uncontractable, and that sentence
        # is the whole value of the call — passing it through beats "400 Bad Request".
        try:
            reason = json.loads(body).get("error")
        except ValueError:
            reason = None
        raise SetupError(reason or f"The appliance answered {e.code}: {body[:200]}")


def describe_contract(result: dict, persisted: bool, bound: bool) -> None:
    """What was drafted, in the register a person reads before deciding to trust it.

    Columns are printed with their evidence because that is the distinction the whole
    feature turns on: `declared` is true of the view as written, `sampled` is one run's
    observation and might not hold tomorrow. A listing that hid the difference would
    invite exactly the promotion this workflow exists to slow down.
    """
    print(f"  {bold(result.get('view', '?'))}  "
          + dim(f"{result.get('contractId', '?')} v{result.get('version', '?')} "
                f"({result.get('status', '?')}, from the {result.get('mode', '?')})"))
    print()
    for column in result.get("columns", []):
        evidence = column.get("evidence", "?")
        logical = column.get("logicalType") or "—"
        marker = TICK if evidence == "declared" else "~"
        suggestions = [key.replace("embabel.suggested", "").lower() for key in column.get("suggestions", [])]
        hint = dim("  suggests " + ", ".join(suggestions)) if suggestions else ""
        print(f"    {marker} {column.get('name', '?'):<28} {logical:<12} {dim(evidence)}{hint}")
    print()
    if result.get("sampleRows"):
        print("  " + dim(f"{result['sampleRows']} row(s) sampled. `~` columns are inferred from them, not proven."))
    for note in result.get("notes", []):
        print("  " + dim(note))
    if persisted:
        print(f"  {TICK} Saved to {bold(result.get('savedAs', '?'))}")
    if bound:
        print(f"  {TICK} The view now OBSERVES this contract — verdicts recorded, no rows withheld.")
        print("  " + dim("Promote it to enforce by hand, once you believe it."))
    elif persisted:
        print("  " + dim("Not bound. Add --bind to have the view observe it."))
