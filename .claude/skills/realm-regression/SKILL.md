---
name: realm-regression
description: Install EVERY published realm into one world on a server build, record what each one became — the name it is listed under, every loading problem by exact text, its types, producers, verbs, views, apps and keys — run every view it ships, and diff that against the same run on a baseline build. Use before a release, after any change to realm loading, Shinar, the realm.ts mapping or producers, after realm repos change, and every so often by manual trigger to see what has drifted.
---

A development skill for this repo. It does not ship: the product skills in `skills/` teach users'
agents, `fresh-appliance` proves one realm end to end on a new box, and each realm's own battery
proves that realm against its source system. This one asks the question none of those can — did
a server change break ANY realm — because a loader change is tested against the handful of realms
its author thought of, and the realm it breaks is always one of the other sixty.

It is a regression harness, not a health check. A realm that needs a key you do not have, or a
service that is not running, fails here on every build, and that is fine: the diff against a
baseline is what separates a regression from a failure that was already there. A single run read
on its own says how the estate looks; it does not say what the change did.

## When

- Before a release, against the release candidate.
- After any change to realm loading, Shinar captures, the realm.ts → realm mapping, producers,
  view planning, or the realm listing — the server code a realm passes through on its way in.
- After realm repos change in bulk (a spec bump, a converter run), with the SERVER held fixed:
  then the diff is the realms' doing, not the server's.
- Every so often by manual trigger, saving the output as a dated baseline, so a drift nobody
  noticed has a date it started.

## Two targets

**(a) A server built from a branch or worktree, run in-process.** The common case: it tests code
that is not in an image yet. Build and launch it the way `scripts/dev/run-worlds.sh` in the server
repo does — a reactor `package` (never `install`: that rewrites `~/.m2` under every other
worktree on the machine), then the exec jar with `-Dloader.main=com.embabel.world.WorldApplicationKt`
under `PropertiesLauncher`, `GRAPH_TYPE=EMBABEL`, `--enable-native-access=ALL-UNNAMED
--add-modules=jdk.incubator.vector` — but **on a port and a world base of its own**:

    -Dserver.port=8045
    -Dassistant.world-base=$HOME_FOR_THIS_RUN/worlds
    -Dgraph.embabel.journal=$HOME_FOR_THIS_RUN/graph/memory.journal

A developer's server is not a test bed: it holds their worlds, their realms and their graph, and a
run that installs seventy realms into it leaves them there. Pick a free port, a fresh directory,
and kill only the process you started. Export `ORG_ROLE_FALLBACK=admin` (the run needs the admin
routes) and `ASSISTANT_OBSERVABILITY_GUESTTEXTREDACTION_MODE=EXCERPT` (or a realm's own error text
reaches the log as `[guest text redacted]`). Set `SHINAR_JAVY_PLUGIN` to the absolute path of the
plugin `shinar/scripts/fetch-javy-plugin.sh` fetches: without it every realm.ts realm fails at
install, and that is the server's setup failing, not the realm. And list the clones' directory in
`-Dassistant.realm-artifact-capture.local-roots=<realms-dir>`: a path install outside every
capture root is never captured, so it is never admitted, and every realm.ts realm then reports
`HOST_BINDING_FAILED` and answers its apps with 403 — a whole run of failures that are the
launch's, not the server's. The server's own `.env` and
`users-local.yml` provide keys and an account; the account's credentials go to the harness as
`--user` and `REALM_REGRESSION_PASSWORD`.

**(b) A fresh appliance instance.** When the thing under test is an image, not a branch.
`EMBABEL_INSTANCE=<name>` with `EMBABEL_REALMS_DIR` pointed at the pinned clones, installed as
`fresh-appliance` describes; the realms dir is mounted read-only at `/realms`, so pass
`--instance <name> --mount-prefix /realms` and the harness installs `/realms/<repo>`.
`embabel --instance <name> uninstall` afterwards.

Either way: **a fresh world**. A world that already holds realms, data or seeds from an earlier
run answers differently, and that difference lands in the diff as if the server made it. Delete
the world base between runs. Shinar writes its captures read-only, so `chmod -R u+w` it first, or
`rm -rf` stops part way and the next run boots on half a world.

## Pinned realms

    scripts/realm-regression.py pin --realms-dir <dir>

discovers the realms at run time — every non-archived `realm-*` repo in `embabel-worlds`, plus the
realms the default world's `config/realms.yml` installs — clones each at its default-branch HEAD,
and writes `<dir>/realms.lock.json` with every sha. `run` restores each clone to its pinned sha
before installing, so a baseline and a candidate install the same bytes even if somebody pushed
in between. `pin --refresh` moves the pins forward; do it between comparisons, never inside one.
`--only realm-a,realm-b` restricts a pin or a run to a subset; `--add-path <dir>` adds a local
realm that is not published.

