// Windows sensor — PARTIAL. The cross-platform readers work today; the
// Windows-specific ones are named here rather than silently missing, so the gap
// is a to-do list instead of a mystery.
//
// Working now: JetBrains recents, VS Code folders (same files, %APPDATA% roots).
//
// Not yet implemented, and what each needs:
//   - Installed apps + pinned taskbar/Start items → registry
//     (HKLM/HKCU …\Uninstall) and %APPDATA%\…\TaskBar, via `reg query`.
//   - Default browser/mail → HKCU …\UrlAssociations (the Windows analogue of
//     LaunchServices).
//   - Frontmost window / idle time → GetForegroundWindow + GetLastInputInfo,
//     which need a native addon or a bundled PowerShell script; there is no
//     `lsappinfo`/`ioreg` equivalent to shell out to.
//   - Browser history → the schema is identical and common.js already handles
//     it, but Windows ships no sqlite3 binary; needs node:sqlite (Node 22+) or a
//     bundled one. Hence `null` below rather than a guess at a path.
//   - WSL: developers' repos often live inside \\wsl$\, invisible to a naive
//     C:\ walk. Any future filesystem scanning must cross that boundary.
//
// One structural difference worth stating: Windows has NO per-app consent
// broker. macOS prompts for Automation on our behalf; Windows stays silent
// while we read. So the Windows sensor must carry the consent UI itself — its
// grantStates() is empty not because everything is granted, but because the OS
// grants nothing and asks nothing.

const path = require('node:path')
const { HOME, chromiumHistoryFacts, gather, jetbrainsRecents, vscodeFolders } = require('./common')

/** @typedef {import('./types').SensorPlatform} SensorPlatform */

const APPDATA = process.env['APPDATA'] ?? path.join(HOME, 'AppData/Roaming')
const LOCALAPPDATA = process.env['LOCALAPPDATA'] ?? path.join(HOME, 'AppData/Local')

const CHROMIUM_ROOTS = [
  path.join(LOCALAPPDATA, 'Google/Chrome/User Data'),
  path.join(LOCALAPPDATA, 'Microsoft/Edge/User Data'),
  path.join(LOCALAPPDATA, 'BraveSoftware/Brave-Browser/User Data'),
  path.join(LOCALAPPDATA, 'Chromium/User Data'),
]

/** No sqlite3 ships with Windows; see the header note. @type {string | null} */
const SQLITE = null

/** @type {SensorPlatform} */
const windows = {
  id: 'windows',
  displayName: 'Windows',
  // No OS-level Automation consent model exists to link to.
  automationSettingsUrl: null,

  scan(options) {
    const collectors = [
      () => jetbrainsRecents([path.join(APPDATA, 'JetBrains')]),
      () => vscodeFolders([path.join(APPDATA, 'Code/User/globalStorage/storage.json')]),
    ]
    if (options.browserHistory) collectors.push(() => chromiumHistoryFacts(SQLITE, CHROMIUM_ROOTS))
    return gather(collectors)
  },

  async sampleFocus() {
    // Honest empty sample rather than a fabricated one: the server treats an
    // all-null focus as "nothing to say", which is exactly the truth here.
    return { denied: [] }
  },

  async grantStates() {
    return []
  },
}

module.exports = { windows }
