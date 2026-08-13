# Embabel Me — sensor app (spike)

The Me side of the appliance/sensor architecture in [DISCOVERY.md](../DISCOVERY.md):
a small native app that reads local signals and **sends** them to your own
appliance. The appliance thinks; this app senses.

macOS is the implemented platform. Everything OS-specific lives behind the
`SensorPlatform` seam in [`src/platform/`](src/platform): `common.ts` holds the
readers whose logic is identical everywhere and whose only OS-specific part is
where the files live (JetBrains recents, VS Code folders, Chromium history),
`macos.ts` holds the Mac-specific ones, and `windows.ts`/`linux.ts` implement
what already works while naming exactly what each still needs. `process.platform`
is read in one place.

## Run it

```bash
cd me-app
npm start        # the first run fetches Electron by itself
```

(`../me.py` offers to do this for you at the end of setup.) `npm start` builds
and launches: the build is esbuild and takes about a tenth of a second, so there
is nothing to remember and nothing to run first.

| | |
| --- | --- |
| `npm start` | build, then run |
| `npm run watch` | rebuild on save (reopen the window to pick it up) |
| `npm run typecheck` | `tsc --noEmit` — the type gate, never in the run path |
| `npm run smoke` | print what a scan would gather, without the UI |
| `npm run dist` | a packaged app (builds first) |

Requires the Me mode up (`../me.py`) and your appliance username/password.

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

**Local files** shares folders with your appliance, in two deliberate levels.
Pick folders (Documents, a projects directory); each is bind-mounted
**read-only** into the assistant container under `/local/<name>`. The panel
writes `../docker-compose.override.yml` — the one file name Docker Compose
merges into plain `docker compose up` by convention, and which `setup.py` also
includes explicitly — then **Apply** recreates the assistant container with the
mounts (the graph and everything the appliance remembers stay up).

*Level one is free and automatic:* Apply also provisions every world in the
appliance data volume for the assistant's **virtual Cypher** `files` producer —
a `data/local → /local` symlink, the `filesTrustedRoots` trust in
`config/world.yml` (a human-only setting the assistant itself can never widen;
this panel, acting for the human who just picked the folders, is the sanctioned
out-of-band writer), and the `File`/`Folder` type and producer declarations
(`resources/`, provisioned marker-first — a hand-edited copy is warned
about and left alone). From then on "what files changed this week?" or "which
files mention the renewal?" are answered by walking your folders **live** —
metadata and bounded grep, nothing copied, nothing stored, no index to go
stale.

*Level two is per-folder opt-in:* tick **index contents** and a background
watcher pushes that folder's documents (PDF, Office, markdown…) through the
appliance's own ingestion pipeline — as `file:///local/…` URLs the server reads
straight off its mount, so no bytes are uploaded — embedding them into the
knowledge base for summarization and semantic search. The watcher keeps
running (and resumes on app launch): every sweep reconciles against the
server's own document list, so only **new or changed** files are ingested —
a file whose mtime is newer than its stored `ingestedAt` re-ingests by the
same URL, replacing rather than duplicating, and a crash anywhere resumes
with at most one file redone. Ingestion is sequential with a duty cycle
(sleep in proportion to how long each file took), and it reads the app's own
presence sensing: screen locked or idle → full tilt, actively working → ~25%
duty. Sweeps run every 15 minutes (sooner with a backlog), capped at 200
ingests per sweep and 30&nbsp;MB per file, with honest truncation reported.
Unticking every folder stops the watcher. Ticking a folder that is ALREADY
mounted starts indexing immediately — no Apply, no restart; only a freshly
added folder needs Apply first, because its mount does not exist until the
container is recreated. Design: appliance issue #10.

The override file is gitignored and never touches the tracked compose files;
removing every folder deletes it again. Before the restart, the panel sends one
fact per folder so the assistant knows where its new files live.

`npm run smoke` prints what a scan would gather without starting the UI;
`node -e "require('./src/platform').platform.scan({browserHistory:true}).then(console.log)"`
includes the history facts.

## Building a real app

```bash
../scripts/build-me-app.sh          # release/mac-*/Embabel Me.app
../scripts/build-me-app.sh --dmg    # …and a DMG, for handing to someone else
```

(`npm run package` / `npm run dist` do the same thing from in here.)

This matters beyond tidiness: unpackaged, the app runs inside Electron's own
bundle, so macOS names **Electron** in permission prompts and in the app menu.
Packaged, it has its own identity — `com.embabel.me` — and the prompt reads
"Embabel Me wants to control Google Chrome", which is the only version of that
sentence a user can act on.

Signing is ad-hoc: no Apple Developer membership needed, and enough for a stable
TCC identity across launches of the same build. Rebuilds change the code hash, so
expect macOS to ask again after one (`tccutil reset AppleEvents com.embabel.me`
clears the slate). Handing the DMG to someone else needs Developer ID signing and
notarization; the App Store is closed to us regardless, since sandboxed apps
cannot script other applications.

`./me.py` opens the packaged app when it finds one and falls back to `npm start`.

## Ask your documents

The **Documents** tab asks questions of what your appliance has ingested — shared
folders you ticked to index, uploads, pages you saved — optionally bounded by when
a document was ingested.