Installs go in sorted order, and some realms build on another's types (a scripture realm whose
parents live in a vocabulary realm that sorts after it). The server rolls back an install whose
references do not resolve, so the harness offers the refused ones again, pass after pass, until a
pass installs nothing new; `install.attempt` says which pass took, and `install.firstRefusal`
keeps the first answer. A realm still refused after the last pass is refused on its own merits.
The server rolls an install back for ANY problem whose source is the realm's root, a WARNING
included (a view reading a label no installed realm declares, an API name another realm already
holds), and answers only the generic `REALM_INSTALL_REFUSED`. The cause is in the server's log as
`warning in realms/<name>: …` / `error in realms/<name>: …`; for an in-process target, grep it
and put it in the report, because the JSON cannot carry what the server did not say.

**It is slow, and that is the server's cost, not the harness's.** Every install rebuilds the whole
world, so the n-th install costs more than the first: seventy realms on a laptop took around three
hours to install, and the sweep comes after. Use `--only` while iterating on the harness; run the
full set for the comparison that counts, and start it before anything else.

The default world installs some realms from git on first boot, at whatever their branch says that
day. The harness removes those first and installs the pinned checkout instead, and records what it
removed under `removedFirst`.

## Baseline, then candidate, then diff

Never blame a branch without the baseline. The same run on `origin/main` first, in its own
worktree (`git worktree add --detach ../<name>-base origin/main`), on its own fresh world; then the
candidate, the same way; then:

    scripts/realm-regression.py run --base http://127.0.0.1:8045 --user <user> \
        --realms-dir <dir> --out base.json --label base --server-sha <sha>
    # ... stop it, wipe the world, start the candidate on the same port ...
    scripts/realm-regression.py run ... --out candidate.json --label candidate --server-sha <sha>
    scripts/realm-regression-diff.py base.json candidate.json

The diff prints only what moved, exits 1 when anything did, and refuses to compare a realm whose
pinned sha differs between the two. To compare against an older state, keep a run as a dated
baseline (`baseline-2026-11-07-<sha>.json`) and diff against that; without a recorded server sha
it is a number with no denominator.

Run each pair on the same machine with the same `.env`. A key present in one and absent in the
other changes which views answer, and that is the most common false regression there is.

## Reading the output

`<out>.json` is keyed by repo name and sorted throughout; `<out>.txt` is the table. For each realm:

- **`listed`** — the name and version the realm listing shows. `underHash` (listed under its
  capture hash) and `zeroVersion` (`0.0.0`) mean the server could not read the realm's manifest
  and a user sees a realm with no name. `absent` means it installed and is not listed at all.
- **`problems`** — every loading problem as `GET /api/v1/realms/{name}/problems` gives it:
  severity, source, exact message, location. `statusProblems` is `realm_status`'s view of the same.
  (`realm_validate` is not used: it checks the world's authoring DRAFT of a realm, and an installed
  realm has none, so it reports every realm as missing its manifest.)
- **`surface`** — the types, producers, verbs, APIs and schedules the realm actually granted once
  loaded, plus `handlers`, `keys` it asks for, and `apps` (each page under `apps/` and the status
  the server answers it with).
- **`views`** — every view the realm ships, invoked on its defaults: `OK` (rows), `EMPTY` (no rows,
  an honest answer on an empty world), `FAILED` with the envelope's `errorCode` and the engine's own
  reason in `detail` (first two lines, from the admin run route — the public envelope deliberately
  says nothing), `FAILS_FIRST_TOUCH` (failed, then answered on a second call), `ERROR` (non-200),
  `TIMEOUT` (past `--view-timeout`, default 60s), `SKIPPED_NEEDS_PARAMS` (a parameter with no
  default).

**A regression** is anything the candidate does worse with the realms held fixed: a new problem
text, a realm newly under its hash or absent, a surface that shrank, a view whose outcome went from
`OK`/`EMPTY` to anything else, or from `EMPTY` to a failure. **Pre-existing** is anything both runs
share, however bad. Report both, separately: pre-existing failures are the backlog, and burying
them under "no regressions" is how a realm stays broken for a year.

Watch for the false ones. `OK` ↔ `EMPTY` on a view over a live source is the source, not the
server. A `TIMEOUT` that does not repeat is load (`--jobs` defaults to 4; drop to 1 to rule it out).
A `detail` that names a schema suggestion may move when ANOTHER realm's labels change.

## Parity: YAML and realm.ts twins (forthcoming)

A converter will produce a realm.ts-only twin of each YAML realm. The harness is shaped for it now:
install a twin set with `--twin-suffix <suffix>` and each twin records `twinOf`; then
`scripts/realm-regression-diff.py --parity <run.json>` compares every twin with its original, and
`--parity a.json b.json` compares across two runs by the same key, ignoring what differs by format
alone (`format`, `sha`, the realm's name, definition file extensions). A twin that differs in any
other fact is a gap in the realm.ts mapping. Twins and originals declare the same labels, so a
run holding both in one world may itself report clashes — prefer two runs, one per set.

## Report

Report the server sha, the realm count and lock file, how many installed clean, how many with
ERROR problems, how many listed under a hash, and the view totals. Then the diff, regressions first.
What could not be tested gets the same prominence: a realm that would not clone, a server that
would not boot, keys that were absent. Findings go to the server repo as issues, never into this
public repo as diagnostics, and any way a realm has broken that this run missed becomes a new field
in the output.
