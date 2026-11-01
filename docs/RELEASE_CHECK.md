# Checking a release

An appliance build is worth shipping when a stranger can install it, finish setting it up,
add realms from git and get true answers out of them — and when it still holds all of that
after a restart. Most of that a machine can check, and should, on every build:
`scripts/release-check.py` does it in one command. What is left is a short list of things
only a person can judge, at the end of this page. The split is the point. A release check
that needs an afternoon of clicking gets skipped; one that runs unattended and hands you six
judgement calls gets done.

## Run it

```bash
export ANTHROPIC_API_KEY=…   # and/or OPENAI_API_KEY — every one that is set is connected
python3 scripts/release-check.py --secrets ~/realm-keys.env
```

That installs a fresh appliance on the **stable** release, climbs every rung below, prints a table and
removes the appliance again. It runs beside whatever this machine already has: the install
goes into a temporary directory as its own instance (`relcheck` by default), with its own
port block, and nothing outside that directory is touched — not your `embabel` command, not
your shell profile, not your coding agents' MCP settings. Expect twenty to forty minutes,
most of it model calls and realm fetches.

| Flag | |
|---|---|
| `--release beta` | install another release instead of stable: `beta`, `nightly`, `canary`, or one build — a release (`0.4.1`), a candidate (`0.5.0-rc.1`) or a main build (`sha-abc1234`). It goes in the way a user's does, as `EMBABEL_RELEASE` to the installer, which writes it into the instance's `.env.<instance>`; the run checks that line is there, so a later `embabel up` stays on it, and that the running image is the release's tag. `--version` is the old name and still works. A legacy `EMBABEL_VERSION` exported in your shell is not passed on, since it would outrank the release |
| `--secrets FILE` | realm keys (`ODOO_API_KEY=…`, `LAGO_API_KEY=…`, `CHATWOOT_API_TOKEN=…`, the names each realm's `apis.yml` asks for), copied to the instance's `secrets.env` before the realms go in. Without it the realms install, and every view that needs a source says so as `SOURCE_UNAVAILABLE` |
| `--realms a/b,c/d` | the realms to install from git. Defaults to the account-health set: `realm-business-vocabulary`, `realm-chatwoot`, `realm-lago`, `realm-odoo`, `realm-account-health` |
| `--battery DIR` | a realm checkout whose `tests/verify.sh` runs last, against this instance, with `FROM_TEXT=1` |
| `--battery-python PATH` | the `python3` the battery runs under. Batteries are the realm's own scripts, and some import PyYAML; when the `python3` on your PATH lacks it, the run says so rather than reporting the realm as broken |
| `--keep` | leave the instance installed, to look at afterwards. The table's last line says how to remove it |
| `--instance NAME` | another instance name, if `relcheck` is in use |
| `--ref BRANCH` | install the appliance from a branch rather than `main` — for checking an installer change before it merges |
| `--plan-fixture FILE` | replace the planner rung's sample email and expectations (see below) |
| `--strict` | a KNOWN issue fails the run too |

The exit code is non-zero when any check FAILED. The JSON report, and a copy of the install
transcript, are written to the system temp directory; the last line of the run names them.

The realms' source systems must be reachable from the appliance. For the default set that
is the account-health demo stack (Odoo, Chatwoot and Lago); its seed data is also what the
planner rung's expectations are written against.

## What each rung proves

**L0 install.** The real `curl | sh` path: `install.sh` downloads the appliance and hands
over to the wizard, all under a pseudo-terminal by `scripts/drive-install.py`, whose own
checks must pass — no raw JVM warnings on screen, no secret measured back, both MCP doors in
the closing block, and that it finished. Then the container reports healthy.

**L0.1 image.** Which image is running, by tag and digest, and that the digest is the one the
registry publishes for that tag. For a release that moves (stable's `:latest`, beta, nightly,
canary) a mismatch means the install started on a stale local pull and every rung after it
would be checking some other build. The **release** row says which release was installed, the
tag it resolves to, and what the running build calls itself — version, revision and build
time, from the image's labels — and `embabel release` must agree with it.

**L0.5 setup link.** The `…/#setup=` link the install printed opens: HTTP 200 on its path.
A doubled slash in it once made every fresh install print a link the server refused with 400,
and nothing else noticed, because nothing else reads that link.

**L1 onboarding.** The account, every provider key in the environment, the terms (only if the
terminal run did not already record them) and `/complete`, through the console's own setup
API with the token from the container. Then `models/in-use` names real models. This proves
the API, not the page — the page is on the list below.

**L1.5 MCP.** `/mcp/code`, `/mcp/chat` and bare `/mcp` each complete a real handshake; `/mcp`
lists exactly the chat door's tools; the code door has `learn_*` and the chat door `memory_*`;
and `plan` is on both and, CALLED with a goal that deletes, answers `refused`. Called rather
than listed because the list and the dispatch have been wrong independently.

**L2 the empty world is honest.** Before any realm, `kg/ask` "Which customers owe us money?"
returns no rows — `NOT_IN_GRAPH`, never an invented customer. It is the first thing a new user
sees.

**L3 realms from git.** Each realm through the MCP `install_realm` tool, then `realm_status`
active (degraded with only notes passes and the note is shown). Then every view those realms
ship, run on its defaults: a view that loads is not a view that answers. A view whose
parameters have no defaults is reported `skipped (needs params)`, not failed.

**L4 planner.** Only when realm-account-health is installed. A sample email from a customer
raising two problems, one with an open support case: the dry run is `planned` with a quote;
the run returns the issues with the case beside the right one; the same question in plain
English returns the same answer; with no email the planner says `needsInput`; and a goal over
a property nobody holds is `unreachable`. The email and expectations live in `PLAN_FIXTURE`
at the top of the script, because they depend on the demo data.

**L5 authoring.** A view saved through `POST /api/v1/admin/kg/views` and invoked; an
observe-only action made with `create_action` and dry-run with `test_action`. Both are deleted
again after L6.

**L6 durability.** The worlds container restarted: healthy again, realms still active, the
saved view and action still run. Then `embabel up` re-run with nothing on stdin — anything it
asks fails it.

**L7 battery.** With `--battery`, that realm's own `tests/verify.sh`, and its summary lines.

**Teardown.** `embabel --instance <name> uninstall`, then a check that no container or volume
of that compose project is left. Leftovers are removed and reported as a failure of uninstall.

### KNOWN

A check that fails in a way already accepted for this build is reported `KNOWN` with its
reason — shown, never hidden, and not failing the run unless `--strict`. The list is
`KNOWN_ISSUES` in the script. When a known issue stops reproducing, the run says so: delete
its line then, or it will quietly excuse the next regression that fails the same way.

## What only you can check

Run the check with `--keep` first, so there is an appliance to do these on; its API and
console addresses are in the table. Sign in as `demo` / `terminat8r` unless you passed others.

1. **Finish onboarding in a browser.** The check drove the setup API; a user drives the page.
   Install a second instance by hand
   (`EMBABEL_INSTANCE=relcheck2 EMBABEL_HOME=$(mktemp -d)/w sh -c 'curl -fsSL https://raw.githubusercontent.com/embabel-worlds/appliance/main/install.sh | sh'`),
   open the printed `#setup=` link, and go through the console's onboarding: account, paste a
   provider key (does it say the key works before you move on?), finish. You should land in a
   working console without reading anything twice.

2. **Ask the console chat about a realm, and judge the answer.** In the kept instance's
   console, ask one question the installed realms can answer — "Which accounts are at risk,
   and why?" for the default set. Is it right (compare a figure with the view the check ran),
   does it say where it came from, does it read like the persona and not a raw model?

3. **Open a realm app.** Console → the realm's app (realm-account-health ships one). It
   renders, its panels fill with the same numbers the views returned, and nothing is blocked
   by the console's content policy (an empty frame or a broken image is the symptom).

4. **Connect a coding agent to the code door.** In Claude Code, `claude mcp add --transport http
   relcheck http://127.0.0.1:<API port>/mcp/code --header "Authorization: Basic $(printf demo:terminat8r | base64)"`,
   then `/mcp` shows it connected. Ask a data question ("which customers owe us the most?").
   Watch what it calls: it should find `view_run` and answer from a view, not wander through
   `code_mode`.

5. **Upgrade an existing appliance.** On an appliance installed from the previous release,
   with realms and a saved view, run `embabel upgrade`. Afterwards: same account, same realms
   active, the saved view still there and answering, and the upgrade said what it changed.

6. **Read the install output as a first-timer.** Open the transcript the run kept (its path is
   in the report). Read it top to bottom as somebody who has never heard of Embabel. Does
   every line earn its place, does anything sound alarming that is not, is it clear what to
   do next and where to click?

When all six are good and the table has no FAIL, the build is a release.
