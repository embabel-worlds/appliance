// Handler Studio — the sibling of the Query Studio, for TypeScript event
// handlers: smallish programs handed the triggering `signal` (or a cron tick)
// that read the graph and take effects through the typed `gateway.*` surface.
//
// The honesty guarantees are the Query Studio's, transplanted:
//
//  - Completion reads the appliance's OWN generated surface (interfaces.ts,
//    parsed by @embabel/code-surface) — the same file that types code-mode —
//    so what the editor offers and what compiles cannot drift apart.
//  - Validity is the engine's own `tsc` gate, debounced as you type; save runs
//    the same gate as the hard stop, so "valid here" and "saved there" agree.
//  - The safe verb is primary: dry-run is observe-only ON THE APPLIANCE,
//    against a real recent signal it names in the result. Enabling and
//    scheduling — the acts that let a handler act — are separate and marked.
//  - Cypher inside kg calls completes through @embabel/vc, the same package
//    the Query Studio next door and the Worlds console compose from.

/* global EmbabelCodeSurface, EmbabelVc, CodeMirror */

import type { Settings } from './types'
import { $ } from './dom'
import { restoreTheme } from './theme'
import './graph'
import { EMPTY_SETTINGS, type StudioDeps } from './studio-deps'
import type { Control } from './dom'

const { parseSurface, membersOf, gatewayPathAt } = EmbabelCodeSurface
/* Shared editor behavior — @embabel/studio-kit, semantics injected. */
/* global EmbabelStudioKit */
const { formatDuration, copyWithNod, cypherFragmentCompletions } = EmbabelStudioKit

/** @param {string} id */
const els = {
  handlers: $('handlers'),
  handlersStatus: $('handlers-status'),
  signalType: $('signal-type'),
  sample: $('sample'),
  surface: $('surface'),
  surfaceFilter: $('surface-filter'),
  askRequest: $('ask-request'),
  askStatus: $('ask-status'),
  generate: $('generate'),
  refineRequest: $('refine-request'),
  refine: $('refine'),
  dryRun: $('dry-run'),
  saveToggle: $('save-toggle'),
  copy: $('copy'),
  validity: $('validity'),
  runStatus: $('run-status'),
  verdict: $('verdict'),
  saveForm: $('save-form'),
  saveName: $('save-name'),
  saveSignal: $('save-signal'),
  saveSchedule: $('save-schedule'),
  scheduleStatus: $('schedule-status'),
  saveAutonomous: $('save-autonomous'),
  saveConfirm: $('save-confirm'),
  saveCancel: $('save-cancel'),
  saveStatus: $('save-status'),
  signalTypesData: $('signal-types'),
  ranAgainst: $('ran-against'),
  runOutput: $('run-output'),
  runEmpty: $('run-empty'),
}

/** @param {HTMLElement} el @param {boolean | null} ok @param {string} message */
function setStatus(el: HTMLElement, ok: boolean | null, message: string) {
  el.textContent = message
  el.className = ok === null ? 'status' : ok ? 'status ok' : 'status error'
}

// ---------------------------------------------------------------------------
// State the completions read: the parsed gateway surface, the KG schema (for
// Cypher inside kg calls), and the trigger sample (for `signal.` keys).
// ---------------------------------------------------------------------------

let settings: Settings = EMPTY_SETTINGS
let surface: ReturnType<typeof parseSurface> = null
let schema: {labels: Array<any>, relationships: Array<any>} | null = null
let sample: Record<string, any> | null = null

// ---------------------------------------------------------------------------
// The editor.
// ---------------------------------------------------------------------------

const cm = CodeMirror.fromTextArea($('editor'), {
  mode: 'text/typescript',
  lineNumbers: true,
  viewportMargin: Infinity,
  extraKeys: {
    'Cmd-Enter': (): void => void dryRun(),
    'Ctrl-Enter': (): void => void dryRun(),
    'Ctrl-Space': 'autocomplete',
  },
})

