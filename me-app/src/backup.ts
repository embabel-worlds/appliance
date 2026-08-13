// Backup and restore — everything the appliance knows, saved to the host's
// own disk and brought back from it.
//
// WHAT A BACKUP IS. The appliance keeps everything a user would cry about in
// two named volumes — embabel_assistant_data (worlds, documents, artifacts,
// credentials) and embabel_appliance_neo4j_data (the knowledge graph) — plus
// the two host-side files that make the checkout THIS appliance: .env and the
// mounts override. A backup is a plain folder holding a cold tarball of each
// volume, those files, and a manifest saying what made it. A folder rather
// than one enveloping archive on purpose: the volumes inside are already
// compressed, and re-archiving gigabytes buys nothing but a second wait.
//
// COLD ON PURPOSE. Community Neo4j has no online backup, and neo4j-admin dump
// wants the database stopped anyway — so the assistant and the graph are
// stopped, the volumes copied at rest, and whatever was running brought back
// up. A copy taken under a live graph would be corrupt in exactly the cases
// that make someone reach for a backup.
//
// The volume bytes never cross a bind mount: a helper container tars them to
// stdout and this process streams that to the file (and back, on restore) —
// so Docker Desktop's file-sharing list never has an opinion about where
// backups may live.
//
// This is a HOST operation, like Update: a container cannot copy the volume
// it is running from. That is why it lives in the Me app and not the server.

/* The REAL volume names are <project>_<key>. The project name is pinned to
 * embabel-appliance in docker-compose.yml precisely so names like these can
 * never move out from under an install — see the note there. */
import { spawn, execFile } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import * as mounts from './mounts'
import * as appliance from './appliance'
import * as pkg from '../package.json'

const VOLUMES = [
  { name: 'embabel-appliance_embabel_assistant_data', file: 'assistant-data.tgz', what: 'worlds and documents' },
  { name: 'embabel-appliance_embabel_appliance_neo4j_data', file: 'neo4j-data.tgz', what: 'the knowledge graph' },
]

/* Host-side state that shapes the appliance: credentials and ports (.env), the
 * mounts-and-models override this app writes. Without .env a restored graph is
 * unreachable — Neo4j's password lives IN its volume, and the assistant's copy
 * of it lives here. */
const CONFIG_FILES = ['.env', 'docker-compose.override.yml']

/* Has tar, weighs a few MB, and is pinned so a backup taken next year is cut
 * by the same tool as one taken today. */
const HELPER_IMAGE = 'alpine:3.22'

const MANIFEST = 'manifest.json'

/* Copying a graph can legitimately take a long time; this is a backstop
 * against a wedged docker CLI, not a pace expectation. */
const STREAM_TIMEOUT = 30 * 60_000

/**
 * @param {string[]} args @param {string} [cwd] @param {number} [timeout]
 * @returns {Promise<{ok: boolean, out: string}>}
 */
/** What every docker invocation here comes back with: did it work, and what it said. */
export interface Shell {
  ok: boolean
  out: string
}

const docker = (args: string[], cwd: string, timeout = 60_000): Promise<Shell> =>
  new Promise<{ok: boolean, out: string}>((resolve) => {
    execFile('docker', args, { cwd, timeout, maxBuffer: 8 * 1024 * 1024 }, (error, stdout, stderr) => {
      resolve({ ok: !error, out: (stderr || stdout || (error ? error.message : '')).trim() })
    })
  })

/** The end of a long output, visibly truncated — never chopped mid-word in silence. */
const tail = (out: string) => (out.length > 300 ? `…${out.slice(-300)}` : out)

/**
 * Run docker with binary stdout streamed straight to a file — how a volume
 * leaves the machine as a tarball without ever touching a bind mount.
 * @param {string[]} args @param {string} file
 * @returns {Promise<{ok: boolean, out: string}>}
 */
