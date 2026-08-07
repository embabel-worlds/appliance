// macOS sensor. EVERYTHING OS-SPECIFIC LIVES HERE — `plutil`, `lsappinfo`,
// `ioreg`, AppleScript, `~/Library` paths. Nothing outside this file may assume
// a Mac; the cross-platform readers live in common.ts and take paths.
//
// Permission tiers, because they are the user-visible cost:
//
//   TIER 0 — no prompt of any kind: preference plists, frontmost app, screen
//     lock, Focus mode, idle time. `lsappinfo` is used rather than System Events
//     precisely because it needs no Automation grant.
//
//   TIER 1 — one macOS Automation grant per target app: browser tab, now
//     playing. Two rules make polling safe:
//       1. NEVER send an Apple Event to an app that isn't running — the event
//          LAUNCHES it. Every probe checks liveness first (tier 0, free).
//       2. A refusal is a normal state, not an error. osascript reports -1743;
//          we surface it as "denied" so the UI can point at System Settings,
//          because macOS only ever prompts once.
//
//   NOT ATTEMPTED — anything needing Full Disk Access (Safari history, Mail,
//     Messages) or Accessibility/Screen Recording (window titles, screenshots).

import fs from 'node:fs/promises'
import path from 'node:path'
import type { FocusSample, GrantState, ScanOptions } from '../types'
import type { SensorPlatform } from './types'
import {
  HOME,
  RawFact,
  chromiumHistoryFacts,
  gather,
  jetbrainsRecents,
  joinCapped,
  run,
  vscodeFolders,
} from './common'

const APP_SUPPORT = path.join(HOME, 'Library/Application Support')
const PREFERENCES = path.join(HOME, 'Library/Preferences')

/** Always present on macOS — and specifically NOT whatever `sqlite3` is on PATH. */
const SQLITE = '/usr/bin/sqlite3'

const IDLE_THRESHOLD_SECONDS = 120

const CHROMIUM_ROOTS = [
  'Google/Chrome', 'Google/Chrome Beta', 'Chromium', 'BraveSoftware/Brave-Browser',
  'Microsoft Edge', 'Arc/User Data', 'Vivaldi',
].map((r) => path.join(APP_SUPPORT, r))

// ---------------------------------------------------------------------------
// Scan — durable facts
// ---------------------------------------------------------------------------

async function plutilXml(file: string): Promise<string> {
  const { stdout } = await run('plutil', ['-convert', 'xml1', '-o', '-', file], { maxBuffer: 32 * 1024 * 1024 })
  return stdout
}

async function plutilJson(file: string): Promise<unknown> {
  const { stdout } = await run('plutil', ['-convert', 'json', '-o', '-', file], { maxBuffer: 32 * 1024 * 1024 })
  return JSON.parse(stdout)
}

/** The Dock: the apps this person keeps at hand, in the order they chose. */
async function dock(): Promise<RawFact[]> {
  // The Dock plist embeds <data> blobs, so JSON conversion fails outright; XML
  // plus a file-label match is the reliable route (verified on a real machine).
  const xml = await plutilXml(path.join(PREFERENCES, 'com.apple.dock.plist'))
  const labels = [...xml.matchAll(/<key>file-label<\/key>\s*<string>([^<]*)<\/string>/g)].map((m) => m[1]!)
  if (labels.length === 0) return []
  return [{ label: 'Dock', text: joinCapped('{user} keeps these apps in their macOS Dock, in order: ', labels) }]
}

const BUNDLE_NAMES: Record<string, string> = {
  'com.apple.safari': 'Safari',
  'com.google.chrome': 'Google Chrome',
  'org.mozilla.firefox': 'Firefox',
  'com.microsoft.edgemac': 'Microsoft Edge',
  'com.brave.browser': 'Brave',
  'company.thebrowser.browser': 'Arc',
  'com.apple.mail': 'Apple Mail',
  'com.microsoft.outlook': 'Microsoft Outlook',
  'com.readdle.smartemail-macos': 'Spark',
  'com.mimestream.mimestream': 'Mimestream',
}

const bundleName = (id: string): string => BUNDLE_NAMES[id.toLowerCase()] ?? id

interface LsHandler {
  LSHandlerURLScheme?: string
  LSHandlerRoleAll?: string
}

