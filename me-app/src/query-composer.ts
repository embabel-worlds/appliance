// Query Studio — the COMPOSER: targets, modes, the narrowing controls, the
// tag universe, and the Tips that follow the chosen target. Plain script,
// one global; compose() writes through the orchestrator's editor.

import { $ } from './dom'
import type { Settings } from './types'
import type { StudioDeps } from './studio-deps'

export const queryComposer = (() => {
/* global EmbabelVc, EmbabelStudioKit */
const { setStatus } = EmbabelStudioKit
const { TARGETS, TIPS, compose: composeCypher } = EmbabelVc
const els = {
  targets: $('targets'), anchorRow: $('anchor-row'), anchor: $('anchor'),
  seedLabel: $('seed-label'), seed: $('seed'), mode: $('mode'), cost: $('cost'),
  intentRow: $('intent-row'), intent: $('intent'), tagRow: $('tag-row'), tag: $('tag'),
  dateRow: $('date-row'), dateField: $('date-field'), dateFrom: $('date-from'), dateTo: $('date-to'),
  minScore: $('min-score'), limit: $('limit'), steerPanel: $('steer-panel'),
  aiHint: $('ai-hint'), aiModel: $('ai-model'), aiTemperature: $('ai-temperature'),
  aiConfidence: $('ai-confidence'), aiFresh: $('ai-fresh'), compose: $('compose'),
  seekPanel: $('seek-panel'), narrowPanel: $('narrow-panel'),
}
let setEditorText: StudioDeps['setEditorText'] = () => {}
let isHandEdited: () => boolean = () => false
let scheduleValidation: (() => void) | undefined

// ---------------------------------------------------------------------------
// The targets and their modes. Costs are the spec's own guidance, shown at the
// moment of choice: agentic is several LLM calls PER ANCHOR, never a default.
// ---------------------------------------------------------------------------

const MODES: Record<string, { label: string; cost: string; dear?: boolean }> = {
  about: { label: 'about — semantic (vector)', cost: 'one embedding pass · cheap', dear: false },
  mentions: { label: 'mentions — exact tokens (keyword)', cost: 'fulltext, deterministic · cheap', dear: false },
  judged: { label: 'judged — agentic, against a brief', cost: 'several LLM calls per anchor · expensive', dear: true },
  semantic: { label: 'semantic — vector over thread summaries', cost: 'one embedding pass · cheap', dear: false },
}

let target = 'documents'

// ---------------------------------------------------------------------------
// Composition — controls → Cypher. The editor is the source of truth once the
// user touches it: composing again is an explicit act (the Compose button or a
// control change while the editor is still machine-written).
// ---------------------------------------------------------------------------
/**
 * The controls, as the spec @embabel/vc composes from. This function is all that
 * is left of the composer here: the Cypher-shaping — three relevance modes, the
 * {ai:{…}} steering, the tag/date/score filters, the commented per-row judgment
 * lines — lives in the package, so the Worlds console composes the same queries
 * from the same understanding instead of a second reading of the spec.
 */
function composeSpec() {
  return {
    target,
    mode: els.mode.value,
    seed: els.seed.value,
    intent: els.intent.value,
    anchor: els.anchor.value,
    tag: els.tag.value,
    dateField: els.dateField.value,
    dateFrom: els.dateFrom.value,
    dateTo: els.dateTo.value,
    minScore: els.minScore.value,
    limit: els.limit.value,
    ai: {
      hint: els.aiHint.value,
      model: els.aiModel.value,
      temperature: els.aiTemperature.value,
      confidence: els.aiConfidence.value,
      fresh: els.aiFresh.checked,
    },
  }
}

function compose() {
  setEditorText(composeCypher(composeSpec()))
  scheduleValidation?.()
}

// ---------------------------------------------------------------------------
// Control wiring.
// ---------------------------------------------------------------------------

function renderTargets() {
  els.targets.innerHTML = ''
  for (const [key, spec] of Object.entries(TARGETS)) {
    const button = document.createElement('button')
    button.className = 'target' + (key === target ? ' is-on' : '')
    const name = document.createElement('span')
    name.className = 't-name'
    name.textContent = String((spec as any).name)
    const what = document.createElement('span')
    what.className = 't-what'
    what.textContent = String((spec as any).what)
    button.append(name, what)
    button.addEventListener('click', () => {
      target = key
      renderTargets()
      applyTarget()
      compose()
      // An open Tips panel follows the target — its teaching is per-target.
      if (!tipsPanel.hidden) renderTips()
    })
    els.targets.append(button)
  }
}

function applyTarget() {
  const spec = TARGETS[target]
  els.seekPanel.hidden = target === 'canvas'
  els.narrowPanel.hidden = target === 'canvas'
  els.seedLabel.textContent = spec.seedLabel ?? 'Search for'
  els.mode.innerHTML = ''
  for (const key of spec.modes ?? []) {
    const option = document.createElement('option')
    option.value = key
    option.textContent = MODES[key].label
    els.mode.append(option)
  }
  els.anchorRow.hidden = !spec.anchors
  if (spec.anchors) {
    els.anchor.innerHTML = ''
    for (const [key, anchor] of Object.entries(spec.anchors)) {
      const option = document.createElement('option')
      option.value = key
      option.textContent = (anchor as { label: string }).label
      els.anchor.append(option)
    }
  }
  els.tagRow.hidden = !spec.tags
  els.dateRow.hidden = !spec.dates
  applyMode()
}

function applyMode() {
  const mode = els.mode.value
  const judged = mode === 'judged'
  els.intentRow.hidden = !judged
  els.steerPanel.hidden = !judged
  if (mode && MODES[mode]) {
    els.cost.textContent = MODES[mode].cost
    els.cost.className = 'cost ' + (MODES[mode].dear ? 'dear' : 'cheap')
  } else {
    els.cost.textContent = ''
  }
  if (TARGETS.threads.anchors && target === 'threads') {
    els.seed.placeholder = TARGETS.threads.anchors[els.anchor.value]?.placeholder ?? ''
  } else {
    els.seed.placeholder = target === 'files' ? 'trip logistics' : 'renewal terms'
  }
}

/** A control change recomposes only while the editor is still machine-written. */
function onControlChange() {
  applyMode()
  if (!isHandEdited()) compose()
}

for (const el of [
  els.anchor, els.seed, els.mode, els.intent, els.tag, els.dateField, els.dateFrom, els.dateTo,
  els.minScore, els.limit, els.aiHint, els.aiModel, els.aiTemperature, els.aiConfidence, els.aiFresh,
]) {
  el.addEventListener('input', onControlChange)
  el.addEventListener('change', onControlChange)
}
els.compose.addEventListener('click', () => {
  compose()
})

// ---------------------------------------------------------------------------
// Tips — the teaching that used to trail every composed query as comment
// lines. Same knowledge, now on demand: a tip in the query text made a
// three-line query read as complex, and vanished on the next compose anyway.
// ---------------------------------------------------------------------------

const tipsButton = $('tips')
const tipsPanel = $('tips-panel')

function renderTips() {
  tipsPanel.innerHTML = ''
  for (const tip of TIPS[target] ?? []) {
    const line = document.createElement('div')
    line.className = 'tip'
    line.textContent = tip
    tipsPanel.append(line)
  }
}

tipsButton.addEventListener('click', () => {
  tipsPanel.hidden = !tipsPanel.hidden
  if (!tipsPanel.hidden) renderTips()
})

// ---------------------------------------------------------------------------
// The tag universe — same query the main window's dropdown uses.
// ---------------------------------------------------------------------------

let settings: Settings = null

async function loadTagUniverse() {
  let result
  try {
    result = await window.me.vcExecute(
      settings,
      'MATCH (d:Document) WHERE d.tags IS NOT NULL UNWIND d.tags AS tag ' +
        'RETURN tag, count(*) AS docs ORDER BY toLower(tag)',
    )
  } catch {
    return
  }
  if (!result.ok || result.error) return
  const previous = els.tag.value
  els.tag.innerHTML = ''
  const none = document.createElement('option')
  none.value = ''
  none.textContent = 'all documents'
  els.tag.append(none)
  for (const row of result.rows) {
    const tag = String(row.tag ?? '')
    if (!tag) continue
    const option = document.createElement('option')
    option.value = tag
    option.textContent = `${tag} · ${Number(row.docs) || 0} doc(s)`
    els.tag.append(option)
  }
  if ([...els.tag.options].some((o) => o.value === previous)) els.tag.value = previous
}

return {
  init(deps: StudioDeps) {
    settings = deps.settings
    setEditorText = deps.setEditorText
    isHandEdited = deps.isHandEdited
    renderTargets()
    applyTarget()
    compose()
    void loadTagUniverse()
  },
}
})()
