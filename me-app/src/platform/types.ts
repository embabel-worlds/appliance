// The platform seam.
//
// Everything the sensor knows how to read is OS-specific in acquisition and
// OS-neutral in meaning: "the apps this person keeps at hand", "what they are
// looking at right now". That split is the whole design — one [SensorPlatform]
// per OS, a single wire format above it, and a server that never learns which
// OS produced anything.
//
// Adding Windows or Linux support means implementing this interface, not
// touching main/preload/renderer or the appliance.

import type { Fact, FocusSample, GrantState, ScanOptions } from '../types'

export interface SensorPlatform {
  readonly id: 'macos' | 'windows' | 'linux' | 'unsupported'
  readonly displayName: string

  /** Durable facts about the machine and the person. Never throws; partial is fine. */
  scan(options: ScanOptions): Promise<Fact[]>

  /**
   * One ambient sample of what the user is doing. `tier1` asks for the sources
   * that need per-app permission — where the OS has no such concept, a platform
   * may ignore the flag entirely.
   */
  sampleFocus(tier1: boolean): Promise<FocusSample>

  /**
   * Per-app permission status for tier-1 sources. Empty on platforms with no
   * per-app consent model (Windows and Linux today) — which is a *weaker*
   * privacy position, not a stronger one, so those sensors must carry the
   * consent UI themselves rather than leaning on the OS.
   */
  grantStates(): Promise<GrantState[]>

  /** Deep link to the OS pane where the user fixes a refusal, or null if none exists. */
  readonly automationSettingsUrl: string | null
}
