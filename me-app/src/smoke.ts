// `npm run smoke` — what a scan would gather, without starting the UI.
//
// Its own entry point since the conversion: the platform modules are ES modules
// now, so `node -e "require('./src/platform')"` cannot load them.
//
// Wrapped in a function rather than using top-level await, because this is
// bundled to CommonJS like the rest of the Node side and CJS has no such thing.

import { platform } from './platform/index'

async function main(): Promise<void> {
  const facts = await platform.scan({})
  console.log(JSON.stringify(facts, null, 2))
}

void main()
