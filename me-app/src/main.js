// Embabel Me sensor app — main process.
//
// A menu-bar app: "Me" sits in the macOS menu bar; the window is the consent
// surface (scan → review facts → send). All privileged work (plist reads, HTTP
// to the appliance) happens here in the main process; the renderer only gets
// the narrow IPC surface defined in preload.js.

const { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, powerMonitor, shell, dialog } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const api = require('./api')
const indexer = require('./indexer')
const mounts = require('./mounts')
const { platform } = require('./platform')

/** @typedef {import('./types').Settings} Settings */
/** @typedef {import('./types').StreamState} StreamState */

/** @type {BrowserWindow | null} */
let window = null
/** @type {Tray | null} */
let tray = null

/** @type {Settings} */
const DEFAULTS = { baseUrl: 'http://localhost:4242', username: '', password: '' }

const settingsFile = () => path.join(app.getPath('userData'), 'settings.json')
const logFile = () => path.join(app.getPath('userData'), 'me-app.log')

/** @returns {Settings} */
function loadSettings() {
  try {
    const loaded = { ...DEFAULTS, ...JSON.parse(fs.readFileSync(settingsFile(), 'utf8')) }
    // A blank URL can get saved (e.g. toggling the stream before filling the
    // form); never let it shadow the default on the next launch.
    if (!loaded.baseUrl.trim()) loaded.baseUrl = DEFAULTS.baseUrl
    return loaded
  } catch {
    return { ...DEFAULTS }
  }
}

/** @param {Settings} settings */
function saveSettings(settings) {
  fs.mkdirSync(app.getPath('userData'), { recursive: true })
  fs.writeFileSync(settingsFile(), JSON.stringify(settings, null, 2))
}

