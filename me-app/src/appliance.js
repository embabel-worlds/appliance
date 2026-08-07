// The appliance from the host's side: is there one to talk to (and if not,
// why not), and bringing it up to date.
//
// DIAGNOSIS — the app assumes the appliance is already running. When it is
// not, the difference between "Docker isn't installed", "Docker isn't running"
// and "the appliance isn't started" is the whole of what the user needs to
// know — and each has a different next step. Reporting "connection refused"
// for all three is a dead end dressed as an error message. Nothing in the
// diagnosis path starts anything: installing Docker is Docker's own
// onboarding, and starting the appliance is `me.py`'s job.
//
// UPDATE — the appliance is pull-only by design (a git checkout of compose
// files plus registry images), so updating is exactly: fast-forward the
// checkout, pull newer images, `docker compose up -d` to reconcile. Only
// containers whose image changed restart; the graph and data volumes ride
// through untouched, and the mounts override is auto-included as always. The
// Me app is the right home for this button because updating is a HOST
// operation — a container cannot re-pull itself.

const { execFile } = require('node:child_process')
const { promisify } = require('node:util')
const mounts = require('./mounts')

const run = promisify(execFile)

/** @typedef {'ok' | 'no-docker' | 'docker-stopped' | 'not-running'} ApplianceState */

/**
 * @typedef {object} ApplianceDiagnosis
 * @property {ApplianceState} state
 * @property {string} message   What happened, in one line.
 * @property {string} [action]  What the user should do about it.
 * @property {string} [url]     Where that action lives, if anywhere.
 */

const DOCKER_URL = 'https://docs.docker.com/get-started/get-docker/'

/** Is the `docker` command present? */
async function dockerInstalled() {
  try {
    await run('docker', ['--version'], { timeout: 5000 })
    return true
  } catch {
    return false
  }
}

/** Is the Docker daemon actually up? `docker info` fails when it is not. */
async function dockerRunning() {
  try {
    await run('docker', ['info'], { timeout: 10000 })
    return true
  } catch {
    return false
  }
}

/**
 * Work out why the appliance could not be reached. Called only after a
 * connection attempt has already failed, so the cheap network case is settled
 * and the expensive Docker probes run at most once per failure — never on the
 * happy path.
 *
 * @param {string} baseUrl
 * @returns {Promise<ApplianceDiagnosis>}
 */
async function diagnose(baseUrl) {
  if (!(await dockerInstalled())) {
    return {
      state: 'no-docker',
      message: 'Docker is not installed on this Mac.',
      action:
        'The appliance runs as Docker containers, so Docker Desktop has to come first. ' +
        'Install it, enable Model Runner (Settings → AI), then run ./me.py.',
      url: DOCKER_URL,
    }
  }
  if (!(await dockerRunning())) {
    return {
      state: 'docker-stopped',
      message: 'Docker is installed but not running.',
      action: 'Start Docker Desktop, wait for it to settle, then run ./me.py.',
    }
  }
  return {
    state: 'not-running',
    message: `Docker is running, but nothing answered at ${baseUrl}.`,
    action:
      'The appliance itself is not started. In the appliance directory: ./me.py — ' +
      'or check the URL above if you moved it off the default port.',
  }
}

/**
 * @param {string} cmd @param {string[]} args @param {number} timeout @param {string} [cwd]
 * @returns {Promise<{ok: boolean, out: string}>}
 */
async function tryRun(cmd, args, timeout, cwd) {
  try {
    const { stdout, stderr } = await run(cmd, args, { timeout, cwd })
    return { ok: true, out: `${stdout}\n${stderr}`.trim() }
  } catch (e) {
    const err = /** @type {{stdout?: string, stderr?: string, message?: string}} */ (e)
    return { ok: false, out: (err.stderr || err.stdout || err.message || String(e)).trim() }
  }
}

