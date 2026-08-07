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
  mountsState: () => ipcRenderer.invoke('mounts:state'),
  addMount: () => ipcRenderer.invoke('mounts:add'),
  removeMount: (host) => ipcRenderer.invoke('mounts:remove', host),
  applyMounts: () => ipcRenderer.invoke('mounts:apply'),
}

contextBridge.exposeInMainWorld('me', api)
