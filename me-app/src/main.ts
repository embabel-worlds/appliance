// Embabel Me sensor app — main process.
//
// A menu-bar app: "Me" sits in the macOS menu bar; the window is the consent
// surface (scan → review facts → send). All privileged work (plist reads, HTTP
// to the appliance) happens here in the main process; the renderer only gets
// the narrow IPC surface defined in preload.ts.

import { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, powerMonitor, shell } from 'electron'
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
    width: 860,
    height: 760,
    title: 'Embabel Me',
    // The console's surface runs to the edges: black chrome, inset traffic
    // lights, no grey title strip cutting across the aurora.
    backgroundColor: '#000000',
    titleBarStyle: 'hiddenInset',
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

  // Presence transitions, delivered rather than polled. Registered once, after
  // ready (powerMonitor is unavailable before it); each is a no-op while the
  // stream is off.
  powerMonitor.on('lock-screen', () => tickNow('screen locked'))
  powerMonitor.on('unlock-screen', () => tickNow('screen unlocked'))
  powerMonitor.on('suspend', () => tickNow('machine sleeping'))
  powerMonitor.on('resume', () => tickNow('machine awake'))
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
// Two rates, because the two tiers cost wildly different amounts. Tier 0
// (frontmost app, lock, idle, Focus mode) is a few cheap shell reads, so it
// samples every TICK_MS and catches app switches within seconds. Tier 1 (the
// browser tab and now playing) is an AppleScript round-trip per app, so it runs
// only when the frontmost app CHANGED — the moment its answer can actually
// differ — or every TIER1_EVERY_TICKS as a refresh.
//
// Sampling is cheap; SENDING is not (a request, a merge, a nudge to every
// consumer of context), so a tick only sends when the picture changed, with a
// heartbeat so a long stretch in one app still refreshes staleness server-side.
//
// Lock, unlock, sleep and wake don't wait for any of that: powerMonitor
// delivers them as events and they tick immediately. They are the strongest
// presence transitions there are — "gone" should never be up to a tick late.
// ---------------------------------------------------------------------------

const TICK_MS = 5_000
const TIER1_EVERY_TICKS = 4 // ~20s: refresh tab/track even when the app hasn't changed
const HEARTBEAT_TICKS = 36 // ~3 minutes even when nothing changes at all

let streamTimer: NodeJS.Timeout | null = null
let ticksSinceSend = 0
let ticksSinceTier1 = 0
let lastSignature = ''
let lastApp: string | undefined
/** Last known tier-1 values, carried across ticks that skip the probe. */
let lastDetail: string | undefined
let lastMedia: string | undefined
let streamSettings: Settings | null = null
const streamState: StreamState = { running: false, lastMessage: 'off', lastAt: null, tier1: false, denied: [] }

async function streamTick(settings: Settings): Promise<void> {
  try {
    // Always take the cheap sample first: it tells us whether the expensive one
    // is even worth taking.
    const base = await platform.sampleFocus(false)
    const appChanged = base.app !== lastApp
    lastApp = base.app
    ticksSinceTier1++

    const wantTier1 =
      streamState.tier1 && base.locked !== true && (appChanged || ticksSinceTier1 >= TIER1_EVERY_TICKS)

    let sample = base
    if (wantTier1) {
      sample = await platform.sampleFocus(true)
      ticksSinceTier1 = 0
      lastDetail = sample.detail
      lastMedia = sample.media
      streamState.denied = sample.denied
    } else if (streamState.tier1 && base.locked !== true) {
      // Carry the last known tab/track forward rather than letting them vanish
      // and reappear, which would read as a change and send pure noise. They
      // refresh at most TIER1_EVERY_TICKS later.
      sample = { ...base, detail: lastDetail, media: lastMedia }
    }

    // Everything that would change what the appliance believes — deliberately
    // NOT idle seconds, which change every tick and mean nothing on their own.
    const signature = JSON.stringify([
      sample.app, sample.detail, sample.media, sample.focusMode, sample.locked, sample.active,
    ])
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

/** Fire a tick right now, for an event that just changed the picture. */
function tickNow(reason: string): void {
  if (!streamState.running || !streamSettings) return
  // The transition IS the news: never let change-suppression swallow it.
  lastSignature = ''
  lastApp = undefined
  streamState.lastMessage = `${reason}…`
  void streamTick(streamSettings)
}

ipcMain.handle('stream:set', (_e, settings: Settings, enabled: boolean, tier1: boolean): StreamState => {
  if (streamTimer) {
    clearInterval(streamTimer)
    streamTimer = null
  }
  streamState.running = enabled
  streamState.tier1 = tier1
  streamSettings = settings
  // A changed tier means a changed picture: force the next tick to send.
  lastSignature = ''
  ticksSinceTier1 = TIER1_EVERY_TICKS
  if (enabled) {
    void streamTick(settings)
    streamTimer = setInterval(() => void streamTick(settings), TICK_MS)
  } else {
    streamState.lastMessage = 'off'
    streamState.denied = []
    lastDetail = undefined
    lastMedia = undefined
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
