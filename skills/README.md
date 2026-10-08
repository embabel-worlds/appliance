# Embabel Worlds skills

Skills for a coding agent working against your own Embabel appliance — a knowledge graph
and agent runtime that you install and run yourself. They survey a business for realms and
agents worth building, author views, handlers and apps, check every figure against the
source system, and diagnose a realm or an install that is not working.

A skill is instructions, not code: it teaches a coding agent how to use the appliance well,
and it runs on your machine with your own tools.

That last part is why these are skills and not MCP tools. The MCP surface reaches
the appliance and deliberately reaches nothing else — no filesystem, no shell. But
the evidence about what a person *could* build a realm from is on their disk: the
`docker-compose.yml` with a Postgres in it, the OpenAPI spec, the half-finished
realm checkout. A skill can see that; a tool on the MCP surface cannot, and
shouldn't be able to.

## Install

This folder is also a Claude Code plugin, `embabel-worlds`: the skills below plus a
connection to your appliance at `/mcp/code`, the endpoint for building on it. You need an
appliance first — it is one command, in the [repository README](https://github.com/embabel-worlds/appliance/blob/main/README.md#quick-start).

```text
/plugin marketplace add embabel-worlds/appliance
/plugin install embabel-worlds@embabel-worlds
```

Claude Code asks for two values when the plugin is enabled. Change them later with
`/plugin configure embabel-worlds@embabel-worlds`.

- **Appliance URL.** Embabel Worlds answers on `http://localhost:11043`, Embabel Me on
  `http://localhost:11042`. An appliance on another machine is its `https://` address.
- **API key.** Make one in the console under *Keys to this world*, or use the token
  first-run setup minted. It is kept in the system's credential store, not in a settings file.

If first-run setup already ran `claude mcp add embabel` for you, remove that entry
(`claude mcp remove embabel`) — otherwise the same tools arrive twice.

The plugin works in Claude Code only. Chat and Cowork in the Claude apps skip an MCP
server whose address each person configures, and every appliance has its own address.
Reaching an appliance from those apps needs a public `https://` address and an OAuth
sign-in, and the appliance does not offer that sign-in yet.

## What the plugin sends, and where

Everything goes to the appliance URL you configured, and nowhere else. The plugin has no
hooks and runs no code of its own: it is the skills below, as text, and one MCP connection
that carries your API key as a Bearer token. What the appliance itself sends out — model
calls to the provider you chose, the realms you installed, a daily anonymous usage count —
is listed in full in [What stays on your machine](https://github.com/embabel-worlds/appliance/blob/main/docs/guide/privacy.md).

## scout-realms

Surveys the directories you approve, works out with you where a realm would add
value — a join onto what the world holds, LLM intelligence the source system
lacks, or plain capability — and installs the ones you agree to. It asks before
it reads anything, and proposes before it writes anything.

Use it when a fresh appliance is a blank page: `learn_sources` on a new install
returns nothing, because nothing has been connected yet, and this is the thing
that finds what to connect.

## scout-agents

Finds work in a business that nobody has automated — the same note written by hand
after every failed payment, calls booked every Monday from the same list, a view run
each week and then acted on — counts it, and proposes the colleagues to hire for it.
Where an installed realm already proposes an agent for that work, it says adopt that
one rather than drafting a second. It records suggestions for the console's Agents
window and drafts only what you choose, unsigned and off duty.

## appliance-doctor

For when the appliance itself is the problem — a failed install, containers that
restart, a console that never connects, a wizard that stops with an error. It
assumes the MCP surface is unavailable, because if it were up this would not be
the situation, so everything in it runs on the user's own machine with docker and
a shell.

Written for the case where the person asking is not a Docker expert and does not
want to become one today: run the commands yourself, translate the log rather
than pasting it, one action at a time. It also carries the list of observations
that LOOK like evidence and are not, each of which has cost somebody hours.

**Fetchable on its own**, unlike every other skill here, and deliberately: the situation it
covers is one where the install never produced a checkout to read it from.

```bash
mkdir -p ~/.claude/skills/appliance-doctor && curl -fsSL \
  https://raw.githubusercontent.com/embabel-worlds/appliance/main/skills/appliance-doctor/SKILL.md \
  -o ~/.claude/skills/appliance-doctor/SKILL.md
```

## realm-doctor

Diagnoses a realm that is not behaving: installed but answering nothing, verbs
missing or stale, degraded status, empty queries, schedules that never fire.
A symptom-first runbook — every entry in it was a real failure diagnosed on a
live appliance, which is why the checks go where they go.

## embabel-client

Helps you write apps that CALL the appliance over REST — running saved views,
querying the graph, invoking verbs — from the server's own OpenAPI spec. Its
center of gravity is a design stance: query logic belongs in saved views on the
server, and the app stays a thin typed client of `POST /api/v1/views/{name}/invoke`.

## vibe-apps

Builds single-page apps served by the world and fed by its data through the app
runtime. Its discipline: build the data before the chrome (run the views first),
then verify like a user — open the served page and watch it answer on live rows.

## how-it-works

The page every app ships behind a discreet footer link: the data and where it comes from,
each named view with the Cypher that actually runs, what an empty panel means, and — with
VOICE.md enforced hard — what the appliance did that a conventional stack could not. It
lives inside the app's own HTML, because a separate file detaches from every fork.
Mandatory at handover and cheap by design: every fact on it is something vibe-apps and
relentless-testing already made the builder verify.

## world-atlas

Interrogates a world systematically — views, capabilities, schema, counts,
realms, apps — and writes a compact atlas. The first move before prospecting,
app-building or querying; the other skills pick up from its gap list.

## world-authoring

Builds a capability into the user's world directly or into a realm — the user
picks the target, the authoring is the same. Saved views (including intelligence
views that classify and synthesize in-query), handlers on cron or signals, and
personal apps. Its own ground is the world lane, which no other skill covers;
realm-format depth stays in realm-authoring, and a world artifact that proves
out gets promoted into a realm, where its hand-verification becomes a battery.

## VOICE.md

The register every skill runs in: the appliance's own — verdict first, numbers not adjectives,
zero filler — with an explicit blacklist of LLM English and STE-style structure for procedures.
A connected world's persona layers on top; the rules are the floor.

## relentless-testing

Adversarial verification against ground truth: reconcile every figure with the source system
directly, run the natural-language battery, assert the same fact equal everywhere, click the
app in a real browser, ship the harness with the realm. Born from three trivially-found
failures in one evening; exists so there is never a fourth.


## tour-authoring

Writes a tour: a short guided walk a surface runs against the vocabulary it publishes —
narrate, open a panel, fill a field, run a view, hand control back. Ships as content in a
realm or a world, and travels as a file.

## Without the plugin

Symlink a skill instead, so a `git pull` updates it too:

```sh
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/scout-realms" ~/.claude/skills/scout-realms
```

Then ask Claude Code what realms you could build. Copy the directory instead of
linking it if you would rather pin the version you have.
