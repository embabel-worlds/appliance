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
/** One open browser tab, as `listTabs` reports it. */
export interface OpenTab {
  browser: string
  window: number
  tab: number
  url: string
  title: string
}

/** Every open tab of every granted browser, plus the browsers that refused. */
export interface TabListing {
  tabs: OpenTab[]
  denied: string[]
}

/** What one machine is currently doing, for the `machineState` verb. */
export interface MachineState {
  [key: string]: unknown
}

export interface SensorPlatform {
  id: 'macos' | 'windows' | 'linux' | 'unsupported'
  displayName: string

  /** Durable facts about the machine and the person. Never throws; partial is fine. */
  scan(options: ScanOptions): Promise<Fact[]>

  /**
   * One ambient sample of what the user is doing. `tier1` asks for the sources
   * that need per-app permission — where the OS has no such concept, a platform
   * may ignore the flag entirely.
   */
  sampleFocus(tier1: boolean): Promise<FocusSample>

  /**
   * Does this frontmost app's tier-1 detail change WITHOUT an app switch? A
   * browser is the case that matters: the user reads three pages without ever
   * leaving Chrome, and a sampler that re-probes only on app change reports the
   * first one for as long as it waits. Omit where tier 1 is empty anyway.
   */
  tier1Volatile?(app: string | undefined): boolean

  /**
   * Per-app permission status for tier-1 sources. Empty on platforms with no
   * per-app consent model (Windows and Linux today) — which is a *weaker*
   * privacy position, not a stronger one, so those sensors must carry the
   * consent UI themselves rather than leaning on the OS.
   */
  grantStates(): Promise<GrantState[]>

  /** Deep link to the OS pane where the user fixes a refusal, or null if none exists. */
  automationSettingsUrl: string | null

  /**
   * Every open tab of every running, granted browser — ON DEMAND only (the
   * "find my tab" verb), never ambient. Empty on platforms without a browser
   * scripting surface.
   */
  listTabs(): Promise<TabListing>

  /**
   * Bring a listTabs row ("app|window|tab") to the front. Stale indexes are a
   * normal failure, not an exception.
   */
  focusTab(ref: string): Promise<{ ok: boolean; error?: string }>

  /**
   * What this machine is doing right now, for the assistant to read on request.
   * Optional: a platform that cannot answer simply omits it, and the verb
   * reports itself unavailable rather than inventing a blank answer.
   */
  machineState?(): Promise<MachineState>

  /** Say something out loud. Optional — silence is the honest default. */
  speak?(text: string): Promise<{ ok: boolean; error?: string }>

  /**
   * The apps running right now. Optional: a platform without a process-listing
   * surface says nothing rather than guessing from what it can see.
   */
  runningApps?(): Promise<string[]>
}
