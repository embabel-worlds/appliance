/*
 * THE KIT'S TRANSPORT, OVER THE BRIDGE. A kit surface in this app has no network of its own: the
 * sandbox and the CSP both see to that. So each request a kit client shapes crosses the bridge as its
 * `RequestSpec` and is sent by the kit's `HttpTransport` in the main process, which refuses any route
 * these pages do not use. The client's own reading of the answer, a 409's refusal included, runs
 * here unchanged.
 *
 * Settings are read per request, because a window builds its clients before it has loaded them.
 */
import type { Outcome, RequestSpec, Transport } from '@embabel/appliance-kit'
import type { Settings } from './types'

export function ipcTransport(settings: () => Settings): Transport {
  return {
    send: <T,>(spec: RequestSpec) => window.me.kitSend(settings(), spec) as Promise<Outcome<T>>,
  }
}
