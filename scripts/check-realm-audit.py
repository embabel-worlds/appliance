#!/usr/bin/env python3
"""`embabel realm audit` scores a realm against realm-spec REALM_QUALITY.md, from its files alone.

What has to stay true for the score to be worth bringing a realm up to:

- The command is reachable as `realm audit` and `realms audit`, with every flag, and asks no
  question about which appliance: it reads a checkout, not an appliance.
- A YAML realm and a realm.ts realm declaring the same thing get the same findings — one model,
  two spellings, read the host's way (a key written twice is a problem, `2026-07-28` a string).
- Each rule fires on the declaration that breaks it and stays quiet on the one that meets it:
  a missing description, an identity said only after the 320-character cut, a status with no
  values, a producer with no pushdown or no cache, paging below the API document's maximum, a
  list-only producer beside an unused by-id read, a partial match the param does not own up to,
  a view named for one value of the several it counts, and a field the host drops without a word
  (`dimension:` on a property, `ttlSeconds` for a ttl's `seconds`). The property measures the host
  does read — `unit:`, `currency:`, `format:` — state a unit or a shape as a description would.
- A directory of checkouts is each checkout, minus `-fix…`, spike and draft working copies and any
  other copy declaring the same realm name as the `realm-<name>` checkout beside it.
- `--fail-under` is the exit status a CI check needs; a directory that is no realm is 2.
- A realm.ts reads as a fresh clone ships it: `import { type Realm }` with no SDK installed, and a
  value import with the SDK installed as TypeScript source under node_modules, which Node will not
  strip by itself. A value import with no SDK, or no runtime at all, is one line saying what to do.
- The name heuristics read words, not letters: `teams` is no measure in ms, `value` no money,
  `candidateId` no date; a depth "in plies" states its unit; a declared `values:` list makes a
  `speed` a category; and an apostrophe ("the plan's") does not swallow the values quoted after it.
- An entry holding only joins into a type another realm owns is not that type's declaration: it is
  not asked for a description or an identity (T1, T3, T5), and its joins are still held to J1.
- Every realm is held to its realm.yml description, which routing shows: an app-only realm is
  scored on it rather than left without a score.

Nothing runs but this process (and Node, only for the realm.ts cases, skipped where there is no
Node 22.13 or later to evaluate them).
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from embabel_setup import cli, realmaudit  # noqa: E402
from embabel_setup.cliparser import build_parser  # noqa: E402

try:
    import yaml  # noqa: F401
except ImportError:
    sys.exit("check-realm-audit needs PyYAML, as `embabel realm audit` does: pip install pyyaml")

failures = []


def check(ok, message):
    if not ok:
        failures.append(message)


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def ids(result, cid):
    return [f["where"] for f in result["findings"] if f["id"] == cid]


# ── the command surface ──────────────────────────────────────────────────────
for verb in ("realm", "realms"):
    args = build_parser().parse_args([verb, "audit", "./r", "--json", "--markdown", "-v", "--fail-under", "80"])
    check(args.realms_command == "audit", f"`{verb} audit` does not reach audit")
    check(args.json and args.markdown and args.verbose and args.fail_under == 80, f"`{verb} audit` dropped a flag")
    check(cli.names_its_own_image(args), f"`{verb} audit` would ask which appliance it is about")

GOOD_TYPES = """
- name: Desk
  description: >-
    One support desk, as a door onto its cases, keyed by the status asked for: 'open', 'pending' or 'all'.
  properties:
    status: { identity: true, description: "The status asked for: 'open', 'pending' or 'all'." }
- name: Ticket
  description: >-
    One support ticket in the helpdesk, identified by its ticket id. A customer's problem or question.
  properties:
    id: { identity: true, description: "The helpdesk's ticket number, as shown in its UI." }
    status: "open, pending or resolved."
    amount: { type: number, description: "What the customer was charged, in major units of currency (ISO 4217)." }
    openedOn: "The business day the customer raised it, ISO YYYY-MM-DD."
    replyCount: { type: int, description: "How many replies the ticket has had." }
    domain: { hub: CustomerAccount, description: "The requester's company domain, lower case." }
  virtualJoins:
    - { anchorLabel: Desk, relationship: HAS_TICKET, keyField: status, recordKeyField: status, producer: ticketsByStatus,
        description: "Every ticket on the desk in this status; one listing per status." }
  examples:
    - q: "how many open tickets"
      cypher: "MATCH (:Desk {status:'open'})-[:HAS_TICKET]->(t) RETURN count(t)"
