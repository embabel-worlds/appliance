// The bridge: exposes the narrow sensor API to the renderer as `window.me`.
// The renderer never sees Node or Electron directly. This IS the app's IPC
// surface — a channel absent here does not exist as far as the page knows.

const { contextBridge, ipcRenderer } = require('electron')

const api = {
  loadSettings: () => ipcRenderer.invoke('settings:load'),
  saveSettings: (settings) => ipcRenderer.invoke('settings:save', settings),
  testConnection: (settings) => ipcRenderer.invoke('connection:test', settings),
  updateAppliance: () => ipcRenderer.invoke('appliance:update'),
  scan: (options) => ipcRenderer.invoke('scan:run', options),
  sendFacts: (settings, facts) => ipcRenderer.invoke('facts:send', settings, facts),
  setStream: (settings, enabled, tier1) => ipcRenderer.invoke('stream:set', settings, enabled, tier1),
  streamState: () => ipcRenderer.invoke('stream:state'),
  grantStates: () => ipcRenderer.invoke('grants:state'),
  openAutomationSettings: () => ipcRenderer.invoke('grants:open-settings'),
  openExternal: (url) => ipcRenderer.invoke('shell:open-external', url),
  askDocuments: (settings, request) => ipcRenderer.invoke('docs:ask', settings, request),
  vcExecute: (settings, cypher) => ipcRenderer.invoke('vc:execute', settings, cypher),
  vcSchema: (settings) => ipcRenderer.invoke('vc:schema', settings),
  vcValidate: (settings, cypher) => ipcRenderer.invoke('vc:validate', settings, cypher),
  vcGenerate: (settings, question) => ipcRenderer.invoke('vc:generate', settings, question),
  vcViews: (settings) => ipcRenderer.invoke('vc:views', settings),
  vcSaveView: (settings, spec) => ipcRenderer.invoke('vc:save-view', settings, spec),
  vcDeleteView: (settings, name) => ipcRenderer.invoke('vc:delete-view', settings, name),
  vcViewInvocation: (settings, name, args) => ipcRenderer.invoke('vc:view-invocation', settings, name, args),
  vcLensModel: (settings) => ipcRenderer.invoke('vc:lens-model', settings),
  vcSetLensModel: (settings, model) => ipcRenderer.invoke('vc:set-lens-model', settings, model),
  openQueryStudio: () => ipcRenderer.invoke('query:popout'),
  uploadDocument: (settings, filename, bytes, tags) => ipcRenderer.invoke('docs:upload', settings, filename, bytes, tags),
  // Live narration of a retrieval loop, for as long as one is running. Returns an
  // unsubscribe so a re-ask does not stack listeners.
  onAskProgress: (cb) => {
    const listener = (_e, step) => cb(step)
    ipcRenderer.on('docs:progress', listener)
    return () => ipcRenderer.removeListener('docs:progress', listener)
  },
  listApps: (settings) => ipcRenderer.invoke('apps:list', settings),
  openApp: (settings, name, title) => ipcRenderer.invoke('apps:open', settings, name, title),
  listRealms: (settings) => ipcRenderer.invoke('realms:list', settings),
  realmCatalog: (settings) => ipcRenderer.invoke('realms:catalog', settings),
  installRealm: (settings, repo) => ipcRenderer.invoke('realms:install', settings, repo),
  updateRealm: (settings, name) => ipcRenderer.invoke('realms:update', settings, name),
  updateAllRealms: (settings) => ipcRenderer.invoke('realms:update-all', settings),
  realmGaps: (settings) => ipcRenderer.invoke('realms:gaps', settings),
  listModels: (settings) => ipcRenderer.invoke('models:list', settings),
  getDefaultModel: () => ipcRenderer.invoke('models:default'),
  chatModel: (settings) => ipcRenderer.invoke('models:chat', settings),
  modelsInUse: (settings) => ipcRenderer.invoke('models:in-use', settings),
  loadedModels: () => ipcRenderer.invoke('models:loaded'),
  setChatModel: (settings, model) => ipcRenderer.invoke('models:set-chat', settings, model),
  setDefaultModel: (model) => ipcRenderer.invoke('models:set-default', model),
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
