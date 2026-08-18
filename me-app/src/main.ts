// Embabel Me sensor app — main process.
//
// A menu-bar app: "Me" sits in the macOS menu bar; the window is the consent
// surface (scan → review facts → send). All privileged work (plist reads, HTTP
// to the appliance) happens here in the main process; the renderer only gets
// the narrow IPC surface defined in preload.js.

import { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, powerMonitor, shell, dialog, Notification } from 'electron'
import type { MenuItemConstructorOptions } from 'electron'
import path from 'node:path'
import fs from 'node:fs'
import * as api from './api'
import * as appliance from './appliance'
import * as backup from './backup'
import * as chat from './chat'
import * as outbox from './outbox'
import * as indexer from './indexer'
import * as mounts from './mounts'
import * as documents from './documents'
import * as logs from './logs'
import * as agents from './agents'
import { platform } from './platform'
import * as pkg from '../package.json'
import type { Fact, ScanOptions, Settings, StreamState, VerbConsent } from './types'
import type { AskRequest } from './wire'
/** The appliance's own property name for its default model. */
const DEFAULT_LLM_KEY = 'EMBABEL_MODELS_DEFAULT_LLM'

let window: BrowserWindow | null = null
let tray: Tray | null = null

/** @type {Settings} */
// `verbs`: standing consent, BY GROUP, for what the assistant may ask this
// machine to do (see verbs.js). The outbox loop runs while any group is on.
//
// ON by default. Everything these grant is local — the assistant reads and acts on
// THIS machine, and nothing leaves it — so an appliance the user installed to know
// about their machine should know about it on first run, rather than appearing inert
// until they find four checkboxes. Each remains individually revocable here, and the
// macOS permissions the deeper reads need are still asked for by the OS, per app, the
// first time they are used.
const DEFAULTS = {
  baseUrl: 'http://localhost:4242', username: '', password: '',
  verbs: { see: true, act: true },
}

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

/**
 * Merge-save: callers pass the fields they know about (the renderer's form
 * knows url/user/password), and flags owned elsewhere (tabVerbs) survive.
 * @param {Partial<Settings>} settings
 */
