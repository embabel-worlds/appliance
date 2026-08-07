/** One observation the sensor gathered — `text` is what actually travels. */
export interface Fact {
  id: string
  label: string
  text: string
}

/** How to reach the appliance. Stored locally in the app's userData directory. */
export interface Settings {
  baseUrl: string
  username: string
  password: string
}

export interface ConnectionResult {
  ok: boolean
  message: string
}

export interface SendResult {
  id: string
  label: string
  ok: boolean
  message: string
}

/** What the scan is allowed to read. Browser history is opt-in — see scanner.ts. */
export interface ScanOptions {
  browserHistory?: boolean
}

/** One sample of what the user has in front of them. */
export interface FocusSample {
  app?: string
  detail?: string
  media?: string
  focusMode?: string
  locked?: boolean
  active?: boolean
  /** Tier 1 targets whose Automation grant was refused — macOS only prompts once. */
  denied: string[]
}

/** Automation grant status for one Tier 1 target app. */
export interface GrantState {
  app: string
  state: 'granted' | 'denied' | 'not-running'
}

/** State of the ambient presence stream. */
export interface StreamState {
  running: boolean
  lastMessage: string
  lastAt: string | null
  /** Tier 1 (browser tab, now playing) — each target needs its own macOS grant. */
  tier1: boolean
  denied: string[]
}

/** One host folder shared into the assistant container — see mounts.ts. */
export interface LocalMount {
  /** Absolute path on this machine. */
  host: string
  /** Where it appears inside the container: always under /local, read-only. */
  target: string
}

/** The Local files panel's state. `dir` is the appliance checkout whose
 *  docker-compose.override.yml holds the mounts. */
export interface MountsState {
  supported: boolean
  /** Why not, when unsupported — or a per-action complaint when supported. */
  message: string
  dir: string | null
  mounts: LocalMount[]
}

/** The narrow IPC surface preload.ts exposes to the renderer as `window.me`. */
export interface MeApi {
  loadSettings(): Promise<Settings>
  saveSettings(settings: Settings): Promise<boolean>
  testConnection(settings: Settings): Promise<ConnectionResult>
  scan(options: ScanOptions): Promise<Fact[]>
  sendFacts(settings: Settings, facts: Fact[]): Promise<SendResult[]>
  setStream(settings: Settings, enabled: boolean, tier1: boolean): Promise<StreamState>
  streamState(): Promise<StreamState>
  grantStates(): Promise<GrantState[]>
  openAutomationSettings(): Promise<void>
  mountsState(): Promise<MountsState>
  /** Opens the folder picker; returns the state after any additions. */
  addMount(): Promise<MountsState>
  removeMount(host: string): Promise<MountsState>
  /** Rewrites the container with the current mounts (docker compose up). */
  applyMounts(): Promise<ConnectionResult>
}
