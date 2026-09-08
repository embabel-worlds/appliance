# Living assets — one catalog, many projections. Working notes

**Status: a working note, not a decision record.** Captured from a design
discussion so the findings aren't re-derived later. Nothing here is committed to
except where it says "decided". No code has been written against it.

This note supersedes an earlier draft that asked a narrower question — should
the appliance expose views as SQL tables. The answer to that turned out to be a
special case of a better question, and the narrow draft is folded in below as a
worked example.

## The question

The appliance's doors today are the console, REST, and the two MCP surfaces.
Every proposal to add another one — SQL, Bolt, a spreadsheet, a generated client
— arrives looking like a protocol decision. They are not protocol decisions.
They are the same decision, asked repeatedly, because the world holds things
that other tools would like to read and there is currently no general account of
what those things are.

## The business case

Stated without jargon, because the argument does not need any.

The appliance's distinctive act is answering questions no single system can
answer, because the answer lives across several at once — the code a company
ships, a vulnerability database, a government threat list, and its own knowledge
of which team owns what. Today, getting such an answer means coming to us:
opening the console, or having a developer write code. That is a ceiling, and it
falls in the wrong place. The people who most need these answers are the least
likely to log into another piece of software to get one.

**The change is to stop treating an answer as a place you visit and start
treating it as something delivered in the form the recipient already works in.**
One definition, written once, becomes a spreadsheet that refreshes itself, a
table inside the reporting tools a company already owns, a diagram embedded in a
wiki page, deadlines that appear in someone's calendar, or a library a developer
builds against.

The commercial consequences, in the order they matter:

**Adoption stops requiring behaviour change.** The main barrier to enterprise
software is rarely the software; it is asking people to change where they work.
A compliance lead gets a spreadsheet. A security team sees it on the dashboard
they already watch. Nobody is trained and nothing is migrated. That is often the
difference between a tool one team uses and a tool a company runs on.

**Answers stop going stale.** Most business reporting is assembled once and
quietly rots — the architecture diagram that has been wrong for two years, the
risk spreadsheet built by hand last quarter. These are re-derived on every read,
so they cannot drift, and they can show what *changed* since last week, which is
usually the part people care about and the part manual reporting never delivers.

**Reach grows faster than cost.** Because every destination is a different
presentation of the same definition, the next one is days of work rather than
months — and it immediately works for every existing customer and every question
they have already defined. It also lets us follow customers into whatever tools
they use rather than arguing with them about it.

**Nothing has to be copied anywhere.** The conventional route to a dashboard is
to bulk-copy data into a warehouse first: expensive, slow to stand up, a
governance burden, and stale on arrival. Here the data stays where it lives and
is fetched when asked for. Cheaper, easier to get signed off, always current.

**And the refusals are a selling point.** The design declines to serve an answer
in a form that would mislead — where it cannot be computed honestly in time,
would render as an unreadable mess, or would let someone join two things that
look related and are not. Wrong numbers in a spreadsheet that looks right are
worse than no spreadsheet, and buyers know it.

Who benefits: managers and executives, who get answers in the spreadsheets,
calendars and documents they already live in; analysts, who can point tools they
already own at live cross-system information; developers, who get generated
libraries with the guardrails built in, so they move faster and are stopped from
making the specific mistakes that produce plausible nonsense; security and
operations teams, who get a live picture rather than a stale audit; the people
holding context in their heads, who can contribute it through a shared
spreadsheet instead of a form; and IT buyers, who get an adoption story with no
migration project attached.

In one sentence: this turns the appliance from a destination people must visit
into a supplier of answers that arrive where the work already happens, and makes
each new place we appear cheaper than the last.

## The model

A **living asset** is a named artifact whose content is derived from the world at
read time rather than authored and left to rot.

```
asset:
  name, description          # the description is load-bearing, as it already is for views
  source:      view | action | composition
  shape:       rows | graph | prose | chart | file
  freshness:   live | materialized(ttl) | scheduled
  bounds:      max rows / max nodes
  representations: negotiated per request
```

A saved view is already most of this. It has a name, a description, typed params
with defaults, and a `RETURN` clause that names its columns. What it lacks is any
declaration of **shape** — rows are assumed — and that assumption is the reason
every new door has to guess how to render what it is given.