/** The image the assistant container is running, or '' when it is not up. */
async function assistantImage() {
  const r = await tryRun('docker', ['inspect', 'embabel-assistant', '--format', '{{.Image}}'], 10_000)
  return r.ok ? r.out : ''
}

/** When an image was built, as epoch millis — 0 when unknowable. */
async function imageCreated(id) {
  if (!id) return 0
  const r = await tryRun('docker', ['image', 'inspect', id, '--format', '{{.Created}}'], 10_000)
  return r.ok ? Date.parse(r.out) || 0 : 0
}

/**
 * Bring the whole appliance up to date: checkout, images, containers.
 * @returns {Promise<{ok: boolean, message: string, appRestartAdvised: boolean}>}
 */
async function update() {
  const dir = mounts.applianceDir()
  if (!dir) return { ok: false, message: 'Appliance checkout not found — run the app from the repo.', appRestartAdvised: false }

  // One door at a time: plain `up -d` opens the me door, and doing that while
  // the worlds door runs would double every scheduled job. Same guard as Apply.
  const worlds = await tryRun('docker', ['ps', '--filter', 'label=com.docker.compose.service=worlds', '--format', '{{.Names}}'], 15_000)
  if (worlds.ok && worlds.out) {
    return { ok: false, message: `The worlds door is running (${worlds.out}) — stop it before updating.`, appRestartAdvised: false }
  }

  const notes = []
  let appRestartAdvised = false

  // 1. The checkout: compose files, provisioning resources, this very app.
  //    --ff-only NEVER rewrites local work — a dirty or diverged tree is
  //    reported and left alone, and images still update below.
  const headBefore = (await tryRun('git', ['-C', dir, 'rev-parse', 'HEAD'], 10_000)).out
  const pull = await tryRun('git', ['-C', dir, 'pull', '--ff-only'], 120_000)
  if (!pull.ok) {
    notes.push('checkout not updated (local changes or no remote) — images only')
  } else {
    const headAfter = (await tryRun('git', ['-C', dir, 'rev-parse', 'HEAD'], 10_000)).out
    if (headBefore && headAfter && headBefore !== headAfter) {
      notes.push('checkout updated')
      const changed = await tryRun('git', ['-C', dir, 'diff', '--name-only', `${headBefore}..${headAfter}`], 15_000)
      appRestartAdvised = changed.ok && changed.out.includes('me-app/')
    }
  }

  // 2. Newer images. Slow on a real update (image layers), quick when current.
  const imageBefore = await assistantImage()
  const imagePull = await tryRun('docker', ['compose', 'pull'], 900_000, dir)
  if (!imagePull.ok) {
    return { ok: false, message: `docker compose pull failed: …${imagePull.out.slice(-300)}`, appRestartAdvised }
  }

  // 3. Reconcile. Only containers whose image (or config) changed restart.
  const up = await tryRun('docker', ['compose', 'up', '-d'], 300_000, dir)
  if (!up.ok) {
    return { ok: false, message: `docker compose up failed: …${up.out.slice(-300)}`, appRestartAdvised }
  }
  const imageAfter = await assistantImage()

  if (imageBefore && imageAfter && imageBefore !== imageAfter) {
    notes.unshift('assistant updated — give it a moment to restart')
    // A developer's locally-built image can be NEWER than what the registry
    // serves (snapshot tags publish on release, not on every push). Replacing
    // it is a silent regression unless someone says so — so say so.
    const [before, after] = [await imageCreated(imageBefore), await imageCreated(imageAfter)]
    if (before && after && after < before) {
      notes.push('NOTE: the pulled image is OLDER than what was running — if that was your local build, docker/build.sh restores it')
    }
  } else if (notes.length === 0) {
    notes.push('already up to date')
  } else {
    notes.push('images already current')
  }
  if (appRestartAdvised) notes.push('this app changed too — quit and reopen it to finish')
  return { ok: true, message: notes.join(' · '), appRestartAdvised }
}

module.exports = { diagnose, dockerInstalled, dockerRunning, update, DOCKER_URL }
