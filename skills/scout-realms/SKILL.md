---
name: scout-realms
description: Find where a realm would add disproportionate value to a user's estate — by RELATING data the world already holds, by adding INTELLIGENCE the source system lacks (LLM-smart views that classify, summarize, judge), or by adding CAPABILITY (verbs, schedules, apps) — and turn the best candidates into Embabel realms. Use when the user asks what realms they could build, whether something could be a realm, what their appliance could learn, what would make a good demo, or wants their estate surveyed.
---

Find where a realm would add value a `psql` prompt cannot, then interview the user about which
of it is worth having, and install what they approve. Value comes down three lanes, and every
candidate should be ranked on the best lane open to it:

1. **Relate** — a join onto something the world already holds. The biggest wow when the anchor
   side is rich; worthless when it is empty.
2. **Intelligence** — LLM smarts INSIDE the surface, where the source system has none: a view
   that classifies rows as it reads them (visit notes into emergency/routine/follow-up), a
   three-valued verdict over a corpus, a summary steered by voice and length, a handler that
   enriches records on a schedule. The engine's view reductions exist for exactly this — the
   flagship document views (`document_themes`, `does_the_corpus_say`, `position_on`) are LLM
   reductions in view clothing, and a realm can ship its own.
3. **Capability** — verbs, scheduled sweeps, NL query over the graph, an app on top. What the
   realm DOES rather than what it connects.

A candidate with no join can still score high on lanes 2 and 3 — say which lane carries it.

The interview follows the `grilling` skill's shape, and for its reason: **facts are your job,
decisions are theirs.** Never ask the user something you could find out yourself.

## Lane 1, the biggest wow when it lands: relating

A realm that mirrors one database's entities answers questions its owner could already answer with
SQL. It is a second copy of a schema. Nobody's eyebrows move.

A realm earns its keep when it **hangs off an anchor the world already has**, so that one question
crosses from data the user knows to data they have never been able to reach in the same breath:

```
MATCH (p:Person) WHERE toLower(p.name) CONTAINS 'jasper'
MATCH (p)-[:HAS_HUBSPOT_OWNER]->(o)-[:OWNS_CONTACT]->(c:HubSpotContact)
WHERE c.notes_last_contacted < '2026-05-01' RETURN c.email
```

That example is a ME appliance's shape — `Person` is the user's own contacts, `HubSpotContact` a
CRM nobody had joined to them. On a Worlds appliance the same move uses whatever anchors ITS
realms declare; step 2 discovers them before any of this applies. The mechanism either way is a
**virtual join** in a type file — `anchorLabel`, `relationship`, `keyField`,
`recordKeyField`, `producer` — and it is the single highest-value thing you are hunting for.

This is not a theory. Across the realms that already exist, `Person` and `Organization` are the
most-used anchor labels after each realm's own internals: github, hubspot, google, research and
diffbot all hang their data off the people the world already knows.

**So rank candidates by their bridge, not by their size.** A 400-table ERP that shares no
identifier with anything is worth less than a 3-column CSV keyed on something the world already
holds.

## 1. Consent, before you read anything

Ask which directories to scan, and scan only those. Offer what you can see as candidates — the
current repo, its parent, a workspace directory the user names — and say plainly that you will
read file names, config and specs, and will not read source beyond what identifies a system.

A user who says "everywhere" still gets a bounded answer: propose two or three specific roots and
have them confirm. Scanning a home directory uninvited is how a helpful survey becomes a search.

When the scope is already obvious — they asked about THE REPO YOU ARE IN — take it and go. Do
not announce that consent was granted or that you are about to survey; the rule is for you, and
reading it aloud is noise.

## 1b. Let them point you at what they care about

Consent bounds *where* you look; focus bounds *what counts as interesting*. Ask both, in the same
breath, and let them answer either as a system ("the CRM and the billing DB") or as an entity type
("I care about customers and invoices, ignore infrastructure").

A focus is a filter on the ranking, never on the scan: still survey the approved paths in full,
still report a Tier 1 bridge you were not asked about — a strong join outside the stated focus is
exactly the thing they did not know to ask for. Surface it in one line under "also found", and let
them widen if they want.

If they have no focus, say what you would focus on given the spine counts and let them correct it.
Guessing and being corrected is cheaper for them than composing a brief from nothing.

## 2. Ask the world what it can anchor on — never assume

Anchors are a property of THIS world, not of the platform. Discover them: `query_guide` for the
live schema, `realm_status`/`available_capabilities` for installed realms and the anchor labels
their types declare. Count what you find. Do not import an anchor model from another product.

**A Me appliance** (the personal assistant) has two canonical spines — `Person` keyed on email
address, `Organization` keyed on domain, each resolved through indexed key-nodes (`EmailAddress`,
`Domain`). If the schema shows those labels populated, the bridge table below applies in full.

