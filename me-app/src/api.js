// The sensor's send side. Outbound-only, on purpose: the sensor is a CLIENT of
// the appliance's API and never listens on a port. Effect verbs, when they
// exist, will ride the same direction — the sensor long-polls an outbox — so
// the appliance never needs a route back to the host.
//
// Primary transport is the sensor envelope endpoint (`/api/v1/sensor/events`,
// typed event kinds, per-event receipts). Against an older image that lacks it,
// fact sending falls back to `/api/v1/memory/remember` — same destination
// (DICE propositions), coarser receipts.

/** @typedef {import('./types').ConnectionResult} ConnectionResult */
/** @typedef {import('./types').Fact} Fact */
/** @typedef {import('./types').FocusSample} FocusSample */
/** @typedef {import('./types').SendResult} SendResult */
/** @typedef {import('./types').Settings} Settings */

const { diagnose } = require('./appliance')

const SOURCE = 'me-app'

/**
 * The machine's IANA zone, sent on every batch envelope. The sensor is the
 * most authoritative source of where the user actually is — it keeps the
 * appliance's per-user zone current for users who never open the web UI,
 * and tracks travel. Older servers ignore the extra field.
 */
const timeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone

/** @param {Settings} settings */
const auth = (settings) =>
  'Basic ' + Buffer.from(`${settings.username}:${settings.password}`).toString('base64')

/** @param {unknown} e @returns {string} */
const errorMessage = (e) => {
  if (e instanceof Error) return e.cause?.message ?? e.message
  return String(e)
}

/** @param {Settings} settings @param {string} path @param {unknown} body */
/**
 * Facts are slower than they look: each batch resolves entities and projects
 * graph edges server-side, so a first scan of a dozen long facts can outlast a
 * conversational timeout. Generous here, tight everywhere else.
 */
const FACTS_TIMEOUT_MS = 120_000

/** Parse a JSON body without letting a non-JSON one throw past the caller. */
async function readJson(res) {
  try {
    return await res.json()
  } catch {
    return null
  }
}

