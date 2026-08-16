# Sharing the Me and Worlds front ends — working notes

**Status: a working note, not a decision record.** Captured mid-discussion so the
findings aren't re-derived later. Nothing here is committed to except where it
says "decided". Uncommitted on purpose.

The problem: the appliance has two modes — **Me** (Electron sensor app, reaches
into the user's machine) and **Worlds** (pure appliance, `worlds-console`
inside it, no host access). Their interfaces have drifted. Query Studio is the
obvious shared surface; it is not the only one.

## What is actually there

**me-app** (`appliance/me-app`) — Electron. The renderer has no bundler and no
module system: `index.html` loads plain `<script>` tags and each file publishes
globals. Zero HTTP in the renderer; everything goes over IPC through
`src/preload.ts` (`window.me`) to `src/api.ts` in the **main process**, which
holds `settings.baseUrl` and the credentials. Query Studio is `src/studio.js`
(1248 lines) plus `query.html` (401 lines of hand-written CSS). CodeMirror and
friends are vendored into `src/vendor/`.

**worlds-console** (sibling repo) — React 19 + Vite, built into an nginx image
that proxies `/api` to the server on the **same origin**. No credentials in the
client at all: the browser's session, or the proxy's injected `Authorization`,
does it. `app/src/App.jsx` is 927 lines with a `call()` helper.

Both already hit the same endpoints. The drift is that each reimplements a
different fraction of them:

| | me-app studio | console |
|---|---|---|
| Composer (targets/anchors/modes/`{ai:{…}}`) | full | absent |
| Schema panel + completion | yes | no |
| Strict preflight validate | yes | no |
| NL → Cypher generate | yes | no |
| Saved views: list/run/invocation | yes | read-only slice (App.jsx:628–750) |
| Save/delete view, params, history | yes | no |

Drift the other way: me-app has `src/markdown.js`, a deliberate policy over
`marked` + DOMPurify for untrusted model output. The console has **no markdown
path at all** — no `marked`, no DOMPurify, no `dangerouslySetInnerHTML` — so
assistant text renders as literal source. That is a live defect and a security
policy existing in only one of two places.

## The seam that decides the architecture

Not React-vs-DOM. It is **how each client is allowed to reach the server**:

- Console: same-origin `fetch`, ambient credentials, no secret in JS.
- me-app: `contextIsolation: on`, CSP `default-src 'self'`, arbitrary
  `baseUrl` — it *cannot* fetch the appliance, and must not hold the credential
  if it could.

So shared code must be transport-blind. The good news: **the adapter interface
already exists.** `vcSchema / vcValidate / vcGenerate / vcExecute / vcViews /
vcSaveView / vcDeleteView / vcViewInvocation` in `preload.js` is exactly the
right shape — capability-style named operations, not "here is a URL and a
token". Drop the `settings` first argument by currying it in an adapter factory
and it matches what a `fetch`-backed console implementation would expose.

## Rod's direction

1. A shared `@embabel` client model for server access.
2. Shared components, using a module system.
3. Convert to TypeScript.
4. Align UI vocabulary so both look like one family, with Me / Worlds branding.
   No drift.
5. Precompile Me for users without `tsc`.

## Sharpenings on each

### 1. Client model — not two implementations

Two hand-written implementations is the same drift one layer down. It is really
**one HTTP client, two configurations, and one transport proxy**:

- The HTTP client takes `{ baseUrl, headers() }`. Console passes
  `{ baseUrl: '', headers: () => ({}) }` (relative URLs, ambient credentials).
  me-app's **main process** passes `{ baseUrl: settings.baseUrl, headers:
  basicAuth(settings) }` — main is Node, `fetch` is legal there, and that is
  where the credential already lives. So `src/api.ts` does not get a sibling; it
  *becomes* the shared client with a different config object.
- me-app's renderer gets the same interface as a generated IPC proxy. Every
  method is `(...args) => ipcRenderer.invoke(channel, ...args)`, so `preload.js`
  stops being hand-maintained and becomes derived. A method then cannot exist in
  the console and be silently missing from Me's bridge.

Endpoints get written down once instead of twice (api.ts's ~40 functions, and
the console's inline `'/api/v1/...'` strings).

### 2. Module system — the `file://` problem

