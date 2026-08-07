// The outbox loop — how the assistant asks this machine to do something, and
// the only inbound path there is. The appliance NEVER calls the host, so the
// sensor long-polls for commands instead: the server holds each poll ~25s, so
// idle cost is about two requests a minute, and an enqueued command completes
// the held request immediately — chat-speed, with no listening port.
//
// Two things travel on this loop, and the order matters. FIRST the sensor
// publishes its catalog: the verbs it can do, for the consent groups the user
// has ticked (verbs.js). Only then can the appliance name any of them. The
// catalog is republished whenever consent changes, so revoking a group takes
// effect on the next command, not the next restart.

const verbs = require('./verbs')

/** @typedef {import('./types').Settings} Settings */

const RETRY_MAX_MS = 30_000
/** How often the verb catalog is re-asserted, so a restarted appliance relearns it. */
const REPUBLISH_MS = 5 * 60_000
/** Outer guard on the held poll — well past the server's ~25s hold. */
const POLL_GUARD_MS = 40_000

let running = false
/** @type {AbortController | null} */
let current = null
/** @type {Settings | null} */
let settings = null
let log = (_line) => {}
/** When the catalog was last published — see the republish note in loop(). */
let publishedAt = 0

/** @param {Settings} s */
const auth = (s) => 'Basic ' + Buffer.from(`${s.username}:${s.password}`).toString('base64')

/** The consent groups this loop is running under. */
const consentOf = (s) => s?.verbs ?? {}

/**
 * Start (or retarget) the loop, publishing the catalog for whatever the user
 * has consented to. Idempotent; the freshest settings win.
 * @param {Settings} s @param {(line: string) => void} [logger]
 */
function start(s, logger) {
  settings = s
  if (logger) log = logger
  const enabled = Object.entries(consentOf(s)).filter(([, on]) => on).map(([g]) => g)
  if (enabled.length === 0) {
    stop()
    return
  }
  void publish()
  if (running) return
  running = true
  log(`[me-app] outbox: listening — consent: ${enabled.join(', ')}`)
  void loop()
}

function stop() {
  if (running) log('[me-app] outbox: stopped')
  running = false
  current?.abort()
  // Tell the appliance the vocabulary is empty, so it stops offering verbs the
  // instant consent is withdrawn rather than when the next poll would have been.
  void publish([])
}

const state = () => ({ running })

/**
 * Publish the verb catalog. The appliance can only ever ask for what appears
 * here — an unpublished verb is one the assistant cannot name.
 * @param {any[]} [override]
 */
async function publish(override) {
  if (!settings?.username) return
  const list = override ?? verbs.catalog(consentOf(settings))
  try {
    publishedAt = Date.now()
    await fetch(`${settings.baseUrl}/api/v1/sensor/capabilities`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: auth(settings) },
      body: JSON.stringify({ verbs: list }),
      signal: AbortSignal.timeout(10_000),
    })
  } catch (e) {
    log(`[me-app] outbox: could not publish capabilities: ${e?.message ?? e}`)
  }
}

async function loop() {
  let backoff = 1_000
  while (running) {
    // Republish periodically: the appliance holds the catalog in memory, so a
    // restart there leaves it thinking this machine offers nothing while the
    // sensor polls on none the wiser. Cheap (one POST per five minutes) and it
    // makes the two sides converge from any state rather than only from ours.
    if (Date.now() - publishedAt > REPUBLISH_MS) await publish()
    current = new AbortController()
    let res
    try {
      res = await fetch(`${settings.baseUrl}/api/v1/sensor/outbox`, {
        headers: { Authorization: auth(settings) },
        signal: AbortSignal.any([current.signal, AbortSignal.timeout(POLL_GUARD_MS)]),
      })
    } catch {
      if (!running) return
      await sleep(backoff)
      backoff = Math.min(backoff * 2, RETRY_MAX_MS)
      continue
    }
    if (res.status === 204) {
      backoff = 1_000
      continue
    }
    if (!res.ok) {
      await sleep(backoff)
      backoff = Math.min(backoff * 2, RETRY_MAX_MS)
      continue
    }
    backoff = 1_000
    const command = await res.json().catch(() => null)
    if (!command?.id || !command.kind) continue
    // Consent is re-read from the LIVE settings on every command, so a group
    // switched off a second ago is already refused.
    const result = await verbs
      .run(command.kind, command.payload, consentOf(settings))
      .catch((e) => ({ ok: false, error: e instanceof Error ? e.message : String(e) }))
    log(`[me-app] verb ${command.kind}: ${summarize(result)}`)
    await fetch(`${settings.baseUrl}/api/v1/sensor/outbox/${command.id}/result`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: auth(settings) },
      body: JSON.stringify(result),
      signal: AbortSignal.timeout(10_000),
    }).catch((e) => log(`[me-app] outbox: could not deliver result for ${command.kind}: ${e?.message ?? e}`))
  }
}

/** A one-line account of what a verb answered — shape, never the user's content. */
function summarize(result) {
  if (result?.error) return `refused (${result.error})`
  if (Array.isArray(result?.tabs)) return `${result.tabs.length} match of ${result.openTabCount ?? '?'} open tab(s)`
  if (result?.ok === false) return 'failed'
  return 'ok'
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

module.exports = { start, stop, state, publish }