async function post(settings, path, body, timeoutMs = 15000, method = 'POST') {
  return fetch(`${settings.baseUrl}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: auth(settings) },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  })
}

/**
 * Cheap authenticated GET to prove the appliance is reachable and the credentials work.
 * @param {Settings} settings
 * @returns {Promise<ConnectionResult>}
 */
async function testConnection(settings) {
  let res
  try {
    res = await fetch(`${settings.baseUrl}/api/v1/hints`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(5000),
    })
  } catch (e) {
    // Unreachable is the interesting case: "connection refused" is true and
    // useless, whereas "Docker is not installed" is the actual next step.
    const why = await diagnose(settings.baseUrl)
    return { ok: false, message: why.message, action: why.action, url: why.url, state: why.state }
  }
  if (res.status === 401 || res.status === 403) {
    return { ok: false, message: 'Connected, but the username/password was rejected.' }
  }
  if (!res.ok) return { ok: false, message: `Appliance answered HTTP ${res.status}.` }
  return { ok: true, message: 'Connected and authenticated.' }
}

/**
 * Send facts through the sensor envelope; falls back to the remember endpoint on 404.
 * @param {Settings} settings
 * @param {Fact[]} facts
 * @returns {Promise<SendResult[]>}
 */
async function sendFacts(settings, facts) {
  let res
  try {
    res = await post(
      settings,
      '/api/v1/sensor/events',
      { source: SOURCE, timeZone: timeZone(), events: facts.map((f) => ({ kind: 'fact', label: f.label, text: f.text })) },
      FACTS_TIMEOUT_MS,
    )
  } catch (e) {
    const message = errorMessage(e)
    return facts.map((f) => ({ id: f.id, label: f.label, ok: false, message }))
  }
  if (res.status === 404) return sendFactsLegacy(settings, facts)
  if (!res.ok) {
    const message = `HTTP ${res.status}`
    return facts.map((f) => ({ id: f.id, label: f.label, ok: false, message }))
  }
  const body = await readJson(res)
  if (!body?.receipts) {
    return facts.map((f) => ({ id: f.id, label: f.label, ok: false, message: 'sent, but the reply was unreadable' }))
  }
  return facts.map((f, i) => {
    const receipt = body.receipts[i]
    const ok = receipt?.status === 'stored' || receipt?.status === 'duplicate'
    return { id: f.id, label: f.label, ok, message: receipt ? receipt.status + (receipt.detail ? ` (${receipt.detail})` : '') : 'no receipt' }
  })
}

/**
 * Older images: one fact at a time through the remember endpoint.
 * @param {Settings} settings
 * @param {Fact[]} facts
 * @returns {Promise<SendResult[]>}
 */
async function sendFactsLegacy(settings, facts) {
  const results = []
  for (const fact of facts) {
    try {
      const res = await post(settings, '/api/v1/memory/remember', { text: fact.text }, FACTS_TIMEOUT_MS)
      results.push({ id: fact.id, label: fact.label, ok: res.ok, message: res.ok ? 'stored' : `HTTP ${res.status}` })
    } catch (e) {
      results.push({ id: fact.id, label: fact.label, ok: false, message: errorMessage(e) })
    }
  }
  return results
}

/**
 * Push one focus observation (the ambient stream). Requires the sensor endpoint.
 *
 * WORKING is asserted only when the user is actually at the keyboard and the
 * screen is unlocked — an idle desktop showing IntelliJ is not someone working,
 * and claiming otherwise would corrupt the very interruption decisions this
 * stream exists to inform.
 *
 * @param {Settings} settings
 * @param {FocusSample} sample
 * @returns {Promise<ConnectionResult>}
 */
async function sendFocus(settings, sample) {
  const working = sample.active === true && sample.locked !== true
  let res
  try {
    res = await post(settings, '/api/v1/sensor/events', {
      source: SOURCE,
      timeZone: timeZone(),
      events: [
        {
          kind: 'context',
          stream: 'focus',
          activities: working ? ['WORKING'] : [],
          app: sample.app,
          detail: sample.detail,
          media: sample.media,
          focusMode: sample.focusMode,
          locked: sample.locked,
        },
      ],
    })
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
  if (res.status === 404) return { ok: false, message: 'This appliance version has no sensor endpoint — streaming needs a newer image.' }
  if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
  // Echo the server's own rendering of what it understood, so the UI shows what
  // was actually communicated rather than what we hoped to communicate.
  const body = await readJson(res)
  return { ok: true, message: body?.receipts?.[0]?.detail ?? (working ? 'active' : 'idle') }
}

/**
 * The documents the appliance already holds — uri + ingestedAt per document.
 * The indexer's reconcile anchor (embabel/appliance#10): server truth, so
 * there is no client ledger to drift.
 * @param {Settings} settings
 * @returns {Promise<{ok: boolean, message: string, documents: Array<{uri?: string, title?: string, ingestedAt?: string}>}>}
 */
async function listDocuments(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/documents`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, documents: [] }
    const body = await readJson(res)
    return { ok: true, message: '', documents: body?.documents ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e), documents: [] }
  }
}

/**
 * Ingest one document by URL — for the Local files panel this is a file:// URL
 * under /local, which the server reads straight off its own mount (no bytes
 * shipped). Conversion of a big PDF is genuinely slow, hence the long timeout.
 * @param {Settings} settings
 * @param {string} url
 * @returns {Promise<ConnectionResult>}
 */
