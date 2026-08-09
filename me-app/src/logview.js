/*
 * The log window's renderer.
 *
 * Everything here is untrusted text from a container, so every line is painted
 * with textContent — never innerHTML, never a template. The only interpretation
 * applied is a severity colour, and that is a class, not markup.
 *
 * Two bounds keep a chatty container from taking the window down with it: the
 * kept-lines cap (the DOM is not an archive — `docker logs` is), and the fact
 * that the tail size is chosen, not unbounded.
 */

const MAX_LINES = 5000
const $ = (id) => document.getElementById(id)

const containerEl = $('container')
const tailEl = $('tail')
const filterEl = $('filter')
const followButton = $('follow')
const logEl = $('log')
const statusEl = $('status')

/* Every line received, filtered or not — so changing the filter can re-render
   what is already here instead of waiting for the container to say it again. */
let lines = []
/* A chunk can end mid-line; hold the remainder until its newline arrives. */
let partial = ''
let following = true

const setStatus = (text) => (statusEl.textContent = text)

/** Severity, by the shape every JVM log line in this appliance happens to have. */
function severity(line) {
  if (/\b(ERROR|FATAL|SEVERE)\b/.test(line)) return 'error'
  if (/\bWARN(ING)?\b/.test(line)) return 'warn'
  return ''
}

const matches = (line) => {
  const needle = filterEl.value.trim().toLowerCase()
  return !needle || line.toLowerCase().includes(needle)
}

/** Was the view scrolled to the end before we appended? Only then do we chase it. */
const atBottom = () => logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40

function appendLine(line) {
  const el = document.createElement('div')
  el.className = `line ${severity(line)}`.trim()
  el.textContent = line
  logEl.append(el)
}

function note(text) {
  const el = document.createElement('div')
  el.className = 'note'
  el.textContent = text
  logEl.append(el)
}

/** Repaint from the kept lines — after a filter change or a clear. */
function render() {
  logEl.textContent = ''
  for (const line of lines) if (matches(line)) appendLine(line)
  logEl.scrollTop = logEl.scrollHeight
}

function ingest(text) {
  const chunk = partial + text
  const split = chunk.split('\n')
  partial = split.pop() ?? '' // the tail with no newline yet
  if (!split.length) return

  const stick = following && atBottom()
  for (const line of split) {
    lines.push(line)
    if (matches(line)) appendLine(line)
  }
  if (lines.length > MAX_LINES) {
    lines = lines.slice(-MAX_LINES)
    while (logEl.childElementCount > MAX_LINES) logEl.firstElementChild.remove()
  }
  if (stick) logEl.scrollTop = logEl.scrollHeight
}

// --- Wiring ----------------------------------------------------------------

async function attach() {
  const name = containerEl.value
  if (!name) return
  lines = []
  partial = ''
  logEl.textContent = ''
  setStatus(`following ${name}…`)
  const result = await window.me.startLogs(name, Number(tailEl.value))
  if (!result.ok) setStatus(result.message)
}

containerEl.addEventListener('change', () => void attach())
tailEl.addEventListener('change', () => void attach())
filterEl.addEventListener('input', render)

followButton.addEventListener('click', () => {
  following = !following
  followButton.setAttribute('aria-pressed', String(following))
  followButton.textContent = following ? 'Following' : 'Paused'
  // Pause only stops the view from chasing the end; the stream keeps arriving,
  // so scrolling back up and reading is not a race against the container.
  if (following) logEl.scrollTop = logEl.scrollHeight
})

$('clear').addEventListener('click', () => {
  lines = []
  logEl.textContent = ''
})

window.me.onLogData(ingest)
window.me.onLogEnd((message) => {
  if (partial) { lines.push(partial); partial = '' }
  note(message)
  setStatus(message)
})

async function init() {
  for (const n of [200, 1000, 5000]) {
    const option = document.createElement('option')
    option.value = String(n)
    option.textContent = `${n} lines`
    tailEl.append(option)
  }

  const result = await window.me.listContainers()
  if (!result.ok) return setStatus(result.message || 'docker is not answering')
  if (!result.containers.length) return setStatus('no appliance containers — is it up?')
  for (const c of result.containers) {
    const option = document.createElement('option')
    option.value = c.name
    option.textContent = c.state === 'running' ? c.service : `${c.service} (${c.state})`
    containerEl.append(option)
  }
  await attach()
}

void init()
