// Local folder mounts — the host half of the "Local files" panel.
//
// The mounts live in docker-compose.override.yml, next to the compose files in
// the appliance checkout this app runs from. That name is the one file Docker
// Compose merges into plain `docker compose up` BY CONVENTION — so the mounts
// survive every way the mode gets opened. (setup.py re-includes the file
// explicitly, because an explicit -f list switches the convention off.) The
// tracked compose files stay pull-only; this one is generated and gitignored.
//
// Folders are mounted READ-ONLY under /local: the appliance may index what is
// there, never change it. Compose merges volume lists by container target, so
// the assistant's own mounts (docker socket, data volume) are untouched.

const { execFile } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const provision = require('./provision')

/** @typedef {import('./types').ConnectionResult} ConnectionResult */
/** @typedef {import('./types').LocalMount} LocalMount */
/** @typedef {import('./types').MountsState} MountsState */

const OVERRIDE = 'docker-compose.override.yml'
const MOUNT_ROOT = '/local'
// First line of every file this panel writes. A file WITHOUT it was written by
// a person, and this panel refuses to touch it rather than eat their work.
const MARKER = '# Written by Embabel Me'

/**
 * The appliance checkout: the nearest ancestor holding docker-compose-me.yml.
 * The spike runs from the repo (me-app/ sits inside it), so walking up from
 * this source directory finds it; a packaged app would need a setting.
 * @returns {string | null}
 */
function applianceDir() {
  let dir = __dirname
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(dir, 'docker-compose-me.yml'))) return dir
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return null
}

// The trailing `# index` comment is this panel's own annotation — compose
// ignores YAML comments, so the override file stays the single source of truth
// for BOTH what is mounted and what the user opted into indexing.
const MOUNT_LINE = /^\s*-\s*"(.+):(\/local\/[^:"]+):ro"(\s*# index)?\s*$/
// Environment the app sets on the assistant service — today the appliance-wide
// default model. Same file because compose merges ONE override by convention;
// two files would need two -f flags and would not survive a plain `up`.
const ENV_LINE = /^\s*-\s*([A-Z0-9_]+)=(.*)$/

/**
 * Everything the app owns in the override file. BOTH halves are read together
 * because write() rewrites the whole file: reading only mounts and writing back
 * would silently drop the environment, and vice versa.
 * @param {string} dir @returns {{ mounts: LocalMount[], env: Record<string,string> }}
 */
function read(dir) {
  const file = path.join(dir, OVERRIDE)
  if (!fs.existsSync(file)) return { mounts: [], env: {} }
  const text = fs.readFileSync(file, 'utf8')
  if (!text.startsWith(MARKER)) {
    throw new Error(`${OVERRIDE} exists but was not written by this panel — edit or remove it yourself.`)
  }
  const mounts = []
  /** @type {Record<string,string>} */
  const env = {}
  for (const line of text.split('\n')) {
    const mount = MOUNT_LINE.exec(line)
    if (mount) {
      mounts.push({ host: mount[1], target: mount[2], index: !!mount[3] })
      continue
    }
    const variable = ENV_LINE.exec(line)
    if (variable) env[variable[1]] = variable[2]
  }
  return { mounts, env }
}

