"""Saved views from the command line: list what exists, run one by name.

WHY A CLI VERB AT ALL. A saved view is the appliance's answer to "call this query by
name" — validated when it was saved, parameterised, and cached where it can be. Every
typed client we generate is a nicer way to make the same call. The shell is the one
consumer that will never have a generated client, and it is the consumer CI, cron and
a person at a terminal actually are, so it gets the call directly.

WHICH TIER IT CALLS, AND WHY IT MATTERS. Running goes through
`/api/v1/views/{name}/invoke` — the productized calling tier — not the admin
`/run` next door. That tier never returns the underlying Cypher, resolves identity
from the authenticated principal alone, and answers a versioned envelope whose
`outcome` separates a real empty from a source being down. Those distinctions are
the whole reason to prefer it, and a CLI that used the admin path would model the
wrong thing for everyone who copies it.

LISTING is the admin path (`/api/v1/admin/kg/views`), deliberately. Discovery needs
each view's declared parameters and cache state, which the calling tier does not
expose and should not — a caller is told what to pass, not how the view is built.
The CLI holds operator credentials, so it may ask the question an app may not.
"""

from __future__ import annotations
import json
import urllib.error
import urllib.parse
import urllib.request

from .colour import BULLET, TICK, bad, bold, dim, warn
from .core import SetupError

# Long enough for a view whose body reaches a slow source or spends a model call per
# row — an intelligence view with an `{ai:{…}}` edge does both, and cannot be cached
# when it takes parameters. Matches the contract drafter's own patience.
_TIMEOUT = 300

# The outcomes a caller must not read as "no data". Kept as a set rather than checked
# inline so the two places that branch on it cannot drift apart.
_NOT_AN_EMPTY = {"SOURCE_UNAVAILABLE", "TOO_BROAD", "NOT_PERMITTED", "UNTRUSTED_DATA", "FAILED"}


def _get(base: str, auth: str, path: str, payload=None):
    """One call, with the appliance's own sentence on failure."""
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method="POST" if payload is not None else "GET",
    )
    request.add_header("Authorization", auth)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 401:
            raise SetupError("The appliance did not accept that password.")
        # 400 here is nearly always a view-argument failure, and the appliance names the
        # parameter and the coercion it wanted. That sentence is the entire value of the
        # response; "400 Bad Request" would send the caller back to guessing.
        try:
            reason = json.loads(body).get("error")
        except ValueError:
            reason = None
        raise SetupError(reason or f"The appliance answered {e.code}: {body[:200]}")


def views_api(base: str, auth: str) -> list:
    """Every saved view, with its declared parameters. The discovery call."""
    result = _get(base, auth, "/api/v1/admin/kg/views")
    return result if isinstance(result, list) else result.get("views", [])


def run_view_api(base: str, auth: str, view: str, args: dict) -> dict:
    """Run one view through the calling tier and return its envelope, whole."""
    return _get(
        base, auth,
        f"/api/v1/views/{urllib.parse.quote(view, safe='')}/invoke",
        {"args": args},
    )


def parse_args(pairs: list, args_json: str | None) -> dict:
    """`--arg name=value`, repeatable, merged over `--args-json`.

    Values go up as the strings they were typed as. The appliance coerces each one to
    the parameter's DECLARED type and refuses what it cannot — so a CLI that guessed
    int-vs-string here would be a second, worse copy of `ViewParamSpec.coerce`, and
    would disagree with it the first time a view declared a string parameter whose
    value happens to look like a number.
    """
    args = {}
    if args_json:
        try:
            args = json.loads(args_json)
        except ValueError as e:
            raise SetupError(f"--args-json is not valid JSON: {e}")
        if not isinstance(args, dict):
            raise SetupError("--args-json must be a JSON object of parameter names to values.")
    for pair in pairs or []:
        if "=" not in pair:
            raise SetupError(f"--arg wants name=value, got '{pair}'.")
        name, value = pair.split("=", 1)
        args[name.strip()] = value
    return args


