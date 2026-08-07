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

/** @typedef {import('../types').Fact} Fact */
/** @typedef {import('../types').FocusSample} FocusSample */
/** @typedef {import('../types').GrantState} GrantState */
/** @typedef {import('../types').ScanOptions} ScanOptions */

/**
 * @typedef {object} SensorPlatform
 *
 * @property {'macos' | 'windows' | 'linux' | 'unsupported'} id
 * @property {string} displayName
 *
 * @property {(options: ScanOptions) => Promise<Fact[]>} scan
 *   Durable facts about the machine and the person. Never throws; partial is fine.
 *
 * @property {(tier1: boolean) => Promise<FocusSample>} sampleFocus
 *   One ambient sample of what the user is doing. `tier1` asks for the sources
 *   that need per-app permission — where the OS has no such concept, a platform
 *   may ignore the flag entirely.
 *
 * @property {() => Promise<GrantState[]>} grantStates
 *   Per-app permission status for tier-1 sources. Empty on platforms with no
 *   per-app consent model (Windows and Linux today) — which is a *weaker*
 *   privacy position, not a stronger one, so those sensors must carry the
 *   consent UI themselves rather than leaning on the OS.
 *
 * @property {string | null} automationSettingsUrl
 *   Deep link to the OS pane where the user fixes a refusal, or null if none exists.
 */

module.exports = {}