**Making shape first-class is the change.** Once an asset says what it is, one
catalog entry becomes a SQL table, a diagram, a typed client method and a
calendar feed, because each door reads the declaration instead of inferring one.

## Four projection families

Doors are not a menu to be worked through. They are projections of the catalog,
and they group:

| Family | Examples | Reaches |
|---|---|---|
| **Wire** | Postgres, Bolt, OData, GraphQL | tools with drivers |
| **File** | Parquet, Iceberg, SQLite, xlsx | anything, offline included |
| **Code** | generated TypeScript / Python clients | developers |
| **Document & feed** | Sheets, iCal, RSS, Prometheus, Slack | humans, in tools they already have |

The families are not equally expensive, and cost does not track reach. A
materialized asset published as Parquet or Iceberg is read by Spark, Trino,
DuckDB, Snowflake, Databricks and Athena for the price of a writer and no
protocol at all — more analytical reach than a Postgres wire implementation
buys, for less work. Wire protocols earn their cost only for the *live* class.

**The catalog is the deliverable and each door is thin — but only if the catalog
is real.** Every door needs the same four things: the latency classification, the
identity semantics, the auth mapping, and the refusal behaviour. Build those per
door and there are N implementations of one contract, drifting. That is not a
hypothetical: `SHARED_CLIENT_DESIGN.md` documents exactly this happening between
me-app and the console, where the same endpoints grew different fractions of the
same features and a security policy ended up existing in only one of two places.

## Freshness decides the door

Virtual Cypher can be slow, and slow in ways most clients have no vocabulary for.
Producers fetch from external systems. Intelligence views call `classify` and
`synthesize`, which is an LLM call per group — minutes over a few hundred groups.

Most wire protocols have no "come back later". The client holds a socket and
blocks, and client-side timeouts are typically around 60s and not negotiable by
the server. So the classification is not a nicety, it decides which door an asset
can appear at:

| Class | Condition | Door |
|---|---|---|
| **live** | bounded, no LLM reduction, producers cached | wire protocols; executes on request |
| **materialized** | `materialized: true` + `ttl` + `outputLabel` | files, wire, anything — served from the snapshot with `as_of` |
| **neither** | slow or generative, no materialization policy | agent surfaces only — MCP, REST, chat — where the caller expects latency |

The middle row needs nothing new. Views already take `materialized` + `ttl` +
`outputLabel` (`skills/world-authoring/SKILL.md:36`), and `outputLabel` means the
result lands as stored nodes. To any door that is a plain, fast, repeatable read.
It is also the shape analysts already expect, since a periodically refreshed mart
is the normal way slow things reach a tool. Materialization is not a compromise
here, it is the familiar answer.

The third row is a refusal, and it should be a good one: an asset that is neither
live nor materialized is **absent from the catalog** with an explanation
available, not present and prone to timing out.

Two consequences. Polling clients hit snapshots, so producer load and third-party
rate limits stop being the surface's problem. And any per-row fan-out — a join
that invokes a producer per row of the other side — is the same implicit async
cost wearing a different hat, so it needs a declared maximum and a refusal past
it rather than a silently slow query.

## Identity is the join, and it dies at the RETURN

Types already declare identity. `realms/realm-exposure/types/exposure.yml`:

```yaml
- name: Dependency
  properties:
    purl:
      description: "Package URL — ecosystem, name and exact version in one string…"
      metadata:
        identity: "true"
```

And `virtualJoins` on the same types carry `anchorLabel`, `relationship` and
`producerKeyFields` — a join with a required key, which is what any federated
planner needs in order to push a predicate rather than materialize a side.

**The gap is that identity does not survive projection.** `RETURN d.purl AS
package` yields a column called `package`, and nothing downstream records that it
is a `Dependency` identity. The type system knows; the result set has forgotten.

**Infer it rather than asking authors to restate it.** Where the `RETURN`
projects an identity-flagged property of a bound variable whose label is known,
the output column *is* that node's identity. The inference is syntactic and
reliable, it costs authors nothing, and — the reason that matters — it cannot
drift from the type definition the way a hand-maintained second declaration
would. An explicit `columns:` block is the fallback for computed projections,
where there is no bound variable to infer from.

