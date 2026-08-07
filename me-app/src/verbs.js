// The verb registry — what this machine can be ASKED to do, and the whole of
// it. The appliance never sends code: it sends a verb name and arguments, and
// this table decides whether such a thing exists, what it means, and how it is
// done here. That inversion is the security model. An `applescript.eval` verb
// would hand a container that ingests untrusted documents, email and web pages
// a remote-execution primitive on the user's Mac (AppleScript reaches Mail,
// Messages, the filesystem, and `do shell script`), so the sensor accepts a
// VOCABULARY and never a script.
//
// Verbs are OS-NEUTRAL IN MEANING and OS-specific in implementation — the same
// split the acquisition seam already makes. Note how little AppleScript is
// actually in here: notify, url.open and clipboard.write are Electron APIs
// that work identically on Windows and Linux; only tabs.* reaches for
// AppleScript, behind the platform seam, where a Windows build would use UI
// Automation instead. The verb is the contract; the automation technology is
// an implementation detail that never appears on the wire.
//
// What makes a verb admissible here — every one must pass all five:
//   1. Its blast radius fits in one sentence a person can consent to.
//   2. It takes DATA, never code, paths, or anything that composes into more
//      than it says (see url.open refusing non-http schemes: `x-apple…:` and
//      custom schemes launch applications with arguments).
//   3. Its worst case under a prompt-injected assistant is annoyance, not loss.
//   4. It is visible or reversible — the user sees it happen.
//   5. It answers with the least it can (titles and hosts, never full URLs).
//
// Deliberately NOT verbs, and not because nobody got to them: shell or script
// evaluation, sending mail or messages as the user (impersonation), deleting
// or moving files, keystroke synthesis, screenshots or screen recording,
// camera and microphone, keychain and password access, and reading the
// clipboard (which is where password managers put secrets — writing is safe,
// reading is not).

const { Notification, clipboard, shell } = require('electron')
const { platform } = require('./platform')
const fs = require('node:fs')
const path = require('node:path')
const mounts = require('./mounts')

/**
 * Consent GROUPS, not one toggle per verb: a panel with fifteen switches is a
 * panel nobody reads, and consent nobody reads is not consent. Each group is a
 * promise stated in the user's terms.
 */
const GROUPS = {
  see: 'Look at what is on this Mac — what I am doing, what tabs and apps are open, what I changed recently',
  act: 'Do small things here — focus a tab, show a file, open a link, copy text, draft an email, speak, notify me',
}

/**
 * @typedef {object} Verb
 * @property {string} name
 * @property {'see' | 'act'} group
 * @property {string} description — written for the LLM that must choose it
 * @property {Record<string, string>} args — name → what it means
 * @property {(args: any) => Promise<any>} run
 */

