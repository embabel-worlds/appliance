# Embabel Me — sensor app (spike)

The Me side of the appliance/sensor architecture in [DISCOVERY.md](../DISCOVERY.md):
a small native app that reads local macOS signals and **sends** them to your own
appliance. The appliance thinks; this app senses.

## Run it

```bash
cd me-app
npm install
npm start
```

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

There is also an opt-in **ambient presence stream**: every 30 seconds, send
whether you're active at the keyboard (idle time via `ioreg` — no permissions
needed, no app names, no content). Each push replaces the previous one in the
appliance's in-memory context layer — a heartbeat for the attention system, not
a stored history. It requires the sensor endpoint (assistant `me` branch).

**Browser history is opt-in** — tick "Include browser history" before scanning and
the sensor also reports most-visited sites, which news outlets you read (with the
headlines you actually opened), and recent web searches. Read from a *copy* of the
Chromium history database via the system `sqlite3`, because the live file is locked
while the browser runs; Chromium needs no Full Disk Access, and Safari — which does
— is deliberately not touched. This is what lets the assistant answer "what kind of
news do I read?" with outlets and stories rather than a guess from your Dock.

`npm run smoke` prints what a scan would gather without starting the UI;
`node -e "require('./dist/scanner').scan({browserHistory:true}).then(console.log)"`
includes the history facts.

## Design notes

- **Outbound-only.** The app is a client of the appliance API and never listens
  on a port. Future effect verbs (notify, open URL…) will ride the same
  direction via long-polling an outbox — the appliance never calls the host.
- **Zero runtime dependencies.** Binary plists are read via `plutil`; HTTP is
  Node's built-in fetch. `electron` and `typescript` are dev-time only.
- Settings (including the password) are stored locally in the app's userData
  directory. Fine for a spike; a real release moves the credential to the
  macOS Keychain.
