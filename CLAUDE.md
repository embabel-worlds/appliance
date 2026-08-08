# Working in this repo

## Conventions

**Use a real library for a solved problem.** Markdown, HTML sanitizing, date
math, argument parsing — reach for the established package rather than growing a
regex edifice that mostly works. A hand-rolled parser is fragile in exactly the
cases nobody tested. The bar for hand-rolling is a genuinely tiny, stable format
(the SSE parse in `me-app/src/chat.js` is one) — not "it started small".

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

The renderer has no bundler and no module system: `index.html` loads plain
`<script>` tags, and each file publishes globals. `contextIsolation` is on and
`nodeIntegration` is off — the renderer reaches the main process only through
the narrow `window.me` bridge defined in `src/preload.js`. A channel absent from
preload does not exist as far as the page is concerned.

Third-party browser libraries are **vendored** into `src/vendor/` and loaded as
plain scripts; the packages themselves are devDependencies, and `npm run vendor`
re-copies the built files after an upgrade. This keeps the packaged app's
`files` list honest and avoids shipping `node_modules` for something that is one
file.

### Rendering model output

Assistant text is markdown and is rendered as such — `src/markdown.js`, which is
policy over `marked` (parse) and `DOMPurify` (sanitize). Rules that matter:

- Everything from a model, or from a document a model quoted, is untrusted.
  DOMPurify is the boundary; never build markup by string concatenation into
  `innerHTML`.
- Links go out through `window.me.openExternal`. An Electron renderer that
  navigates away from `index.html` has no way back.
- What the *user* typed is painted as `textContent`, never re-parsed —
  re-interpreting their words rewrites them back at them.
- Rendered markdown carries the `md` class; its styles live in `index.html`.