const editorText = () => cm.getValue()
function setEditorText(text: string) {
  cm.setValue(text)
  scheduleValidation()
}

cm.on('change', () => {
  // A verdict — and its painted lines — is stale the moment the text changes.
  clearErrorLines()
  scheduleValidation()
})

// ---------------------------------------------------------------------------
// Failure lines. tsc violations arrive in the SOURCE's own coordinates (the
// server rebases away its wrapper), so `handler.ts(3,…)` is editor line 3 —
// painted in place, and each locatable violation clicks through to its line.
// ---------------------------------------------------------------------------

/** @type {number[]} zero-based editor lines currently painted as failures */
let errorLines: number[] = []

function clearErrorLines() {
  for (const line of errorLines) cm.removeLineClass(line, 'background', 'error-line')
  errorLines = []
}

/** The (line, column) a violation names, zero-based for CodeMirror — or null. */
function violationPosition(violation: any) {
  const m = violation.match(/handler\.ts\((\d+),(\d+)\)/)
  if (!m) return null
  const line = Number(m[1]) - 1
  if (line < 0 || line >= cm.lineCount()) return null
  return { line, ch: Math.max(0, Number(m[2]) - 1) }
}

function markErrorLine(position: { line: number } | null) {
  cm.addLineClass(position.line, 'background', 'error-line')
  errorLines.push(position.line)
}

