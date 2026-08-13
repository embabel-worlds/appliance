import type { Settings } from './types'
import type { SchemaSnapshot } from './wire'

/**
 * What each studio panel is handed at init: the connection, and the few
 * operations that belong to the editor rather than to any one panel.
 */
export interface StudioDeps {
  settings: Settings
  setEditorText: (text: string, opts?: { edited?: boolean }) => void
  scheduleValidation?: () => void
  isHandEdited?: () => boolean
  editorText?: () => string
  run?: () => void
  useLabel?: (label: string) => void
  /** Hover definitions, owned by the editor and shown over any panel. */
  definitions?: { show: (target: HTMLElement, name: string, text: string) => void; hide: () => void }
  /** Told when a fresh schema snapshot arrives, so the editor can complete against it. */
  onSchema?: (snapshot: SchemaSnapshot) => void
}

/**
 * A settings object before init() supplies the real one.
 *
 * The panels used to start these at `null` and rely on nobody touching them
 * until init ran — true, but stated nowhere. An empty connection is the same
 * promise made in the type: reachable, and reaching nothing.
 */
export const EMPTY_SETTINGS: Settings = { baseUrl: '', username: '', password: '' }