Retrieval is agentic and happens server-side (`POST /api/v1/documents/ask`): a
bounded LLM loop that searches both semantically and lexically, reformulates when
results look thin, and reads further into a candidate before judging it relevant.
The LLM lives in the appliance, with your key — this app never holds it.

**Attribution is the point.** Every claim carries a `[n]` marker; the server
verifies each one against what it actually retrieved and strips any it cannot
resolve, reporting the count rather than hiding it. Click a marker to jump to its
source, which shows the passages the answer was drawn from — and, because this app
knows where the appliance's mounts came from, a local document offers **Show in
Finder**: `/local/Documents/paper.pdf` inside the container is
`~/Documents/paper.pdf` on this Mac. Documents fetched from the web link back to
their URL instead. A citation you can open is verification; one you can only read
is decoration.

## What runs where

A banner sits above every tab saying whether anything you type reaches a model
provider. Green when it does not — *"Fully private — nothing leaves this Mac"* —
amber and specific when it does, naming the surface and the provider rather than
gesturing at "some data".

It is computed by the appliance (`GET /api/v1/models/in-use`), not here: role
resolution and per-world overrides live there, and a privacy claim computed twice
eventually disagrees with itself. The Chat and Documents tabs each name the model
that answered them, so you never have to hold the whole picture in your head.

When a model on this machine could do the talking, the banner offers **Go fully
local** — one click, no restart.

## Models

The **Models** tab says which model does what: chat, everyday work, code, document
search. Models running on this Mac — LM Studio, Ollama — are grouped first and
marked `local`: they cost nothing to run and the work stays on the machine.
Hosted ones are billed to your own provider key.

Every mechanism here belongs to the appliance (`/api/v1/config/models` and
`/api/v1/config/llm-roles`); the app adds a picker and the knowledge of which
models are yours.

The **appliance default** — used by anything with no role of its own — is at the
top of the same tab. It is a different kind of setting: read at boot, so changing
it writes `EMBABEL_MODELS_DEFAULT_LLM` into `docker-compose.override.yml` and
restarts the assistant. The graph and everything it remembers stay up; only the
app container is recreated.

Three behaviours, worth knowing before you wonder why nothing happened:

- **Changing a role takes effect immediately.** The world re-reads its config on
  every access, so the next piece of work uses the new model. No restart.
- **A newly-started local model needs a restart.** The appliance registers models
  at boot, so a model you load in LM Studio afterwards is invisible until then.
- **Changing the appliance default restarts it**, for the same reason — and the
  tab says so on the button rather than leaving you to discover it.

For the appliance to see local models at all it must address them as the host
rather than as itself — `LMSTUDIO_BASE_URL` and `OLLAMA_BASE_URL` default to
`host.docker.internal`, which Docker Desktop proxies through to your loopback.

## If there is no appliance yet

The app does not install or start the appliance — it talks to one that is already
running. When it cannot reach it, it says which of the three things is actually
wrong, because they have different fixes:

| What it says | What to do |
|---|---|
| Docker is not installed | Install Docker Desktop (there is a button), enable Model Runner, then `./me.py` |
| Docker is installed but not running | Start Docker Desktop, then `./me.py` |
| Docker is running, but nothing answered | The appliance is not started: `./me.py` |

"Connection refused" is true for all three and useful for none, which is why the
app probes rather than reporting the socket error.

## When something goes wrong

The app logs to a file as well as the console, because `./me.py` starts it
detached and the console then goes nowhere:

```bash
tail -f ~/Library/Application\ Support/Embabel\ Me/me-app.log
```

Failed sends, failed scans and failed focus pushes all land there with a stack.
The appliance's own side of the same story:

```bash
docker compose logs -f assistant | grep "\[sensor\]"
```

If a batch never appears in the appliance log AND nothing appears in this one,
the request never left the app.

## Design notes

- **Outbound-only.** The app is a client of the appliance API and never listens
  on a port. Future effect verbs (notify, open URL…) will ride the same
  direction via long-polling an outbox — the appliance never calls the host.
- **Nothing ships that isn't ours.** Binary plists are read via `plutil`; HTTP
  is Node's built-in fetch; `dependencies` is empty, so the packaged app carries
  no runtime library at all. Everything the pages need is either bundled from
  `src/` or vendored into `src/vendor/`.
- **TypeScript, bundled per window.** esbuild emits, `tsc --noEmit` checks, and
  the two are separate on purpose: the build stays in milliseconds and a type
  error never blocks a launch. The wire shapes live as real interfaces in
  [`src/types.ts`](src/types.ts), [`src/wire.ts`](src/wire.ts) and
  [`src/platform/types.ts`](src/platform/types.ts).

  The renderer is bundled rather than loaded as loose modules because it cannot
  be otherwise: pages load from `file://`, where Chromium refuses ES modules,
  and `nodeIntegration: false` rules out `require`. A classic IIFE bundle is
  what remains, and it is what lets the sources use imports at all.

  `strictNullChecks` is not on yet — the conversion turned on `noImplicitAny`
  and stopped there deliberately, so the two reviews stay separable.
- Settings (including the password) are stored locally in the app's userData
  directory. Fine for a spike; a real release moves the credential to the
  macOS Keychain.
