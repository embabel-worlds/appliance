import type { Settings } from './types'

/**
 * A settings object before init() supplies the real one.
 *
 * The windows used to start these at `null` and rely on nobody touching them
 * until init ran — true, but stated nowhere. An empty connection is the same
 * promise made in the type: reachable, and reaching nothing.
 */
export const EMPTY_SETTINGS: Settings = { baseUrl: '', username: '', password: '' }
