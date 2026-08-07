// The sensor's read side: macOS signals gathered WITHOUT any npm dependency.
// Binary plists go through `plutil` (always present on macOS); everything else
// is plain file reading. Pure Node — no Electron imports — so it can be smoke
// tested directly: `npm run smoke`.
//
// Every extractor returns facts as { label, text } — text is what actually
// travels, so keep each one self-contained, human-readable, and under the
// server's 2000-char limit.

import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import type { Fact } from './types'

const run = promisify(execFile)
const HOME = os.homedir()
const MAX_FACT = 1900 // server cap is 2000; leave headroom

type RawFact = Omit<Fact, 'id'>

const tildeify = (p: string): string => p.replace(HOME, '~').replace('$USER_HOME$', '~')

/** Cap a comma-joined list so the fact stays under MAX_FACT. */
function joinCapped(prefix: string, items: string[]): string {
  let text = prefix
  let used = 0
  for (const item of items) {
    const next = used === 0 ? text + item : text + ', ' + item
    if (next.length > MAX_FACT) break
    text = next
    used++
  }
  if (used < items.length) text += ` (and ${items.length - used} more)`
  return text
}

async function plutilXml(file: string): Promise<string> {
  const { stdout } = await run('plutil', ['-convert', 'xml1', '-o', '-', file], { maxBuffer: 32 * 1024 * 1024 })
  return stdout
}

async function plutilJson(file: string): Promise<unknown> {
  const { stdout } = await run('plutil', ['-convert', 'json', '-o', '-', file], { maxBuffer: 32 * 1024 * 1024 })
  return JSON.parse(stdout)
}

/** Dock: the apps the user keeps at hand, in their chosen order. */
async function dock(): Promise<RawFact[]> {
  // The Dock plist embeds <data> blobs, so JSON conversion fails; XML + regex on
  // file-label is the reliable route (verified on a real machine).
  const xml = await plutilXml(path.join(HOME, 'Library/Preferences/com.apple.dock.plist'))
  const labels = [...xml.matchAll(/<key>file-label<\/key>\s*<string>([^<]*)<\/string>/g)].map((m) => m[1]!)
  if (labels.length === 0) return []
  return [{ label: 'Dock', text: joinCapped('{user} keeps these apps in their macOS Dock, in order: ', labels) }]
}

const BUNDLE_NAMES: Record<string, string> = {
  'com.apple.safari': 'Safari',
  'com.google.chrome': 'Google Chrome',
  'org.mozilla.firefox': 'Firefox',
  'com.microsoft.edgemac': 'Microsoft Edge',
  'com.brave.browser': 'Brave',
  'company.thebrowser.browser': 'Arc',
  'com.apple.mail': 'Apple Mail',
  'com.microsoft.outlook': 'Microsoft Outlook',
  'com.readdle.smartemail-macos': 'Spark',
  'com.mimestream.mimestream': 'Mimestream',
}

const bundleName = (id: string): string => BUNDLE_NAMES[id.toLowerCase()] ?? id

interface LsHandler {
  LSHandlerURLScheme?: string
  LSHandlerRoleAll?: string
}

/** LaunchServices: default browser and mail handler — who owns http and mailto. */
async function defaultHandlers(): Promise<RawFact[]> {
  const ls = (await plutilJson(
    path.join(HOME, 'Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist'),
  )) as { LSHandlers?: LsHandler[] }
  const handlers = ls.LSHandlers ?? []
  const forScheme = (scheme: string): string | undefined =>
    handlers.find((h) => h.LSHandlerURLScheme === scheme)?.LSHandlerRoleAll
  const facts: RawFact[] = []
  const browser = forScheme('https') ?? forScheme('http')
  if (browser) facts.push({ label: 'Default browser', text: `{user}'s default web browser is ${bundleName(browser)}.` })
  const mail = forScheme('mailto')
  if (mail) facts.push({ label: 'Mail handler', text: `{user}'s default mail app is ${bundleName(mail)}.` })
  return facts
}

/** JetBrains IDEs: recent projects across every installed product/version, newest first. */
async function jetbrainsRecents(): Promise<RawFact[]> {
  const root = path.join(HOME, 'Library/Application Support/JetBrains')
  let products: string[]
  try {
    products = await fs.readdir(root)
  } catch {
    return []
  }
  const byPath = new Map<string, number>() // project path -> latest open timestamp
  for (const product of products) {
    const file = path.join(root, product, 'options', 'recentProjects.xml')
    let xml: string
    try {
      xml = await fs.readFile(file, 'utf8')
    } catch {
      continue
    }
    // Entries look like: <entry key="$USER_HOME$/dev/foo"> ... projectOpenTimestamp" value="169..."
    for (const entry of xml.split('<entry key="').slice(1)) {
      const projectPath = entry.slice(0, entry.indexOf('"'))
      const ts = Number(entry.match(/projectOpenTimestamp"\s+value="(\d+)"/)?.[1] ?? 0)
      if ((byPath.get(projectPath) ?? -1) < ts) byPath.set(projectPath, ts)
    }
  }
  if (byPath.size === 0) return []
  const recent = [...byPath.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15)
    .map(([p]) => tildeify(p))
  return [
    {
      label: 'JetBrains projects',
      text: joinCapped('{user} recently opened these projects in JetBrains IDEs (most recent first): ', recent),
    },
  ]
}

interface VsCodeStorage {
  backupWorkspaces?: { folders?: Array<{ folderUri?: string }> }
  windowsState?: { lastActiveWindow?: { folder?: string } }
}

/** VS Code: folders it would restore — a proxy for current working projects. */
async function vscodeRecents(): Promise<RawFact[]> {
  let json: VsCodeStorage
  try {
    json = JSON.parse(
      await fs.readFile(path.join(HOME, 'Library/Application Support/Code/User/globalStorage/storage.json'), 'utf8'),
    ) as VsCodeStorage
  } catch {
    return []
  }
  const uris = new Set<string>()
  for (const f of json.backupWorkspaces?.folders ?? []) if (f.folderUri) uris.add(f.folderUri)
  const last = json.windowsState?.lastActiveWindow?.folder
  if (last) uris.add(last)
  const folders = [...uris]
    .filter((u) => u.startsWith('file://'))
    .map((u) => tildeify(decodeURIComponent(u.replace('file://', ''))))
  if (folders.length === 0) return []
  return [{ label: 'VS Code folders', text: joinCapped('{user} has these folders open in Visual Studio Code: ', folders) }]
}

/** Installed applications: a compact profile of the tools on the machine. */
async function applications(): Promise<RawFact[]> {
  const names: string[] = []
  for (const dir of ['/Applications', path.join(HOME, 'Applications')]) {
    try {
      for (const entry of await fs.readdir(dir)) {
        if (entry.endsWith('.app')) names.push(entry.replace(/\.app$/, ''))
      }
    } catch {
      /* directory may not exist */
    }
  }
  if (names.length === 0) return []
  names.sort((a, b) => a.localeCompare(b))
  return [{ label: 'Installed apps', text: joinCapped('{user} has these applications installed on their machine: ', names) }]
}

/** Run every extractor, tolerating individual failures. */
export async function scan(): Promise<Fact[]> {
  const extractors = [dock, defaultHandlers, jetbrainsRecents, vscodeRecents, applications]
  const results = await Promise.allSettled(extractors.map((fn) => fn()))
  const facts: RawFact[] = []
  for (const r of results) {
    if (r.status === 'fulfilled') facts.push(...r.value)
  }
  return facts.map((f, i) => ({ id: `fact-${i}`, ...f }))
}
