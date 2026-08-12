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

/* global EmbabelVc */
/*
 * The virtual-Cypher semantics come from @embabel/vc — composing a query, reading
 * the schema, finding a view's parameters. Vendored as a plain script (this
 * renderer has no module system), same as marked and CodeMirror next door.
 *
 * Nothing about the engine's shape is decided in this file any more. That is the
 * point: the console builds the same queries from the same package rather than
 * from a second, drifting reading of VIRTUAL_CYPHER.md.
 */
const { TARGETS, VIA_VALUES, AI_KEYS, aliasMap, declaredParams, anchorLabels, relationshipTypesFor, connectedLabels, edgeContext, nodeContext, propertyMapContext, compose: composeCypher } = EmbabelVc
/** The schema-aware lookup, curried with whatever snapshot we last loaded. */
const propertiesOf = (label) => EmbabelVc.propertiesOf(schema, label)

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
  run: $('run'),
  compose: $('compose'),
  copy: $('copy'),
  validity: $('validity'),
  verdict: $('verdict'),
  runStatus: $('run-status'),
  results: $('results'),
  history: $('history'),
  seekPanel: $('seek-panel'),
  narrowPanel: $('narrow-panel'),
  schema: $('schema'),
  schemaFilter: $('schema-filter'),
}

/** @param {HTMLElement} el @param {boolean | null} ok @param {string} message */
function setStatus(el, ok, message) {
  el.textContent = message
  el.className = ok === null ? 'status' : ok ? 'status ok' : 'status error'
}

/**
 * '31691 ms' reads as a code, not a wait. Milliseconds up to a second, then
 * seconds, then minutes — a cold extract/aggregate really does take minutes.
 * @param {number} ms
 */
function formatDuration(ms) {
  if (ms < 1000) return `${ms} ms`
  const seconds = Math.round(ms / 100) / 10
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const whole = Math.round(ms / 1000)
  const rest = whole % 60
  return rest ? `${Math.floor(whole / 60)} min ${rest} s` : `${Math.floor(whole / 60)} min`
}

// ---------------------------------------------------------------------------
// The editor: CodeMirror over the textarea — Cypher highlighting, completion
// from the schema, ⌘⏎ to run. Vendored like every other browser library here.
// ---------------------------------------------------------------------------

/* global CodeMirror */
const cm = CodeMirror.fromTextArea($('editor'), {
  mode: 'application/x-cypher-query',
  lineNumbers: true,
  viewportMargin: Infinity,
  extraKeys: {
    'Cmd-Enter': () => void run(),
    'Ctrl-Enter': () => void run(),
    'Ctrl-Space': 'autocomplete',
  },
})

let handEdited = false
let programmatic = false

