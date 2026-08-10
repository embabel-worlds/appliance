// World provisioning — wiring the /local mounts into virtual Cypher.
//
// The assistant's `files` producer walks roots RELATIVE to a world's data/ tree
// and follows symlinks only into directories the world's config trusts
// (filesTrustedRoots — pinned human-only at the app's own write seam, so the
// assistant can never widen it; this panel, acting for the human who just
// picked the folders, is exactly the out-of-band actor that setting expects).
// Provisioning therefore means, for EVERY world in the appliance data volume:
//
//   data/local -> /local                      the symlink the walk enters
//   config/world.yml: filesTrustedRoots [/local]   the trust that lets it
//   config/types/local-files.yml              File + Folder virtual types
//   config/producers/local-files.yml          the four `files` producers
//
// All of it lands in the data VOLUME, not a container — done with a throwaway
// alpine run so it works whether or not the assistant is up, and survives the
// recreate that Apply triggers right after. Files this panel writes carry a
// marker first line; a file without the marker is someone's hand work and is
// left alone (warned, never overwritten).

const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

/** The appliance data volume: compose project `embabel-appliance` (pinned by
 *  `name:` in the compose files) + the `embabel_assistant_data` volume key.
 *  Overridable for tests and unconventional installs. */
const VOLUME = process.env.EMBABEL_APPLIANCE_VOLUME || 'embabel-appliance_embabel_assistant_data'
const HELPER_IMAGE = 'alpine:3.20'
const MARKER = 'Written by Embabel Me'

/** @param {string} name @returns {string} base64 of the bundled resource */
const resourceB64 = (name) =>
  fs.readFileSync(path.join(__dirname, '..', 'resources', name)).toString('base64')

// The in-volume script. Reports one WORLD line per world touched, WARN lines
// for anything left alone, and a final CHANGED/UNCHANGED verdict so the caller
// knows whether the assistant needs a restart to reload world config.
const SCRIPT = `
BASE=/data/embabel/assistant/users
[ -d "$BASE" ] || { echo NOWORLDS; exit 0; }
CHANGED=0
write_owned() {
  if [ -f "$1" ] && ! head -1 "$1" | grep -q "${MARKER}"; then
    echo "WARN $1 exists and was not written by the Me app - left alone"
    return
  fi
  new="$(echo "$2" | base64 -d)"
  if [ ! -f "$1" ] || [ "$new" != "$(cat "$1")" ]; then
    printf '%s\\n' "$new" > "$1"
    CHANGED=1
  fi
}
for w in "$BASE"/*/*; do
  [ -d "$w/config" ] || continue
  mkdir -p "$w/data" "$w/config/types" "$w/config/producers"
  if [ "$(readlink "$w/data/local" 2>/dev/null)" != "/local" ]; then
    ln -sfn /local "$w/data/local"
    CHANGED=1
  fi
  wy="$w/config/world.yml"
  [ -f "$wy" ] || printf '%s\\n' "# World configuration." > "$wy"
  if ! grep -q filesTrustedRoots "$wy"; then
    printf '\\n%s\\n%s\\n%s\\n' \\
      "# ${MARKER} - trusts the read-only /local folder mounts (Local files panel)." \\
      "filesTrustedRoots:" \\
      "  - /local" >> "$wy"
    CHANGED=1
  elif ! grep -q /local "$wy"; then
    echo "WARN $wy declares filesTrustedRoots without /local - add '- /local' by hand"
  fi
  write_owned "$w/config/types/local-files.yml" "$TYPES_B64"
  write_owned "$w/config/producers/local-files.yml" "$PRODUCERS_B64"
  echo "WORLD $w"
done
[ "$CHANGED" = 1 ] && echo CHANGED || echo UNCHANGED
`

/**
 * @param {string[]} args @param {string} [input]
 * @returns {Promise<{ code: number, out: string }>}
 */
const run = (args, input) =>
  new Promise((resolve) => {
    const child = spawn('docker', args, { stdio: ['pipe', 'pipe', 'pipe'] })
    let out = ''
    child.stdout.on('data', (d) => (out += d))
    child.stderr.on('data', (d) => (out += d))
    child.on('error', (e) => resolve({ code: 1, out: String(e) }))
    child.on('close', (code) => resolve({ code: code ?? 1, out }))
    if (input) child.stdin.write(input)
    child.stdin.end()
  })

/**
 * Provision every world for the /local mounts. Never throws — an appliance
 * that has no data volume yet (mode never started) is a normal outcome.
 * @returns {Promise<{ changed: boolean, worlds: number, warnings: string[], error?: string }>}
 */
async function provision() {
  const none = { changed: false, worlds: 0, warnings: [] }
  const volume = await run(['volume', 'inspect', VOLUME])
  if (volume.code !== 0) return none // fresh machine: nothing to provision yet

  const result = await run(
    [
      'run', '-i', '--rm',
      '-e', `TYPES_B64=${resourceB64('local-files-types.yml')}`,
      '-e', `PRODUCERS_B64=${resourceB64('local-files-producers.yml')}`,
      '-v', `${VOLUME}:/data`,
      HELPER_IMAGE, 'sh', '-s',
    ],
    SCRIPT,
  )
  if (result.code !== 0) return { ...none, error: `provisioning failed: ${result.out.trim().slice(-300)}` }

  const lines = result.out.split('\n').map((l) => l.trim())
  return {
    changed: lines.includes('CHANGED'),
    worlds: lines.filter((l) => l.startsWith('WORLD ')).length,
    warnings: lines.filter((l) => l.startsWith('WARN ')).map((l) => l.slice(5)),
  }
}

module.exports = { provision }
