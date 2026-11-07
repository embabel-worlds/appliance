#!/usr/bin/env python3
"""Compare two realm-regression runs and print only what changed.

    scripts/realm-regression-diff.py base.json candidate.json
    scripts/realm-regression-diff.py --parity twins.json              # forthcoming: see below

Two runs over the same pinned realms, on two server builds: every line printed is a realm whose
facts moved — a loading problem that appeared or went (by exact text), a surface that grew or
shrank, a view whose outcome changed, a realm that started or stopped being listed under its hash.
Nothing printed means no difference, and the exit status says so (0 same, 1 different), so a
candidate check is one command.

Each change is marked `+` (new in the second run), `-` (gone from it) or `~` (changed), and the
realm sha is checked first: two runs that installed different realm commits are not comparable,
and the diff says so rather than reporting the realm's own edits as the server's regression.

`--parity` is the hook for YAML→realm.ts twins. A converter (not yet written) will produce a
realm.ts-only twin of each YAML realm, named `<repo><suffix>`; a run that installs both sets with
`--twin-suffix` records each twin's `twinOf`. Given one such run, `--parity` compares every twin
with its original, ignoring what differs by format alone: the `format`, `sha` and `repo` fields,
the realm's name, and file extensions in problem locations. Given two runs, it compares realms
across them by that same twin key.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# Not facts: they move on every run, or describe the run rather than the realm.
IGNORED_TOP = {"timings", "startedAt", "server", "label", "serverSha", "note"}
# Format-only fields, dropped when comparing a realm with its twin.
FORMAT_ONLY = {"format", "sha", "repo", "twinOf", "defaultWorld"}


def load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def flatten(value, prefix: str = "") -> dict[str, object]:
    """Paths to scalars, with lists of scalars kept whole so a set difference can be shown."""
    out: dict[str, object] = {}
    if isinstance(value, dict):
        for k in sorted(value):
            out.update(flatten(value[k], f"{prefix}.{k}" if prefix else k))
    elif isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
        out[prefix] = sorted(json.dumps(x, sort_keys=True) for x in value)
    else:
        out[prefix] = value
    return out


def problem_text(p: str) -> str:
    d = json.loads(p)
    where = ":".join(str(d[k]) for k in ("file", "path", "line") if d.get(k) is not None)
    return f"[{d.get('severity')}] {d.get('source')}{(' ' + where) if where else ''}: {d.get('message')}"


def neutral(realm: dict, name_from: str | None, name_to: str | None) -> dict:
    """A realm's facts with format-only differences removed, for twin comparison."""
    r = {k: v for k, v in realm.items() if k not in FORMAT_ONLY}
    text = json.dumps(r, sort_keys=True)
    if name_from and name_to and name_from != name_to:
        text = text.replace(name_from, name_to)
    text = re.sub(r"\b(realm|pack)\.(ya?ml|ts)\b", "realm.<def>", text)
    return json.loads(text)


def compare_realm(key: str, a: dict, b: dict) -> list[str]:
    lines = []
    if a.get("sha") and b.get("sha") and a["sha"] != b["sha"]:
        lines.append(f"! {key}: realm source differs ({a['sha'][:12]} vs {b['sha'][:12]}) — not comparable")
    fa, fb = flatten(a), flatten(b)
    for path in sorted(set(fa) | set(fb)):
        va, vb = fa.get(path), fb.get(path)
        if va == vb:
            continue
        if isinstance(va, list) or isinstance(vb, list):
            sa, sb = set(va or []), set(vb or [])
            show = problem_text if "roblems" in path else (lambda x: x)
            for x in sorted(sb - sa):
                lines.append(f"+ {key} {path}: {show(x) if isinstance(x, str) and x.startswith('{') else x}")
            for x in sorted(sa - sb):
                lines.append(f"- {key} {path}: {show(x) if isinstance(x, str) and x.startswith('{') else x}")
        elif va is None and path.count(".") >= 2 and path.startswith("views."):
            lines.append(f"+ {key} {path}: {vb}")
        elif vb is None and path.count(".") >= 2 and path.startswith("views."):
            lines.append(f"- {key} {path}: {va}")
        else:
            lines.append(f"~ {key} {path}: {va!r} -> {vb!r}")
    return lines


def diff_runs(a: dict, b: dict) -> list[str]:
    lines = []
    for k in sorted((set(a) | set(b)) - IGNORED_TOP - {"realms"}):
        if a.get(k) != b.get(k):
            lines.append(f"~ run {k}: {json.dumps(a.get(k), sort_keys=True)} -> {json.dumps(b.get(k), sort_keys=True)}")
    ra, rb = a.get("realms", {}), b.get("realms", {})
    for key in sorted(set(ra) - set(rb)):
        lines.append(f"- {key}: only in the first run")
    for key in sorted(set(rb) - set(ra)):
        lines.append(f"+ {key}: only in the second run")
    for key in sorted(set(ra) & set(rb)):
        lines.extend(compare_realm(key, ra[key], rb[key]))
    return lines


def parity(runs: list[dict]) -> list[str]:
    if len(runs) == 1:
        realms = runs[0]["realms"]
        pairs = [(r["twinOf"], k) for k, r in realms.items() if r.get("twinOf") in realms]
        left, right = realms, realms
    else:
        left, right = runs[0]["realms"], runs[1]["realms"]
        by_twin = {r.get("twinOf") or k: k for k, r in right.items()}
        pairs = [(k, by_twin[k]) for k in left if k in by_twin]
    lines = []
    for orig, twin in sorted(pairs):
        lo, rt = left[orig], right[twin]
        a = neutral(lo, None, None)
        b = neutral(rt, rt.get("listed", {}).get("name"), lo.get("listed", {}).get("name"))
        lines.extend(compare_realm(f"{orig}~{twin}", a, b))
    if not pairs:
        lines.append("! no twin pairs found: a parity run installs twins with --twin-suffix")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="two harness outputs, or one with --parity")
    ap.add_argument("--parity", action="store_true", help="compare realms with their realm.ts twins")
    args = ap.parse_args()
    runs = [load(p) for p in args.runs]
    if args.parity:
        lines = parity(runs)
    elif len(runs) == 2:
        lines = diff_runs(*runs)
    else:
        ap.error("give two runs, or --parity")
    print("\n".join(lines) if lines else "no differences")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