/** @type {Verb[]} */
const VERBS = [
  {
    name: 'focus.now',
    group: 'see',
    description:
      'What the user is doing on their machine RIGHT NOW: frontmost app, what it is showing, ' +
      'what is playing, whether the screen is locked and whether they are at the keyboard. ' +
      'Use before interrupting, or when the user says "this" about what is in front of them.',
    args: {},
    async run() {
      const sample = await platform.sampleFocus(true)
      return {
        app: sample.app,
        detail: sample.detail,
        media: sample.media,
        focusMode: sample.focusMode,
        locked: sample.locked,
        atKeyboard: sample.active,
      }
    },
  },
  {
    name: 'tabs.find',
    group: 'see',
    description:
      'Search the tabs open in the user\'s browsers right now. Answers "do I have a tab open ' +
      'with…" and "find that page about…". Returns matching tabs numbered for tabs.focus. ' +
      'Never answer a question about open tabs from memory — only this can see them.',
    args: { query: 'Words from the tab title or site, e.g. "victoria\'s basement"' },
    async run(args) {
      // A model that calls this with `q`, `search` or `title` meant `query`. The
      // choice is between refusing on a synonym and answering the question the
      // user actually asked; two refused calls in a row cost a whole turn, and
      // taught the model nothing, because the error did not say what to send.
      const text = firstString(args, ['query', 'q', 'search', 'text', 'title', 'term'])
      if (!text) {
        throw new Error(
          'query is required: call tabs.find({"query": "words from the tab title or site"}). ' +
            `Received: ${describeArgs(args)}`,
        )
      }
      const { tabs, denied } = await platform.listTabs()
      const matches = rank(text, tabs).slice(0, 8).map((t) => ({
        ref: `${t.browser}|${t.window}|${t.tab}`,
        // Page titles are ATTACKER-CONTROLLED — any site sets its own — and
        // they land in an LLM's context. Flatten and cap them so a title
        // cannot smuggle newlines and forged instructions into the answer.
        title: sanitize(t.title),
        host: hostOf(t.url),
        browser: t.browser,
      }))
      return { tabs: matches, denied, openTabCount: tabs.length }
    },
  },
  {
    name: 'tabs.focus',
    group: 'act',
    description: 'Bring one tab from the most recent tabs.find result to the front of the screen.',
    args: { ref: 'The `ref` of a tab returned by tabs.find' },
    run: (args) => {
      const ref = firstString(args, ['ref', 'tab', 'id'])
      if (!ref) throw new Error('ref is required: use a `ref` exactly as returned by tabs.find')
      return platform.focusTab(ref)
    },
  },
  {
    name: 'notify',
    group: 'act',
    description:
      'Post a notification on the user\'s machine — the way to reach them when they are not ' +
      'looking at the chat. Text only.',
    args: { title: 'Short title', body: 'One or two lines' },
    async run({ title, body }) {
      if (!Notification.isSupported()) return { ok: false, error: 'notifications unavailable' }
      new Notification({
        title: sanitize(String(title ?? 'Embabel Me'), 120),
        body: sanitize(String(body ?? ''), 400),
      }).show()
      return { ok: true }
    },
  },
  {
    name: 'url.open',
    group: 'act',
    description: 'Open a web page in the user\'s default browser. Web addresses only.',
    args: { url: 'An http:// or https:// address' },
    async run({ url }) {
      const raw = String(url ?? '')
      let parsed
      try {
        parsed = new URL(raw)
      } catch {
        return { ok: false, error: 'not a valid URL' }
      }
      // http(s) ONLY, and this is the whole point of verb-level validation:
      // `file:` reads the disk, and custom schemes are how a URL launches an
      // application with arguments. A generic "open this" would be a much
      // bigger capability than "open a web page".
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        return { ok: false, error: `refused ${parsed.protocol} — only http and https can be opened` }
      }
      await shell.openExternal(parsed.toString())
      return { ok: true }
    },
  },
  {
    name: 'clipboard.write',
    group: 'act',
    description:
      'Put text on the user\'s clipboard so they can paste it. Use when they ask for something ' +
      'to copy. (Reading the clipboard is deliberately not possible.)',
    args: { text: 'The text to place on the clipboard' },
    async run({ text }) {
      const value = String(text ?? '')
      if (!value) return { ok: false, error: 'nothing to copy' }
      clipboard.writeText(value.slice(0, 100_000))
      return { ok: true, characters: Math.min(value.length, 100_000) }
    },
  },
  {
    name: 'apps.running',
    group: 'see',
    description:
      'The applications open on the user\'s Mac right now. Answers "what have I got open" and ' +
      'tells you which tools they actually work in. Windowed applications only — no daemons.',
    args: {},
    async run() {
      const apps = await platform.runningApps()
      return { apps, count: apps.length }
    },
  },
  {
    name: 'machine.state',
    group: 'see',
    description:
      'The machine\'s situation: on battery or mains, battery level, how many displays, whether ' +
      'the screen is locked, how long since the user touched it, and any Focus mode. Use to judge ' +
      'whether this is a good moment, or whether they are at a desk or on the move.',
    args: {},
    run: () => platform.machineState(),
  },
  {
    name: 'files.recent',
    group: 'see',
    description:
      'Files changed recently in the folders the user shared with their appliance — what they ' +
      'have actually been working on. Returns names, folders and times, NEVER contents, each with ' +
      'a `ref` for files.reveal. Ask before guessing what someone is working on.',
    args: {
      within: 'Optional: how far back, e.g. "2h", "3d" (default 24h)',
      match: 'Optional: only files whose name contains this',
    },
    async run(args) {
      const within = parseWithin(firstString(args, ['within', 'since', 'window']) || '24h')
      const match = firstString(args, ['match', 'name', 'contains', 'query']).toLowerCase()
      const found = recentFiles(within, match)
      return {
        files: found.map((f) => ({ ref: f.ref, name: f.name, folder: f.folder, modified: f.modified })),
        searchedFolders: found.folders,
        note: found.length === 0 ? 'No shared folder has changed in that window' : undefined,
      }
    },
  },
  {
    name: 'files.reveal',
    group: 'act',
    description:
      'Show one file from the most recent files.recent result in the Finder, so the user can see ' +
      'where it is. Opens a window; changes nothing.',
    args: { ref: 'The `ref` of a file returned by files.recent' },
    async run(args) {
      const ref = firstString(args, ['ref', 'file', 'id'])
      // BY REF, never by path: the assistant can only reveal something this
      // machine already showed it, so a prompt-injected instruction cannot name
      // an arbitrary file — and a path argument would compose into more than the
      // verb says.
      const target = revealable.get(ref)
      if (!target) return { ok: false, error: 'unknown ref — call files.recent first and use a ref from it' }
      shell.showItemInFolder(target)
      return { ok: true, revealed: path.basename(target) }
    },
  },
  {
    name: 'mail.draft',
    group: 'act',
    description:
      'Open a NEW EMAIL, pre-filled, in the user\'s mail app. It is a draft on their screen: they ' +
      'read, edit and send it themselves. This never sends anything.',
    args: { to: 'Optional recipient address', subject: 'Subject line', body: 'The message' },
    async run(args) {
      // Composed HERE from fields, never from a caller-supplied URL: `url.open`
      // refuses non-http schemes precisely because a scheme launches an
      // application with arguments, and this is the safe, bounded exception.
      const to = firstString(args, ['to', 'recipient', 'address'])
      const subject = firstString(args, ['subject', 'title'])
      const body = firstString(args, ['body', 'text', 'message'])
      if (!subject && !body) return { ok: false, error: 'give a subject or a body to draft' }
      const query = new URLSearchParams()
      if (subject) query.set('subject', sanitize(subject, 200))
      if (body) query.set('body', sanitize(body, 5_000))
      await shell.openExternal(`mailto:${encodeURIComponent(to)}?${query.toString()}`)
      return { ok: true, drafted: true, sent: false }
    },
  },
  {
    name: 'say',
    group: 'act',
    description:
      'Speak a short line aloud on the user\'s Mac. Reaches them when they are not looking at a ' +
      'screen. Keep it to a sentence.',
    args: { text: 'What to say' },
    async run(args) {
      const text = firstString(args, ['text', 'message', 'body'])
      if (!text) return { ok: false, error: 'nothing to say' }
      return platform.speak(sanitize(text, 300))
    },
  },
]