async function ingestDocumentUrl(settings, url) {
  let res
  try {
    res = await post(settings, '/api/v1/documents/url', { url }, 300_000)
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
  const body = await readJson(res)
  if (!res.ok || body?.status === 'error') {
    return { ok: false, message: body?.message ?? `HTTP ${res.status}` }
  }
  return { ok: true, message: body?.title ?? 'ingested' }
}

/**
 * Upload one document's bytes for ingestion, with tags. The multipart twin of
 * ingestDocumentUrl, for files that are NOT on a shared mount — an upload has
 * no directory the server could derive a tag from, so the tags the user gave
 * are the only scope it will ever have. Conversion is slow; be patient.
 * @param {Settings} settings
 * @param {string} filename
 * @param {ArrayBuffer | Uint8Array} bytes
 * @param {string[]} tags
 */
async function uploadDocument(settings, filename, bytes, tags) {
  const form = new FormData()
  form.append('file', new Blob([bytes]), filename)
  for (const tag of tags) form.append('tags', tag)
  let res
  try {
    // No explicit Content-Type: FormData carries its own multipart boundary.
    res = await fetch(`${settings.baseUrl}/api/v1/documents/upload`, {
      method: 'POST',
      headers: { Authorization: auth(settings) },
      body: form,
      signal: AbortSignal.timeout(300_000),
    })
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
  const body = await readJson(res)
  if (!res.ok || body?.status === 'error') {
    return { ok: false, message: body?.message ?? `HTTP ${res.status}` }
  }
  return { ok: true, message: body?.title ?? 'ingested', uri: body?.uri ?? null }
}

/*
 * Saved views — named Cypher bodies living in the user's world, optionally with
 * declared parameters. Four calls: list, save, delete, and "what would this view
 * run", which returns the invocation's Cypher so the studio can show (and let the
 * user edit) it before running through the ordinary execute path.
 */

/** @param {Settings} settings */
async function listViews(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/admin/kg/views`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (res.status === 404) return { ok: false, message: 'no views endpoint — older appliance', views: [] }
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, views: [] }
    return { ok: true, message: '', views: (await readJson(res)) ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e), views: [] }
  }
}

/**
 * Save a query as a named view. The appliance validates before persisting — an
 * unrunnable body or a name a shipped view already owns comes back as a refusal
 * with the reason, never a silently broken view.
 * @param {Settings} settings
 * @param {{name: string, cypher: string, description?: string, params?: object}} spec
 */
async function saveView(settings, spec) {
  try {
    const res = await post(settings, '/api/v1/admin/kg/views', spec, 60000)
    if (res.status === 404) return { ok: false, message: 'this appliance cannot save views — it needs a newer image' }
    const body = await readJson(res)
    if (!res.ok) {
      const violations = body?.violations?.length ? ` — ${body.violations.join('; ')}` : ''
      return { ok: false, message: (body?.error ?? `HTTP ${res.status}`) + violations }
    }
    return { ok: true, message: `saved “${body?.name ?? spec.name}”` }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** @param {Settings} settings @param {string} name */
async function deleteView(settings, name) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/admin/kg/views/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (res.status === 404) return { ok: false, message: `no saved view named ${name}` }
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    return { ok: true, message: `deleted ${name}` }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * The runnable Cypher for a view invoked with [args] — args merged over the
 * declared defaults, coerced and substituted server-side. A bad argument comes
 * back as an actionable message rather than a doomed run.
 * @param {Settings} settings @param {string} name @param {object} args
 */
async function viewInvocation(settings, name, args) {
  try {
    const res = await post(settings, `/api/v1/admin/kg/views/${encodeURIComponent(name)}/invocation`, { args }, 30000)
    const body = await readJson(res)
    if (res.status === 404) return { ok: false, message: `no such view ${name}` }
    if (!res.ok) return { ok: false, message: body?.error ?? `HTTP ${res.status}` }
    return { ok: true, cypher: body?.cypher ?? '' }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * The acting user's graph schema — labels with typed properties, relationship
 * triples, virtual labels included. The same snapshot the engine's preflight
 * validates against, which is what makes it fit to drive completion: what the
 * editor offers and what validation accepts cannot disagree.
 * @param {Settings} settings
 */
async function kgSchema(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/admin/kg/schema`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (res.status === 404) return { ok: false, message: 'no schema endpoint — older appliance', labels: [], relationships: [] }
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, labels: [], relationships: [] }
    const body = await readJson(res)
    return { ok: true, message: '', labels: body?.labels ?? [], relationships: body?.relationships ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e), labels: [], relationships: [] }
  }
}

