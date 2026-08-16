// Query Studio — the advanced virtual-Cypher surface, in its own window so the
// main app stays a sensor panel rather than a database console.
//
// The composer's job is honesty about the engine's real primitives, per the
// virtual-cypher spec (realm-spec/VIRTUAL_CYPHER.md):
//
//  - Relevance is an EDGE from an anchor, and the mode is chosen AT the edge:
//    no `via` = vector (about X), `via:'keyword'` = lexical (mentions X),
//    `via:'agentic-rag'` + `intent` = a bounded LLM loop judging every
//    candidate against the brief. Three modes, three different questions.
//  - `{ai:{…}}` on the edge steers an LLM-backed fetch (hint / model role /
//    temperature / confidence / fresh). Nested map, not the retired ai_* keys.
//  - Per-row judgment (`ai.relevant`, `ai.score`, `ai.classify`) is textual by
//    nature — the composer teaches it as commented lines, one uncomment away.
//  - Documents are ONE target. Files and email threads are relevance targets
//    too, each with its own anchors, and the blank canvas is the whole graph.
//
// Three honesty guarantees come from using the appliance's own surfaces rather
// than reimplementing them: the SCHEMA panel and completion read the same
// snapshot the engine validates against; VALIDATION is the engine's strict
// preflight run without execution, so "valid" here and "rejected" there can
// never disagree; and the per-user scope is applied server-side, so an edited
// query can be wrong but not unsafe.

/*
 * The virtual-Cypher semantics come from the kit — composing a query, reading the
 * schema, finding a view's parameters. Imported, and bundled in by esbuild: the
 * renderer has no module system of its own, but the SOURCES do, and the bundle is
 * a classic script by the time the page loads it.
 *
 * Nothing about the engine's shape is decided in this file any more. That is the
 * point: the Worlds console builds the same queries from the same package rather
 * than from a second, drifting reading of VIRTUAL_CYPHER.md.
 */
import type { Settings } from './types'
import { $ } from './dom'
import { restoreTheme } from './theme'
import { queryAsk } from './query-ask'
import { queryComposer } from './query-composer'
import { queryViews } from './query-views'
import { querySchemaPanel } from './query-schema-panel'
import './graph'
import { EMPTY_SETTINGS, type StudioDeps } from './studio-deps'
import type { SchemaSnapshot } from './wire'
import * as Vc from '@embabel/appliance-kit/vc'
import { aliasMap, rowColumns, rowsToCsv, rowsToMarkdown } from '@embabel/appliance-kit/vc'
/* The shared EDITOR BEHAVIOR — hint orchestration, the definition tooltip,
 * durations, acknowledged copies. The semantics are passed IN rather than
 * imported there, so the two packages compose without studio-kit depending on
 * vc. */
import {
  copyWithNod,
  createCypherHint,
  createDefinitionTooltip,
  definitionTitle,
  formatDuration,
  setStatus,
} from '@embabel/appliance-kit/studio-kit'



/** @param {string} id */
const els = {
  run: $('run'),
  copy: $('copy'),
  validity: $('validity'),
  verdict: $('verdict'),
  runStatus: $('run-status'),
  results: $('results'),
  history: $('history'),
}

// ---------------------------------------------------------------------------
// The editor: CodeMirror over the textarea — Cypher highlighting, completion
// from the schema, ⌘⏎ to run. Vendored like every other browser library here.
// ---------------------------------------------------------------------------

/* global CodeMirror, queryComposer, queryAsk, querySchemaPanel, queryViews */
const cm = CodeMirror.fromTextArea($('editor'), {
  mode: 'application/x-cypher-query',
  lineNumbers: true,
  viewportMargin: Infinity,
  extraKeys: {
    'Cmd-Enter': (): void => void run(),
    'Ctrl-Enter': (): void => void run(),
    'Ctrl-Space': 'autocomplete',
  },
})

let handEdited = false
let programmatic = false

const editorText = () => cm.getValue()
function setEditorText(text: string, { edited = false }: { edited?: boolean } = {}) {
  programmatic = true
  cm.setValue(text)
  programmatic = false
  handEdited = edited
}

cm.on('change', () => {
  if (programmatic) return
  handEdited = true
  scheduleValidation()
})