const byName = new Map(VERBS.map((v) => [v.name, v]))

/**
 * The catalog for the groups the user has consented to — this is what the
 * appliance is told exists. A verb absent from here is one the assistant
 * cannot even name, let alone call.
 * @param {{ see?: boolean, act?: boolean }} consent
 */
function catalog(consent) {
  return VERBS.filter((v) => consent?.[v.group]).map((v) => ({
    name: v.name,
    group: v.group,
    description: v.description,
    args: v.args,
  }))
}

/**
 * Run a verb the user has consented to. Unknown or unconsented names are
 * refused HERE — the sensor is the policy point, never the caller.
 * @param {string} name @param {any} args @param {{ see?: boolean, act?: boolean }} consent
 */
async function run(name, args, consent) {
  const verb = byName.get(name)
  if (!verb) return { ok: false, error: `no such verb '${name}' on this machine` }
  if (!consent?.[verb.group]) {
    return { ok: false, error: `the user has not enabled '${GROUPS[verb.group]}' in Embabel Me` }
  }
  return verb.run(args ?? {})
}

/**
 * Files revealed by ref: what files.recent last showed, so files.reveal can act
 * on it without ever accepting a path. Bounded, and replaced on each search.
 */
const revealable = new Map()

/** "2h", "3d", "45m" → milliseconds. Anything unparseable falls back to a day. */
function parseWithin(text) {
  const m = /^(\d+)\s*([mhdw])/i.exec(text.trim())
  if (!m) return 24 * 60 * 60 * 1000
  const n = Number(m[1])
  const unit = m[2].toLowerCase()
  const ms = unit === 'm' ? 60_000 : unit === 'h' ? 3_600_000 : unit === 'd' ? 86_400_000 : 604_800_000
  return Math.min(n * ms, 30 * 86_400_000)
}

/**
 * Recently-changed files across the folders the user already shared with the
 * appliance — the same consent boundary the indexer uses, and no wider. Names
 * and times only; contents never leave through this verb.
 */
