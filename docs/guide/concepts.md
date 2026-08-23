# What the words mean

Six terms do most of the work in this product. Asked "what is a world?", an
appliance seeded with the rest of this guide could describe how to *author* one
and never say what one *is* — so this page exists to be the answer.

The wording follows [worlds.embabel.com](https://worlds.embabel.com) deliberately:
the site and the product describing the same thing differently is worse than
either being wrong.

## World

**A world is a governed, living knowledge graph of your business, derived from
the systems you already run and owned by you.**

It is the thing your AI acts in. Enterprise AI stalls on integration rather than
on model quality, and no model ships knowing your entities, your systems or your
permissions — a bag of tools only leaves it improvising every join. A world is
the missing piece: typed, governed, queryable, and yours. It runs on your own
machine, and one appliance can hold several.

Concretely a world is a directory of declarative configuration — types, views,
handlers, apps, the realms it installs — plus the graph those things populate
and query.

## Realm

**A realm is how your world learns about a system you already run.** A folder of
declarative YAML in git. Drop one in and the runtime wires up what it declares:
types, actions, goals, APIs, MCP servers, scheduled work, saved queries, whole
dashboards. Nothing to compile, and nothing you cannot read before you trust it.

The word to hold onto is **join**. A realm that brings in data nothing else can
connect to is a second database you now maintain. A realm sharing an identifier
with what your world already knows — an email address, a company name, a
repository — makes every existing thing more useful the moment it lands.

## Virtual Cypher

**One `MATCH` that spans persisted and live data uniformly.**

A normal knowledge-graph query reads what is stored in Neo4j. Virtual Cypher lets
one query *also* reach data that is not in the graph — a HubSpot contact, a
GitHub issue, a semantically related email thread — by fetching it on demand the
moment the query reaches for it, splicing it in for the life of that one query,
and rolling it back afterwards.

The query author writes ordinary Cypher and does not need to know which labels
are persisted and which are fetched live. That is the whole point: no mirroring
project, no sync to fall behind.

## View

**A saved query, referenced by name as if it were a label.** The questions worth
asking twice become views; the ones worth asking continuously become handlers
that tell you when the answer changes.

A view composes: because its rows are nodes of a real or virtual type, you can
`MATCH` a view and keep traversing from it.

## Handler

**Work the world does without being asked** — a scheduled job, or a reaction to a
signal. Cron-driven or event-driven, declared in the world or shipped by a realm.

## Appliance

**The thing you installed.** A knowledge graph, an agent runtime, the realms
installed into it, and the surfaces you reach it through — the console, the
assistant, the MCP endpoint your coding agent connects to. One appliance, one
machine, your data on it.
