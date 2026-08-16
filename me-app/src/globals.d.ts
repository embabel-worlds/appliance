/**
 * What the renderer can see that no `import` explains.
 *
 * The renderer has no module system: `index.html` loads plain `<script>` tags
 * and each file publishes a global. That is a deliberate constraint (ES modules
 * are refused over `file://`, and `nodeIntegration` is off, so neither `import`
 * nor `require` is available in a page) — but it leaves the type checker with
 * no way to know these names exist. This file tells it, and nothing else.
 *
 * Types only. Nothing here is emitted, loaded, or shipped.
 */

/** The one channel to the main process, TYPED FROM THE BRIDGE ITSELF. */
type MeBridge = typeof import('./preload').api

/** What `src/theme.js` publishes. */
interface MeTheme {
  applyTheme(settings: import('./types').Settings, name: string): Promise<{ ok: boolean; message: string }>
  restoreTheme(settings: import('./types').Settings): Promise<void>
  chosenTheme(settings: import('./types').Settings): string
}

/** What `src/markdown.js` publishes: policy over marked + DOMPurify. */
interface MeMarkdown {
  render(text: string): string
  paint(el: HTMLElement, text: string): void
}

declare global {
  interface Window {
    /**
     * The preload bridge. Deriving it from preload.js rather than restating it
     * is the point: a channel that is not in preload.js does not exist as far
     * as the page is concerned, and now it does not typecheck either.
     */
    me: MeBridge
    meTheme: MeTheme
    markdown: MeMarkdown
    /* The Query Studio's own modules, each an IIFE publishing one object. Their
       shapes are internal to that window and change together with their callers,
       so they are typed as their implementations infer rather than restated. */
    queryAsk: unknown
    queryComposer: unknown
    queryViews: unknown
    querySchemaPanel: unknown
  }

  /* ── Vendored third-party ──────────────────────────────────────────────────
     Loaded as plain scripts from src/vendor/, which `npm run vendor` refreshes.
     Declared with the surface this app actually uses — not the library's full
     API, which we would only get wrong.

     `@embabel/appliance-kit` USED TO BE DECLARED HERE, as three `any`s standing
     in for EmbabelVc, EmbabelStudioKit and EmbabelCodeSurface. It is a real
     dependency now and is imported, so its own .d.ts applies and the unchecked
     boundary is gone. What is left below is third-party code that genuinely
     ships no types we can use. */

  /** CodeMirror 5, vendored. Its own types are not vendored with it. */
  const CodeMirror: any
}

export {}
