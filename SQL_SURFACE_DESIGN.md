# Views as tables — a SQL client surface, working notes

**Status: a working note, not a decision record.** Captured from a design
discussion so the findings aren't re-derived later. Nothing here is committed to
except where it says "decided". No code has been written against it.

The question behind it: the appliance's doors today are the console, REST, and
the two MCP surfaces. Should there be another one that existing client tooling
already knows how to speak?

Two candidates came up — Cypher over Bolt, and SQL. This note is about SQL,
because the shape it takes here is narrower and more defensible than
"SQL over the graph", and because most of what it needs already exists.
The Bolt question is left open at the end.

## The proposal

**A named view is exposed as a read-only SQL table.** Its `params` become
predicates, its `RETURN` aliases become columns, and the node identities it
projects become foreign keys, so two views anchored on the same node type are
joinable — including across realms.

Not the graph as tables. The *views* as tables. That distinction is the whole
design, and everything below follows from it.

## Why views and not the graph

A view is already a relation. It has a name, a description, typed parameters
with defaults, and a `RETURN` clause that names every column. Nothing has to be
invented to call it a table — the mapping is recognition, not modelling.

The graph is not a relation, and every attempt to make it one has to answer
questions with no good answer: is a label a table, is a relationship type a join
table, what happens when the relationship is a producer call that requires a
key. Those questions get worse exactly where realms are interesting.

The second reason is bounding. Every BI tool's opening move is `SELECT *` plus a
profiling scan. Against a raw graph projection that is not a scan, it is an API
storm — `MATCH (n) RETURN n` over a realm fans out to whatever is behind it.
Against a view it is safe by construction, because the view's Cypher already
decided its own blast radius. **A view carries its bound with it. A table over
the graph does not.**

## What already exists

Most of the metadata this needs is in the realm model today.

**The view is a table function signature.** `realms/realm-exposure/views/exposure.yml`:

```yaml
- name: ActivelyExploited
  description: The shortlist. Vulnerabilities in your watched repositories that
    CISA has confirmed are being exploited in the wild right now…
  params:
    limit:
      type: int
      default: 50
  cypher: |
    MATCH (w:WatchedRepo)-[:HAS_DEPENDENCY]->(d:Dependency)
    WHERE d.purl CONTAINS '@'
    …
    RETURN w.service AS service,
           w.fullName AS repo,
           d.purl AS package,
           v.cveId AS cve,
           …
```

Name, description, typed params, named columns.

**The foreign key is declared.** `realms/realm-exposure/types/exposure.yml` marks
identity properties:

```yaml
- name: Dependency
  properties:
    purl:
      description: "Package URL — ecosystem, name and exact version in one string…"
      metadata:
        identity: "true"
```

**The pushdown contract is declared.** The same file's `virtualJoins` carry
`anchorLabel`, `relationship` and `producerKeyFields` — a join with a required
key, which is precisely what a federated planner needs to know it can push a
predicate rather than materialize a side.

**Materialization exists.** Views take optional `materialized` + `ttl` +
`outputLabel` (`skills/world-authoring/SKILL.md:36`). `outputLabel` means a
materialized view's result lands as stored nodes. This turns out to be the
answer to the hardest problem here — see below.

So the work is not modelling. It is projecting metadata the world already holds
into a SQL catalog, and executing against it.

## The one real gap: identity dies at the RETURN

`RETURN d.purl AS package` yields a column called `package`. Nothing downstream
records that it is a `Dependency` identity. The type system knows; the result set
has forgotten. That single fact is what stands between the current model and
foreign keys.

**Infer it, don't ask authors to restate it.** When the `RETURN` projects an
identity-flagged property of a bound variable whose label is known, the output
column *is* that node's identity. The inference is syntactic and reliable, it
costs view authors nothing, and — the reason that matters — it cannot drift from
the type definition the way a hand-maintained second declaration would.

An explicit `columns:` block is the fallback for computed projections, where
there is no bound variable to infer from.

## The FK rule is structural, never nominal

**Two columns join only when both are identity projections of the same label.**
Never on matching column names, and never on type compatibility alone.

This is not fastidiousness, it is the difference between a useful surface and a
confidently wrong one. `realms/realm-filings` has ticker-is-not-a-company as a
load-bearing rule; a `ticker` column and a `company` column are one keystroke
apart in any BI tool, and a string-equality join between them produces a table
that looks exactly like a real one. Typed identity rules that join out by
construction rather than by documentation.

The same applies to the version guard in `realm-exposure`: a `Dependency` column
projected from a view without `WHERE d.purl CONTAINS '@'` is a wrong key, not
just a wrong row count. That remains a view-level defect — the FK mechanism
cannot catch it — but it is worth stating that identity columns inherit whatever
correctness the view's own guards give them.

## Async: the classification that makes "table" honest

Virtual Cypher can be slow, and it can be slow in ways SQL clients have no
vocabulary for. Producers fetch from external systems. Intelligence views call
`classify` and `synthesize`, which is an LLM call per group — over a few hundred
groups that is minutes.

Postgres wire has no "come back later". The client holds a socket and blocks,
and BI-tool timeouts are client-side policy, typically around 60s, not something
the server can negotiate. A dashboard tile must never invoke an LLM per row —
not because it would time out, though it would, but because a tile that refreshes
on a timer would be doing it every cycle.

The resolution is that **the SQL surface exposes a subset of views, not all of
them**, and the catalog classifies by latency:

