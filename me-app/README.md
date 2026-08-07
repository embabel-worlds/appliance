# Embabel Me — sensor app (spike)

The Me side of the appliance/sensor architecture in [DISCOVERY.md](../DISCOVERY.md):
a small native app that reads local signals and **sends** them to your own
appliance. The appliance thinks; this app senses.

macOS is the implemented platform. Everything OS-specific lives behind the
`SensorPlatform` seam in [`src/platform/`](src/platform): `common.js` holds the
readers whose logic is identical everywhere and whose only OS-specific part is
where the files live (JetBrains recents, VS Code folders, Chromium history),
`macos.js` holds the Mac-specific ones, and `windows.js`/`linux.js` implement
what already works while naming exactly what each still needs. `process.platform`
is read in one place.

## Run it

```bash
cd me-app
npm start        # the first run fetches Electron by itself
```

(`../me.py` offers to do this for you at the end of setup.) The app is plain
JavaScript with no build step — Electron is the only thing `npm` fetches, and
there is nothing to compile.

Requires the Me door up (`../me.py`) and your appliance username/password.

v1 reads, with no special permissions:

- the Dock (apps you keep at hand, in order)
- default browser and mail handlers (LaunchServices)
- recent JetBrains projects and open VS Code folders
- installed applications

You review every fact, untick what you don't want shared, and send. Facts go to
`POST /api/v1/sensor/events` with your credentials — they land in your
assistant's memory as propositions, deduplicated server-side, with a per-event
receipt. Against an older image without the sensor endpoint, fact sending falls
back to `POST /api/v1/memory/remember`. Nothing is read beyond the listed
sources; nothing is sent without the button press.

There is also an opt-in **ambient focus stream** — what you're doing right now,
in two tiers:

- **Tier 0, no permission prompt at all**: frontmost app (`lsappinfo`), screen
  lock, Focus/Do-Not-Disturb mode, keyboard idle. Samples every 5 seconds
  (~32ms per sample).
- **Tier 1, one macOS Automation grant per app**: the active browser tab (title
  and host — never the full URL) and what's playing. Probed only when the
  frontmost app changes, or every ~20s as a refresh, because each probe is an
  AppleScript round-trip. macOS prompts once per target app; if you decline,
  the app says so and links straight to the right System Settings pane.

Lock, unlock, sleep and wake are delivered as events rather than polled, so
"gone" is never a tick late. A tick only *sends* when the picture changed, with
a ~3-minute heartbeat otherwise. Each push replaces the previous one in the
appliance's in-memory context layer — a live view for the attention system, not
a stored history. Requires the sensor endpoint (assistant `me` branch).

**Browser history is opt-in** — tick "Include browser history" before scanning and
the sensor also reports most-visited sites, which news outlets you read (with the
headlines you actually opened), and recent web searches. Read from a *copy* of the
Chromium history database via the system `sqlite3`, because the live file is locked
while the browser runs; Chromium needs no Full Disk Access, and Safari — which does
— is deliberately not touched. This is what lets the assistant answer "what kind of
news do I read?" with outlets and stories rather than a guess from your Dock.

**Local files** shares folders with your appliance so it can index what's in
them. Pick folders (Documents, a projects directory); each is bind-mounted
**read-only** into the assistant container under `/local/<name>`. The panel
writes `../docker-compose.override.yml` — the one file name Docker Compose
merges into plain `docker compose up` by convention, and which `setup.py` also
includes explicitly — then **Apply** recreates the assistant container with the
mounts (the graph and everything the appliance remembers stay up). Before the
restart, the panel sends one fact per folder so the assistant knows where its
new files live; ask it to index `/local/<name>` and the documents become part
of its graph. The file is gitignored and never touches the tracked compose
files; removing every folder deletes it again.

`npm run smoke` prints what a scan would gather without starting the UI;
`node -e "require('./src/platform').platform.scan({browserHistory:true}).then(console.log)"`
includes the history facts.

## Design notes

- **Outbound-only.** The app is a client of the appliance API and never listens
  on a port. Future effect verbs (notify, open URL…) will ride the same
  direction via long-polling an outbox — the appliance never calls the host.
- **Zero dependencies beyond Electron, and no build step.** Binary plists are
  read via `plutil`; HTTP is Node's built-in fetch; the source is plain
  JavaScript that Electron runs directly, so a fresh checkout needs nothing but
  `npm start` — no compiler to have, no compiled output to go stale. The shapes
  that would have been TypeScript interfaces live as JSDoc typedefs in
  [`src/types.js`](src/types.js) and [`src/platform/types.js`](src/platform/types.js).
- Settings (including the password) are stored locally in the app's userData
  directory. Fine for a spike; a real release moves the credential to the
  macOS Keychain.