// Completion opens as you type the characters that begin a completable thing —
// a label after `(x:`, a relationship after `[:`, a property after `alias.`.
cm.on('inputRead', (_cm: any, change: any) => {
  if (change.origin !== '+input') return
  const ch = change.text[change.text.length - 1]
  if (/[:.'{]/.test(ch) || /\w/.test(ch)) {
    const cursor = cm.getCursor()
    const before = cm.getLine(cursor.line).slice(0, cursor.ch)
    if (/(\(\s*\w*:\w*|\[\s*\w*:\w*|\w+\.\w*|via:\s*'\w*|ai:\s*\{\s*\w*|\(\s*\w*\s*(?::\s*\w+)?\s*\{[^{}]*)$/.test(before)) {
      cm.showHint({ completeSingle: false })
    }
  }
})

// ---------------------------------------------------------------------------
// The schema — fetched once from the appliance, driving BOTH the browser panel
// and completion. Virtual labels included: this is the engine's own snapshot.
// ---------------------------------------------------------------------------

let schema: {labels: Array<any>, relationships: Array<any>} | null = null
let settings: Settings = EMPTY_SETTINGS

const KEYWORDS = [
  'MATCH', 'WHERE', 'RETURN', 'ORDER BY', 'LIMIT', 'WITH', 'DISTINCT', 'AND', 'OR', 'NOT',
  'CONTAINS', 'STARTS WITH', 'ENDS WITH', 'IN', 'IS NULL', 'IS NOT NULL', 'count(', 'toLower(',
  'ai.relevant(', 'ai.score(', 'ai.classify(',
]

/* The custom hint: what fits HERE, from the schema, never a generic word
 * list — the kit's orchestration over @embabel/vc's decisions, identical on
 * every surface that mounts it. */
CodeMirror.registerHelper('hint', 'cypher', createCypherHint(CodeMirror, Vc, {
  schema: () => schema,
  keywords: KEYWORDS,
}))

/* The definition tooltip — the kit's: one floating element the page styles
 * by its id, titled with each label's realm (or core), hidden on any scroll.
 * Local names kept so every call site reads as before. */
const definitions = createDefinitionTooltip(document)
const showDefinition = (target: HTMLElement, name: string, text: string) => definitions.show(target, name, text)
const hideDefinition = () => definitions.hide()

// The QUERY is where labels are actually pointed at: hovering `Electorate` in
// your own Cypher asks what it means, and the answer is the same declared
// definition the schema panel shows. Token under the cursor → its definition.
let hoveredToken: string | null = null
cm.getWrapperElement().addEventListener('mousemove', (e: MouseEvent) => {
  const pos = cm.coordsChar({ left: e.clientX, top: e.clientY }, 'window')
  const token = cm.getTokenAt(pos)
  const word = token?.string ?? ''
  // coordsChar clamps to the nearest char; only a cursor actually ON the
  // token counts, or the last word of a line answers for the space after it.
  const start = cm.charCoords({ line: pos.line, ch: token.start }, 'window')
  const end = cm.charCoords({ line: pos.line, ch: token.end }, 'window')
  const inside = e.clientX >= start.left && e.clientX <= end.right && e.clientY >= start.top && e.clientY <= start.bottom
  if (!inside) {
    hoveredToken = null
    return hideDefinition()
  }
  if (word === hoveredToken) return
  hoveredToken = word
  // The cypher mode tokenizes `e:Electorate` as ONE atom — the label is what
  // follows the colon. A bare alias (`e` in `e.division`) resolves through
  // the query's own alias map, so hovering it answers for its label too.
  const box = { left: start.left, right: end.right, top: start.top, bottom: start.bottom }
  const labels = schema?.labels ?? []
  const name = word.includes(':') ? word.slice(word.lastIndexOf(':') + 1) : word
  const resolved = labels.some((l) => l.label === name) ? name : aliasMap(editorText())[name]
  const label = labels.find((l) => l.label === resolved)
  if (label) return showDefinition(box as unknown as HTMLElement, definitionTitle(label), label.description ?? '')
  // e.marginPct → the PROPERTY's declared description: the alias before the
  // dot names the label, the token names the property.
  const owner = cm.getLine(pos.line).slice(0, token.start).match(/(\w+)\.$/)
  const ownerLabel = owner && labels.find((l) => l.label === aliasMap(editorText())[owner[1]])
  const prop = ownerLabel?.properties?.find((p: { name: string; description?: string }) => p.name === word && p.description)
  if (prop) return showDefinition(box as unknown as HTMLElement, `${ownerLabel.label}.${prop.name}`, prop.description)
  hideDefinition()
})
cm.getWrapperElement().addEventListener('mouseleave', () => {
  hoveredToken = null
  hideDefinition()
})

// ---------------------------------------------------------------------------
// Validation — the engine's strict preflight, debounced as you type. Silent
// when the appliance predates the endpoint.
// ---------------------------------------------------------------------------

let validateTimer: ReturnType<typeof setTimeout> | null = null
let validateSupported = true

function scheduleValidation() {
  if (!validateSupported) return
  if (validateTimer) clearTimeout(validateTimer)
  setStatus(els.validity, null, '…')
  validateTimer = setTimeout(() => void validateNow(), 700)
}

async function validateNow() {
  const cypher = editorText().trim()
  if (!cypher) {
    els.validity.textContent = ''
    els.verdict.innerHTML = ''
    return
  }
  const result = await window.me.vcValidate(settings, cypher)
  if (!result.ok) {
    // An appliance without the endpoint: say nothing, once — don't nag per keystroke.
    validateSupported = false
    els.validity.textContent = ''
    return
  }
  els.verdict.innerHTML = ''
  if (result.valid) {
    setStatus(els.validity, true, '✓ schema-valid')
    return
  }
  setStatus(els.validity, false, `${result.violations.length} schema problem(s)`)
  for (const violation of result.violations) {
    const line = document.createElement('div')
    line.className = 'violation'
    line.textContent = violation
    els.verdict.append(line)
  }
}

els.copy.addEventListener('click', () => void copyWithNod(els.copy, 'Copy', editorText()))

// The results, copyable in the two shapes people paste: a Markdown table for a
// doc or a chat, CSV for a spreadsheet. Serialized by @embabel/vc under the
// same column rule the table renders with, so what you copy is what you saw.
const copyMd = $('copy-md')
const copyCsv = $('copy-csv')
let lastRows: Array<Record<string, unknown>> = []

copyMd.addEventListener('click', () => void copyWithNod(copyMd, 'Copy as Markdown', rowsToMarkdown(lastRows)))
copyCsv.addEventListener('click', () => void copyWithNod(copyCsv, 'Copy as CSV', rowsToCsv(lastRows)))

// ---------------------------------------------------------------------------
// Running, results, history.
// ---------------------------------------------------------------------------

/** Every cell textContent — row values come from documents, and documents lie. */
function renderRows(rows: Array<Record<string, unknown>>) {
  els.results.innerHTML = ''
  lastRows = rows
  copyMd.hidden = !rows.length
  copyCsv.hidden = !rows.length
  if (!rows.length) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = 'No rows.'
    els.results.append(p)
    return
  }
  const columns = rowColumns(rows)
  const table = document.createElement('table')
  table.className = 'results-table'
  const head = table.createTHead().insertRow()
  for (const column of columns) {
    const th = document.createElement('th')
    th.textContent = column
    head.append(th)
  }
  const body = table.createTBody()
  for (const row of rows) {
    const tr = body.insertRow()
    for (const column of columns) {
      const value = row[column]
      tr.insertCell().textContent =
        value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)
    }
  }
  els.results.append(table)
}

const HISTORY_KEY = 'embabel-me-studio-history'
const HISTORY_MAX = 20

function historyEntries() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]')
  } catch {
    return []
  }
}

