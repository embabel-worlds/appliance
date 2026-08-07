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
  renderMounts(await window.me.mountsState())
}

$('test').addEventListener('click', async () => {
  setStatus(connectionStatus, null, 'Testing…')
  const settings = currentSettings()
  const result = await window.me.testConnection(settings)
  setStatus(connectionStatus, result.ok, result.message)
  if (result.ok) await window.me.saveSettings(settings)
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
    const remove = document.createElement('button')
    remove.textContent = 'Remove'
    remove.addEventListener('click', async () => renderMounts(await window.me.removeMount(mount.host)))
    row.append(host, target, remove)
    mountList.append(row)
  }
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
          `and its documents can be read and indexed from that path.`,
      })),
    )
    const okCount = results.filter((r) => r.ok).length
    told = ` · told the assistant about ${okCount}/${results.length} folder(s)`
  }
  setStatus(mountStatus, null, 'Recreating the assistant container…')
  const result = await window.me.applyMounts()
  setStatus(mountStatus, result.ok, result.message + (result.ok ? told : ''))
  mountApply.disabled = false
})

void init()