"""
GOOD_PRODUCERS = """
- name: ticketsByStatus
  kind: remote
  operation: listTickets
  keyArg: status
  records: "$.items[*]"
  paging: { style: page, size: 100, maxPages: 50 }
  pushdown: []
  cache: { kind: ttl, seconds: 60, negativeTtlSeconds: 60 }
- name: ticketById
  kind: remote
  operation: getTicket
  keyArg: id
  pushdown: []
  cache: { kind: ttl, seconds: 60, negativeTtlSeconds: 60 }
"""
API = {"openapi": "3.0.0", "paths": {
    "/tickets": {"get": {"operationId": "listTickets", "parameters": [
        {"name": "per_page", "in": "query", "schema": {"type": "integer", "maximum": 100}}]}},
    "/tickets/{id}": {"get": {"operationId": "getTicket"}}}}
GOOD_VIEWS = """
- name: TicketsByWord
  description: "Open tickets whose subject holds a word. Not resolved ones; at most the first 50 by date."
  params:
    word: { type: string, default: "", description: "Partial, case-insensitive: any subject containing it." }
  cypher: |
    MATCH (:Desk {status:'open'})-[:HAS_TICKET]->(t) WHERE toLower(t.subject) CONTAINS toLower($word) RETURN t
"""

tmp = tempfile.mkdtemp()
try:
    good = os.path.join(tmp, "realm-good")
    write(good, "realm.yml", "name: good\ndescription: A helpdesk's tickets, read live. Nothing is mirrored.\nversion: 0.1.0\n")
    write(good, "types/t.yml", GOOD_TYPES)
    write(good, "producers/p.yml", GOOD_PRODUCERS)
    write(good, "views/v.yml", GOOD_VIEWS)
    write(good, "apis/apis.yml", "- { url: desk.json, name: desk, type: openapi }\n")
    write(good, "apis/desk.json", json.dumps(API))
    r = realmaudit.audit_realm(good)
    check(r["score"] == 100 and not r["findings"],
          f"a realm meeting every rule scored {r['score']} with {[(f['id'], f['where']) for f in r['findings']]}")

    # ── each rule, broken once ──────────────────────────────────────────────
    bad = os.path.join(tmp, "realm-bad")
    shutil.copytree(good, bad)
    types = GOOD_TYPES.replace("One support ticket in the helpdesk, identified by its ticket id. A customer's problem or question.",
                               "Support work. " + "It has a long history of being handled by people. " * 7 + "Each is one ticket, by id.") \
        .replace('status: "open, pending or resolved."', 'status: "The status."') \
        .replace('amount: { type: number, description: "What the customer was charged, in major units of currency (ISO 4217)." }',
                 'amount: { type: number, description: "What the customer was charged." }') \
        .replace("openedOn: \"The business day the customer raised it, ISO YYYY-MM-DD.\"", "openedOn: \"When it was opened by them.\"") \
        .replace("replyCount: { type: int, description:", "replyCount: { description:") \
        .replace("domain: { hub: CustomerAccount, description:", "domain: { description:") \
        .replace('description: "Every ticket on the desk in this status; one listing per status." ', "") \
        .replace("  examples:\n    - q: \"how many open tickets\"\n      cypher: \"MATCH (:Desk {status:'open'})-[:HAS_TICKET]->(t) RETURN count(t)\"\n", "")
    types = types.replace('openedOn: "When it was opened by them."', 'openedOn: { description: "When it was opened by them.", dimension: date }')
    write(bad, "types/t.yml", types + "- name: Orphan\n  properties: { x: {} }\n  virtualJoins: [{ anchorLabel: Desk, relationship: HAS_ORPHAN, keyField: status, producer: ticketsByStatus }]\n"
                    "- name: Twice\n  properties: { x: {} }\n  virtualJoins:\n"
                    "    - { anchorLabel: Desk, relationship: A, keyField: status, producer: ticketsByStatus }\n"
                    "    - { anchorLabel: Desk, relationship: B, keyField: status, producer: ticketsByStatus }\n")
    write(bad, "producers/p.yml", """
- name: ticketsByStatus
  kind: remote
  operation: listTickets
  keyArg: status
  paging: { style: page, size: 25, total: meta.count }
  cache: { kind: ttl, seconds: 60 }
- name: ticketsAgain
  kind: remote
  operation: listTickets
  pushdown: [{ property: subject, qualifier: "q={value}" }]
  cache: { kind: ttl, ttlSeconds: 60 }
- name: ticketsUncached
  kind: remote
  operation: listTickets
  pushdown: []
""")
    write(bad, "realm.yml", "name: bad\ndescription: Tickets.\n")
    write(bad, "views/v.yml", """
