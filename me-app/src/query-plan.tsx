/*
 * THE PLAN PANEL in Query Studio: the kit's `PlanPanel`, the same one the Worlds console shows,
 * watching the editor.
 *
 * Nothing here plans, renders a chain or decides what an outcome means; the kit does, so the two
 * front ends cannot disagree about what the planner said. This file hands the panel the editor's
 * text as it changes and the kit's client, sent over the bridge like every other kit request in
 * this app. The kit's QueryStudioSurface has no plan of its own, so this app keeps its panel beside
 * the surface, fed by the surface's `onCypherChange`.
 *
 * Typing never runs anything. The panel pauses before each dry run, and the dry-run route on the
 * appliance cannot execute; running is the panel's own button, a separate route.
 *
 * Save as goal view is offered only when the appliance says it stores goal views: its views listing
 * carries the `goal` field. An older appliance gets no button rather than a refusal after the click.
 */
import React from 'react'
import { type KgClient, goalViewsSupported } from '@embabel/appliance-kit'
import { PlanPanel, type PlanServices } from '@embabel/appliance-kit/react/features'

export function QueryPlan({ kg, goal }: { kg: KgClient; goal: string }) {
  const [services, setServices] = React.useState<PlanServices>(() => ({
    plan: (g, inputs) => kg.plan(g, inputs),
    runPlan: (g, inputs) => kg.runPlan(g, inputs),
  }))
  /* The capability is read once; until it answers, there is no Save. */
  React.useEffect(() => {
    void kg.views().then((views) => {
      if (!views.ok || !goalViewsSupported(views.value)) return
      setServices((s) => ({ ...s, saveGoalView: (spec) => kg.saveGoalView(spec) }))
    })
  }, [kg])
  return <PlanPanel services={services} goal={goal} />
}
