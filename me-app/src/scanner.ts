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
import type { Fact, ScanOptions } from './types'

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
export async function scan(options: ScanOptions = {}): Promise<Fact[]> {
  const extractors = [dock, defaultHandlers, jetbrainsRecents, vscodeRecents, applications]
  if (options.browserHistory) extractors.push(browserHistory)
  const results = await Promise.allSettled(extractors.map((fn) => fn()))
  const facts: RawFact[] = []
  for (const r of results) {
    if (r.status === 'fulfilled') facts.push(...r.value)
  }
  return facts.map((f, i) => ({ id: `fact-${i}`, ...f }))
}

// ---------------------------------------------------------------------------
// Browser history — OPT-IN, and the reason the "what news do I read?" question
// has an answer at all. The plist sources above describe the machine; this one
// describes the person, which is why it is off by default and why each fact is
// still individually deselectable before anything is sent.
//
// Read via the system sqlite3 (/usr/bin/sqlite3 — not whatever `sqlite3` on
// PATH happens to be) against a COPY: the live file is locked while the browser
// runs. Chromium history needs no Full Disk Access; Safari's does, so it is
// deliberately not attempted here.
// ---------------------------------------------------------------------------

const SQLITE = '/usr/bin/sqlite3'
const HISTORY_DAYS = 90

/** Chromium stores time as microseconds since 1601-01-01. */
const chromeCutoff = (days: number): number =>
  Math.round((Date.now() / 1000 - days * 86400 + 11644473600) * 1e6)

const NEWS_DOMAINS = [
  'lemonde.fr', 'bbc.co', 'bbc.com', 'theguardian.com', 'nytimes.com', 'abc.net.au',
  'reuters.com', 'cnn.com', 'foxnews.com', 'washingtonpost.com', 'politico.com',
  'cbc.ca', 'ft.com', 'economist.com', 'wsj.com', 'aljazeera.com', 'apnews.com',
  'smh.com.au', 'theage.com.au', 'news.com.au', 'telegraph.co.uk', 'independent.co.uk',
  'spiegel.de', 'lefigaro.fr', 'nzherald.co.nz', 'thetimes.co.uk', 'npr.org',
]

/** Chromium profile history files, across the common forks. */
async function chromiumHistoryFiles(): Promise<string[]> {
  const roots = [
    'Google/Chrome', 'Google/Chrome Beta', 'Chromium', 'BraveSoftware/Brave-Browser',
    'Microsoft Edge', 'Arc/User Data', 'Vivaldi',
  ].map((r) => path.join(HOME, 'Library/Application Support', r))
  const files: string[] = []
  for (const root of roots) {
    let entries: string[]
    try {
      entries = await fs.readdir(root)
    } catch {
      continue
    }
    for (const entry of entries) {
      if (entry !== 'Default' && !entry.startsWith('Profile ')) continue
      const file = path.join(root, entry, 'History')
      try {
        await fs.access(file)
        files.push(file)
      } catch {
        /* profile without history */
      }
    }
  }
  return files
}

/** Query a snapshot of [historyFile]; rows come back as `|`-joined columns. */
async function querySnapshot(historyFile: string, sql: string): Promise<string[][]> {
  const snapshot = path.join(os.tmpdir(), `embabel-history-${process.pid}-${path.basename(path.dirname(historyFile))}.db`)
  try {
    await fs.copyFile(historyFile, snapshot)
    const { stdout } = await run(SQLITE, [snapshot, sql], { maxBuffer: 32 * 1024 * 1024 })
    return stdout.split('\n').filter(Boolean).map((line) => line.split('|'))
  } finally {
    await fs.rm(snapshot, { force: true })
  }
}

/** Merge rows across profiles: key -> summed count, plus first-seen extras. */
async function acrossProfiles(sql: string): Promise<string[][]> {
  const files = await chromiumHistoryFiles()
  const rows: string[][] = []
  for (const file of files) {
    try {
      rows.push(...(await querySnapshot(file, sql)))
    } catch {
      /* a profile that won't open shouldn't lose the others */
    }
  }
  return rows
}

/** Most-visited sites, news outlets with real headlines, and recent searches. */
async function browserHistory(): Promise<RawFact[]> {
  const cutoff = chromeCutoff(HISTORY_DAYS)
  const facts: RawFact[] = []

  // Domain extraction in SQL: everything between "//" and the next "/".
  const domainExpr =
    "substr(url, instr(url,'//')+2, CASE WHEN instr(substr(url, instr(url,'//')+2), '/') = 0 " +
    "THEN length(url) ELSE instr(substr(url, instr(url,'//')+2), '/')-1 END)"

  const domainRows = await acrossProfiles(
    `SELECT ${domainExpr} AS domain, sum(visit_count) FROM urls
     WHERE last_visit_time > ${cutoff} GROUP BY domain ORDER BY 2 DESC LIMIT 60`,
  )
  // Local dev servers and IP literals say nothing about the person.
  const interesting = domainRows
    .filter(([d]) => d && !/^(localhost|127\.0\.0\.1|\d+\.\d+\.\d+\.\d+)(:|$)/.test(d))
    .slice(0, 20)
    .map(([d, n]) => `${d} (${n} visits)`)
  if (interesting.length > 0) {
    facts.push({
      label: 'Top sites',
      text: joinCapped(`{user}'s most-visited websites over the last ${HISTORY_DAYS} days: `, interesting),
    })
  }

  const newsLike = NEWS_DOMAINS.map((d) => `url LIKE '%${d}%'`).join(' OR ')
  const outletRows = await acrossProfiles(
    `SELECT ${domainExpr} AS domain, sum(visit_count) FROM urls
     WHERE last_visit_time > ${cutoff} AND (${newsLike}) GROUP BY domain ORDER BY 2 DESC LIMIT 15`,
  )
  if (outletRows.length > 0) {
    facts.push({
      label: 'News outlets',
      text: joinCapped(
        `{user} reads news from these outlets (visits over the last ${HISTORY_DAYS} days): `,
        outletRows.map(([d, n]) => `${d} (${n})`),
      ),
    })
    const headlines = await acrossProfiles(
      `SELECT title FROM urls WHERE last_visit_time > ${cutoff} AND title IS NOT NULL AND title != ''
       AND (${newsLike}) ORDER BY last_visit_time DESC LIMIT 25`,
    )
    // Front pages ("BBC Home - Breaking News…") repeat endlessly and say nothing
    // about interests; distinct titles are the signal.
    const distinct = [...new Set(headlines.map(([t]) => (t ?? '').trim()).filter(Boolean))].slice(0, 15)
    if (distinct.length > 0) {
      facts.push({
        label: 'News stories',
        text: joinCapped('News stories {user} recently opened: ', distinct),
      })
    }
  }

  const searches = await acrossProfiles(
    `SELECT DISTINCT term FROM keyword_search_terms ORDER BY rowid DESC LIMIT 40`,
  )
  const terms = [...new Set(searches.map(([t]) => (t ?? '').trim()).filter(Boolean))].slice(0, 25)
  if (terms.length > 0) {
    facts.push({ label: 'Web searches', text: joinCapped('{user} recently searched the web for: ', terms) })
  }

  return facts
}
