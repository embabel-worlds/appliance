---
name: realm-doctor
description: Diagnose an Embabel realm that is not behaving — installed but answering nothing, verbs missing or stale, status degraded, queries returning empty, a schedule that never fires. Use when a realm install "succeeded" but the realm does not work, or any time realm behaviour disagrees with what its files say.
---

A realm's worst failures are the quiet ones: it installs, `status` says `active`, and it does
nothing. Diagnose by comparing three things that must agree — **the files on disk**, **the
surface the world reports**, and **what actually happens when you call it**. Every defect below
is one of those three disagreeing, and each was found the hard way on a live appliance.

Work symptom-first. Run the checks for the symptom you have; stop at the first verdict.

## First, always: the ground truth pass

1. `realm_status` — note `status`, `verbs`, `types`, `producers`, and READ `problems` fully.
   `problems` is the server telling you the answer; do not skim past it to go spelunking.
2. Compare `verbs` against what `wasm/handlers.ts` (or `handlers/`) actually exports. A mismatch
   IS the finding — name it before theorizing about causes.
3. If you have host access, the container logs are the build's own voice:
   `docker logs embabel-worlds | grep -E "RealmBundleBuilder|<realm-name>"`.

## Symptom: installed and active, but `verbs: []` (or a handler is missing)

- **`problems` names a wasm build failure** — the reason is the compiler's own words ("TypeScript
  enum is not supported in strip-only mode", a six-field cron complaint). Fix the source, then
  `realm_refresh`. The previous good bundle keeps serving until the build goes green.
- **`problems` is empty and the image is old** — appliances before the read-only-mount fix could
  not build a mounted realm's handlers AND could not record that they failed: install reported
  green with no verbs. The logs still say so
  (`wasm build failed (could not atomically publish …)`). Verdict: upgrade the image, or build on
  the host (`tooling/wasm-realm/build-handlers-wasm.mjs`) and `realm_refresh`.
- **Both `handlers.js` and `handlers.ts` exist** — the builder refuses to choose ("ship one
  source file"). Delete one.
- **The handler declares `host: docker`** — wasm source is deliberately left alone; that realm's
  handlers dispatch through the docker path or not at all.

## Symptom: I renamed/added a verb and the surface still shows the old one

- The verb set comes from `dist/manifest.json`, not from the source. On current images a rebuild
  replaces a generated manifest; before the fix, every in-place rebuild threw
  `FileAlreadyExistsException` and the verb set froze at first build. Check the manifest's
  `entries` against the source's exports; if they disagree and the manifest has no
  `generator: embabel-wasm-build` marker, it is HAND-AUTHORED and the author must update it —
  that is the single-file contract, not a bug.
- On a read-only mount the manifest lives in the appliance's own storage
  (`$HOME/.embabel/realm-bundles/<dir>-<hash>/` in the container — `/data/...` on the appliance),
  not in the realm's `dist/`. Look there.

## Symptom: removed, but still present

- `realm_remove` does not rebuild the world; the realm stays loaded until the next
  `realm_refresh`. Its own return value even reports the pre-removal state. Refresh, then trust
  `realm_status`.

## Symptom: the gateway namespace is not what I named the realm

- The namespace derives from the realm DIRECTORY name, not `realm.yml`'s `name:` —
  `realm-scout-probe/` becomes `gateway.realmScoutProbe` even when the realm calls itself
  `scout-probe`. Not a defect to fix from your side; name the directory what you want the
  namespace to be.

## Symptom: install_realm_from_path fails or the realm is invisible

- The appliance sees ONLY the mounted realms dir (`EMBABEL_REALMS_DIR` → `/realms`, read-only).
  A checkout elsewhere on disk does not exist as far as the server is concerned.
- Symlinks are refused anywhere in a realm — the directory itself, `dist/`, any file. "…is a
  symbolic link, or sits behind one" means exactly that; replace the link with real files.

## Symptom: queries return nothing though the data exists

- Read the error, it is a teacher: virtual labels are reached through DOORS
  (`(:Author)-[:WROTE]->(Book)`), and a bare `MATCH (b:Book)` matches nothing by design. The
  rejection lists the doors.
- An identity property mismatch returns empty silently — a join keyed on `email` when the data
  carries `primaryEmail` (or vice versa). Match the property the write path actually populated.
- `warnings` non-empty in the result envelope means a backing source failed or truncated: few
  rows means "source unavailable", not "no data".

## Symptom: a schedule never fires

- `defineSchedule` needs SIX cron fields (`0 0 8 * * *`); the build refuses fewer, and a
  schedule naming no exported handler is refused at build time. Both land in `problems` — read
  them. A verb with `onType` or required input fields cannot be scheduled: the schedule is
  ignored (recorded in `problems`), the verb stays callable.

## Symptom: a producer "succeeds" and its edge returns nothing

The engine's worst failure shape: the fetch ran, warnings are empty, and the traversal answers
zero. Diagnose from the FILE log, not the console — the console shows only the operator channel;
`/app/logs/assistant.log` inside the container carries the firehose, including the two lines
that settle most producer mysteries:

    [cache] producer 'x': 3 key(s) — 0 memo, 0 shared, 3 miss
    producer 'x' → tool 'op' args={...}      then:      producer 'x' returned 0 record(s)

- **"returned 0 record(s)" against a source that demonstrably has the data** — look at the
  logged `args`. Join keys are STRINGS throughout the engine, and an integer-typed source
  parameter can match nothing on `["9"]` and say nothing about it (Odoo's ORM domains do
  exactly this). Declare `keyType: int` on the producer. Prove the theory first with one direct
  call to the source using string keys, then int keys.
- **A sentinel non-key in the batch** — some sources spell "no relation" as `false`, not null,
  and a falsy key inside an integer IN-list is a 500 (or worse, silence). Filter them in the
  producer's domain and say so in a comment.
- **Stale rows after editing a producer** — the per-key TTL cache survives `realm_refresh`;
  restart the container when re-testing a producer change, or wait out the TTL.
- **"virtual label not reached via a registered relationship" listing a strange self-door**
  (`(:X)-[:R]->(X)`) — the joins are declared on the wrong side. A type declares the joins that
  REACH it: `virtualJoins` live on the TARGET type, naming their `anchorLabel`.

## Symptom: a key in secrets.env unlocks nothing

`data/secrets.env` is read at world ACTIVATION, and activation fires on login-like paths — a box
driven only by REST or MCP may never activate, so the key sits unread and the gaps endpoint
keeps reporting the API inert after a restart. Look for "Activating world resources" in the
log; absent, the env-var route (the variable named by `unlockedBy`, set on the server process)
unlocks without depending on activation.

## Symptom: learn_* finds nothing

- `learn_sources` empty means nothing is CONNECTED, not nothing exists — `learn_connect` first.
- The target app must expose a reachable JDBC endpoint. Dev apps often default to in-memory
  (petclinic's default is H2): start the real database service or profile first, and verify with
  a direct connection before blaming the learner.

## The rule under all of it

Never report a realm fixed on `status: active` alone. The proof is behaviour: call a verb, run
the demo query, watch the schedule's next fire land in the surface. A realm that installs clean
and answers nothing is still broken — and now you know where to look.

## Voice

Output follows `../VOICE.md` (the appliance's `skills/VOICE.md`) — its own register, not LLM English. The
non-negotiables while this skill runs: verdict first, then evidence; numbers, not adjectives;
no preamble, no postamble, no emoji, no narrating intentions — report what happened, not what
you are doing. A connected world's persona sets register on top of these rules, never instead.
