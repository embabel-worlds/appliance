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

const { parseSurface, membersOf, gatewayPathAt } = EmbabelCodeSurface

/** @param {string} id */
const $ = (id) => document.getElementById(id)

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
function setStatus(el, ok, message) {
  el.textContent = message
  el.className = ok === null ? 'status' : ok ? 'status ok' : 'status error'
}

/**
 * '31691 ms' reads as a code, not a wait — same scale the Query Studio uses,
 * and generation really does take a while on a local model.
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
// State the completions read: the parsed gateway surface, the KG schema (for
// Cypher inside kg calls), and the trigger sample (for `signal.` keys).
// ---------------------------------------------------------------------------

let settings = null
/** @type {ReturnType<typeof parseSurface>} */
let surface = null
/** @type {{labels: Array<any>, relationships: Array<any>} | null} */
let schema = null
/** @type {Record<string, any> | null} */
let sample = null

// ---------------------------------------------------------------------------
// The editor.
// ---------------------------------------------------------------------------

const cm = CodeMirror.fromTextArea($('editor'), {
  mode: 'text/typescript',
  lineNumbers: true,
  viewportMargin: Infinity,
  extraKeys: {
    'Cmd-Enter': () => void dryRun(),
    'Ctrl-Enter': () => void dryRun(),
    'Ctrl-Space': 'autocomplete',
  },
})

const editorText = () => cm.getValue()
function setEditorText(text) {
  cm.setValue(text)
  scheduleValidation()
}

cm.on('change', () => scheduleValidation())

