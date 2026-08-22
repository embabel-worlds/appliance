---
name: vibe-apps
description: Build single-page HTML apps that run OFF an Embabel appliance — served by the world, fetching live data through the app runtime (named views and lenses, typed gateway calls). Use when the user wants an app, dashboard, tracker or tool over their world's data, or wants an existing world app improved.
---

A world app is a single self-contained `.html` the appliance serves and the world's data feeds.
The server owns the contract; your job is to build something excellent INSIDE it, and to verify
the result the way a user would — by opening it and watching it answer.

## 1. The brief is the contract — fetch it, don't freelance

`vibe_app_brief` (pass the user's request verbatim) returns the system prompt for THIS world:
conventions, theme, existing apps, and the typed tool surface. It is generated from the running
server, so it outranks anything you remember. `vibe_app_list` first when you only need to know
what exists — cheaper, and editing beats duplicating.

The parts of the contract that decide success:

- **Data is fetched at RUNTIME, never baked in.** The app loads its runtime and asks the world
  each time it opens: `/api/v1/apps-runtime/v1/embabel.js` for `embabel.views.invoke(...)` /
  `embabel.lenses.invoke(...)`, `/apps-runtime/gateway.js` for typed `gateway.<ns>.<method>`
  calls. Never raw-fetch a gateway path; any other same-origin fetch passes
  `credentials: 'include'`. Typed shapes live at `/apps-runtime/interfaces.ts`.
- **Views are the fast synchronous path; lens invokes are async-by-default** — the runtime hands
  back one promise either way and polls run handles itself. Never open your own `EventSource`;
  the runtime keeps the one connection.
- **A manifest declares dependencies**: one `application/embabel-app+json` v1 block naming the
  views, lenses, realms and gateway namespaces the app relies on. `/apps-runtime/v1/preflight`
  checks them.
- **Fetch discipline**: lazy-load detail on interaction, cache per render, no fan-out across a
  collection on load, and STOP on error/429 — never retry in a loop.
- **The Embabel banner div** before `</body>` — host-guaranteed (auto-injected if omitted), so
  leave room for it in the layout.

## 2. Build the data before the chrome

An awesome app is a thin, beautiful layer over views that already answer the question. So work
in this order:

1. Say the app's core question as a sentence, then find or SAVE the view that answers it
   (`POST /api/v1/admin/kg/views` validates before persisting; a view that could never run
   cannot be saved). Views can carry LLM smarts — classify, summarize, judge — so "the app needs
   intelligence" usually still means "the VIEW needs intelligence".
2. **Run those views yourself, first.** An app built over a view returning zero rows ships a
   beautiful empty state and nothing else. If the rows aren't there, fix the data story before
   writing any HTML.
3. Only then author the page.

## 3. Make it awesome, not merely functional

The floor is the theme CSS and a working table. Aim above it:

- Design for the actual rows you saw in step 2 — real names, real magnitudes, real empty cases —
  not for imagined data.
- Loading, empty and error are three designed states, not an afterthought; the runtime renders
  waiting states for deferred lens results, so build on that rather than spinners of your own.
- Interactions that answer the follow-up question ("and which of those…") beat static charts:
  drill-down on click, a filter bound to a view parameter, detail fetched lazily on open.
- Compute time-relative things ("last 7 days") in the app's own script at load, never bake a
  date in.

## 4. Test EVERYTHING the user will touch — before they touch it

The full ladder — ground truth from the source system, exact reconciliation, the NL battery,
same-fact consistency, browser click-through, the shipped harness — is `../relentless-testing/`.
Run it. What follows is the floor, not the ceiling.

The failure this section exists to prevent, verbatim from the field: a realm shipped with a
verified flagship view and a served app, and the user's FIRST three natural-language questions
all returned zero rows. "I tested my query" is not "I tested their questions."

Non-negotiable before handing anything over:

- **Every view, every parameter.** Run each view by name through the runtime path
  (`POST /api/v1/views/{name}/invoke`), with defaults AND with each declared parameter varied,
  and read the whole envelope — status, outcome, warnings, rows. A view that has never returned
  rows in front of you does not exist yet.
- **The generator is a user too.** Ask `kg_ask` the five questions a person would actually type
  — "how many X", "which X has the most Y", the superlatives and counts — and read the CYPHER
  it generated, not just the row count. Zero rows with an unpinned door in the generated query
  means your schema needs a default-seeded door (`metadata: { identity: "true", default: ... }`),
  not a better prompt. If the generator cannot answer it, the user cannot either.
- **Empty must be loud.** For every surface, force the empty case and check what it says. "0
  rows" with empty warnings on a question the data can answer is a defect somewhere — find it
  before the user does.
- **The app's own calls.** Fetch the served page AND exercise the calls its scripts make
  (views, gateway methods) with curl using the same arguments the script sends. What you cannot
  execute (in-browser JS), say so explicitly when handing over — never imply it was tested.

## 5. Verify like a user, then iterate

To customize an app the world already serves — a realm's app included — start from
`vibe_app_duplicate`, not from scratch: same name replaces what the user sees at the
original URL, a new name is an independent fork, stem siblings come along with references
rewritten. Then edit the copy like any app.

`vibe_app_save` validates; that is not the finish line. `vibe_app_list` returns the app's `url`
— open it (or fetch it) and check: the runtime loads, the views return the rows you saw in step
2, preflight passes, empty/error states render. `vibe_app_inspect` and `vibe_app_read` support
the edit loop; iterate against the live page, not against your memory of it.

Report the app done only when it has answered its core question in front of you, on live data.
An app that saves clean and shows an empty shell is not done — say so and fix the view or the
data first.

## Voice

Output follows `../VOICE.md` (the appliance's `skills/VOICE.md`) — its own register, not LLM English. The
non-negotiables while this skill runs: verdict first, then evidence; numbers, not adjectives;
no preamble, no postamble, no emoji, no narrating intentions — report what happened, not what
you are doing. A connected world's persona sets register on top of these rules, never instead.
