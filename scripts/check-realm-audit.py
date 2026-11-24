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
  (`format:` on a property, `ttlSeconds` for a ttl's `seconds`).
- A directory of checkouts is each checkout, minus `-fix…`, spike and draft working copies and any
  other copy declaring the same realm name as the `realm-<name>` checkout beside it.
- `--fail-under` is the exit status a CI check needs; a directory that is no realm is 2.

Nothing runs but this process (and Node, only for the realm.ts case, skipped where there is no
Node 22.6 or later to evaluate it).
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
    types = types.replace('openedOn: "When it was opened by them."', 'openedOn: { description: "When it was opened by them.", format: date }')
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
