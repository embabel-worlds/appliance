/*
 * THE ROUTINE STUDIO: the kit's `HandlerStudioSurface`, the same one the Worlds console shows.
 *
 * Nothing here edits, completes, validates or dry-runs a routine; the kit does all of it, so the two
 * front ends cannot disagree about what a routine is or what saving one does. This file is the host:
 * the services the surface asks for, reached through the kit's own clients over the bridge, and the
 * two ways this app moves between windows. A routine can be opened here from its agent, and a
 * routine's agent button opens the Agents window at that agent, which is where its stage is set.
 */
import React from 'react'
import { createRoot } from 'react-dom/client'
import {
  HandlersClient,
  KgClient,
  ok,
  AgentsClient,
  CronClient,
  type HandlerGenerated,
  type Outcome,
} from '@embabel/appliance-kit'
import { HandlerStudioSurface, type HandlerStudioServices, type SignalType, type WorldSkill } from '@embabel/appliance-kit/react/features'
import { restoreTheme } from './theme'
import { EMPTY_SETTINGS } from './studio-deps'
import { ipcTransport } from './kit-transport'
import type { Settings } from './types'

let settings: Settings = EMPTY_SETTINGS

const transport = ipcTransport(() => settings)
const handlers = new HandlersClient(transport)
const agents = new AgentsClient(transport)
const cron = new CronClient(transport)

const services: HandlerStudioServices = {
  kg: new KgClient(transport),
  handlers,
  listAgents: () => agents.list(),
  compileSchedule: (schedule) => cron.compileSchedule(schedule),
  // Sent whole rather than through `handlers.generate`, which does not yet carry the skills.
  generateHandler: (english, current, skills) => transport.send<HandlerGenerated>({
    method: 'POST', path: '/api/v1/admin/handlers/generate', body: { english, current, skills },
  }),
  saveHandler: (spec) => handlers.save(spec),
  async gatewayInterfaces(): Promise<Outcome<string>> {
    const result = await window.me.gatewaySurface(settings)
    return result.ok ? ok(result.text) : { ok: false, kind: 'unreachable', message: `Could not load the gateway: ${result.message}` }
  },
  signalTypes: () => transport.send<SignalType[]>({ method: 'GET', path: '/api/v1/signal-types' }),
  worldSkills: () => transport.send<WorldSkill[]>({ method: 'GET', path: '/api/v1/world/skills' }),
}

/*
 * Opened at a routine: once from the query the window was opened with, and again for each Edit
 * while it is open. The counter makes a second request for the same routine reopen it.
 */
function RoutineStudio() {
  const [request, setRequest] = React.useState(() => {
    const name = new URLSearchParams(location.search).get('open')
    return name ? { name, n: 1 } : null
  })
  React.useEffect(() => window.me.onHandlerOpenRequest((name) => setRequest((r) => ({ name, n: (r?.n ?? 0) + 1 }))), [])
  return (
    <HandlerStudioSurface
      services={services}
      openRequest={request}
      onOpenAgent={(agent) => void window.me.openAgents(agent)}
    />
  )
}

async function init() {
  settings = await window.me.loadSettings()
  void restoreTheme(settings)
  createRoot(document.getElementById('studio')).render(<RoutineStudio />)
}

void init()