**A Worlds appliance** (the developer platform) knows nothing about email and should not be made
to. Its anchors are whatever its installed realms declare — a `GitHubRepository`, a `SitePoint`,
a domain type from a learned schema. If no email-shaped anchor exists in the schema, do not
mention email at all: not as a bridge, not as a column to add, not in the demo sentence. Rank
against the anchors that exist, and when few do, say the value lives in capability instead
(see the tiers below).

Whatever the world, never hardcode an identity property. Where a Person spine does exist, its
identity spans `primaryEmail`, `email` and `emails` (different write paths populate different
ones) — match the way the existing realms do rather than inventing a property check.

An empty anchor side is a finding, not an embarrassment: report it, and rank a realm that
populates an anchor above any realm that would join to one.

## 3. Survey — hunt bridges, dispatch, do not interrogate

Send subagents over the approved paths in parallel. For every system found, the question is not
"what entities does it have" but **"what does it carry that the world already knows?"**

These are the bridge identifiers worth grepping schemas, specs and fixtures for — each row
applies ONLY when the world actually has the anchor it names (step 2). A bridge onto an
anchor this world does not hold is not a bridge, and proposing it reads as reciting a
different product's data model:

| Bridge | Anchors onto | Where it hides |
|---|---|---|
| Email address | `Person` spine | user/contact/customer tables, CRM exports, `*_email` columns |
| Email address, domain part | `Organization` spine — the org spine extracts the domain, so ONE email column is often two bridges | the same columns; also `website`, `homepage`, vendor records |
| Company domain or website | `Organization` spine | account/vendor/supplier tables, invoices |
| Company registration no., ABN, ticker | `Organization`, once a spine realm maps it to a domain | registries, finance and procurement data |
| Person name alone | `Person` (WEAK — ambiguous) | anything; only worth it with a second key to disambiguate |
| Repo, org, handle | `Person`, via github's learned-handle path | CI config, `CODEOWNERS`, package metadata |
| Address, lat/long, postcode | a realm's own spatial anchor, not a built-in spine | property, logistics, council, delivery tables |
| Date range | any timeline already in the world | events, transactions, meetings |

**Freemail does not key an organization.** The org spine excludes it deliberately: a customer table
whose domains are all gmail.com and hotmail.com carries a strong `Person` bridge and no `Organization`
bridge at all. Check which you actually have before promising a company-level join.

Report per system: the bridge it carries, the anchor it would hit, and roughly how many rows carry
that bridge non-null. **A column that exists but is 95% empty is not a bridge** — measure it, never
assume it. A registry table with an `abn` column populated on 0.5% of rows offers a name and
nothing else, and a plan built on that identifier fails only after someone has built it.

When the bridge falls back to a **name**, check for duplicate spellings of one entity before
promising a join: a donations register carrying both "Macquarie Bank" and "Macquarie Group Limited"
will match neither cleanly, and name-matching without a second key is the weakest bridge there is.
Say what resolution it would need rather than quietly hoping.

Also collect, as before:

- **Realms that already exist.** Directories with a `realm.yml`, or repos named `realm-*`. Do not
  propose authoring what someone already wrote.
- **APIs with a spec.** `openapi*.y*ml`, `swagger*.json`, `*.graphql` — note title, operation
  count, auth. A public reference API keyed on a company number is a strong spine.
- **Databases.** `docker-compose*.yml`, `*.tf`, `.env*`, `application*.yml` — the service, the
  schema, how it is reached.
- **The application in front of the data.** For every database found, record what OWNS it: a
  Spring/Django/Rails app, its controllers, whether it exposes REST/GraphQL (or only server-side
  pages), whether an OpenAPI spec exists or could be generated (springdoc is one dependency away
  for a Spring app). The database is rarely the only door, and it is often the wrong one.

  **Ask what is RUNNING, not only what is configured.** `docker ps` and listening ports find
  databases no compose sweep will: a container started by hand, one whose compose file lives
  outside the approved paths, a port-forward to something remote. A live database on a port is
  far stronger evidence than a compose file, because you can count its rows instead of guessing.
  Connect and measure — tables, row counts, and the fill rate of the bridge column — before you
  rank it. Declared demo credentials in a compose file are fine to use; say that you are using
  them.

Two false positives will otherwise dominate the database list, so filter both:

**Infrastructure, not domain.** The appliance ships its own Postgres and Neo4j, and every checkout
of it has them in `docker-compose*.yml`. They hold the world's own state and are not candidates.
Neither is a test fixture or a database whose only tables are migrations. Drop them silently; a
survey that proposes learning the appliance's own store reads as one that did not look.