def describe_views(views: list) -> None:
    """What can be run, and what each one wants passed.

    Parameters are printed with `required` and the default, because those are the two
    facts a caller needs before typing anything: a parameter declaring no default must
    be supplied, and one that does may be left out.
    """
    if not views:
        print("  " + dim("No saved views. Author one in Query Studio, or install a realm that ships some."))
        return
    for view in sorted(views, key=lambda v: (v.get("source") or "", v.get("name") or "")):
        realm = view.get("source")
        cached = ""
        if view.get("materialized"):
            cached = dim(f"  cached {view.get('ttl', '?')}")
        print(f"  {BULLET} {bold(view.get('name', '?'))}"
              + (dim(f"  [{realm}]") if realm else "") + cached)
        if view.get("description"):
            print(f"      {dim(view['description'].strip().splitlines()[0])}")
        for name, spec in (view.get("params") or {}).items():
            spec = spec or {}
            has_default = spec.get("default") is not None
            need = dim("optional") if has_default else warn("required")
            default = dim(f" = {spec['default']}") if has_default else ""
            means = spec.get("description") or ""
            print(f"      --arg {name}=<{spec.get('type', 'string')}>{default}  {need}"
                  + (f"  {dim(means)}" if means else ""))
    print()


def describe_run(view: str, result: dict, as_json: bool) -> int:
    """One run's envelope, in the register the caller reads.

    The envelope is read BEFORE the rows, and the distinction it draws is the point:
    `EMPTY` is a real answer, `SOURCE_UNAVAILABLE` is not, and a client that prints
    "0 rows" for both teaches its user to believe the second one.
    """
    if as_json:
        print(json.dumps(result, indent=2))
        return 0 if result.get("outcome") not in _NOT_AN_EMPTY else 1

    outcome = result.get("outcome", "?")
    rows = result.get("data") or []
    metrics = result.get("metrics") or {}

    for w in result.get("warnings") or []:
        print(f"  {warn('!')} {w.get('message', '')}  {dim(w.get('code', ''))}")

    if outcome in _NOT_AN_EMPTY:
        error = result.get("error") or {}
        print(f"  {bad(outcome)}  {error.get('message', 'The view could not be run.')}")
        if outcome == "SOURCE_UNAVAILABLE":
            print("  " + dim("A backing source is unreachable. This is NOT the same as having no data."))
        return 1

    if outcome == "EMPTY":
        print(f"  {TICK} {bold(view)} ran and returned nothing. That is an answer, not a failure.")
        return 0

    if outcome == "PARTIAL":
        print("  " + warn("Capped — treat any count below as a floor, not a total."))

    _print_rows(rows)
    cache = metrics.get("cacheHit")
    served = "from cache" if cache else ("computed" if cache is False else "")
    print(f"  {TICK} {len(rows)} row(s) in {metrics.get('durationMs', '?')}ms {dim(served)}")
    return 0


def _print_rows(rows: list) -> None:
    """A table when the rows agree on their columns, JSON when they do not.

    Nested values are not flattened into a cell: a column holding an object is printed
    as JSON so nothing is silently truncated into something that reads like a scalar.
    """
    if not rows:
        return
    if not all(isinstance(r, dict) for r in rows):
        print(json.dumps(rows, indent=2))
        return
    columns = list(rows[0].keys())
    if any(list(r.keys()) != columns for r in rows):
        print(json.dumps(rows, indent=2))
        return

    def cell(v):
        return json.dumps(v) if isinstance(v, (dict, list)) else ("" if v is None else str(v))

    widths = {c: max(len(c), *(len(cell(r.get(c))) for r in rows)) for c in columns}
    widths = {c: min(w, 40) for c, w in widths.items()}
    print("  " + dim("  ".join(c.ljust(widths[c])[:widths[c]] for c in columns)))
    for row in rows:
        print("  " + "  ".join(cell(row.get(c)).ljust(widths[c])[:widths[c]] for c in columns))
    print()
