// The wire types, as JSDoc typedefs — the single source of truth other modules
// point at with `@typedef {import('./types').Fact} Fact`. The app is plain
// JavaScript run directly by Electron (no compile step, nothing to install
// beyond Electron itself); these carry the shapes for editors and readers.

/**
 * One observation the sensor gathered — `text` is what actually travels.
 * @typedef {object} Fact
 * @property {string} id
 * @property {string} label
 * @property {string} text
 */

/**
 * How to reach the appliance. Stored locally in the app's userData directory.
 * @typedef {object} Settings
 * @property {string} baseUrl
 * @property {string} username
 * @property {string} password
 */

/**
 * @typedef {object} ConnectionResult
 * @property {boolean} ok
 * @property {string} message
 */

/**
 * @typedef {object} SendResult
 * @property {string} id
 * @property {string} label
 * @property {boolean} ok
 * @property {string} message
 */

/**
 * What the scan is allowed to read. Browser history is opt-in — see common.js.
 * @typedef {object} ScanOptions
 * @property {boolean} [browserHistory]
 */

/**
 * One sample of what the user has in front of them. `denied` lists Tier 1
 * targets whose Automation grant was refused — macOS only prompts once.
 * @typedef {object} FocusSample
 * @property {string} [app]
 * @property {string} [detail]
 * @property {string} [media]
 * @property {string} [focusMode]
 * @property {boolean} [locked]
 * @property {boolean} [active]
 * @property {string[]} denied
 */

/**
 * Automation grant status for one Tier 1 target app.
 * @typedef {object} GrantState
 * @property {string} app
 * @property {'granted' | 'denied' | 'not-running'} state
 */

/**
 * State of the ambient presence stream. `tier1` (browser tab, now playing)
 * needs its own macOS grant per target.
 * @typedef {object} StreamState
 * @property {boolean} running
 * @property {string} lastMessage
 * @property {string | null} lastAt
 * @property {boolean} tier1
 * @property {string[]} denied
 */

/**
 * One host folder shared into the assistant container — see mounts.js.
 * `host` is the absolute path on this machine; `target` is where it appears
 * inside the container: always under /local, read-only. `index` is the user's
 * opt-in to embed the folder's documents into the knowledge base — sharing
 * alone only makes files queryable (virtual Cypher), never stored.
 * @typedef {object} LocalMount
 * @property {string} host
 * @property {string} target
 * @property {boolean} index
 */

/**
 * The Local files panel's state. `dir` is the appliance checkout whose
 * docker-compose.override.yml holds the mounts; `message` says why not when
 * unsupported — or carries a per-action complaint when supported.
 * @typedef {object} MountsState
 * @property {boolean} supported
 * @property {string} message
 * @property {string | null} dir
 * @property {LocalMount[]} mounts
 */

/**
 * Progress of the background indexing trickle — see indexer.js and
 * embabel/appliance#10. `enabled` says the watcher is on (sweeps keep
 * rescheduling); `running` says a sweep is processing right now. Polled by the
 * renderer the way StreamState is; `message` carries each sweep's verdict,
 * `skipped` counts files the server already holds unchanged, and `nextSweepAt`
 * is when the next change check fires.
 * @typedef {object} IndexState
 * @property {boolean} enabled
 * @property {boolean} running
 * @property {'idle' | 'waiting' | 'scanning' | 'indexing'} phase
 * @property {number} total
 * @property {number} done
 * @property {number} ok
 * @property {number} failed
 * @property {number} skipped
 * @property {string} currentFile
 * @property {string} message
 * @property {boolean} truncated
 * @property {string | null} nextSweepAt
 */

// The narrow IPC surface preload.js exposes to the renderer as `window.me`:
// loadSettings, saveSettings, testConnection, scan, sendFacts, setStream,
// streamState, grantStates, openAutomationSettings, mountsState, addMount
// (opens the folder picker), removeMount, setMountIndex, applyMounts
// (provision + docker compose up), startIndexing, indexingState.
// See preload.js — it is short enough to be its own definition.

module.exports = {}