function streamToFile(args: string[], file: string) {
  return new Promise<{ok: boolean, out: string}>((resolve) => {
    const out = fs.createWriteStream(file)
    const child = spawn('docker', args, { stdio: ['ignore', 'pipe', 'pipe'] })
    let err = ''
    child.stderr.on('data', (d) => (err += d))
    child.on('error', (e) => (err += e.message))
    child.stdout.pipe(out)
    const timer = setTimeout(() => child.kill(), STREAM_TIMEOUT)
    child.on('close', (code) => {
      clearTimeout(timer)
      out.close(() => resolve({ ok: code === 0, out: err.trim() }))
    })
  })
}

/**
 * The mirror image: a file streamed into docker's stdin (tar back into a
 * volume). The stdin error handler matters — a container that dies mid-write
 * turns the pipe into an EPIPE, which must become a failed result, not an
 * uncaught exception in the main process.
 * @param {string[]} args @param {string} file
 * @returns {Promise<{ok: boolean, out: string}>}
 */
function streamFromFile(args: string[], file: string) {
  return new Promise<{ok: boolean, out: string}>((resolve) => {
    const child = spawn('docker', args, { stdio: ['pipe', 'pipe', 'pipe'] })
    let err = ''
    child.stderr.on('data', (d) => (err += d))
    child.on('error', (e) => (err += e.message))
    child.stdin.on('error', (e) => (err += ` ${e.message}`))
    fs.createReadStream(file).pipe(child.stdin)
    const timer = setTimeout(() => child.kill(), STREAM_TIMEOUT)
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({ ok: code === 0, out: err.trim() })
    })
  })
}

