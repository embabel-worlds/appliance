---
name: fresh-appliance
description: Prove the appliance installs and works from NOTHING — wipe to a fresh install with the user's consent, walk first boot, then climb the acceptance ladder from container health to realm batteries to authoring surfaces. Use before cutting a release, after any change to install.sh / setup.py / compose files / first-boot behaviour, or when asked to test the appliance end to end.
---

A development skill for this repo. It does not ship: the product skills in `skills/` teach
users' agents; this one exists because the fresh-install experience is the one thing a
developer's long-lived appliance never exercises. Every rung below either found a real
regression once or guards a promise the installer makes in writing.

## What fresh costs — say it before running anything

The compose project name is a constant (`embabel-appliance`, setup.py), so this machine
runs ONE appliance: there is no side-by-side test instance, and a fresh install is paid for
with the current one's state. `./worlds.py --fresh` says exactly what dies before it asks —
let it, and get explicit consent. What survives, so you can say so plainly: `.env` and
`secrets.env` (files, not volumes — re-setup asks nothing), the realm checkouts under
`realms/`, and every container in OTHER compose projects (`odoo-demo` among them, which is
why the realm-odoo battery still has a source system to reconcile against afterwards).

## Install

- `python3 scripts/drive-install.py --fresh` runs it under a PTY, answers the questions,
  saves the transcript, and asserts on what a person actually saw — raw log lines, an
  echoed password length, a stale org reference, both MCP doors, and that it finished.
  Prefer it: every check is a regression somebody hit, and `--check <transcript>` re-runs
  the assertions against a saved run without reinstalling. Drive it by hand when the point
  IS the hand-driving (a new question, a prompt's wording, the Tab completion on the realm
  directory — a harness that always presses Enter never sees those).
- `./worlds.py --fresh` for the worlds door, `./me.py --fresh` for me — ask which door the
  change under test affects; worlds is the default here.
- Watch the first-boot log it streams. A stack trace swallowed by a healthy-looking wizard
  is exactly what this skill exists to catch.
- **A pipe is not a terminal.** `printf '...' | ./worlds.py` tests a different program:
  `isatty()` is false, so the realm-directory question is never asked at all, getpass warns
  it cannot control echo, and colour switches off. That is why the harness uses a PTY, and
  why a "it installed fine" from a piped run proves less than it looks.
- To test the real new-user path — the `curl | sh` installer — run `install.sh` with
  `EMBABEL_REPO`/`EMBABEL_REF` pointed at the branch under test and `EMBABEL_HOME` at a
  scratch directory. Same single-instance constraint: down the checkout instance first.

## The ladder

Climb in order; a rung that fails stops the climb — diagnose (realm-doctor has the
symptom-first runbook), fix, and start again from L0, because the fix may have changed
what a fresh boot does.

**L0 — containers.** Every service in the project is up and the app container reports
healthy (`docker ps`, health column), not merely running.

**L0.5 — the install READS right.** What a first-timer saw, not just what happened.
`scripts/drive-install.py` asserts most of it; the judgement calls are yours. No raw
` WARN `/stack-trace lines — the follower streams the designed operator block and ERROR,
and counts warnings into one line, because a JVM's warnings are ours and not the
operator's. No secret measured back at them (a password confirms `OK`; an API key still
shows a count, since a truncated paste is its failure mode). Every repo named in the
correct org — a GitHub rename redirect keeps a stale reference working until the day the
old name is reused. And the closing block ends on the two MCP doors, which is what anything
agentic connects to.

**L1 — surfaces serve.** The worlds API answers on its port (11043 unless `.env` says
otherwise), the console serves on its (11044), Neo4j browser and bolt answer, and an MCP
connection lists capabilities (`available_capabilities`). Use the admin credentials the
wizard just printed — retyping stale ones tests your memory, not the install.

**L1.5 — both doors, and they differ.** `/mcp/chat` is the assistant surface
(`embabel-chat`); `/mcp/code` is the building surface (`embabel-code`). Handshake each,
diff their `tools/list` — `learn_*` on the code door only, `personality`/`memory_*` on the
chat door only — and CALL a code-door-only tool, because the tool LIST and the tool
DISPATCH have been wrong independently of each other. Both doors carry the world's
persona: a coding agent without it falls back to its own voice, which is what the persona
exists to replace. Handshake bare `/mcp` too: it is an alias of the chat door — a servlet
forward, so a client must complete a real `initialize` there and not merely get a 200.

**L2 — the empty world is honest.** Before any realm: ask the world a question
(`kg_ask`). The pass is an honest empty — a stated absence, never invented rows, never an
error. The empty case is the first thing a new user sees.

**L3 — realm lifecycle.** Install a realm from the local checkout (realm-odoo is the
fullest), confirm `realm_status` active, edit nothing, `realm_refresh`, still active.
Degraded-with-reasons is a fail here: a fresh install plus a known-good realm has no
excuse.

**L4 — seed and reconcile.** Run the realm's seed (gated, so consent again), then its
shipped battery — `tests/verify.sh` with the keys from `secrets.env` — and require ALL
CHECKS PASS. This is the rung that proves the whole stack: producers, joins, views, the
NL surface, the app assets, figures to the cent. relentless-testing owns the discipline;
the realm ships the harness; this skill just insists it runs on a fresh box.

**L5 — authoring works.** Save a view through `POST /api/v1/admin/kg/views` and invoke
it. Create a handler through `action_brief` → `create_action`, dry-run it with
`test_action`, and confirm it appears in the console's Handler Studio. Save a vibe app
and load the page. These are the world-authoring surfaces; a fresh install where they
fail is read-only in disguise.

**L6 — durability.** Restart the app container: state survives, realms still active.
Re-run `./worlds.py` (no flag): idempotent, asks nothing, changes nothing. `--fresh`'s
own promise — that `.env` survival makes re-setup silent — is a rung because it was made
in writing.

## Report, and grow the list

Report the ladder as climbed: each rung, pass or the exact failure with the log path.
What was NOT tested gets stated with the same prominence — a door not walked, a mode not
installed, a battery skipped for a missing key. And the list is append-only in spirit:
any regression a fresh install ever shows that this ladder missed becomes a new rung in
this file, the same way the realm batteries grow by disappointment.
