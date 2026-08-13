// Opt-in document indexing — promoting shared folders into the knowledge base.
// Design: embabel/appliance#10.
//
// The mounts make files QUERYABLE (virtual Cypher walks them live; metadata and
// grep, nothing stored). Indexing is the deliberate second step: for folders the
// user ticked, each document is ingested through the appliance's own pipeline
// (conversion, chunking, embedding into the graph), which is what unlocks
// summarization and semantic search over file BODIES — PDFs included. No bytes
// are shipped: the files already exist INSIDE the container under /local, so
// each one is a `POST /api/v1/documents/url` with a file:// URL.
//
// Three properties this module guarantees:
//
//  - RESILIENT, WITH NO CLIENT LEDGER. Every sweep reconciles against the
//    server's own document list (`GET /api/v1/documents`): a file is ingested
//    only when it is absent, or its mtime is newer than the stored ingestedAt —
//    which is also how CHANGED files re-index (same URL replaces, never
//    duplicates). Crash anywhere — app quit, appliance restart, laptop sleep —
//    and the next sweep resumes with at most one file redone. A local ledger
//    would be strictly worse: it can lie about what the server holds.
//
//  - RESOURCE-CONSIDERATE. Conversion is CPU-heavy (docling) and embedding
//    hits the Model Runner's GPU, so ingestion is sequential with a duty
//    cycle: after each file, sleep in proportion to how long it took — heavy
//    PDFs self-throttle harder, observed latency doubling as the load signal.
//    And this app IS the sensor that knows whether the user is at the
//    keyboard: screen locked or idle → full tilt; actively working → ~25%
//    duty. The machine chews through the backlog over lunch, not mid-demo.
//
//  - A BACKGROUND TRICKLE, NOT AN APPLY-SCOPED SPRINT. Apply kicks or resumes
//    the loop; it keeps sweeping (every SWEEP_INTERVAL_MS, sooner with a
//    backlog) while any folder stays ticked, and resumes on app launch, so
//    edits made anytime get picked up without touching the panel. Unticking
//    every folder stops it.
//
// The walk happens on the HOST (the folder is the user's own), mirroring the
// server walk's manners: dotfiles and build/dependency directories skipped, an
// extension allowlist, size caps, honest truncation. Progress is readable at
// any time via state(), which the renderer polls.

import fs from 'node:fs'
import path from 'node:path'
import * as api from './api'
import * as mounts from './mounts'
import { platform } from './platform'
import type { IndexState, Settings } from './types'
/** Extensions the documents pipeline can do something real with. */
const DOC_EXTENSIONS = new Set([
  'pdf', 'md', 'markdown', 'txt', 'docx', 'doc', 'pptx', 'xlsx', 'csv', 'html', 'htm', 'rtf',
])
const DENY_DIRECTORIES = new Set(['node_modules', 'target', 'build'])
const MAX_FILE_BYTES = 30 * 1024 * 1024
const MAX_DEPTH = 12
// Scanning is cheap (a stat per file) — scan wide so reconciliation sees
// everything; cap only the expensive part, ingestion, per sweep.
const MAX_SCAN_PER_MOUNT = 10_000
const MAX_INGESTS_PER_SWEEP = 200
// How long to wait out an appliance that Apply just restarted.
const WAIT_ATTEMPTS = 60
const WAIT_INTERVAL_MS = 3_000
// The change-detection cadence: a quiet system re-reconciles this often. A
// sweep with nothing to do is one list call and a few hundred stats.
const SWEEP_INTERVAL_MS = 15 * 60_000
const BACKLOG_INTERVAL_MS = 60_000
const UNREACHABLE_RETRY_MS = 5 * 60_000
// Mtime comparison slack: host stats are fractional-millisecond, the
// container's mount view is whole-second — see the reconcile comment below.
const MTIME_SLACK_MS = 2_000

const current: IndexState = {
  enabled: false,
  running: false,
  phase: 'idle',
  total: 0,
  done: 0,
  ok: 0,
  failed: 0,
  skipped: 0,
  currentFile: '',
  message: '',
  truncated: false,
  nextSweepAt: null,
}

