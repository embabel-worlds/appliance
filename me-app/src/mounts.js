// Local folder mounts — the host half of the "Local files" panel.
//
// The mounts live in docker-compose.override.yml, next to the compose files in
// the appliance checkout this app runs from. That name is the one file Docker
// Compose merges into plain `docker compose up` BY CONVENTION — so the mounts
// survive every way the door gets opened. (setup.py re-includes the file
// explicitly, because an explicit -f list switches the convention off.) The
// tracked compose files stay pull-only; this one is generated and gitignored.
//
// Folders are mounted READ-ONLY under /local: the appliance may index what is
// there, never change it. Compose merges volume lists by container target, so
// the assistant's own mounts (docker socket, data volume) are untouched.

const { execFile } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

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

const MOUNT_LINE = /^\s*-\s*"(.+):(\/local\/[^:"]+):ro"$/

/** @param {string} dir @returns {LocalMount[]} */
function read(dir) {
  const file = path.join(dir, OVERRIDE)
  if (!fs.existsSync(file)) return []
  const text = fs.readFileSync(file, 'utf8')
  if (!text.startsWith(MARKER)) {
    throw new Error(`${OVERRIDE} exists but was not written by this panel — edit or remove it yourself.`)
  }
  const mounts = []
  for (const line of text.split('\n')) {
    const match = MOUNT_LINE.exec(line)
    if (match) mounts.push({ host: match[1], target: match[2] })
  }
  return mounts
}

/** @param {string} dir @param {LocalMount[]} mounts */
function write(dir, mounts) {
  const file = path.join(dir, OVERRIDE)
  if (mounts.length === 0) {
    // No mounts is NO FILE, not an empty override: `services: assistant: {}`
    // would still merge, and plain `up` should see exactly what is shared.
    if (fs.existsSync(file)) fs.rmSync(file)
    return
  }
  const lines = mounts.map((m) => `      - "${m.host}:${m.target}:ro"`).join('\n')
  fs.writeFileSync(
    file,
    `${MARKER} — the "Local files" panel rewrites this file; don't edit it by hand.\n` +
      `# Folders below are visible READ-ONLY inside the assistant container under\n` +
      `# ${MOUNT_ROOT}, for indexing. Plain \`docker compose up\` merges this file by\n` +
      `# convention; setup.py includes it explicitly. Gitignored — these paths are\n` +
      `# this machine's business.\n` +
      `services:\n  assistant:\n    volumes:\n${lines}\n`,
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
    }
  }
  try {
    return { supported: true, message: '', dir, mounts: read(dir) }
  } catch (e) {
    return { supported: false, message: e instanceof Error ? e.message : String(e), dir, mounts: [] }
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
    mounts.push({ host, target })
  }
  write(current.dir, mounts)
  return { ...current, mounts }
}

/** @param {string} host @returns {MountsState} */
function remove(host) {
  const current = state()
  if (!current.supported || !current.dir) return current
  const mounts = current.mounts.filter((m) => m.host !== host)
  write(current.dir, mounts)
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

/**
 * Recreate the assistant with the current mounts. `up -d` reconciles: compose
 * sees the changed volume list and recreates just the assistant; the graph —
 * and everything the appliance remembers — lives in other containers and
 * stays up throughout.
 * @returns {Promise<ConnectionResult>}
 */
async function apply() {
  const current = state()
  if (!current.supported || !current.dir) return { ok: false, message: current.message }
  // One door at a time is an appliance invariant (the doors share one graph);
  // `up` here would START the me door, so refuse while the other one is up.
  const worlds = await docker(
    ['ps', '--filter', 'label=com.docker.compose.service=worlds', '--format', '{{.Names}}'],
    current.dir,
  )
  if (worlds.ok && worlds.out) {
    return { ok: false, message: `The worlds door is running (${worlds.out}) — stop it before applying mounts.` }
  }
  const up = await docker(['compose', 'up', '-d', 'assistant'], current.dir)
  if (!up.ok) return { ok: false, message: `docker compose up failed: ${up.out.slice(-300)}` }
  return {
    ok: true,
    message: current.mounts.length
      ? `${current.mounts.length} folder(s) mounted under ${MOUNT_ROOT} — give the assistant a moment to restart.`
      : 'No folders shared — mounts removed; give the assistant a moment to restart.',
  }
}

module.exports = { applianceDir, state, add, remove, apply }
