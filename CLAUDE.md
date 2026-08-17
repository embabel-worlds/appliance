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

**Nothing is vendored. Everything is imported.** There is no `src/vendor/`, no
`npm run vendor`, and no third-party global in `src/globals.d.ts` — CodeMirror,
marked, DOMPurify and `@embabel/appliance-kit` are all dependencies, imported by
the sources and bundled into each window by esbuild like any other import. Do not
reintroduce a copied library; if a new one is needed, `npm install` it and import
it.

The kit went first and made the case. As IIFE globals it was three `any`s in
`globals.d.ts`, and converting it found real bugs those `any`s had been hiding —
a wrong callback annotation in `query-views.ts`, and a `SchemaLabel` in `wire.ts`
that had drifted from the server's guarded contract. The same package backs the
Worlds console, which is the point. The remaining three followed for the same
three reasons: their own `.d.ts` files apply, `npm update` is the upgrade rather
than a copy script somebody has to remember to run, and the packaged app's
`files` list stops carrying a second copy of code npm already installed.

CSS arrives the same way, because a `<link>` at a `file://` origin needs a real
path on disk and `dist/` is one: `src/kit.css` imports the shared visual language
and esbuild emits `dist/kit.css`, which every page links; `src/editor.css` does
the same for CodeMirror's stylesheet, linked by the two studio pages that have an
editor.

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
