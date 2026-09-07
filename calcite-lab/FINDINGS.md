# Calcite SQL surface — what the lab found

**A lab, not a product.** Built to answer one question — is a SQL surface over an
appliance's saved views useful as a client — against the live appliance on
`:11043`. Run it with `mvn -q exec:java`.

**Nothing was installed in the appliance.** Calcite is an embeddable library, and
here it ran in the lab's own process against endpoints that already exist:
`/api/v1/admin/kg/views`, `/kg/schema`, `/views/{name}/run` and `/kg/execute`,
over basic auth. That matters for deployment: the door can sit in the client, in
the appliance, or in a **sidecar** holding only a base URL and credentials —
independently versioned, and disabled by not running it.

## It works, and the joins are the payoff

Seven `split-estate-drift` views became tables alongside sixteen stored node
labels. Two- and three-way joins on `policy_number` returned correct rows in
190–290 ms, dominated by the HTTP fetches rather than by Calcite.

The value is not the tables, it is what a client can now ask that nobody wrote a
view for:

```sql
SELECT COUNT(*), SUM("premium"), AVG("premium") FROM "world"."policy-premium"
```

That aggregate exists in no view, cost 32 ms, and needed no authoring round-trip.
Joining a view to a stored node table worked identically. **This is the case for
the door**: not that SQL renders a view, but that it composes views the author
never anticipated composing.

## Gaps found — one retracted, one fixable, one architectural

### 1. Identity does not reach the API at all

The design note says identity dies at the `RETURN`. It dies earlier.
`GET /kg/schema` serves only `name`, `type`, `sparse`, `description` per
property. The `metadata: identity: "true"` declared in realm type files never
arrives. What survives is prose — `Dependency.purl`'s description happens to
contain the word "IDENTITY" because a human wrote it there.

Scraping that prose as a stand-in recovered identity for **5 labels out of 116**,
and for the seven views under test it recovered **nothing**, because
`split-estate-drift` describes its properties without the magic word. Inferring
foreign keys from `RETURN` + identity is correct in principle and impossible in
practice until the flag is served.

### 2. ~~Virtual and persisted labels are indistinguishable~~ — WRONG, retracted

**This was my error, and the field I needed was there all along.** An earlier
pass tested `exhaustive`, found it true for every label, and concluded the
distinction was unavailable. The right field is **`anchor`**, which the schema
API already serves and documents as: *"True when this label may open a MATCH
pattern bare — a real read, or a virtual population implicitly bound by user
tenancy. False means it is reachable only by traversal from a bound anchor."*

That is exactly the extent test, and it discriminates correctly:

| label | anchor | realm |
|---|---|---|
| `Policy` | true | split-estate-drift |
| `Claim` | true | split-estate-drift |
| `WatchedRepo` (stored, unseeded) | true | realm-exposure |
| `Dependency` (producer-backed) | **false** | realm-exposure |
| `Vulnerability` (producer-backed) | **false** | realm-exposure |

**67 of 116 labels report `anchor: false`** and are now refused as tables on the
correct grounds rather than admitted for want of evidence. The rule the design
note proposes — a label with no extent must not become a table — is implementable
today, with no server change.

The API also serves `realm` per label, which gives schema-per-realm naming for
free.

### 3. Types are guessed, not declared

Realm-projected properties report `type: "any"`, so the lab infers column types
from the first non-null value in the data. A door that guesses its own column
types has no contract to offer a client, and the guess changes when the data
does.

## Fixing identity: small, and precedented

The flag is not missing from the model, only from the projection.
`metadata["identity"]` is already carried on the property definitions and already
read elsewhere in the assistant — `RepositoryStore.kt:291` uses it to find a
type's identity property, and `TypeShapeAdapters.kt:33` already surfaces it into
a DTO as `FieldShape(..., identity = metadata["identity"] == "true")`. Nothing in
the framework needs to change; the schema endpoint simply does not pass on what
it holds.

The change follows the path `description` already takes, which is the argument
that it is the right change rather than a convenient one:

1. `KgSchemaProperty` (`KgAskApi.kt`) gains `identity: Boolean = false` —
   additive, so an older client reading a newer appliance loses nothing, the same
   posture `anchor` already documents for itself.
2. `KgAskService` gains `propertyIdentities(userId): Map<String, Set<String>>`
   defaulting to `emptyMap()`, implemented in `TextToCypherKgAskService` exactly
   parallel to `propertyDescriptions` — walk `dynamicTypes`, filter
   `ownProperties` on `metadata["identity"].equals("true", ignoreCase = true)`.
3. Both controllers that build the DTO are wired to it: `KgAskController` around
   line 174 and `KgStudioController` around line 117, which carry the same
   mapping and would otherwise drift apart.

With that, the design note's rule becomes implementable as stated — a column is a
foreign key when it is an identity projection of a label, not when its values
happen to sit inside another column's. The lab's containment fallback found 140
constraints for want of this; a declaration would find a handful.

## One field, and one thing Calcite cannot do

On `/kg/schema`: **`identity: true`** on properties. That is the whole ask — the
storage-class field I originally asked for alongside it already exists as
`anchor`. Everything the design note proposes for the SQL, OData and codegen
doors follows from identity, and none of it follows without it.

Descriptions are the other half, and they are not the appliance's fault:
`REMARKS` is unreachable through Calcite's JDBC driver at all (see below), so
they need the pg-wire front end rather than a server change.

## Joins execute but are not discoverable

`DatabaseMetaData.getImportedKeys` returns **0 foreign keys**. The joins above all
work — but a BI tool reading the catalog would never suggest one, which is the
concern raised before the lab was built, now confirmed. Note that this is a
Calcite-side gap here; whether Postgres foreign tables can carry FK metadata is a
separate question the FDW experiment would answer.