**The join rule is structural, never nominal.** Two columns relate only when both
are identity projections of the same label — never on matching column names, and
never on type compatibility alone. This is not fastidiousness. `realm-filings`
has ticker-is-not-a-company as a load-bearing rule, a `ticker` column and a
`company` column are one keystroke apart in any tool, and a string-equality join
between them produces a table that looks exactly like a real one. Typed identity
rules that join out by construction rather than by documentation.

Identity columns inherit whatever correctness their view's own guards give them.
A `Dependency` column projected without `WHERE d.purl CONTAINS '@'` is a wrong
key, not merely a wrong row count. That stays a view-level defect; no catalog
mechanism can catch it.

## Two bounds every asset carries

**Cost.** A view carries its own bound because its Cypher decided its blast
radius. This is why assets are the unit of exposure and the raw graph is not:
every tool's opening move is `SELECT *` plus a profiling scan, which against a
raw realm projection is not a scan but an API storm.

**Legibility.** A 10,000-row table is merely long. A 10,000-node diagram is
useless. Graph-shaped assets need a declared node budget and a refusal past it,
in the posture of commit 4a3cbf8 — the failure here is silent ugliness rather
than an error, which makes stating the bound more important, not less.

## The render boundary

Assets whose prose comes from `synthesize` or `classify` are model output, and
some doors render markup.

`CLAUDE.md` is already unambiguous for me-app: everything from a model, or from a
document a model quoted, is untrusted; DOMPurify is the boundary; never build
markup by string concatenation into `innerHTML`. **That policy extends to every
asset door that emits markup, and SVG is a worse vector than markdown** because
it can carry script where markdown cannot. This needs stating explicitly rather
than inheriting by analogy, because the drift precedent above is precisely a
security policy that existed in only one of the two places that needed it.

## Worked example: assets as SQL tables

The narrow question this note grew out of. A `shape: rows` asset is a read-only
SQL table: params become predicates, `RETURN` aliases become columns, inferred
identity columns become foreign keys. Two views anchored on the same node type
are joinable, including across realms.

Levels, in the order they pay:

- **Level 0 — asset as table.** Params bind as predicates. No joins. This alone
  points a BI tool at a world and is most of the value.
- **Level 1 — identity columns as catalog foreign keys**, so tools draw and
  suggest relationships. The FK matters *more* than pushdown, because within a
  realm the join has usually already happened — `realm-exposure`'s types file
  says of its own four-organization chain that *"the join is the product"*. What
  SQL adds is ad-hoc composition across realms by someone who will not write
  Virtual Cypher.
- **Level 2 — lateral pushdown**, invoking the right side per row against its
  producer. Deferred: this is the implicit async fan-out the freshness section
  refuses.

### Implementation: try borrowing the door before building it

