---
name: relentless-testing
description: Verify a realm, view, NL surface or app against GROUND TRUTH before any user touches it — reconciling every figure against the source system directly, asserting exact equality, and shipping the harness with the realm. Use before handing over anything a user will query, click, or read a number from; use it again after any change to producers, views, types or apps.
---

The user found, trivially: a natural-language question returning zero on a populated system; a
money total inflated 3x by a fan-out; a card total silently missing an invoice because dedupe
keyed on name and the source holds two invoices with the same name and amount. None required
skill to find. All were findable by the discipline below, which exists so the user NEVER finds
anything this way again.

The posture is adversarial: you are not confirming your work functions, you are trying to catch
it lying. A surface passes when you have failed to catch it.

## L0 — Ground truth, from the source, first

Before testing any Embabel surface, connect DIRECTLY to the system of record — its API, or its
database read-only — and compute the reference answers by hand: the counts, the sums, the
top-N with exact figures. Write them down. Every layer below is judged against these numbers
and nothing else. If you cannot reach the source directly, say so at handover — everything you
verified is then internally consistent at best, not true.

## L1 — Traversals reconcile exactly

Run the realm's queries (`kg_query`) for the same facts. EXACT equality with L0 — a count off
by one is a bug, a sum off by anything is a money bug. "Rows came back" is not a pass.

## L2 — Every view, every parameter

Invoke every view by name through the runtime path (`POST /api/v1/views/{name}/invoke`), with
defaults and with each declared parameter varied. Read the WHOLE envelope — status, outcome,
warnings, rows — and reconcile every figure against L0.

## L3 — The natural-language battery

The generator is a user, and the first user. Ask `kg_ask` the questions a person would type:
the counts ("how many X"), the superlatives in BOTH numbers ("which customers owe most",
"which is our most valuable customer"), the "our/we" phrasings. For each: read the GENERATED
cypher, and reconcile the figures against L0 — nonzero is not a pass; the 3x-inflated sum was
nonzero. An unpinned door in the generated query is a schema defect (declare a default);
a grafted-on subgraph from another domain is an authoring-rules defect. Fix the layer, re-run
the battery.

Run each MONEY question at least three times — generation is stochastic, and a 3x-inflated sum
passed once before failing. The stable fix for a battery question is not a better prompt: it is
a NAMED VIEW whose description claims the question, so the ask pipeline SELECTS it instead of
composing. A battery question still answered by free generation after the realm ships is a
missing view.

## L4 — The same fact, shown twice, must match

Any figure surfaced in more than one place — a card total, an ask answer, the facts fed to a
generated summary — is asserted EQUAL across all of them, and equal to L0. And the rule that
failure taught: **dedupe by IDENTITY, never by name or by (name, amount)** — real systems hold
distinct records with identical names and identical amounts, and a display-side dedupe that
merges them silently loses money. Views must RETURN the ids; apps must key on them.

## L5 — Apps: replay their calls, then use a browser

Assets serve. Every call the app's scripts make is replayed with the script's OWN arguments —
same view names, same args, same gateway methods. Then the part curl cannot do: open the page
in a real browser and click everything — every button, every selector, every expansion — or
state plainly at handover that in-browser behavior is untested. Implying a tested UI that was
never clicked is the lie this skill exists to prevent.

## L6 — Force the failures

Make the empty case happen and check it is LOUD (a warning, a named reason — never a bare
zero). Break a credential and confirm the failure surfaces. A surface that cannot show its
failures will show them to the user instead.

## The battery ships with the realm — and users extend it

The battery is a spec-level artifact, not a script you keep: `tests/questions.yml` per the
realm specification — GENERATED mechanically from the realm's shape (per type the count and
list forms; per numeric field the superlative in both phrasings; per status/stage value the
filtered ask; per date field absolute AND relative windows; plus synonym-heavy free forms),
then extended by users with every question that ever disappointed them. Money and count
questions assert `matchesView`: the NL answer's figure must EQUAL the curated view's — the
realm reconciling against itself, no literals to rot. A vocabulary the battery generation
missed ("win", "receivables", "client") is exactly what a user will type first: enumerate the
schema's stage and status values into questions so no domain word goes unclaimed.

## The harness ships with the realm

Encode L0–L4 as an executable script in the realm (`tests/verify.sh` or equivalent): source
queries, expected figures inline, exact-match assertions, exit nonzero on any drift. One
command re-verifies after every change; a harness that lives in your head re-verifies nothing.

## The discipline under it

- Write the expected numbers BEFORE running the check. A check written after peeking confirms
  anything.
- A test that has never failed proves nothing: break one input once, watch it fail, restore it.
- After ANY change to a producer, view, type or app — the whole ladder again. The cache is part
  of the system: producer TTLs survive `realm_refresh`, so restart before re-measuring.
- At handover, list what was NOT verified with the same prominence as what was. The user must
  never discover the boundary of your testing by falling over it.
