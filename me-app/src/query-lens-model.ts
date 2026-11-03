/*
 * Query Studio — WHICH MODEL WRITES THE CYPHER that the studio's Ask and Refine hand back.
 *
 * Writing a good graph query against a large schema is frontier-model work, and the appliance's
 * default model is often a small local one chosen for privacy and price. So this picker sets the
 * world's text-to-Cypher model (its `lensLlm` override), live from the next generation with no
 * restart. The kit's surface has no such control; this app's studio has always had one.
 */

import { $ } from './dom'
import type { Settings } from './types'
import { setStatus } from '@embabel/appliance-kit/studio-kit'

export async function initLensModel(settings: Settings): Promise<void> {
  const select = $('lens-model')
  const status = $('lens-status')
  const [models, lens] = await Promise.all([
    window.me.listModels(settings),
    window.me.vcLensModel(settings),
  ])
  select.innerHTML = ''
  const inherit = document.createElement('option')
  inherit.value = ''
  // NOT the appliance's general default model — text-to-Cypher has its own deployment default,
  // which the server does not expose by name; naming the general default here would be a lie.
  inherit.textContent = 'appliance default'
  select.append(inherit)
  if (models.ok) {
    for (const m of models.models) {
      const option = document.createElement('option')
      option.value = m.name ?? m.model ?? String(m)
      option.textContent = `${option.value}${m.provider ? ` · ${m.provider}` : ''}`
      select.append(option)
    }
  }
  if (lens.ok && lens.model) {
    // Configured to a model the provider list does not show now: keep it selectable rather than
    // silently displaying "default".
    if (![...select.options].some((o) => o.value === lens.model)) {
      const option = document.createElement('option')
      option.value = lens.model
      option.textContent = lens.model
      select.append(option)
    }
    select.value = lens.model
  }
  select.addEventListener('change', async () => {
    const result = await window.me.vcSetLensModel(settings, select.value)
    setStatus(status, result.ok, result.ok ? `${result.message} — live from the next generation` : result.message)
  })
}
