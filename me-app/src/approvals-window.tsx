/*
 * THE APPROVALS WINDOW: the kit's Approvals surface, the same one the Worlds console shows.
 *
 * What agents ask before they act, decided here: approving calls the request's verb as you, and
 * rejecting needs a reason. Nothing here draws a request; the cards, the evidence and the refusals
 * live in `@embabel/appliance-kit`. This file is the host: a transport that reaches the appliance,
 * and a way from a request to the agent that raised it.
 */
import React from 'react'
import { createRoot } from 'react-dom/client'
import { RequestsClient } from '@embabel/appliance-kit'
import { ApprovalsSurface, type ApprovalsServices } from '@embabel/appliance-kit/react/features'
import { restoreTheme } from './theme'
import { EMPTY_SETTINGS } from './studio-deps'
import { ipcTransport } from './kit-transport'
import { worldQueries } from './world-queries'
import type { Settings } from './types'

let settings: Settings = EMPTY_SETTINGS

const transport = ipcTransport(() => settings)
const requests = new RequestsClient(transport)
const { lists } = worldQueries(() => settings)

const services: ApprovalsServices = {
  listRequests: () => requests.list(),
  approve: (id) => requests.approve(id),
  reject: (id, reason) => requests.reject(id, reason),
}

/*
 * A request names the routine that raised it; the Agents window opens at the agent holding it. One
 * query for the one holder, rather than every agent's whole card to search through. No answer opens
 * the window at the routine's name, which is no worse than not knowing.
 */
async function openAgentHolding(routine: string) {
  const holder = await lists.agentHolding(routine)
  void window.me.openAgents((holder.ok ? holder.value : null) ?? routine)
}

async function init() {
  settings = await window.me.loadSettings()
  void restoreTheme(settings)
  createRoot(document.getElementById('approvals')).render(
    <ApprovalsSurface services={services} host={{ openAgent: (routine) => void openAgentHolding(routine) }} />,
  )
}

void init()