/**
 * The model that writes text-to-Cypher: the world's lensLlm override when set,
 * else the deployment default. Read for display, written by the studio's model
 * picker. LIVE — lensLlm is resolved per generation, no restart.
 * @param {Settings} settings
 */
async function lensModel(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/config/llms`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(20000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    const body = await readJson(res)
    return { ok: true, model: body?.lensLlm?.model ?? null }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** Point text-to-Cypher at a model, or clear (blank) to inherit the default. */
async function setLensModel(settings, model) {
  try {
    const res = await post(settings, '/api/v1/config/llms/lensLlm', { model: model || null }, 20000, 'PUT')
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    return { ok: true, message: model ? `text-to-Cypher → ${model}` : 'text-to-Cypher inherits the default' }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * Text-to-Cypher, generation ONLY — the appliance writes the query for a plain-
 * English question and explains what executing it will do, without running it.
 * The two-phase console pattern: generate → display (editable) → execute.
 * An LLM call, so patient; a local model can take half a minute.
 * @param {Settings} settings
 * @param {string} question
 */
async function kgGenerate(settings, question) {
  try {
    const res = await post(settings, '/api/v1/admin/kg/generate', { question }, 120_000)
    if (res.status === 404) return { ok: false, message: 'no generate endpoint — older appliance' }
    const body = await readJson(res)
    if (!res.ok) return { ok: false, message: body?.error ?? `HTTP ${res.status}` }
    return {
      ok: true,
      cypher: body?.cypher ?? '',
      explain: body?.explain ?? null,
      durationMs: body?.durationMs ?? null,
      // Server-validated before it was handed over; an older appliance sends
      // neither field and the caller falls back to validating client-side.
      valid: typeof body?.valid === 'boolean' ? body.valid : null,
      violations: body?.violations ?? [],
      attempts: body?.attempts ?? null,
    }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * The engine's strict preflight WITHOUT execution — the editor's validator.
 * @param {Settings} settings
 * @param {string} cypher
 */
async function kgValidate(settings, cypher) {
  try {
    const res = await post(settings, '/api/v1/admin/kg/validate', { cypher }, 30000)
    if (res.status === 404) return { ok: false, message: 'no validate endpoint — older appliance' }
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    const body = await readJson(res)
    return { ok: true, valid: body?.ok === true, violations: body?.violations ?? [], detail: body?.message ?? null }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * Run virtual Cypher verbatim through the appliance's engine — the advanced
 * documents surface. Same executor the appliance's own ask path uses: per-user
 * scope rewrite, preflight, live producer joins. An admin endpoint, which the
 * appliance operator account holds (the compose file's ORG_ROLE_FALLBACK).
 * Relevance joins fetch live and can take a while — be patient.
 * @param {Settings} settings
 * @param {string} cypher
 */
async function kgExecute(settings, cypher) {
  let res
  try {
    res = await post(settings, '/api/v1/admin/kg/execute', { cypher }, 180_000)
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
  if (res.status === 404) {
    return { ok: false, message: 'This appliance has no virtual-Cypher endpoint — it needs a newer image.' }
  }
  if (res.status === 401 || res.status === 403) {
    return { ok: false, message: 'The appliance refused the admin query surface for these credentials.' }
  }
  const body = await readJson(res)
  if (!res.ok) return { ok: false, message: body?.error ?? `HTTP ${res.status}` }
  return {
    ok: true,
    rows: body?.rows ?? [],
    rowCount: body?.rowCount ?? (body?.rows?.length ?? 0),
    durationMs: body?.durationMs ?? null,
    warnings: body?.warnings ?? [],
    // The engine's own failure/emptiness explanations — a 200 can still carry them.
    error: body?.error ?? null,
    hint: body?.hint ?? null,
  }
}

/*
 * Handlers — the user's TypeScript event handlers, over the same admin surface
 * the appliance's own console and MCP tools use. The studio adds an editor,
 * not a mechanism: save is tsc-gated server-side, dry-run is observe-only and
 * binds a real sampled signal, and generation reports the compiler's verdict
 * alongside the source. Every endpoint acts as the authenticated user.
 */

/** The user's handlers plus inactive realm-shipped ones available to adopt. */
async function handlersList(settings) {
  try {
    const res = await post(settings, '/api/v1/admin/handlers/list', {})
    if (res.status === 404) return { ok: false, message: 'no handlers surface — older appliance' }
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    const body = await readJson(res)
    return { ok: true, yours: body?.yours ?? [], available: body?.available ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** One handler's source and metadata, for open → edit → save. */
async function handlerOpen(settings, name) {
  try {
    const res = await post(settings, `/api/v1/admin/handlers/open?name=${encodeURIComponent(name)}`, {})
    const body = await readJson(res)
    if (!res.ok) return { ok: false, message: body?.error ?? `HTTP ${res.status}` }
    return {
      ok: true,
      name: body?.name ?? name,
      description: body?.description ?? '',
      source: body?.source ?? '',
      signalType: body?.signalType ?? '*',
      schedule: body?.schedule ?? null,
      autonomous: body?.autonomous === true,
    }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** Create or update a handler — the appliance type-checks before persisting. */
async function handlerSave(settings, spec) {
  try {
    const res = await post(settings, '/api/v1/admin/handlers/save', spec, 60_000)
    const body = await readJson(res)
    if (!res.ok) return { ok: false, message: body?.error ?? `HTTP ${res.status}` }
    return { ok: body?.ok === true, message: body?.message ?? (body?.ok ? 'saved' : 'save failed') }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** Delete a user-authored handler (realm handlers can only be disabled). */
async function handlerDelete(settings, name) {
  try {
    const res = await post(settings, `/api/v1/admin/handlers/delete?name=${encodeURIComponent(name)}`, {})
    const body = await readJson(res)
    if (!res.ok) return { ok: false, message: body?.error ?? `HTTP ${res.status}` }
    return { ok: body?.ok === true, message: body?.message ?? '' }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** Enable (adopt) or disable a handler for this user. */
async function handlerSetEnabled(settings, name, enabled) {
  try {
    const res = await post(settings, `/api/v1/admin/handlers/set-enabled?name=${encodeURIComponent(name)}&enabled=${enabled}`, {})
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    return { ok: true }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** Set (or clear, with blank) a per-user cron schedule for a handler. */
async function handlerSetSchedule(settings, name, schedule) {
  try {
    const query = `name=${encodeURIComponent(name)}${schedule ? `&schedule=${encodeURIComponent(schedule)}` : ''}`
    const res = await post(settings, `/api/v1/admin/handlers/set-schedule?${query}`, {})
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    return { ok: true }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * Run a handler OBSERVE-ONLY against the most recent real signal of the given
 * type (or a cron tick), changing nothing external. The studio's primary verb.
 */
async function handlerDryRun(settings, source, signalType) {
  try {
    const res = await post(settings, '/api/v1/admin/handlers/dry-run', { source, signalType: signalType || null }, 120_000)
    if (res.status === 404) return { ok: false, message: 'no handlers surface — older appliance' }
    const body = await readJson(res)
    if (!res.ok) return { ok: false, message: body?.error ?? `HTTP ${res.status}` }
    return {
      ok: true,
      ran: body?.ok === true,
      ranAgainst: body?.ranAgainst ?? null,
      stdout: body?.stdout ?? '',
      error: body?.error ?? null,
    }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** English → handler source, with the server's tsc verdict on the result. */
async function handlerGenerate(settings, english) {
  try {
    const res = await post(settings, '/api/v1/admin/handlers/generate', { english }, 180_000)
    if (res.status === 404) return { ok: false, message: 'no generate endpoint — older appliance' }
    const body = await readJson(res)
    if (!res.ok) return { ok: false, message: body?.error ?? `HTTP ${res.status}` }
    return {
      ok: true,
      source: body?.source ?? '',
      valid: typeof body?.valid === 'boolean' ? body.valid : null,
      violations: body?.violations ?? [],
      attempts: body?.attempts ?? null,
      durationMs: body?.durationMs ?? null,
    }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** Type-check a handler body without saving or running — the editor's debounced verdict. */
async function handlerValidate(settings, source) {
  try {
    const res = await post(settings, '/api/v1/admin/handlers/validate', { source }, 60_000)
    if (res.status === 404) return { ok: false, message: 'no validate endpoint — older appliance' }
    const body = await readJson(res)
    if (!res.ok) return { ok: false, message: body?.error ?? `HTTP ${res.status}` }
    return { ok: true, valid: body?.valid === true, violations: body?.violations ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * English → a 6-field cron expression, through the appliance's own compiler —
 * the same one the Vaadin cron drawer uses. The user never writes cron: a 200
 * carries either a cron key (parse-validated server-side) or an error key
 * worth showing verbatim, since it says how to rephrase.
 */
async function compileSchedule(settings, schedule) {
  try {
    const res = await post(settings, '/api/v1/cron/compile-schedule', { schedule }, 60_000)
    if (res.status === 404) return { ok: false, message: 'no schedule compiler — older appliance' }
    const body = await readJson(res)
    if (!res.ok) return { ok: false, message: body?.error ?? `HTTP ${res.status}` }
    return { ok: true, cron: body?.cron ?? null, error: body?.error ?? null }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * The user's typed gateway surface (interfaces.ts) — generated from the same
 * pass that types code-mode, so completion and the compiler cannot drift.
 */
async function gatewaySurface(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/apps-runtime/interfaces.ts`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, text: '' }
    return { ok: true, text: await res.text() }
  } catch (e) {
    return { ok: false, message: errorMessage(e), text: '' }
  }
}

