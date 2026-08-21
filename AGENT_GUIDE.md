# Working with an Embabel appliance

This guidance is for any coding agent connected to an Embabel appliance over MCP — Codex
sessions get routed here by the block setup installs in `~/.codex/AGENTS.md`; other agents can
be pointed at it directly. (Guidance for working ON this repo is `AGENTS.md`.) The server named `embabel` IS the appliance: a world runtime holding
the user's data, realms (capability packs), saved views, and apps. In Worlds mode it starts in
DEVELOPER mode, so realm authoring is available to you.

## First calls, in order — these save you failed guesses

- `available_capabilities` before your first `code_mode` call, every session. It returns the
  `gateway.*` namespaces, data collections and installed skills; skipping it typically costs
  3–4 failed guesses. `describe_namespace` gives exact TypeScript signatures — never guess
  argument names.
- For any question about the world's data: `view_run` with NO arguments first. One call lists
  every saved view, what it answers, and its parameters. A view that fits is one call and
  encodes joins that are easy to get wrong by hand.
- Before writing Cypher: `query_guide` — the live schema. Virtual labels are reached through
  DOORS; a bare `MATCH` on one matches nothing, and the rejection message lists the doors.
- Before authoring a realm: `realm_brief`. The server also ships full Agent Skills over MCP —
  `activate_skill` with a name from `available_capabilities` (e.g. `realm-authoring`) loads the
  complete recipe.

## The full runbooks

The appliance checkout carries detailed skill documents — plain markdown, written for coding
agents. Read the one that matches your task:

| Task | Read |
|---|---|
| Survey what a user's estate could become realms | `skills/scout-realms/SKILL.md` |
| A realm installs but does not behave | `skills/realm-doctor/SKILL.md` |
| Write an app that CALLS the server over REST | `skills/embabel-client/SKILL.md` |
| Build a single-page app the world serves | `skills/vibe-apps/SKILL.md` |
| Map what a world knows and can do | `skills/world-atlas/SKILL.md` |

## Voice

Reply in the appliance's register, not LLM English: verdict first, numbers not adjectives, no
preamble or postamble, no emoji, report what happened rather than narrating what you will do.
The full contract is `skills/VOICE.md`; a connected world's persona layers on top of it.

## Rules that prevent the classic failures

- **Verify by behaviour, never by status.** `realm_status: active` with `problems: []` is not
  proof; call the verb, run the view, get rows. A realm that installs clean and answers nothing
  is still broken.
- **Empty is only an answer once the query is sound.** Read rejections — they name the bad label
  or edge and usually the fix. Non-empty `warnings` means a source failed: few rows means
  "unavailable", not "no data".
- **REST is contract-first.** `GET /v3/api-docs` on the server is the spec; apps call saved
  views via `POST /api/v1/views/{name}/invoke` and keep query logic server-side.
- **Local realm checkouts** live under the mounted realms dir and install by reference
  (`install_realm_from_path`); after editing, `realm_refresh` — and note the gateway namespace
  derives from the DIRECTORY name, not `realm.yml`.
- Credentials go to the encrypted wallet via the tools that ask for them — never into YAML,
  code, or git.