**The cheapest version of this door may involve none of our code.** Supabase's
[OpenAPI wrapper](https://supabase.com/docs/guides/database/extensions/wrappers/openapi)
is a generic WebAssembly foreign data wrapper that connects to any REST API with
an OpenAPI 3.0+ spec, supporting path parameters, pagination, `WHERE`/`ORDER
BY`/`LIMIT` pushdown, and automatic table creation via `IMPORT FOREIGN SCHEMA`
from a `spec_url`. The appliance already publishes an OpenAPI spec — the
`embabel-client` skill establishes it as the contract — so pointing that wrapper
at a world makes its assets foreign tables inside somebody else's Postgres.

That inverts the build. Rather than implementing a Postgres-compatible *server*,
the client hosts the server and we supply a spec. It is also `IMPORT FOREIGN
SCHEMA` doing precisely what this note means by catalog-as-deliverable: reading
a remote catalog and materialising the local objects from it. If the generic
wrapper proves insufficient, [Wrappers](https://supabase.com/blog/postgres-foreign-data-wrappers-with-wasm)
allows a purpose-built Wasm FDW distributed from any URL, with no per-platform
compilation.

**Test this before building anything.** An hour pointing the generic wrapper at a
live world's spec either removes a large tranche of proposed work or explains
precisely why it cannot.

**The mechanism is standard; the reach is not.** SQL/MED is ISO/IEC 9075-9:2008,
and Postgres states that `CREATE FOREIGN DATA WRAPPER`
[conforms to it](https://www.postgresql.org/docs/current/sql-createforeigndatawrapper.html) —
so `CREATE SERVER`, `CREATE FOREIGN TABLE` and `IMPORT FOREIGN SCHEMA` are
standard SQL rather than a vendor trick. But the standard's `LIBRARY` and
`LANGUAGE` clauses, the part that would have made *wrappers* portable, are not
implemented; Postgres substitutes its own `HANDLER`/`VALIDATOR` against Postgres
C functions. **What the user writes is standard. What we would write is not**, and
no other database has a meaningful SQL/MED ecosystem — Oracle, SQL Server, MySQL
and MariaDB each federate by their own mechanism.

**And a custom FDW cannot be installed on most managed Postgres.** RDS and Aurora
gate extensions behind an `rds.allowed_extensions` allowlist, and anything
shipping a shared library must also enter `shared_preload_libraries` via a custom
parameter group; `pg_tle` covers trusted-language extensions, not native code.
The FDW route therefore reaches **self-hosted Postgres and Supabase** — the
latter only because Supabase ships the allowlisted `wrappers` extension itself
and third parties supply Wasm modules into it, which is a distribution channel
specific to them.

So the three routes are complementary rather than ranked, and the earlier framing
of the wire surface as merely a "fallback" was wrong:

| Route | Reaches |
|---|---|
| FDW | self-hosted Postgres, Supabase |
| pg-wire service | any BI tool directly, including everyone on managed Postgres |
| OData | Excel and Power BI, with no driver or extension at all |

For an estate whose Postgres is managed — which is most of them — the wire
surface is not a fallback, it is the only one of the first two that works.

**If we do build it: Apache Calcite**, per the repo's convention on solved
problems. JVM, gives SQL parse, catalog, planner and JDBC via Avatica, and its
adapter framework is built for tables that declare which predicates they can
push. Behind it, a read-only Postgres-wire front end; read-only means no
transaction machinery, which makes this materially cheaper than a Bolt surface.
The real cost is `pg_catalog` introspection, which every engine taking this route
has paid.

**Verified limitation — Calcite can enforce a required predicate but cannot
declare one.** [`FilterableTable.scan(DataContext, List<RexNode> filters)`](https://calcite.apache.org/javadocAggregate/org/apache/calcite/schema/FilterableTable.html)
is explicitly permissive: the table removes filters it implements, leaves those
it cannot, and *"any filters remaining will be implemented by the consuming
Calcite operator."* There is no SPI for "reject a query that does not bind this
key". Table functions do express required arguments by construction, but BI tools
emit `SELECT … FROM schema.table WHERE …` and generally cannot call
`TABLE(f(…))` — the mechanism that expresses *required* is the one the primary
audience cannot reach.

### Decided: default every param on an asset meant for a table

This is not a Calcite quirk. The same problem was found in **four unrelated
ecosystems**, each handling mandatory parameters badly by a different mechanism:

| Route | How required params fail |
|---|---|
| Calcite | `scan` is permissive; enforcement only by throwing at runtime |
| OData | function imports exist; Excel's support for them is poor |
| MDX | no equivalent concept at all |
| OpenAPI FDW | `IMPORT FOREIGN SCHEMA` **skips parameterized endpoints entirely** |

Four independent sightings is not a protocol quirk, it is a property of the
tabular client world, and it promotes what began as a preference into a
**constraint on asset authoring: an asset intended for a table door gives every
parameter a default.** A view whose params all carry defaults has no required
predicate anywhere, on any of these routes. Both params in
`realms/realm-exposure/views/exposure.yml` are already defaulted, so existing
views are largely unaffected — the constraint bites only on producer-keyed
assets, which are the ones most likely to be live rather than materialized in any
case.

Two supporting rules:

- Where a key is genuinely required and cannot be defaulted, enforce it by
  throwing, and expose the asset additionally as a table function for clients
  that can call one. The refusal message is then the entire user experience,
  which is the posture of 4a3cbf8.
- Params need classifying regardless — *required key*, *predicate* (`minEpss`),
  *control* (`limit`, ordering). `limit` is not a `WHERE` clause; SQL has its
  own, and surfacing it as a column puts a junk column on every table.

Type mapping: view columns are mostly scalars, which is the good case. Maps and
lists have no SQL type and should land as JSON rather than being flattened into
something that looks structured and is not.

## Where the leverage actually is

Ranked by reach ÷ cost across everything considered.

**1. Graph-shaped assets — build first.** The world is already a graph, so a
subgraph is already a diagram, and graph→diagram is *lossless* where graph→table
is not. `realm-exposure` argues this out of its own mouth: the purl identity is
*"shared across repositories on purpose, so the blast radius of one bad version
is an edge count"* — one package version, five repositories, one node with five
edges. Flattened into rows, that fact is destroyed. Blast radius is a picture.

A derived diagram also differs in kind from a drawn one. It cannot rot, because
it is re-derived. It is parameterized, so it is a family rather than a file. And
the **diff is itself an asset** — the world at T₁ against T₂, rendering drift and
newly-exploited findings, which no drawing tool does.

Smallest valuable version: a view returning paths, declared `shape: graph`,
addressable at a stable URL, negotiating Mermaid/SVG/JSON, with a node budget and
a visible `as_of`. Mermaid is text, so it is cheap to emit and embeds anywhere.

**2. Generated typed clients.** The catalog is enough to generate a client for a
*specific* world — a method per asset, typed params, typed columns. The
`embabel-client` skill already establishes OpenAPI as the contract, but a generic
generated client gives `run_view(name, params)`, a stringly-typed hole. World
catalog codegen gives real names, and two properties that no wire protocol
offers: identity columns generate as branded types, so joining a ticker to a
company is a compile error rather than a convention in this document; and the
freshness class becomes the method signature, so a generative asset returns a run
handle or an async iterator rather than being hidden as SQL must hide it. The
descriptions become docstrings, so the prose that the NL selector matches against
also drives autocomplete.

Codegen is also the honest test of the catalog: **if the catalog cannot generate
a typed client, it is not rich enough to serve SQL or OData honestly either.**

**3. Addressable assets with content negotiation.** One asset, one URL, `Accept`
choosing Mermaid, SVG, PNG, JSON, CSV or xlsx. This deletes a large part of the
door menu — N formats do not need N protocols — and an address is what makes an
asset embeddable in a wiki, a Slack unfurl, or a service's own README, which is
distribution that requires no client at all.

**4. Spreadsheets, in both directions.** Outward: the appliance writes and
refreshes a Sheet, so the recipient's client is a link with no install and no
credential. **OData is the Excel and Power BI route — decided**, and the argument
is in the next section, because it turns on disagreeing with Cube for a reason
worth stating. It also subsumes an earlier recommendation in this note for a
custom Power Query connector: Excel's OData support *is* Power Query underneath
and Power BI has a native OData connector, so a feed reaches both with no
connector to write.

Inward is the part that was missed. `realm-exposure`'s types file says
`WatchedRepo` is *"the one thing a user seeds by hand: everything else in this
realm hangs off it"* — repositories, service names, owning team, internet-facing,
tier. That is a spreadsheet, it has always been a spreadsheet, and the people who
know which service is internet-facing will not log into an appliance to say so.
**A synced Sheet is a better realm-seeding surface than any console form**, which
makes this a realm-authoring feature wearing a client-surface costume.

**5. An OpenAI-compatible chat endpoint.** The widest reach on the list for the
cost, and the correct door for the generative class, since chat clients are the
only ones that expect a slow streaming answer.

**6. Files for materialized assets** — Parquet, Iceberg, or a SQLite snapshot.
One file, offline, a driver in every language.

**7. Time-shaped assets as calendar feeds.** `realms/realm-grid/views/grid.yml`
describes its half-hourly curve as *"the shape of the day you schedule against"*.
That is a calendar. Publishing low-carbon windows as a subscribable `.ics` puts
it where scheduling decisions are actually made, and `.ics` is a text format.

Explicitly not pursued: Gremlin (imperative traversal fits the producer model
poorly), Elasticsearch `_search` compatibility (wrong shape, no payoff), and A2A
(too early). Arrow Flight SQL is a later throughput optimisation of the SQL door,
not an alternative to it. Graph languages and graph doors get their own section
below, because "Bolt or GQL" turns out to be the wrong question.

## Graph doors: Bolt, GQL and SQL/PGQ

An earlier draft of this note weighed a Bolt door against GQL as if they were
alternatives. They are not, and the confusion is worth naming because it is the
same *class* of error as the OData/MDX case above. That one turned on the shape
of the content; this one turns on layer.

**Bolt is a transport. GQL and Cypher are languages.** One can carry the other.
Deciding between them is a category error, and once separated both questions get
easier.

**The language axis.** [GQL](https://www.iso.org/standard/76120.html)
(ISO/IEC 39075:2024) is the first new ISO query language since SQL in 1987, and
it borrowed heavily from Cypher — `MATCH`/`RETURN` construction included — which
means the gap from what the engine speaks today is narrower than a fresh dialect
would be. It is being implemented in the wider market. But **it buys almost no
clients**: there is no GQL driver ecosystem to speak of, and the one wire
protocol effort found in the survey
([GrafeoDB's `gwp`](https://github.com/GrafeoDB/gwp), a pure-Rust gRPC transport)
is early and single-vendor. GQL is therefore a *conformance and portability*
decision about the engine's dialect, not a client-reach decision.

**The transport axis.** Bolt's entire value is its installed client base —
drivers in every major language, the graph browser, the shell, and the
GraphRAG and agent-framework integrations that already know how to speak it.
That value is independent of which language travels inside, and because GQL is
Cypher-derived, a Bolt door built to carry Virtual Cypher today would not need
rebuilding to carry GQL later.

**Decided: GQL is not a door, so it never competes with Bolt.** If both happen,
the order is Bolt first, because that is where the clients are, and GQL after, as
a claim about the language already being sent over it. The cheap version of the
GQL decision is worth stating too: because it derives from Cypher, *tracking*
GQL where it does not conflict is close to free, and only full conformance is
expensive. Write toward GQL; claim conformance when it is earned.

### Decided: SQL/PGQ is how graph patterns reach SQL clients

The third answer, and the one this note's own logic prefers, is neither.
[SQL/PGQ](https://www.iso.org/standard/79473.html) (ISO/IEC 9075-16:2023) puts a
`GRAPH_TABLE` operator in the `FROM` clause: it runs a graph pattern against a
property graph and returns the matches as rows for ordinary SQL to carry on with.
It ships in Oracle 23ai, in
[DuckDB via DuckPGQ](https://duckdb.org/docs/lts/guides/sql_features/graph_queries),
and in Spanner Graph.

If the SQL door is being built anyway, **this is graph pattern matching on a door
that already exists** — the same move as OData subsuming the Power Query
connector above: reuse a door rather than add one, which is the whole point of
treating doors as commodity.

It also lands on this note's own vocabulary. `GRAPH_TABLE` returns *tabular*
results from a *graph* pattern, which is exactly the `shape: graph` versus
`shape: rows` distinction written in the standard's own syntax — and it is honest
about the traversal in a way a flattened view is not.

The caveat, kept rather than buried: **SQL/PGQ fixes the language on the SQL
door, not the client problem.** BI tools do not emit `GRAPH_TABLE`; its audience
is people writing SQL by hand, and DuckDB users. It therefore does not reach the
graph-native and GraphRAG tooling a Bolt door would, and is not a substitute for
one. The two answer different questions, which is the recurring lesson of this
whole section.

Still open: whether to build a Bolt door at all. That remains a bet on client
reach, unchanged by any of the above.

## Prior art

Surveyed after the model above was drafted, which is the wrong order. Almost
every individual mechanism here has been built before, some of it maturely, and
the note is more useful for saying so plainly than for pretending otherwise.

**[Cube](https://docs.cube.dev/docs/introduction) is the closest structural
match.** Metrics, dimensions, joins and access rules are defined once and served
over a Postgres-compatible SQL interface, REST, GraphQL, MDX/DAX and an MCP
server for agents, with pre-aggregations and a two-level cache for speed. That is
"one catalog, many projections" as a shipping product, and the model is validated
commercially by it.

### Decided: OData, not MDX — and Cube is right for Cube

Cube reaches Excel and Power BI over MDX/DAX, which was initially logged here as
a data point against this note's OData recommendation. Reconsidered, it is not.
The two protocols serve different consumption patterns, and the choice follows
from the shape of the content rather than the merits of the wire.

**MDX exists to make pivot tables work.** An XMLA endpoint gives Excel a live
PivotTable with server-side aggregation — the client asks for measures by
dimensions and the server returns the subtotals. OData gives a refreshable flat
table. For a user who wants to *re-slice*, MDX is plainly better, and it is the
right answer for Cube because **Cube's assets are cubes**: measures, dimensions
and aggregation rules, authored precisely so a consumer can re-aggregate them.

**Our assets are not cubes, and are not meant to be.** `ActivelyExploited`
returns service, repo, team, CVE, severity, ransomware flag, deadline and summary
— a ranked list of records with identity, carrying no additive measures and no
dimensional hierarchy. The authoring guidance is explicit that this is deliberate:
*"aggregation and dedupe live in the view, tested once — not in every consumer"*
(`skills/world-authoring/SKILL.md`). That is a direct rejection of the
consumer-re-aggregates model MDX exists to serve. Serving MDX would mean
inventing measures, hierarchies and aggregation semantics over assets that
deliberately have none — the graph-as-tables mistake committed at a higher
altitude, forcing content into a shape it does not have.

**OData fits the shape we do have.** Entity sets with keys and navigation
properties are record lists with identity and typed relationships, which is the
identity model of this note almost exactly: identity columns become entity keys,
inferred foreign keys become navigation properties, and the join surfaces as a
clickable expansion. It is REST plus a metadata document against XMLA's
DISCOVER/EXECUTE metadata model of catalogs, cubes, measure groups, hierarchies,
levels and members — days of work rather than months, which matters when the
strategic position is that doors should be cheap.

Three honest caveats:

- For someone who genuinely wants to slice, OData is the worse experience. The
  answer is that our assets are not sliceable by construction, not that pivoting
  does not matter.
- Parameterized assets are awkward in OData — function imports exist, Excel's
  support for them is poor. This is the *same* problem as the Calcite finding
  above, arriving by a different route, which is evidence it is a catalog-level
  issue rather than a protocol one. Same mitigation: prefer defaulted params.
- If the world ever grows genuinely dimensional assets — a `shape: cube` with
  measures and hierarchies — MDX becomes the right door **for those**. The
  projection model absorbs that without contradiction, because doors are chosen
  per shape rather than globally. That is a point in the model's favour.

**[Palantir's Ontology and OSDK](https://www.palantir.com/docs/foundry/ontology-sdk/overview)
is the closest conceptual match.** Object types, link types and action types,
with generated typed TypeScript, Python and Java clients covering just the
subset of the ontology a given consumer needs. That is the codegen proposal here,
already built, including the "governed interface" framing — which is strong
evidence for ranking codegen highly rather than a reason to drop it. It also
means the structural-identity join rule in this note is *convergent, not novel*:
link types are the same idea.

**[Denodo](https://community.denodo.com/docs/html/accessible/9.1/vdp/administration/restful_architecture/restful_architecture.html)
has published views over uncopied sources via JDBC, ODBC, REST, OData, GraphQL
and SOAP for years**, with base views holding metadata only and data fetched live
unless cached. The plumbing argued for here is a mature product category. This is
the clearest caution in the survey: the differentiation cannot be the plumbing.

**[Steampipe](https://steampipe.io/docs/steampipe_postgres/overview) is live
external APIs as SQL tables, already** — a zero-ETL Postgres foreign data wrapper
over 100+ services and 2000+ tables, translating queries into real-time API calls
with qualifiers pushed into them. That is the producer mechanic, expressed
relationally. If a user simply wants cloud APIs as SQL, it exists and is free.

This is the sharpest competitive item in the survey, because it makes *federation
itself* commodity: "we query your systems in place rather than copying them" is
now a free Postgres extension, not a differentiator. Federation should therefore
stop being a headline. What Steampipe structurally cannot do is the rest of this
note — there is nowhere in the foreign-table model to declare that a purl in one
wrapper is the same entity as a purl in another, no traversal where each hop's key
comes from the previous hop's result, and no judgment inside the query. **It
federates tables; realms federate a graph.**

### Consume rather than compete — the licence permits it

[Turbot's split](https://github.com/turbot/steampipe/issues/488) is AGPLv3 for
the CLI and Postgres FDW, **Apache 2.0 for the plugin SDK and the plugins**. The
hundred-odd API integrations are the Apache-licensed half; the copyleft covers
the engine we would not need.

The architecture cooperates too. The
[plugin SDK](https://github.com/turbot/steampipe-plugin-sdk) states that plugins
work across all their engine types — CLI, Postgres FDW, SQLite extension, export
CLI — because a plugin is a gRPC server, deliberately engine-agnostic, with
in-process and gRPC encapsulation since v5.8.0. So plugins could be hosted and
called directly as realm producers, with neither Postgres nor AGPL code in the
stack: someone else maintains a hundred integrations, and we add the identity,
the traversal and the judgment on top.

Three risks to weigh before committing. The plugin gRPC interface is an SDK
contract for *their* engines rather than a published third-party surface, so it
may change — mitigated by its already being stable across four of their own
engines. The licence boundary deserves an actual legal read rather than an
inference from a licence table, particularly as Turbot names commercialisation as
the reason for the split. And plugins are Go binaries, so a JVM appliance takes
on process supervision and a gRPC client.

**Worth an experiment before it is worth a plan**: stand up one plugin as a gRPC
server and write a producer against it.

**[Structurizr](https://docs.structurizr.com/) is the anti-rot diagram
argument, shipped.** Many views generated from a single model, all updating when
the model changes, explicitly motivated by stale diagrams; Backstage and
Spotify's system model do the catalog-driven version. Useful warning attached:
their hard problem is *view selection* — deciding which subset to draw — not
rendering. This note's node-budget bound is a partial answer to that and probably
not a sufficient one.

**[Datasette](https://datasette.io/) already does one URL, many
representations**, and publishing SQLite as a distribution format. Both were
proposed here as though new.

### What is actually left

Being honest about the above sharpens rather than weakens the case, because it
isolates what is genuinely ours:

- Semantic layers (Cube, dbt) sit over a **warehouse** — the data has already
  been centralised. Denodo and Steampipe federate but add no judgment.
- **Nothing surveyed puts LLM reductions inside the query**, so that a judgment
  — a classification, a synthesis — is part of the asset's definition and travels
  with it to every door, beside exact figures that reconcile.
- **Nothing surveyed combines that with a graph-native identity model** spanning
  several organisations' data, which is what makes the cross-source relating
  cheap rather than a modelling project.
- The **appliance** deployment — owned, self-hosted, small — is a different
  proposition from a platform engagement or a hosted service.
- **The freshness class as a safety property was not found in the survey.**
  Cube's pre-aggregations are a performance mechanism, and query latency for AI
  agents is actively discussed, but withholding an asset from a door that cannot
  honestly serve it appears to be new. It is new *because* judgment-in-query is
  new: a warehouse query is slow in seconds, not minutes, so nobody previously
  needed a refusal class. This is "not found", not "does not exist".

The strategic reading: the doors are commodity and should be treated as such —
copied shamelessly, cheaply, from products that have already proven which ones
matter. What is defensible sits behind them, in what the world can compute.

## Not decided

- **Whether to do any of this**, and in what order. The ranking above is an
  argument, not a plan. Two experiments would resolve more of it than more
  argument will, and both are hours rather than days: point the generic OpenAPI
  FDW at a live world's spec and see how far `IMPORT FOREIGN SCHEMA` gets, and
  stand up one Steampipe plugin as a gRPC server behind a producer. Each either
  removes a tranche of proposed work or explains why it cannot be removed.
- **How far `shape` goes.** Renderers must be a closed set. The moment shapes are
  user-supplied templates this becomes a templating language with its own
  maintenance surface, which is a different product.
- **Where the surface lives** — in the app process beside the existing doors, or
  as its own service. Note that the store's own Bolt port (`infra.yml:26`) is
  deliberately kept off the surfaces block as an implementation detail
  (`embabel_setup/surfaces.py:63`). A client door is a product surface and would
  belong in that block, which is a different decision from the one taken there.
- **Engine independence.** Any door must sit above the graph engine, not beside a
  particular one: `GRAPH_TYPE` is `NEO4J`, `FALKORDB` or `MEMORY`, and a door
  that exists on only one of them is not a door.
- **Auth.** The console and MCP doors have their posture. A wire protocol with
  its own auth handshake needs that mapped onto it, not reinvented.
- **A Cypher-over-Bolt door.** Still unresolved, and it is a bet on client reach:
  it reaches graph-native and GraphRAG tooling that nothing else here does. Two
  observations from this note bear on it. The freshness classification matters
  *more* on a SQL or BI door than a Bolt one, because driver clients can wait and
  dashboards cannot. And per the graph-doors section, GQL is not an alternative
  to it — SQL/PGQ is the answer for SQL clients, and Bolt remains the only route
  to the graph-native ones.
