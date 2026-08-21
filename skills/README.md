# Skills

Claude Code skills that ship with the appliance. A skill is instructions, not code:
it teaches a coding agent how to use this appliance well, and it runs on the user's
machine with the user's own tools.

That last part is why these are skills and not MCP tools. The MCP surface reaches
the appliance and deliberately reaches nothing else — no filesystem, no shell. But
the evidence about what a person *could* build a realm from is on their disk: the
`docker-compose.yml` with a Postgres in it, the OpenAPI spec, the half-finished
realm checkout. A skill can see that; a tool on the MCP surface cannot, and
shouldn't be able to.

## scout-realms

Surveys the directories you approve, works out with you where a realm would add
value — a join onto what the world holds, LLM intelligence the source system
lacks, or plain capability — and installs the ones you agree to. It asks before
it reads anything, and proposes before it writes anything.

Use it when a fresh appliance is a blank page: `learn_sources` on a new install
returns nothing, because nothing has been connected yet, and this is the thing
that finds what to connect.

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

## world-atlas

Interrogates a world systematically — views, capabilities, schema, counts,
realms, apps — and writes a compact atlas. The first move before prospecting,
app-building or querying; the other skills pick up from its gap list.

## VOICE.md

The register every skill runs in: the appliance's own — verdict first, numbers not adjectives,
zero filler — with an explicit blacklist of LLM English and STE-style structure for procedures.
A connected world's persona layers on top; the rules are the floor.

## Installing

Symlink, so a `git pull` updates the skill too:

```sh
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/scout-realms" ~/.claude/skills/scout-realms
```

Then ask Claude Code what realms you could build. Copy the directory instead of
linking it if you would rather pin the version you have.