/** @type {Settings | null} */
let settings: Settings = null
let timer: NodeJS.Timeout | null = null
let sweeping = false

/** @returns {IndexState} */
const state = () => ({ ...current })

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

function schedule(ms: number) {
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => void sweep(), ms)
  current.nextSweepAt = new Date(Date.now() + ms).toISOString()
}

/**
 * Candidate documents under a mount's host folder, with the mtimes the
 * reconcile compares against the server's ingestedAt.
 * @param {string} hostDir
 * @returns {{ files: Array<{relative: string, mtimeMs: number}>, truncated: boolean }}
 */
function candidates(hostDir: string) {
  const files: Array<{relative: string, mtimeMs: number}> = []
  let truncated = false
  /** @param {string} dir @param {string} rel @param {number} depth */
  const walk = (dir: string, rel: string, depth: number) => {
    if (truncated || depth > MAX_DEPTH) return
    let entries
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      if (truncated) return
      if (entry.name.startsWith('.') || DENY_DIRECTORIES.has(entry.name)) continue
      const full = path.join(dir, entry.name)
      const relative = rel ? `${rel}/${entry.name}` : entry.name
      if (entry.isDirectory()) {
        walk(full, relative, depth + 1)
      } else if (entry.isFile()) {
        const ext = entry.name.substring(entry.name.lastIndexOf('.') + 1).toLowerCase()
        if (!DOC_EXTENSIONS.has(ext)) continue
        let stat
        try {
          stat = fs.statSync(full)
        } catch {
          continue
        }
        if (stat.size > MAX_FILE_BYTES) continue
        if (files.length >= MAX_SCAN_PER_MOUNT) {
          truncated = true
          return
        }
        files.push({ relative, mtimeMs: stat.mtimeMs })
      }
    }
  }
  walk(hostDir, '', 0)
  return { files, truncated }
}

/** The container-side file:// URL for a document, path segments encoded. */
const containerUrl = (target: string, relative: string) =>
  'file://' + [...target.split('/'), ...relative.split('/')].map(encodeURIComponent).join('/')

/**
 * What the server already holds, keyed by our stable file:// URLs. Null means
 * the list itself failed — the sweep must NOT proceed as if nothing were
 * ingested, or one flaky call re-ingests every document.
 * @param {Settings} s
 * @returns {Promise<Map<string, number> | null>}
 */
async function ingestedMap(s: Settings) {
  const listed = await api.listDocuments(s)
  if (!listed.ok) return null
  const map = new Map()
  for (const doc of listed.documents) {
    if (typeof doc.uri === 'string' && doc.uri.startsWith('file:///local/')) {
      // The SOURCE's modified date — what the server holds for the file itself.
      // undefined when the server has none, which is a real state: that document
      // was ingested before source dates were captured, so our copy is missing
      // information the file has and is worth re-reading.
      map.set(doc.uri, Date.parse(doc.modifiedAt ?? '') || undefined)
    }
  }
  return map
}

/**
 * The duty cycle. Idle or locked → no sleep (the user isn't competing for the
 * machine). Actively working → sleep three times what the ingest took (~25%
 * duty). Platforms that can't sense presence get an even 50%.
 * @param {number} tookMs
 */
async function pace(tookMs: number) {
  let focus = null
  try {
    focus = await platform.sampleFocus(false)
  } catch {
    /* presence unknown — fall through to the even duty cycle */
  }
  const away = focus != null && (focus.locked === true || focus.active === false)
  if (away) return
  const factor = focus != null ? 3 : 1
  const floor = factor === 3 ? 2_000 : 1_000
  const ceiling = factor === 3 ? 90_000 : 30_000
  await sleep(Math.min(Math.max(tookMs * factor, floor), ceiling))
}

