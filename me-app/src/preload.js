// The bridge: exposes the narrow sensor API to the renderer as `window.me`.
// The renderer never sees Node or Electron directly. This IS the app's IPC
// surface — a channel absent here does not exist as far as the page knows.

const { contextBridge, ipcRenderer } = require('electron')

const api = {
  loadSettings: () => ipcRenderer.invoke('settings:load'),
  saveSettings: (settings) => ipcRenderer.invoke('settings:save', settings),
  testConnection: (settings) => ipcRenderer.invoke('connection:test', settings),
  scan: (options) => ipcRenderer.invoke('scan:run', options),
  sendFacts: (settings, facts) => ipcRenderer.invoke('facts:send', settings, facts),
  setStream: (settings, enabled, tier1) => ipcRenderer.invoke('stream:set', settings, enabled, tier1),
  streamState: () => ipcRenderer.invoke('stream:state'),
  grantStates: () => ipcRenderer.invoke('grants:state'),
  openAutomationSettings: () => ipcRenderer.invoke('grants:open-settings'),
  openExternal: (url) => ipcRenderer.invoke('shell:open-external', url),
  askDocuments: (settings, request) => ipcRenderer.invoke('docs:ask', settings, request),
  listModels: (settings) => ipcRenderer.invoke('models:list', settings),
  getRoles: (settings) => ipcRenderer.invoke('models:roles', settings),
  setRole: (settings, roleId, model) => ipcRenderer.invoke('models:set-role', settings, roleId, model),
  revealFile: (filePath) => ipcRenderer.invoke('docs:reveal', filePath),
  openFile: (filePath) => ipcRenderer.invoke('docs:open', filePath),
  mountsState: () => ipcRenderer.invoke('mounts:state'),
  addMount: () => ipcRenderer.invoke('mounts:add'),
  removeMount: (host) => ipcRenderer.invoke('mounts:remove', host),
  setMountIndex: (host, index) => ipcRenderer.invoke('mounts:set-index', host, index),
  applyMounts: () => ipcRenderer.invoke('mounts:apply'),
  startIndexing: (settings) => ipcRenderer.invoke('index:start', settings),
  indexingState: () => ipcRenderer.invoke('index:state'),
  setVerbs: (settings, enabled) => ipcRenderer.invoke('verbs:set', settings, enabled),
  verbsState: () => ipcRenderer.invoke('verbs:state'),
  chatOpen: (settings) => ipcRenderer.invoke('chat:open', settings),
  chatSend: (settings, text) => ipcRenderer.invoke('chat:send', settings, text),
  chatHistory: (settings) => ipcRenderer.invoke('chat:history', settings),
  // The one push channel: chat liveness events from the main-process SSE
  // stream. The callback gets `{type, data}` — never the raw IPC event.
  onChatEvent: (callback) => ipcRenderer.on('chat:event', (_e, event) => callback(event)),
}

contextBridge.exposeInMainWorld('me', api)
