// Asking questions of the appliance's documents, and making the answer's
// citations openable.
//
// The appliance answers with URIs as IT sees them — a shared folder is
// `/local/<name>/…` inside the container. That path means nothing in Finder. But
// this app is the half that knows the mapping: the mounts it wrote to
// docker-compose.override.yml record `<host folder>:/local/<name>:ro`, so a
// container path can be walked back to the file on this Mac.
//
// That walk-back is the whole point of doing attribution here rather than in a
// web UI: a citation you can OPEN is verification, a citation you can only read
// is decoration.

const fs = require('node:fs')
const path = require('node:path')
const mounts = require('./mounts')

/** @typedef {import('./types').Settings} Settings */

const ASK_TIMEOUT_MS = 180_000 // agentic retrieval runs a bounded LLM loop; be patient

/** @param {Settings} settings */
const auth = (settings) =>
  'Basic ' + Buffer.from(`${settings.username}:${settings.password}`).toString('base64')

/**
 * Where a cited document actually lives, from the app's side of the boundary.
 *
 * @param {string | null | undefined} uri
 * @returns {{ kind: 'local' | 'url' | 'opaque', path?: string, url?: string, exists?: boolean, label: string }}
 */
function resolveSource(uri) {
  if (!uri) return { kind: 'opaque', label: 'unknown source' }

  if (/^https?:\/\//i.test(uri)) {
    // A document fetched from the web: the URL is where it came from and where
    // the user should go back to.
    let label = uri
    try {
      label = new URL(uri).hostname + new URL(uri).pathname
    } catch {
      /* keep the raw uri */
    }
    return { kind: 'url', url: uri, label }
  }

  if (uri.startsWith('file://')) {
    let containerPath
    try {
      containerPath = decodeURIComponent(new URL(uri).pathname)
    } catch {
      containerPath = uri.replace(/^file:\/\//, '')
    }
    const host = toHostPath(containerPath)
    if (host) {
      return { kind: 'local', path: host, exists: fs.existsSync(host), label: tildeify(host) }
    }
    // A file:// path we cannot map: inside the appliance's own volume, or from a
    // mount that has since been removed. Honest about that rather than offering
    // a button that would open nothing.
    return { kind: 'opaque', label: containerPath }
  }

  return { kind: 'opaque', label: uri }
}

/**
 * Translate a container path under /local back to this Mac, using the mounts the
 * app itself wrote. Returns null when no mount covers it.
 * @param {string} containerPath
 * @returns {string | null}
 */
function toHostPath(containerPath) {
  const state = mounts.state()
  if (!state.supported) return null
  for (const mount of state.mounts) {
    if (containerPath === mount.target) return mount.host
    if (containerPath.startsWith(mount.target + '/')) {
      const rest = containerPath.slice(mount.target.length + 1)
      return path.join(mount.host, rest)
    }
  }
  return null
}

const tildeify = (p) => p.replace(require('node:os').homedir(), '~')

/**
 * Ask the appliance a question of the user's documents. Sources come back
 * already resolved to somewhere openable, so the renderer only renders.
 *
 * @param {Settings} settings
 * @param {{question: string, from?: string, to?: string, topK?: number, history?: string[]}} request
 */
async function ask(settings, request) {
  const body = {
    question: request.question,
    dateField: request.dateField || undefined,
    from: request.from || undefined,
    to: request.to || undefined,
    topK: request.topK || undefined,
    history: request.history || [],
    answer: true,
  }

  let res
  try {
    res = await fetch(`${settings.baseUrl}/api/v1/documents/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: auth(settings) },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(ASK_TIMEOUT_MS),
    })
  } catch (e) {
    const message = e instanceof Error ? (e.cause?.message ?? e.message) : String(e)
    return { ok: false, message: `Could not reach the appliance: ${message}` }
  }

  if (res.status === 404) {
    return { ok: false, message: 'This appliance has no /documents/ask endpoint — it needs a newer image.' }
  }
  if (res.status === 401 || res.status === 403) {
    return { ok: false, message: 'The appliance rejected these credentials.' }
  }
  if (!res.ok) return { ok: false, message: `The appliance answered HTTP ${res.status}.` }

  let payload
  try {
    payload = await res.json()
  } catch {
    return { ok: false, message: 'The appliance sent a reply this app could not read.' }
  }

  return {
    ok: true,
    answer: payload.answer ?? null,
    // Why there is no answer, when the appliance can tell — a model that spent
    // its budget thinking rather than writing, most often.
    note: payload.note ?? null,
    unresolvedCitations: payload.unresolvedCitations ?? 0,
    filters: payload.filters ?? {},
    sources: (payload.sources ?? []).map((s) => ({ ...s, where: resolveSource(s.uri) })),
  }
}

module.exports = { ask, resolveSource }