/*
 * Realms — the units of capability a world installs. The same three endpoints
 * the Worlds console speaks: the installed list, the discovery catalog (the
 * directory's live scan of realm-* repos, grouped by provider), and install —
 * which appends the realm to the world's realms.yml and rebuilds it, so the
 * moment install returns the world is already regaining consciousness with
 * its new capability. Gaps is the honest postscript: which installed APIs
 * are inert until a key is set, and which variable unlocks each.
 */

/** @param {Settings} settings */
async function listRealms(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/realms`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, realms: [] }
    return { ok: true, message: '', realms: (await readJson(res)) ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e), realms: [] }
  }
}

/** @param {Settings} settings */
async function realmCatalog(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/directory/browse/realms`, {
      headers: { Authorization: auth(settings) },
      /* a live GitHub scan on a cold cache — allow it time */
      signal: AbortSignal.timeout(60000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, providers: [] }
    const body = await readJson(res)
    return { ok: true, message: '', providers: body?.providers ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e), providers: [] }
  }
}

/** @param {Settings} settings @param {string} repo clone URL or owner/repo */
async function installRealm(settings, repo) {
  try {
    const res = await post(settings, '/api/v1/realms', { repo }, 120_000)
    const body = await readJson(res)
    if (res.status === 409) return { ok: false, message: 'already installed' }
    if (!res.ok) return { ok: false, message: body?.detail ?? body?.message ?? `HTTP ${res.status}` }
    return { ok: true, message: body?.name ? `installed ${body.name}` : 'installed' }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * Pull a realm's checkout and rebuild the world around it. Two things the UI
 * has to be honest about, both of which come back in the server's summary:
 * a realm referenced by local path has no checkout to pull and is already
 * live, and a git-backed checkout is shared between every world on that ref,
 * so this moves more than the current world.
 * @param {Settings} settings @param {string} name installed realm name
 */
async function updateRealm(settings, name) {
  try {
    const res = await post(settings, `/api/v1/realms/${encodeURIComponent(name)}/update`, {}, 120_000)
    const body = await readJson(res)
    if (res.status === 404) return { ok: false, message: `no realm named ${name}` }
    if (!res.ok) return { ok: false, message: body?.message ?? body?.detail ?? `HTTP ${res.status}` }
    return { ok: true, message: body?.summary ?? 'updated' }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * Pull every git-backed realm at once. Each entry comes back with its own
 * status — `updated`, `local` (a path reference, already live) or `error` —
 * because a partial success is the normal case: one unreachable remote must
 * not read as "nothing updated", nor be hidden behind an overall "ok".
 * @param {Settings} settings
 */
async function updateAllRealms(settings) {
  try {
    const res = await post(settings, '/api/v1/realms/update-all', {}, 180_000)
    const body = await readJson(res)
    if (!res.ok) return { ok: false, message: body?.message ?? `HTTP ${res.status}`, results: [] }
    return { ok: true, message: '', results: body?.results ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e), results: [] }
  }
}

/**
 * The world's HTML apps — user vibe-coded, world-template and realm-shipped,
 * unioned server-side in the same order /apps/{name} serves them, so this
 * list and what a window opens can never disagree. readOnly=false marks the
 * user's own apps.
 * @param {Settings} settings
 */
async function listApps(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/artifacts/app`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, apps: [] }
    return { ok: true, message: '', apps: (await readJson(res)) ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e), apps: [] }
  }
}

/** @param {Settings} settings */
async function realmGaps(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/realms/gaps`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, inertApis: [] }
    const body = await readJson(res)
    return { ok: true, message: '', inertApis: body?.inertApis ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e), inertApis: [] }
  }
}

