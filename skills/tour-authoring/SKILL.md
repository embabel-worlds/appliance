---
name: tour-authoring
description: Write a TOUR — a guided walk a surface runs against a vocabulary it publishes, shipped as content in a realm or a world and exchangeable as a file. Use when building or improving a realm (a realm that adds capability should offer a walk through it), when a user asks for onboarding, a guided demo, a walkthrough or "show me how this works", or when turning a recorded draft into a real tour.
---

A tour is a small, closed script that a UI runs against a vocabulary the UI itself
publishes. It narrates, opens panels, fills fields, runs views, and hands control back at
the steps the user should do themselves — and it can be paused, left and resumed.

**Offer one whenever you build a realm.** A realm that adds types, views and verbs has just
added capability nobody can see. `hints/` tells a user one thing at a time and `next-steps/`
asks one question; a tour is how somebody who has never seen your realm is walked through
what it made possible. Ship it in `tours/` beside `views/`, and it arrives and leaves with
the realm.

## The shape

```yaml
- id: EsgFirstLook
  name: ESG, first look
  description: >-
    From an empty corpus to a cited observation, on one company you choose.
  params:
    domain:
      ask: "Whose disclosures shall we read? A domain — e.g. acme.com"
  steps:
    - say: |
        Markdown narration. This is the half that teaches.
    - open: panel.documents
    - set: field.domain
      to: "{{ domain }}"
    - invoke: button.populate
      say: This is the slow part — it reads real pages.
      doneWhen: "MATCH (d:Document) WHERE 'esg' IN d.tags RETURN d LIMIT 1"
    - wait: state.ingest.idle
      timeout: 10m
      meanwhile: Fetching and chunking.
    - run: view.EsgCoverage
      expect: "documents > 0"
      else: Nothing landed — try the investor-relations domain.
    - open: panel.query
      by: user
      hint: "Open Query — it is in the tab strip."
```

Eight verbs, and no ninth: `say`, `ask`, `open`, `set`, `invoke`, `run`, `wait`, `expect`.
Targets are `kind.name` — `panel.x`, `field.x`, `button.x`, `view.X`, `state.x.y`. A tour
may name **only** what a surface declares in its dictionary; anything else is refused before
the tour starts, with the missing target in the message.

`say:` beside another verb is that step's narration. `say:` alone is a narration step.

## The two things that make a tour worth running

Everything else is mechanics. These are the craft.

**`say:` — narrate the WHY, not the what.** The user can see that a panel opened. What they
cannot see is why this realm bothers, what the number means, or what would be wrong to
conclude from it. A tour whose narration reads "Now click Populate" has wasted the only
channel it had. Write the sentence you would say aloud sitting next to them.

**`doneWhen:` — Cypher that is true once the step need not run again.** A row means
satisfied, so the step is skipped. This is what makes a tour resumable: somebody pauses at
step three, comes back tomorrow, and is walked from four rather than made to redo the first
three. It runs as the user, over the same surface `kg_query` sees.

**It can name the tour's parameters.** Everything collected so far is bound, so the
condition can be about the thing the user just named — which is usually the only useful
form for an expensive step:

```yaml
    - run: view.EsgExtract
      with: { domain: "{{ domain }}" }
      doneWhen: |
        MATCH (o:EsgObservation) WHERE o.domain = $domain RETURN o LIMIT 1
```

Note the two syntaxes and do not mix them up: `{{ domain }}` is the tour's own
interpolation, used in `to:` and `with:`; `$domain` is a Cypher parameter, used in
`doneWhen`, and the appliance binds it.

Write it against the world as it IS, never against what somebody clicked. And be careful
which absence you are asking about — `MATCH (v:ConfigView)` is answered by the views every
appliance ships with, so it reports a fresh world as one whose owner has been busy;
`WHERE v.userAuthored` is the question worth asking. The same trap sits under realms
(installed is not built) and documents (ingested is not extracted).

Put `doneWhen` on every step that is **expensive or not idempotent** — anything that spends
a model call, fetches the network, or writes. Cheap idempotent steps (`open`, `say`, a
refresh) do not need one.

## `by: user` — the half that makes it a lesson

A tour that only ever performs is a demo the user watched. Hand over the steps that are
worth learning by doing: the tour points, explains, and waits.

```yaml
    - open: panel.query
      by: user
      hint: "Open Query — it is in the tab strip."
```

Rule of thumb: the tour does the tedious and the risky; the user does the thing they will
have to do again on their own.

## Portability: what a realm may name

A realm has no idea which surface its user is on, and the Me app's panel names are not the
console's tab names. But `view.*` and `verb.*` resolve against the WORLD rather than the
layout, so:

- A realm tour that leans on `run:` runs on every surface.
- A tour that leans on `open:` and `invoke:` must pick a surface and say so: `surface: me`
  or `surface: console`. Omit `surface:` only when every target is in both dictionaries.

