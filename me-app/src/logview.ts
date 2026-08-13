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

import { $ } from './dom'
import { restoreTheme } from './theme'

const MAX_LINES = 5000

const containerEl = $('container')
const tailEl = $('tail')
const filterEl = $('filter')
const followButton = $('follow')
const logEl = $('log')
const statusEl = $('status')

/* Every line received, filtered or not — so changing the filter can re-render
   what is already here instead of waiting for the container to say it again. */
let lines: string[] = []
/* A chunk can end mid-line; hold the remainder until its newline arrives. */
let partial = ''
/* Paused means the DOM does not change AT ALL — not merely that it stops
   scrolling. A selection dies the moment its nodes are appended past or
   trimmed away, so on a busy container "pause" is the only way to copy a
   stack trace out. Lines keep accumulating in `lines`; the view catches up on
   resume. */
let following = true
/** Where the frozen view stopped, so pause can say how far behind it is. */
let pausedAt = 0
const pending = () => Math.max(0, lines.length - pausedAt)

const setStatus = (text: string) => (statusEl.textContent = text)

/** Severity, by the shape every JVM log line in this appliance happens to have. */
function severity(line: string) {
  if (/\b(ERROR|FATAL|SEVERE)\b/.test(line)) return 'error'
  if (/\bWARN(ING)?\b/.test(line)) return 'warn'
  return ''
}

const matches = (line: string) => {
  const needle = filterEl.value.trim().toLowerCase()
  return !needle || line.toLowerCase().includes(needle)
}

/** Was the view scrolled to the end before we appended? Only then do we chase it. */
const atBottom = () => logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40

function appendLine(line: string) {
  const el = document.createElement('div')
  el.className = `line ${severity(line)}`.trim()
  el.textContent = line
  logEl.append(el)
}

function note(text: string) {
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

function ingest(text: string) {
  const chunk = partial + text
  const split = chunk.split('\n')
  partial = split.pop() ?? '' // the tail with no newline yet
  if (!split.length) return

  const stick = atBottom()
  for (const line of split) {
    lines.push(line)
    if (following && matches(line)) appendLine(line)
  }
  const overflow = lines.length - MAX_LINES
  if (overflow > 0) lines = lines.slice(overflow)
  if (following) {
    while (logEl.childElementCount > MAX_LINES) logEl.firstElementChild.remove()
    if (stick) logEl.scrollTop = logEl.scrollHeight
  } else {
    setStatus(`paused — ${pending()} line(s) behind`)
  }
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
  followButton.classList.toggle('paused', !following)
  followButton.querySelector<HTMLElement>('.pause')!.hidden = !following
  followButton.querySelector<HTMLElement>('.play')!.hidden = following
  $('follow-label').textContent = following ? 'Pause' : 'Resume'
  followButton.title = following
    ? 'Freeze the view so you can select and copy'
    : 'Catch up with the stream'
  if (following) {
    render() // catch up on everything that arrived while frozen
    setStatus(`following ${containerEl.value}…`)
  } else {
    pausedAt = lines.length
    setStatus('paused — the view is frozen; the stream is not')
  }
})

$('clear').addEventListener('click', () => {
  lines = []
  pausedAt = 0
  logEl.textContent = ''
})

/* Copy what is on screen — the filtered view, whole lines, no DOM selection
   games. Selecting by hand still works (that is what pause is for); this is
   for "give me all of it" without a 5000-line drag. */
$('copy').addEventListener('click', async () => {
  const text = lines.filter(matches).join('\n')
  try {
    await navigator.clipboard.writeText(text)
    setStatus(`copied ${text ? text.split('\n').length : 0} line(s)`)
  } catch (e) {
    setStatus(`copy failed: ${e instanceof Error ? e.message : String(e)}`)
  }
})

window.me.onLogData(ingest)
window.me.onLogEnd((message: string) => {
  if (partial) { lines.push(partial); partial = '' }
  note(message)
  setStatus(message)
})

async function init() {
  // This window carries the theme element and the script but never painted one.
  // Now that the theme is a menu away from any window, "every window follows"
  // has to include this one.
  void restoreTheme(await window.me.loadSettings())

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