/** LaunchServices: who owns https and mailto — the default browser and mail app. */
async function defaultHandlers(): Promise<RawFact[]> {
  const ls = (await plutilJson(
    path.join(PREFERENCES, 'com.apple.LaunchServices/com.apple.launchservices.secure.plist'),
  )) as { LSHandlers?: LsHandler[] }
  const handlers = ls.LSHandlers ?? []
  const forScheme = (scheme: string): string | undefined =>
    handlers.find((h) => h.LSHandlerURLScheme === scheme)?.LSHandlerRoleAll
  const facts: RawFact[] = []
  const browser = forScheme('https') ?? forScheme('http')
  if (browser) facts.push({ label: 'Default browser', text: `{user}'s default web browser is ${bundleName(browser)}.` })
  const mail = forScheme('mailto')
  if (mail) facts.push({ label: 'Mail handler', text: `{user}'s default mail app is ${bundleName(mail)}.` })
  return facts
}

/** Installed applications: a compact profile of the tools on the machine. */
async function applications(): Promise<RawFact[]> {
  const names: string[] = []
  for (const dir of ['/Applications', path.join(HOME, 'Applications')]) {
    try {
      for (const entry of await fs.readdir(dir)) {
        if (entry.endsWith('.app')) names.push(entry.replace(/\.app$/, ''))
      }
    } catch {
      /* directory may not exist */
    }
  }
  if (names.length === 0) return []
  names.sort((a, b) => a.localeCompare(b))
  return [{ label: 'Installed apps', text: joinCapped('{user} has these applications installed on their machine: ', names) }]
}

// ---------------------------------------------------------------------------
// Focus — tier 0
// ---------------------------------------------------------------------------

/** Frontmost application name. No Automation grant needed — that's why lsappinfo. */
async function frontmostApp(): Promise<string | undefined> {
  try {
    const { stdout } = await run('/bin/sh', ['-c', 'lsappinfo info -only name "$(lsappinfo front)"'])
    // Output looks like: "LSDisplayName"="IntelliJ IDEA"
    return stdout.match(/"LSDisplayName"\s*=\s*"([^"]*)"/)?.[1] || undefined
  } catch {
    return undefined
  }
}

/** Screen locked? Present at the desk is not the same as present. */
async function screenLocked(): Promise<boolean | undefined> {
  try {
    const { stdout } = await run('ioreg', ['-n', 'Root', '-d1', '-k', 'CGSSessionScreenIsLocked'])
    if (!stdout.includes('CGSSessionScreenIsLocked')) return false
    return /CGSSessionScreenIsLocked"\s*=\s*Yes/.test(stdout)
  } catch {
    return undefined
  }
}

/** Seconds since the last keyboard or mouse input. */
async function idleSeconds(): Promise<number | undefined> {
  try {
    const { stdout } = await run('ioreg', ['-c', 'IOHIDSystem'])
    const nanos = Number(stdout.match(/"HIDIdleTime"\s*=\s*(\d+)/)?.[1] ?? NaN)
    return Number.isFinite(nanos) ? nanos / 1e9 : undefined
  } catch {
    return undefined
  }
}

/**
 * Active Focus / Do Not Disturb mode. The strongest interruption signal there
 * is: the user has already told their machine not to bother them. The file
 * shape has changed across macOS releases, so this reads defensively and
 * returns undefined rather than guessing.
 */
async function focusMode(): Promise<string | undefined> {
  try {
    const json = JSON.parse(
      await fs.readFile(path.join(HOME, 'Library/DoNotDisturb/DB/Assertions.json'), 'utf8'),
    ) as {
      data?: Array<{ storeAssertionRecords?: Array<{ assertionDetails?: { assertionDetailsModeIdentifier?: string } }> }>
    }
    const id = json.data?.[0]?.storeAssertionRecords?.[0]?.assertionDetails?.assertionDetailsModeIdentifier
    if (!id) return undefined
    // Ids look like "com.apple.donotdisturb.mode.work", or a UUID for custom modes.
    const leaf = id.split('.').pop() ?? id
    return /^[0-9A-F-]{20,}$/i.test(leaf) ? 'on' : leaf
  } catch {
    return undefined
  }
}

// ---------------------------------------------------------------------------
// Focus — tier 1 (one Automation grant per target app)
// ---------------------------------------------------------------------------

const BROWSERS = [
  { proc: 'Google Chrome', app: 'Google Chrome', chromium: true },
  { proc: 'Brave Browser', app: 'Brave Browser', chromium: true },
  { proc: 'Microsoft Edge', app: 'Microsoft Edge', chromium: true },
  { proc: 'Safari', app: 'Safari', chromium: false },
]

const PLAYERS = ['Spotify', 'Music']

/** Is a process with this exact name running? Free, and never launches anything. */
async function isRunning(appName: string): Promise<boolean> {
  try {
    await run('pgrep', ['-x', appName])
    return true
  } catch {
    return false
  }
}

