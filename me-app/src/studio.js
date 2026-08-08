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
// Everything composed is editable; the appliance applies the per-user scope
// server-side, so an edited query can be wrong but not unsafe.

/** @param {string} id */
const $ = (id) => document.getElementById(id)

const els = {
  targets: $('targets'),
  anchorRow: $('anchor-row'),
  anchor: $('anchor'),
  seedLabel: $('seed-label'),
  seed: $('seed'),
  mode: $('mode'),
  cost: $('cost'),
  intentRow: $('intent-row'),
  intent: $('intent'),
  tagRow: $('tag-row'),
  tag: $('tag'),
  dateRow: $('date-row'),
  dateField: $('date-field'),
  dateFrom: $('date-from'),
  dateTo: $('date-to'),
  minScore: $('min-score'),
  limit: $('limit'),
  steerPanel: $('steer-panel'),
  aiHint: $('ai-hint'),
  aiModel: $('ai-model'),
  aiTemperature: $('ai-temperature'),
  aiConfidence: $('ai-confidence'),
  aiFresh: $('ai-fresh'),
  editor: $('editor'),
  run: $('run'),
  compose: $('compose'),
  copy: $('copy'),
  runStatus: $('run-status'),
  results: $('results'),
  history: $('history'),
  seekPanel: $('seek-panel'),
  narrowPanel: $('narrow-panel'),
}

/** @param {HTMLElement} el @param {boolean | null} ok @param {string} message */
function setStatus(el, ok, message) {
  el.textContent = message
  el.className = ok === null ? 'status' : ok ? 'status ok' : 'status error'
}