**The same system, counted many times.** Worktrees, clones and branch checkouts of one repo
produce one candidate, not nine. Group by git origin — `git -C <dir> remote get-url origin` —
normalising before you compare, since `…/me` and `…/me.git` are one repo, as are its ssh and https
spellings. A checkout with no origin is its own candidate rather than a match for anything.

## 4. Rank in tiers — a reasoning tool, never words you say

The tiers order your thinking and this file's rules. They are not customer language: the reply
says "nothing here connects to what your world holds" or "this would join straight onto your
contacts", never "Tier 3" or "lane 2". If the verdict needs this taxonomy to be understood, it
is not yet written.

- **Tier 1 — it relates.** Carries a populated bridge onto an anchor the world already has. One
  question spans both. This is what you are looking for and it goes at the top even if the source
  is small.
- **Tier 2 — a spine.** Public or reference data pinned by a literal the caller knows: a company
  register, a postcode, planning controls at a point. Not wow alone; wow the moment a Tier 1 realm
  supplies the key. Say what it would need to light up.
- **Tier 3 — an island, as data.** Entities sharing no identifier with anything the world holds
  answer what SQL already answered. Say so plainly. But an island can still be worth building on
  the other two lanes: **intelligence** — a view that classifies or judges rows with an LLM as it
  reads them, semantic search over text columns, an enrichment sweep — and **capability** — verbs,
  schedules, NL query, an app. On a developer world this is usually the honest pitch — name the
  concrete views, classifications and verbs the realm would add, and rank it on those, never on a
  join it does not have. "This view answers a question the schema cannot" is a real pitch;
  "imagine if it joined to something" is not.

**The wow test, for choosing between Tier 1 candidates.** Disproportionate impact comes from
small, cheap data that crosses a boundary the user believes is uncrossable — not from volume:

- It returns **few rows**, and they are specific enough to check. "These four" beats "these 4,000".
- The user could not have got it from either side alone. If one SQL query or one API call answers
  it, the join added nothing.
- The cost is asymmetric: a three-column mapping table that connects two large estates is worth
  more than either estate. Look hardest for the small thing sitting between two big things.
- It survives the obvious follow-up. A demo that answers one question and dead-ends is a trick;
  one where "and which of those…" keeps working is a realm.

Be honest when the estate is all Tier 3. "Nothing here relates to your world yet — here is what a
realm could DO for each, and here is what would have to exist before anything could join" is a
better answer than five islands dressed as bridges.

## 5. Ask what actually needs asking — and size the asking to the decision

**One candidate, one plan** — the common case: "can I make a realm from this?" — is a
conversation, not an interview. Give the verdict and the plan in prose, fold your
recommendations in as decisions you have provisionally made, and end with the one or two
questions that would genuinely change the plan, asked in plain sentences. "I'd learn it
read-only at schema depth and wire any write through the app — say the word, or tell me the
question you want it to answer if mine is wrong" covers what four numbered questions were
doing.

**A whole estate with real forks** is where the interview earns its structure. Map the
decisions as a tree; the frontier is every decision whose prerequisites are settled; ask the
whole frontier in one round and wait. Format each as a short bold question followed by your
recommendation and why — plain text, no emoji, no arrow glyphs; the recommendation is a
sentence, not a labelled field.

The decisions that matter, in roughly this order (fold the settled ones into the plan; ask
only the live ones):

0. **Focus, if they have not already given one** — which systems or entity types they actually
   care about. Ask it alongside the tiers rather than before them, so they are choosing against
   real candidates instead of in the abstract.