/** @param {string} dir @param {LocalMount[]} mounts @param {Record<string,string>} env */
function write(dir, mounts, env = {}) {
  const file = path.join(dir, OVERRIDE)
  const entries = Object.entries(env).filter(([, v]) => v !== '' && v != null)
  if (mounts.length === 0 && entries.length === 0) {
    // Nothing to say is NO FILE, not an empty override: `services: assistant: {}`
    // would still merge, and plain `up` should see exactly what is set.
    if (fs.existsSync(file)) fs.rmSync(file)
    return
  }
  const volumes = mounts.length
    ? `    volumes:\n${mounts.map((m) => `      - "${m.host}:${m.target}:ro"${m.index ? ' # index' : ''}`).join('\n')}\n`
    : ''
  const environment = entries.length
    ? `    environment:\n${entries.map(([k, v]) => `      - ${k}=${v}`).join('\n')}\n`
    : ''
  fs.writeFileSync(
    file,
    `${MARKER} — the Local files and Models panels rewrite this file; don't edit it by hand.\n` +
      `# Folders below are visible READ-ONLY inside the assistant container under\n` +
      `# ${MOUNT_ROOT}, for indexing. Any environment below is appliance settings the\n` +
      `# app manages. Plain \`docker compose up\` merges this file by convention;\n` +
      `# setup.py includes it explicitly. Gitignored — this is this machine's business.\n` +
      `services:\n  assistant:\n${volumes}${environment}`,
  )
}

/** @returns {MountsState} */
function state() {
  const dir = applianceDir()
  if (!dir) {
    return {
      supported: false,
      message: 'Appliance checkout not found — run the app from the repo (me-app/).',
      dir: null,
      mounts: [],
      env: {},
    }
  }
  try {
    const { mounts, env } = read(dir)
    return { supported: true, message: '', dir, mounts, env }
  } catch (e) {
    return { supported: false, message: e instanceof Error ? e.message : String(e), dir, mounts: [], env: {} }
  }
}

/**
 * A container-side name for a host folder: its basename made filesystem-plain,
 * deduplicated against what is already mounted.
 * @param {string} host @param {Set<string>} taken
 */
function targetFor(host, taken) {
  const base = (path.basename(host) || 'folder').replace(/[^A-Za-z0-9._-]+/g, '-')
  let name = base
  for (let n = 2; taken.has(`${MOUNT_ROOT}/${name}`); n++) name = `${base}-${n}`
  return `${MOUNT_ROOT}/${name}`
}