> **Settled: option 2 (bundle with esbuild), 2026-08.** me-app is TypeScript,
> bundled one IIFE per window; `npm start` builds and runs. The `file://`
> constraint below is unchanged and is exactly why bundling was the answer — the
> output is a classic script, so the pages load it the way they loaded the
> hand-written globals, while the sources behind it use real imports.

`src/main.ts` uses `loadFile`, so the renderer's origin is `file://`,
and Chromium blocks ESM `import` over `file://`. `<script type="module">` will
not work as-is. Four ways out, all keeping JS if desired:

1. **Custom protocol.** Register `app://` as a privileged standard scheme before
   `app.ready`, `protocol.handle` it, `loadURL('app://me/index.html')`. Native
   ESM, no build step, CSP and `sandbox: true` untouched. Costs ~40 lines in
   main plus a path-traversal guard — it is serving the filesystem by URL.
2. **Bundle (esbuild).** Keep `loadFile`, author with `import`/`export`, ship one
   IIFE per page. Costs the build step and an electron-builder `files` change.
3. **Extend the vendor pattern to our own code.** The shared package is authored
   with modules and published in two builds: ESM for Vite, global-publishing IIFE
   for Me. `npm run vendor` copies the IIFE in, exactly as it already does for
   marked and DOMPurify. Me's renderer changes not one line. Ceiling: Me's own
   code stays global and script-order-dependent, so the shared/local asymmetry
   grows.
4. Share only the client model and leave the renderer alone.

**Note (superseded):** at the time of writing, me-app had a module system
everywhere except the renderer — main-process files used CommonJS `require`,
renderer files none. Both are ES modules now.

### 3. TypeScript — orthogonal to the module question

> **Settled: converted, 2026-08.** All 32 files, `noImplicitAny` on and clean.
> The prediction below held exactly: the JSDoc typedefs had drifted from the
> code they described — `Settings` was missing `theme` and `verbs`, `MountsState`
> was missing `env`, `SensorPlatform` was missing three methods that `verbs.ts`
> calls — because nothing had ever read them. Converting also surfaced a live
> bug: a click handler in `query-views.ts` referenced `status`, which resolved to
> the global `window.status`, a string.

The stated reason me-app was plain JS was in `src/types.js:3`: "no compile step,
nothing to install beyond Electron itself." That reason evaporates the moment a
build exists. Once it does, JSDoc-with-`checkJs` is strictly worse than TS.

**The precompile worry dissolves:** the shared library can be TypeScript while
me-app stays plain JavaScript, because npm ships compiled JS plus `.d.ts`. Me
gets editor-level checking through JSDoc `import()` types with no build step and
no `tsc` anywhere near it. Converting me-app becomes a separate, optional call.

Users never needed `tsc` regardless — they install a `.dmg`; CI compiles.
Contributors get tsc/esbuild as devDependencies via the existing `prestart` hook.

Cost to name: `CLAUDE.md`'s me-app paragraph ("no bundler and no module
system... each file publishes globals") stops being true and must be rewritten in
the same commit. The *security* invariants in it — contextIsolation, the narrow
`window.me` bridge, DOMPurify as the boundary — survive untouched.

### 4. Vocabulary and branding — two problems in one label

