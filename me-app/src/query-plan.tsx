/*
 * THE PLAN PANEL in Query Studio: the kit's `PlanPanel`, the same one the Worlds console shows,
 * watching the editor.
 *
 * Nothing here plans, renders a chain or decides what an outcome means; the kit does, so the two
 * front ends cannot disagree about what the planner said. This file is the host: it hands the panel
 * the editor's text as it changes and the kit's clients, sent over the bridge like every other kit
 * request in this app.
 *
 * Typing never runs anything. The panel pauses before each dry run, and the dry-run route on the
 * appliance cannot execute; running is the panel's own button, a separate route, and nothing in
 * this file calls it.
 *
 * Save as goal view is offered only when the appliance says it stores goal views: its views listing
 * carries the `goal` field. An older appliance gets no button rather than a refusal after the click.
 */
import React from 'react'
import { createRoot } from 'react-dom/client'
import { KgClient, goalViewsSupported } from '@embabel/appliance-kit'
import { PlanPanel, type PlanServices } from '@embabel/appliance-kit/react/features'
import { ipcTransport } from './kit-transport'
import type { Settings } from './types'

export interface QueryPlan {
  /** The editor's text now. Cheap to call on every change: the panel waits for it to settle. */
  setGoal(goal: string): void
}

export function mountQueryPlan(container: HTMLElement, settings: () => Settings): QueryPlan {
  const kg = new KgClient(ipcTransport(settings))
  const root = createRoot(container)
  let goal = ''
  let services: PlanServices = {
    plan: (g, inputs) => kg.plan(g, inputs),
    runPlan: (g, inputs) => kg.runPlan(g, inputs),
  }
  const render = () => root.render(<PlanPanel services={services} goal={goal} />)

  /* The capability is read once, after settings are loaded; until it answers, there is no Save. */
  void kg.views().then((views) => {
    if (!views.ok || !goalViewsSupported(views.value)) return
    services = { ...services, saveGoalView: (spec) => kg.saveGoalView(spec) }
    render()
  })

  render()
  return {
    setGoal(next: string) {
      if (next === goal) return
      goal = next
      render()
    },
  }
}
