// Renderer: the consent surface. Scan → review (check/uncheck) → send → receipt.
//
// Loaded straight off index.html as a plain browser script — no module system
// here (`nodeIntegration: false`), so no require/import. Everything it may do
// arrives through `window.me`, the narrow bridge preload.js exposes.

/** @param {string} id */
const $ = (id) => document.getElementById(id)

const baseUrlInput = $('baseUrl')
const usernameInput = $('username')
const passwordInput = $('password')
const connectionStatus = $('connection-status')
const advice = $('connection-advice')
const factsSection = $('facts')
const factList = $('fact-list')
const sendButton = $('send')
const receipt = $('receipt')

let facts = []

function currentSettings() {
  let baseUrl = baseUrlInput.value.trim().replace(/\/+$/, '')
  // A URL without a scheme ("localhost:4242") makes fetch throw before any
  // connection attempt — normalize rather than error.
  if (baseUrl && !/^https?:\/\//i.test(baseUrl)) baseUrl = `http://${baseUrl}`
  baseUrlInput.value = baseUrl
  return {
    baseUrl,
    username: usernameInput.value.trim(),
    password: passwordInput.value,
  }
}

/** @param {HTMLElement} el @param {boolean | null} ok @param {string} message */
function setStatus(el, ok, message) {
  el.textContent = message
  el.className = ok === null ? 'status' : ok ? 'status ok' : 'status error'
}

async function init() {
  const settings = await window.me.loadSettings()
  baseUrlInput.value = settings.baseUrl
  usernameInput.value = settings.username
  passwordInput.value = settings.password
  verbsSee.checked = Boolean(settings.verbs?.see)
  verbsAct.checked = Boolean(settings.verbs?.act)
  renderMounts(await window.me.mountsState())
  // The indexing watcher runs in the background across window closes and app
  // relaunches — say what it's up to whenever the panel opens.
  const indexing = await window.me.indexingState()
  if (indexing.enabled) {
    if (indexing.running) followIndexing('')
    else if (indexing.message) setStatus(mountStatus, indexing.failed === 0, indexing.message)
  }
}

$('test').addEventListener('click', async () => {
  setStatus(connectionStatus, null, 'Connecting…')
  if (advice) advice.innerHTML = ''
  const settings = currentSettings()
  const result = await window.me.testConnection(settings)
  setStatus(connectionStatus, result.ok, result.message)
  if (result.ok) {
    await window.me.saveSettings(settings)
    setPill(true, settings.baseUrl.replace(/^https?:\/\//, ''))
    // Nothing more to do here: fold the form away and let the tabs have the room.
    connPanel.hidden = true
    return
  }
  setPill(false, 'not connected')
  // A failure the app can explain is worth more screen than one it can't:
  // "connection refused" is a dead end, "Docker is not installed" is a path.
  if (result.action && advice) {
    const p = document.createElement('p')
    p.className = 'hint'
    p.textContent = result.action
    advice.append(p)
  }
  if (result.url && advice) {
    const link = document.createElement('button')
    link.textContent = 'Get Docker Desktop'
    link.addEventListener('click', () => void window.me.openExternal(result.url))
    advice.append(link)
  }
})

const streamToggle = $('stream-toggle')
const tier1Toggle = $('tier1-toggle')
const streamStatus = $('stream-status')
const grantsStatus = $('grants-status')

async function applyStream() {
  const settings = currentSettings()
  await window.me.saveSettings(settings)
  const state = await window.me.setStream(settings, streamToggle.checked, tier1Toggle.checked)
  setStatus(streamStatus, state.running ? true : null, state.running ? 'streaming…' : 'off')
}

streamToggle.addEventListener('change', applyStream)
tier1Toggle.addEventListener('change', async () => {
  // Reading a tab or a track is meaningless without the stream that carries it.
  if (tier1Toggle.checked) streamToggle.checked = true
  await applyStream()
})

$('grants').addEventListener('click', async () => {
  setStatus(grantsStatus, null, 'checking (this may prompt)…')
  const states = await window.me.grantStates()
  const granted = states.filter((g) => g.state === 'granted').map((g) => g.app)
  const denied = states.filter((g) => g.state === 'denied').map((g) => g.app)
  const parts = []
  if (granted.length) parts.push(`granted: ${granted.join(', ')}`)
  if (denied.length) parts.push(`DENIED: ${denied.join(', ')}`)
  if (!parts.length) parts.push('none of the supported apps are running')
  setStatus(grantsStatus, denied.length === 0 ? (granted.length ? true : null) : false, parts.join(' · '))
})

$('open-settings').addEventListener('click', () => void window.me.openAutomationSettings())

// What the assistant may ask this machine to do, by GROUP — persisted, and the
// outbox loop starts, stops and republishes its catalog with it immediately.
const verbsSee = $('verbs-see')
const verbsAct = $('verbs-act')
const verbsStatus = $('verbs-status')

async function applyVerbs() {
  const settings = currentSettings()
  await window.me.saveSettings(settings)
  const groups = { see: verbsSee.checked, act: verbsAct.checked }
  const state = await window.me.setVerbs(settings, groups)
  const on = Object.entries(groups).filter(([, v]) => v).map(([g]) => g)
  setStatus(verbsStatus, state.running ? true : null,
    state.running ? `listening for assistant requests (${on.join(', ')})` : 'not listening')
}

verbsSee.addEventListener('change', applyVerbs)
verbsAct.addEventListener('change', applyVerbs)

// Reflect the stream's latest heartbeat while the window is open.
setInterval(async () => {
  const state = await window.me.streamState()
  if (!state.running) return
  streamToggle.checked = true
  tier1Toggle.checked = state.tier1
  const isError = state.lastMessage.startsWith('error')
  const when = state.lastAt ? ` at ${new Date(state.lastAt).toLocaleTimeString()}` : ''
  setStatus(streamStatus, !isError, `${state.lastMessage}${when}`)
  // A denied grant is silent at the OS level; say so, or the stream just looks thin.
  if (state.denied.length > 0) {
    setStatus(grantsStatus, false, `permission refused for ${state.denied.join(', ')} — grant it in System Settings`)
  }
}, 5000)

$('scan').addEventListener('click', async () => {
  factList.innerHTML = ''
  receipt.innerHTML = ''
  factsSection.hidden = false
  factList.textContent = 'Scanning…'
  try {
    facts = await window.me.scan({ browserHistory: $('history-toggle').checked })
  } catch (e) {
    factList.textContent = `Scan failed: ${e instanceof Error ? e.message : String(e)}`
    sendButton.disabled = true
    return
  }
  factList.textContent = ''
  if (facts.length === 0) {
    factList.textContent = 'Nothing found to report.'
    sendButton.disabled = true
    return
  }
  for (const fact of facts) {
    const row = document.createElement('label')
    row.className = 'fact'
    const checkbox = document.createElement('input')
    checkbox.type = 'checkbox'
    checkbox.checked = true
    checkbox.dataset['factId'] = fact.id
    const body = document.createElement('div')
    const title = document.createElement('strong')
    title.textContent = fact.label
    const text = document.createElement('p')
    text.textContent = fact.text
    body.append(title, text)
    row.append(checkbox, body)
    factList.append(row)
  }
  sendButton.disabled = false
})

sendButton.addEventListener('click', async () => {
  const checkedIds = new Set(
    [...factList.querySelectorAll('input[type=checkbox]:checked')].map((c) => c.dataset['factId']),
  )
  const selected = facts.filter((f) => checkedIds.has(f.id))
  if (selected.length === 0) return
  sendButton.disabled = true
  receipt.textContent = `Sending ${selected.length} fact(s)… (a first scan can take a minute)`
  const settings = currentSettings()
  await window.me.saveSettings(settings)
  let results
  try {
    results = await window.me.sendFacts(settings, selected)
  } catch (e) {
    // Anything escaping the send path lands here rather than leaving the button
    // dead and the user guessing.
    receipt.innerHTML = ''
    const line = document.createElement('div')
    line.className = 'status error'
    line.textContent = `Send failed: ${e instanceof Error ? e.message : String(e)}`
    receipt.append(line)
    sendButton.disabled = false
    return
  }
  receipt.innerHTML = ''
  for (const r of results) {
    const line = document.createElement('div')
    line.className = r.ok ? 'status ok' : 'status error'
    line.textContent = `${r.label}: ${r.message}`
    receipt.append(line)
  }
  const okCount = results.filter((r) => r.ok).length
  const summary = document.createElement('div')
  summary.className = 'summary'
  summary.textContent = `${okCount}/${results.length} sent. Your appliance remembers these — ask it "what do you know about my machine?"`
  receipt.append(summary)
  sendButton.disabled = false
})

// Local files: pick folders → the main process rewrites the compose override →
// Apply recreates the assistant container with them mounted under /local.

const mountList = $('mount-list')
const mountAdd = $('mount-add')
const mountApply = $('mount-apply')
const mountStatus = $('mount-status')

/** @param {import('./types').MountsState} state */
function renderMounts(state) {
  mountList.innerHTML = ''
  mountAdd.disabled = !state.supported
  mountApply.disabled = !state.supported
  if (state.message) setStatus(mountStatus, state.supported ? null : false, state.message)
  for (const mount of state.mounts) {
    const row = document.createElement('div')
    row.className = 'mount'
    const host = document.createElement('span')
    host.className = 'path'
    host.textContent = mount.host
    const target = document.createElement('span')
    target.className = 'target'
    target.textContent = `→ ${mount.target} (read-only)`
    // Sharing makes the folder QUERYABLE (live metadata and grep); indexing is
    // the separate, deliberate opt-in that embeds its documents into the graph.
    const index = document.createElement('label')
    index.className = 'check'
    const tick = document.createElement('input')
    tick.type = 'checkbox'
    tick.checked = mount.index
    tick.addEventListener('change', async () => {
      const ticked = tick.checked
      renderMounts(await window.me.setMountIndex(mount.host, ticked))
      // An already-mounted folder starts indexing on tick, no Apply needed —
      // the main process kicks the watcher when the mount is live. Show it.
      if (ticked && (await window.me.indexingState()).enabled) followIndexing('')
    })
    index.append(tick, document.createTextNode('index contents'))
    const remove = document.createElement('button')
    remove.textContent = 'Remove'
    remove.addEventListener('click', async () => renderMounts(await window.me.removeMount(mount.host)))
    row.append(host, target, index, remove)
    mountList.append(row)
  }
}

/**
 * Follow an indexing sweep to its end, narrating progress in the status line.
 * @param {string} prefix — context for the final line (e.g. the Apply result), or ''
 */
function followIndexing(prefix) {
  const poll = setInterval(async () => {
    const s = await window.me.indexingState()
    if (s.running) {
      const progress =
        s.phase === 'waiting' ? 'waiting for the appliance…'
        : s.phase === 'scanning' ? 'checking what changed…'
        : `indexing ${s.done}/${s.total}${s.currentFile ? ` — ${s.currentFile}` : ''}`
      setStatus(mountStatus, null, progress)
      return
    }
    clearInterval(poll)
    setStatus(mountStatus, s.failed === 0, prefix ? `${prefix} · ${s.message}` : s.message)
    mountApply.disabled = false
  }, 2000)
}

mountAdd.addEventListener('click', async () => renderMounts(await window.me.addMount()))

mountApply.addEventListener('click', async () => {
  mountApply.disabled = true
  const state = await window.me.mountsState()
  // Tell the appliance where its files will be BEFORE the restart, while it is
  // still up to hear it — the memory lands in the graph, which the restart
  // doesn't touch. Skipped without credentials; the mounts still work, the
  // assistant just isn't told where to look.
  let told = ''
  if (state.supported && state.mounts.length > 0 && usernameInput.value.trim()) {
    const settings = currentSettings()
    await window.me.saveSettings(settings)
    const results = await window.me.sendFacts(
      settings,
      state.mounts.map((m) => ({
        id: `mount:${m.target}`,
        label: 'shared folder',
        text:
          `The local folder ${m.host} on this Mac is shared with the assistant: ` +
          `it is mounted read-only inside the appliance container at ${m.target}, ` +
          `and its files are queryable as File and Folder nodes reached from the user anchor.` +
          (m.index ? ' Its documents are also being indexed into the document knowledge base.' : ''),
      })),
    )
    const okCount = results.filter((r) => r.ok).length
    told = ` · told the assistant about ${okCount}/${results.length} folder(s)`
  }
  setStatus(mountStatus, null, 'Recreating the assistant container…')
  const result = await window.me.applyMounts()
  setStatus(mountStatus, result.ok, result.message + (result.ok ? told : ''))
  // Ticked folders: once the restarted assistant answers again, push their
  // documents through its ingestion pipeline (file:// URLs under /local — the
  // server reads its own mount; nothing is uploaded). Progress via polling.
  if (result.ok && state.mounts.some((m) => m.index) && usernameInput.value.trim()) {
    await window.me.startIndexing(currentSettings())
    followIndexing(result.message)
    return // followIndexing re-enables the button when the run ends
  }
  mountApply.disabled = false
})

void init().then(async () => {
  // A silent probe with what was saved, so the pill starts out truthful and the
  // form only appears when it is actually needed.
  const settings = currentSettings()
  if (!settings.username || !settings.password) {
    setPill(null, 'not connected')
    connPanel.hidden = false
    return
  }
  const result = await window.me.testConnection(settings)
  setPill(result.ok, result.ok ? settings.baseUrl.replace(/^https?:\/\//, '') : 'not connected')
  if (!result.ok) {
    connPanel.hidden = false
    setStatus(connectionStatus, false, result.message)
  }
})

// ---------------------------------------------------------------------------
// Tabs. The sensor and the documents are different jobs; showing both at once
// is what made this window read as a settings screen.
// ---------------------------------------------------------------------------

// The connection pill: shows the state, and is the way to the form. Hidden
// behind it because a working connection is something you check, not something
// you do — and the tabs deserve the top of the window.
const connPill = $('conn-pill')
const connPanel = $('conn-panel')
const connDot = $('conn-dot')
const connPillText = $('conn-pill-text')

connPill.addEventListener('click', () => {
  connPanel.hidden = !connPanel.hidden
})

/** Reflect a connection attempt on the pill, so the collapsed state still tells the truth. */
function setPill(ok, text) {
  connPill.classList.toggle('ok', ok === true)
  // null is "we have not tried yet" — grey, not red. An app that shouts failure
  // before it has attempted anything trains people to ignore it.
  connPill.classList.toggle('error', ok === false)
  connPillText.textContent = text
}

for (const tab of document.querySelectorAll('.tab')) {
  tab.addEventListener('click', () => {
    for (const t of document.querySelectorAll('.tab')) t.classList.toggle('is-on', t === tab)
    for (const panel of document.querySelectorAll('.tabpanel')) {
      panel.hidden = panel.dataset['panel'] !== tab.dataset['tab']
    }
    // Chat wakes on first visit — no stream, no polling until someone looks.
    if (tab.dataset['tab'] === 'chat') startChat()
  })
}

// ---------------------------------------------------------------------------
// Chat — deliberately the simplest possible surface over the real protocol,
// the same one the Worlds console and the TUI speak. History is the source of
// truth (the SSE stream only animates liveness): replies broadcast to every
// surface bound to the session, so a stream-only client misses turns whenever
// the web UI or TUI is also open.
// ---------------------------------------------------------------------------

const chatScroll = $('chat-scroll')
const chatInput = $('chat-input')
const chatSendButton = $('chat-send')
const chatStatus = $('chat-status')

let chatStarted = false
/** Renderer-side "thinking…" line; cleared by the next message/done event. */
let chatWorking = null

function paintChat(messages) {
  chatScroll.innerHTML = ''
  if (messages.length === 0) {
    const empty = document.createElement('div')
    empty.className = 'chat-empty'
    empty.textContent = 'Say something — same assistant, smallest possible window.'
    chatScroll.append(empty)
  }
  for (const m of messages) {
    const row = document.createElement('div')
    row.className = `chat-msg ${m.role}`
    row.textContent = m.content
    chatScroll.append(row)
  }
  if (chatWorking) {
    const line = document.createElement('div')
    line.className = 'chat-working'
    line.textContent = chatWorking
    chatScroll.append(line)
  }
  chatScroll.scrollTop = chatScroll.scrollHeight
}

async function syncChat() {
  const result = await window.me.chatHistory(currentSettings())
  if (!result.ok) {
    setStatus(chatStatus, false, `history: ${result.message}`)
    return
  }
  paintChat(result.messages)
}

function startChat() {
  if (chatStarted) return
  chatStarted = true
  const settings = currentSettings()
  if (!settings.username || !settings.password) {
    setStatus(chatStatus, false, 'Connect to your appliance first (the pill, top right).')
    chatStarted = false
    return
  }
  void window.me.chatOpen(settings)
  void syncChat()
  setInterval(syncChat, 3000)
}

window.me.onChatEvent((event) => {
  switch (event.type) {
    case 'connected':
      setStatus(chatStatus, true, event.data?.assistantName ? `connected · ${event.data.assistantName}` : 'connected')
      break
    case 'user':
      chatWorking = 'thinking…'
      break
    case 'progress':
      chatWorking = event.data?.message ?? event.data?.text ?? 'working…'
      void syncChat()
      break
    case 'message':
    case 'assistant':
    case 'done':
      chatWorking = null
      void syncChat()
      break
    case 'stream-closed':
    case 'stream-error':
      setStatus(chatStatus, false, 'stream down — reconnecting…')
      break
  }
})

async function sendChat() {
  const text = chatInput.value.trim()
  if (!text) return
  chatInput.value = ''
  chatWorking = 'thinking…'
  const settings = currentSettings()
  await window.me.saveSettings(settings)
  const result = await window.me.chatSend(settings, text)
  if (!result.ok) {
    chatWorking = null
    setStatus(chatStatus, false, result.message)
  }
  void syncChat()
}

chatSendButton.addEventListener('click', () => void sendChat())
chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') void sendChat()
})

// ---------------------------------------------------------------------------
// Ask your documents.
//
// The answer arrives with [n] markers the SERVER has already verified against
// what it actually retrieved, so rendering them as buttons is safe: every one
// resolves to a source below.
// ---------------------------------------------------------------------------

const askQuestion = $('ask-question')
const askStatus = $('ask-status')
const askAnswer = $('ask-answer')
const askSources = $('ask-sources')
const askButton = $('ask')

/** Turn [1] into a chip that jumps to its source. */
function renderAnswer(text) {
  askAnswer.innerHTML = ''
  const parts = text.split(/(\[\d{1,3}\])/g)
  for (const part of parts) {
    const match = part.match(/^\[(\d{1,3})]$/)
    if (!match) {
      askAnswer.append(document.createTextNode(part))
      continue
    }
    const chip = document.createElement('button')
    chip.className = 'cite'
    chip.textContent = match[1]
    chip.title = 'Show this source'
    chip.addEventListener('click', () => {
      const card = document.getElementById(`source-${match[1]}`)
      if (!card) return
      card.scrollIntoView({ behavior: 'smooth', block: 'center' })
      card.classList.add('flash')
      setTimeout(() => card.classList.remove('flash'), 1400)
    })
    askAnswer.append(chip)
  }
}

/** One source card: where it lives, what it said, and how to go look. */
function renderSource(source) {
  const card = document.createElement('div')
  card.className = 'source'
  card.id = `source-${source.n}`

  const head = document.createElement('div')
  head.className = 'head'
  const n = document.createElement('span')
  n.className = 'n'
  n.textContent = source.n
  const title = document.createElement('span')
  title.className = 'title'
  title.textContent = source.title || source.where.label
  head.append(n, title)
  // Show the date the user is actually filtering on, and SAY which it is —
  // "modified 3 Mar" and "ingested 3 Mar" mean very different things.
  const field = $('ask-datefield').value
  const shown =
    field === 'created' ? ['created', source.createdAt]
    : field === 'ingested' ? ['ingested', source.ingestedAt]
    : ['modified', source.modifiedAt]
  const stamp = shown[1] ?? source.modifiedAt ?? source.ingestedAt
  if (stamp) {
    const meta = document.createElement('span')
    meta.className = 'meta'
    const label = shown[1] ? shown[0] : source.modifiedAt ? 'modified' : 'ingested'
    meta.textContent = `${label} ${new Date(stamp).toLocaleDateString()}`
    head.append(meta)
  }
  card.append(head)

  const where = document.createElement('div')
  where.className = 'where'
  where.textContent = source.where.label
  card.append(where)

  for (const passage of source.passages || []) {
    const quote = document.createElement('blockquote')
    quote.textContent = passage
    card.append(quote)
  }

  // Local files reveal in Finder — the question is "which file said that", and
  // showing it in its folder answers that better than launching Preview over
  // the top of this window. Web sources go back where they came from.
  const actions = document.createElement('div')
  actions.className = 'actions'
  if (source.where.kind === 'local' && source.where.exists) {
    const reveal = document.createElement('button')
    reveal.textContent = 'Show in Finder'
    reveal.addEventListener('click', () => void window.me.revealFile(source.where.path))
    const open = document.createElement('button')
    open.textContent = 'Open'
    open.addEventListener('click', () => void window.me.openFile(source.where.path))
    actions.append(reveal, open)
  } else if (source.where.kind === 'local') {
    // Mapped to this Mac, but not there any more — say so rather than offering
    // a button that opens nothing.
    const gone = document.createElement('span')
    gone.className = 'status error'
    gone.textContent = 'no longer at that path'
    actions.append(gone)
  } else if (source.where.kind === 'url') {
    const open = document.createElement('button')
    open.textContent = 'Open source'
    open.addEventListener('click', () => void window.me.openExternal(source.where.url))
    actions.append(open)
  }
  if (actions.children.length > 0) card.append(actions)
  return card
}

async function runAsk() {
  const question = askQuestion.value.trim()
  if (!question) return
  askButton.disabled = true
  askAnswer.innerHTML = ''
  askSources.innerHTML = ''
  setStatus(askStatus, null, 'Searching your documents…')
  const settings = currentSettings()
  await window.me.saveSettings(settings)

  let result
  try {
    result = await window.me.askDocuments(settings, {
      question,
      dateField: $('ask-datefield').value,
      from: $('ask-from').value || undefined,
      to: $('ask-to').value || undefined,
      topK: Number($('ask-topk').value) || undefined,
    })
  } catch (e) {
    setStatus(askStatus, false, `Ask failed: ${e instanceof Error ? e.message : String(e)}`)
    askButton.disabled = false
    return
  }

  askButton.disabled = false
  if (!result.ok) {
    setStatus(askStatus, false, result.message)
    return
  }
  if (!result.sources.length) {
    setStatus(askStatus, null, 'Nothing in your documents matched that.')
    return
  }

  const counts = [`${result.sources.length} document(s)`]
  // A stripped citation means the answer was less grounded than it looked. The
  // server counts them; hiding that here would defeat the point of counting.
  if (result.unresolvedCitations > 0) counts.push(`${result.unresolvedCitations} unverifiable citation(s) removed`)
  setStatus(askStatus, result.unresolvedCitations === 0, counts.join(' · '))

  if (result.answer) renderAnswer(result.answer)
  for (const source of result.sources) askSources.append(renderSource(source))
}

askButton.addEventListener('click', () => void runAsk())
askQuestion.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') void runAsk()
})

// ---------------------------------------------------------------------------
// Models: which model does what.
//
// Every mechanism here is the appliance's own — this is a picker over
// /api/v1/config/models and /api/v1/config/llm-roles, nothing more. The value
// it adds is knowing which models are LOCAL, and saying what a change costs:
// role changes are live, a newly-started local model needs a restart.
// ---------------------------------------------------------------------------

// The roles worth putting in front of someone, in the order they matter. The
// appliance has more; a picker listing all of them is a picker nobody reads.
const ROLES = [
  ['chat_best', 'Chat — the model you talk to'],
  ['chat_cheap', 'Chat, cheaper — short or routine turns'],
  ['best', 'Hardest work — planning, judgement'],
  ['workhorse', 'Everyday work'],
  ['cheap', 'Bulk work — classification, routing'],
  ['code_best', 'Writing code'],
  ['agentic_rag', 'Searching your documents'],
]

const LOCAL_PROVIDERS = new Set(['LM Studio', 'Ollama'])
const modelsStatus = $('models-status')
const roleList = $('role-list')

async function loadModels() {
  setStatus(modelsStatus, null, 'Loading…')
  roleList.innerHTML = ''
  const settings = currentSettings()
  const [listed, roles] = await Promise.all([window.me.listModels(settings), window.me.getRoles(settings)])
  if (!listed.ok) {
    setStatus(modelsStatus, false, listed.message)
    return
  }
  const local = listed.models.filter((m) => LOCAL_PROVIDERS.has(m.provider))
  setStatus(
    modelsStatus,
    true,
    `${listed.models.length} model(s) · ${local.length} on this Mac · default ${listed.default}`,
  )

  for (const [roleId, description] of ROLES) {
    const row = document.createElement('div')
    row.className = 'role'

    const what = document.createElement('div')
    what.className = 'what'
    const name = document.createElement('div')
    name.className = 'name'
    name.textContent = roleId
    const says = document.createElement('div')
    says.className = 'says'
    says.textContent = description
    what.append(name, says)

    const select = document.createElement('select')
    const inherit = document.createElement('option')
    inherit.value = ''
    inherit.textContent = `— appliance default —`
    select.append(inherit)
    // Grouped, so the user's own models are not lost among the hosted ones.
    for (const group of ['LM Studio', 'Ollama']) {
      const models = listed.models.filter((m) => m.provider === group)
      if (models.length === 0) continue
      const optgroup = document.createElement('optgroup')
      optgroup.label = `${group} — on this Mac`
      for (const model of models) {
        const option = document.createElement('option')
        option.value = model.name
        option.textContent = model.name
        optgroup.append(option)
      }
      select.append(optgroup)
    }
    const hostedGroup = document.createElement('optgroup')
    hostedGroup.label = 'Hosted — billed to your key'
    for (const model of listed.models.filter((m) => !LOCAL_PROVIDERS.has(m.provider))) {
      const option = document.createElement('option')
      option.value = model.name
      option.textContent = `${model.name} (${model.provider})`
      hostedGroup.append(option)
    }
    select.append(hostedGroup)
    select.value = roles.roles?.[roleId]?.model ?? ''

    const badge = document.createElement('span')
    badge.className = 'local'
    const markLocal = () => {
      const chosen = listed.models.find((m) => m.name === select.value)
      badge.textContent = chosen && LOCAL_PROVIDERS.has(chosen.provider) ? 'local' : ''
    }
    markLocal()

    select.addEventListener('change', async () => {
      setStatus(modelsStatus, null, `Setting ${roleId}…`)
      const result = await window.me.setRole(currentSettings(), roleId, select.value)
      markLocal()
      setStatus(
        modelsStatus,
        result.ok,
        result.ok ? `${roleId}: ${result.message} — in effect now, no restart` : result.message,
      )
    })

    row.append(what, select, badge)
    roleList.append(row)
  }
}

$('models-refresh').addEventListener('click', () => void loadModels())

// Load once when the tab is first opened, not at startup: it costs two calls to
// the appliance, and most sessions never touch this tab.
let modelsLoaded = false
for (const tab of document.querySelectorAll('.tab')) {
  tab.addEventListener('click', () => {
    if (tab.dataset['tab'] === 'models' && !modelsLoaded) {
      modelsLoaded = true
      void loadModels()
    }
  })
}
