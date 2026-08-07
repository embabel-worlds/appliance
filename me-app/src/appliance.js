// Is there an appliance to talk to at all, and if not, why not?
//
// The app assumes the appliance is already running. When it is not, the
// difference between "Docker isn't installed", "Docker isn't running" and "the
// appliance isn't started" is the whole of what the user needs to know — and
// each has a different next step. Reporting "connection refused" for all three
// is a dead end dressed as an error message.
//
// Nothing here starts anything. Installing Docker is Docker's own onboarding,
// and starting the appliance is `me.py`'s job; this module only diagnoses, so
// the app can point rather than guess.

const { execFile } = require('node:child_process')
const { promisify } = require('node:util')

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

module.exports = { diagnose, dockerInstalled, dockerRunning, DOCKER_URL }