module.exports = {
  listThemes,
  themeCss,
  loadedLocalModels,
  modelsInUse,
  getWorldConfig,
  setChatModel,
  chatModel,
  listModels,
  getRoles,
  setRole, testConnection, sendFacts, sendFocus, listDocuments, ingestDocumentUrl, kgExecute,
  kgSchema, kgValidate, kgGenerate, lensModel, setLensModel,
  listViews, saveView, deleteView, viewInvocation,
  handlersList, handlerOpen, handlerSave, handlerDelete, handlerSetEnabled, handlerSetSchedule,
  handlerDryRun, handlerGenerate, handlerValidate, gatewaySurface, compileSchedule,
  uploadDocument, listRealms, realmCatalog, installRealm, updateRealm, updateAllRealms, realmGaps, listApps }

// ---------------------------------------------------------------------------
// Models. Everything here is the appliance's own REST surface — the app adds a
// picker, not a mechanism.
// ---------------------------------------------------------------------------

/**
 * Every model the appliance can use, with its provider. Local ones (LM Studio,
 * Ollama) are the user's own hardware: free to run, and nothing leaves the
 * machine.
 * @param {Settings} settings
 */
async function listModels(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/config/models`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, models: [], default: '' }
    const body = await readJson(res)
    return {
      ok: true,
      message: '',
      models: body?.modelsWithProvider ?? [],
      default: body?.default ?? '',
    }
  } catch (e) {
    return { ok: false, message: errorMessage(e), models: [], default: '' }
  }
}

/** The role→model overrides this world has set. Empty means "inherit the defaults". */
async function getRoles(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/config/llm-roles`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, roles: {} }
    return { ok: true, message: '', roles: (await readJson(res)) ?? {} }
  } catch (e) {
    return { ok: false, message: errorMessage(e), roles: {} }
  }
}