function recentFiles(withinMs, match) {
  const state = mounts.state()
  const results = []
  revealable.clear()
  if (!state.supported) return Object.assign([], { folders: 0 })
  const cutoff = Date.now() - withinMs
  let scanned = 0
  for (const mount of state.mounts) {
    walk(mount.host, mount.host, 0)
  }

  function walk(dir, root, depth) {
    if (depth > MAX_DEPTH || scanned > MAX_SCAN || results.length > MAX_RESULTS) return
    let entries
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      if (entry.name.startsWith('.') || SKIP_DIRS.has(entry.name)) continue
      const full = path.join(dir, entry.name)
      scanned++
      if (entry.isDirectory()) {
        walk(full, root, depth + 1)
        continue
      }
      let stat
      try {
        stat = fs.statSync(full)
      } catch {
        continue
      }
      if (stat.mtimeMs < cutoff) continue
      if (match && !entry.name.toLowerCase().includes(match)) continue
      const ref = `f${results.length}`
      revealable.set(ref, full)
      results.push({
        ref,
        name: entry.name,
        folder: path.dirname(full).replace(require('node:os').homedir(), '~'),
        modified: new Date(stat.mtimeMs).toISOString(),
      })
    }
  }

  results.sort((a, b) => (a.modified < b.modified ? 1 : -1))
  return Object.assign(results.slice(0, MAX_RESULTS), { folders: state.mounts.length })
}

const MAX_DEPTH = 6
const MAX_SCAN = 20_000
const MAX_RESULTS = 25
const SKIP_DIRS = new Set(['node_modules', 'target', 'build', 'dist', '.git', 'Library', 'venv', '__pycache__'])

/**
 * The first non-empty string among [names] — so a verb answers when a caller
 * reaches for a reasonable synonym of its argument. The verb's declared name
 * stays the one advertised; this only forgives the call.
 */
function firstString(args, names) {
  if (!args || typeof args !== 'object') return ''
  for (const name of names) {
    const value = args[name]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number') return String(value)
  }
  return ''
}

/** What actually arrived, for an error a model can act on. */
function describeArgs(args) {
  if (!args || typeof args !== 'object') return String(args)
  const keys = Object.keys(args)
  return keys.length === 0 ? 'no arguments' : `{${keys.join(', ')}}`
}

/** Flatten and cap untrusted text before it reaches an LLM's context. */
function sanitize(text, max = 300) {
  return String(text ?? '')
    .replace(/[\p{Cc}\p{Cf}]+/gu, ' ')
    .trim()
    .slice(0, max)
}

/**
 * Score tabs against the query: token hits in title or URL, with a
 * punctuation-blind pass so "victoria's basement" finds victoriasbasement.com.
 * Runs HERE, over every open tab, so the URLs never leave to be searched.
 */
function rank(query, tabs) {
  const squash = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '')
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (tokens.length === 0) return []
  const squashedQuery = squash(query)

  const scored = tabs
    .map((tab) => {
      const plain = `${tab.title} ${tab.url}`.toLowerCase()
      const squashed = squash(plain)
      let hits = 0
      let score = 0
      for (const token of tokens) {
        const bare = squash(token)
        if (plain.includes(token)) {
          score += 3
          hits++
        } else if (bare && squashed.includes(bare)) {
          // Punctuation-blind: "victoria's basement" finds victoriasbasement.com.
          score += 2
          hits++
        } else if (bare.length >= 4 && wordPrefixMatch(squashed, bare)) {
          // A near miss that people actually type: "cypher" for "cyphers",
          // "config" for "configuration". Worth a point, not a full hit.
          score += 1
          hits++
        }
      }
      // The whole phrase, punctuation-blind: "virtual cypher cheat sheet" should
      // beat a tab that merely says "cheat" — and matches "CheatSheet", where the
      // word boundary the user typed does not exist in the title.
      if (squashedQuery && squashed.includes(squashedQuery)) score += 6
      return { tab, score, hits, coverage: hits / tokens.length }
    })
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score)

  // Prefer tabs that matched MOST of what was asked for. Falling back to weaker
  // partials only when nothing covers the query keeps "one match of 163" from
  // being a tab that happened to contain the word "sheet".
  const strong = scored.filter((x) => x.coverage >= 0.6)
  return (strong.length > 0 ? strong : scored).map((x) => x.tab)
}

/** Does any word in [haystack] start with [needle]? Cheap stemming, no dictionary. */
function wordPrefixMatch(haystack, needle) {
  let from = 0
  for (;;) {
    const at = haystack.indexOf(needle, from)
    if (at === -1) return false
    // A prefix hit anywhere is enough once the text is squashed — the squash has
    // already removed the boundaries we would otherwise anchor on.
    if (needle.length >= 4) return true
    from = at + 1
  }
}

function hostOf(url) {
  try {
    return new URL(url).hostname
  } catch {
    return undefined
  }
}

module.exports = { GROUPS, catalog, run }