## The most instructive result: SQL made a wrong number easy

Reconciling `policy-loss-ratio` against a hand-written SQL aggregate over
`claim-amounts` disagreed by roughly 2×. The world was right and the SQL was
wrong: the view defines loss as `loss_payment + loss_reserve + expense_payment +
expense_reserve`, and omitting the expense columns produces a plausible,
authoritative-looking, incorrect figure. With the correct definition it matches
`policy-loss-ratio` and `claim-total-loss` to the cent across every policy.

That is an argument **for** the view layer, and for the authoring rule that
aggregation lives in the view and is tested once. An open SQL door hands users
the ability to redefine a business measure by accident. Whatever ships should
lead people to the views rather than around them.

## From a client's side: a model file is enough

The stronger test is not whether our own `main` can query the world, but whether
somebody else's tool can, having been told only a URL. It can.

`WorldSchemaFactory` plus `world-model.json` make the world reachable from any
Calcite JDBC client with **no code at all**. Driving it from sqlline — an
external SQL shell that knows nothing about appliances:

```
sqlline -u "jdbc:calcite:model=world-model.json"
> SELECT p."policy_number", p."premium", c."claims", r."loss_ratio"
    FROM "policy-premium" p
    JOIN "policy-claims"  c ON p."policy_number" = c."policy_number"
    JOIN "policy-loss-ratio" r ON r."policy_number" = p."policy_number"
   ORDER BY r."loss_ratio" DESC;
```

Twelve correct rows, a three-way join across three saved views of a realm, from a
client that was handed a JDBC URL and nothing else. **That is the answer to "is
it useful as a client": yes.** The model file names an environment variable for
the password rather than carrying one, so it is safe to commit.

Column metadata is clean, too — a browsing tool sees real types rather than a
wall of strings:

| column | DATA_TYPE | TYPE_NAME | NULLABLE |
|---|---|---|---|
| `policy_number` | 12 | VARCHAR | 1 |
| `premium` | 8 | DOUBLE | 1 |
| `loss_ratio` | 8 | DOUBLE | 1 |

### But the descriptions do not arrive

`REMARKS` is empty for every table and every column. JDBC has a standard,
universally-read field for exactly this, and the world has excellent prose to put
in it — the authoring guide calls a view's description *load-bearing*, since it
is what the NL selector matches questions against. A client browsing this catalog
sees bare names and no explanation.

That is the **third** instance of one pattern, after identity and the
virtual/stored distinction: *metadata the world already holds, dropped on the way
to the client*. Whether Calcite 1.38 can populate `REMARKS` from a custom schema
needs checking; the appliance side of it is simply that the text exists and is
not being passed on.

## Quality JDBC metadata: Calcite cannot carry it

Tested rather than assumed. The lab declares real metadata to Calcite —
`Statistics.of(rowCount, keys, referentialConstraints, collations)`, with
candidate keys detected from the data and **140 referential constraints**
discovered and attached. What a client then sees:

| table | `getPrimaryKeys` | `getImportedKeys` | `REMARKS` |
|---|---|---|---|
| `policy-premium` | 0 | 0 | empty |
| `policy-claims` | 0 | 0 | empty |
| `claim-amounts` | 0 | 0 | empty |

**Calcite's `Statistic` is planner-only.** It informs join elimination and
costing; it never reaches `DatabaseMetaData`, which Avatica serves from its own
implementation without consulting it. And Calcite 1.38 has no table or column
comment mechanism at all — `REMARKS` is unreachable by construction, not merely
unpopulated.

**So Calcite-as-JDBC-driver cannot be the client-facing surface if metadata
matters.** The sidecar must speak **Postgres wire and synthesize `pg_catalog`
itself** — `pg_class`, `pg_attribute`, `pg_constraint`, and `pg_description` for
comments — where we author every row and keys, foreign keys and descriptions all
land. Calcite stays behind it as the query engine, which is what it is good at.
This is the strongest argument the lab produced for the wire surface over the
embedded-driver shortcut.

### And 140 is the number that condemns inferred foreign keys

The design note forbids inferring relationships from anything but declared
identity. Containment-based discovery over 23 tables produced **140**
constraints where a declaration would produce a handful — columns are
accidentally subsets of one another constantly. Implemented here only to prove
the wiring; it is a measurement of why the rule exists, not a fallback worth
shipping.

## Sidecar: no capability loss, on two conditions

Everything the door needs already exists on the REST API — view invocation with
parameters (which is predicate pushdown), `/kg/execute` for stored-node tables,
and per-row invocation for lateral joins. A sidecar differs from an in-server
door in latency, not capability. Two conditions make that true rather than
nearly-true:

1. **It must forward the client's credentials, not hold its own.** A sidecar
   holding one admin credential collapses per-user authorization to a single
   identity. Since the appliance uses basic auth and every SQL client supplies a
   user and password, the mapping is one-to-one and authorization is preserved
   exactly.
2. **The schema API must serve the three missing fields.** This is the one real
   asymmetry: identity, storage class and descriptions exist inside the server,
   and an in-server door could read them directly. A sidecar can never see more
   than the API exposes.

Given both, the sidecar is strictly better — no appliance change, versioned
independently, and turned off by not running it.

## Smaller notes

- Hyphenated view names (`policy-claims`) force double-quoting in every
  statement. Either normalise names at the door or accept the wart.
- Eagerly probing 114 views to build a catalog would fire live producers and LLM
  reductions — the catalog build is itself the API storm the note warns about.
  The lab takes an explicit allow-list instead, which is the freshness
  classification showing up as an implementation detail on day one.
- Calcite and Jackson skew: a `jackson-databind` newer than Calcite's
  `jackson-core` fails at runtime, not compile time. Pin all three together.
