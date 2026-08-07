// The bridge: exposes the narrow, typed sensor API to the renderer as
// `window.me`. The renderer never sees Node or Electron directly.

import { contextBridge, ipcRenderer } from 'electron'
import type { Fact, MeApi, ScanOptions, Settings } from './types'

const api: MeApi = {
  loadSettings: () => ipcRenderer.invoke('settings:load'),
  saveSettings: (settings: Settings) => ipcRenderer.invoke('settings:save', settings),
  testConnection: (settings: Settings) => ipcRenderer.invoke('connection:test', settings),
  scan: (options: ScanOptions) => ipcRenderer.invoke('scan:run', options),
  sendFacts: (settings: Settings, facts: Fact[]) => ipcRenderer.invoke('facts:send', settings, facts),
  setStream: (settings: Settings, enabled: boolean, tier1: boolean) =>
    ipcRenderer.invoke('stream:set', settings, enabled, tier1),
  streamState: () => ipcRenderer.invoke('stream:state'),
  grantStates: () => ipcRenderer.invoke('grants:state'),
  openAutomationSettings: () => ipcRenderer.invoke('grants:open-settings'),
  mountsState: () => ipcRenderer.invoke('mounts:state'),
  addMount: () => ipcRenderer.invoke('mounts:add'),
  removeMount: (host: string) => ipcRenderer.invoke('mounts:remove', host),
  applyMounts: () => ipcRenderer.invoke('mounts:apply'),
}

contextBridge.exposeInMainWorld('me', api)
