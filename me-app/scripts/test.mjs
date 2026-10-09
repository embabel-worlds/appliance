/*
 * `npm test` — the main-process unit tests, on Node's own runner.
 *
 * Each `src/*.test.ts` is bundled by esbuild into a temporary directory, the same
 * way the app itself is built, and handed to `node --test`. Only main-process code
 * is tested here: it runs on Node, so it needs no Electron and no browser.
 */

import { build } from 'esbuild'
import { spawnSync } from 'node:child_process'
import { mkdtemp, readdir, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

const tests = (await readdir('src')).filter((f) => f.endsWith('.test.ts'))
const out = await mkdtemp(path.join(os.tmpdir(), 'me-app-test-'))
try {
  await build({
    entryPoints: tests.map((f) => path.join('src', f)),
    outdir: out,
    bundle: true,
    platform: 'node',
    format: 'cjs',
    target: 'node20',
    external: ['electron'],
    logLevel: 'warning',
  })
  const files = tests.map((f) => path.join(out, f.replace(/\.ts$/, '.js')))
  const run = spawnSync(process.execPath, ['--test', ...files], { stdio: 'inherit' })
  process.exitCode = run.status ?? 1
} finally {
  await rm(out, { recursive: true, force: true })
}
