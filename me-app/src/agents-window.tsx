/*
 * THE AGENTS WINDOW: the kit's Agents surface, the same one the Worlds console shows.
 *
 * Nothing here draws an agent. The roster, the stage ladder, the refusals and the signing all
 * live in `@embabel/appliance-kit`, so the two front ends cannot describe one agent two ways.
 * What this file adds is the host: a transport that reaches the appliance, a question before a
 * signature, and a way from a routine to the editor for its body.
 *
 * THE TRANSPORT IS THE KIT'S CLIENT OVER IPC. The page has no network of its own (the sandbox
 * and the CSP both see to that), so each request the kit's `AgentsClient` shapes crosses the
 * bridge as its `RequestSpec` and is sent by the kit's `HttpTransport` in the main process. The
 * client's own reading of the answer, including lifting a refusal's sentence out of a 409, runs
 * here unchanged.
 */
import React from 'react'
import { createRoot } from 'react-dom/client'
import { AgentsClient, type Agent, type Outcome, type RequestSpec, type Transport } from '@embabel/appliance-kit'
import { AgentsSurface, type AgentsServices } from '@embabel/appliance-kit/react/features'
import { restoreTheme } from './theme'
import { EMPTY_SETTINGS } from './studio-deps'
import type { Settings } from './types'

let settings: Settings = EMPTY_SETTINGS

const ipc: Transport = {
  send: <T,>(spec: RequestSpec) => window.me.agentsSend(settings, spec) as Promise<Outcome<T>>,
}
const client = new AgentsClient(ipc)

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

async function init() {
  settings = await window.me.loadSettings()
  void restoreTheme(settings)
  createRoot(document.getElementById('agents')).render(
    <AgentsSurface
      services={services}
      host={{ confirmSign, editRoutine: (name) => void window.me.openHandlerStudio(name) }}
    />,
  )
}

void init()
