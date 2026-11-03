/*
 * QUERY STUDIO: the kit's `QueryStudioSurface`, the same one the Worlds console shows.
 *
 * Nothing here edits, completes, validates, runs or draws a query; the kit does all of it — the
 * example cards over an empty editor, completion and hover from the schema the engine validates
 * against, the results table and graph, Ask and Refine, saving a view, scopes, fills and the
 * Session pane — so the two front ends cannot disagree about what a query means or what it
 * returned. This file is the host: the services the surface asks for, reached through the kit's own
 * client over the bridge, and the two things this app keeps beside it that the surface does not
 * have — the composer and the plan panel.
 */
import React from 'react'
import { createPortal } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { KgClient, ok } from '@embabel/appliance-kit'
import {
  QueryStudioSurface,
  type FeatureState,
  type InteractiveSessionState,
  type KgFill,
  type QueryHistoryEntry,
  type QueryStudioHost,
  type QueryStudioServices,
} from '@embabel/appliance-kit/react/features'
import type { VcEvent } from '@embabel/appliance-kit/vc'
import { restoreTheme } from './theme'
import { EMPTY_SETTINGS } from './studio-deps'
import { ipcTransport } from './kit-transport'
import { queryComposer } from './query-composer'
import { initLensModel } from './query-lens-model'
import { QueryPlan } from './query-plan'
import type { Settings } from './types'

let settings: Settings = EMPTY_SETTINGS

const transport = ipcTransport(() => settings)
const kg = new KgClient(transport)

const services: QueryStudioServices = {
  kg,
  /*
   * Fills are the one service with no method on the kit's client: the console reaches them with its
   * own calls, and so does this. Over the same bridge, so the main process's route list still decides.
   */
  fills: {
    list: () => transport.send<KgFill[]>({ method: 'GET', path: '/api/v1/admin/kg/fills' }),
    create: (cypher, label) => transport.send<KgFill>({ method: 'POST', path: '/api/v1/admin/kg/fills', body: { cypher, label } }),
    async delete(id) {
      const outcome = await transport.send<unknown>({ method: 'DELETE', path: `/api/v1/admin/kg/fills/${encodeURIComponent(id)}` })
      return outcome.ok ? ok(undefined) : outcome
    },
  },
  /*
   * The engine's live trace. The page has no network, so the main process holds the stream and
   * forwards each event; aborting the signal closes it there.
   */
  subscribeProgress(onEvent: (event: VcEvent) => void, signal: AbortSignal) {
    const stop = window.me.vcEvents(settings, (event) => onEvent(event as VcEvent))
    signal.addEventListener('abort', () => stop(), { once: true })
  },
}

/*
 * History and the Session pane persist in this window's own storage. The history key is the one the
 * app's earlier studio wrote, in the same shape, so the queries someone ran before carry over.
 */
function featureState<T>(key: string, accepts: (value: unknown) => value is T): FeatureState<T> {
  return {
    read() {
      try {
        const raw = localStorage.getItem(key)
        if (raw === null) return null
        const value: unknown = JSON.parse(raw)
        return accepts(value) ? value : null
      } catch {
        return null
      }
    },
    write(value) {
      try {
        if (value === null) localStorage.removeItem(key)
        else localStorage.setItem(key, JSON.stringify(value))
      } catch {
        // A full or unavailable store leaves the studio working; it only forgets.
      }
    },
  }
}

const isHistory = (value: unknown): value is QueryHistoryEntry[] =>
  Array.isArray(value) && value.every((e) => e !== null && typeof e === 'object'
    && typeof e.cypher === 'string' && (e.rows === null || typeof e.rows === 'number') && typeof e.at === 'string')

/* The Session pane's state is the kit's to read; a stored value it cannot use is dropped by it. */
const isSession = (value: unknown): value is InteractiveSessionState =>
  value !== null && typeof value === 'object' && Array.isArray((value as InteractiveSessionState).entries)

const host: QueryStudioHost = {
  history: featureState('embabel-me-studio-history', isHistory),
  interactive: { session: featureState('embabel-me-studio-session', isSession) },
}

const STARTER_QUERY = `MATCH (n)
RETURN n LIMIT 25`

function QueryStudio() {
  /* What the composer last put in the editor, and a counter so composing the same text lands it again. */
  const [handoff, setHandoff] = React.useState<{ cypher: string; n: number } | null>(null)
  const [cypher, setCypher] = React.useState('')
  const current = React.useRef('')
  current.current = cypher
  const lastComposed = React.useRef<string | null>(null)
  lastComposed.current = handoff?.cypher ?? null

  React.useEffect(() => {
    queryComposer.init({
      settings,
      land: (text) => setHandoff((h) => ({ cypher: text, n: (h?.n ?? 0) + 1 })),
      isHandEdited: () => current.current.trim() !== '' && current.current !== lastComposed.current,
    })
    void initLensModel(settings)
  }, [])

  return (
    <>
      <QueryStudioSurface
        services={services}
        host={host}
        handedOver={handoff?.cypher}
        handoffRevision={handoff?.n}
        initialCypher={STARTER_QUERY}
        onCypherChange={setCypher}
      />
      {createPortal(<QueryPlan kg={kg} goal={cypher} />, document.getElementById('plan'))}
    </>
  )
}

async function init() {
  settings = await window.me.loadSettings()
  void restoreTheme(settings)
  createRoot(document.getElementById('studio')).render(<QueryStudio />)
}

void init()
