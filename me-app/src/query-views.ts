// Query Studio — the SAVED VIEWS panel: save the query you just wrote, run a
// saved one with parameters. A plain script publishing one global, per this
// renderer's no-module idiom; the orchestrator injects what it owns at init.

import { $ } from './dom'
import type { Settings } from './types'
import { EMPTY_SETTINGS, type StudioDeps } from './studio-deps'
import type { ViewSpec } from './wire'
import { declaredParams } from '@embabel/appliance-kit/vc'
import { setStatus } from '@embabel/appliance-kit/studio-kit'

export const queryViews = (() => {

/* Injected at init — the editor and its verbs belong to the orchestrator. */
/* Filled by init(deps) before anything here runs — see StudioDeps. */
let settings: Settings = EMPTY_SETTINGS
let editorText: () => string = () => ''
let setEditorText: StudioDeps['setEditorText'] = () => {}
let scheduleValidation: () => void = () => {}
let run: () => void = () => {}

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
function paramField(name: string, spec: Record<string, unknown>, container: HTMLElement) {
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
    input.placeholder = String(hint ?? '')
    input.title = String(hint ?? '')
    if (spec.default !== null && spec.default !== undefined) input.value = String(spec.default)
    // No default means the engine will reject a run without it; catch that in
    // the dialog instead of round-tripping to be told.
    else input.required = true
  }
  row.append(input)
  if (spec.description) {
    const desc = document.createElement('span')
    desc.className = 'vdesc'
    desc.textContent = String(spec.description ?? '')
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
  /* `declaredParams` returns the NAMES — plain strings. This annotation used to
   * say `{name, type, def}`, which was simply wrong: the body below already
   * treated `p` as a string (`$${p}`) and built the type/default controls
   * itself. Nothing caught it while the package arrived as an untyped global. */
  saveParamFields = params.map((p: string) => {
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

let saveParamFields: Array<{name: string, type: () => string, def: () => string, desc: () => string}> = []

$('save-cancel').addEventListener('click', () => {
  saveForm.hidden = true
})

$('save-confirm').addEventListener('click', async () => {
  const name = saveName.value.trim()
  if (!name) {
    setStatus(saveStatus, false, 'a view needs a name')
    return
  }
  const params: Record<string, unknown> = {}
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
  const rank = (key: string) => (key === 'yours' ? 0 : key === 'world' ? 1 : 2)
  const keys = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
  for (const key of keys) {
    const views = groups.get(key).sort((a: ViewSpec, b: ViewSpec) => String(a.name).localeCompare(String(b.name)))
    viewsEl.append(renderGroup(key, views))
  }
}

/** The bucket a view belongs to: your own saves, the world's own, or a realm. */
function viewGroup(view: ViewSpec) {
  if (view.source === 'saved') return 'yours'
  return view.source || 'world'
}

/** One collapsed realm section. Open state survives a reload of the list. */
function renderGroup(key: string, views: ViewSpec[]) {
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

function renderView(view: ViewSpec) {
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
function viewArgs(fields: Array<{ name: string; read: () => unknown }>) {
  const args: Record<string, unknown> = {}
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
function withArgs(view: ViewSpec, verb: string, act: (args: Record<string, unknown>) => void) {
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
async function showView(view: ViewSpec, args: Record<string, unknown>, status: HTMLElement) {
  const result = await window.me.vcViewInvocation(settings, view.name, args)
  if (!result.ok) return setStatus(status, false, result.message)
  setEditorText(result.cypher, { edited: true })
  scheduleValidation()
  setStatus(status, true, 'in the editor')
}

async function runView(view: ViewSpec, args: Record<string, unknown>, status: HTMLElement) {
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

return {
  init(deps: StudioDeps) {
    settings = deps.settings
    editorText = deps.editorText
    setEditorText = deps.setEditorText
    scheduleValidation = deps.scheduleValidation
    run = deps.run
    return loadViews()
  },
}
})()
