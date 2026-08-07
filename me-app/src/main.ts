// Embabel Me sensor app — main process.
//
// A menu-bar app: "Me" sits in the macOS menu bar; the window is the consent
// surface (scan → review facts → send). All privileged work (plist reads, HTTP
// to the appliance) happens here in the main process; the renderer only gets
// the narrow IPC surface defined in preload.ts.

import { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain } from 'electron'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import fs from 'node:fs'
import * as scanner from './scanner'
import * as api from './api'
import type { Fact, ScanOptions, SendResult, Settings, StreamState } from './types'

const run = promisify(execFile)

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
ipcMain.handle('scan:run', (_e, options: ScanOptions) => scanner.scan(options))
ipcMain.handle('facts:send', (_e, settings: Settings, facts: Fact[]): Promise<SendResult[]> =>
  api.sendFacts(settings, facts),
)

// ---------------------------------------------------------------------------
// Ambient presence stream: every STREAM_INTERVAL_MS, read keyboard idle time
// (ioreg — no permissions needed) and push one context event. Each push
// replaces the last server-side, so this is a heartbeat, not an accumulation.
// ---------------------------------------------------------------------------

const STREAM_INTERVAL_MS = 30_000
const IDLE_THRESHOLD_SECONDS = 120

let streamTimer: NodeJS.Timeout | null = null
const streamState: StreamState = { running: false, lastMessage: 'off', lastAt: null }

/** Seconds since last keyboard/mouse input, via IOHIDSystem's HIDIdleTime (nanoseconds). */
async function idleSeconds(): Promise<number> {
  const { stdout } = await run('ioreg', ['-c', 'IOHIDSystem'])
  const nanos = Number(stdout.match(/"HIDIdleTime"\s*=\s*(\d+)/)?.[1] ?? 0)
  return nanos / 1e9
}

async function streamTick(settings: Settings): Promise<void> {
  try {
    const idle = await idleSeconds()
    const result = await api.sendPresence(settings, idle < IDLE_THRESHOLD_SECONDS)
    streamState.lastMessage = result.ok ? `sent: ${result.message}` : `error: ${result.message}`
  } catch (e) {
    streamState.lastMessage = `error: ${e instanceof Error ? e.message : String(e)}`
  }
  streamState.lastAt = new Date().toISOString()
}

ipcMain.handle('stream:set', (_e, settings: Settings, enabled: boolean): StreamState => {
  if (streamTimer) {
    clearInterval(streamTimer)
    streamTimer = null
  }
  streamState.running = enabled
  if (enabled) {
    void streamTick(settings)
    streamTimer = setInterval(() => void streamTick(settings), STREAM_INTERVAL_MS)
  } else {
    streamState.lastMessage = 'off'
  }
  return { ...streamState }
})

ipcMain.handle('stream:state', (): StreamState => ({ ...streamState }))
