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

## Three gaps that block the design as written

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

### 2. Virtual and persisted labels are indistinguishable

The note's rule — stored labels are tables, virtual labels are not, because a
virtual label has no extent — cannot be implemented either:

| label | sampleCount | exhaustive |
|---|---|---|
| `Policy` (stored) | 12 | true |
| `Dependency` (producer-backed) | 0 | true |
| `WatchedRepo` (stored, unseeded) | 0 | true |

**No label in the world reports `exhaustive: false`.** A producer-backed label
and an empty stored one are the same row. The lab admitted every zero-count label
as a table because it had no basis to refuse one.

### 3. Types are guessed, not declared

Realm-projected properties report `type: "any"`, so the lab infers column types
from the first non-null value in the data. A door that guesses its own column
types has no contract to offer a client, and the guess changes when the data
does.

## Two fields would unblock all three

On `/kg/schema`: **`identity: true`** on properties, and a **storage class on
labels** distinguishing stored from virtual. Everything the note proposes for the
SQL, OData and codegen doors follows from those two, and none of it follows
without them.

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

## Smaller notes

- Hyphenated view names (`policy-claims`) force double-quoting in every
  statement. Either normalise names at the door or accept the wart.
- Eagerly probing 114 views to build a catalog would fire live producers and LLM
  reductions — the catalog build is itself the API storm the note warns about.
  The lab takes an explicit allow-list instead, which is the freshness
  classification showing up as an implementation detail on day one.
- Calcite and Jackson skew: a `jackson-databind` newer than Calcite's
  `jackson-core` fails at runtime, not compile time. Pin all three together.
