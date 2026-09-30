/*
 * THE AGENTS WINDOW: the kit's Agents surface, the same one the Worlds console shows.
 *
 * Nothing here draws an agent. The roster, the stage ladder, the refusals and the signing all
 * live in `@embabel/appliance-kit`, so the two front ends cannot describe one agent two ways.
 * What this file adds is the host: a transport that reaches the appliance, a question before a
 * signature, and a way from a routine to the editor for its body.
 *
 * Its requests go through `kit-transport.ts`, the kit's own client over the bridge.
 */
import React from 'react'
import { createRoot } from 'react-dom/client'
import { AgentsClient, type Agent } from '@embabel/appliance-kit'
import { AgentsSurface, type AgentsServices } from '@embabel/appliance-kit/react/features'
import { restoreTheme } from './theme'
import { EMPTY_SETTINGS } from './studio-deps'
import { ipcTransport } from './kit-transport'
import type { Settings } from './types'

let settings: Settings = EMPTY_SETTINGS

const client = new AgentsClient(ipcTransport(() => settings))

const services: AgentsServices = {
  listAgents: () => client.list(),
  setStage: (name, stage, routine) => client.setStage(name, stage, routine),
  sign: (name) => client.sign(name),
  versions: (name) => client.versions(name),
}

/*
 * A native question, not an in-page dialog. It is the one moment this window asks for a
 * signature, and the platform's own sheet is the form people already read as "this matters".
 */
const confirmSign = (agent: Agent) => Promise.resolve(window.confirm(
  `Sign ${agent.name} version ${agent.version + 1}?\n\nThe signed version is what runs from now on. Later edits wait for the next signature.`,
))

/*
 * Opened at an agent: once from the query the window was opened with, and again for each request
 * while it is open. A new request remounts the surface, since `initialAgent` is where it starts.
 */
function AgentsWindow() {
  const [start, setStart] = React.useState(() => ({ agent: new URLSearchParams(location.search).get('open'), n: 0 }))
  React.useEffect(() => window.me.onAgentOpenRequest((agent) => setStart((s) => ({ agent, n: s.n + 1 }))), [])
  return (
    <AgentsSurface
      key={start.n}
      services={services}
      initialAgent={start.agent ?? undefined}
      host={{ confirmSign, editRoutine: (name) => void window.me.openHandlerStudio(name) }}
    />
  )
}

async function init() {
  settings = await window.me.loadSettings()
  void restoreTheme(settings)
  createRoot(document.getElementById('agents')).render(<AgentsWindow />)
}

void init()
