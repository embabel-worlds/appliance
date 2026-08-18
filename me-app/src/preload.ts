// The bridge: exposes the narrow sensor API to the renderer as `window.me`.
// The renderer never sees Node or Electron directly. This IS the app's IPC
// surface — a channel absent here does not exist as far as the page knows.

import { contextBridge, ipcRenderer } from 'electron'
import type { Fact, ScanOptions, Settings, VerbConsent } from './types'

const api = {
  loadSettings: () => ipcRenderer.invoke('settings:load'),
  saveSettings: (settings: Settings) => ipcRenderer.invoke('settings:save', settings),
  testConnection: (settings: Settings) => ipcRenderer.invoke('connection:test', settings),
  updateAppliance: () => ipcRenderer.invoke('appliance:update'),
  scan: (options: ScanOptions) => ipcRenderer.invoke('scan:run', options),
  sendFacts: (settings: Settings, facts: Fact[]) => ipcRenderer.invoke('facts:send', settings, facts),
  setStream: (settings: Settings, enabled: boolean, tier1: boolean) => ipcRenderer.invoke('stream:set', settings, enabled, tier1),
  streamState: () => ipcRenderer.invoke('stream:state'),
  grantStates: () => ipcRenderer.invoke('grants:state'),
  openAutomationSettings: () => ipcRenderer.invoke('grants:open-settings'),
  openExternal: (url: string) => ipcRenderer.invoke('shell:open-external', url),
  askDocuments: (settings: Settings, request: unknown) => ipcRenderer.invoke('docs:ask', settings, request),
  vcExecute: (settings: Settings, cypher: string) => ipcRenderer.invoke('vc:execute', settings, cypher),
  vcSchema: (settings: Settings) => ipcRenderer.invoke('vc:schema', settings),
  vcValidate: (settings: Settings, cypher: string) => ipcRenderer.invoke('vc:validate', settings, cypher),
  vcGenerate: (settings: Settings, question: string) => ipcRenderer.invoke('vc:generate', settings, question),
  vcRefine: (settings: Settings, cypher: string, instruction: string) => ipcRenderer.invoke('vc:refine', settings, cypher, instruction),
  vcViews: (settings: Settings) => ipcRenderer.invoke('vc:views', settings),
  vcSaveView: (settings: Settings, spec: unknown) => ipcRenderer.invoke('vc:save-view', settings, spec),
  vcDeleteView: (settings: Settings, name: string) => ipcRenderer.invoke('vc:delete-view', settings, name),
  vcViewInvocation: (settings: Settings, name: string, args: unknown) => ipcRenderer.invoke('vc:view-invocation', settings, name, args),
  vcLensModel: (settings: Settings) => ipcRenderer.invoke('vc:lens-model', settings),
  vcSetLensModel: (settings: Settings, model: string) => ipcRenderer.invoke('vc:set-lens-model', settings, model),
  openQueryStudio: () => ipcRenderer.invoke('query:popout'),
  openHandlerStudio: () => ipcRenderer.invoke('handlers:popout'),
  // Handlers — the Handler Studio's surface. All effects happen server-side.
  handlersList: (settings: Settings) => ipcRenderer.invoke('handlers:list', settings),
  handlerOpen: (settings: Settings, name: string) => ipcRenderer.invoke('handlers:open', settings, name),
  handlerSave: (settings: Settings, spec: unknown) => ipcRenderer.invoke('handlers:save', settings, spec),
  handlerDelete: (settings: Settings, name: string) => ipcRenderer.invoke('handlers:delete', settings, name),
  handlerSetEnabled: (settings: Settings, name: string, enabled: boolean) => ipcRenderer.invoke('handlers:set-enabled', settings, name, enabled),
  handlerSetSchedule: (settings: Settings, name: string, schedule: string) => ipcRenderer.invoke('handlers:set-schedule', settings, name, schedule),
  handlerDryRun: (settings: Settings, source: string, signalType: string) => ipcRenderer.invoke('handlers:dry-run', settings, source, signalType),
  handlerGenerate: (settings: Settings, english: string, current: string) => ipcRenderer.invoke('handlers:generate', settings, english, current),
  handlerValidate: (settings: Settings, source: string) => ipcRenderer.invoke('handlers:validate', settings, source),
  gatewaySurface: (settings: Settings) => ipcRenderer.invoke('handlers:surface', settings),
  compileSchedule: (settings: Settings, english: string) => ipcRenderer.invoke('handlers:compile-schedule', settings, english),
  // Themes: the appliance owns the list and the CSS; the renderer only paints it.
  listThemes: (settings: Settings) => ipcRenderer.invoke('themes:list', settings),
  themeCss: (settings: Settings, name: string) => ipcRenderer.invoke('themes:css', settings, name),
  // Picked from the application menu, which lives in the main process — so the
  // choice arrives at every open window this way rather than being read back.
  onThemeChanged: (cb: (payload: any) => void) => {
    const listener = (_e: unknown, name: string) => cb(name)
    ipcRenderer.on('theme:changed', listener)
    return () => ipcRenderer.removeListener('theme:changed', listener)
  },
  // Container logs — for the log window only; opening it is a menu action, so
  // there is no popout channel here. Read-only: list, follow, stop. The two
  // `on*` calls return an unsubscribe, and the stream itself is owned by the
  // main process — the page can only ask it to point somewhere else.
  listContainers: () => ipcRenderer.invoke('logs:list'),
  startLogs: (name: string, tail: number) => ipcRenderer.invoke('logs:start', name, tail),
  stopLogs: () => ipcRenderer.invoke('logs:stop'),
  onLogData: (cb: (payload: any) => void) => {
    const listener = (_e: unknown, text: string) => cb(text)
    ipcRenderer.on('logs:data', listener)
    return () => ipcRenderer.removeListener('logs:data', listener)
  },
  onLogEnd: (cb: (payload: any) => void) => {
    const listener = (_e: unknown, message: string) => cb(message)
    ipcRenderer.on('logs:end', listener)
    return () => ipcRenderer.removeListener('logs:end', listener)
  },
  uploadDocument: (settings: Settings, filename: string, bytes: ArrayBuffer, tags: string) => ipcRenderer.invoke('docs:upload', settings, filename, bytes, tags),
  // Live narration of a retrieval loop, for as long as one is running. Returns an
  // unsubscribe so a re-ask does not stack listeners.
  onAskProgress: (cb: (payload: any) => void) => {
    const listener = (_e: unknown, step: unknown) => cb(step)
    ipcRenderer.on('docs:progress', listener)
    return () => ipcRenderer.removeListener('docs:progress', listener)
  },
  listApps: (settings: Settings) => ipcRenderer.invoke('apps:list', settings),
  openApp: (settings: Settings, name: string, title: string) => ipcRenderer.invoke('apps:open', settings, name, title),
  listRealms: (settings: Settings) => ipcRenderer.invoke('realms:list', settings),
  realmCatalog: (settings: Settings) => ipcRenderer.invoke('realms:catalog', settings),
  installRealm: (settings: Settings, repo: string) => ipcRenderer.invoke('realms:install', settings, repo),
  updateRealm: (settings: Settings, name: string) => ipcRenderer.invoke('realms:update', settings, name),
  /** Which installed realms have a newer version upstream — see api.realmUpdates. */
  realmUpdates: (settings: Settings) => ipcRenderer.invoke('realms:updates', settings),
  updateAllRealms: (settings: Settings) => ipcRenderer.invoke('realms:update-all', settings),
  realmGaps: (settings: Settings) => ipcRenderer.invoke('realms:gaps', settings),
  // Icons: realms' and apps'. Bytes, not a URL — the page cannot authenticate,
  // so the main process fetches and hands back a data: URI.
  icon: (settings: Settings, path: string) => ipcRenderer.invoke('icon:get', settings, path),
  // The MCP surface — mode, liveness, and wiring a coding agent to it.
  mcpMode: (settings: Settings) => ipcRenderer.invoke('mcp:mode', settings),
  setMcpMode: (settings: Settings, mode: string) => ipcRenderer.invoke('mcp:set-mode', settings, mode),
  mcpProbe: (settings: Settings) => ipcRenderer.invoke('mcp:probe', settings),
  agentsAvailable: () => ipcRenderer.invoke('agents:available'),
  wireClaudeCode: (settings: Settings, token: string) => ipcRenderer.invoke('agents:wire-claude', settings, token),
  wireCodex: (settings: Settings, token: string) => ipcRenderer.invoke('agents:wire-codex', settings, token),
  agentCommand: (settings: Settings) => ipcRenderer.invoke('agents:command', settings),
  listModels: (settings: Settings) => ipcRenderer.invoke('models:list', settings),
  getDefaultModel: () => ipcRenderer.invoke('models:default'),
  chatModel: (settings: Settings) => ipcRenderer.invoke('models:chat', settings),
  modelsInUse: (settings: Settings) => ipcRenderer.invoke('models:in-use', settings),
  loadedModels: () => ipcRenderer.invoke('models:loaded'),
  setChatModel: (settings: Settings, model: string) => ipcRenderer.invoke('models:set-chat', settings, model),
  setDefaultModel: (model: string) => ipcRenderer.invoke('models:set-default', model),
  getRoles: (settings: Settings) => ipcRenderer.invoke('models:roles', settings),
  setRole: (settings: Settings, roleId: string, model: string) => ipcRenderer.invoke('models:set-role', settings, roleId, model),
  revealFile: (filePath: string) => ipcRenderer.invoke('docs:reveal', filePath),
  openFile: (filePath: string) => ipcRenderer.invoke('docs:open', filePath),
  mountsState: () => ipcRenderer.invoke('mounts:state'),
  addMount: () => ipcRenderer.invoke('mounts:add'),
  removeMount: (host: string) => ipcRenderer.invoke('mounts:remove', host),
  setMountIndex: (host: string, index: boolean) => ipcRenderer.invoke('mounts:set-index', host, index),
  applyMounts: () => ipcRenderer.invoke('mounts:apply'),
  startIndexing: (settings: Settings) => ipcRenderer.invoke('index:start', settings),
  indexingState: () => ipcRenderer.invoke('index:state'),
  setVerbs: (settings: Settings, enabled: VerbConsent) => ipcRenderer.invoke('verbs:set', settings, enabled),
  verbsState: () => ipcRenderer.invoke('verbs:state'),
  chatOpen: (settings: Settings) => ipcRenderer.invoke('chat:open', settings),
  chatSend: (settings: Settings, text: string) => ipcRenderer.invoke('chat:send', settings, text),
  chatHistory: (settings: Settings) => ipcRenderer.invoke('chat:history', settings),
  // The one push channel: chat liveness events from the main-process SSE
  // stream. The callback gets `{type, data}` — never the raw IPC event.
  onChatEvent: (callback: (payload: any) => void) => ipcRenderer.on('chat:event', (_e: unknown, event: unknown) => callback(event)),
}

contextBridge.exposeInMainWorld('me', api)

/* Exported so the renderer's `window.me` can be TYPED from this object rather
 * than from a hand-written copy of it — see globals.d.ts. Electron loads this
 * file as a CommonJS preload and never reads its exports, so this costs nothing
 * at runtime and means a channel added here is immediately known to every page,
 * and one removed here becomes a compile error at its call sites. */
export { api }
