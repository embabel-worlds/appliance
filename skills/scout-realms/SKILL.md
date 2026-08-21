---
name: scout-realms
description: Find the things in a user's estate that would RELATE to data their world already holds — the joins that make a demo land — and turn the best ones into Embabel realms. Use when the user asks what realms they could build, what their appliance could learn, what would make a good demo, how to get their data or an API into a world, or wants their estate surveyed for realm candidates.
---

Find what could be **related**, not what could be stored. Then interview the user about which
relationships are worth having, and install the ones they approve.

The interview follows the `grilling` skill's shape, and for its reason: **facts are your job,
decisions are theirs.** Never ask the user something you could find out yourself.

## The thing that makes a realm worth building

A realm that mirrors one database's entities answers questions its owner could already answer with
SQL. It is a second copy of a schema. Nobody's eyebrows move.

A realm earns its keep when it **hangs off an anchor the world already has**, so that one question
crosses from data the user knows to data they have never been able to reach in the same breath:

```
MATCH (p:Person) WHERE toLower(p.name) CONTAINS 'jasper'
MATCH (p)-[:HAS_HUBSPOT_OWNER]->(o)-[:OWNS_CONTACT]->(c:HubSpotContact)
WHERE c.notes_last_contacted < '2026-05-01' RETURN c.email
```

`Person` is the user's own contacts. `HubSpotContact` is a CRM nobody had joined to them. The
mechanism is a **virtual join** in a type file — `anchorLabel`, `relationship`, `keyField`,
`recordKeyField`, `producer` — and it is the single highest-value thing you are hunting for.

This is not a theory. Across the realms that already exist, `Person` and `Organization` are the
most-used anchor labels after each realm's own internals: github, hubspot, google, research and
diffbot all hang their data off the people the world already knows.

**So rank candidates by their bridge, not by their size.** A 400-table ERP that shares no
identifier with anything is worth less than a 3-column CSV keyed on email address.

## 1. Consent, before you read anything

Ask which directories to scan, and scan only those. Offer what you can see as candidates — the
current repo, its parent, a workspace directory the user names — and say plainly that you will
read file names, config and specs, and will not read source beyond what identifies a system.

A user who says "everywhere" still gets a bounded answer: propose two or three specific roots and
have them confirm. Scanning a home directory uninvited is how a helpful survey becomes a search.

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

## 2. Count the anchor side FIRST

Before valuing any candidate, find out what the world can already anchor on. A bridge to `Person`
is only a bridge if there are people there.

The world has exactly **two built-in canonical spines**, and they are the bridge surface:

| Spine | Keyed on | Normalised by | Key-node |
|---|---|---|---|
| `Person` | email address | lowercase, trim; must contain `@` | `EmailAddress`, `(p)-[:HAS_EMAIL]->(e)`, id `email-address:<addr>` |
| `Organization` | domain | extract domain, lowercase, strip trailing dot; must have a dot | `Domain`, `(d)-[:USED_BY_ORG]->(o)`, id `dom:<domain>` |

Count both spines and their key-nodes — the key-nodes are the honest measure, since resolution goes
through them by deterministic id in one indexed hop:

```
MATCH (p:Person) RETURN count(p)
MATCH (e:EmailAddress) RETURN count(e)
MATCH (o:Organization) RETURN count(o)
MATCH (d:Domain) RETURN count(d)
```

**Never hardcode the identity property.** A Person's identity lives across `primaryEmail`, `email`
and `emails`; an Organization's across `domain` and `domains`. Different write paths populate
different ones — the sender pre-pass writes `primaryEmail`, the identity path writes `email` — so a
join keyed on your favourite spelling silently returns nothing. The identity hub exists precisely
so realms never hardcode `email` vs `primaryEmail`; match the way the existing realms do rather
than inventing a property check.

Then `query_guide` for the live schema, and `realm_status` for what is installed, so you never
propose what is already there.

A world with thousands of `EmailAddress` nodes will light up almost anything keyed on email. A
world with none is the opposite: say so plainly, and rank a realm that **populates** a spine above
any realm that joins to it. On an empty world the most impressive-sounding integration returns
zero rows.

## 3. Survey — hunt bridges, dispatch, do not interrogate

Send subagents over the approved paths in parallel. For every system found, the question is not
"what entities does it have" but **"what does it carry that the world already knows?"**

These are the bridge identifiers worth grepping schemas, specs and fixtures for:

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

