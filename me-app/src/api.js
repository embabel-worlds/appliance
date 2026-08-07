// The sensor's send side. Outbound-only, on purpose: the sensor is a CLIENT of
// the appliance's API and never listens on a port. Effect verbs, when they
// exist, will ride the same direction — the sensor long-polls an outbox — so
// the appliance never needs a route back to the host.
//
// Primary transport is the sensor envelope endpoint (`/api/v1/sensor/events`,
// typed event kinds, per-event receipts). Against an older image that lacks it,
// fact sending falls back to `/api/v1/memory/remember` — same destination
// (DICE propositions), coarser receipts.

/** @typedef {import('./types').ConnectionResult} ConnectionResult */
/** @typedef {import('./types').Fact} Fact */
/** @typedef {import('./types').FocusSample} FocusSample */
/** @typedef {import('./types').SendResult} SendResult */
/** @typedef {import('./types').Settings} Settings */

const { diagnose } = require('./appliance')

const SOURCE = 'me-app'

/** @param {Settings} settings */
const auth = (settings) =>
  'Basic ' + Buffer.from(`${settings.username}:${settings.password}`).toString('base64')

/** @param {unknown} e @returns {string} */
const errorMessage = (e) => {
  if (e instanceof Error) return e.cause?.message ?? e.message
  return String(e)
}

/** @param {Settings} settings @param {string} path @param {unknown} body */
/**
 * Facts are slower than they look: each batch resolves entities and projects
 * graph edges server-side, so a first scan of a dozen long facts can outlast a
 * conversational timeout. Generous here, tight everywhere else.
 */
const FACTS_TIMEOUT_MS = 120_000

/** Parse a JSON body without letting a non-JSON one throw past the caller. */
async function readJson(res) {
  try {
    return await res.json()
  } catch {
    return null
  }
}

async function post(settings, path, body, timeoutMs = 15000) {
  return fetch(`${settings.baseUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: auth(settings) },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  })
}

/**
 * Cheap authenticated GET to prove the appliance is reachable and the credentials work.
 * @param {Settings} settings
 * @returns {Promise<ConnectionResult>}
 */
async function testConnection(settings) {
  let res
  try {
    res = await fetch(`${settings.baseUrl}/api/v1/hints`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(5000),
    })
  } catch (e) {
    // Unreachable is the interesting case: "connection refused" is true and
    // useless, whereas "Docker is not installed" is the actual next step.
    const why = await diagnose(settings.baseUrl)
    return { ok: false, message: why.message, action: why.action, url: why.url, state: why.state }
  }
  if (res.status === 401 || res.status === 403) {
    return { ok: false, message: 'Connected, but the username/password was rejected.' }
  }
  if (!res.ok) return { ok: false, message: `Appliance answered HTTP ${res.status}.` }
  return { ok: true, message: 'Connected and authenticated.' }
}

/**
 * Send facts through the sensor envelope; falls back to the remember endpoint on 404.
 * @param {Settings} settings
 * @param {Fact[]} facts
 * @returns {Promise<SendResult[]>}
 */
async function sendFacts(settings, facts) {
  let res
  try {
    res = await post(
      settings,
      '/api/v1/sensor/events',
      { source: SOURCE, events: facts.map((f) => ({ kind: 'fact', label: f.label, text: f.text })) },
      FACTS_TIMEOUT_MS,
    )
  } catch (e) {
    const message = errorMessage(e)
    return facts.map((f) => ({ id: f.id, label: f.label, ok: false, message }))
  }
  if (res.status === 404) return sendFactsLegacy(settings, facts)
  if (!res.ok) {
    const message = `HTTP ${res.status}`
    return facts.map((f) => ({ id: f.id, label: f.label, ok: false, message }))
  }
  const body = await readJson(res)
  if (!body?.receipts) {
    return facts.map((f) => ({ id: f.id, label: f.label, ok: false, message: 'sent, but the reply was unreadable' }))
  }
  return facts.map((f, i) => {
    const receipt = body.receipts[i]
    const ok = receipt?.status === 'stored' || receipt?.status === 'duplicate'
    return { id: f.id, label: f.label, ok, message: receipt ? receipt.status + (receipt.detail ? ` (${receipt.detail})` : '') : 'no receipt' }
  })
}

/**
 * Older images: one fact at a time through the remember endpoint.
 * @param {Settings} settings
 * @param {Fact[]} facts
 * @returns {Promise<SendResult[]>}
 */
async function sendFactsLegacy(settings, facts) {
  const results = []
  for (const fact of facts) {
    try {
      const res = await post(settings, '/api/v1/memory/remember', { text: fact.text }, FACTS_TIMEOUT_MS)
      results.push({ id: fact.id, label: fact.label, ok: res.ok, message: res.ok ? 'stored' : `HTTP ${res.status}` })
    } catch (e) {
      results.push({ id: fact.id, label: fact.label, ok: false, message: errorMessage(e) })
    }
  }
  return results
}

/**
 * Push one focus observation (the ambient stream). Requires the sensor endpoint.
 *
 * WORKING is asserted only when the user is actually at the keyboard and the
 * screen is unlocked — an idle desktop showing IntelliJ is not someone working,
 * and claiming otherwise would corrupt the very interruption decisions this
 * stream exists to inform.
 *
 * @param {Settings} settings
 * @param {FocusSample} sample
 * @returns {Promise<ConnectionResult>}
 */
async function sendFocus(settings, sample) {
  const working = sample.active === true && sample.locked !== true
  let res
  try {
    res = await post(settings, '/api/v1/sensor/events', {
      source: SOURCE,
      events: [
        {
          kind: 'context',
          stream: 'focus',
          activities: working ? ['WORKING'] : [],
          app: sample.app,
          detail: sample.detail,
          media: sample.media,
          focusMode: sample.focusMode,
          locked: sample.locked,
        },
      ],
    })
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
  if (res.status === 404) return { ok: false, message: 'This appliance version has no sensor endpoint — streaming needs a newer image.' }
  if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
  // Echo the server's own rendering of what it understood, so the UI shows what
  // was actually communicated rather than what we hoped to communicate.
  const body = await readJson(res)
  return { ok: true, message: body?.receipts?.[0]?.detail ?? (working ? 'active' : 'idle') }
}

/**
 * The documents the appliance already holds — uri + ingestedAt per document.
 * The indexer's reconcile anchor (embabel/appliance#10): server truth, so
 * there is no client ledger to drift.
 * @param {Settings} settings
 * @returns {Promise<{ok: boolean, message: string, documents: Array<{uri?: string, title?: string, ingestedAt?: string}>}>}
 */
async function listDocuments(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/documents`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, documents: [] }
    const body = await readJson(res)
    return { ok: true, message: '', documents: body?.documents ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e), documents: [] }
  }
}

/**
 * Ingest one document by URL — for the Local files panel this is a file:// URL
 * under /local, which the server reads straight off its own mount (no bytes
 * shipped). Conversion of a big PDF is genuinely slow, hence the long timeout.
 * @param {Settings} settings
 * @param {string} url
 * @returns {Promise<ConnectionResult>}
 */
async function ingestDocumentUrl(settings, url) {
  let res
  try {
    res = await post(settings, '/api/v1/documents/url', { url }, 300_000)
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
  const body = await readJson(res)
  if (!res.ok || body?.status === 'error') {
    return { ok: false, message: body?.message ?? `HTTP ${res.status}` }
  }
  return { ok: true, message: body?.title ?? 'ingested' }
}

module.exports = { testConnection, sendFacts, sendFocus, listDocuments, ingestDocumentUrl }