/** Local time, filesystem-plain: 2026-08-13-1430. A backup is named by when it was taken. */
function timestamp() {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`
}

/** One value out of .env, or '' — enough for the manifest, not a dotenv parser. */
function envValue(dir: string, key: string) {
  try {
    const line = fs
      .readFileSync(path.join(dir, '.env'), 'utf8')
      .split('\n')
      .find((l) => l.startsWith(`${key}=`))
    return line ? line.slice(key.length + 1).trim() : ''
  } catch {
    return ''
  }
}

/**
 * The guards every operation here shares: a checkout to run compose in, a
 * daemon to reach the volumes through, and not the worlds mode — stopping and
 * starting services is me-mode work, and the modes share these very volumes.
 * @returns {Promise<{ok: boolean, message: string, dir: string}>}
 */
async function preflight(verb: string) {
  const dir = mounts.applianceDir()
  if (!dir) return { ok: false, message: 'Appliance checkout not found — run the app from the repo.', dir: '' }
  if (!(await appliance.dockerRunning())) {
    return { ok: false, message: 'Docker is not running — the appliance volumes are only reachable through it.', dir }
  }
  const worlds = await docker(['ps', '--filter', 'label=com.docker.compose.service=worlds', '--format', '{{.Names}}'], dir, 15_000)
  if (worlds.ok && worlds.out) {
    return { ok: false, message: `The worlds mode is running (${worlds.out}) — stop it before ${verb}.`, dir }
  }
  return { ok: true, message: '', dir }
}

/**
 * Back up the appliance into a new timestamped folder under destDir.
 * @param {string} destDir
 * @returns {Promise<{ok: boolean, message: string, path?: string}>}
 */
async function backUp(destDir: string) {
  const gate = await preflight('backing up')
  if (!gate.ok) return gate
  const dir = gate.dir

  // Bring-back is decided by what is running NOW, never assumed: backing up a
  // deliberately stopped appliance must not be the thing that starts it.
  const running = await docker(['compose', 'ps', '--services', '--status', 'running'], dir, 30_000)
  const wasRunning = running.ok && running.out !== ''

  const dest = path.join(destDir, `embabel-backup-${timestamp()}`)
  fs.mkdirSync(dest, { recursive: true })

  const stop = await docker(['compose', 'stop', 'assistant', 'neo4j'], dir, 300_000)
  if (!stop.ok) return { ok: false, message: `Could not stop the appliance: ${tail(stop.out)}` }

  const result = await copyOut(dir, dest)

  // The appliance comes back whether the copy worked or not — a failed backup
  // must never leave the assistant down as a side effect.
  if (wasRunning) {
    const up = await docker(['compose', 'up', '-d'], dir, 300_000)
    if (!up.ok) {
      return { ok: false, message: `${result.message} — and restarting the appliance failed: ${tail(up.out)}` }
    }
  }
  return result
}

/** The copy itself, between the stop and the restart. @returns {Promise<{ok: boolean, message: string, path?: string}>} */
async function copyOut(dir: string, dest: string) {
  const saved = []
  for (const volume of VOLUMES) {
    const exists = await docker(['volume', 'inspect', volume.name], dir, 15_000)
    if (!exists.ok) {
      return { ok: false, message: `Volume ${volume.name} does not exist — has the appliance ever run on this machine?` }
    }
    const copied = await streamToFile(
      ['run', '--rm', '-v', `${volume.name}:/from:ro`, HELPER_IMAGE, 'tar', 'czf', '-', '-C', '/from', '.'],
      path.join(dest, volume.file),
    )
    if (!copied.ok) return { ok: false, message: `Backing up ${volume.what} failed: ${tail(copied.out)}` }
    saved.push(volume.file)
  }

  for (const file of CONFIG_FILES) {
    if (fs.existsSync(path.join(dir, file))) {
      fs.copyFileSync(path.join(dir, file), path.join(dest, file))
      saved.push(file)
    }
  }

  // Enough to answer, a year from now, "what is this and can I restore it
  // here": when, from which appliance version, holding which files. The image
  // IDs matter because two of the version pins are not in the backup itself —
  // the Neo4j tag lives in the tracked compose files (so the checkout COMMIT
  // is the pin), and an unpinned EMBABEL_VERSION is a moving snapshot tag —
  // this records what those names actually resolved to on the day.
  const image = await docker(['inspect', 'embabel-assistant', '--format', '{{.Config.Image}}'], dir, 15_000)
  const neo4jImage = await docker(['inspect', 'embabel-appliance-neo4j', '--format', '{{.Config.Image}}'], dir, 15_000)
  const commit = await new Promise<Shell>((resolve) => {
    execFile('git', ['-C', dir, 'rev-parse', 'HEAD'], { timeout: 10_000 }, (error, stdout) =>
      resolve({ ok: !error, out: error ? '(unknown)' : stdout.trim() }),
    )
  })
  fs.writeFileSync(
    path.join(dest, MANIFEST),
    JSON.stringify(
      {
        createdAt: new Date().toISOString(),
        appVersion: pkg.version,
        embabelVersion: envValue(dir, 'EMBABEL_VERSION') || '(default)',
        assistantImage: image.ok ? image.out : '(unknown)',
        neo4jImage: neo4jImage.ok ? neo4jImage.out : '(unknown)',
        checkoutCommit: commit,
        files: saved,
      },
      null,
      2,
    ) + '\n',
  )
  fs.writeFileSync(
    path.join(dest, 'README.txt'),
    `Embabel appliance backup — ${new Date().toString()}\n\n` +
      `Everything this appliance knew, at rest: the knowledge graph, worlds,\n` +
      `documents, and the settings that shaped them. Restore it from the Me app:\n` +
      `Embabel Me menu → Restore Appliance… and pick this folder.\n\n` +
      `The .env here carries credentials (database password, API keys).\n` +
      `Treat this folder like the keys it holds.\n`,
  )
  return { ok: true, message: `Backed up to ${dest}`, path: dest }
}

/**
 * Is this folder a backup this app can restore? Read before the confirmation
 * dialog, so the dialog can say WHICH backup it is about to overwrite the
 * appliance with — a date, not a folder name, is what someone confirms.
 * @param {string} backupDir
 * @returns {{ok: boolean, message: string, createdAt?: string}}
 */
function inspect(backupDir: string) {
  let manifest
  try {
    manifest = JSON.parse(fs.readFileSync(path.join(backupDir, MANIFEST), 'utf8'))
  } catch {
    return { ok: false, message: `No readable ${MANIFEST} — this folder is not an Embabel backup.` }
  }
  // Both volumes or nothing: a graph without its documents (or the reverse) is
  // an appliance that contradicts itself, which is worse than a refusal.
  for (const volume of VOLUMES) {
    if (!fs.existsSync(path.join(backupDir, volume.file))) {
      return { ok: false, message: `Backup is incomplete — ${volume.file} is missing.` }
    }
  }
  return { ok: true, message: '', createdAt: manifest.createdAt }
}

/**
 * Replace the appliance's data and config with a backup's. Destructive by
 * definition — the caller owns the confirmation dialog; nothing here asks.
 * @param {string} backupDir
 * @returns {Promise<{ok: boolean, message: string}>}
 */
async function restore(backupDir: string) {
  const valid = inspect(backupDir)
  if (!valid.ok) return valid
  const gate = await preflight('restoring')
  if (!gate.ok) return gate
  const dir = gate.dir

  const stop = await docker(['compose', 'stop'], dir, 300_000)
  if (!stop.ok) return { ok: false, message: `Could not stop the appliance: ${tail(stop.out)}` }

  /* Config first, volumes second: .env decides ports and the Neo4j password
   * the restored graph was created under, so compose must read the backup's
   * copy when it brings everything back up. What is being replaced is set
   * aside, not deleted — one .before-restore per file, kept until the next
   * restore overwrites it. Restoring means the backup's world, so a file the
   * backup does NOT have is set aside too — with one refusal: an override
   * without this app's marker was written by a person (see mounts.js), and a
   * restore does not eat their work. */
  for (const file of CONFIG_FILES) {
    const current = path.join(dir, file)
    const replacement = path.join(backupDir, file)
    if (fs.existsSync(current)) {
      if (file === 'docker-compose.override.yml' && !fs.readFileSync(current, 'utf8').startsWith(mounts.MARKER)) {
        return { ok: false, message: `${file} was hand-written, not generated — move it aside yourself, then restore again.` }
      }
      fs.renameSync(current, `${current}.before-restore`)
    }
    if (fs.existsSync(replacement)) fs.copyFileSync(replacement, current)
  }

  /* A fresh machine has no volumes yet. Let COMPOSE create them — a volume
   * made by `docker run` lacks compose's project labels, and `up` on some
   * versions refuses to adopt it. `up --no-start` also pulls images, so a
   * first restore on a clean machine is a long one; on an existing install
   * both volumes exist and this never runs. */
  const missing = []
  for (const volume of VOLUMES) {
    const exists = await docker(['volume', 'inspect', volume.name], dir, 15_000)
    if (!exists.ok) missing.push(volume.name)
  }
  if (missing.length) {
    const create = await docker(['compose', 'up', '--no-start'], dir, 900_000)
    if (!create.ok) return { ok: false, message: `Could not create the appliance's volumes: ${tail(create.out)}` }
  }

  for (const volume of VOLUMES) {
    const restored = await streamFromFile(
      ['run', '--rm', '-i', '-v', `${volume.name}:/to`, HELPER_IMAGE, 'sh', '-c', 'find /to -mindepth 1 -delete && tar xzf - -C /to'],
      path.join(backupDir, volume.file),
    )
    if (!restored.ok) return { ok: false, message: `Restoring ${volume.what} failed: ${tail(restored.out)}` }
  }

  const up = await docker(['compose', 'up', '-d'], dir, 300_000)
  if (!up.ok) return { ok: false, message: `Data restored, but starting the appliance failed: ${tail(up.out)}` }

  const when = valid.createdAt ? new Date(valid.createdAt).toLocaleString() : 'an unknown time'
  return { ok: true, message: `Restored the backup from ${when} — give the appliance a moment to come up.` }
}

export { backUp, restore, inspect }
