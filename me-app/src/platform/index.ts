// Picks the sensor for the host OS. The ONE place `process.platform` is read —
// everything above this line works against [SensorPlatform] and stays portable.

import { macos } from './macos'
import { windows } from './windows'
import { linux } from './linux'
import type { SensorPlatform } from './types'
import type { ScanOptions } from '../types'
/**
 * Neither degraded nor broken — simply an OS nobody has written a sensor for.
 * @type {SensorPlatform}
 */
const unsupported: SensorPlatform = {
  id: 'unsupported',
  displayName: process.platform,
  automationSettingsUrl: null,
  async scan(_options: ScanOptions) {
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
function pick(): SensorPlatform {
  switch (process.platform) {
    case 'darwin':
      return macos as SensorPlatform
    case 'win32':
      return windows
    case 'linux':
      return linux
    default:
      return unsupported
  }
}

/* One platform per process, chosen at load: the OS does not change under us. */
export const platform = pick()