// Completion opens as you type into the things it can complete: a `gateway.`
// or `signal.` chain, or the characters that begin a completable Cypher thing
// inside a kg call's string.
cm.on('inputRead', (_cm: any, change: any) => {
  if (change.origin !== '+input') return
  const ch = change.text[change.text.length - 1]
  if (!/[.\w:['{]/.test(ch)) return
  const cursor = cm.getCursor()
  const before = cm.getLine(cursor.line).slice(0, cursor.ch)
  if (/\b(gateway|signal)\.[\w.]*$/.test(before) || cypherContext(before)) {
    cm.showHint({ completeSingle: false })
  }
})

const KEYWORDS = [
  'await', 'const', 'let', 'function', 'return', 'if', 'else', 'for', 'of', 'try', 'catch',
  'JSON.stringify(', 'console.log(', 'gateway', 'signal', 'trigger', 'now', 'dryRun',
]

/**
 * Cypher inside a kg call: when the cursor is inside an unterminated string on
 * a line that touches `kg.`, the string's content so far is virtual Cypher and
 * completes as such. A heuristic, honestly held: it reads one line, so a query
 * built across lines completes only where the schema-bearing parts sit — and
 * the server's compile/preflight remain the arbiters either way.
 * @param {string} before
 */
function cypherContext(before: string) {
  if (!/\bkg\s*\./.test(before)) return null
  const m = before.match(/(['"`])((?:(?!\1).)*)$/)
  return m ? { cypher: m[2] ?? '' } : null
}

/** The custom hint: what fits HERE — surface members, signal keys, embedded Cypher. */
CodeMirror.registerHelper('hint', 'javascript', (editor: any) => {
  const cursor = editor.getCursor()
  const line = editor.getLine(cursor.line)
  const before = line.slice(0, cursor.ch)

  // Every list alphabetical, same rule as the Query Studio.
  const found = (list: any[], from: any) => ({
    list: [...list].sort((a, b) => (a.text ?? a).localeCompare(b.text ?? b)),
    from: CodeMirror.Pos(cursor.line, from),
    to: CodeMirror.Pos(cursor.line, cursor.ch),
  })

  // gateway.… → the typed surface. Absence is meaningful: the surface is
  // generated, complete and per-user, so nothing falls back to a word list.
  const chain = gatewayPathAt(before)
  if (chain) {
    const members = membersOf(surface, chain.path)
      .filter((m: any) => m.name.toLowerCase().startsWith(chain.stem.toLowerCase()))
      .map((m: any) => ({ text: m.name, displayText: m.kind === 'namespace' ? `${m.name}.` : `${m.name}()` }))
    return found(members, cursor.ch - chain.stem.length)
  }

  // signal.… → the keys of the trigger sample actually selected. No sample, no
  // guesses — the panel next door shows exactly what the code will see.
  let m
  if ((m = before.match(/\bsignal\.(\w*)$/))) {
    const stem = m[1]
    const keys = sample ? Object.keys(sample) : []
    return found(keys.filter((k) => k.toLowerCase().startsWith(stem.toLowerCase())), cursor.ch - stem.length)
  }

  // Cypher inside a kg call — @embabel/vc, the same semantics as next door.
  const embedded = cypherContext(before)
  if (embedded) {
    const c = cypherCompletions(embedded.cypher)
    if (c) return found(c.list, cursor.ch - c.stemLength)
  }

  // Bare word → the ambient vocabulary of a handler.
  if ((m = before.match(/(\w+)$/))) {
    const stem = m[1]
    return found(KEYWORDS.filter((w) => w.toLowerCase().startsWith(stem.toLowerCase())), cursor.ch - stem.length)
  }
  return null
})

/** Cypher inside a kg call completes through the SAME kit path as the Query
 * Studio next door — the fragment doubles as its own alias source. */
function cypherCompletions(cypher: string) {
  return cypherFragmentCompletions(EmbabelVc, schema, cypher, cypher)
}

// ---------------------------------------------------------------------------
// Validation — the appliance's tsc gate, debounced. A compile spins the
// sandbox, so the debounce is generous and an unchanged text never re-asks.
// ---------------------------------------------------------------------------

let validateTimer: ReturnType<typeof setTimeout> | null = null
let validateSupported = true
let lastValidated: string | null = null
let lastVerdict: { valid: boolean; violations?: unknown[] } | null = null

function scheduleValidation() {
  if (!validateSupported) return
  if (validateTimer) clearTimeout(validateTimer)
  setStatus(els.validity, null, '…')
  validateTimer = setTimeout(() => void validateNow(), 1500)
}

async function validateNow() {
  const source = editorText().trim()
  if (!source) {
    els.validity.textContent = ''
    els.verdict.innerHTML = ''
    return
  }
  if (source === lastValidated) {
    // An edit that undid itself — the last verdict still holds; a compile
    // spins the sandbox, so it is not re-bought for the same text.
    if (lastVerdict) renderVerdict(lastVerdict.valid, lastVerdict.violations)
    return
  }
  const result = await window.me.handlerValidate(settings, source)
  if (!result.ok) {
    validateSupported = false
    els.validity.textContent = ''
    return
  }
  lastValidated = source
  lastVerdict = { valid: result.valid, violations: result.violations }
  renderVerdict(result.valid, result.violations)
}

function renderVerdict(valid: boolean, violations: any[]) {
  els.verdict.innerHTML = ''
  clearErrorLines()
  if (valid) {
    setStatus(els.validity, true, '✓ type-checks')
    return
  }
  setStatus(els.validity, false, `${violations.length} type problem(s)`)
  for (const violation of violations) {
    const row = document.createElement('div')
    row.className = 'violation'
    row.textContent = violation
    const position = violationPosition(violation)
    if (position) {
      markErrorLine(position)
      row.classList.add('locatable')
      row.title = 'jump to the line'
      row.addEventListener('click', () => {
        cm.setCursor(position)
        cm.focus()
      })
    }
    els.verdict.append(row)
  }
}

// ---------------------------------------------------------------------------
// Ask — English → handler through the appliance's generator. Generation only:
// the code lands HERE with its verdict, and running it stays a deliberate act.
// ---------------------------------------------------------------------------

/**
 * One path for both verbs: with [current], [english] is a CHANGE to the source
 * in the editor — the round-trip Refine drives — and everything the
 * instruction doesn't name survives the model pass.
 */
async function generate(english: string, current: string, button: Control, verb: string) {
  if (!english) return
  if (current !== null && !current) {
    return setStatus(els.askStatus, false, 'nothing to refine — the editor is empty; Ask writes first')
  }
  button.disabled = true
  setStatus(els.askStatus, null, `The appliance is ${verb === 'written' ? 'writing' : 'revising'} your handler — an LLM call plus a compile, give it a moment…`)
  let result
  try {
    result = await window.me.handlerGenerate(settings, english, current ?? undefined)
  } catch (e) {
    result = { ok: false, message: e instanceof Error ? e.message : String(e) }
  }
  button.disabled = false
  if (!result.ok) {
    setStatus(els.askStatus, false, result.message)
    return
  }
  cm.setValue(result.source)
  lastValidated = result.source.trim()
  lastVerdict = result.valid === null ? null : { valid: result.valid, violations: result.violations }
  if (validateTimer) clearTimeout(validateTimer)
  const took = result.durationMs != null ? ` in ${formatDuration(result.durationMs)}` : ''
  const tries = result.attempts && result.attempts > 1 ? ` after ${result.attempts} attempts` : ''
  if (result.valid === true) {
    // Server-generated, compiled, and self-corrected before handing over.
    setStatus(els.askStatus, true, `${verb}${took}${tries} — review, then dry-run`)
    renderVerdict(true, [])
  } else if (result.valid === false) {
    setStatus(els.askStatus, false, `${verb}${took}, but type problems remain — edit before running`)
    renderVerdict(false, result.violations)
  } else {
    // Older appliance: no verdict came back — check here as usual.
    setStatus(els.askStatus, true, `${verb}${took} — review, then dry-run`)
    lastValidated = null
    scheduleValidation()
  }
}

els.generate.addEventListener('click', () => void generate(els.askRequest.value.trim(), null, els.generate, 'written'))
els.askRequest.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') void generate(els.askRequest.value.trim(), null, els.generate, 'written')
})
els.refine.addEventListener('click', () => void generate(els.refineRequest.value.trim(), editorText().trim(), els.refine, 'revised'))
els.refineRequest.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') void generate(els.refineRequest.value.trim(), editorText().trim(), els.refine, 'revised')
})

// ---------------------------------------------------------------------------
// Dry-run — the primary verb. Observe-only on the appliance; the result names
// what it ran against so "it worked" means something.
// ---------------------------------------------------------------------------

async function dryRun() {
  const source = editorText().trim()
  if (!source) return
  els.dryRun.disabled = true
  setStatus(els.runStatus, null, 'Dry-running on the appliance — compile plus execution, give it a moment…')
  let result
  try {
    // The blank choice means "as a scheduled run". The server has no explicit
    // cron-tick flag — it falls back to one when no signal matches — so ask
    // for the CronTick type, which no stored signal carries, to get there
    // deliberately rather than by accident of an empty graph.
    result = await window.me.handlerDryRun(settings, source, els.signalType.value || 'CronTick')
  } catch (e) {
    result = { ok: false, message: e instanceof Error ? e.message : String(e) }
  }
  els.dryRun.disabled = false
  if (!result.ok) return setStatus(els.runStatus, false, result.message)
  els.runEmpty.hidden = true
  els.ranAgainst.hidden = false
  const against = result.ranAgainst
  els.ranAgainst.textContent = against?.signalId && against.signalId !== 'cron'
    ? `ran against ${against.signalType} · ${against.signalId}`
    : 'ran against a cron tick — no matching signal on record'
  els.runOutput.hidden = false
  els.runOutput.className = result.ran ? '' : 'error'
  els.runOutput.textContent = result.ran
    ? (result.stdout || '(no output)')
    : `${result.error ?? 'failed'}${result.stdout ? `\n\n${result.stdout}` : ''}`
  setStatus(els.runStatus, result.ran, result.ran ? 'dry-run ok' : 'dry-run failed')
}

els.dryRun.addEventListener('click', () => void dryRun())
els.copy.addEventListener('click', () => void copyWithNod(els.copy, 'Copy', editorText()))

// ---------------------------------------------------------------------------
// The handlers list — yours, and realm-shipped ones to adopt. Open → edit →
// save round-trips through the appliance; nothing here edits world files.
// ---------------------------------------------------------------------------

async function loadHandlers() {
  const result = await window.me.handlersList(settings)
  els.handlers.innerHTML = ''
  if (!result.ok) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = result.message
    els.handlers.append(p)
    return
  }
  if (!result.yours.length && !result.available.length) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = 'No handlers yet — write one and Save, or Ask for one in English.'
    els.handlers.append(p)
    return
  }
  if (result.yours.length) els.handlers.append(renderGroup('yours', result.yours, true))
  if (result.available.length) els.handlers.append(renderGroup('available to adopt', result.available, false))
}

