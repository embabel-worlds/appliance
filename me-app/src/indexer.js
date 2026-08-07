// Opt-in document indexing — promoting shared folders into the knowledge base.
//
// The mounts make files QUERYABLE (virtual Cypher walks them live; metadata and
// grep, nothing stored). Indexing is the deliberate second step: for folders the
// user ticked, each document is ingested through the appliance's own pipeline
// (conversion, chunking, embedding into the graph), which is what unlocks
// summarization and semantic search over file BODIES — PDFs included.
//
// No bytes are shipped: the files already exist INSIDE the container under
// /local, so each one is a `POST /api/v1/documents/url` with a file:// URL and
// the server reads its mount directly. Re-ingesting the same URL replaces the
// stored version, so re-running Apply refreshes changed documents rather than
// duplicating them.
//
// The walk happens on the HOST (this folder is the user's own; the app can read
// it), mirroring the server walk's manners: dotfiles and build/dependency
// directories skipped, an extension allowlist, size and count caps with honest
// truncation. Ingestion is sequential — document conversion is the slow part
// and the appliance shouldn't be flooded — with progress readable at any time
// via state(), which the renderer polls.

const fs = require('node:fs')
const path = require('node:path')
const api = require('./api')

/** @typedef {import('./types').Settings} Settings */
/** @typedef {import('./types').LocalMount} LocalMount */
/** @typedef {import('./types').IndexState} IndexState */

/** Extensions the documents pipeline can do something real with. */
const DOC_EXTENSIONS = new Set([
  'pdf', 'md', 'markdown', 'txt', 'docx', 'doc', 'pptx', 'xlsx', 'csv', 'html', 'htm', 'rtf',
])
const DENY_DIRECTORIES = new Set(['node_modules', 'target', 'build'])
const MAX_FILE_BYTES = 30 * 1024 * 1024
const MAX_FILES_PER_MOUNT = 200
const MAX_DEPTH = 12
// The appliance restarts right before indexing; wait this long for it to answer.
const WAIT_ATTEMPTS = 60
const WAIT_INTERVAL_MS = 3_000

/** @type {IndexState} */
const current = {
  running: false,
  phase: 'idle',
  total: 0,
  done: 0,
  ok: 0,
  failed: 0,
  currentFile: '',
  message: '',
  truncated: false,
}

/** @returns {IndexState} */
const state = () => ({ ...current })

/**
 * Candidate documents under a mount's host folder, as container-relative paths.
 * @param {string} hostDir
 * @returns {{ files: string[], truncated: boolean }}
 */
function candidates(hostDir) {
  /** @type {string[]} */
  const files = []
  let truncated = false
  /** @param {string} dir @param {string} rel @param {number} depth */
  const walk = (dir, rel, depth) => {
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
        try {
          if (fs.statSync(full).size > MAX_FILE_BYTES) continue
        } catch {
          continue
        }
        if (files.length >= MAX_FILES_PER_MOUNT) {
          truncated = true
          return
        }
        files.push(relative)
      }
    }
  }
  walk(hostDir, '', 0)
  return { files, truncated }
}

/** The container-side file:// URL for a document, path segments encoded. */
const containerUrl = (target, relative) =>
  'file://' + [...target.split('/'), ...relative.split('/')].map(encodeURIComponent).join('/')

/**
 * Index every ticked mount. Fire-and-forget from the IPC handler; progress and
 * the final verdict live in state(). A second call while running is a no-op.
 * @param {Settings} settings
 * @param {LocalMount[]} mounts
 */
async function start(settings, mounts) {
  if (current.running) return
  const ticked = mounts.filter((m) => m.index)
  Object.assign(current, {
    running: true, phase: 'waiting', total: 0, done: 0, ok: 0, failed: 0,
    currentFile: '', message: '', truncated: false,
  })
  try {
    if (ticked.length === 0) {
      current.message = 'no folders are ticked for indexing'
      return
    }

    // The Apply that got us here restarted the assistant — wait it back up.
    let up = false
    for (let attempt = 0; attempt < WAIT_ATTEMPTS && !up; attempt++) {
      up = (await api.testConnection(settings)).ok
      if (!up) await new Promise((r) => setTimeout(r, WAIT_INTERVAL_MS))
    }
    if (!up) {
      current.message = 'the appliance did not come back in time — press Apply again to retry indexing'
      current.failed = 1
      return
    }

    current.phase = 'scanning'
    const work = ticked.map((mount) => ({ mount, ...candidates(mount.host) }))
    current.total = work.reduce((n, w) => n + w.files.length, 0)
    current.truncated = work.some((w) => w.truncated)

    current.phase = 'indexing'
    for (const { mount, files } of work) {
      for (const relative of files) {
        current.currentFile = `${path.basename(mount.host)}/${relative}`
        const result = await api.ingestDocumentUrl(settings, containerUrl(mount.target, relative))
        current.done++
        if (result.ok) current.ok++
        else current.failed++
      }
    }
    current.message =
      `indexed ${current.ok}/${current.total} document(s)` +
      (current.failed ? `, ${current.failed} failed` : '') +
      (current.truncated ? ` (capped at ${MAX_FILES_PER_MOUNT} per folder)` : '') +
      ' — ask the assistant about them'
  } finally {
    current.running = false
    current.phase = 'idle'
    current.currentFile = ''
  }
}

module.exports = { start, state }
