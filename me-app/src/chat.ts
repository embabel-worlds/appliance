// Chat transport — the smallest possible window onto the appliance's chat,
// speaking the SAME protocol the Worlds console and the TUI speak:
//
//   POST /api/v1/chat {message}        → 202; the reply does NOT come back here
//   GET  /api/v1/chat/stream           → SSE: connected/user/progress/message/done
//   GET  /api/v1/chat/history          → the conversation, authoritative
//
// History is the source of truth; the stream only animates liveness. That is a
// lesson, not a shortcut: replies broadcast to every surface bound to the chat
// session (web UI, TUI, Telegram, here), so a client trusting only its own
// stream misses turns whenever another surface is open. The TUI learned this
// first; the console encodes it; this transport inherits it.
//
// The POST endpoint refuses (409) unless an SSE stream is open, so the stream
// is opened before the first send and kept alive with dumb 3s reconnects while
// the renderer wants it. Zero dependencies: Node's fetch streams the SSE body,
// and ~30 lines below parse it — an EventSource polyfill would be the app's
// first dependency, for a format that is two prefixed lines and a blank.

import type { Settings } from './types'

const RECONNECT_MS = 3_000

/** @param {Settings} settings */
const auth = (settings: Settings) =>
  'Basic ' + Buffer.from(`${settings.username}:${settings.password}`).toString('base64')

let controller: AbortController | null = null
/** The settings the open stream was built from — a change reopens it. */
let streamKey = ''
/** Latest event sink from the renderer side; swapped on every open(). */
let emit: (event: unknown) => void = () => {}
let wanted = false

const keyOf = (settings: Settings) => `${settings.baseUrl}|${settings.username}`

/**
 * Want a live stream against these settings, forwarding events through
 * [sink]. Idempotent: same settings → the existing stream stands.
 * @param {Settings} settings
 * @param {(event: { type: string, data?: any }) => void} sink
 */
function open(settings: Settings, sink: (event: unknown) => void) {
  emit = sink
  wanted = true
  const key = keyOf(settings)
  if (controller && streamKey === key) return
  controller?.abort()
  streamKey = key
  void pump(settings, key)
}

/** Stop wanting a stream (window closed, app quitting). */
function stop() {
  wanted = false
  controller?.abort()
  controller = null
  streamKey = ''
}

/**
 * Hold the SSE connection open, forwarding events, reconnecting on drop for
 * as long as this key is still the wanted one.
 * @param {Settings} settings @param {string} key
 */
async function pump(settings: Settings, key: string) {
  while (wanted && streamKey === key) {
    controller = new AbortController()
    try {
      const res = await fetch(`${settings.baseUrl}/api/v1/chat/stream`, {
        headers: { Authorization: auth(settings), Accept: 'text/event-stream' },
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        emit({ type: 'stream-error', data: { message: `HTTP ${res.status}` } })
      } else {
        // Minimal SSE parse: events are `event:` + `data:` lines ended by a
        // blank line. Anything unparseable is passed through as raw text.
        let buffer = ''
        // The DOM type has no async iterator; Node's implementation does.
    for await (const chunk of res.body as unknown as AsyncIterable<Uint8Array>) {
          buffer += Buffer.from(chunk).toString('utf8')
          let cut
          while ((cut = buffer.indexOf('\n\n')) >= 0) {
            const block = buffer.slice(0, cut)
            buffer = buffer.slice(cut + 2)
            let type = 'message'
            let data = ''
            for (const line of block.split('\n')) {
              if (line.startsWith('event:')) type = line.slice(6).trim()
              else if (line.startsWith('data:')) data += line.slice(5).trim()
            }
            if (!data && !type) continue
            let parsed
            try {
              parsed = JSON.parse(data)
            } catch {
              parsed = { content: data }
            }
            if (wanted && streamKey === key) emit({ type, data: parsed })
          }
        }
      }
    } catch {
      /* aborted, refused, or dropped — the loop below decides what happens */
    }
    if (!wanted || streamKey !== key) return
    emit({ type: 'stream-closed' })
    await new Promise((resolve) => setTimeout(resolve, RECONNECT_MS))
  }
}

/** True while a stream is (nominally) open for these settings. */
const streaming = (settings: Settings) => Boolean(controller) && streamKey === keyOf(settings) && wanted

/**
 * Send one user message. The reply arrives on the stream / in history, never
 * in this response. A 409 means the server saw no stream yet — one settle-and-
 * retry covers the race where the stream is still connecting.
 * @param {Settings} settings @param {string} text
 * @returns {Promise<{ ok: boolean, message: string }>}
 */
async function send(settings: Settings, text: string) {
  for (let attempt = 0; ; attempt++) {
    let res
    try {
      res = await fetch(`${settings.baseUrl}/api/v1/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: auth(settings) },
        body: JSON.stringify({ message: text }),
        signal: AbortSignal.timeout(15_000),
      })
    } catch (e) {
      return { ok: false, message: e instanceof Error ? ((e.cause as Error | undefined)?.message ?? e.message) : String(e) }
    }
    if (res.status === 409 && attempt === 0 && streaming(settings)) {
      await new Promise((resolve) => setTimeout(resolve, 1_200))
      continue
    }
    if (res.status === 409) return { ok: false, message: 'No stream open — reopen the Chat tab.' }
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    return { ok: true, message: 'sent' }
  }
}

/**
 * The conversation so far, normalized to `{role, content}` rows the renderer
 * can paint without knowing the server's message classes.
 * @param {Settings} settings
 * @returns {Promise<{ ok: boolean, message: string, messages: Array<{ role: string, content: string }> }>}
 */
async function history(settings: Settings) {
  let res
  try {
    res = await fetch(`${settings.baseUrl}/api/v1/chat/history`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(15_000),
    })
  } catch (e) {
    return { ok: false, message: e instanceof Error ? ((e.cause as Error | undefined)?.message ?? e.message) : String(e), messages: [] }
  }
  if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, messages: [] }
  const body = await res.json().catch((): null => null)
  const raw = Array.isArray(body) ? body : body?.messages ?? []
  const messages = raw
    .map((m: { role?: string; content?: string }) => ({
      role: String(m.role ?? '').toLowerCase().includes('user') ? 'user' : 'assistant',
      content: String(m.content ?? ''),
    }))
    .filter((m: { content?: string }) => m.content)
  return { ok: true, message: '', messages }
}

export { open, stop, send, history }