1. **Which bridges are worth building** — present the tiers with the evidence and the row counts.
2. **The demo question.** For each candidate, get the user to say the sentence they want to be
   able to ask. For a Tier 1 it crosses both sources; for an intelligence-lane candidate it is the
   question the LLM answers that the schema cannot ("which of last month's visits were really
   emergencies?"). This is the highest-value thing you will
   get from them, it is the thing a schema cannot tell you, and it decides the join direction and
   which producer you need. Offer your best guess so they can correct rather than compose.
3. **Whether the bridge resolves.** Ask how the two sides actually match when it is not obvious —
   people with several email addresses, companies that trade under another name. github's realm
   needs three strategies for this (`existingBridge`, `learnedHandle`, `canonicalEmail`) because
   one was never enough.
4. **Which door — the architecture question, asked explicitly.** A system with a middle tier
   offers two doors, and they are different products:

   - **Through the application** (its REST/GraphQL API, learned from an OpenAPI spec): every
     read and WRITE goes through the app's validation, business rules and authorization. The
     realm's verbs ("book a visit", "register a pet") are exactly the app's own operations,
     typed by its own spec — the realm-github pattern. Coupled to the API contract, which is
     what contracts are for.
   - **Straight to the database** (`learn_connect`): richer ad-hoc reads, joins the app never
     exposed, and the learn pipeline builds types in one pass. But a WRITE this way bypasses
     everything the middle tier enforces, and the realm couples to schema internals the app
     considers private.

   Recommend by what the realm will DO: **reads and analytics can go to the database; anything
   that writes goes through the application.** A hybrid is often right — learn the schema for
   query, wire verbs to the API. When the app has no API, gaining one (or a thin one for the
   operations the realm needs) is genuine product work worth naming as an option, not a detour
   from the "real" route. Do not default to the database just because `learn_connect` makes it
   the easiest thing to reach.
5. **How deeply to read.** `learn_source` takes `METADATA` (default, schema only),
   `DISTINCT_VALUES` (low-cardinality column values — usually where the meaning is) or
   `SAMPLE_ROWS`. Recommend `DISTINCT_VALUES` for a system they own, say what it will read, and
   never widen without an explicit answer.
6. **Credentials.** Name what is needed and where it goes — a password goes to their encrypted
   wallet, never into YAML or git. Never invent one, and never reuse one found in a scanned `.env`
   without saying that is what you are doing.
7. **Name and scope** per realm, once the above are settled.

Stop when the frontier is empty. Do not install anything until they confirm the plan.

## 6. Propose, then install what they approved

For each realm: the route, the demo question it unlocks, and what it costs.

| Evidence | Route |
|---|---|
| A reachable database | `learn_connect` → `learn_sources` → `learn_source` → review → `learn_promote` |
| A directory with `realm.yml` | `install_realm_from_path` if it is under the mounted realms dir, else `install_realm` for its git repo — see below |
| An OpenAPI spec | The console's `POST /api/v1/world/apis/learn` — say so; it is not on the MCP surface |
| Genuinely new capability | `realm_write` → `realm_validate` → `realm_install`, per `realm_brief` |

`install_realm_from_path` reaches only what the operator mounted — the appliance sees
`${EMBABEL_REALMS_DIR:-./realms}` from the compose file as `/realms`, read-only, and nothing else.
A `realm.yml` you found under `~/dev` is therefore not installable where it sits. Say that plainly
and offer the two honest routes: `install_realm` from its git repo, or have the user symlink the
checkout into their realms directory and restart.

Route by the door decided in question 4, not by reachability — a running Postgres is the
easiest thing to connect and therefore the easiest wrong default. Mining gets types and columns
right and **relationships wrong** — a learned realm arrives as
`auto-<source>` with its joins marked, and the report says which proposed joins actually returned
rows. A join returning nothing is a guess. Review it, add the virtual join onto `Person` or
`Organization` yourself with `realm_write`, confirm it returns rows, and only then `learn_promote`.
Promoting is the record that a person judged it fit.

`realm_status` should be `active`
with `problems: []`, but the real test is running the sentence from question 2 and getting rows
back that span both sides. A realm that installs clean and answers nothing is not done — say so
rather than reporting success.

If the MCP tools are unavailable, stop at the plan and give the exact calls. A plan the user can
execute is a good outcome; a half-installed realm is not.

## What not to do

- **Never manufacture the bridge you were sent to find.** Do not propose adding an identifier
  column to the source system, or seeding its data with values chosen to match the world, so that
  a join demos well. A seeded join reveals exactly what you typed into it. If a missing field is
  genuine product work for a system the user owns, name it as that — a schema change on its own
  merits — never as demo preparation, and never as this skill's recommendation.
- **Verify how the system actually runs before proposing to learn it.** A dev app often defaults
  to an in-memory database (petclinic's default profile is H2); the compose services beside it are
  opt-in. `learn_connect` needs a reachable JDBC endpoint — establish which profile or service
  provides one and whether it is running, as a fact you report, not a decision you delegate.
- **Do not write through the back door.** A verb that mutates a system someone's application
  owns goes through that application. `sql_update` against a live app's database bypasses its
  validation, its business rules and its audit — propose it only for a store nothing else owns,
  and say what is being bypassed if the user insists.
- **One decision per question.** A round question bundling two choices gets half an answer to
  each. Split them.

- Do not rank by size, table count, or how impressive the system sounds. Rank by the bridge.
- Do not scan a path they did not approve, or widen a scan because the first one was thin.
- Do not read a credential out of a scanned file and use it silently.
- Do not propose a realm for something already installed or already sitting ready to install.
- Do not hand-write a realm for a system that can be learned — but do not trust a learned realm's
  relationships without running them.

## Voice

Output follows `../VOICE.md` (the appliance's `skills/VOICE.md`) — its own register, not LLM English. The
non-negotiables while this skill runs: verdict first, then evidence; numbers, not adjectives;
no preamble, no postamble, no emoji, no narrating intentions — report what happened, not what
you are doing. A connected world's persona sets register on top of these rules, never instead.
