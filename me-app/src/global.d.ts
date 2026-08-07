// Renderer-side types, published into the GLOBAL scope.
//
// renderer.ts must compile to a plain browser script: with `nodeIntegration:
// false` there is no `exports` object, so any import — even an erased
// `import type` — makes tsc emit a CommonJS preamble that throws
// `ReferenceError: exports is not defined` and kills the whole script.
// Re-exporting the types globally here keeps types.ts the single source of
// truth while leaving renderer.ts import-free.

import type { ConnectionResult as _ConnectionResult, Fact as _Fact, MeApi, ScanOptions as _ScanOptions, SendResult as _SendResult, Settings as _Settings, StreamState as _StreamState } from './types'

declare global {
  type Fact = _Fact
  type Settings = _Settings
  type ConnectionResult = _ConnectionResult
  type ScanOptions = _ScanOptions
  type SendResult = _SendResult
  type StreamState = _StreamState

  interface Window {
    me: MeApi
  }
}
