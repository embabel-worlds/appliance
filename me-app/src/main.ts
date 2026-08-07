// Embabel Me sensor app — main process.
//
// A menu-bar app: "Me" sits in the macOS menu bar; the window is the consent
// surface (scan → review facts → send). All privileged work (plist reads, HTTP
// to the appliance) happens here in the main process; the renderer only gets
// the narrow IPC surface defined in preload.ts.

import { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, shell } from 'electron'
import path from 'node:path'
import fs from 'node:fs'
import * as api from './api'
import { platform } from './platform'
import type { Fact, GrantState, ScanOptions, SendResult, Settings, StreamState } from './types'

let window: BrowserWindow | null = null
let tray: Tray | null = null

const DEFAULTS: Settings = { baseUrl: 'http://localhost:4242', username: '', password: '' }

const settingsFile = (): string => path.join(app.getPath('userData'), 'settings.json')

function loadSettings(): Settings {
  try {
    const loaded = { ...DEFAULTS, ...(JSON.parse(fs.readFileSync(settingsFile(), 'utf8')) as Partial<Settings>) }
    // A blank URL can get saved (e.g. toggling the stream before filling the
    // form); never let it shadow the default on the next launch.
    if (!loaded.baseUrl.trim()) loaded.baseUrl = DEFAULTS.baseUrl
    return loaded
  } catch {
    return { ...DEFAULTS }
  }
}

function saveSettings(settings: Settings): void {
  fs.mkdirSync(app.getPath('userData'), { recursive: true })
  fs.writeFileSync(settingsFile(), JSON.stringify(settings, null, 2))
}

function createWindow(): void {
  if (window) {
    window.show()
    window.focus()
    return
  }
  window = new BrowserWindow({
    width: 800,
    height: 700,
    title: 'Embabel Me',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  void window.loadFile(path.join(__dirname, '..', 'index.html'))
  window.on('closed', () => {
    window = null
  })
}

app.setName('Embabel Me')

void app.whenReady().then(() => {
  // Named application menu: standard roles so ⌘Q/⌘C/⌘V work and menus carry
  // the app name. (The bold app-menu title itself still reads "Electron" in
  // dev — that comes from the binary's Info.plist and changes when the app is
  // packaged under its own bundle.)
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      { label: 'Embabel Me', submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'quit' }] },
      { role: 'editMenu' },
      { role: 'windowMenu' },
    ]),
  )

  // Menu-bar presence: an empty template image plus a text title renders as
  // plain "Me" in the menu bar — no icon asset needed for the spike.
  tray = new Tray(nativeImage.createEmpty())
  tray.setTitle('Me')
  tray.setToolTip('Embabel Me')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Open Embabel Me', click: createWindow },
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() },
    ]),
  )

  createWindow()
  app.on('activate', createWindow)
})

// Keep running in the menu bar when the window closes.
app.on('window-all-closed', () => {})

ipcMain.handle('settings:load', (): Settings => loadSettings())
ipcMain.handle('settings:save', (_e, settings: Settings): boolean => {
  saveSettings(settings)
  return true
})
ipcMain.handle('connection:test', (_e, settings: Settings) => api.testConnection(settings))
ipcMain.handle('scan:run', (_e, options: ScanOptions) => platform.scan(options))
ipcMain.handle('facts:send', (_e, settings: Settings, facts: Fact[]): Promise<SendResult[]> =>
  api.sendFacts(settings, facts),
)

// ---------------------------------------------------------------------------
// The ambient focus stream.
//
// Polls every STREAM_INTERVAL_MS but SENDS only when something changed, with a
// heartbeat every HEARTBEAT_TICKS so a long stretch in one app still refreshes
// staleness server-side. Sampling is cheap; sending is not free (a request, a
// merge, a nudge to every consumer of context), and a stream that repeats
// itself teaches the appliance nothing.
// ---------------------------------------------------------------------------

const STREAM_INTERVAL_MS = 20_000
const HEARTBEAT_TICKS = 9 // ~3 minutes even when nothing changes

let streamTimer: NodeJS.Timeout | null = null
let ticksSinceSend = 0
let lastSignature = ''
const streamState: StreamState = { running: false, lastMessage: 'off', lastAt: null, tier1: false, denied: [] }

async function streamTick(settings: Settings): Promise<void> {
  try {
    const sample = await platform.sampleFocus(streamState.tier1)
    streamState.denied = sample.denied
    // Everything that would change what the appliance believes — deliberately
    // NOT idle seconds, which change every tick and mean nothing on their own.
    const signature = JSON.stringify([sample.app, sample.detail, sample.media, sample.focusMode, sample.locked, sample.active])
    ticksSinceSend++
    if (signature === lastSignature && ticksSinceSend < HEARTBEAT_TICKS) {
      streamState.lastMessage = 'unchanged'
      return
    }
    lastSignature = signature
    ticksSinceSend = 0
    const result = await api.sendFocus(settings, sample)
    streamState.lastMessage = result.ok ? `sent: ${result.message}` : `error: ${result.message}`
  } catch (e) {
    streamState.lastMessage = `error: ${e instanceof Error ? e.message : String(e)}`
  }
  streamState.lastAt = new Date().toISOString()
}

ipcMain.handle('stream:set', (_e, settings: Settings, enabled: boolean, tier1: boolean): StreamState => {
  if (streamTimer) {
    clearInterval(streamTimer)
    streamTimer = null
  }
  streamState.running = enabled
  streamState.tier1 = tier1
  // A changed tier means a changed picture: force the next tick to send.
  lastSignature = ''
  if (enabled) {
    void streamTick(settings)
    streamTimer = setInterval(() => void streamTick(settings), STREAM_INTERVAL_MS)
  } else {
    streamState.lastMessage = 'off'
    streamState.denied = []
  }
  return { ...streamState }
})

ipcMain.handle('stream:state', (): StreamState => ({ ...streamState }))
ipcMain.handle('grants:state', (): Promise<GrantState[]> => platform.grantStates())

// macOS prompts for Automation exactly once; after a refusal the only route
// back is System Settings, so the app must be able to open the right pane. On
// platforms with no such consent broker the URL is null and this is a no-op.
ipcMain.handle('grants:open-settings', async (): Promise<void> => {
  if (platform.automationSettingsUrl) await shell.openExternal(platform.automationSettingsUrl)
})
