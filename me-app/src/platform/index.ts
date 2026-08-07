// Picks the sensor for the host OS. The ONE place `process.platform` is read —
// everything above this line works against [SensorPlatform] and stays portable.

import { macos } from './macos'
import { windows } from './windows'
import { linux } from './linux'
import type { SensorPlatform } from './types'
import type { FocusSample, GrantState, ScanOptions } from '../types'

/** Neither degraded nor broken — simply an OS nobody has written a sensor for. */
const unsupported: SensorPlatform = {
  id: 'unsupported',
  displayName: process.platform,
  automationSettingsUrl: null,
  async scan(_options: ScanOptions) {
    return []
  },
  async sampleFocus(_tier1: boolean): Promise<FocusSample> {
    return { denied: [] }
  },
  async grantStates(): Promise<GrantState[]> {
    return []
  },
}

function pick(): SensorPlatform {
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

export const platform: SensorPlatform = pick()
export type { SensorPlatform }
