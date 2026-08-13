/*
 * The build. Six bundles: the main process, the preload bridge, and one per
 * window.
 *
 * WHY BUNDLES AND NOT LOOSE FILES. The renderer cannot use ES modules: pages are
 * loaded with `loadFile`, so their origin is `file://`, and Chromium refuses
 * module scripts from there ("Cross origin requests are only supported for
 * protocol schemes: chrome, …, http, https"). `require` is out too —
 * `nodeIntegration` is off and `sandbox` is on. An IIFE bundle is a CLASSIC
 * script, so it loads exactly like the hand-written globals it replaces, while
 * the sources behind it get real imports.
 *
 * WHY ESBUILD EMITS AND TSC ONLY CHECKS. Two tools, one job each: esbuild does
 * not typecheck (it strips types and moves on), which keeps the build under a
 * second, and `npm run typecheck` is the gate that has opinions. A build that
 * also typechecked would make every edit wait for the slower of the two.
 *
 * The vendored libraries in src/vendor are NOT bundled. They stay `<script>`
 * tags in the HTML, publishing the globals that src/globals.d.ts declares —
 * they are prebuilt IIFEs already, and bundling them would only re-wrap them.
 */

import { build, context } from 'esbuild'
import { rm } from 'node:fs/promises'

const watch = process.argv.includes('--watch')

/** Shared across every bundle: this app targets one Electron, not the web. */
const common = {
  bundle: true,
  format: 'iife',
  target: 'es2022',
  logLevel: 'info',
  // Sourcemaps always, including in a packaged build. A stack trace from a
  // user's machine that points at bundled column 41827 is not a bug report.
  sourcemap: true,
}

/**
 * The main process and the preload. Node platform, and `electron` stays
 * external — it is provided by the runtime, not something to bundle in.
 *
 * The preload is bundled rather than compiled file-by-file because a SANDBOXED
 * preload cannot `require` a relative path at runtime: whatever it needs has to
 * already be inside it.
 */
const node = [
  { entryPoints: ['src/main.ts'], outfile: 'dist/main.js' },
  { entryPoints: ['src/preload.ts'], outfile: 'dist/preload.js' },
  { entryPoints: ['src/smoke.ts'], outfile: 'dist/smoke.js' },
].map((c) => ({
  ...common,
  ...c,
  platform: 'node',
  // CJS, not IIFE, for the Node side: Electron loads main and preload as
  // CommonJS, and an IIFE bundle cannot contain top-level await.
  format: 'cjs',
  external: ['electron'],
}))

/** One bundle per window, named for the page that loads it. */
const browser = [
  { entryPoints: ['src/renderer.ts'], outfile: 'dist/renderer.js' },
  { entryPoints: ['src/query-studio.ts'], outfile: 'dist/query-studio.js' },
  { entryPoints: ['src/handler-studio.ts'], outfile: 'dist/handler-studio.js' },
  { entryPoints: ['src/logview.ts'], outfile: 'dist/logview.js' },
].map((c) => ({ ...common, ...c, platform: 'browser' }))

const configs = [...node, ...browser]

if (watch) {
  for (const config of configs) {
    const ctx = await context(config)
    await ctx.watch()
  }
  console.log('watching — edit src/*.ts and the window reloads on its next open')
} else {
  await rm('dist', { recursive: true, force: true })
  await Promise.all(configs.map((config) => build(config)))
}
