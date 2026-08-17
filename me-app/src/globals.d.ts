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

  /* NOTHING THIRD-PARTY IS DECLARED HERE ANY MORE, and that is the point.
     CodeMirror, marked and DOMPurify were `<script>` tags from src/vendor/
     publishing globals this file typed as `any` — an unchecked boundary in the
     two files that use them most. @embabel/appliance-kit left first, and the
     conversion found two real bugs those `any`s had been hiding. The rest
     followed: they are dependencies now, imported and bundled, so their own
     .d.ts files apply and there is nothing left for this file to promise. */
}

export {}