function createWindow() {
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

  // Resume the indexing trickle across app restarts: if any shared folder is
  // ticked for indexing and credentials are saved, the watcher picks up where
  // it left off — the server-side reconcile makes resuming free, and edits
  // made while the app was closed are caught by the first sweep.
  const saved = loadSettings()
  if (saved.username.trim() && mounts.state().mounts.some((m) => m.index)) {
    log('[me-app] resuming document indexing watcher')
    indexer.start(saved)
  }

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

/**
 * Log to a FILE as well as the console. `./me.py` launches this app detached
 * with its output sent to /dev/null — correct, since Electron's chatter has no
 * business in the terminal being handed back — so the console is not a
 * diagnostic channel for anyone who started the app the normal way, and a
 * failure nobody can see is the same as no failure report at all.
 */
function log(line) {
  const stamped = `${new Date().toISOString()} ${line}\n`
  process.stdout.write(stamped)
  try {
    fs.mkdirSync(app.getPath('userData'), { recursive: true })
    fs.appendFileSync(logFile(), stamped)
  } catch {
    /* logging must never be the thing that breaks the app */
  }
}

/**
 * Wrap an IPC handler so a thrown error is LOGGED and rethrown with a legible
 * message. Unwrapped, a rejection reaches the renderer as an opaque "Error
 * invoking remote method" — which is how a failed send ends up looking like a
 * UI that simply stopped, the worst failure mode for the one button that moves
 * the user's data.
 */
function handle(channel, fn) {
  ipcMain.handle(channel, async (_e, ...args) => {
    try {
      return await fn(...args)
    } catch (e) {
      log(`[me-app] ${channel} failed: ${e instanceof Error ? (e.stack ?? e.message) : String(e)}`)
      throw new Error(`${channel}: ${e instanceof Error ? e.message : String(e)}`)
    }
  })
}

ipcMain.handle('settings:load', () => loadSettings())
ipcMain.handle('settings:save', (_e, settings) => {
  saveSettings(settings)
  return true
})
handle('connection:test', (settings) => api.testConnection(settings))
handle('scan:run', (options) => platform.scan(options))
handle('facts:send', async (settings, facts) => {
  // Say WHICH facts, by label, and what the appliance made of each — that is
  // what correlates this file with the appliance's own [sensor] log. Labels
  // only, never the text: the user's data belongs in exactly two places, the
  // review pane they approved it in and the appliance, not a third file.
  const noun = facts.length === 1 ? 'fact' : 'facts'
  log(`[me-app] sending ${facts.length} ${noun} to ${settings.baseUrl}: ${facts.map((f) => f.label).join(', ')}`)
  const results = await api.sendFacts(settings, facts)
  const counts = new Map()
  for (const r of results) {
    const status = r.ok ? r.message.split(' ')[0] : 'failed'
    counts.set(status, (counts.get(status) ?? 0) + 1)
  }
  log(`[me-app] appliance answered: ${[...counts].map(([s, n]) => `${n} ${s}`).join(', ') || 'nothing'}`)
  for (const r of results) if (!r.ok) log(`[me-app]   ${r.label}: ${r.message}`)
  return results
})

// Local files: host folders shared read-only into the assistant container so
// the appliance can index them. All the actual work — the override file, the
// container recreate — lives in mounts.js; this is just the IPC skin.
ipcMain.handle('mounts:state', () => mounts.state())
ipcMain.handle('mounts:add', async () => {
  const picked = await dialog.showOpenDialog({
    title: 'Share folders with your appliance',
    buttonLabel: 'Share',
    properties: ['openDirectory', 'multiSelections'],
  })
  return picked.canceled ? mounts.state() : mounts.add(picked.filePaths)
})
ipcMain.handle('mounts:remove', (_e, host) => mounts.remove(host))
ipcMain.handle('mounts:set-index', async (_e, host, index) => {
  const state = mounts.setIndex(host, index)
  // Ticking an ALREADY-MOUNTED folder needs no Apply and no restart: the files
  // are visible in the container right now, so the watcher starts straight
  // away. Only a freshly added folder waits for Apply to create its mount.
  if (index) {
    const saved = loadSettings()
    const mount = state.mounts.find((m) => m.host === host)
    if (saved.username.trim() && mount && (await mounts.isLive(mount.target))) {
      log(`[me-app] index ticked for ${mount.target} — mount is live, starting the watcher`)
      indexer.start(saved)
    }
  }
  return state
})
ipcMain.handle('mounts:apply', () => mounts.apply())

// Opt-in indexing of ticked folders (indexer.js, embabel/appliance#10). start
// kicks or resumes the background trickle and returns immediately; the
// renderer polls index:state for progress, exactly like the ambient stream's
// heartbeat. The trickle keeps sweeping for new and CHANGED files on its own.
ipcMain.handle('index:start', (_e, settings) => indexer.start(settings))
ipcMain.handle('index:state', () => indexer.state())

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

/** @type {NodeJS.Timeout | null} */
let streamTimer = null
let ticksSinceSend = 0
let ticksSinceTier1 = 0
let lastSignature = ''
/** @type {string | undefined} */
let lastApp
// Last known tier-1 values, carried across ticks that skip the probe.
/** @type {string | undefined} */
let lastDetail
/** @type {string | undefined} */
let lastMedia
/** @type {Settings | null} */
let streamSettings = null
/** @type {StreamState} */
const streamState = { running: false, lastMessage: 'off', lastAt: null, tier1: false, denied: [] }

/** @param {Settings} settings */
async function streamTick(settings) {
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
    if (!result.ok) log(`[me-app] focus push failed: ${result.message}`)
  } catch (e) {
    streamState.lastMessage = `error: ${e instanceof Error ? e.message : String(e)}`
  }
  streamState.lastAt = new Date().toISOString()
}

/**
 * Fire a tick right now, for an event that just changed the picture.
 * @param {string} reason
 */
function tickNow(reason) {
  if (!streamState.running || !streamSettings) return
  // The transition IS the news: never let change-suppression swallow it.
  lastSignature = ''
  lastApp = undefined
  streamState.lastMessage = `${reason}…`
  void streamTick(streamSettings)
}

ipcMain.handle('stream:set', (_e, settings, enabled, tier1) => {
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

ipcMain.handle('stream:state', () => ({ ...streamState }))
handle('grants:state', () => platform.grantStates())

// macOS prompts for Automation exactly once; after a refusal the only route
// back is System Settings, so the app must be able to open the right pane. On
// platforms with no such consent broker the URL is null and this is a no-op.
// Only http(s): an IPC channel that opens arbitrary URL schemes is a way to
// launch anything on the user's machine from the renderer.
ipcMain.handle('shell:open-external', async (_e, url) => {
  if (typeof url === 'string' && /^https?:\/\//i.test(url)) await shell.openExternal(url)
})

ipcMain.handle('grants:open-settings', async () => {
  if (platform.automationSettingsUrl) await shell.openExternal(platform.automationSettingsUrl)
})
