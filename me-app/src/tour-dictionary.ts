/*
 * WHAT THIS APP CAN BE ASKED TO DO — read off its own markup rather than restated beside it.
 *
 * A tour names `panel.documents`, `field.question`, `button.ask`; something has to say which of
 * those exist here. The obvious way is a hand-maintained list, and the obvious problem with a
 * hand-maintained list is that it is a second copy of the truth: a button gets renamed, the list
 * does not, and a tour is refused for a control that is sitting right there — or worse, accepted
 * and then not found.
 *
 * So the dictionary is DERIVED. `data-panel` already named the panels (it is how tab switching
 * works); `data-field` and `data-control` name the two other kinds, on the elements themselves. An
 * element that is not annotated is not scriptable, which is the right default: being reachable by
 * a tour is a decision somebody makes, not something every button gets for free.
 *
 * NAMES ARE UNIQUE ACROSS THE SURFACE, not per panel, because a tour says `set: field.question`
 * without repeating the panel it just opened. Duplicates would make a tour ambiguous, so they are
 * reported loudly here rather than resolved by guessing.
 */

import type { TourDictionary } from '@embabel/appliance-kit/tour'

/**
 * Named conditions a tour may `wait` for or `expect`.
 *
 * Not in the markup, because they are behaviour rather than layout: whether the appliance is
 * reachable is not a property of any element. Each one is answered by `states.ts`'s probe in the
 * host — this list and that map must agree, and the test in the kit's `fitness` will not catch a
 * disagreement, so they sit close together deliberately.
 */
export const TOUR_STATES = ['connection.ok', 'documents.answered'] as const

/** Kinds whose names come from the WORLD rather than the layout — checked when the step runs. */
const DYNAMIC_KINDS = ['view', 'verb', 'app']

/**
 * Read the dictionary out of the live document.
 *
 * Live rather than cached: panels are in the page from the start, but a control inside one may be
 * rendered after a fetch, and a dictionary captured at boot would be missing it.
 */
export function readDictionary(doc: Document = document): TourDictionary {
  const panels: TourDictionary['panels'] = {}
  const seen = new Map<string, string>()

  const claim = (kind: string, name: string, panel: string) => {
    const key = `${kind}.${name}`
    const owner = seen.get(key)
    if (owner && owner !== panel) {
      // Loud, because the consequence is a tour that does something other than what it reads as.
      console.warn(`[tours] ${key} is declared in both ${owner} and ${panel} — a tour naming it is ambiguous`)
      return false
    }
    seen.set(key, panel)
    return true
  }

  for (const element of doc.querySelectorAll<HTMLElement>('[data-panel]')) {
    const panel = element.dataset['panel']
    if (!panel) continue
    const fields: string[] = []
    const controls: string[] = []
    for (const field of element.querySelectorAll<HTMLElement>('[data-field]')) {
      const name = field.dataset['field']
      if (name && claim('field', name, panel)) fields.push(name)
    }
    for (const control of element.querySelectorAll<HTMLElement>('[data-control]')) {
      const name = control.dataset['control']
      if (name && claim('button', name, panel)) controls.push(name)
    }
    panels[panel] = { fields, controls }
  }

  return {
    surface: 'me',
    version: 1,
    panels,
    states: [...TOUR_STATES],
    dynamic: DYNAMIC_KINDS,
  }
}
