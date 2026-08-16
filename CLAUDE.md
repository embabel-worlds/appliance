# Working in this repo

## Conventions

**Use a real library for a solved problem.** Markdown, HTML sanitizing, date
math, argument parsing — reach for the established package rather than growing a
regex edifice that mostly works. A hand-rolled parser is fragile in exactly the
cases nobody tested. The bar for hand-rolling is a genuinely tiny, stable format
(the SSE parse in `me-app/src/chat.ts` is one) — not "it started small".

**Block comments, not walls of `//`.** File headers and multi-line explanations
use `/* ... */`. Single-line `//` is for a single line. This applies to JS; in
Python use a module docstring.

**Comments say why, not what.** The code already says what. Match the density
and voice of the file you are editing — this codebase explains its decisions and
its refusals, and that is deliberate.

**Kotlin: never concatenate string literals.** Use triple-quoted strings with
`.trimIndent()`. Annotation arguments are the one exception, since they must be
compile-time constants.

**Don't commit unless asked.** Make the change; leave the committing to the user.

## me-app (the Electron sensor app)

**TypeScript, built by esbuild, checked by tsc.** `npm start` builds and runs;
`npm run typecheck` is a separate gate that nothing in the run path invokes, so
a type error never stops you launching the app and the build stays in
milliseconds. `npm run watch` rebuilds on save.

The renderer is bundled **per window** — one IIFE bundle each for
`index.html`, `query-studio.html`, `handler-studio.html` and `logs.html`. That
shape is not a preference: pages load via `loadFile`, so their origin is
`file://`, and Chromium refuses ES module scripts from there; `nodeIntegration`
is off and `sandbox` on, so `require` is out too. A classic script is what is
left, and bundling is how the sources behind it get real imports.

`contextIsolation` is on — the renderer reaches the main process only through
the narrow `window.me` bridge defined in `src/preload.ts`. A channel absent from
preload does not exist as far as the page is concerned, and no longer typechecks
either: `window.me` is typed as `typeof import('./preload').api`, derived from
the bridge rather than restated beside it.

`strictNullChecks` is off, deliberately and temporarily — see the note in
`tsconfig.json`. Turning it on is its own change.

**Third-party** browser libraries (CodeMirror, marked, DOMPurify) are
**vendored** into `src/vendor/` and loaded as plain `<script>` tags ahead of the
bundles, publishing the globals that `src/globals.d.ts` declares. The packages
themselves are devDependencies and `npm run vendor` re-copies the built files
after an upgrade. This keeps the packaged app's `files` list honest and avoids
shipping `node_modules` for something that is one file.

**`@embabel/appliance-kit` is not vendored — it is imported.** It is a real
dependency (`github:johnsonr/appliance-kit#main`), and esbuild bundles it into
each window's bundle like any other import, so the client, the virtual-Cypher
semantics, the editor behaviour and the gateway-surface reader all arrive with
their own `.d.ts`. It used to be vendored as IIFE globals declared `any` in
`globals.d.ts`; converting found real bugs those `any`s were hiding — a wrong
callback annotation in `query-views.ts`, and a `SchemaLabel` in `wire.ts` that
had drifted from the server's guarded contract. The same package backs the
Worlds console, which is the point. Its CSS comes in the same way: `src/kit.css`
imports it and esbuild emits `dist/kit.css`, which every page links.

### Rendering model output

Assistant text is markdown and is rendered as such — `src/markdown.ts`, which is
policy over `marked` (parse) and `DOMPurify` (sanitize). Rules that matter:

- Everything from a model, or from a document a model quoted, is untrusted.
  DOMPurify is the boundary; never build markup by string concatenation into
  `innerHTML`.
- Links go out through `window.me.openExternal`. An Electron renderer that
  navigates away from `index.html` has no way back.
- What the *user* typed is painted as `textContent`, never re-parsed —
  re-interpreting their words rewrites them back at them.
- Rendered markdown carries the `md` class; its styles live in `index.html`.