/**
 * Point one role at a model, or clear it with an empty model to fall back to the
 * appliance default. Takes effect immediately: the world re-reads its config on
 * every access, so the next piece of work uses it — no restart.
 */
async function setRole(settings, roleId, model) {
  try {
    const res = await post(settings, `/api/v1/config/llm-roles/${encodeURIComponent(roleId)}`, { model }, 15000, 'PUT')
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    return { ok: true, message: model ? `now ${model}` : 'cleared' }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** The world's own config — needed to know HOW chat is currently pinned. */
/**
 * The appliance's installed themes. Same service the Vaadin UI's Theme tab uses
 * (`GET /api/v1/themes`), so a theme chosen anywhere is the same list here.
 */
async function listThemes(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/themes`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(15000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, themes: [] }
    return { ok: true, message: '', themes: (await readJson(res)) ?? [] }
  } catch (e) {
    return { ok: false, message: errorMessage(e), themes: [] }
  }
}

/**
 * One theme's stylesheet. TEXT, not JSON — this endpoint serves `text/css`, and
 * the renderer cannot fetch it itself (no HTTP in the renderer, by design), so
 * it travels over IPC and is injected into `<style id="theme">`.
 */
async function themeCss(settings, name) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/themes/${encodeURIComponent(name)}.css`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(15000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, css: '' }
    return { ok: true, message: '', css: await res.text() }
  } catch (e) {
    return { ok: false, message: errorMessage(e), css: '' }
  }
}

async function getWorldConfig(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/config`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(30000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, config: {} }
    return { ok: true, message: '', config: (await readJson(res)) ?? {} }
  } catch (e) {
    return { ok: false, message: errorMessage(e), config: {} }
  }
}

/**
 * Point CHAT at a model — the setting people mean by "change the model".
 *
 * One call, because the appliance owns the rule that makes this fiddly: a chat
 * config pinned to a ROLE ignores any model written beside it, so the server
 * clears the role as it sets the model. The app deliberately does not
 * re-implement that precedence; it would drift.
 */
async function setChatModel(settings, model) {
  try {
    const res = await post(settings, '/api/v1/config/chat-model', { model }, 15000, 'PUT')
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    const body = await readJson(res)
    return { ok: true, message: body?.inherited ? 'chat follows the default again' : `chat → ${body?.model}` }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/** What chat is pinned to right now: an explicit model, a role, or inherited. */
async function chatModel(settings) {
  const [world, roles] = await Promise.all([getWorldConfig(settings), getRoles(settings)])
  const chat = world.config?.chat ?? {}
  if (chat.model) return { model: chat.model, via: 'pinned' }
  if (chat.role) return { model: roles.roles?.[chat.role]?.model ?? '', via: `role ${chat.role}`, role: chat.role }
  return { model: '', via: 'the default' }
}

/**
 * Which models are actually in use, and whether any is hosted. The appliance
 * resolves this — role-versus-model precedence lives there, and a banner making
 * a privacy claim must not be computed from a second, drifting copy of the rules.
 */
async function modelsInUse(settings) {
  try {
    const res = await fetch(`${settings.baseUrl}/api/v1/models/in-use`, {
      headers: { Authorization: auth(settings) },
      signal: AbortSignal.timeout(20000),
    })
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` }
    const body = await readJson(res)
    return { ok: true, ...body }
  } catch (e) {
    return { ok: false, message: errorMessage(e) }
  }
}

/**
 * Which local models are LOADED, from LM Studio's own view.
 *
 * A model being listed does not mean it can answer: LM Studio advertises every
 * model in the library, but one that is not loaded replies "Model has not
 * started loading" to any request. Sixteen of seventeen entries in a picker
 * being duds is not a menu, it is a trap — so the app asks the question the
 * appliance cannot (it is on the host side of the boundary, and this endpoint
 * is LM Studio's own, not the OpenAI-compatible one).
 *
 * Returns a Map of model id → true when loaded. Empty when LM Studio is absent
 * or the endpoint is unknown to it, in which case the UI simply says nothing.
 */
async function loadedLocalModels() {
  for (const base of ['http://127.0.0.1:1234', 'http://localhost:1234']) {
    try {
      const res = await fetch(`${base}/api/v0/models`, { signal: AbortSignal.timeout(2500) })
      if (!res.ok) continue
      const body = await readJson(res)
      const loaded = new Map()
      for (const m of body?.data ?? []) {
        if (m?.id) loaded.set(m.id, m.state === 'loaded')
      }
      return loaded
    } catch {
      /* try the next spelling, then give up quietly */
    }
  }
  return new Map()
}
