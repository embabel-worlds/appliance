// Query Studio — the SCHEMA PANEL: every node type this world can reach, its
// definition and realm on hover, and `use` composing a legal starting query
// (reach-only labels are composed REACHED, never as the naked scan the
// preflight rejects). Plain script, one global.

import type { Settings } from './types'
import { EMPTY_SETTINGS, type StudioDeps } from './studio-deps'
import type { SchemaLabel, SchemaRel, SchemaSnapshot } from './wire'
import type { Control } from './dom'
export const querySchemaPanel = (() => {
/* global EmbabelVc, EmbabelStudioKit */
const $ = (id: string) => document.getElementById(id)
const { setStatus } = EmbabelStudioKit
const { anchorLabels } = EmbabelVc
const { definitionTitle } = EmbabelStudioKit
const els = { schema: $('schema'), schemaFilter: $('schema-filter') }
/* Filled by init(deps) before anything here runs — see StudioDeps. */
let settings: Settings = EMPTY_SETTINGS
let setEditorText: StudioDeps['setEditorText'] = () => {}
let scheduleValidation: () => void = () => {}
let showDefinition: NonNullable<StudioDeps['definitions']>['show'] = () => {}
let hideDefinition: NonNullable<StudioDeps['definitions']>['hide'] = () => {}
let onSchema: (snapshot: SchemaSnapshot) => void = () => {}
let schema: SchemaSnapshot | null = null

/**
 * The query "use" starts on a label. An anchor label reads bare; a reach-only
 * one is composed REACHED — through an edge from an anchor when the schema
 * shows one — because the bare scan is exactly what the preflight rejects.
 */
function useQuery(label: SchemaLabel) {
  const bare = `MATCH (n:${label.label})\nRETURN n LIMIT 25`
  if (label.anchor !== false) return bare
  const anchors = new Set(anchorLabels(schema))
  const inbound = (schema?.relationships ?? []).find((r: SchemaRel) => r.to === label.label && anchors.has(r.from))
  if (inbound) return `MATCH (a:${inbound.from})-[:${inbound.type}]->(n:${label.label})\nRETURN n LIMIT 25`
  const outbound = (schema?.relationships ?? []).find((r: SchemaRel) => r.from === label.label && anchors.has(r.to))
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
  const filter = (els.schemaFilter as Control).value.trim().toLowerCase()
  const labels = schema.labels
    .filter((l: SchemaLabel) => !filter || l.label.toLowerCase().includes(filter))
    .sort((a: SchemaLabel, b: SchemaLabel) => a.label.localeCompare(b.label))
  for (const label of labels) {
    const details = document.createElement('details')
    details.className = 'schema-label'
    const summary = document.createElement('summary')
    const name = document.createElement('span')
    name.textContent = label.label
    // The realm's own definition of the type, on hover — verbatim, because the
    // registry says what a label MEANS and what its rule of use is, and a
    // paraphrase here would drift from the source the engine reads. Our own
    // tooltip, not the `title` attribute: native tips proved unreliable in
    // this frameless window, and appeared only after the OS's dwell anyway.
    // Every label answers WHERE it comes from — its realm, or core.
    summary.addEventListener('mouseenter', () => showDefinition(summary, definitionTitle(label), label.description ?? ''))
    summary.addEventListener('mouseleave', hideDefinition)
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
      // The property's own declared description — same treatment as the label's.
      if (p.description) {
        line.addEventListener('mouseenter', () => showDefinition(line, `${label.label}.${p.name}`, p.description))
        line.addEventListener('mouseleave', hideDefinition)
      }
      props.append(line)
    }
    if (!label.exhaustive) {
      const caveat = document.createElement('div')
      caveat.className = 'prop-type'
      caveat.textContent = 'sampled, not exhaustive — absent ≠ nonexistent'
      props.append(caveat)
    }
    details.append(props)

    const touching = (schema.relationships ?? []).filter((r: SchemaRel) => r.from === label.label || r.to === label.label)
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
  onSchema(schema)
  renderSchema()
}

return {
  init(deps: StudioDeps) {
    settings = deps.settings
    setEditorText = deps.setEditorText
    scheduleValidation = deps.scheduleValidation
    showDefinition = deps.definitions?.show ?? (() => {})
    hideDefinition = deps.definitions?.hide ?? (() => {})
    onSchema = deps.onSchema ?? (() => {})
    return loadSchema()
  },
}
})()