function renderGroup(title: string, handlers: any[], yours: boolean) {
  const group = document.createElement('details')
  group.className = 'handler-group'
  group.open = yours
  const summary = document.createElement('summary')
  const label = document.createElement('span')
  label.textContent = title
  const count = document.createElement('span')
  count.className = 'gcount'
  count.textContent = String(handlers.length)
  summary.append(label, count)
  group.append(summary)
  for (const handler of handlers) group.append(renderHandler(handler, yours))
  return group
}

function renderHandler(handler: any, yours: boolean) {
  const row = document.createElement('div')
  row.className = 'handler-row'
  const name = document.createElement('div')
  name.className = 'hname'
  name.textContent = handler.name
  row.append(name)

  const meta = document.createElement('div')
  meta.className = 'hmeta'
  const parts = []
  if (handler.signalType && handler.signalType !== '*') parts.push(`on ${handler.signalType}`)
  if (handler.schedule) parts.push(`cron ${handler.schedule}`)
  if (handler.autonomous) parts.push('autonomous')
  meta.textContent = parts.join(' · ') || 'on any signal'
  if (yours) {
    const state = document.createElement('span')
    state.className = handler.active ? 'on' : ''
    state.textContent = `${parts.length ? ' · ' : ''}${handler.active ? 'live' : 'off'}`
    meta.append(state)
  }
  row.append(meta)

  const actions = document.createElement('div')
  actions.className = 'hactions'
  const openButton = document.createElement('button')
  openButton.textContent = 'Open'
  openButton.addEventListener('click', () => void openHandler(handler.name, rowStatus))
  actions.append(openButton)

  // Enable/adopt is the act that lets code act on your behalf — marked so.
  const toggle = document.createElement('button')
  toggle.className = handler.active ? '' : 'arm'
  toggle.textContent = yours ? (handler.active ? 'Disable' : 'Enable') : 'Adopt'
  toggle.addEventListener('click', async () => {
    const result = await window.me.handlerSetEnabled(settings, handler.name, !handler.active)
    setStatus(els.handlersStatus, result.ok, result.ok ? '' : result.message)
    if (result.ok) void loadHandlers()
  })
  actions.append(toggle)

  if (yours) {
    const deleteButton = document.createElement('button')
    deleteButton.textContent = 'Delete'
    deleteButton.addEventListener('click', async () => {
      const result = await window.me.handlerDelete(settings, handler.name)
      setStatus(els.handlersStatus, result.ok, result.message)
      if (result.ok) void loadHandlers()
    })
    actions.append(deleteButton)
  }
  const rowStatus = document.createElement('span')
  rowStatus.className = 'status'
  actions.append(rowStatus)
  row.append(actions)
  return row
}