/** Cypher string-literal escape: backslashes first, then quotes. */
const esc = (s) => s.replace(/\\/g, '\\\\').replace(/'/g, "\\'")

// ---------------------------------------------------------------------------
// The targets and their modes. Costs are the spec's own guidance, shown at the
// moment of choice: agentic is several LLM calls PER ANCHOR, never a default.
// ---------------------------------------------------------------------------

const MODES = {
  about: { label: 'about — semantic (vector)', cost: 'one embedding pass · cheap', dear: false },
  mentions: { label: 'mentions — exact tokens (keyword)', cost: 'fulltext, deterministic · cheap', dear: false },
  judged: { label: 'judged — agentic, against a brief', cost: 'several LLM calls per anchor · expensive', dear: true },
  semantic: { label: 'semantic — vector over thread summaries', cost: 'one embedding pass · cheap', dear: false },
}

const TARGETS = {
  documents: {
    name: 'Documents',
    what: 'your ingested knowledge base',
    seedLabel: 'Search for',
    modes: ['about', 'mentions', 'judged'],
    tags: true,
    dates: true,
  },
  files: {
    name: 'Files',
    what: 'shared folders, walked live',
    seedLabel: 'Term or idea',
    modes: ['mentions', 'judged'],
    tags: false,
    dates: false,
  },
  threads: {
    name: 'Email threads',
    what: 'relevance over thread summaries',
    seedLabel: 'Seed',
    modes: ['semantic'],
    tags: false,
    dates: false,
    anchors: {
      topic: { label: 'Topic (Concept)', pattern: (v) => `(:Concept {value:'${esc(v)}'})`, placeholder: 'the renewal' },
      person: { label: 'Person', pattern: (v) => `(:Person {name:'${esc(v)}'})`, placeholder: 'Ada Lovelace' },
      organization: { label: 'Organization', pattern: (v) => `(:Organization {name:'${esc(v)}'})`, placeholder: 'Acme' },
      meeting: { label: 'Meeting', pattern: (v) => `(:Meeting {subject:'${esc(v)}'})`, placeholder: 'Q3 planning' },
    },
  },
  canvas: {
    name: 'Blank canvas',
    what: 'the whole graph, your shapes',
    modes: [],
  },
}

let target = 'documents'

// ---------------------------------------------------------------------------
// Composition — controls → Cypher. The editor is the source of truth once the
// user touches it: composing again is an explicit act (the Compose button or a
// control change while the editor is still machine-written).
// ---------------------------------------------------------------------------

let handEdited = false

/** The edge property map: via, intent, and the nested {ai:{…}} steering. */
function edgeProps() {
  const parts = []
  const mode = els.mode.value
  if (mode === 'mentions') parts.push("via:'keyword'")
  if (mode === 'judged') {
    parts.push("via:'agentic-rag'")
    const intent = els.intent.value.trim()
    if (intent) parts.push(`intent:'${esc(intent)}'`)
  }
  const ai = []
  if (els.aiHint.value.trim()) ai.push(`hint:'${esc(els.aiHint.value.trim())}'`)
  if (els.aiModel.value.trim()) ai.push(`model:'${esc(els.aiModel.value.trim())}'`)
  if (els.aiTemperature.value !== '') ai.push(`temperature:${Number(els.aiTemperature.value)}`)
  if (els.aiConfidence.value !== '') ai.push(`confidence:${Number(els.aiConfidence.value)}`)
  if (els.aiFresh.checked) ai.push('fresh:true')
  if (ai.length && mode === 'judged') parts.push(`ai:{${ai.join(', ')}}`)
  return parts.length ? ` {${parts.join(', ')}}` : ''
}

function whereParts(alias) {
  const parts = []
  const spec = TARGETS[target]
  if (spec.tags && els.tag.value) parts.push(`'${esc(els.tag.value)}' IN ${alias}.tags`)
  if (spec.dates && els.dateField.value) {
    if (els.dateFrom.value) parts.push(`${alias}.${els.dateField.value} >= '${els.dateFrom.value}'`)
    if (els.dateTo.value) parts.push(`${alias}.${els.dateField.value} < '${els.dateTo.value}'`)
  }
  if (els.minScore.value !== '') parts.push(`r.score >= ${Number(els.minScore.value)}`)
  return parts
}

const LIMIT = () => Math.max(1, Number(els.limit.value) || 10)

function composeDocuments() {
  const seed = els.seed.value.trim() || 'your search'
  const mode = els.mode.value
  const where = whereParts('d')
  const evidence =
    mode === 'mentions'
      ? 'r.score AS score, r.snippet AS snippet, r.matchedTerms AS matched'
      : mode === 'judged'
        ? 'r.score AS score, r.snippet AS evidence, r.intent AS intent'
        : 'r.score AS score, r.snippet AS snippet, r.mode AS mode'
  return [
    mode === 'judged' ? '// judged retrieval: several LLM calls per anchor — an explicit choice, never a default' : null,
    `MATCH (:Concept {value:'${esc(seed)}'})-[r:RELEVANT_TO${edgeProps()}]->(d:Document)`,
    where.length ? `WHERE ${where.join('\n  AND ')}` : null,
    `RETURN d.title AS title, ${evidence}`,
    `ORDER BY r.score DESC LIMIT ${LIMIT()}`,
    '',
    '// Per-row LLM judgment over the fetched rows — one uncomment away:',
    "//   AND ai.relevant(d, 'genuinely about <your criterion>')      -- filter",
    "//   ORDER BY ai.score(d, '<your criterion>') DESC               -- rerank",
    '// Content, not metadata: MATCH (d)-[:HAS_SUMMARY]->(s:Summary) RETURN s.summary',
    '// AND at the document: a second seed meeting the same d asserts BOTH terms.',
  ]
    .filter((l) => l !== null)
    .join('\n')
}

function composeFiles() {
  const mode = els.mode.value
  if (mode === 'mentions') {
    // The files keyword join greps CONTENT for the literal term; the seed must
    // be lowercase (the graph re-applies the predicate to the excerpt).
    const seed = (els.seed.value.trim() || 'your term').toLowerCase()
    return [
      `MATCH (:Concept {value:'${esc(seed)}'})-[r:RELEVANT_TO {via:'keyword'}]->(f:File)`,
      `RETURN f.name AS name, f.dir AS dir, f.content AS excerpt, f.modifiedAt AS modified`,
      `ORDER BY modified DESC LIMIT ${LIMIT()}`,
      '',
      '// The excerpt holds the matching lines, never the whole file body.',
      '// For summarization use the Documents target — files are metadata + grep.',
    ].join('\n')
  }
  const seed = els.seed.value.trim() || 'your idea'
  return [
    '// judged retrieval over file contents: several LLM calls per anchor',
    `MATCH (:Concept {value:'${esc(seed)}'})-[r:RELEVANT_TO${edgeProps()}]->(f:File)`,
    `RETURN f.name AS name, f.dir AS dir, r.score AS score, r.snippet AS evidence`,
    `ORDER BY r.score DESC LIMIT ${LIMIT()}`,
  ].join('\n')
}

function composeThreads() {
  const anchor = TARGETS.threads.anchors[els.anchor.value]
  const seed = els.seed.value.trim() || anchor.placeholder
  const floor = els.minScore.value !== '' ? Number(els.minScore.value) : 0.6
  return [
    `MATCH ${anchor.pattern(seed)}-[r:RELEVANT_TO]->(t:RelevantEmailThread)`,
    `WHERE r.score >= ${floor}`,
    `RETURN t.subject AS subject, t.snippet AS snippet, r.score AS score`,
    `ORDER BY r.score DESC LIMIT ${LIMIT()}`,
    '',
    "// RELEVANT_TO is semantic — 'reads as being about', never 'corresponded with'.",
    '// Compose with structure: (me:AssistantUser)-[:EMAILED]->(p:Person)-[r:RELEVANT_TO]->(t)',
  ].join('\n')
}

function composeCanvas() {
  return [
    '// The whole graph is yours. Shapes the schema serves:',
    "//   (:Concept {value:'…'})-[r:RELEVANT_TO]->(d:Document)            -- about (vector)",
    "//   (:Concept {value:'…'})-[r:RELEVANT_TO {via:'keyword'}]->(d)     -- mentions",
    "//   (:Concept)-[r:RELEVANT_TO {via:'agentic-rag', intent:'…'}]->(d) -- judged",
    "//   (:Concept {value:'…'})-[r:RELEVANT_TO {via:'keyword'}]->(f:File)",
    '//   (:Person|Organization|Meeting)-[r:RELEVANT_TO]->(t:RelevantEmailThread)',
    '//   (d:Document)-[:HAS_SUMMARY]->(s:Summary)',
    "//   WHERE 'tag' IN d.tags — membership, never CONTAINS (tags is a list)",
    "//   ai.relevant(n, '…') / ai.score(n, '…') / ai.classify(n, '…') — per-row LLM judgment",
    '',
    'MATCH (d:Document)',
    'RETURN d.title AS title, d.tags AS tags, d.uri AS uri',
    'ORDER BY d.ingestionTimestamp DESC LIMIT 25',
  ].join('\n')
}

function compose() {
  const composers = { documents: composeDocuments, files: composeFiles, threads: composeThreads, canvas: composeCanvas }
  els.editor.value = composers[target]()
  handEdited = false
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
    name.textContent = spec.name
    const what = document.createElement('span')
    what.className = 't-what'
    what.textContent = spec.what
    button.append(name, what)
    button.addEventListener('click', () => {
      target = key
      renderTargets()
      applyTarget()
      compose()
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
      option.textContent = anchor.label
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
  if (!handEdited) compose()
}

for (const el of [
  els.anchor, els.seed, els.mode, els.intent, els.tag, els.dateField, els.dateFrom, els.dateTo,
  els.minScore, els.limit, els.aiHint, els.aiModel, els.aiTemperature, els.aiConfidence, els.aiFresh,
]) {
  el.addEventListener('input', onControlChange)
  el.addEventListener('change', onControlChange)
}
els.editor.addEventListener('input', () => { handEdited = true })
els.compose.addEventListener('click', () => { compose() })
els.copy.addEventListener('click', () => void navigator.clipboard.writeText(els.editor.value))

// ---------------------------------------------------------------------------
// The tag universe — same query the main window's dropdown uses.
// ---------------------------------------------------------------------------

let settings = null

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

// ---------------------------------------------------------------------------
// Running, results, history.
// ---------------------------------------------------------------------------

/** Every cell textContent — row values come from documents, and documents lie. */
function renderRows(rows) {
  els.results.innerHTML = ''
  if (!rows.length) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = 'No rows.'
    els.results.append(p)
    return
  }
  const columns = []
  for (const row of rows) for (const key of Object.keys(row)) if (!columns.includes(key)) columns.push(key)
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
    const firstLine = entry.cypher.split('\n').find((l) => l.trim() && !l.trim().startsWith('//')) ?? entry.cypher
    button.textContent = `${firstLine}  `
    const meta = document.createElement('span')
    meta.className = 'history-meta'
    meta.textContent = `· ${entry.rows} row(s)`
    button.append(meta)
    button.addEventListener('click', () => {
      els.editor.value = entry.cypher
      handEdited = true
    })
    els.history.append(button)
  }
}

function remember(cypher, rows) {
  const entries = historyEntries().filter((e) => e.cypher !== cypher)
  entries.unshift({ cypher, rows, at: new Date().toISOString() })
  localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, HISTORY_MAX)))
  renderHistory()
}

async function run() {
  const cypher = els.editor.value.trim()
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
  if (result.durationMs != null) parts.push(`${result.durationMs} ms`)
  for (const warning of result.warnings) parts.push(warning)
  if (!result.rowCount && result.hint) parts.push(result.hint)
  setStatus(els.runStatus, result.warnings.length ? null : true, parts.join(' · '))
  renderRows(result.rows)
  remember(cypher, result.rowCount)
}

els.run.addEventListener('click', () => void run())
els.editor.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') void run()
})

// ---------------------------------------------------------------------------
// Boot.
// ---------------------------------------------------------------------------

async function init() {
  settings = await window.me.loadSettings()
  renderTargets()
  applyTarget()
  compose()
  renderHistory()
  void loadTagUniverse()
}

void init()
