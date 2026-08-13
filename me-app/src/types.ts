// The wire types — the single source of truth other modules import from.
//
// These were JSDoc typedefs while the app was plain JavaScript. TypeScript
// ignores JSDoc types inside a .ts file, so the conversion had to bring them
// across as real declarations or they would have become documentation again,
// this time unread by anything at all.

/** One observation the sensor gathered — `text` is what actually travels. */
export interface Fact {
  id: string
  label: string
  text: string
}

/**
 * Standing consent for what the assistant may ask this machine to do, by group.
 * Absent groups are off; the outbox loop runs while any is on.
 */
export interface VerbConsent {
  /** Read this Mac when asked — what's in front, what tabs are open. */
  see?: boolean
  /** Do small things here — focus a tab, open a link, copy, notify. */
  act?: boolean
}

/**
 * How to reach the appliance, and the few preferences that travel with the
 * connection. Stored locally in the app's userData directory.
 */
export interface Settings {
  baseUrl: string
  username: string
  password: string
  /** The appliance theme to paint; '' or absent for the built-in palette. */
  theme?: string
  /** Standing consent by group — see verbs.ts. */
  verbs?: VerbConsent
}

export interface ConnectionResult {
  ok: boolean
  message: string
  /** What to do about a failure, when we can tell. */
  action?: string
  /** Where that action lives, if anywhere. */
  url?: string
  /** ok | no-docker | docker-stopped | not-running */
  state?: string
}

export interface SendResult {
  id: string
  label: string
  ok: boolean
  message: string
}

/** What the scan is allowed to read. Browser history is opt-in — see common.ts. */
export interface ScanOptions {
  browserHistory?: boolean
}

/**
 * One sample of what the user has in front of them. `denied` lists Tier 1
 * targets whose Automation grant was refused — macOS only prompts once.
 */
export interface FocusSample {
  app?: string
  detail?: string
  media?: string
  focusMode?: string
  locked?: boolean
  active?: boolean
  denied: string[]
}

/** Automation grant status for one Tier 1 target app. */
export interface GrantState {
  app: string
  state: 'granted' | 'denied' | 'not-running'
}

/**
 * State of the ambient presence stream. `tier1` (browser tab, now playing)
 * needs its own macOS grant per target.
 */
export interface StreamState {
  running: boolean
  lastMessage: string
  lastAt: string | null
  tier1: boolean
  denied: string[]
}

/**
 * One host folder shared into the assistant container — see mounts.ts.
 * `host` is the absolute path on this machine; `target` is where it appears
 * inside the container: always under /local, read-only. `index` is the user's
 * opt-in to embed the folder's documents into the knowledge base — sharing
 * alone only makes files queryable (virtual Cypher), never stored.
 */
export interface LocalMount {
  host: string
  target: string
  index: boolean
}

/**
 * The Local files panel's state. `dir` is the appliance checkout whose
 * docker-compose.override.yml holds the mounts; `message` says why not when
 * unsupported — or carries a per-action complaint when supported.
 */
export interface MountsState {
  supported: boolean
  message: string
  dir: string | null
  mounts: LocalMount[]
  /**
   * Appliance settings the app manages, carried in the same override file as
   * the mounts — read and rewritten together, or writing one would silently
   * drop the other (see mounts.ts).
   */
  env: Record<string, string>
}

/**
 * Progress of the background indexing trickle — see indexer.ts and
 * embabel/appliance#10. `enabled` says the watcher is on (sweeps keep
 * rescheduling); `running` says a sweep is processing right now. Polled by the
 * renderer the way StreamState is; `message` carries each sweep's verdict,
 * `skipped` counts files the server already holds unchanged, and `nextSweepAt`
 * is when the next change check fires.
 */
export interface IndexState {
  enabled: boolean
  running: boolean
  phase: 'idle' | 'waiting' | 'scanning' | 'indexing'
  total: number
  done: number
  ok: number
  failed: number
  skipped: number
  currentFile: string
  message: string
  truncated: boolean
  nextSweepAt: string | null
}