- name: TicketsByWord
  description: "Tickets."
  params:
    word: { type: string }
  cypher: |
    MATCH (:Desk {status:'open'})-[:HAS_TICKET]->(t) WHERE toLower(t.subject) CONTAINS toLower($word) RETURN t
- name: UrgentTicketCount
  description: "How many urgent tickets are open right now, counted across every inbox and every agent on the desk; not resolved ones."
  cypher: |
    MATCH (:Desk {status:'open'})-[:HAS_TICKET]->(t) WHERE t.priority IN ['urgent', 'high'] RETURN count(t) AS urgent
""")
    b = realmaudit.audit_realm(bad)
    expect = {
        "T1": "Orphan", "T2": "Ticket", "T3": "Twice", "T5": "Orphan", "P1": "Ticket.status", "P2": "Ticket.amount",
        "P3": "Ticket.status", "P4": "Ticket.openedOn", "P5": "Ticket.replyCount", "K1": "Ticket.domain",
        "J1": "HAS_TICKET", "R1": "ticketsByStatus", "R2": "ticketsUncached", "R3": "ticketsByStatus",
        "R4": "ticketsByStatus", "R5": "ticketsByStatus", "R6": "ticketsByStatus", "R7": "ticketsByStatus",
        "V1": "TicketsByWord", "V2": "$word", "V3": "$word", "V4": "$word", "V6": "UrgentTicketCount", "X1": "realm",
        "V7": "UrgentTicketCount", "R8": "ticketsAgain pushdown subject", "M1": "realm.yml",
        "Z1": "Ticket.openedOn",
    }
    for cid, where in expect.items():
        check(any(where in w for w in ids(b, cid)), f"{cid} did not fire on {where}: {ids(b, cid)}")
    check(any("ttlSeconds" in f["message"] for f in b["findings"] if f["id"] == "Z1"),
          "a ttl cache spelled with ttlSeconds is not called out")
    check(b["score"] < 60, f"a realm breaking every rule scored {b['score']}")
    check(b["mustFailures"] > 0 and b["checks"]["T1"]["failed"] == 2, "MUST failures are not counted")
    t2 = [f for f in b["findings"] if f["id"] == "T2"]
    check(t2 and "the guide stops at 320" in t2[0]["message"],
          "an identity said only after the 320-character cut is not called out as cut")

    # An entry that only adds joins into a type another realm owns (no properties, no description:
    # joins accumulate across declarations) is not a declaration of that type, so it is not asked
    # for the type's description or identity. Its joins are still this realm's, and still held to J1.
    bridge = os.path.join(tmp, "realm-bridge")
    shutil.copytree(good, bridge)
    write(bridge, "types/bridge.yml", """
- name: Firmographics
  userAnchor: false
  virtualJoins:
    - { anchorLabel: Desk, relationship: HAS_FIRMOGRAPHICS, keyField: status, recordKeyField: matchedFor,
        producer: ticketsByStatus, description: "The company behind the desk, matched by its status key." }
    - { anchorLabel: Ticket, relationship: HAS_FIRMOGRAPHICS, keyField: id, producer: ticketById }