function saveSettings(settings: Partial<Settings>) {
  fs.mkdirSync(app.getPath('userData'), { recursive: true })
  fs.writeFileSync(settingsFile(), JSON.stringify({ ...loadSettings(), ...settings }, null, 2))
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

// ── The menus: where maintenance lives ──────────────────────────────────────
// The Appliance panel folds away once connected, so anything only reachable
// there is invisible in daily use. Update, Back Up and Restore sit in BOTH
// menus — the application menu and the "Me" tray menu — because the tray is
// present even with every window closed, which is exactly when maintenance is
// least disruptive.

/* One maintenance operation at a time: update, backup and restore all stop and
 * start containers, and two of them interleaved would fight over the same
 * appliance. The name of the one running doubles as the menu's label state. */
/** The maintenance operations that stop and start containers, one at a time. */
type MaintenanceName = 'update' | 'backup' | 'restore'

let busyWith: MaintenanceName | null = null

/* The appliance's themes, as the menu last saw them. Empty until a fetch
 * succeeds — which needs credentials, so the menu is built once without them at
 * launch and rebuilt when refreshThemes() returns. */
let themes: {name: string, displayName?: string, description?: string}[] = []

/**
 * The Theme submenu: radio items, so the menu itself shows which theme is on
 * without anyone having to open a panel to find out.
 *
 * "Embabel (default)" is always present and always first — it is the palette in
 * the shared stylesheet, not an appliance theme, so it exists even when the
 * appliance has none installed or cannot be reached. An appliance that answers
 * with nothing gets a disabled line saying so, because a lone default reads as
 * "there is only one theme" rather than "none are installed".
 */
function themeSubmenu() {
  const chosen = loadSettings().theme || ''
  const items: MenuItemConstructorOptions[] = [
    {
      label: 'Embabel (default)',
      type: 'radio',
      checked: chosen === '',
      click: (): void => void chooseTheme(''),
    },
  ]
  if (themes.length) {
    items.push({ type: 'separator' })
    for (const theme of themes) {
      items.push({
        label: theme.displayName || theme.name,
        type: 'radio',
        checked: chosen === theme.name,
        click: (): void => void chooseTheme(theme.name),
      })
    }
  } else {
    items.push({ type: 'separator' },
      { label: 'No themes on your appliance', enabled: false })
  }
  return items
}

/**
 * Pick a theme: persist it, then tell every open window to repaint. The windows
 * do not poll and are not reloaded — a theme change must not cost the Query
 * Studio the query someone was halfway through writing.
 */
async function chooseTheme(name: string) {
  saveSettings({ theme: name })
  for (const win of BrowserWindow.getAllWindows()) win.webContents.send('theme:changed', name)
  buildMenus()
}

/**
 * Re-read the appliance's theme list and rebuild the menu around it. Called
 * once at start-up and again whenever settings are saved, since the first
 * launch has no credentials and the list is unreachable until it does.
 */
async function refreshThemes() {
  const result = await api.listThemes(loadSettings())
  // A failed fetch leaves the previous list alone: an appliance that is briefly
  // down should not empty a menu that was correct a moment ago.
  if (!result.ok) return
  themes = result.themes
  buildMenus()
}

/** Build (or rebuild — the maintenance items' state changes) both menus. */
function buildMenus() {
  const item = (name: MaintenanceName, idle: string, working: string, run: () => Promise<void>) =>
    busyWith === name
      ? { label: working, enabled: false }
      : { label: idle, enabled: !busyWith, click: (): void => void run() }
  const maintenance = [
    item('update', 'Update Appliance', 'Updating Appliance…', runMenuUpdate),
    item('backup', 'Back Up Appliance…', 'Backing Up Appliance…', runMenuBackup),
    item('restore', 'Restore Appliance…', 'Restoring Appliance…', runMenuRestore),
  ]
  // Standard roles so ⌘Q/⌘C/⌘V work and menus carry the app name. (The bold
  // app-menu title itself still reads "Electron" in dev — that comes from the
  // binary's Info.plist and changes when the app is packaged.)
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: 'Embabel Me',
        submenu: [
          { role: 'about', label: 'About Embabel Me' },
          { type: 'separator' },
          // Appearance belongs where someone looks for a preference, not at the
          // foot of a settings panel that folds away once connected.
          { label: 'Theme', submenu: themeSubmenu() },
          { type: 'separator' },
          ...maintenance,
          { label: 'Container Logs…', click: openLogsWindow },
          { type: 'separator' },
          { role: 'quit' },
        ],
      },
      { role: 'editMenu' },
      { role: 'windowMenu' },
    ]),
  )
  tray?.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Open Embabel Me', click: createWindow },
      { type: 'separator' },
      ...maintenance,
      { label: 'Container Logs…', click: openLogsWindow },
      { label: 'About Embabel Me', click: () => app.showAboutPanel() },
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() },
    ]),
  )
}

/**
 * Run one maintenance operation with the shared narration: menus disabled, a
 * marker on the tray title, notifications at both ends — there may be no
 * window. The confirmation dialogs happen BEFORE this, so `busyWith` never
 * spans a question the user might walk away from.
 * @param {'update' | 'backup' | 'restore'} name @param {string} mark
 * @param {{start: string, done: string, failed: string}} titles
 * @param {() => Promise<{ok: boolean, message: string, path?: string}>} work
 */
async function runMaintenance(
  name: MaintenanceName,
  mark: string,
  titles: { start: string; done: string; failed: string },
  work: () => Promise<{ ok: boolean; message: string; path?: string }>,
) {
  busyWith = name
  buildMenus()
  tray?.setTitle(`Me ${mark}`)
  log(`[me-app] appliance ${name} requested (menu)`)
  new Notification({ title: 'Embabel appliance', body: titles.start }).show()
  const result = await work()
  log(`[me-app] appliance ${name}: ${result.ok ? 'ok' : 'FAILED'} — ${result.message}`)
  busyWith = null
  buildMenus()
  tray?.setTitle('Me')
  new Notification({ title: result.ok ? titles.done : titles.failed, body: result.message }).show()
  return result
}

/** Update from a menu. */
async function runMenuUpdate() {
  if (busyWith) return
  await runMaintenance(
    'update', '↺',
    { start: 'Updating — pulling the checkout and images…', done: 'Embabel appliance updated', failed: 'Appliance update failed' },
    () => appliance.update(),
  )
}

