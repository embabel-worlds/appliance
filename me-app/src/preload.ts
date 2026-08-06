// The bridge: exposes the narrow, typed sensor API to the renderer as
// `window.me`. The renderer never sees Node or Electron directly.

import { contextBridge, ipcRenderer } from 'electron'
import type { Fact, MeApi, Settings } from './types'

const api: MeApi = {
  loadSettings: () => ipcRenderer.invoke('settings:load'),
  saveSettings: (settings: Settings) => ipcRenderer.invoke('settings:save', settings),
  testConnection: (settings: Settings) => ipcRenderer.invoke('connection:test', settings),
  scan: () => ipcRenderer.invoke('scan:run'),
  sendFacts: (settings: Settings, facts: Fact[]) => ipcRenderer.invoke('facts:send', settings, facts),
  setStream: (settings: Settings, enabled: boolean) => ipcRenderer.invoke('stream:set', settings, enabled),
  streamState: () => ipcRenderer.invoke('stream:state'),
}

contextBridge.exposeInMainWorld('me', api)