async function openHandler(name: string, status: HTMLElement) {
  const result = await window.me.handlerOpen(settings, name)
  if (!result.ok) return setStatus(status, false, result.message)
  setEditorText(result.source)
  els.saveName.value = result.name
  els.saveSignal.value = result.signalType ?? '*'
  // The stored schedule is cron and shows as such — it round-trips through the
  // cron passthrough on save; a reworded English schedule recompiles.
  els.saveSchedule.value = result.schedule ?? ''
  els.scheduleStatus.textContent = ''
  els.saveAutonomous.checked = result.autonomous
  els.saveForm.hidden = false
  setStatus(status, true, 'in the editor')
}

// ---------------------------------------------------------------------------
// Save / schedule — the going-live half, deliberately separate from dry-run.
// ---------------------------------------------------------------------------

els.saveToggle.addEventListener('click', () => {
  els.saveForm.hidden = !els.saveForm.hidden
  if (!els.saveForm.hidden) els.saveName.focus()
})
els.saveCancel.addEventListener('click', () => {
  els.saveForm.hidden = true
})

/*
 * The schedule is written in ENGLISH and compiled to cron on the appliance —
 * the user never writes cron. A text that already IS a cron expression passes
 * through untouched (an opened handler's stored schedule round-trips, and a
 * power user can still paste one): six tokens of cron vocabulary — digits,
 * `*,/-?LW#` and uppercase day/month names — which no English phrase matches,
 * every word being lowercase.
 */