## 4. Rank in tiers, and say which tier each is in

- **Tier 1 — it relates.** Carries a populated bridge onto an anchor the world already has. One
  question spans both. This is what you are looking for and it goes at the top even if the source
  is small.
- **Tier 2 — a spine.** Public or reference data pinned by a literal the caller knows: a company
  register, a postcode, planning controls at a point. Not wow alone; wow the moment a Tier 1 realm
  supplies the key. Say what it would need to light up.
- **Tier 3 — an island.** Entities that share no identifier with anything the world holds. It
  answers what SQL already answered. Rank it last and say why, rather than dressing it up.

**The wow test, for choosing between Tier 1 candidates.** Disproportionate impact comes from
small, cheap data that crosses a boundary the user believes is uncrossable — not from volume:

- It returns **few rows**, and they are specific enough to check. "These four" beats "these 4,000".
- The user could not have got it from either side alone. If one SQL query or one API call answers
  it, the join added nothing.
- The cost is asymmetric: a three-column mapping table that connects two large estates is worth
  more than either estate. Look hardest for the small thing sitting between two big things.
- It survives the obvious follow-up. A demo that answers one question and dead-ends is a trick;
  one where "and which of those…" keeps working is a realm.

Be honest when the estate is all Tier 3. "Nothing here relates to your world yet, and the highest
-value move is to get your contacts in first" is a better answer than five islands.

## 5. Grill — rounds, with your recommendation on every question

Map the decisions as a tree. The **frontier** is every decision whose prerequisites are settled.
Ask the whole frontier in one round, numbered, each with your recommended answer, then wait.

```
❓ **Q1** - **<question title>**: <question body, options if there are options>

➡️ <your recommended answer, and why>

---

❓ **Q2** - **<question title>**: <question body>

➡️ <your recommended answer, and why>
```

The questions that matter here, in roughly this order:

0. **Focus, if they have not already given one** — which systems or entity types they actually
   care about. Ask it alongside the tiers rather than before them, so they are choosing against
   real candidates instead of in the abstract.
1. **Which bridges are worth building** — present the tiers with the evidence and the row counts.
2. **The demo question.** For each Tier 1 candidate, get the user to say the sentence they want to
   be able to ask — the one that crosses both sources. This is the highest-value thing you will
   get from them, it is the thing a schema cannot tell you, and it decides the join direction and
   which producer you need. Offer your best guess so they can correct rather than compose.
3. **Whether the bridge resolves.** Ask how the two sides actually match when it is not obvious —
   people with several email addresses, companies that trade under another name. github's realm
   needs three strategies for this (`existingBridge`, `learnedHandle`, `canonicalEmail`) because
   one was never enough.
4. **How deeply to read.** `learn_source` takes `METADATA` (default, schema only),
   `DISTINCT_VALUES` (low-cardinality column values — usually where the meaning is) or
   `SAMPLE_ROWS`. Recommend `DISTINCT_VALUES` for a system they own, say what it will read, and
   never widen without an explicit answer.
5. **Credentials.** Name what is needed and where it goes — a password goes to their encrypted
   wallet, never into YAML or git. Never invent one, and never reuse one found in a scanned `.env`
   without saying that is what you are doing.
6. **Name and scope** per realm, once the above are settled.

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

Mining gets types and columns right and **relationships wrong** — a learned realm arrives as
`auto-<source>` with its joins marked, and the report says which proposed joins actually returned
rows. A join returning nothing is a guess. Review it, add the virtual join onto `Person` or
`Organization` yourself with `realm_write`, confirm it returns rows, and only then `learn_promote`.
Promoting is the record that a person judged it fit.

**Verify by asking the demo question, not by checking status.** `realm_status` should be `active`
with `problems: []`, but the real test is running the sentence from question 2 and getting rows
back that span both sides. A realm that installs clean and answers nothing is not done — say so
rather than reporting success.

If the MCP tools are unavailable, stop at the plan and give the exact calls. A plan the user can
execute is a good outcome; a half-installed realm is not.

## What not to do

- Do not rank by size, table count, or how impressive the system sounds. Rank by the bridge.
- Do not scan a path they did not approve, or widen a scan because the first one was thin.
- Do not read a credential out of a scanned file and use it silently.
- Do not propose a realm for something already installed or already sitting ready to install.
- Do not hand-write a realm for a system that can be learned — but do not trust a learned realm's
  relationships without running them.