/** @param {string[]} hosts @returns {MountsState} */
function add(hosts) {
  const current = state()
  if (!current.supported || !current.dir) return current
  const mounts = [...current.mounts]
  const taken = new Set(mounts.map((m) => m.target))
  for (const host of hosts) {
    // Quotes and backslashes break the double-quoted YAML scalar; a colon breaks
    // compose's short volume syntax. Vanishingly rare in real folder names —
    // refuse rather than write a file compose will choke on.
    if (/["\\:]/.test(host)) {
      return { ...current, message: `Cannot share ${host} — quotes, colons or backslashes in the path confuse compose.` }
    }
    if (mounts.some((m) => m.host === host)) continue
    const target = targetFor(host, taken)
    taken.add(target)
    mounts.push({ host, target, index: false })
  }
  write(current.dir, mounts, current.env)
  return { ...current, mounts }
}

/** @param {string} host @returns {MountsState} */
function remove(host) {
  const current = state()
  if (!current.supported || !current.dir) return current
  const mounts = current.mounts.filter((m) => m.host !== host)
  write(current.dir, mounts, current.env)
  return { ...current, mounts }
}

/**
 * Tick or untick a folder for document indexing. The flag is only an intent
 * until Apply runs — indexing rides the same restart-then-ingest flow.
 * @param {string} host @param {boolean} index @returns {MountsState}
 */
function setIndex(host, index) {
  const current = state()
  if (!current.supported || !current.dir) return current
  const mounts = current.mounts.map((m) => (m.host === host ? { ...m, index } : m))
  write(current.dir, mounts, current.env)
  return { ...current, mounts }
}

/**
 * @param {string[]} args @param {string} cwd
 * @returns {Promise<{ ok: boolean, out: string }>}
 */
const docker = (args, cwd) =>
  new Promise((resolve) => {
    execFile('docker', args, { cwd, timeout: 180_000 }, (error, stdout, stderr) => {
      resolve({ ok: !error, out: (stderr || stdout).trim() })
    })
  })

/** The end of a long output, visibly truncated — never chopped mid-word in silence. */
const tail = (out) => (out.length > 300 ? `…${out.slice(-300)}` : out)

/**
 * True when the running assistant container already carries this mount — the
 * distinction that lets an "index contents" tick start indexing IMMEDIATELY
 * (the files are visible in the container right now), while a freshly added
 * folder still waits for Apply to create its mount.
 * @param {string} target @returns {Promise<boolean>}
 */
async function isLive(target) {
  const run = await docker(
    ['inspect', 'embabel-assistant', '--format', '{{range .Mounts}}{{.Destination}}\n{{end}}'],
    applianceDir() ?? '.',
  )
  return run.ok && run.out.split('\n').map((l) => l.trim()).includes(target)
}

/**
 * Set (or clear, with an empty value) one appliance environment variable the app
 * manages — today the appliance-wide default model.
 *
 * This is a BOOT-TIME setting, unlike an LLM role: model beans are built during
 * startup from the provider configuration, so the value lands in the override
 * file and takes effect on the next `apply()`. Saying so is the whole reason
 * this is separate from the role picker, which is live.
 *
 * @param {string} key @param {string} value @returns {MountsState}
 */
function setEnv(key, value) {
  const current = state()
  if (!current.supported || !current.dir) return current
  const env = { ...current.env }
  if (value) env[key] = value
  else delete env[key]
  write(current.dir, current.mounts, env)
  return state()
}

/**
 * Recreate the assistant with the current mounts, provisioning every world so
 * virtual Cypher can walk them (see provision.js). `up -d` reconciles: compose
 * sees the changed volume list and recreates just the assistant; the graph —
 * and everything the appliance remembers — lives in other containers and
 * stays up throughout.
 * @returns {Promise<ConnectionResult>}
 */
async function apply() {
  const current = state()
  if (!current.supported || !current.dir) return { ok: false, message: current.message }
  // One mode at a time is an appliance invariant (the modes share one graph);
  // `up` here would START the me mode, so refuse while the other one is up.
  const worlds = await docker(
    ['ps', '--filter', 'label=com.docker.compose.service=worlds', '--format', '{{.Names}}'],
    current.dir,
  )
  if (worlds.ok && worlds.out) {
    return { ok: false, message: `The worlds mode is running (${worlds.out}) — stop it before applying mounts.` }
  }

  // Provision BEFORE the restart, straight into the data volume, so the boot
  // that follows loads the wiring. With no mounts left there is nothing to
  // point the walk at — leave the worlds as they are (dangling wiring is
  // inert: a missing data/local root simply yields no rows).
  const wired = current.mounts.length > 0 ? await provision.provision() : { changed: false, worlds: 0, warnings: [] }
  if (wired.error) return { ok: false, message: wired.error }

  const up = await docker(['compose', 'up', '-d', 'assistant'], current.dir)
  if (!up.ok) return { ok: false, message: `docker compose up failed: ${tail(up.out)}` }
  // World config is read at boot. An unchanged mount list means `up` recreated
  // nothing — force the reload the provisioning just made necessary.
  if (wired.changed && !/recreat/i.test(up.out)) {
    const restart = await docker(['compose', 'restart', 'assistant'], current.dir)
    if (!restart.ok) return { ok: false, message: `docker compose restart failed: ${restart.out.slice(-300)}` }
  }

  const wiring =
    (wired.worlds ? ` and wired into virtual Cypher for ${wired.worlds} world(s)` : '') +
    (wired.warnings.length ? ` — NOTE: ${wired.warnings.join('; ')}` : '')
  return {
    ok: true,
    message: current.mounts.length
      ? `${current.mounts.length} folder(s) mounted under ${MOUNT_ROOT}${wiring} — give the assistant a moment to restart.`
      : 'No folders shared — mounts removed; give the assistant a moment to restart.',
  }
}

module.exports = {
  setEnv, applianceDir, state, add, remove, setIndex, isLive, apply }
