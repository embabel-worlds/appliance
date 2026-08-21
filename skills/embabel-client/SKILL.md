---
name: embabel-client
description: Write an app that CALLS an Embabel appliance over REST — run saved views, query the knowledge graph, invoke verbs — using the server's own OpenAPI spec as the contract. Use when the user is building a frontend, script, service or integration that consumes their world's data, or asks how to reach views/queries/tools from code.
---

The appliance is a typed API server, and an app that consumes it should be written the way you
would write against any well-specified API: **from the spec, against the endpoints built for
callers**. Not by scraping the console, not by talking to Neo4j directly, and not by rebuilding
query logic the server already owns.

## 1. The spec is the contract — fetch it first

```
GET /v3/api-docs          # the full OpenAPI 3 document (~186 paths)
GET /swagger-ui/index.html # the same, browsable
```

Fetch it, read the operations you need, and generate or hand-write your client from it. Never
invent an endpoint from memory — the spec is served by the running server, so it is exactly
what this appliance version supports. All API paths sit under the `/api/v1` prefix; auth for a
local appliance is HTTP Basic with the operator account (the setup-created user), and services
can use the bearer token (`EMBABEL_MCP_API_TOKEN`) instead. Credentials live in the caller's
config, never in committed code.

## 2. Saved views are the API your app should want

A saved view is a named, parameterised, server-owned query: it encodes the joins and exclusions
that are easy to get wrong by hand, it is validated at save time, and it can be MATERIALISED
(cached, TTL-refreshed) without the caller changing a line. For an app, a view is what a stored
procedure was to a database client — call it by name, get typed rows.

**The endpoint built for apps:**

```
POST /api/v1/views/{name}/invoke
Body: {"args": {"limit": 5}}
```

Returns a stable, versioned envelope and never exposes the underlying query:

```json
{"executionId":"exec_…","operationId":"my_memories","contractVersion":"1",
 "status":"SUCCEEDED","outcome":"EMPTY","outputType":"rows","data":[],
 "warnings":[],"error":null,"metrics":{"durationMs":114,"externalCalls":0,"cacheHit":true}}
```

Read the envelope, all of it: `status`/`outcome` before `data`; non-empty `warnings` means a
backing source failed or truncated, so few rows means "source unavailable", not "no data";
`metrics.cacheHit` tells you whether the materialised tier answered. `error` is actionable text
— surface it, do not swallow it.

To discover what exists: `GET /api/v1/admin/kg/views` lists every view with its declared
parameters, defaults and cache state. The admin tier also has
`POST /api/v1/admin/kg/views/{name}/run` (full query envelope, for debugging and harnesses) and
`POST /api/v1/admin/kg/views` to SAVE a new view — validated before it persists, so a view that
could never run cannot be saved.

**The design consequence:** when your app needs a query the views don't cover, the right move is
usually to SAVE A VIEW and call it by name — not to embed Cypher in the app. The query logic
lands where it is validated, versioned, parameterised and cacheable, and the app stays a thin
typed client. An app full of embedded query strings is the client-side copy of platform logic
that this surface exists to remove.

## 3. Ad-hoc queries, when a view genuinely doesn't fit

- `POST /api/v1/admin/kg/execute` — run Cypher through the same scoped engine (per-user scope is
  injected; never add a userId filter, never interpolate values — bind params).
- `POST /api/v1/admin/kg/ask` — plain English in, rows out; the server writes the query. Costs a
  model hop; fine for exploratory UI, wrong for a hot path.
- `GET /api/v1/admin/kg/schema` — the live schema, for building query UIs or validating input.
- `POST /api/v1/admin/kg/validate` — preflight a query without running it.

Prefer them in that order behind a feature, and promote anything that stabilises into a view.

## 4. Verbs and the rest of the surface

Realm verbs (wasm/docker handlers) and gateway tools are listed at `GET /api/v1/world/tools`
and invocable over REST; documents, artifacts, images, notifications, webhooks and cron each
have their own controller under `/api/v1` — the spec's tags map them. If your app needs the
world to DO something on a schedule or in response to an event, that is a realm handler or
webhook on the server, not a poller in your app.

## 5. Failure discipline for a client

- 401 means authenticate, 404 on a view means the NAME is wrong or not saved — list views before
  concluding data is missing.
- 400 on a view carries the actionable argument error (bad name, missing required param, bad
  coercion) — show it to the developer verbatim.
- An `error` mentioning a "virtual label … not reached via a registered relationship" is the
  engine teaching you its shape: virtual types are reached through doors, and the message lists
  them. Fix the view (or report it), don't retry blind.
- Treat `contractVersion` as the envelope's compatibility promise; parse defensively across it.

## What not to do

- Do not connect to Neo4j/Postgres behind the appliance directly. The REST surface injects
  per-user scope, policy and redaction; the datastore has none of that, and a client wired to it
  breaks on the next internal schema change.
- Do not scrape or replay console endpoints marked hidden — they are the console's own plumbing,
  unversioned on purpose. Build on what the OpenAPI spec advertises.
- Do not re-implement argument merging, defaults or coercion client-side; `invoke`/`run` exist
  so the caller exercises the same path the platform validates.