| Class | Condition | Exposed as |
|---|---|---|
| **live** | bounded, no LLM reduction, producers cached | a table, executed on query |
| **materialized** | `materialized: true` + `ttl` + `outputLabel` | a table over the snapshot, with `_as_of` |
| **neither** | slow or generative, no materialization policy | **not exposed** — stays on MCP/REST |

The middle row is the one that does the work, and it needs nothing new: a
materialized view's output is already stored nodes, refreshed on the world's
existing handler/cron lane. To SQL that is a plain table — fast, bounded,
repeatable. It is also the shape analysts already expect, since a
periodically-refreshed mart is the normal way slow things reach a BI tool.
Materialization is not a compromise here; it is the familiar answer.

The third row is the refusal, and it should be a good one — a view that is
neither live nor materialized should be absent from the catalog with an
explanation available, not present and prone to timing out.

Two knock-ons:

- **Producer load stops being a worry.** The concern that dashboard polling would
  hammer third-party APIs through the producer cache mostly dissolves, because
  polling hits snapshots. Live-class views still need cache and rate-limit
  posture, but they are by definition the cheap ones.
- **It bounds the join work.** See Level 2 below.

## Joins: which case actually earns the work

Within a realm, the join has usually already happened. `realms/realm-exposure/types/exposure.yml`
says it plainly — WatchedRepo → Dependency → Vulnerability → KevListing, four
organizations' data, one read, *"the join is the product"*. A SQL user joining
two exposure views is doing worse and more expensively what one view already did.

The genuine value is **ad-hoc composition by people who will not write Virtual
Cypher** — an analyst in a BI tool joining `realm-exposure`'s Dependency purl to
`realm-oss-health`'s Scorecard purl. Note that even that join is expressible as a
view; what the SQL surface adds is that nobody had to author it in advance.

That reframes the priority. **The FK catalog matters more than the pushdown**,
because the FK is what makes the tool *offer* the join to someone who did not
know it was available.

- **Level 0 — view as table.** Params bind as predicates. No joins. This alone
  points a BI tool at a world and is most of the value.
- **Level 1 — FKs in the catalog.** Inferred per above, published as real
  constraints so tools draw and suggest relationships. Joins execute in the SQL
  layer over materialized sides.
- **Level 2 — lateral pushdown.** When the right side is parameterized by the
  join's anchor, invoke it per-row against the producer rather than materializing
  both. This is where `producerKeyFields` pays.

**Level 2 is deferred, and async is why.** Per-row producer invocation across a
large left side is an implicit fan-out of exactly the kind this design otherwise
refuses to allow. If it is built, it needs a declared maximum fan-out and a
refusal past it — a query that would make ten thousand calls should say so and
stop, not run.

## Wrinkles worth deciding early

**Params are not uniformly predicates.** Every view here carries
`limit: {type: int, default: 50}`. That is not a `WHERE` clause — SQL has its own
`LIMIT`, and surfacing it as a column would put a junk column on every table.
Params need a classification of their own:

- *required keys* — must be bound; drive pushdown.
- *predicates* — bind to `WHERE` (`minEpss` in `RiskRanked`).
- *control* — `limit`, ordering. Map onto SQL's own constructs, or hide.

**Type mapping.** View columns are mostly scalars, which is the good case. Maps
and lists have no SQL type and should land as JSON rather than being flattened
into something that looks structured and is not. Temporals need a decision per
source rather than a blanket cast.

**Catalog introspection is the real tax.** JDBC drivers and BI tools fire
substantial `pg_catalog` queries on connect, and this is where a Postgres-wire
implementation actually spends its effort. Every engine that has taken this route
has paid the same bill.

## Implementation shape

Per the repo's convention on solved problems: **use Apache Calcite.** It is JVM,
it gives SQL parse, catalog, planner and JDBC (Avatica), and its adapter
framework exists for exactly this shape — tables that declare which predicates
they can push, plus correlate/lateral joins for Level 2. Hand-rolling a SQL
parser and planner here would be the regex-edifice failure mode at scale.

A read-only Postgres-wire front end sits in front of it for the BI tools.
Read-only means no transaction machinery, which makes this materially less work
than a Bolt surface would be.

Optional, and only if a scripted client asks for it: a callable
`views.refresh('X')` returning a run handle, so a tool that *can* orchestrate a
two-step refresh is able to. Not for BI tools, which cannot.

## Not decided

- **Whether to do this at all**, and whether it precedes or follows a Bolt
  surface. They serve different audiences — Bolt reaches graph-native and
  GraphRAG tooling, SQL reaches BI and analysts — and they are not alternatives
  to each other. The observation in favour of SQL-first is that it is cheaper,
  and that the latency classification above is *more* necessary on the SQL door
  than the Bolt door, because driver clients can wait and dashboards cannot.
- **Where the surface lives** — in the app process beside the existing doors, or
  as its own service. Note that the store's own Bolt port (`infra.yml:26`) is
  deliberately kept off the surfaces block as an implementation detail
  (`embabel_setup/surfaces.py:63`); a SQL door would be a product surface and
  would belong in that block, which is a different decision from the one taken
  there.
- **Engine independence.** The surface should sit above the graph engine, not
  beside a particular one — `GRAPH_TYPE` is `NEO4J`, `FALKORDB` or `MEMORY`, and
  a client door that exists on only one of them is not a door.
- **Auth.** The console and MCP doors have their posture; a wire protocol with
  its own auth handshake needs that mapped, not reinvented.