/** Back up: pick where, then copy the volumes and config out (backup.js). */
async function runMenuBackup() {
  if (busyWith) return
  const picked = await dialog.showOpenDialog({
    title: 'Where should the backup folder be created?',
    buttonLabel: 'Back Up Here',
    defaultPath: app.getPath('documents'),
    properties: ['openDirectory', 'createDirectory'],
  })
  if (picked.canceled || !picked.filePaths.length) return
  const result = await runMaintenance(
    'backup', '⇣',
    {
      start: 'Backing up — the assistant pauses while the volumes are copied…',
      done: 'Embabel appliance backed up',
      failed: 'Appliance backup failed',
    },
    () => backup.backUp(picked.filePaths[0]),
  )
  // Show the folder that now holds their data — the receipt IS the folder.
  if (result.ok && result.path) shell.showItemInFolder(result.path)
}

/** Restore: pick a backup, say exactly what will be replaced, then do it. */
async function runMenuRestore() {
  if (busyWith) return
  const picked = await dialog.showOpenDialog({
    title: 'Choose a backup to restore',
    buttonLabel: 'Choose',
    defaultPath: app.getPath('documents'),
    properties: ['openDirectory'],
  })
  if (picked.canceled || !picked.filePaths.length) return
  const chosen = picked.filePaths[0]
  const valid = backup.inspect(chosen)
  if (!valid.ok) {
    await dialog.showMessageBox({ type: 'error', message: 'Not a restorable backup', detail: valid.message })
    return
  }
  // The one genuinely destructive act in this app, so the dialog names the
  // stake and the date, and Cancel is both the default and the escape key.
  const when = valid.createdAt ? new Date(valid.createdAt).toLocaleString() : 'an unknown time'
  const answer = await dialog.showMessageBox({
    type: 'warning',
    message: `Replace this appliance with the backup from ${when}?`,
    detail:
      'The knowledge graph, worlds, documents and appliance settings will all be ' +
      'replaced by what the backup holds. Everything added since it was taken is lost. ' +
      'The appliance restarts as part of the restore.',
    buttons: ['Cancel', 'Restore'],
    defaultId: 0,
    cancelId: 0,
  })
  if (answer.response !== 1) return
  await runMaintenance(
    'restore', '⇡',
    {
      start: 'Restoring — the appliance stops while its data is replaced…',
      done: 'Embabel appliance restored',
      failed: 'Appliance restore failed',
    },
    () => backup.restore(chosen),
  )
}