""")
    jb = realmaudit.audit_realm(bridge)
    check(not [w for cid in ("T1", "T2", "T3", "T5") for w in ids(jb, cid)],
          f"a join-only entry is scored as a type declaration: {[(f['id'], f['where']) for f in jb['findings']]}")
    check(any("HAS_FIRMOGRAPHICS" in w and "Ticket" in w for w in ids(jb, "J1")),
          f"a join-only entry's undescribed join went unflagged: {ids(jb, 'J1')}")

    # Property measures are read by the host: a unit, a currency or a format declared as a field
    # states it, with no word of it in the description, and is not a dropped key.
    measured = os.path.join(tmp, "realm-measured")
    shutil.copytree(good, measured)
    write(measured, "types/t.yml", GOOD_TYPES
          .replace('amount: { type: number, description: "What the customer was charged, in major units of currency (ISO 4217)." }',
                   'amount: { type: number, currency: currency, description: "What the customer was charged." }')
          .replace("openedOn: \"The business day the customer raised it, ISO YYYY-MM-DD.\"",
                   "openedOn: { format: yyyy-MM-dd, description: \"The business day the customer raised it.\" }"))
    m = realmaudit.audit_realm(measured)
    check(not [f for f in m["findings"] if f["id"] in ("P2", "P4", "Z1")],
          f"declared measures are not read as stating the unit and shape: {[(f['id'], f['where']) for f in m['findings']]}")

    # The host's YAML: a key written twice is a problem, not the last one quietly kept.
    write(bad, "types/dup.yml", "- name: Dup\n  description: one\n  description: two\n")
    check(any("written twice" in p for p in realmaudit.audit_realm(bad)["problems"]),
          "a mapping that writes a key twice is read without complaint")

    # ── the same realm in realm.ts reads the same ────────────────────────────
    def node_strips_types():
        """Node 22.6 or later; an older one (a CI runner's default) cannot read realm.ts at all."""
        try:
            v = subprocess.run(["node", "-p", "process.versions.node"], capture_output=True, text=True).stdout
            major, minor = (int(x) for x in v.strip().split(".")[:2])
            return (major, minor) >= (22, 6)
        except (OSError, ValueError):
            return False

    if node_strips_types():
        ts = os.path.join(tmp, "realm-ts")
        os.makedirs(ts)
        definition = {"name": "good", "description": "A helpdesk's tickets, read live. Nothing is mirrored.", "version": "0.1.0",
                      "types": {t.pop("name"): t for t in yaml.safe_load(GOOD_TYPES)},
                      "producers": {p.pop("name"): p for p in yaml.safe_load(GOOD_PRODUCERS)},
                      "views": yaml.safe_load(GOOD_VIEWS),
                      "apis": {"desk": {"url": "desk.json", "type": "openapi"}}}
        write(ts, "realm.ts", "import type { Realm } from '@embabel/realm-types'\n"
                              f"export default {json.dumps(definition)} satisfies Realm\n")
        write(ts, "apis/desk.json", json.dumps(API))
        t = realmaudit.audit_realm(ts)
        check(t.get("source") == "realm.ts" and t.get("score") == 100 and not t.get("findings"),
              f"the realm.ts spelling of a clean realm read as {t.get('error') or [(f['id'], f['where']) for f in t['findings']]}")

        # A fresh clone: an inline type import erases to `import {} from`, which still loads the
        # package, and nothing has installed it.
        inline = os.path.join(tmp, "realm-ts-inline")
        os.makedirs(inline)
        write(inline, "realm.ts", "import { type Realm } from '@embabel/realm-types'\n"
                                  f"export default {json.dumps(definition)} satisfies Realm\n")
        write(inline, "apis/desk.json", json.dumps(API))
        t = realmaudit.audit_realm(inline)
        check(t.get("score") == 100, f"an inline type import with no SDK installed read as {t.get('error') or t.get('score')}")

    # ── realm.ts beyond a type import ────────────────────────────────────────
    def node_strips_node_modules():
        """Node 22.13 or later has the stripper the hooks use for TypeScript under node_modules."""
        try:
            v = subprocess.run(["node", "-p", "process.versions.node"], capture_output=True, text=True).stdout
            return tuple(int(x) for x in v.strip().split(".")[:2]) >= (22, 13)
        except (OSError, ValueError):
            return False

    sdk_index = ("export interface Realm { name: string; description?: string }\n"
                 "export function defineRealm<T extends Realm>(realm: T): T { return realm }\n")
    uses_value = ("import { defineRealm, type Realm } from '@embabel/realm-types'\n"
                  "export default defineRealm({ name: 'valued', description: 'A realm built by a helper, read as shipped.' } satisfies Realm)\n")
    if node_strips_node_modules():
        # The SDK installed from its tarball ships TypeScript source, which Node refuses to strip
        # under node_modules on its own.
        installed = os.path.join(tmp, "ts-sdk-installed")
        write(installed, "realm.ts", uses_value)
        write(installed, "node_modules/@embabel/realm-types/package.json", json.dumps(
            {"name": "@embabel/realm-types", "version": "0.2.0", "type": "module", "exports": {".": "./src/index.ts"}}))
        write(installed, "node_modules/@embabel/realm-types/src/index.ts", sdk_index)
        t = realmaudit.audit_realm(installed)
        check(t.get("realm") == "valued" and not t.get("error"),
              f"a realm.ts using the SDK installed as TypeScript source read as {t.get('error')}")

        absent = os.path.join(tmp, "ts-sdk-absent")
        write(absent, "realm.ts", uses_value)
        e = realmaudit.audit_realm(absent).get("error") or ""
        check("defineRealm" in e and "not installed" in e and "\n" not in e and "    at " not in e,
              f"a value import with no SDK installed is not one line saying to install it: {e!r}")

    # No runtime at all is said, not thrown.
    saved_path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.path.join(tmp, "no-such-bin")
    try:
        no_runtime = os.path.join(tmp, "ts-no-runtime")
        write(no_runtime, "realm.ts", uses_value)
        e = realmaudit.audit_realm(no_runtime).get("error") or ""
        check("none is installed" in e, f"a machine with no Node, bun or tsx is not told so: {e!r}")
    finally:
        os.environ["PATH"] = saved_path

    # ── name heuristics that misfired on real realms ─────────────────────────
    fp = os.path.join(tmp, "realm-names")
    write(fp, "realm.yml", "name: names\ndescription: Words that look like units, dates and money, and are not.\n")
    write(fp, "types/t.yml", """
- name: Player
  description: One cricket player, identified by their player id, with the teams they have played for.
  properties:
    id: { identity: true, description: "The scorer's id for the player." }
    teams: "Every county the player has turned out for, most recent first."
    value: "Whatever the stat holds, as the scorer wrote it."
    candidateId: "The FEN and the first move in UCI, which identify the line."
    depth: "Search depth reached, in plies."
    speed: { values: [bullet, blitz, rapid, classical], description: "Time control class, for a Lichess game." }
    tier: "The plan's tier: `free`, `pro` or `team`; the customer's choice, made at signup."
    durationMs: "How long the innings lasted."
    openedOn: "When the season opened for them."
""")
    n = realmaudit.audit_realm(fp)
    flagged = {f["where"].rsplit(".", 1)[-1] for f in n["findings"] if f["id"] in ("P2", "P3", "P4")}
    for name, why in (("teams", "a unit in ms, from the letters 'ms'"), ("value", "money"),
                      ("candidateId", "a date, from the letters 'dateId'"), ("depth", "a measure with no unit, in plies"),
                      ("speed", "a measure, though it declares its values"),
                      ("tier", "an enumeration with no values, its backticks swallowed by an apostrophe")):
        check(name not in flagged, f"`{name}` is still read as {why}")
    check({"durationMs", "openedOn"} <= flagged, f"a real measure or date went unflagged: {sorted(flagged)}")

    # ── an app-only realm is scored on its description ───────────────────────
    app = os.path.join(tmp, "realm-app-only")
    write(app, "realm.yml", "name: app-only\ndescription: Pull-request throughput as a page, read from another realm's view.\n")
    write(app, "apps/index.html", "<!doctype html><title>page</title>\n")
    r_app = realmaudit.audit_realm(app)
    check(r_app["score"] == 100 and set(r_app["checks"]) == {"M1", "M2"},
          f"an app-only realm scored {r_app['score']} on {sorted(r_app['checks'])}")
    write(app, "realm.yml", "name: app-only\n")
    r_app = realmaudit.audit_realm(app)
    check(r_app["score"] == 0 and ids(r_app, "M1"), f"an app-only realm with no description scored {r_app['score']}")
    shutil.rmtree(app)

    # ── a directory of checkouts ─────────────────────────────────────────────
    shutil.copytree(good, os.path.join(tmp, "realm-good-fix-thing"))
    shutil.copytree(good, os.path.join(tmp, "realm-spike"))
    shutil.copytree(good, os.path.join(tmp, "realm-good-experiment"))
    os.makedirs(os.path.join(tmp, "not-a-realm"))
    names = [os.path.basename(d) for d in realmaudit.realm_dirs(tmp)]
    check("realm-good" in names and "realm-bad" in names, f"a directory of checkouts missed one: {names}")
    check(not any(n in names for n in ("realm-good-fix-thing", "realm-spike", "realm-good-experiment", "not-a-realm")),
          f"a working copy or a non-realm was audited: {names}")

    with contextlib.redirect_stdout(io.StringIO()) as out:
        status = realmaudit.run_audit(good, as_json=True)
    check(status == 0 and json.loads(out.getvalue())["score"] == 100, "--json on one realm is not its result")
    with contextlib.redirect_stdout(io.StringIO()):
        check(realmaudit.run_audit(bad, fail_under=90) == 1, "--fail-under did not fail a low score")
        check(realmaudit.run_audit(good, fail_under=90) == 0, "--fail-under failed a clean realm")
        check(realmaudit.run_audit(os.path.join(tmp, "not-a-realm")) == 2, "a directory with no realm is not refused")
    with contextlib.redirect_stdout(io.StringIO()) as out:
        realmaudit.run_audit(tmp, markdown=True)
    check(out.getvalue().startswith("| Realm | Score |") and "realm-good | 100" in out.getvalue(),
          "--markdown is not a score table")

    # The command, end to end, as a person types it.
    run = subprocess.run([sys.executable, os.path.join(ROOT, "embabel"), "realm", "audit", good],
                         capture_output=True, text=True, env={**os.environ, "EMBABEL_INSTANCE": ""})
    check(run.returncode == 0 and "score 100" in run.stdout, f"`embabel realm audit` answered {run.returncode}: {run.stdout}{run.stderr}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if failures:
    print("realm audit:")
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)
print("realm audit: ok")
