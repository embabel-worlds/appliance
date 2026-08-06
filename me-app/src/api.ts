// The sensor's send side. Outbound-only, on purpose: the sensor is a CLIENT of
// the appliance's API and never listens on a port. Effect verbs, when they
// exist, will ride the same direction — the sensor long-polls an outbox — so
// the appliance never needs a route back to the host.
//
// Primary transport is the sensor envelope endpoint (`/api/v1/sensor/events`,
// typed event kinds, per-event receipts). Against an older image that lacks it,
// fact sending falls back to `/api/v1/memory/remember` — same destination
// (DICE propositions), coarser receipts.

import type { ConnectionResult, Fact, SendResult, Settings } from './types'

const SOURCE = 'me-app'

const auth = (settings: Settings): string =>
  'Basic ' + Buffer.from(`${settings.username}:${settings.password}`).toString('base64')

const errorMessage = (e: unknown): string => {
  if (e instanceof Error) return (e.cause as Error | undefined)?.message ?? e.message
  return String(e)
}

async function post(settings: Settings, path: string, body: unknown): Promise<Response> {
  return fetch(`${settings.baseUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: auth(settings) },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  })
}

/** Cheap authenticated GET to prove the appliance is reachable and the credentials work. */
export async function testConnection(settings: Settings): Promise<ConnectionResult> {
  let res: Response
  try {
    res = await fetch(`${settings.baseUrl}/api/v1/hints`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(5000),
    })
  } catch (e) {
    return { ok: false, message: `Cannot reach ${settings.baseUrl}: ${errorMessage(e)}` }
  }
  if (res.status === 401 || res.status === 403) {
    return { ok: false, message: 'Connected, but the username/password was rejected.' }
  }
  if (!res.ok) return { ok: false, message: `Appliance answered HTTP ${res.status}.` }
  return { ok: true, message: 'Connected and authenticated.' }
}

interface EventReceipt {
  label: string | null
  status: string
  detail: string | null
}

interface SensorBatchResponse {
  receipts: EventReceipt[]
}

/** Send facts through the sensor envelope; falls back to the remember endpoint on 404. */
export async function sendFacts(settings: Settings, facts: Fact[]): Promise<SendResult[]> {
  let res: Response
  try {
    res = await post(settings, '/api/v1/sensor/events', {
      source: SOURCE,
      events: facts.map((f) => ({ kind: 'fact', label: f.label, text: f.text })),
    })
  } catch (e) {
    const message = errorMessage(e)
    return facts.map((f) => ({ id: f.id, label: f.label, ok: false, message }))
  }
  if (res.status === 404) return sendFactsLegacy(settings, facts)
  if (!res.ok) {
    const message = `HTTP ${res.status}`
    return facts.map((f) => ({ id: f.id, label: f.label, ok: false, message }))
  }
  const body = (await res.json()) as SensorBatchResponse
  return facts.map((f, i) => {
    const receipt = body.receipts[i]
    const ok = receipt?.status === 'stored' || receipt?.status === 'duplicate'
    return { id: f.id, label: f.label, ok, message: receipt ? receipt.status + (receipt.detail ? ` (${receipt.detail})` : '') : 'no receipt' }
  })
}

/** Older images: one fact at a time through the remember endpoint. */
async function sendFactsLegacy(settings: Settings, facts: Fact[]): Promise<SendResult[]> {
  const results: SendResult[] = []
  for (const fact of facts) {
    try {
      const res = await post(settings, '/api/v1/memory/remember', { text: fact.text })
      results.push({ id: fact.id, label: fact.label, ok: res.ok, message: res.ok ? 'stored' : `HTTP ${res.status}` })
    } catch (e) {
      results.push({ id: fact.id, label: fact.label, ok: false, message: errorMessage(e) })
    }
  }
  return results
}

/** Push one presence observation (the ambient stream). Requires the sensor endpoint. */
export async function sendPresence(settings: Settings, active: boolean): Promise<ConnectionResult> {
  let res: Response
  try {
    res = await post(settings, '/api/v1/sensor/events', {
      source: SOURCE,
      events: [{ kind: 'context', stream: 'presence', activities: active ? ['WORKING'] : [] }],
    })
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
  if (res.status === 404) return { ok: false, message: 'This appliance version has no sensor endpoint — streaming needs a newer image.' }
  if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
  return { ok: true, message: active ? 'active at keyboard' : 'idle' }
}