const CRON_SHAPE = /^(?:[\dA-Z*,/\-?LW#]+\s+){5}[\dA-Z*,/\-?LW#]+$/
let compiledSchedule: { from: string | null; cron: string | null } = { from: null, cron: null }

/** English (or cron, or blank) → cron | null (none) | undefined (failed — status shown). */
async function resolveSchedule(text: string) {
  if (!text) {
    els.scheduleStatus.textContent = ''
    return null
  }
  if (CRON_SHAPE.test(text)) return text
  if (compiledSchedule.from === text) return compiledSchedule.cron
  setStatus(els.scheduleStatus, null, 'compiling — an LLM call…')
  let result
  try {
    result = await window.me.compileSchedule(settings, text)
  } catch (e) {
    result = { ok: false, message: e instanceof Error ? e.message : String(e) }
  }
  if (!result.ok || result.error) {
    setStatus(els.scheduleStatus, false, result.error ?? result.message)
    return undefined
  }
  compiledSchedule = { from: text, cron: result.cron }
  setStatus(els.scheduleStatus, true, `→ ${result.cron}`)
  return result.cron
}

// Compile as soon as the field settles, so the cron is on screen before Save.
els.saveSchedule.addEventListener('change', () => void resolveSchedule(els.saveSchedule.value.trim()))

els.saveConfirm.addEventListener('click', async () => {
  const name = els.saveName.value.trim()
  if (!name) return setStatus(els.saveStatus, false, 'a handler needs a name')
  const schedule = await resolveSchedule(els.saveSchedule.value.trim())
  if (schedule === undefined) return setStatus(els.saveStatus, false, 'fix the schedule first')
  setStatus(els.saveStatus, null, 'saving — the appliance compiles it first…')
  const result = await window.me.handlerSave(settings, {
    name,
    source: editorText(),
    signalType: els.saveSignal.value.trim() || '*',
    schedule,
    autonomous: els.saveAutonomous.checked,
  })
  setStatus(els.saveStatus, result.ok, result.message)
  if (result.ok) void loadHandlers()
})

// ---------------------------------------------------------------------------
// The trigger: observed signal types from the graph, and the latest sample of
// the chosen one — which is exactly what a dry-run will bind as `signal`.
// ---------------------------------------------------------------------------

async function loadSignalTypes() {
  let result
  try {
    result = await window.me.vcExecute(
      settings,
      "MATCH (s:Signal) UNWIND labels(s) AS type WITH type, count(*) AS n " +
        "WHERE type <> 'Signal' RETURN type, n ORDER BY type",
    )
  } catch {
    return
  }
  if (!result.ok || result.error) return
  for (const row of result.rows) {
    const type = String(row.type ?? '')
    if (!type) continue
    const option = document.createElement('option')
    option.value = type
    option.textContent = `${type} · ${Number(row.n) || 0} seen`
    els.signalType.append(option)
    const dataOption = document.createElement('option')
    dataOption.value = type
    els.signalTypesData.append(dataOption)
  }
}

async function loadSample() {
  const type = els.signalType.value
  sample = null
  els.sample.hidden = true
  if (!type) return
  const { esc } = EmbabelVc
  let result
  try {
    result = await window.me.vcExecute(
      settings,
      `MATCH (s:Signal) WHERE '${esc(type)}' IN labels(s) AND s.occurredAt IS NOT NULL ` +
        'RETURN properties(s) AS props ORDER BY s.occurredAt DESC LIMIT 1',
    )
  } catch {
    return
  }
  const props = result.ok && !result.error ? result.rows?.[0]?.props : null
  if (props && typeof props === 'object') {
    sample = props
    els.sample.hidden = false
    els.sample.textContent = JSON.stringify(props, null, 2)
  }
}

els.signalType.addEventListener('change', () => void loadSample())

// ---------------------------------------------------------------------------
// The gateway browser — the schema panel's idiom, over the typed surface.
// ---------------------------------------------------------------------------

function renderSurface() {
  els.surface.innerHTML = ''
  if (!surface) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = 'Surface unavailable — an older appliance, or not reachable.'
    els.surface.append(p)
    return
  }
  const filter = els.surfaceFilter.value.trim().toLowerCase()
  const groups = [
    // The gateway's own verbs first — notify is the one handlers reach for.
    { name: 'gateway', methods: surface.methods },
    ...surface.namespaces,
  ].filter((g) => g.methods.length && (!filter || g.name.toLowerCase().includes(filter)))
  for (const group of groups) {
    const details = document.createElement('details')
    details.className = 'ns'
    const summary = document.createElement('summary')
    const name = document.createElement('span')
    name.textContent = group.name === 'gateway' ? 'gateway.*' : `gateway.${group.name}.*`
    const count = document.createElement('span')
    count.className = 'count'
    count.textContent = `${group.methods.length}`
    summary.append(name, count)
    details.append(summary)
    const methods = document.createElement('div')
    methods.className = 'methods'
    for (const method of group.methods) {
      const sig = document.createElement('div')
      sig.className = 'sig'
      sig.textContent = method.signature
      methods.append(sig)
      if (method.doc) {
        const doc = document.createElement('div')
        doc.className = 'doc'
        doc.textContent = method.doc
        methods.append(doc)
      }
    }
    details.append(methods)
    els.surface.append(details)
  }
  if (!groups.length) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = 'No namespace matches that filter.'
    els.surface.append(p)
  }
}

els.surfaceFilter.addEventListener('input', renderSurface)

async function loadSurface() {
  const result = await window.me.gatewaySurface(settings)
  surface = result.ok ? parseSurface(result.text) : null
  renderSurface()
}

async function loadSchema() {
  const result = await window.me.vcSchema(settings)
  schema = result.ok ? { labels: result.labels, relationships: result.relationships } : null
}

// ---------------------------------------------------------------------------
// Boot.
// ---------------------------------------------------------------------------

const STARTER = `// A handler reacts: \`signal\` is the triggering event (or undefined on a cron
// tick), and \`gateway.*\` is your typed surface — Ctrl-Space completes both.
// Dry-run is observe-only: effects are suppressed, output comes back here.

console.log('triggered by', signal?.typeName ?? 'cron tick')
`

async function init() {
  settings = await window.me.loadSettings()
  void restoreTheme(settings)
  cm.setValue(STARTER)
  void loadSurface()
  void loadSchema()
  void loadHandlers()
  void loadSignalTypes()
}

void init()
