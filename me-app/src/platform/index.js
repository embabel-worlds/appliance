// Picks the sensor for the host OS. The ONE place `process.platform` is read —
// everything above this line works against [SensorPlatform] and stays portable.

const { macos } = require('./macos')
const { windows } = require('./windows')
const { linux } = require('./linux')

/** @typedef {import('./types').SensorPlatform} SensorPlatform */

/**
 * Neither degraded nor broken — simply an OS nobody has written a sensor for.
 * @type {SensorPlatform}
 */
const unsupported = {
  id: 'unsupported',
  displayName: process.platform,
  automationSettingsUrl: null,
  async scan(_options) {
    return []
  },
  async sampleFocus(_tier1) {
    return { denied: [] }
  },
  async grantStates() {
    return []
  },

  async listTabs() {
    return { tabs: [], denied: [] }
  },

  async focusTab(_ref) {
    return { ok: false, error: 'not implemented on this platform' }
  },
}

/** @returns {SensorPlatform} */
function pick() {
  switch (process.platform) {
    case 'darwin':
      return macos
    case 'win32':
      return windows
    case 'linux':
      return linux
    default:
      return unsupported
  }
}

module.exports = { platform: pick() }