// Completion opens as you type into the things it can complete: a `gateway.`
// or `signal.` chain, or the characters that begin a completable Cypher thing
// inside a kg call's string.
cm.on('inputRead', (_cm, change) => {
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
function cypherContext(before) {
  if (!/\bkg\s*\./.test(before)) return null
  const m = before.match(/(['"`])((?:(?!\1).)*)$/)
  return m ? { cypher: m[2] ?? '' } : null
}

/** The custom hint: what fits HERE — surface members, signal keys, embedded Cypher. */
CodeMirror.registerHelper('hint', 'javascript', (editor) => {
  const cursor = editor.getCursor()
  const line = editor.getLine(cursor.line)
  const before = line.slice(0, cursor.ch)

  // Every list alphabetical, same rule as the Query Studio.
  const found = (list, from) => ({
    list: [...list].sort((a, b) => (a.text ?? a).localeCompare(b.text ?? b)),
    from: CodeMirror.Pos(cursor.line, from),
    to: CodeMirror.Pos(cursor.line, cursor.ch),
  })

  // gateway.… → the typed surface. Absence is meaningful: the surface is
  // generated, complete and per-user, so nothing falls back to a word list.
  const chain = gatewayPathAt(before)
  if (chain) {
    const members = membersOf(surface, chain.path)
      .filter((m) => m.name.toLowerCase().startsWith(chain.stem.toLowerCase()))
      .map((m) => ({ text: m.name, displayText: m.kind === 'namespace' ? `${m.name}.` : `${m.name}()` }))
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

/**
 * The Query Studio's schema-aware branches, applied to the Cypher fragment
 * before the cursor: labels behind their relationship, edges scoped to the
 * node on the left, an alias's properties. Composed from @embabel/vc so this
 * and the Cypher editor next door cannot disagree.
 * @param {string} cypher @returns {{list: string[], stemLength: number} | null}
 */
function cypherCompletions(cypher) {
  const { aliasMap, propertiesOf, relationshipTypesFor, connectedLabels, edgeContext, nodeContext } = EmbabelVc
  let m
  if ((m = cypher.match(/[([]\s*\w*:(\w*)$/)) && cypher.lastIndexOf('(') > cypher.lastIndexOf('[')) {
    const stem = m[1]
    const context = nodeContext(cypher, aliasMap(cypher))
    const labels = context
      ? connectedLabels(schema, context.label, context.type, context.direction)
      : (schema?.labels ?? []).map((l) => l.label)
    return { list: labels.filter((l) => l.toLowerCase().startsWith(stem.toLowerCase())), stemLength: stem.length }
  }
  if ((m = cypher.match(/\[\s*\w*:(\w*)$/))) {
    const stem = m[1]
    const context = edgeContext(cypher, aliasMap(cypher))
    const rels = relationshipTypesFor(schema, context?.label, context?.direction)
    return { list: rels.filter((r) => r.toLowerCase().startsWith(stem.toLowerCase())), stemLength: stem.length }
  }
  if ((m = cypher.match(/(\w+)\.(\w*)$/))) {
    const [, alias, stem] = m
    const label = aliasMap(cypher)[alias]
    if (label) {
      const props = propertiesOf(schema, label)
      return { list: props.filter((p) => p.toLowerCase().startsWith(stem.toLowerCase())), stemLength: stem.length }
    }
  }
  return null
}

// ---------------------------------------------------------------------------
// Validation — the appliance's tsc gate, debounced. A compile spins the
// sandbox, so the debounce is generous and an unchanged text never re-asks.
// ---------------------------------------------------------------------------

let validateTimer = null
let validateSupported = true
let lastValidated = null
let lastVerdict = null

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

function renderVerdict(valid, violations) {
  els.verdict.innerHTML = ''
  if (valid) {
    setStatus(els.validity, true, '✓ type-checks')
    return
  }
  setStatus(els.validity, false, `${violations.length} type problem(s)`)
  for (const violation of violations) {
    const line = document.createElement('div')
    line.className = 'violation'
    line.textContent = violation
    els.verdict.append(line)
  }
}

// ---------------------------------------------------------------------------
// Ask — English → handler through the appliance's generator. Generation only:
// the code lands HERE with its verdict, and running it stays a deliberate act.
// ---------------------------------------------------------------------------

async function generate() {
  const english = els.askRequest.value.trim()
  if (!english) return
  els.generate.disabled = true
  setStatus(els.askStatus, null, 'The appliance is writing your handler — an LLM call plus a compile, give it a moment…')
  let result
  try {
    result = await window.me.handlerGenerate(settings, english)
  } catch (e) {
    result = { ok: false, message: e instanceof Error ? e.message : String(e) }
  }
  els.generate.disabled = false
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
    setStatus(els.askStatus, true, `written${took}${tries} — review, then dry-run`)
    renderVerdict(true, [])
  } else if (result.valid === false) {
    setStatus(els.askStatus, false, `written${took}, but type problems remain — edit before running`)
    renderVerdict(false, result.violations)
  } else {
    // Older appliance: no verdict came back — check here as usual.
    setStatus(els.askStatus, true, `written${took} — review, then dry-run`)
    lastValidated = null
    scheduleValidation()
  }
}

els.generate.addEventListener('click', () => void generate())
els.askRequest.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') void generate()
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
els.copy.addEventListener('click', () => void navigator.clipboard.writeText(editorText()))

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

function renderGroup(title, handlers, yours) {
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

function renderHandler(handler, yours) {
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

async function openHandler(name, status) {
  const result = await window.me.handlerOpen(settings, name)
  if (!result.ok) return setStatus(status, false, result.message)
  setEditorText(result.source)
  els.saveName.value = result.name
  els.saveSignal.value = result.signalType ?? '*'
  els.saveSchedule.value = result.schedule ?? ''
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

els.saveConfirm.addEventListener('click', async () => {
  const name = els.saveName.value.trim()
  if (!name) return setStatus(els.saveStatus, false, 'a handler needs a name')
  setStatus(els.saveStatus, null, 'saving — the appliance compiles it first…')
  const result = await window.me.handlerSave(settings, {
    name,
    source: editorText(),
    signalType: els.saveSignal.value.trim() || '*',
    schedule: els.saveSchedule.value.trim() || null,
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
  void window.meTheme.restoreTheme(settings)
  cm.setValue(STARTER)
  void loadSurface()
  void loadSchema()
  void loadHandlers()
  void loadSignalTypes()
}

void init()
