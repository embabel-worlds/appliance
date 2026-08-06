// Renderer: the consent surface. Scan → review (check/uncheck) → send → receipt.
// Compiled to a plain script (no runtime imports), loaded by index.html.

import type { Fact, MeApi, Settings } from './types'

declare global {
  interface Window {
    me: MeApi
  }
}

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T

const baseUrlInput = $<HTMLInputElement>('baseUrl')
const usernameInput = $<HTMLInputElement>('username')
const passwordInput = $<HTMLInputElement>('password')
const connectionStatus = $<HTMLSpanElement>('connection-status')
const factsSection = $<HTMLDivElement>('facts')
const factList = $<HTMLDivElement>('fact-list')
const sendButton = $<HTMLButtonElement>('send')
const receipt = $<HTMLDivElement>('receipt')

let facts: Fact[] = []

function currentSettings(): Settings {
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

function setStatus(el: HTMLElement, ok: boolean | null, message: string): void {
  el.textContent = message
  el.className = ok === null ? 'status' : ok ? 'status ok' : 'status error'
}

async function init(): Promise<void> {
  const settings = await window.me.loadSettings()
  baseUrlInput.value = settings.baseUrl
  usernameInput.value = settings.username
  passwordInput.value = settings.password
}

$<HTMLButtonElement>('test').addEventListener('click', async () => {
  setStatus(connectionStatus, null, 'Testing…')
  const settings = currentSettings()
  const result = await window.me.testConnection(settings)
  setStatus(connectionStatus, result.ok, result.message)
  if (result.ok) await window.me.saveSettings(settings)
})

const streamToggle = $<HTMLInputElement>('stream-toggle')
const streamStatus = $<HTMLSpanElement>('stream-status')

streamToggle.addEventListener('change', async () => {
  const settings = currentSettings()
  await window.me.saveSettings(settings)
  const state = await window.me.setStream(settings, streamToggle.checked)
  setStatus(streamStatus, state.running ? true : null, state.running ? 'streaming' : 'off')
})

// Reflect the stream's latest heartbeat while the window is open.
setInterval(async () => {
  const state = await window.me.streamState()
  if (state.running) {
    const isError = state.lastMessage.startsWith('error')
    setStatus(streamStatus, !isError, `${state.lastMessage}${state.lastAt ? ` at ${new Date(state.lastAt).toLocaleTimeString()}` : ''}`)
    streamToggle.checked = true
  }
}, 5000)

$<HTMLButtonElement>('scan').addEventListener('click', async () => {
  factList.innerHTML = ''
  receipt.innerHTML = ''
  facts = await window.me.scan()
  factsSection.hidden = false
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
    [...factList.querySelectorAll<HTMLInputElement>('input[type=checkbox]:checked')].map((c) => c.dataset['factId']),
  )
  const selected = facts.filter((f) => checkedIds.has(f.id))
  if (selected.length === 0) return
  sendButton.disabled = true
  receipt.textContent = `Sending ${selected.length} fact(s)…`
  const settings = currentSettings()
  await window.me.saveSettings(settings)
  const results = await window.me.sendFacts(settings, selected)
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

void init()