Check before you ship: read the surface's dictionary rather than guessing. The Me app builds
its own from `data-panel`, `data-field` and `data-control` in `me-app/index.html`.

### `requires:` — say what the tour needs

A tour may list targets it needs but does not `open:` directly, and a surface refuses the
whole tour up front rather than dying at step 6:

```yaml
- id: HandlerWalk
  requires:
    - panel.handlers
    - control.handler-new
```

Refusal names what is missing and which surface is missing it, which is a better failure
than a tour that starts confidently and stops halfway.

**What `requires:` does NOT cover, and this is the trap.** `view`, `verb` and `app` are
*dynamic* kinds — a world's views come from its realms, so a surface cannot know at load
time whether one exists, and it declines to refuse rather than block a tour that would
have worked. That means a tour naming `view.PlaceDossier` imports cleanly into a world
without `realm-uk-streets` and then stops when it reaches that step.

So a tour whose steps run a realm's views should **ship inside that realm**, where the
question cannot arise. Send a standalone file only when every `run:` target is one the
recipient's world already has.

## Pictures, for the steps that happen somewhere else

Narration is markdown, and markdown image syntax works — but **only for an asset this appliance
serves**, named by a rooted path:

```yaml
- say: |
    One terminal window, and you are done. Roughly what it looks like:

    ![A terminal running claude against this world](/apps/world/tour-claude-code.svg)
```

Anything else — `https://…`, `//host/…`, a bare relative name, a `data:` blob — is **deleted**
before the caption is painted. That is not a limitation to work around. A tour is a file people
exchange, so an image on a host its author controls is a beacon: it reports that the tour was
opened, when, and from where, before the reader has agreed to anything.

Put the file in the world's `config/apps/` (served at `/apps/world/<name>`) or a realm's `apps/`
(served at `/apps/<realm>/<name>`). SVG, PNG, WEBP and friends all serve.

**Use one only where the app cannot show the thing itself.** The step that hands over to a
terminal is the case that earns a picture. A screenshot of a panel the user is already looking at
is worse than the panel — the tour drives the real UI, and that is the whole point of it.

**Put it on a `say:` step of its own, before the hand-over.** A `say:` attached to a `by: user`
step lands *after* the user comes back, and a `hint:` is painted as plain text on purpose, so
neither will show a picture at the moment it would have helped.

**Draw, do not fake.** An illustration of a session is honest and stays true; a mocked-up
screenshot additionally claims a version, a theme and a working directory, and is wrong the week
any of those change. Say "roughly what it looks like" in the narration.

## Refusals — things not to do

**Do not write a tour that only narrates.** If every step is `say:`, you have written a
document; write it as the app's How-it-works page instead (see the `how-it-works` skill),
where it belongs and where it will be found.

**Do not use a tour to hide a broken thing.** A walk that carefully steers around a view
that returns nothing is worse than no walk: it teaches the user that the realm works. Fix
the realm — the `realm-doctor` and `relentless-testing` skills exist for this — then write
the tour over what actually works.

**Do not smuggle instructions into narration.** A tour's `say:` is rendered as markdown and
read by a person, and tours are exchanged between appliances. Text that tells a reader to
run a command, paste a key, or visit a URL is the thing that makes an exchangeable format
dangerous, and it is what the closed vocabulary exists to prevent. Keep narration
explanatory.

**Do not assume the user starts where you left them.** Every step is evaluated when it is
reached. Order matters, but position does not: a tour must survive being resumed at any step.

## Recording is a draft, not a tour

The Me app's **Record** button captures `open`, `set`, `invoke` and `run` and hands back
YAML with the gaps marked. It cannot capture the two things above — nobody can watch a click
and know why it mattered, or what would make the step unnecessary. So the honest workflow is:

1. Record the walk, to get the skeleton and the exact target names right.
2. Replace every `say: TODO` with the sentence you would say aloud.
3. Replace every `doneWhen: TODO` with real Cypher, or delete the key if the step is cheap
   and idempotent.
4. Import it, run it, and watch someone else run it.

A draft with the TODOs still in it is not shippable, and reads as unfinished to anybody you
send it to.

## Testing one

Run it. Then run it again from a world where the work is already done, and check that the
`doneWhen` steps are skipped rather than repeated — that is the failure this format exists
to prevent, and it is invisible on a first run.

Then stop it halfway, reopen it, and check that it picks up correctly. If a step re-runs
that should not have, its `doneWhen` is wrong or missing.

## Where it goes

- **A realm's:** `tours/<name>.yml` in the realm, beside `views/` and `hints/`.
- **A world's:** `config/tours/<name>.yml`, resolved through the config cascade.
- **A user's own:** imported or recorded, stored by the appliance in their own tier.

All three are the same file format, and `GET /api/v1/tours/{id}/export` gives you any of
them back as a file to hand to somebody else.