void app.whenReady().then(() => {
  // The About box: the mark, the version, and who made it.
  app.setAboutPanelOptions({
    applicationName: 'Embabel Me',
    applicationVersion: pkg.version,
    copyright: '© Embabel 2026',
    credits: 'The sensor side of your appliance.\nThis app senses; the appliance thinks.',
    iconPath: path.join(__dirname, '..', 'resources', 'embabel.png'),
  })

  // Menu-bar presence: an empty template image plus a text title renders as
  // plain "Me" in the menu bar — no icon asset needed for the spike.
  tray = new Tray(nativeImage.createEmpty())
  tray.setTitle('Me')
  tray.setToolTip('Embabel Me')
  buildMenus()
  void refreshThemes()

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

  // Standing consent survives restarts: resume listening (and republish the
  // catalog) for whatever groups the user left enabled.
  if (saved.username.trim() && Object.values(saved.verbs ?? {}).some(Boolean)) {
    outbox.start(saved, log)
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

// The chat SSE stream and the outbox loop have no listener once the app is
// going down.
app.on('before-quit', () => {
  chat.stop()
  outbox.stop()
  logs.stop()
})

/**
 * Log to a FILE as well as the console. `./me.py` launches this app detached
 * with its output sent to /dev/null — correct, since Electron's chatter has no
 * business in the terminal being handed back — so the console is not a
 * diagnostic channel for anyone who started the app the normal way, and a
 * failure nobody can see is the same as no failure report at all.
 */
function log(line: string) {
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
function handle(channel: string, fn: (...args: any[]) => unknown) {
  // The invoke event is passed as the LAST argument, after the caller's own — a
  // handler that wants to push updates back while it works (docs:ask narrating a
  // retrieval loop) needs the sender, and every handler that does not simply
  // ignores the extra parameter.
  ipcMain.handle(channel, async (event, ...args) => {
    try {
      return await fn(...args, event)
    } catch (e) {
      log(`[me-app] ${channel} failed: ${e instanceof Error ? (e.stack ?? e.message) : String(e)}`)
      throw new Error(`${channel}: ${e instanceof Error ? e.message : String(e)}`)
    }
  })
}

ipcMain.handle('settings:load', () => loadSettings())
ipcMain.handle('settings:save', (_e, settings: Settings) => {
  saveSettings(settings)
  // Credentials may have only just arrived — the theme list is unreachable
  // without them, so this is the moment the menu can finally be filled.
  void refreshThemes()
  return true
})
handle('connection:test', (settings: Settings) => api.testConnection(settings))
// Update = git fast-forward + compose pull + up (appliance.js). Long-running
// and serialized by the renderer's disabled button; a second call while one
// runs would just queue harmlessly behind the docker CLI anyway.
handle('appliance:update', async () => {
  log('[me-app] appliance update requested')
  const result = await appliance.update()
  log(`[me-app] appliance update: ${result.ok ? 'ok' : 'FAILED'} — ${result.message}`)
  return result
})
handle('scan:run', (options: ScanOptions) => platform.scan(options))
handle('facts:send', async (settings: Settings, facts: Fact[]) => {
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

// Chat: the appliance's own chat, in the smallest possible window (chat.js).
// The SSE stream lives in this process; events reach the page as pushes on
// `chat:event`, the one place the renderer LISTENS rather than asks.
handle('chat:open', (settings: Settings) => {
  chat.open(settings, (event: unknown) => {
    if (window && !window.isDestroyed()) window.webContents.send('chat:event', event)
  })
  return true
})
handle('chat:send', (settings: Settings, text: string) => chat.send(settings, text))
handle('chat:history', (settings: Settings) => chat.history(settings))

// Effect verbs: the outbox loop (outbox.js) that lets the assistant ask this
// machine things — gated on the consent toggle, which is persisted so the
// answer survives restarts.
handle('verbs:set', (settings: Settings, groups: VerbConsent) => {
  const next = { ...settings, verbs: { ...DEFAULTS.verbs, ...groups } }
  saveSettings(next)
  if (Object.values(next.verbs).some(Boolean)) outbox.start(next, log)
  else outbox.stop()
  return outbox.state()
})
ipcMain.handle('verbs:state', () => outbox.state())

// Local files: host folders shared read-only into the assistant container so
// the appliance can index them. All the actual work — the override file, the
// container recreate — lives in mounts.js; this is just the IPC skin.
/*
 * Apps — the HTML applications a world carries (vibe-coded, world-template,
 * realm-shipped), each opened in its OWN child window pointed straight at the
 * appliance. A dedicated window rather than an embedded webview on purpose:
 * the app gets first-class presence (its own frame, its own lifecycle) and the
 * sensor surface never hosts foreign HTML. The window answers the appliance's
 * HTTP Basic challenge with the saved credentials, so it opens signed in; any
 * link leaving the appliance goes out through the user's browser.
 */

/** @type {Map<string, BrowserWindow>} — one window per app, refocused on reopen */
const appWindows = new Map()

/** @param {Settings} settings @param {string} name @param {string} [title] */
function openAppWindow(settings: Settings, name: string, title?: string) {
  const existing = appWindows.get(name)
  if (existing && !existing.isDestroyed()) {
    existing.show()
    existing.focus()
    return
  }
  const win = new BrowserWindow({
    width: 1000,
    height: 760,
    title: title || name.replace(/\.html?$/, ''),
    backgroundColor: '#000000',
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
  })
  appWindows.set(name, win)
  win.on('closed', () => appWindows.delete(name))
  win.webContents.on('login', (event, _details, _authInfo, callback) => {
    event.preventDefault()
    callback(settings.username, settings.password)
  })
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url)
    return { action: 'deny' }
  })
  /* The served page sets its own <title>; don't let it be overwritten by ours
     and then flicker back — but do keep whatever the app itself declares. */
  void win.loadURL(`${settings.baseUrl}/apps/${encodeURIComponent(name)}`)
}

handle('apps:list', (settings: Settings) => api.listApps(settings))
handle('apps:open', (settings: Settings, name: string, title: string) => {
  log(`[me-app] opening app ${name}`)
  openAppWindow(settings, name, title)
  return true
})

/* Realms: discovery and install, same surface the Worlds console speaks. */
handle('realms:list', (settings: Settings) => api.listRealms(settings))
handle('realms:catalog', (settings: Settings) => api.realmCatalog(settings))
handle('realms:install', async (settings: Settings, repo: string) => {
  log(`[me-app] realm install requested: ${repo}`)
  const result = await api.installRealm(settings, repo)
  log(`[me-app] realm install: ${result.ok ? 'ok' : 'FAILED'} — ${result.message}`)
  return result
})
handle('realms:update', async (settings: Settings, name: string) => {
  log(`[me-app] realm update requested: ${name}`)
  const result = await api.updateRealm(settings, name)
  log(`[me-app] realm update: ${result.ok ? 'ok' : 'FAILED'} — ${result.message}`)
  return result
})
handle('realms:updates', (settings: Settings) => api.realmUpdates(settings))

handle('realms:update-all', async (settings: Settings) => {
  log('[me-app] realm update-all requested')
  const result = await api.updateAllRealms(settings)
  log(`[me-app] realm update-all: ${result.ok ? 'ok' : 'FAILED'} — ${result.results.length} realm(s)`)
  return result
})
handle('realms:gaps', (settings: Settings) => api.realmGaps(settings))
handle('icon:get', (settings: Settings, path: string) => api.icon(settings, path))

/* The MCP surface: which mode a connected agent is in, and whether /mcp is live. */
handle('mcp:mode', (settings: Settings) => api.mcpMode(settings))
handle('mcp:set-mode', async (settings: Settings, mode: string) => {
  log(`[me-app] MCP mode -> ${mode}`)
  return api.setMcpMode(settings, mode)
})
handle('mcp:probe', (settings: Settings) => api.mcpProbe(settings))

/* Wiring a coding agent. The token is supplied by the operator per call and is never stored
   here — the appliance minted it once at setup and does not return it again. */
handle('agents:available', async () => ({
  claude: agents.claudeAvailable(),
  // Whether we can read the appliance's own token. The renderer asks so it can hide the
  // paste field entirely in the normal case — a field nobody needs is a field that
  // teaches people the token is theirs to look after.
  token: (await agents.applianceToken()) !== null,
}))

/**
 * The token for a wiring call: the appliance's own unless the operator supplied one.
 *
 * Never logged, never returned to the renderer, never written to settings — it lives for
 * the length of the call that uses it. A pasted token wins because that is the escape
 * hatch for an appliance this machine cannot reach through Docker.
 */
async function wiringToken(supplied: string): Promise<string | null> {
  const given = supplied.trim()
  if (given) return given
  return agents.applianceToken()
}

handle('agents:wire-claude', async (settings: Settings, token: string) => {
  const resolved = await wiringToken(token)
  if (!resolved) return { ok: false, message: 'No token: the appliance is not reachable through Docker from here, so paste one.' }
  log('[me-app] wiring Claude Code')
  const result = await agents.wireClaudeCode(settings.baseUrl, resolved)
  log(`[me-app] wire Claude Code: ${result.ok ? 'ok' : 'FAILED'}`)
  return result
})
handle('agents:wire-codex', async (settings: Settings, token: string) => {
  const resolved = await wiringToken(token)
  if (!resolved) return { ok: false, message: 'No token: the appliance is not reachable through Docker from here, so paste one.' }
  log('[me-app] wiring Codex')
  const result = agents.wireCodex(settings.baseUrl, resolved)
  log(`[me-app] wire Codex: ${result.ok ? 'ok' : 'FAILED'}`)
  return result
})
handle('agents:command', (settings: Settings) => agents.manualCommand(settings.baseUrl))

handle('models:list', (settings: Settings) => api.listModels(settings))
handle('models:chat', (settings: Settings) => api.chatModel(settings))
handle('models:in-use', (settings: Settings) => api.modelsInUse(settings))
handle('models:loaded', async () => Object.fromEntries(await api.loadedLocalModels()))
handle('models:set-chat', (settings: Settings, model: string) => {
  log(`[me-app] chat model -> ${model || '(follow default)'}`)
  return api.setChatModel(settings, model)
})
handle('models:default', () => ({ model: mounts.state().env?.[DEFAULT_LLM_KEY] ?? '' }))
handle('models:set-default', async (model: string) => {
  // A BOOT-time setting: model beans are built during startup, so this lands in
  // the override file and the appliance restarts to pick it up. The role picker
  // is live; this one costs a restart, and the UI says so.
  log(`[me-app] appliance default model -> ${model || '(cleared)'}`)
  const state = mounts.setEnv(DEFAULT_LLM_KEY, model)
  if (!state.supported) return { ok: false, message: state.message }
  return mounts.apply()
})
handle('models:roles', (settings: Settings) => api.getRoles(settings))
handle('models:set-role', (settings: Settings, roleId: string, model: string) => {
  log(`[me-app] set role ${roleId} -> ${model || '(cleared)'}`)
  return api.setRole(settings, roleId, model)
})

// The Query Studio: the advanced virtual-Cypher surface in its own window, so
// the main window stays a sensor panel rather than a database console. One
// instance — a second open focuses the first.
let studioWindow: BrowserWindow | null = null
// Themes: the appliance owns the list and the stylesheet. The renderer has no
// HTTP of its own, so both cross here and it only paints the result.
handle('themes:list', (settings: Settings) => api.listThemes(settings))
handle('themes:css', (settings: Settings, name: string) => api.themeCss(settings, name))

handle('query:popout', () => {
  if (studioWindow) {
    studioWindow.show()
    studioWindow.focus()
    return
  }
  studioWindow = new BrowserWindow({
    width: 1180,
    height: 840,
    title: 'Embabel Me — Query Studio',
    backgroundColor: '#000000',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  void studioWindow.loadFile(path.join(__dirname, '..', 'query-studio.html'))
  studioWindow.on('closed', () => {
    studioWindow = null
  })
})

// The Handler Studio: the sibling surface for TypeScript event handlers —
// same one-instance rule, same reasons.
let handlersWindow: BrowserWindow | null = null

handle('handlers:popout', () => {
  if (handlersWindow) {
    handlersWindow.show()
    handlersWindow.focus()
    return
  }
  handlersWindow = new BrowserWindow({
    width: 1180,
    height: 840,
    title: 'Embabel Me — Handler Studio',
    backgroundColor: '#000000',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  void handlersWindow.loadFile(path.join(__dirname, '..', 'handler-studio.html'))
  handlersWindow.on('closed', () => {
    handlersWindow = null
  })
})

// Handlers — the admin surface behind the Handler Studio. Effects only ever
// happen server-side; dry-run is observe-only by construction there.
handle('handlers:list', (settings: Settings) => api.handlersList(settings))
handle('handlers:open', (settings: Settings, name: string) => api.handlerOpen(settings, name))
handle('handlers:save', (settings: Settings, spec: { name?: string }) => {
  log(`[me-app] handler save '${spec?.name}'`)
  return api.handlerSave(settings, spec)
})
handle('handlers:delete', (settings: Settings, name: string) => {
  log(`[me-app] handler delete '${name}'`)
  return api.handlerDelete(settings, name)
})
handle('handlers:set-enabled', (settings: Settings, name: string, enabled: boolean) => {
  log(`[me-app] handler '${name}' enabled=${enabled}`)
  return api.handlerSetEnabled(settings, name, enabled)
})
handle('handlers:set-schedule', (settings: Settings, name: string, schedule: string) => {
  log(`[me-app] handler '${name}' schedule='${schedule ?? ''}'`)
  return api.handlerSetSchedule(settings, name, schedule)
})
handle('handlers:dry-run', (settings: Settings, source: string, signalType: string) => api.handlerDryRun(settings, source, signalType))
handle('handlers:generate', (settings: Settings, english: string, current: string) => {
  log(`[me-app] handler generate${current ? ' (refining)' : ''}`)
  return api.handlerGenerate(settings, english, current)
})
handle('handlers:validate', (settings: Settings, source: string) => api.handlerValidate(settings, source))
handle('handlers:surface', (settings: Settings) => api.gatewaySurface(settings))
handle('handlers:compile-schedule', (settings: Settings, english: string) => api.compileSchedule(settings, english))

/*
 * Container logs — what the appliance is actually doing, in its own window.
 * Reached from the menus (both of them, like Update) rather than from a panel:
 * it answers for the whole appliance, not for whatever surface you happened to
 * be looking at, and the tray menu is there even with every window closed —
 * which is exactly when "is it even running?" gets asked. The stream lives and
 * dies with the window.
 */
let logsWindow: BrowserWindow | null = null

/** Push to the log window if it is still there; used by both handlers below. */
const toLogs = (channel: string, payload: unknown) => {
  if (logsWindow && !logsWindow.isDestroyed()) logsWindow.webContents.send(channel, payload)
}

function openLogsWindow() {
  if (logsWindow) {
    logsWindow.show()
    logsWindow.focus()
    return
  }
  logsWindow = new BrowserWindow({
    width: 1040,
    height: 720,
    title: 'Embabel Me — Container logs',
    backgroundColor: '#000000',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  void logsWindow.loadFile(path.join(__dirname, '..', 'logs.html'))
  logsWindow.on('closed', () => {
    logsWindow = null
    logs.stop() // nobody left to read it
  })
}

handle('logs:list', () => logs.list())
handle('logs:start', (name: string, tail: number) => {
  log(`[me-app] following logs for ${name} (tail ${tail})`)
  logs.start(name, tail, (text: string) => toLogs('logs:data', text), (message: string) => toLogs('logs:end', message))
  return { ok: true, message: '' }
})
handle('logs:stop', () => {
  logs.stop()
  return { ok: true, message: '' }
})

// Dropped-file ingestion: bytes arrive over IPC (no file paths cross the
// bridge), tags ride along as the upload's only scope.
handle('docs:upload', (settings: Settings, filename: string, bytes: ArrayBuffer, tags: string) => {
  log(`[me-app] uploading ${filename} (${bytes.byteLength} bytes, tags: ${tags || 'none'})`)
  return api.uploadDocument(settings, filename, bytes, tags.split(',').filter(Boolean))
})

handle('vc:schema', (settings: Settings) => api.kgSchema(settings))
handle('vc:validate', (settings: Settings, cypher: string) => api.kgValidate(settings, cypher))
handle('vc:generate', (settings: Settings, question: string) => {
  log(`[me-app] text-to-cypher: ${JSON.stringify(question).slice(0, 120)}`)
  return api.kgGenerate(settings, question)
})
handle('vc:refine', (settings: Settings, cypher: string, instruction: string) => {
  log(`[me-app] cypher refine: ${JSON.stringify(instruction).slice(0, 120)}`)
  return api.kgRefine(settings, cypher, instruction)
})
handle('vc:views', (settings: Settings) => api.listViews(settings))
handle('vc:save-view', (settings: Settings, spec: { name?: string }) => {
  log(`[me-app] saving view ${spec.name}`)
  return api.saveView(settings, spec)
})
handle('vc:delete-view', (settings: Settings, name: string) => api.deleteView(settings, name))
handle('vc:view-invocation', (settings: Settings, name: string, args: Record<string, unknown>) => api.viewInvocation(settings, name, args))
handle('vc:lens-model', (settings: Settings) => api.lensModel(settings))
handle('vc:set-lens-model', (settings: Settings, model: string) => {
  log(`[me-app] text-to-cypher model → ${model || '(inherit default)'}`)
  return api.setLensModel(settings, model)
})

// The advanced documents surface: verbatim virtual Cypher, run by the appliance.
handle('vc:execute', (settings: Settings, cypher: string) => {
  log(`[me-app] virtual cypher: ${JSON.stringify(cypher).slice(0, 160)}`)
  return api.kgExecute(settings, cypher)
})

handle('docs:ask', (settings: Settings, request: AskRequest, event: Electron.IpcMainInvokeEvent) => {
  log(`[me-app] asking documents: ${JSON.stringify(request.question).slice(0, 120)}`)
  // Narrate the retrieval loop back to the window that asked — the ask itself is
  // one long POST, so without this the user watches a spinner for up to 3 minutes
  // with no way to tell progress from a hang.
  return documents.ask(settings, request, (step: { step?: string; detail?: string }) => {
    log(`[me-app] retrieval ${step.step}: ${step.detail}`)
    if (!event.sender.isDestroyed()) event.sender.send('docs:progress', step)
  })
})

// Open a cited document where it actually lives. Reveal rather than open: the
// user asked "which file said that", and Finder showing it in its folder answers
// that better than launching Preview over the top of the app.
handle('docs:reveal', async (filePath: string) => {
  if (typeof filePath !== 'string' || !filePath.startsWith('/')) return false
  shell.showItemInFolder(filePath)
  return true
})

handle('docs:open', async (filePath: string) => {
  if (typeof filePath !== 'string' || !filePath.startsWith('/')) return 'refused'
  return (await shell.openPath(filePath)) || 'ok'
})

ipcMain.handle('mounts:state', () => mounts.state())
ipcMain.handle('mounts:add', async () => {
  const picked = await dialog.showOpenDialog({
    title: 'Share folders with your appliance',
    buttonLabel: 'Share',
    properties: ['openDirectory', 'multiSelections'],
  })
  return picked.canceled ? mounts.state() : mounts.add(picked.filePaths)
})
ipcMain.handle('mounts:remove', (_e, host: string) => mounts.remove(host))
ipcMain.handle('mounts:set-index', async (_e, host: string, index: boolean) => {
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
ipcMain.handle('index:start', (_e, settings: Settings) => indexer.start(settings))
ipcMain.handle('index:state', () => indexer.state())

// ---------------------------------------------------------------------------
// The ambient focus stream.
//
// Two rates, because the two tiers cost wildly different amounts. Tier 0
// (frontmost app, lock, idle, Focus mode) is a few cheap shell reads, so it
// samples every TICK_MS and catches app switches within seconds. Tier 1 (the
// browser tab and now playing) is an AppleScript round-trip per app, so it runs
// only when the frontmost app CHANGED — the moment its answer can actually
// differ — or every TIER1_EVERY_TICKS as a refresh. The exception is an app
// whose tier-1 answer moves under it, a browser above all: while one of those
// is frontmost, tier 1 samples at TICK_MS like everything else, because a new
// page is exactly as much of an event as a new app.
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
// Last known tier-1 values, carried across ticks that skip the probe.
let lastDetail: string | undefined
let lastMedia: string | undefined
let streamSettings: Settings | null = null
const streamState: StreamState = { running: false, lastMessage: 'off', lastAt: null, tier1: false, denied: [] }

/** @param {Settings} settings */
async function streamTick(settings: Settings) {
  try {
    // Always take the cheap sample first: it tells us whether the expensive one
    // is even worth taking.
    const base = await platform.sampleFocus(false)
    const appChanged = base.app !== lastApp
    lastApp = base.app
    ticksSinceTier1++

    /* An app switch is not the only moment tier 1 can differ: someone reading
     * three pages in a row never leaves the browser, and waiting for the slow
     * refresh reports the first page for up to TIER1_EVERY_TICKS afterwards.
     * While the frontmost app is one that rewrites its own tier-1 answer, probe
     * every tick — the cost is one AppleScript round-trip, and only while the
     * user is actually sitting in that app. */
    const every = platform.tier1Volatile?.(base.app) ? 1 : TIER1_EVERY_TICKS
    const wantTier1 =
      streamState.tier1 && base.locked !== true && (appChanged || ticksSinceTier1 >= every)

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
function tickNow(reason: string) {
  if (!streamState.running || !streamSettings) return
  // The transition IS the news: never let change-suppression swallow it.
  lastSignature = ''
  lastApp = undefined
  streamState.lastMessage = `${reason}…`
  void streamTick(streamSettings)
}

ipcMain.handle('stream:set', (_e, settings: Settings, enabled: boolean, tier1) => {
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
ipcMain.handle('shell:open-external', async (_e, url: string) => {
  if (typeof url === 'string' && /^https?:\/\//i.test(url)) await shell.openExternal(url)
})

ipcMain.handle('grants:open-settings', async () => {
  if (platform.automationSettingsUrl) await shell.openExternal(platform.automationSettingsUrl)
})
