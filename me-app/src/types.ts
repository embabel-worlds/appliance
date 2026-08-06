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

/** State of the ambient presence stream. */
export interface StreamState {
  running: boolean
  lastMessage: string
  lastAt: string | null
}

/** The narrow IPC surface preload.ts exposes to the renderer as `window.me`. */
export interface MeApi {
  loadSettings(): Promise<Settings>
  saveSettings(settings: Settings): Promise<boolean>
  testConnection(settings: Settings): Promise<ConnectionResult>
  scan(): Promise<Fact[]>
  sendFacts(settings: Settings, facts: Fact[]): Promise<SendResult[]>
  setStream(settings: Settings, enabled: boolean): Promise<StreamState>
  streamState(): Promise<StreamState>
}
