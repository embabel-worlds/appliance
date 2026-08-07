// Linux sensor — PARTIAL, and deliberately conservative.
//
// Working now: JetBrains recents, VS Code folders, and Chromium history (the
// schema is identical and sqlite3 is nearly always present).
//
// Not yet implemented:
//   - Installed apps → .desktop entries under /usr/share/applications and
//     ~/.local/share/applications; easy, just not written.
//   - Default browser/mail → `xdg-settings get default-web-browser`.
//   - Frontmost window and idle time → these are display-server specific:
//     X11 has xprop/xdotool and XScreenSaver idle, while Wayland deliberately
//     denies both to unprivileged clients, varying further by compositor. There
//     is no single answer, which is why nothing is guessed here.
//
// Note the appliance itself commonly runs on Linux headlessly, with no desktop
// at all — for those installs this sensor is irrelevant rather than degraded,
// and the appliance is fully functional without any sensor.

const path = require('node:path')
const { HOME, chromiumHistoryFacts, gather, jetbrainsRecents, vscodeFolders } = require('./common')

/** @typedef {import('./types').SensorPlatform} SensorPlatform */

const CONFIG = process.env['XDG_CONFIG_HOME'] ?? path.join(HOME, '.config')

const CHROMIUM_ROOTS = [
  path.join(CONFIG, 'google-chrome'),
  path.join(CONFIG, 'chromium'),
  path.join(CONFIG, 'microsoft-edge'),
  path.join(CONFIG, 'BraveSoftware/Brave-Browser'),
]

/** Distro-provided; on PATH when present, and common.js no-ops if it is absent. */
const SQLITE = 'sqlite3'

/** @type {SensorPlatform} */
const linux = {
  id: 'linux',
  displayName: 'Linux',
  automationSettingsUrl: null,

  scan(options) {
    const collectors = [
      () => jetbrainsRecents([path.join(CONFIG, 'JetBrains')]),
      () => vscodeFolders([path.join(CONFIG, 'Code/User/globalStorage/storage.json')]),
    ]
    if (options.browserHistory) collectors.push(() => chromiumHistoryFacts(SQLITE, CHROMIUM_ROOTS))
    return gather(collectors)
  },

  async sampleFocus() {
    return { denied: [] }
  },

  async grantStates() {
    return []
  },
}

module.exports = { linux }
