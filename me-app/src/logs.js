/*
 * Container logs — `docker logs -f`, piped to a window.
 *
 * Read-only by construction: this module can list the appliance's containers
 * and follow their output, and that is the whole surface. No start, no stop, no
 * exec — the app already has narrow, deliberate seams for changing the
 * appliance (appliance.js, mounts.js), and a log viewer is not one of them.
 *
 * One stream at a time. The window that watches is the thing that keeps it
 * alive; when it closes, or the user switches container, the old child is
 * killed rather than left following into a dead pipe.
 */

const { spawn, execFile } = require('node:child_process')

/** Compose pins this project name in the compose files; containers carry it as a label. */
const PROJECT = process.env.EMBABEL_APPLIANCE_PROJECT || 'embabel-appliance'

/** How much history to show on attach. Anything unbounded on a long-running
 *  container floods the renderer with megabytes nobody scrolls back to. */
const TAIL_CHOICES = [200, 1000, 5000]

/**
 * The appliance's containers, running first — including stopped ones, whose
 * logs are usually the interesting ones after a crash.
 * @returns {Promise<{ok: boolean, message: string, containers: {name: string, service: string, state: string}[]}>}
 */
function list() {
  return new Promise((resolve) => {
    execFile(
      'docker',
      ['ps', '-a', '--filter', `label=com.docker.compose.project=${PROJECT}`,
        '--format', '{{.Names}}\t{{.Label "com.docker.compose.service"}}\t{{.State}}'],
      { timeout: 15_000 },
      (error, stdout, stderr) => {
        if (error) {
          return resolve({ ok: false, message: (stderr || error.message).trim(), containers: [] })
        }
        const containers = stdout
          .split('\n')
          .map((line) => line.split('\t'))
          .filter((parts) => parts[0])
          .map(([name, service, state]) => ({ name, service: service || name, state: state || '' }))
          .sort((a, b) => {
            const up = (c) => (c.state === 'running' ? 0 : 1)
            // The assistant is what anyone opening this wants to read first.
            const first = (c) => (c.service === 'assistant' ? 0 : 1)
            return up(a) - up(b) || first(a) - first(b) || a.name.localeCompare(b.name)
          })
        resolve({ ok: true, message: '', containers })
      },
    )
  })
}

/** @type {import('node:child_process').ChildProcess | null} */
let child = null

/** Kill whatever is streaming now. Safe to call when nothing is. */
function stop() {
  if (!child) return
  const dying = child
  child = null
  dying.kill('SIGTERM')
}

/**
 * Follow one container. `onChunk` gets raw text as it arrives — docker
 * interleaves stdout and stderr and so do we, because that is the order the
 * container actually wrote them in. `onEnd` fires once, when the stream dies:
 * the container stopped, docker went away, or we killed it.
 *
 * @param {string} name container name
 * @param {number} tail history lines to replay before following
 * @param {(text: string) => void} onChunk
 * @param {(message: string) => void} onEnd
 */
function start(name, tail, onChunk, onEnd) {
  stop()
  const lines = TAIL_CHOICES.includes(tail) ? tail : TAIL_CHOICES[0]
  const proc = spawn('docker', ['logs', '-f', '--tail', String(lines), '--timestamps', name])
  child = proc
  const mine = () => child === proc // a later start() disowns this one
  proc.stdout.on('data', (d) => mine() && onChunk(String(d)))
  proc.stderr.on('data', (d) => mine() && onChunk(String(d)))
  proc.on('error', (e) => {
    if (mine()) { child = null; onEnd(String(e.message || e)) }
  })
  proc.on('close', () => {
    if (mine()) { child = null; onEnd('stream ended — the container stopped, or docker did') }
  })
}

module.exports = { list, start, stop, TAIL_CHOICES }
