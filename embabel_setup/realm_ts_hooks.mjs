/*
 * Module hooks that let Node read a realm.ts the way a fresh clone of the realm ships it.
 *
 * Two things stop plain `node realm.ts` there, and each is answered here and nowhere else:
 *
 * - The SDK, `@embabel/realm-types`, is not installed. The host bundles it, so a realm need not
 *   declare it, and most realms import only its types, which erasure removes. But
 *   `import { type Realm } from '@embabel/realm-types'` erases to `import {} from '…'`, which
 *   still loads the package. When the package cannot be found it resolves here to an empty
 *   module, so a type-only import reads. A VALUE import (defineRealm, vc, …) from the empty
 *   module fails by name, with the ABSENT marker in the error, and the caller says to install it.
 *
 * - The SDK is installed (from its tarball) and ships TypeScript source. Node refuses to strip
 *   types under node_modules, so every .ts file is stripped here instead, wherever it lives.
 *   Stripping keeps every offset; nothing is transformed, so what runs is what was written.
 *
 * Nothing else is changed: every other specifier resolves and loads exactly as Node would.
 */
import * as nodeModule from 'node:module'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const SDK = '@embabel/realm-types'
const ABSENT = 'data:text/javascript,/*embabel-realm-types-absent*/'
const strip = nodeModule.stripTypeScriptTypes

export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context)
  } catch (error) {
    const sdk = specifier === SDK || specifier.startsWith(`${SDK}/`)
    if (sdk && error?.code === 'ERR_MODULE_NOT_FOUND') return { url: ABSENT, shortCircuit: true }
    throw error
  }
}

export async function load(url, context, nextLoad) {
  /* A Node without the stripper (before 22.13) strips the realm's own files under
     --experimental-strip-types, and an SDK under node_modules fails as before. */
  if (strip && url.startsWith('file:') && /\.m?ts$/.test(url)) {
    const source = await readFile(fileURLToPath(url), 'utf8')
    return { format: 'module', source: strip(source), shortCircuit: true }
  }
  return nextLoad(url, context)
}
