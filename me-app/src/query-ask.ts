// Query Studio — ASK and REFINE: English in, Cypher out, and the round-trip
// that revises what is already in the editor. Plain script, one global.

import { $ } from './dom'
import type { Settings } from './types'
import { EMPTY_SETTINGS, type StudioDeps } from './studio-deps'

export const queryAsk = (() => {
/* global EmbabelVc, EmbabelStudioKit */
const { setStatus } = EmbabelStudioKit
const { formatDuration } = EmbabelStudioKit
const validityEl = $('validity')
const verdictEl = $('verdict')
/* Filled by init(deps) before anything here runs — see StudioDeps. */
let settings: Settings = EMPTY_SETTINGS
let setEditorText: StudioDeps['setEditorText'] = () => {}
let scheduleValidation: () => void = () => {}
let editorText: (() => string) | undefined

// ---------------------------------------------------------------------------
// Ask — text-to-Cypher through the appliance's own generator. Generation only:
// the query lands HERE, validated and editable, and running it stays a
// deliberate second step — execution can be arbitrarily slow (a cold
// extract/aggregate materializes on first traversal), and the whole point of
// this surface is seeing the query before paying for it.
// ---------------------------------------------------------------------------

const askQuestion = $('ask-question')
const askStatus = $('ask-status')
const generateButton = $('generate')
const explainEl = $('explain')

/** Land a generated/refined result: editor, verdict, explain — one rule for both verbs. */
function landGenerated(result: any, verb: string) {
  // Marked as hand-edited: the composer's controls must not clobber a
  // generated query on the next dropdown twitch.
  setEditorText(result.cypher, { edited: true })
  verdictEl.innerHTML = ''
  const took = result.durationMs != null ? ` in ${formatDuration(result.durationMs)}` : ''
  if (result.valid === true) {
    // The server generated, PREFLIGHTED, and self-corrected before handing this
    // over — say so, with the price paid (attempts), and skip the re-check.
    const tries = result.attempts && result.attempts > 1 ? ` after ${result.attempts} attempts` : ''
    setStatus(askStatus, true, `${verb}${took}${tries} — review, then Run`)
    setStatus(validityEl, true, '✓ schema-valid')
  } else if (result.valid === false) {
    // The generator could not reach a valid query — honest, with the verdict.
    setStatus(askStatus, false, `${verb}, but ${result.violations.length} schema problem(s) remain — edit before running`)
    setStatus(validityEl, false, `${result.violations.length} schema problem(s)`)
    for (const violation of result.violations) {
      const line = document.createElement('div')
      line.className = 'violation'
      line.textContent = violation
      verdictEl.append(line)
    }
  } else {
    // Older appliance: no server-side verdict — validate here as before.
    setStatus(askStatus, true, `${verb}${took} — review, edit if you like, then Run`)
    scheduleValidation()
  }
  if (result.explain) {
    explainEl.textContent = result.explain
    explainEl.hidden = false
  }
}

async function generate() {
  const question = askQuestion.value.trim()
  if (!question) return
  generateButton.disabled = true
  explainEl.hidden = true
  setStatus(askStatus, null, 'The appliance is writing your query — an LLM call, give it a moment…')
  let result
  try {
    result = await window.me.vcGenerate(settings, question)
  } catch (e) {
    result = { ok: false, message: e instanceof Error ? e.message : String(e) }
  }
  generateButton.disabled = false
  if (!result.ok) {
    setStatus(askStatus, false, result.message)
    return
  }
  landGenerated(result, 'written')
}

generateButton.addEventListener('click', () => void generate())

// Refine — the round-trip half: the instruction says what to CHANGE, and the
// CURRENT query (hand edits included) travels with it and the schema, so what
// the instruction doesn't name survives the model pass.
const refineInstruction = $('refine-instruction')
const refineButton = $('refine')

async function refine() {
  const instruction = refineInstruction.value.trim()
  const cypher = (editorText?.() ?? '').trim()
  if (!instruction) return
  if (!cypher) return setStatus(askStatus, false, 'nothing to refine — the editor is empty; Ask writes first')
  refineButton.disabled = true
  explainEl.hidden = true
  setStatus(askStatus, null, 'The appliance is revising your query — an LLM call, give it a moment…')
  let result
  try {
    result = await window.me.vcRefine(settings, cypher, instruction)
  } catch (e) {
    result = { ok: false, message: e instanceof Error ? e.message : String(e) }
  }
  refineButton.disabled = false
  if (!result.ok) {
    setStatus(askStatus, false, result.message)
    return
  }
  landGenerated(result, 'revised')
}

refineButton.addEventListener('click', () => void refine())
refineInstruction.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') void refine()
})
askQuestion.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') void generate()
})

// Which model writes the Cypher. Writing a good graph query against a large
// schema is frontier-model work; the appliance's DEFAULT model is often a
// small local one chosen for privacy and price, so this picker exists — the
// per-world lensLlm override, live from the next generation, no restart.
const lensModelSelect = $('lens-model')

async function initLensModel() {
  const [models, lens] = await Promise.all([
    window.me.listModels(settings),
    window.me.vcLensModel(settings),
  ])
  lensModelSelect.innerHTML = ''
  const inherit = document.createElement('option')
  inherit.value = ''
  // NOT the appliance's general default model — text-to-Cypher has its own
  // deployment default (assistant.lens.llm), which the server doesn't expose
  // by name; naming the general default here would be a lie.
  inherit.textContent = 'appliance default'
  lensModelSelect.append(inherit)
  if (models.ok) {
    for (const m of models.models) {
      const option = document.createElement('option')
      option.value = m.name ?? m.model ?? String(m)
      option.textContent = `${option.value}${m.provider ? ` · ${m.provider}` : ''}`
      lensModelSelect.append(option)
    }
  }
  if (lens.ok && lens.model && [...lensModelSelect.options].some((o) => o.value === lens.model)) {
    lensModelSelect.value = lens.model
  } else if (lens.ok && lens.model) {
    // Configured to a model the provider list doesn't currently show — keep it
    // selectable rather than silently displaying "default".
    const option = document.createElement('option')
    option.value = lens.model
    option.textContent = lens.model
    lensModelSelect.append(option)
    lensModelSelect.value = lens.model
  }
}

lensModelSelect.addEventListener('change', async () => {
  const result = await window.me.vcSetLensModel(settings, lensModelSelect.value)
  setStatus(askStatus, result.ok, result.ok ? `${result.message} — live from the next generation` : result.message)
})

return {
  init(deps: StudioDeps) {
    settings = deps.settings
    setEditorText = deps.setEditorText
    scheduleValidation = deps.scheduleValidation
    return initLensModel()
  },
}
})()
