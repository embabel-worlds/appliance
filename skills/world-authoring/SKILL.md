---
name: world-authoring
description: Build a capability into the user's WORLD directly or into a REALM — the user picks the target, the authoring is the same. Covers saved Virtual Cypher views (including intelligence views that classify and synthesize in-query), handlers (cron-scheduled jobs and event reactions), and single-page apps. Use when the user wants a saved view, an automation, a scheduled job, a reaction to a signal, a personal app, or asks whether something belongs in their world or a realm.
---

Three kinds of artifact make a world act rather than just answer: a VIEW (a named,
parameterized question), a HANDLER (code that runs on a schedule or in reaction to a signal),
and an APP (a page over the world's data). Each can be written straight into the user's world
— personal, immediate, no repository — or into a realm — versioned, shareable, installed by
anyone. The artifact is the same either way; only the destination differs. The server makes
this literal: a world-tier view and a realm-shipped view are the same ViewSpec, distinguished
only by provenance stamped at load time.

## First: pick the target

Ask, or infer and confirm. The decision has two forks:

1. **Does it need a new type, producer, virtual join, API, or seed data?** Then it is a realm
   — those capabilities only exist as realm files. Load `realm-authoring` (ships with the
   realm-spec realm) and work there.
2. **Does it read and act on what the world already holds?** Then either target works, and
   the default is the world: for one user's own question, automation, or page, a repository
   is ceremony. Reach for a realm when someone else will install it, when it should survive
   as a reviewable artifact, or when it has grown a battery worth shipping.

The lanes are not rivals — the natural life of a capability is to prove out in the world and
be promoted to a realm. Promotion is mechanical: a world view's spec becomes `views/*.yml`
verbatim; an action's body becomes a realm handler; the questions it answered well become the
battery.

If you do not know what the world already holds, run `world-atlas` first.

## Views

World lane: `POST /api/v1/admin/kg/views` saves a user-authored view — name, cypher,
description, params (with defaults), optional `materialized` + `ttl` + `outputLabel`. The
save validates: an unrunnable body is a 400, not a saved landmine. Realm lane: the same
fields as a `views/*.yml` entry.

Rules that hold in both lanes:

- **The description is load-bearing.** It is the vocabulary the ask surface's selector
  matches questions against. Write every phrasing the view should claim; when a question
  ever fails to route, its phrasing goes into the description — that is the upgrade path.
- **The selector binds a view only if it can express every constraint the question states.**
  A question that filters ("which deals are at risk") needs the view to carry that filter as
  a parameter, or the selector will rightly decline it.
- **Ids travel beside display columns** (`customerId` beside `customer`). Dedupe and
  record-linking key on ids; names duplicate, and figures merged by name inflate.
- **Aggregation and dedupe live in the view, tested once** — not in every consumer.

### Intelligence views

The engine exposes LLM reductions as Cypher aggregation functions, so judgment can live
inside the query, beside exact figures — a view stays the deterministic answer surface, and
the model-made cells are labels and prose, never the numbers.

- `synthesize(factText, instruction)` — compose prose per group from facts the query
  assembled. The figures travel as their own columns and reconcile; the prose narrates only
  what the query handed it.
- `classify(text, labels [, rubric])` — one label per group from a closed set. The third
  argument defines what each label MEANS; a judgment like strategic/standard/at_risk is only
  as good as its definitions, and they belong in the call, versioned with the view. An empty
  group is null, never a sentence: silence is unknown.
- **The filterable lane**: judge in a `WITH`, filter on the result —
  `WITH l, classify(digest, 'a,b,c', rubric) AS verdict WHERE $verdict = '' OR verdict = $verdict`.
  The engine computes and stamps the label before the query filters, which is what lets the
  selector bind "which deals are at risk" as a parameter instead of declining.
- **Lens vs view**: a judgment spanning several queries composed in code is a lens; the
  moment one Virtual Cypher statement can walk the whole fact surface, it is a view — and its
  figures join the reconciled surface. Prefer the view; realm-odoo's deal triage made this
  exact journey.

Deeper Virtual Cypher authoring — doors, anchors, fan-out aggregation, caching — is the
realm-spec's `VIRTUAL_CYPHER_GUIDE.md`.

## Handlers

The world lane is the strongest tooling of the three artifact kinds. The process:

1. **`action_brief` first, always**, with the user's request verbatim. The brief is the
   contract: it classifies the trigger (cron, signal, or typed planner action), lists the
   live signal catalogue with each event's exact fields, the `gateway.*` effect surface, the
   KG schema with anchoring rules, and the existing actions so you edit instead of
   duplicating. Writing a handler without the brief is guessing at closed sets.
2. **Settle the trigger with the user before writing.** Which event, what schedule, whether
   it may make changes — these are closed sets; present them as options, never free text.
   A schedule is 6-field cron and the hour goes in the HOUR field: every morning at 7 is
   `0 0 7 * * *`, and `0 7 * * * *` fires at seven minutes past every hour.
3. **Write the body to the brief's contract.** `trigger`/`signal`/`now`/`dryRun` are in
   scope; type-specific event fields are on `signal.properties`; every external or write
   effect is guarded `if (!dryRun)` and logged. Reaching the user is always
   `gateway.notify({message, urgency})` — urgency, never a channel; routing config decides
   where it lands.
4. **`create_action`** type-checks with `tsc` against the gateway surface before saving —
   compiler errors mean nothing saved. Actions are observe-only until the user grants
   `makesChanges: true`; do not grant it on their behalf.
5. **`test_action` before trusting it.** Dry-run executes every read and judgment for real
   and logs what effects it would take. Read the log as an adversary: did it bail early on
   the right conditions, would it have fired on the wrong ones?

What you build here, the user can see and edit: the worlds console's Handler Studio (and the
me-app's) lists every handler, edits it with completion generated from the appliance's own
typed surface behind the same tsc gate, dry-runs it against a real recent signal, and holds
the schedule and the autonomous toggle. Tell the user that is where their handler lives —
a handler they can inspect and change is theirs; one they can't is yours.

Realm lane: `handlers/` YAML binds a cron or signal to a verb; the verbs are `src/api/*.ts`
with Vitest specs (`mockGateway`, hermetic in Node); scheduled KG enrichment is
`decorations/`. `realm-authoring` has the format.

## Apps

World lane: vibe apps — served by the world, personal to the user. Load `vibe-apps`; its
discipline (build the data before the chrome, verify like a user) applies unchanged. Realm
lane: `apps/` shipped with the realm. Either way the app stays a thin client of named views —
query logic belongs in views, tested once, not in page scripts.

Apps also have a promotion path IN REVERSE: `vibe_app_duplicate` copies any app the world
serves — realm-shipped included — into the user's world for customizing. Serving resolves
user → template → realm per file, so a copy under the SAME name replaces what the original
URL serves for this user, and a new name is an independent fork; multi-file apps come with
their stem siblings, references rewritten. Never rebuild a realm app from scratch to change
it — fork it.

Either lane, the app ships its How-it-works page — the in-page section `how-it-works` (the
skill beside this one) specifies. Handover includes it.

## Testing, per lane

A realm ships its battery: `tests/questions.yml` reconciled by a harness, per
`relentless-testing`. A world artifact has no repository to ship a battery in, so
verification happens at authoring time, before you call it done:

- **A view**: invoke it through the runtime path and read the whole envelope. Reconcile its
  figures against the source system directly if you can reach one; say so plainly if you
  cannot. Then ask the natural-language question it exists to answer and confirm it ROUTES
  to the view — a view the selector never picks is a view that does not exist.
- **A handler**: `test_action`, then break one input and watch it bail loudly. A handler
  that has never failed in front of you will fail in front of the user.
- **An app**: open the served page and click everything, or say at handover that you did not.

Judgment cells (classify labels, synthesized prose) are asserted for vocabulary and for the
cases seeded to be unambiguous — never for exact wording. Figures are asserted exactly.

When a world artifact earns promotion to a realm, the questions you verified by hand become
`tests/questions.yml`, and the hand-verification becomes a harness. That is the point of the
promotion: the checking you did once starts running forever.