function renderHistory() {
  const entries = historyEntries()
  els.history.innerHTML = ''
  if (!entries.length) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = 'Queries you run land here.'
    els.history.append(p)
    return
  }
  for (const entry of entries) {
    const button = document.createElement('button')
    button.className = 'history-item'
    button.title = entry.cypher
    const firstLine = entry.cypher.split('\n').find((l: string) => l.trim() && !l.trim().startsWith('//')) ?? entry.cypher
    button.textContent = `${firstLine}  `
    const meta = document.createElement('span')
    meta.className = 'history-meta'
    meta.textContent = `· ${entry.rows} row(s)`
    button.append(meta)
    button.addEventListener('click', () => {
      setEditorText(entry.cypher, { edited: true })
      scheduleValidation()
    })
    els.history.append(button)
  }
}

function remember(cypher: string, rows: Array<Record<string, unknown>>) {
  const entries = historyEntries().filter((e: { cypher: string }) => e.cypher !== cypher)
  entries.unshift({ cypher, rows, at: new Date().toISOString() })
  localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, HISTORY_MAX)))
  renderHistory()
}

async function run() {
  const cypher = editorText().trim()
  if (!cypher) return
  els.run.disabled = true
  els.results.innerHTML = ''
  setStatus(els.runStatus, null, 'Running — relevance joins fetch live, give it a moment…')
  let result
  try {
    result = await window.me.vcExecute(settings, cypher)
  } catch (e) {
    setStatus(els.runStatus, false, e instanceof Error ? e.message : String(e))
    els.run.disabled = false
    return
  }
  els.run.disabled = false
  if (!result.ok) return setStatus(els.runStatus, false, result.message)
  if (result.error) return setStatus(els.runStatus, false, result.error)
  const parts = [`${result.rowCount} row(s)`]
  if (result.durationMs != null) parts.push(formatDuration(result.durationMs))
  for (const warning of result.warnings) parts.push(warning)
  if (!result.rowCount && result.hint) parts.push(result.hint)
  setStatus(els.runStatus, result.warnings.length ? null : true, parts.join(' · '))
  renderRows(result.rows)
  remember(cypher, result.rowCount)
}

els.run.addEventListener('click', () => void run())

// ---------------------------------------------------------------------------
// Boot.
// ---------------------------------------------------------------------------

async function init() {
  settings = await window.me.loadSettings()
  void restoreTheme(settings)
  // The panels are their own files, each publishing one global (this
  // renderer's no-module idiom); the orchestrator hands each what it owns —
  // the editor, its verbs, and the schema snapshot completion reads.
  queryComposer.init({ settings, setEditorText, isHandEdited: () => handEdited })
  renderHistory()
  void queryAsk.init({ settings, setEditorText, scheduleValidation })
  void querySchemaPanel.init({
    settings,
    definitions,
    setEditorText,
    scheduleValidation,
    onSchema: (snapshot: SchemaSnapshot) => { schema = snapshot },
  })
  void queryViews.init({ settings, editorText, setEditorText, scheduleValidation, run })
}

void init()