**Visual.** Shared components read only CSS custom properties; each host ships a
theme. Me's palette is hard-coded hex in `query.html` (`--ink`, `--signal:
#625fff`, `#05060f`); the console already has `design-system/system/
{variables.css,tokens.default.json}` and a `brand.json`. Promote the console's
token set and make Me a second theme over it — do not invent a third vocabulary.

**Nomenclature.** The same server concept has two names per side: Me says
*Query Studio* and *Views*; the console says *Virtual Cypher* and *Lenses*. The
REST path says `views`. Needs a glossary both repos and the server agree on.

## Package and repo topology

**Decided direction:** own repo, published via npm/GitHub, pulled into both.

Precedent found: `@embabel/runtime-types` already exists at
`assistant/tooling/runtime-types` — TypeScript, dual ESM/CJS via two tsconfigs,
consumed by `realm-github`, `realm-google`, `realm-drugtrials`. But it is
`"private": true` and every consumer takes it as
`"file:../assistant/tooling/runtime-types"`.

**That model cannot work here.** The console's `Dockerfile` builds in a clean
`node:22-alpine` with only `app/` copied in; a `file:../` dep is unresolvable
there, as it is for a CI job packaging the `.dmg`. This one must be genuinely
published.

What own-repo gives up, and it is real: in-assistant, a controller change and its
client update land in the *same commit*, so a breaking REST change cannot merge
without the client following. Mitigation: a scheduled/`workflow_dispatch` job in
the client repo that regenerates against assistant `main` and opens a PR on diff
— drift caught within a day rather than at the commit. Build it on day one.

The contract still originates in assistant: generate from `/v3/api-docs`, commit
the spec snapshot into the client repo, fail CI on regeneration diff. The client
repo owns the code but not the truth.

### Suggested package split

Three packages, one repo — because the consumers are not alike:

- **`@embabel/appliance-client`** — transport interface, HTTP implementation,
  generated types, skew handling. Node *and* browser, **no DOM**. Required by
  me-app's main process, so it must never pull in a component.
- **`@embabel/vc`** — virtual-Cypher semantics: composers, schema/alias model,
  param parsing. Pure, no DOM, no transport. Where `VIRTUAL_CYPHER.md` becomes
  code once.
- **`@embabel/appliance-ui`** — Query Studio and the markdown policy. DOM,
  styled only via CSS custom properties.

Three build outputs, and `runtime-types` already demonstrates the recipe
in-house: ESM (Vite), CJS (me-app main process `require`), IIFE-global (me-app
renderer, if it stays on the vendor pattern).

### Versioning — do NOT put it on EMBABEL_VERSION

`infra.yml:6` says "One EMBABEL_VERSION moves every Embabel image AND both modes
together", and worlds-console's Dockerfile signs up to it. Right for **images** —
they deploy as a set.

The client library is different, because one consumer is not an image. A Me
`.dmg` installed in March talks to an appliance pulled in August, and vice versa
(app auto-updated, appliance not). There is no version at which both are true.

So: **independent semver**, and each consumer pins — the console pins an exact
version at image build, Me pins whatever it shipped with.

### Skew is the actual design problem

The console already handles it ad hoc: `App.jsx:248` renders "This server
predates `GET /api/v1/realms/gaps`". Me has nothing equivalent.

Make it first-class: every call distinguishes *endpoint absent on this server*
(a 404 on a route the client knows exists) from *call failed*, and surfaces it as
a typed outcome. Both UIs then render the same "your appliance predates this"
affordance from one signal. **For maximum sharing this may be the highest-value
thing in the package** — it is the part neither UI can do well alone.

## Guardrails

- **Registry.** GitHub Packages requires auth even for public reads, so every
  consumer needs a PAT in `.npmrc` — the console's Docker build (which has none
  today; `app/.npmrc` is just `fund=false`/`audit=false`), me-app CI, and every
  contributor. Public npm under `@embabel` is materially less friction. If it
  must be private, plan the token plumbing into that Dockerfile up front.
- **Scope creep.** Nothing enters the package until two consumers need it, and
  the package never imports from either UI. Otherwise both UIs end up coupled to
  each other through it.
- **Contract tests** against a real mode in CI — `docker-compose-worlds.yml`
  boots one. Generated types prove the *shapes* agree; only a live call proves
  the *behaviour* does, and it is behaviour (auth, error bodies, 404-vs-405 on
  absent endpoints) that the skew handling depends on.

## What is NOT shared, in either direction

Me only: sensors, `mounts.js`, grant states, `logs.js` container following,
`platform/*`, reveal/open file. Worlds only: commissioning, switchboard,
multi-user auth.

Rejected: collapsing Me into a shell hosting the console build. Me needs native
surfaces with no console analogue, and hosting a general web app means widening
`preload.js` to serve it — which dissolves the guarantee that a channel absent
from preload does not exist. That guarantee is doing real work.

## Open questions

- Registry: public npm under `@embabel`, or GitHub Packages with PAT plumbing?
- Module approach for Me's renderer: `app://` protocol, bundle, or extend the
  vendor pattern?
- How far does the TS conversion go — library only, or me-app too?
- Repo topology confirmed as own-repo? (Leaning yes; assistant tempting for
  lockstep but it is a Kotlin/Maven repo with a different cadence.)
- The console's README still describes it as a one-evening concept with mock
  data, while the git log (image build, nginx proxy, commissioning fixes,
  document upload) says it is a real client. Fix before investing in sharing.
  `KnowledgeLedgerCenter` is the one part where that description is still
  accurate — it is a design mock with no endpoint behind it, and its types now
  say so.

## Both studios are on the console now, 2026-08

> **Settled.** The console has **Query Studio** and **Handler Studio**, both built
> on `@embabel/appliance-kit` — the same client, semantics and editor behaviour
> the Me app runs on. The drift table near the top of this document is now
> historical: composer, schema panel, strict preflight, NL→Cypher, save/delete
> view, params and history are all present on both sides.

What it took, in order, and why the order was forced:

1. **The handlers surface had to be typed first.** `OpenApiKgContractTest` only
   guarded `/api/v1/admin/kg`; `HandlerAdminController` sat at `/admin/handlers`
   answering `ResponseEntity<Map<String, Any?>>` on all nine endpoints, so
   `/v3/api-docs` described every payload as a bare `{"type": "object"}` and a
   generated client got `unknown`. Me's Handler Studio consumed those shapes
   anyway, from a hand-written reading in `me-app/src/api.ts` — the exact drift
   this package exists to end. Fixed in the assistant: wire types in
   `HandlerAdminApi.kt`, `@ApiResponse` 200/401 per operation, and request DTOs
   renamed `Handler*` because `SaveRequest` and `ValidateRequest` were one
   collision away from the `AskRequest` bug.
2. **The contract test now guards both prefixes**, and is renamed
   `OpenApiClientContractTest` (snapshot: `client-surface.json`) because it no
   longer guards only the KG surface. `GUARDED_PREFIXES` is the list; adding to
   it is how a surface becomes generateable. It immediately earned its keep —
   the new `required` invariant caught `signalType` and `autonomous` advertised
   as mandatory on `HandlerSaveRequest`, the same Kotlin-defaults defect listed
   below.
3. **`HandlerAdminWireFormatTest`** pins the bytes, following
   `KgAskControllerWireFormatTest`: the payloads were `mapOf`s emitting explicit
   nulls, and a data class with `@JsonInclude(NON_NULL)` would have silently
   dropped `error` on a clean dry run and `schedule` on an unscheduled handler.
   Both are keys consumers branch on.
4. **The kit gained `HandlersClient`** and, from the refreshed snapshot, three
   KG operations it had been missing: `refine`, `propertyValues` and `runView`.

### Decisions this forced, recorded

- **CodeMirror 5, from npm, in the console.** Not a preference for a
  maintenance-mode editor: `createCypherHint` in `studio-kit` is written against
  CM5's `registerHelper`/`showHint` and is already in production in Me. CM6 here
  would mean a second hint implementation and two front ends completing
  differently on the same keystroke. When Me moves, the console moves with it.
  (Vite pre-bundles the core, the addon and the modes into chunks that share one
  `require_codemirror`, so the CM5 singleton survives — worth re-checking after
  any Vite upgrade, because a second instance fails silently as "completion just
  stopped working".)
- **The console's standalone Views panel is gone**, folded into Query Studio.
  A view is a saved query, and a runner that could show you rows but never the
  query that produced them was the odd one out. Its provenance grouping (by the
  realm that shipped each view) survived the move.
- **Me's vocabulary won.** Tabs are *Query Studio* and *Handler Studio*, not
  *Virtual Cypher* and *Lenses*. One of the two names had to lose; Me's won
  because the studios came from there.
- **Endpoints outside the guarded prefixes stay untyped**, deliberately. Realms,
  keys, documents, folders and chat are read through a thin `call()` in
  `app/src/api.ts` with local interfaces marked as the console's *reading* of
  them. A hand-written type there would be a guess wearing a type's clothes.

### Still open after this

- **`/kg/validate` answers `{ok, violations, message}` — there is no `valid`.**
  `ok` IS the verdict, while the handlers surface next door splits the same idea
  into `ok` (did the check run) and `valid` (did it pass). Two endpoints, two
  meanings for `ok`. Worth reconciling; not reconciled here, because changing
  either is a contract change.
- The gateway surface (`/apps-runtime/interfaces.ts`) is TypeScript source, not
  JSON, so it cannot travel through the kit's transport and Handler Studio
  fetches it directly. Fine, and worth naming rather than discovering later.
- ~~me-app's last mile~~ **done.** me-app imports the kit instead of loading it
  from `src/vendor/embabel-*.js`; those files, `src/vendor/embabel-ui/` and the
  `sync:ui` script are gone, and `globals.d.ts` no longer declares `EmbabelVc`,
  `EmbabelStudioKit` or `EmbabelCodeSurface` as `any`. Removing that unchecked
  boundary immediately found two things it had been hiding: `query-views.ts`
  annotated the `declaredParams` callback as `{name, type, def}` when it returns
  plain strings (the body was already right), and `wire.ts` declared
  `SchemaLabel.properties` and `SchemaProperty.type` OPTIONAL where the guarded
  `KgSchemaLabel` marks both required — so `wire.ts` now re-exports the kit's
  types rather than restating them. What is still genuinely vendored is
  third-party only: CodeMirror, marked, DOMPurify.

- **Still not shared: the client itself, in Me.** `me-app/src/api.ts` remains
  ~40 hand-written `fetch` calls in the main process, and the vendored
  `embabel-appliance-client.js` had never been referenced by anything — it was
  deleted unused. `ApplianceClient` is Node-safe and the credential already lives
  there, so this is the obvious next move; it is a bigger one, because the
  renderer reaches it through `preload.ts` and every call site names a bridge
  method.

## Earlier: the prerequisite

Before any generated client, the OpenAPI had to be worth generating from. Done
on branch `openapi-kg-surface` in the assistant repo.
Findings that motivated it:

- Every KG response was `{"type": "object"}` — all 13 handlers returned
  `ResponseEntity<Any>` with hand-built `mapOf`. Repo-wide: 95 such handlers.
- `POST /kg/ask` and `/generate` advertised the **wrong request body**: three
  classes named `AskRequest` collapse into one schema and DocumentAskController's
  won. 29 duplicate simple-names exist (`Result` ×8, `Ok` ×7, `Entry` ×7).
- Declaring `@ApiResponse(404)` **drops the default 200**, so the most carefully
  documented endpoints had no success shape at all.
- No `securitySchemes` despite the whole API being Basic-auth'd.
- `required` wrong on every request DTO with Kotlin defaults.
- `SpringDocKotlinConfiguration` is excluded in config, but re-enabling it
  produces a **byte-identical** spec — it is inert for schema quality. The bad
  `required` lists come from swagger-core's Jackson introspection.

### Separate finding, not fixed there — flagged for a decision

`KgAskController` resolves identity two different ways. `resolve()` is
principal-first and documents the cross-tenant impersonation hole it closes. But
`/ask`, `/schema`, `/validate`, `/execute` and `/generate` still use an inline
**username-first** copy: an authenticated caller passing `?username=ben` acts as
ben. The other nine handlers use `resolve()`. Preserved as-is in the OpenAPI PR
(behaviour change does not belong there) — needs its own fix.