/** One reconcile-and-ingest pass. Reschedules itself while folders stay ticked. */
async function sweep() {
  if (sweeping) return
  sweeping = true
  Object.assign(current, {
    running: true, phase: 'waiting', total: 0, done: 0, ok: 0, failed: 0,
    skipped: 0, currentFile: '', truncated: false,
  })
  try {
    const ticked = mounts.state().mounts.filter((m) => m.index)
    if (ticked.length === 0 || !settings) {
      current.enabled = false
      current.nextSweepAt = null
      current.message = 'indexing off — no folders ticked'
      return
    }

    // Apply may have just restarted the assistant — wait it back up.
    let up = false
    for (let attempt = 0; attempt < WAIT_ATTEMPTS && !up; attempt++) {
      up = (await api.testConnection(settings)).ok
      if (!up) await sleep(WAIT_INTERVAL_MS)
    }
    if (!up) {
      current.message = 'appliance unreachable — retrying later'
      schedule(UNREACHABLE_RETRY_MS)
      return
    }

    current.phase = 'scanning'
    const already = await ingestedMap(settings)
    if (already === null) {
      current.message = 'could not list existing documents — retrying later'
      schedule(UNREACHABLE_RETRY_MS)
      return
    }

    const work: Array<{mount: import('./types').LocalMount, relative: string, url: string}> = []
    let scanTruncated = false
    for (const mount of ticked) {
      const scan = candidates(mount.host)
      scanTruncated = scanTruncated || scan.truncated
      for (const file of scan.files) {
        const url = containerUrl(mount.target, file.relative)
        const known = already.has(url)
        const storedModified = already.get(url)
        // The change check compares the file's mtime against the SOURCE date the
        // server stored for it — not against when we ingested it.
        //
        // Ingestion time is always newer than the file, by construction: you index
        // a folder today, so every file in it was "ingested after it changed", and
        // nothing ever looks stale again. Ticking and unticking could not fix it,
        // because the ledger said everything was current forever.
        //
        // A known document with NO stored modified date predates date capture:
        // re-read it, once, and it gains one.
        //
        // The slack absorbs precision loss between the two views of ONE mtime:
        // the host stat carries fractional milliseconds, while the container's
        // bind mount truncates to whole SECONDS (measured: stored …482000 vs
        // host …482726.215) — so an exact >= re-read the entire corpus on every
        // sweep, "unchanged" being unreachable by construction. A real edit
        // moves mtime by far more than the slack; one within 2s of the ingest
        // is caught the next time the file is touched.
        if (known && storedModified !== undefined && storedModified >= file.mtimeMs - MTIME_SLACK_MS) {
          current.skipped++
          continue
        }
        work.push({ mount, relative: file.relative, url })
      }
    }

    const batch = work.slice(0, MAX_INGESTS_PER_SWEEP)
    const leftover = work.length - batch.length
    current.truncated = scanTruncated || leftover > 0
    current.total = batch.length

    current.phase = 'indexing'
    for (const item of batch) {
      if (!current.enabled) break // unticked/stopped mid-sweep
      current.currentFile = `${path.basename(item.mount.host)}/${item.relative}`
      const started = Date.now()
      const result = await api.ingestDocumentUrl(settings, item.url)
      current.done++
      if (result.ok) current.ok++
      else current.failed++
      await pace(Date.now() - started)
    }

    current.message =
      (batch.length === 0
        ? `everything up to date — ${current.skipped} unchanged`
        : `indexed ${current.ok}/${current.total}` +
          (current.failed ? `, ${current.failed} failed` : '') +
          `, ${current.skipped} unchanged` +
          (leftover > 0 ? `, ${leftover} queued` : '')) +
      ' · watching for changes'
    schedule(leftover > 0 ? BACKLOG_INTERVAL_MS : SWEEP_INTERVAL_MS)
  } finally {
    current.running = false
    current.phase = 'idle'
    current.currentFile = ''
    sweeping = false
  }
}

/**
 * Kick or resume the trickle. Called from Apply, and once at app launch so a
 * relaunch keeps watching without a visit to the panel. Idempotent: a sweep
 * already in flight just keeps its new settings for the next pass.
 * @param {Settings} s
 * @returns {IndexState}
 */
function start(s: Settings) {
  settings = s
  current.enabled = true
  if (!sweeping) void sweep()
  return state()
}

export { start, state }
