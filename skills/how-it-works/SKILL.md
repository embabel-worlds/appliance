---
name: how-it-works
description: Give an app its "How it works" page — a discreetly linked, in-page explanation of the data model, the named views with the Virtual Cypher that actually runs, the handlers behind it, what an empty panel means, and what the appliance did here that a conventional stack could not. Mandatory at handover of any new world or realm app; use it also to retrofit an app that lacks one, or when a user asks how an app works or why a figure looks wrong.
---

Every app ships a How-it-works page: a discreet footer link that reveals an in-page
explanation of the machinery — the data, the views with their actual Cypher, the handlers,
what an empty panel means, and what the appliance did that a conventional stack could not.
It is mandatory at handover and cheap to build, because every fact on it is something the
authoring and testing disciplines already made you verify. Writing it is assembly, not
research — and a section that CANNOT be assembled from what you verified is a testing gap,
not a writing problem; close it first.

The page serves three readers: the user three months on, wondering whether a number is
stale; whoever they show the app to, asking where a figure comes from; and the next agent
asked to change the app.

## Where it lives: inside the app, never a separate file

The page is a section INSIDE the app's own HTML — hidden by default, shown by the footer
link (a `#how-it-works` hash target works and survives reload) — not a second `.html`. The
reason is mechanical: `vibe_app_duplicate` carries only `<stem>.css` / `<stem>.js` siblings,
so a separate page detaches from every fork and copy, and an explanation that can separate
from its app eventually will — and then it lies. In-page, the explanation travels wherever
the app goes and dies when the app dies.

The link is one line of small text beside the Embabel banner: "How it works". Not a nav
item, not a button. Somewhat hidden is the design — it must never compete with the app's own
question, and must always be there when someone goes looking. The section uses the app's own
theme; Cypher renders in `<pre>` blocks that scroll horizontally rather than break the
layout.

## The four sections, always in this order

A fixed structure is the point: someone who found this page on one app knows where to look
on every other.

**1. The data.** Each type the app reads: which realm feeds it, from which source system,
how it anchors to the rest of the world, and how fresh it is. Producer cache TTLs and
handler schedules decide whether "now" means now or means this morning, and the reader
deserves to know which.

**2. The questions.** Every named view (and lens) the app invokes — the manifest is the
checklist. For each: the question it answers as a sentence, its parameters with defaults,
and the actual Virtual Cypher, read back from the server at writing time. Where a view
judges — `classify`, `synthesize` — show the rubric: the label definitions ARE the meaning
of the words on screen, and this page is the one place a user can check them.

**3. When it looks wrong.** The empty and failure cases forced under `../relentless-testing/`
L6 become entries here: what an empty panel MEANS (no matching data, a parameter excluding
everything, a source unreachable), what to check, and what is worth reporting. Include the
staleness case — "figures refresh every N minutes; a mismatch with the source inside that
window is the cache, not a bug" — and where deeper diagnosis lives.

**4. What made this possible.** The Embabel section, and the one with guardrails. Concrete
mechanism contrasts only: "the at-risk label is computed by `classify()` inside the query
that fetches the rows — on a conventional stack that is a separate batch pipeline and a
staleness window", "this page asks the world by view name at load; there is no backend to
deploy or keep patched". Every claim points at evidence elsewhere on this same page.
`../VOICE.md`'s banned list applies to the page text doubly: one "seamless" and the reader
correctly discounts the whole page as marketing.

## Read back, never remember

Assemble from the live artifacts, not from the conversation: `vibe_app_read` for what the
app actually calls, the views API (or `realm_read`) for each view's saved spec and Cypher,
`read_action` for handler bodies and schedules. The Cypher shown must be the Cypher that
runs. A how-it-works page that drifts from its app is worse than none — it converts
confusion into false confidence.

## Verify it like any other surface

- Fetch the served page; confirm the link is present and the section renders.
- Both directions: every view the app invokes appears in section 2, and every view section 2
  names exists on the server with the parameters it claims.
- After ANY change to a view, handler, or the app's calls, the page is part of the change —
  update it in the same edit, or it starts lying at that moment.

## Retrofit

An existing app without the page gets one by the same process: `vibe_app_read` to find its
calls, read back the specs, write the four sections, save, verify. For a realm-shipped app,
either fork it (`vibe_app_duplicate` under the same name shadows the original for this user)
or add the section to the realm's `apps/` file so every installer gets it — the realm is the
better home when the explanation is true for everyone.
