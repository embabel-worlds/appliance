// Cross-platform collectors: the readers whose LOGIC is identical everywhere and
// whose only OS-specific part is where the files live.
//
// This is most of the interesting reading, which is the point of the seam. A
// JetBrains recentProjects.xml is the same XML on all three OSes; Chromium's
// history schema is the same SQLite. Only the paths differ — so those are
// parameters, and each platform module supplies them.
//
// Nothing in this file may reference `process.platform`, `~/Library`, the
// registry, or any OS-specific tool.

const { execFile } = require('node:child_process')
const { promisify } = require('node:util')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')

/** @typedef {import('../types').Fact} Fact */
/** A fact before numbering: everything but the `id`. @typedef {Omit<Fact, 'id'>} RawFact */

const run = promisify(execFile)
const HOME = os.homedir()

/** Cap on one fact's text: the server rejects over 2000 chars, so leave headroom. */
const MAX_FACT = 1900

/**
 * Home-relative paths read better in memory and leak less than absolute ones.
 * @param {string} p
 */
const tildeify = (p) => p.replace(HOME, '~').replace('$USER_HOME$', '~')

/**
 * Cap a comma-joined list so the fact stays under [MAX_FACT], noting what was dropped.
 * @param {string} prefix @param {string[]} items
 */
function joinCapped(prefix, items) {
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

/**
 * Run collectors concurrently, tolerating individual failures, and number the results.
 * @param {Array<() => Promise<RawFact[]>>} collectors
 * @returns {Promise<Fact[]>}
 */
async function gather(collectors) {
  const results = await Promise.allSettled(collectors.map((fn) => fn()))
  const facts = []
  for (const r of results) if (r.status === 'fulfilled') facts.push(...r.value)
  return facts.map((f, i) => ({ id: `fact-${i}`, ...f }))
}

// ---------------------------------------------------------------------------
// JetBrains — same XML on every OS; only the settings root differs.
// ---------------------------------------------------------------------------

/** @param {string[]} roots @returns {Promise<RawFact[]>} */
async function jetbrainsRecents(roots) {
  const byPath = new Map() // project path -> latest open timestamp
  for (const root of roots) {
    let products
    try {
      products = await fs.readdir(root)
    } catch {
      continue
    }
    for (const product of products) {
      let xml
      try {
        xml = await fs.readFile(path.join(root, product, 'options', 'recentProjects.xml'), 'utf8')
      } catch {
        continue
      }
      // Entries look like: <entry key="$USER_HOME$/dev/foo"> … projectOpenTimestamp" value="169…"
      for (const entry of xml.split('<entry key="').slice(1)) {
        const projectPath = entry.slice(0, entry.indexOf('"'))
        const ts = Number(entry.match(/projectOpenTimestamp"\s+value="(\d+)"/)?.[1] ?? 0)
        if ((byPath.get(projectPath) ?? -1) < ts) byPath.set(projectPath, ts)
      }
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

// ---------------------------------------------------------------------------
// VS Code — same storage.json everywhere.
// ---------------------------------------------------------------------------

/** @param {string[]} storageFiles @returns {Promise<RawFact[]>} */
async function vscodeFolders(storageFiles) {
  const uris = new Set()
  for (const file of storageFiles) {
    let json
    try {
      json = JSON.parse(await fs.readFile(file, 'utf8'))
    } catch {
      continue
    }
    for (const f of json.backupWorkspaces?.folders ?? []) if (f.folderUri) uris.add(f.folderUri)
    const last = json.windowsState?.lastActiveWindow?.folder
    if (last) uris.add(last)
  }
  const folders = [...uris]
    .filter((u) => u.startsWith('file://'))
    .map((u) => tildeify(decodeURIComponent(u.replace('file://', ''))))
  if (folders.length === 0) return []
  return [{ label: 'VS Code folders', text: joinCapped('{user} has these folders open in Visual Studio Code: ', folders) }]
}

// ---------------------------------------------------------------------------
// Chromium history — identical schema on every OS. OPT-IN.
//
// The plist/registry sources describe the machine; this describes the person,
// which is why it is off by default and why every fact stays individually
// deselectable before sending.
//
// Read from a COPY via a sqlite3 binary the platform names: the live file is
// locked while the browser runs. Firefox (places.sqlite) and Safari are
// deliberately not read — Safari's needs Full Disk Access.
// ---------------------------------------------------------------------------

const HISTORY_DAYS = 90

/**
 * Chromium stores time as microseconds since 1601-01-01.
 * @param {number} days
 */
const chromeCutoff = (days) =>
  Math.round((Date.now() / 1000 - days * 86400 + 11644473600) * 1e6)

const NEWS_DOMAINS = [
  'lemonde.fr', 'bbc.co', 'bbc.com', 'theguardian.com', 'nytimes.com', 'abc.net.au',
  'reuters.com', 'cnn.com', 'foxnews.com', 'washingtonpost.com', 'politico.com',
  'cbc.ca', 'ft.com', 'economist.com', 'wsj.com', 'aljazeera.com', 'apnews.com',
  'smh.com.au', 'theage.com.au', 'news.com.au', 'telegraph.co.uk', 'independent.co.uk',
  'spiegel.de', 'lefigaro.fr', 'nzherald.co.nz', 'thetimes.co.uk', 'npr.org',
]

/**
 * Every `History` file under the given Chromium user-data roots.
 * @param {string[]} userDataRoots @returns {Promise<string[]>}
 */
async function chromiumHistoryFiles(userDataRoots) {
  const files = []
  for (const root of userDataRoots) {
    let entries
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

/**
 * @param {string} sqliteBin @param {string} historyFile @param {string} sql
 * @returns {Promise<string[][]>}
 */
async function querySnapshot(sqliteBin, historyFile, sql) {
  const snapshot = path.join(
    os.tmpdir(),
    `embabel-history-${process.pid}-${path.basename(path.dirname(historyFile))}.db`,
  )
  try {
    await fs.copyFile(historyFile, snapshot)
    const { stdout } = await run(sqliteBin, [snapshot, sql], { maxBuffer: 32 * 1024 * 1024 })
    return stdout.split('\n').filter(Boolean).map((line) => line.split('|'))
  } finally {
    await fs.rm(snapshot, { force: true })
  }
}

/**
 * Top sites, news outlets, the headlines actually opened, and recent searches.
 * [sqliteBin] is the platform's sqlite3; platforms without one pass null and
 * get [] rather than a bundled binary.
 * @param {string | null} sqliteBin @param {string[]} userDataRoots
 * @returns {Promise<RawFact[]>}
 */
async function chromiumHistoryFacts(sqliteBin, userDataRoots) {
  if (!sqliteBin) return []
  const files = await chromiumHistoryFiles(userDataRoots)
  if (files.length === 0) return []

  /** @param {string} sql @returns {Promise<string[][]>} */
  const across = async (sql) => {
    const rows = []
    for (const file of files) {
      try {
        rows.push(...(await querySnapshot(sqliteBin, file, sql)))
      } catch {
        /* a profile that won't open shouldn't lose the others */
      }
    }
    return rows
  }

  const cutoff = chromeCutoff(HISTORY_DAYS)
  const facts = []
  // Domain extraction in SQL: everything between "//" and the next "/".
  const domainExpr =
    "substr(url, instr(url,'//')+2, CASE WHEN instr(substr(url, instr(url,'//')+2), '/') = 0 " +
    "THEN length(url) ELSE instr(substr(url, instr(url,'//')+2), '/')-1 END)"

  const domainRows = await across(
    `SELECT ${domainExpr} AS domain, sum(visit_count) FROM urls
     WHERE last_visit_time > ${cutoff} GROUP BY domain ORDER BY 2 DESC LIMIT 60`,
  )
  // Local dev servers and IP literals say nothing about a person.
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
  const outletRows = await across(
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
    const headlines = await across(
      `SELECT title FROM urls WHERE last_visit_time > ${cutoff} AND title IS NOT NULL AND title != ''
       AND (${newsLike}) ORDER BY last_visit_time DESC LIMIT 25`,
    )
    // Front pages ("BBC Home – Breaking News…") repeat endlessly and say nothing
    // about interests; distinct titles are the signal.
    const distinct = [...new Set(headlines.map(([t]) => (t ?? '').trim()).filter(Boolean))].slice(0, 15)
    if (distinct.length > 0) {
      facts.push({ label: 'News stories', text: joinCapped('News stories {user} recently opened: ', distinct) })
    }
  }

  const searches = await across('SELECT DISTINCT term FROM keyword_search_terms ORDER BY rowid DESC LIMIT 40')
  const terms = [...new Set(searches.map(([t]) => (t ?? '').trim()).filter(Boolean))].slice(0, 25)
  if (terms.length > 0) {
    facts.push({ label: 'Web searches', text: joinCapped('{user} recently searched the web for: ', terms) })
  }

  return facts
}

module.exports = {
  run,
  HOME,
  tildeify,
  joinCapped,
  gather,
  jetbrainsRecents,
  vscodeFolders,
  chromiumHistoryFiles,
  chromiumHistoryFacts,
}