const editorText = () => cm.getValue()
function setEditorText(text, { edited = false } = {}) {
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
cm.on('inputRead', (_cm, change) => {
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

/** @type {{labels: Array<any>, relationships: Array<any>} | null} */
let schema = null

const KEYWORDS = [
  'MATCH', 'WHERE', 'RETURN', 'ORDER BY', 'LIMIT', 'WITH', 'DISTINCT', 'AND', 'OR', 'NOT',
  'CONTAINS', 'STARTS WITH', 'ENDS WITH', 'IN', 'IS NULL', 'IS NOT NULL', 'count(', 'toLower(',
  'ai.relevant(', 'ai.score(', 'ai.classify(',
]

/** The custom hint: what fits HERE, from the schema, never a generic word list. */
CodeMirror.registerHelper('hint', 'cypher', (editor) => {
  const cursor = editor.getCursor()
  const line = editor.getLine(cursor.line)
  const before = line.slice(0, cursor.ch)

  // Every list alphabetical: completion is for scanning, not for whatever
  // order the schema snapshot happened to arrive in.
  const found = (list, from) => ({
    list: [...list].sort((a, b) => a.localeCompare(b)),
    from: CodeMirror.Pos(cursor.line, from),
    to: CodeMirror.Pos(cursor.line, cursor.ch),
  })

  let m
  // (x:Lab… or (:Lab… → labels; behind a relationship, only the labels the
  // schema has seen at its far end — (n:Document)-[:MENTIONS]-(c: asks what a
  // Document can mention, not for every label under the sun. The pattern's
  // FIRST node offers only labels the engine lets OPEN a pattern: bound
  // anchors, and populations tenancy anchors implicitly (your File, your
  // Document). Reach-only labels complete after an edge, where they are legal.
  if ((m = before.match(/[([]\s*\w*:(\w*)$/)) && before.lastIndexOf('(') > before.lastIndexOf('[')) {
    const stem = m[1]
    const context = nodeContext(before, aliasMap(editor.getValue()))
    const labels = context
      ? connectedLabels(schema, context.label, context.type, context.direction)
      : anchorLabels(schema)
    return found(labels.filter((l) => l.toLowerCase().startsWith(stem.toLowerCase())), cursor.ch - stem.length)
  }
  // [r:REL… → relationship types, scoped to the node on the left when the
  // pattern names one — (d:Document)-[: offers only Document's sampled edges.
  // The helper widens in tiers: typed direction, then either direction, then —
  // only for a label the snapshot doesn't know — the full vocabulary. A known
  // label with no edges at all (gov-au's Electorate joins by property) offers
  // nothing: a hint offers and never forbids, so typing past it still works.
  if ((m = before.match(/\[\s*\w*:(\w*)$/))) {
    const stem = m[1]
    const context = edgeContext(before, aliasMap(editor.getValue()))
    const rels = relationshipTypesFor(schema, context?.label, context?.direction)
    return found(rels.filter((r) => r.toLowerCase().startsWith(stem.toLowerCase())), cursor.ch - stem.length)
  }
  // via:'… → the mode selector's vocabulary
  if ((m = before.match(/via:\s*'(\w*)$/))) {
    const stem = m[1]
    return found(VIA_VALUES.filter((v) => v.startsWith(stem)), cursor.ch - stem.length)
  }
  // ai:{ hi… → steering keys
  if ((m = before.match(/ai:\s*\{[^}]*?(\w*)$/))) {
    const stem = m[1]
    return found(AI_KEYS.filter((k) => k.startsWith(stem)), cursor.ch - stem.length)
  }
  // (c:Concept {na… → that label's own property KEYS. A map is a conjunction
  // of equalities, so what fits is what the label has minus what the map
  // already binds; value positions and edge maps get nothing from this branch.
  const mapContext = propertyMapContext(before, aliasMap(editor.getValue()))
  if (mapContext) {
    const stem = before.match(/(\w*)$/)[1]
    const props = mapContext.label ? propertiesOf(mapContext.label).filter((p) => !mapContext.used.includes(p)) : []
    return found(props.filter((p) => p.toLowerCase().startsWith(stem.toLowerCase())), cursor.ch - stem.length)
  }
  // alias.prop… → that alias's label's properties
  if ((m = before.match(/(\w+)\.(\w*)$/))) {
    const [, alias, stem] = m
    const label = aliasMap(editor.getValue())[alias]
    if (label) {
      const props = propertiesOf(label)
      return found(props.filter((p) => p.toLowerCase().startsWith(stem.toLowerCase())), cursor.ch - stem.length)
    }
  }
  // bare word → keywords and labels
  if ((m = before.match(/(\w+)$/))) {
    const stem = m[1]
    const pool = [...KEYWORDS, ...(schema?.labels ?? []).map((l) => l.label)]
    return found(pool.filter((w) => w.toLowerCase().startsWith(stem.toLowerCase())), cursor.ch - stem.length)
  }
  return null
})

/**
 * The query "use" starts on a label. An anchor label reads bare; a reach-only
 * one is composed REACHED — through an edge from an anchor when the schema
 * shows one — because the bare scan is exactly what the preflight rejects.
 */
function useQuery(label) {
  const bare = `MATCH (n:${label.label})\nRETURN n LIMIT 25`
  if (label.anchor !== false) return bare
  const anchors = new Set(anchorLabels(schema))
  const inbound = (schema?.relationships ?? []).find((r) => r.to === label.label && anchors.has(r.from))
  if (inbound) return `MATCH (a:${inbound.from})-[:${inbound.type}]->(n:${label.label})\nRETURN n LIMIT 25`
  const outbound = (schema?.relationships ?? []).find((r) => r.from === label.label && anchors.has(r.to))
  if (outbound) return `MATCH (n:${label.label})-[:${outbound.type}]->(a:${outbound.to})\nRETURN n LIMIT 25`
  return `// ${label.label} is reach-only: traverse to it from a bound anchor\n${bare}`
}

function renderSchema() {
  els.schema.innerHTML = ''
  if (!schema) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = 'Schema unavailable — an older appliance, or not reachable.'
    els.schema.append(p)
    return
  }
  const filter = els.schemaFilter.value.trim().toLowerCase()
  const labels = schema.labels
    .filter((l) => !filter || l.label.toLowerCase().includes(filter))
    .sort((a, b) => a.label.localeCompare(b.label))
  for (const label of labels) {
    const details = document.createElement('details')
    details.className = 'schema-label'
    const summary = document.createElement('summary')
    const name = document.createElement('span')
    name.textContent = label.label
    const count = document.createElement('span')
    count.className = 'count'
    count.textContent = (label.sampleCount ? `${label.sampleCount} sampled` : 'virtual') +
      (label.anchor === false ? ' · reach-only' : '')
    const use = document.createElement('button')
    use.className = 'use'
    use.textContent = 'use'
    use.addEventListener('click', (e) => {
      e.preventDefault()
      setEditorText(useQuery(label), { edited: true })
      scheduleValidation()
    })
    summary.append(name, count, use)
    details.append(summary)

    const props = document.createElement('div')
    props.className = 'props'
    for (const p of label.properties ?? []) {
      const line = document.createElement('div')
      line.textContent = `${p.name} `
      const type = document.createElement('span')
      type.className = 'prop-type'
      type.textContent = p.type + (p.sparse ? ' · sparse' : '')
      line.append(type)
      props.append(line)
    }
    if (!label.exhaustive) {
      const caveat = document.createElement('div')
      caveat.className = 'prop-type'
      caveat.textContent = 'sampled, not exhaustive — absent ≠ nonexistent'
      props.append(caveat)
    }
    details.append(props)

    const touching = (schema.relationships ?? []).filter((r) => r.from === label.label || r.to === label.label)
    if (touching.length) {
      const edges = document.createElement('div')
      edges.className = 'edges'
      for (const r of touching.slice(0, 12)) {
        const line = document.createElement('div')
        line.textContent = `(:${r.from})-[:${r.type}]->(:${r.to})`
        edges.append(line)
      }
      details.append(edges)
    }
    els.schema.append(details)
  }
  if (!labels.length) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = 'No label matches that filter.'
    els.schema.append(p)
  }
}

els.schemaFilter.addEventListener('input', renderSchema)

async function loadSchema() {
  const result = await window.me.vcSchema(settings)
  schema = result.ok ? { labels: result.labels, relationships: result.relationships } : null
  renderSchema()
}

// ---------------------------------------------------------------------------
// Validation — the engine's strict preflight, debounced as you type. Silent
// when the appliance predates the endpoint.
// ---------------------------------------------------------------------------

let validateTimer = null
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
  scheduleValidation()
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
els.compose.addEventListener('click', () => {
  compose()
})
els.copy.addEventListener('click', () => void navigator.clipboard.writeText(editorText()))

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
  // Marked as hand-edited: the composer's controls must not clobber a
  // generated query on the next dropdown twitch.
  setEditorText(result.cypher, { edited: true })
  els.verdict.innerHTML = ''
  if (result.valid === true) {
    // The server generated, PREFLIGHTED, and self-corrected before handing this
    // over — say so, with the price paid (attempts), and skip the re-check.
    const tries = result.attempts && result.attempts > 1 ? ` after ${result.attempts} attempts` : ''
    setStatus(askStatus, true, `written${result.durationMs != null ? ` in ${formatDuration(result.durationMs)}` : ''}${tries} — review, then Run`)
    setStatus(els.validity, true, '✓ schema-valid')
  } else if (result.valid === false) {
    // The generator could not reach a valid query — honest, with the verdict.
    setStatus(askStatus, false, `generated, but ${result.violations.length} schema problem(s) remain — edit before running`)
    setStatus(els.validity, false, `${result.violations.length} schema problem(s)`)
    for (const violation of result.violations) {
      const line = document.createElement('div')
      line.className = 'violation'
      line.textContent = violation
      els.verdict.append(line)
    }
  } else {
    // Older appliance: no server-side verdict — validate here as before.
    setStatus(askStatus, true, `written${result.durationMs != null ? ` in ${formatDuration(result.durationMs)}` : ''} — review, edit if you like, then Run`)
    scheduleValidation()
  }
  if (result.explain) {
    explainEl.textContent = result.explain
    explainEl.hidden = false
  }
}

generateButton.addEventListener('click', () => void generate())
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
      setEditorText(entry.cypher, { edited: true })
      scheduleValidation()
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
// Saved views — (a) save the query you just wrote, (b) run a saved one with
// parameters.
//
// The param controls follow the appliance's own console: the field matches the
// declared TYPE (int/number → numeric, boolean → checkbox, string → text), the
// default is prefilled, the description is the placeholder, and a param with no
// default is marked required. Values go back RAW — the server coerces and
// validates them (`viewInvocation`), so the client never second-guesses the
// engine's own rules.
//
// Saving goes through the appliance too: it preflights the body before writing,
// so a view that could never run can't be saved, and nothing here edits the
// world's YAML directly.
// ---------------------------------------------------------------------------

const viewsEl = $('views')
const viewsStatus = $('views-status')
/* Which realm sections are expanded; the list re-renders on save and delete. */
const openGroups = new Set()
const saveForm = $('save-form')
const saveName = $('save-name')
const saveDescription = $('save-description')
const saveParams = $('save-params')
const saveStatus = $('save-status')

/** One control for a declared param, matched to its type. Returns {name, read()}. */
function paramField(name, spec, container) {
  const row = document.createElement('div')
  row.className = 'param-row'
  const label = document.createElement('span')
  label.className = 'pname'
  label.textContent = spec.default === null || spec.default === undefined ? `${name} *` : name
  row.append(label)
  const hint = spec.description || name
  let input
  if (spec.type === 'boolean') {
    input = document.createElement('input')
    input.type = 'checkbox'
    input.checked = spec.default === true
  } else {
    input = document.createElement('input')
    input.type = spec.type === 'int' || spec.type === 'number' ? 'number' : 'text'
    if (spec.type === 'number') input.step = 'any'
    input.placeholder = hint
    input.title = hint
    if (spec.default !== null && spec.default !== undefined) input.value = String(spec.default)
    // No default means the engine will reject a run without it; catch that in
    // the dialog instead of round-tripping to be told.
    else input.required = true
  }
  row.append(input)
  if (spec.description) {
    const desc = document.createElement('span')
    desc.className = 'vdesc'
    desc.textContent = spec.description
    row.append(desc)
  }
  container.append(row)
  return {
    name,
    read: () => {
      if (spec.type === 'boolean') return input.checked
      if (input.value === '') return undefined // absent ⇒ the declared default applies
      return input.value
    },
  }
}

// --- (a) save the current query -------------------------------------------

$('save-view').addEventListener('click', () => {
  if (!saveForm.hidden) {
    saveForm.hidden = true
    return
  }
  // Offer a declaration for every bind variable the body references, so a
  // parameterized query is savable without hand-writing YAML.
  saveParams.innerHTML = ''
  const params = declaredParams(editorText())
  if (params.length) {
    const note = document.createElement('p')
    note.className = 'hint'
    note.textContent = `Parameters found in the query: ${params.join(', ')} — give each a type and a default (blank default = required at run time).`
    saveParams.append(note)
  }
  saveParamFields = params.map((p) => {
    const row = document.createElement('div')
    row.className = 'param-row'
    const name = document.createElement('span')
    name.className = 'pname'
    name.textContent = `$${p}`
    const type = document.createElement('select')
    for (const t of ['string', 'int', 'number', 'boolean']) {
      const option = document.createElement('option')
      option.value = t
      option.textContent = t
      type.append(option)
    }
    const def = document.createElement('input')
    def.type = 'text'
    def.placeholder = 'default (blank = required)'
    const desc = document.createElement('input')
    desc.type = 'text'
    desc.placeholder = 'what this argument means'
    row.append(name, type, def, desc)
    saveParams.append(row)
    return { name: p, type: () => type.value, def: () => def.value, desc: () => desc.value }
  })
  saveStatus.textContent = ''
  saveForm.hidden = false
  saveName.focus()
})

/** @type {Array<{name: string, type: () => string, def: () => string, desc: () => string}>} */
let saveParamFields = []

$('save-cancel').addEventListener('click', () => {
  saveForm.hidden = true
})

$('save-confirm').addEventListener('click', async () => {
  const name = saveName.value.trim()
  if (!name) {
    setStatus(saveStatus, false, 'a view needs a name')
    return
  }
  const params = {}
  for (const field of saveParamFields) {
    const type = field.type()
    const raw = field.def().trim()
    // Blank default = required on invocation; otherwise coerce to the declared
    // type here so the YAML holds a number/boolean, not the string of one.
    let value = null
    if (raw !== '') {
      value = type === 'int' ? parseInt(raw, 10)
        : type === 'number' ? Number(raw)
          : type === 'boolean' ? raw === 'true'
            : raw
    }
    params[field.name] = { type, default: value, description: field.desc().trim() }
  }
  setStatus(saveStatus, null, 'saving — the appliance validates it first…')
  const result = await window.me.vcSaveView(settings, {
    name,
    cypher: editorText(),
    description: saveDescription.value.trim(),
    params,
  })
  setStatus(saveStatus, result.ok, result.message)
  if (result.ok) {
    saveForm.hidden = true
    saveName.value = ''
    saveDescription.value = ''
    openGroups.add('yours') // show the thing that was just saved
    void loadViews()
  }
})

// --- (b) run a saved view --------------------------------------------------

async function loadViews() {
  const result = await window.me.vcViews(settings)
  viewsEl.innerHTML = ''
  if (!result.ok) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = result.message
    viewsEl.append(p)
    return
  }
  if (!result.views.length) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = 'No views in your world yet — write a query and Save as view.'
    viewsEl.append(p)
    return
  }
  // A world with a few realms installed has more views than anyone wants to
  // scroll past, and the realm a view came from is the only axis that makes
  // them findable. So: one collapsed group per source, the way the appliance's
  // own console lists them. Yours first — the ones you can edit and delete.
  const groups = new Map()
  for (const view of result.views) {
    const key = viewGroup(view)
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(view)
  }
  const rank = (key) => (key === 'yours' ? 0 : key === 'world' ? 1 : 2)
  const keys = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
  for (const key of keys) {
    const views = groups.get(key).sort((a, b) => String(a.name).localeCompare(String(b.name)))
    viewsEl.append(renderGroup(key, views))
  }
}

/** The bucket a view belongs to: your own saves, the world's own, or a realm. */
function viewGroup(view) {
  if (view.source === 'saved') return 'yours'
  return view.source || 'world'
}

/** One collapsed realm section. Open state survives a reload of the list. */
function renderGroup(key, views) {
  const group = document.createElement('details')
  group.className = 'view-group'
  group.open = openGroups.has(key)
  group.addEventListener('toggle', () => {
    if (group.open) openGroups.add(key)
    else openGroups.delete(key)
  })

  const summary = document.createElement('summary')
  const label = document.createElement('span')
  label.className = 'gname'
  label.textContent = key === 'yours' || key === 'world' ? key : `${key} realm`
  const count = document.createElement('span')
  count.className = 'gcount'
  count.textContent = String(views.length)
  summary.append(label, count)
  group.append(summary)

  for (const view of views) group.append(renderView(view))
  return group
}

function renderView(view) {
  const row = document.createElement('div')
  row.className = 'view-row'

  const head = document.createElement('div')
  head.className = 'vhead'
  const name = document.createElement('span')
  name.className = 'vname'
  name.textContent = view.name
  head.append(name)
  // No source badge: the group heading the row sits under already says it.
  if (view.materialized) {
    const cached = document.createElement('span')
    cached.className = 'vsrc'
    cached.textContent = `cached · ttl ${view.ttl ?? '?'}`
    head.append(cached)
  }
  row.append(head)

  if (view.description) {
    const desc = document.createElement('p')
    desc.className = 'vdesc'
    desc.textContent = view.description
    row.append(desc)
  }

  // The params moved into the dialog, so name them here — otherwise Run looks
  // identical whether it fires immediately or asks for three arguments first.
  const params = Object.keys(view.params ?? {})
  if (params.length) {
    const takes = document.createElement('p')
    takes.className = 'vparams'
    takes.textContent = `takes ${params.join(', ')}`
    row.append(takes)
  }

  const actions = document.createElement('div')
  actions.className = 'vactions'
  const runButton = document.createElement('button')
  runButton.textContent = 'Run'
  runButton.addEventListener('click', () => void withArgs(view, 'Run', (args) => runView(view, args, rowStatus)))
  const showButton = document.createElement('button')
  showButton.textContent = 'Show query'
  showButton.addEventListener('click', () => void withArgs(view, 'Show query', (args) => showView(view, args, rowStatus)))
  actions.append(runButton, showButton)
  if (view.source === 'saved') {
    const deleteButton = document.createElement('button')
    deleteButton.textContent = 'Delete'
    deleteButton.addEventListener('click', async () => {
      const result = await window.me.vcDeleteView(settings, view.name)
      setStatus(viewsStatus, result.ok, result.message)
      if (result.ok) void loadViews()
    })
    actions.append(deleteButton)
  }
  const rowStatus = document.createElement('span')
  rowStatus.className = 'status'
  actions.append(rowStatus)
  row.append(actions)
  return row
}

/** The args a view's fields currently hold, omitting blanks so declared defaults apply. */
function viewArgs(fields) {
  const args = {}
  for (const field of fields) {
    const value = field.read()
    if (value !== undefined) args[field.name] = value
  }
  return args
}

/*
 * Arguments are asked for in a modal rather than sitting open on every row: a
 * parameterized view's form is taller than the view itself, and in a list of
 * dozens that noise is what made the panel unnavigable. A view with no params
 * skips the dialog entirely — nothing to ask.
 */
function withArgs(view, verb, act) {
  const params = Object.entries(view.params ?? {})
  if (!params.length) return act({})

  const dialog = document.createElement('dialog')
  dialog.className = 'param-dialog'
  const form = document.createElement('form')
  form.method = 'dialog' // a submit here closes the dialog with the button's value

  const title = document.createElement('h3')
  title.textContent = view.name
  form.append(title)
  if (view.description) {
    const desc = document.createElement('p')
    desc.className = 'vdesc'
    desc.textContent = view.description
    form.append(desc)
  }

  const fields = params.map(([p, spec]) => paramField(p, spec, form))

  const actions = document.createElement('div')
  actions.className = 'vactions'
  const go = document.createElement('button')
  go.value = 'go'
  go.textContent = verb
  const cancel = document.createElement('button')
  cancel.value = 'cancel'
  cancel.textContent = 'Cancel'
  // Cancel must not trip the required-field checks it is escaping from.
  cancel.formNoValidate = true
  actions.append(go, cancel)
  form.append(actions)
  dialog.append(form)

  // Esc closes with an empty returnValue, so anything but 'go' means "never mind".
  dialog.addEventListener('close', () => {
    dialog.remove()
    if (dialog.returnValue === 'go') void act(viewArgs(fields))
  })
  document.body.append(dialog)
  dialog.showModal()
  form.querySelector('input')?.focus()
}

/** Put the view's invocation into the editor — see before you run, same as everything else here. */
async function showView(view, args, status) {
  const result = await window.me.vcViewInvocation(settings, view.name, args)
  if (!result.ok) return setStatus(status, false, result.message)
  setEditorText(result.cypher, { edited: true })
  scheduleValidation()
  setStatus(status, true, 'in the editor')
}

async function runView(view, args, status) {
  setStatus(status, null, 'resolving…')
  const invocation = await window.me.vcViewInvocation(settings, view.name, args)
  // A bad argument is the user's to fix in the form — say so on the row rather
  // than launching a doomed run.
  if (!invocation.ok) return setStatus(status, false, invocation.message)
  setEditorText(invocation.cypher, { edited: true })
  setStatus(status, null, 'running…')
  await run()
  setStatus(status, true, '')
}

// ---------------------------------------------------------------------------
// Boot.
// ---------------------------------------------------------------------------

async function init() {
  settings = await window.me.loadSettings()
  void window.meTheme.restoreTheme(settings)
  renderTargets()
  applyTarget()
  compose()
  renderHistory()
  void loadTagUniverse()
  void loadSchema()
  void initLensModel()
  void loadViews()
}

void init()