/** Run one AppleScript, distinguishing "denied" from "nothing to report". */
async function osascript(script: string): Promise<{ value?: string; denied?: boolean }> {
  try {
    const { stdout } = await run('osascript', ['-e', script], { timeout: 5000 })
    const value = stdout.trim()
    return value ? { value } : {}
  } catch (e) {
    const message = (e as { stderr?: string; message?: string }).stderr ?? (e as Error).message ?? ''
    // -1743: the user has not authorized (or has denied) Automation for this target.
    if (message.includes('-1743') || message.includes('Not authorized')) return { denied: true }
    // -600 (not running), -1728 (no window/tab) and friends: nothing to say.
    return {}
  }
}

function safeHost(url: string): string | undefined {
  try {
    return new URL(url).hostname
  } catch {
    return undefined
  }
}

/** Active tab of the browser the user is actually looking at. */
async function browserTab(front: string | undefined): Promise<{ detail?: string; denied?: string }> {
  for (const browser of BROWSERS) {
    // Only ask the frontmost browser: polling a background one is noisier and a
    // worse consent story for no extra signal.
    if (front !== browser.app) continue
    if (!(await isRunning(browser.proc))) continue
    const script = browser.chromium
      ? `tell application "${browser.app}" to get (title of active tab of front window) & " || " & (URL of active tab of front window)`
      : `tell application "${browser.app}" to get (name of front document) & " || " & (URL of front document)`
    const { value, denied } = await osascript(script)
    if (denied) return { denied: browser.app }
    if (!value) continue
    const [title, url] = value.split(' || ')
    const host = url ? safeHost(url) : undefined
    // Title plus host: the title says what they are reading, the host says where
    // from, and neither carries the full path or query string.
    return { detail: [title?.trim(), host && `(${host})`].filter(Boolean).join(' ') || undefined }
  }
  return {}
}

/** Now playing, from whichever supported player is running AND actually playing. */
async function nowPlaying(): Promise<{ media?: string; denied?: string }> {
  for (const player of PLAYERS) {
    if (!(await isRunning(player))) continue
    const script =
      `tell application "${player}"\n` +
      `  if player state is playing then\n` +
      `    return (name of current track) & " — " & (artist of current track)\n` +
      `  end if\n` +
      `end tell`
    const { value, denied } = await osascript(script)
    if (denied) return { denied: player }
    if (value) return { media: value }
  }
  return {}
}

// ---------------------------------------------------------------------------

export const macos: SensorPlatform = {
  id: 'macos',
  displayName: 'macOS',
  automationSettingsUrl: 'x-apple.systempreferences:com.apple.preference.security?Privacy_Automation',

  scan(options: ScanOptions) {
    const collectors: Array<() => Promise<RawFact[]>> = [
      dock,
      defaultHandlers,
      () => jetbrainsRecents([path.join(APP_SUPPORT, 'JetBrains')]),
      () => vscodeFolders([path.join(APP_SUPPORT, 'Code/User/globalStorage/storage.json')]),
      applications,
    ]
    if (options.browserHistory) collectors.push(() => chromiumHistoryFacts(SQLITE, CHROMIUM_ROOTS))
    return gather(collectors)
  },

  async sampleFocus(tier1: boolean): Promise<FocusSample> {
    const [app, locked, idle, mode] = await Promise.all([
      frontmostApp(),
      screenLocked(),
      idleSeconds(),
      focusMode(),
    ])
    const sample: FocusSample = {
      app,
      locked,
      focusMode: mode,
      active: idle !== undefined ? idle < IDLE_THRESHOLD_SECONDS : undefined,
      denied: [],
    }
    // Nothing to read off a locked screen, and asking would be pure noise.
    if (tier1 && locked !== true) {
      const [tab, playing] = await Promise.all([browserTab(app), nowPlaying()])
      sample.detail = tab.detail
      sample.media = playing.media
      sample.denied = [tab.denied, playing.denied].filter((d): d is string => Boolean(d))
    }
    return sample
  },

  async grantStates(): Promise<GrantState[]> {
    const targets = [
      ...BROWSERS.map((b) => ({ name: b.app, proc: b.proc })),
      ...PLAYERS.map((p) => ({ name: p, proc: p })),
    ]
    const states: GrantState[] = []
    for (const target of targets) {
      if (!(await isRunning(target.proc))) {
        states.push({ app: target.name, state: 'not-running' })
        continue
      }
      // `name of application X` is the cheapest event that still needs the grant.
      const { denied } = await osascript(`tell application "${target.name}" to return name`)
      states.push({ app: target.name, state: denied ? 'denied' : 'granted' })
    }
    return states
  },
}
