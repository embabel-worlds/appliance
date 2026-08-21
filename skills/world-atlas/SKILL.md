---
name: world-atlas
description: Interrogate an Embabel world systematically — what realms are installed, what data and anchors exist and in what quantity, what views answer what questions with what parameters, what verbs and namespaces can be called — and produce a compact atlas of it. Use when the user asks what their world knows or can do, before prospecting realms, building an app, or writing queries, or whenever behaviour suggests the world's shape is not what you assumed.
---

Before building on a world — a realm, an app, a query — know what it actually holds. Guessing
its shape is how queries silently return nothing and apps ship empty. One systematic pass, in
this order (each step makes the next cheaper), ending in a written atlas you and the user can
reuse.

## The pass

1. **Views first — always.** `view_run` with NO arguments lists every saved view: name, what it
   answers, parameters and defaults, which are materialised. This is the world's own statement
   of what questions it considers worth answering, and it costs one call. Over REST it is
   `GET /api/v1/admin/kg/views`.
2. **Capabilities.** `available_capabilities`: every `gateway.*` namespace and method, data
   collections, connected integrations, installed skills. `describe_namespace` for exact
   signatures of the ones that matter — never guess argument names.
3. **Schema.** `query_guide` for the live graph schema — real labels, properties, relationships,
   virtual joins — plus the authoring rules. Pull it BEFORE writing any Cypher, once per
   session.
4. **Counts, for the labels that matter.** A label existing is not data existing. Count the
   anchor-shaped things (`MATCH (x:Label) RETURN count(x)`) so every later judgement ("this
   would join", "this app has rows") rests on numbers. Remember virtual labels are reached
   through DOORS — a bare `MATCH` on one matches nothing, and the rejection message lists the
   doors: read it, it is the schema teaching you.
5. **Realms.** `realm_status` per installed realm: active or degraded, its verbs, types,
   producers, schedules, and any problems. What is installable but absent shows up in
   `realm_brief` / `suggest_capabilities` — the gap list.
6. **Apps and documents.** `vibe_app_list` for the world's pages; `document_search` behaviour
   matters here: it returns `documents` and `emailThreads` as SEPARATE sets — report which a
   hit came from, never merge them.

## Reading results honestly

- Zero rows is only an answer once the query is sound. A rejection names the bad label or edge;
  an empty result on a sound query is real. Never turn a failed call into "the world has none".
- Non-empty `warnings` in any envelope means a backing source failed or truncated: few rows
  means "source unavailable", not "no data".
- "Unknown type" and "hidden type" are different errors — hidden means it exists and this
  surface will not show it; do not report it as absent.
- A view's cost is not free just because it is a view: LLM-reduction views spend model calls
  proportional to what they read. `cost`/`metrics` in the envelope say what a call spent.

## The atlas

Write the result down — a short document, not a memory: realms (state, one line each), anchors
with counts, views grouped by the question they answer (with parameters), the namespaces worth
calling, apps, and a final line of gaps: what the world plausibly SHOULD know or do that nothing
currently covers. That gap line is what scout-realms, embabel-client and vibe-apps each pick up
from here.

Keep it honest about emptiness: "0 people, 0 organizations, 6 toy realms" is a useful atlas —
it says build the data before building anything on it.
