# The `embabel` command

The appliance as a verb rather than a directory. Everything here was already
possible — as `cd ~/embabel/worlds && ./worlds.py`, `./setup.py --uninstall`,
`docker compose -f docker-compose-worlds.yml ps` — which asks you to remember
where the product lives and which compose file today's mode uses.

It is installed onto your `PATH` by the installer, as a two-line forwarder to the
checkout's own CLI. Updating the checkout updates the command; there is no second
copy to drift.

```bash
curl -fsSL https://raw.githubusercontent.com/embabel-worlds/appliance/main/install.sh | EMBABEL_MODE=worlds sh
```

If `~/.local/bin` is not on your `PATH`, the installer says so and gives you the
line to add.

---

Typing `embabel` on its own prints status and the verbs worth knowing next.

## The short version

```bash
embabel up            # start it, and finish setup if it has not been set up
embabel status        # what is running, what is still downloading, where to go
embabel doctor        # why it is not working
embabel open          # the console, in your browser
embabel backup        # everything it knows, copied somewhere safe
embabel version       # tag, digest, the commit it was built from
embabel bugreport     # one folder to attach to an issue, with no secrets in it
```

---

## Reference

### `embabel up`

Start the appliance and complete first-run setup. **Safe to run at any time** —
a running mode is reconciled with the compose file rather than started twice, and
a completed setup says so instead of asking again.

| Flag | |
|---|---|
| `--worlds` | the world runtime and its console |
| `--me` | the personal-assistant door |
| `--fresh` | **delete all data first** (asks), then start over |

With neither flag it starts **the mode this machine is already running, or was last
set up as** — recorded as `EMBABEL_MODE` in `.env` the first time. That matters
because the installer's default door is Me and this command's own fallback is
Worlds: without it, `embabel down` then `embabel up` handed an assistant user a
world runtime on the same graph, and said nothing about it. Worlds is still the
fallback for a machine that has set up neither.

The first run pulls roughly 0.8 GB before handing the terminal back, then
continues downloading the rest — the code sandbox, metrics, and structured
document conversion — behind you. `embabel status` says what is still arriving.

### `embabel status`

Splits what is running from what is still on its way, because during the first
quarter of an hour "not everything is up" is the normal state and a flat
container list cannot tell that apart from broken.

It ends with every surface of whichever mode is up: for Worlds the console, the
API, the MCP endpoint, the graph browser and dashboards; for Me the assistant
itself, its MCP endpoint and the graph.

### `embabel doctor`

Checks the things that have actually gone wrong for somebody, and says what to do
about each. It prints the appliance directory first, because that is what it is
reporting on.

- Docker installed, running, and Compose v2 present
- **Docker Model Runner** — embeddings run locally and need it
- whether this directory has been set up
- whether realm checkouts are linked, and whether that path is one the appliance
  can actually see (a path outside Docker Desktop's file sharing mounts **empty**,
  with nothing in any log to say why)
- stray code-sandbox containers left by a JVM that died without its shutdown hook

### `embabel logs [service]`

The appliance's own log by default; name a compose service for any other.

| Flag | |
|---|---|
| `-f`, `--follow` | follow |
| `--tail N` | lines of history, default 200 |

### `embabel open [what]`

Open a surface in your browser: `console`, `graph`, `dashboards`, `me`. With
nothing named it opens this appliance's front door — the console on Worlds, the
assistant on Me.

### `embabel realms link <directory>`

Point the appliance at the directory your realm checkouts live **in** — the
parent, so adding another realm is a `git clone` rather than a change to any
config.

The path is checked before it is written: one that does not exist, is not
readable, is a realm rather than a directory of realms, or is somewhere Docker
Desktop does not share, is refused with the reason. What it found is printed.

```
  Realm checkouts: /Users/you/dev
  4 realms visible: realm-esg, realm-github, realm-legal, realm-stripe
```

The mount is **read-only, by design**. You edit on the host — where your editor,
your coding agent and your git remote already are — and the appliance only reads.
A world then loads one by path instead of by repo:

```yaml
# config/realms.yml, in the world
- name: esg
  path: /realms/realm-esg
```

One consequence to know rather than discover: a realm's declared npm/wasm build
runs as part of cloning, so it never fires for a local realm. A declarative realm
needs nothing; a realm with a build step must be built on the host first.

### `embabel realms list`

Which realms the appliance can currently see.

### `embabel version`

Which appliance this is, in the four layers that can actually differ between
two installs. There is no single number, and printing one would be a lie:

| | |
|---|---|
| **Checkout** | this repo's commit — the pin for everything in files rather than images: the compose files, the Neo4j tag they name, `setup.py`, the skills |
| **Server** | the image **tag** as compose resolves it, and the **digest** that tag currently means. `EMBABEL_VERSION` defaults to a snapshot tag, so the tag is a name; the digest is the artifact |
| **Built from** | the commit the server's jar was built from, read out of `git.properties` inside it — with the branch, the subject line, and whether the build carried **uncommitted changes** |
| **Graph** | the Neo4j image and its digest |

A build from a dirty tree says so, loudly, because its commit names where the
build *started* rather than what is in it. A build old enough to carry only an
abbreviated SHA is marked `(abbreviated only)` rather than printing seven
characters as though they were an answer.

**It does not call the server.** `/actuator/info` carries the same build and git
blocks, but it is authenticated, and the moment anyone needs the version is the
moment the appliance will not boot, is wedged, or is halfway through an upgrade
— when an endpoint answers nothing. This reads the image and the jar instead,
and works with the container stopped (a little slower: it starts a throwaway
container to read from the image).

Reading the jar is cheap despite the jar being ~400MB: the zip index lives at
the end of the file, so it takes the tail, finds one entry's offset, and reads
a couple of hundred bytes.

| Flag | |
|---|---|
| `--json` | the same four layers, as JSON |

`embabel backup` records exactly this in each backup's `manifest.json`, so a
year-old backup can still say what wrote it.

### `embabel backup [directory]`

Everything the appliance knows, copied to a folder on the host: a cold tarball
of each of the two volumes, this machine's `.env`, `secrets.env` and mounts
override, and a manifest recording when it was taken, from which images, at
which checkout commit.

Backups go under `~/embabel-backups` unless you name a directory — deliberately
**outside the checkout**, because `embabel uninstall` deletes the checkout and a
backup an uninstall removes is not a backup. Each run makes its own timestamped
folder, so backing up twice never overwrites the first one.

**The copy is cold.** Whichever mode is running stops for it and starts again
afterwards — including on a failure, so a backup that goes wrong is never the
reason your assistant is down. This is not caution: Community Neo4j has no
online backup, and a graph copied while it is live restores as a corrupt graph.

The volume bytes never cross a bind mount — a helper container tars them to
stdout and the CLI streams that to the file — so Docker Desktop's file-sharing
list has no opinion about where a backup may live, and an external disk works.

| Flag | |
|---|---|
| `--list` | what backups are already in that directory, newest first |

The folder holds credentials: the database password, provider keys, realm
tokens. Treat it like the keys it holds — the `README.txt` written beside them
says so too.

### `embabel restore <directory>`

Put a backup back, **replacing what is on this machine**: the graph, the worlds,
the documents, and this machine's configuration. Everything added since the
backup was taken is gone. It asks first, and names the backup's date rather than
its folder — the date is what you are actually confirming.

What gets replaced is set aside rather than deleted: one `.before-restore` file
per config file, kept until the next restore overwrites it. A
`docker-compose.override.yml` the Me app did not write is a **refusal**, not a
set-aside — a hand-written override is somebody's work and a restore does not
eat it.

Restoring onto a machine with no volumes yet works, and is slow the first time:
compose has to create them, which means pulling the images.

| Flag | |
|---|---|
| `--yes` | skip the confirmation |

### `embabel ps`

What this appliance has on the host, in the three groups that fail differently:
its own containers, the deferred extras (allowed to be missing for the first
quarter of an hour, and not a fault), and **code sandboxes**.

Sandboxes are the reason this verb exists. They are created by the server
through the docker socket as *siblings* of the appliance rather than as compose
services, so `down` does not take them and `docker compose ps` cannot see them.
Until now they were visible only as one line inside `doctor`.

| Flag | |
|---|---|
| `--json` | the same, as JSON |

### `embabel prune`

Remove code-sandbox containers left on the host. Asks first, and says the thing
that makes it safe to answer: **if you run an assistant from an IDE, its
sandboxes are in this list too.** They carry the same label and nothing here can
tell an orphan from a live session, so it names them and lets you decide.

Sandboxes only — deliberately not `docker system prune`, and not dangling
images. This runs on developer machines where most of what Docker considers
garbage belongs to somebody else's work, and a cleanup verb that reaches beyond
its own project is one people learn not to run.

| Flag | |
|---|---|
| `--yes` | skip the confirmation |

### `embabel bugreport [directory]`

One folder to attach to an issue, instead of six rounds of "and can you also
send…". It holds `doctor`, `status` and `version` exactly as they printed,
`versions.json`, the container list, what Docker says about itself and its disk,
and per-container logs.

**What is deliberately not in it.** This appliance holds someone's email,
contacts and documents, so a diagnostic bundle is an exfiltration shape if it is
careless. Two rules, both enforced in code rather than left to a warning:

- **`.env` values are never copied.** `env-keys.txt` lists the keys and whether
  each holds anything — "is `OPENAI_API_KEY` set" is a real diagnostic question;
  "what is it" never is. A key that is empty and a key that is absent are
  different bugs, so both are reported.
- **Logs are filtered to warnings, errors and stack traces.** An INFO line from
  this server can carry a document title, a contact's name, or the text of a
  query somebody typed.

The bundle is left unpacked beside its `.zip` so it can be read before it is
sent. A bundle you cannot inspect is one people send blind, or not at all.

| Flag | |
|---|---|
| `--all-logs` | full logs, not just warnings and errors — the bundle's `README.txt` then says so in the first paragraph |

### `embabel reset-password`

Forgot the password. Recreates the operator account and **keeps every byte of
data** — the account lives in two small files under the volume's admin
directory, and this deletes exactly those two. The appliance refuses to reopen
setup over its API by design, permanently; whoever controls Docker on the host
already has this authority, which is why it is sound rather than a back door.

Was previously reachable only as `setup.py --reset-password`, so someone locked
out had to know to go around the CLI.

### `embabel completion <bash|zsh|fish>`

Tab completion, **generated from the parser** rather than written beside it — a
hand-maintained completion script is a list of verbs that quietly stops matching
the ones that exist, which is worse than none, because it teaches people the
command lacks a verb it has.

```bash
source <(embabel completion bash)                       # ~/.bashrc
embabel completion zsh > "${fpath[1]}/_embabel"         # zsh
embabel completion fish > ~/.config/fish/completions/embabel.fish
```

Verbs, their flags, and positional choices (`open console`, `realms link`) all
complete. Flags hidden from `--help` stay hidden here too.

### `embabel upgrade`

Pull newer images and recreate the containers. **Your data is untouched** — this
is the opposite of `up --fresh`.

### `embabel down`

Stop the appliance, keeping everything. `embabel up` brings it back.

| Flag | |
|---|---|
| `--wipe` | also **delete all data** (asks first) |

### `embabel uninstall`

Undo the installation: the appliance's state, this machine's configuration —
`.env`, the shared-folder override, the MCP registration whose token died with the
volume — and **the `embabel` command itself**, taken back off your PATH.

That last one only removes the launcher THIS installation wrote. `embabel` is not a
rare name, and install.sh warns when another one already comes first on your PATH;
uninstall reads the file before deleting it and leaves anything it did not write
alone. If another `embabel` still answers afterwards, it says so — otherwise the next
`which embabel` finds a hit and the uninstall looks like it failed.

It also offers to remove stray code-sandbox containers. They are created by the
app through the Docker socket as siblings of the appliance rather than as compose
services, so `down` never sees them. It **asks** rather than assumes, because an
assistant you are running from an IDE owns containers carrying the same label.

Two things it deliberately keeps:

- **Images and the local embedding model.** The embedding artifact alone is over a
  gigabyte, and re-downloading one that has not changed is waste. There is no flag
  to remove them.
- **Your realm checkouts.** They are your repositories.
- **The installation directory.** `~/embabel/worlds` (or wherever you put it) stays,
  so `./worlds.py` sets up again from it. Delete the directory yourself when you
  want it gone — this script does not remove the ground it is standing on.

### `embabel where`

Print the appliance directory.

---

## Working on a realm

The loop the CLI exists to make short:

```bash
embabel realms link ~/dev        # once
```

then, from a coding agent connected over MCP:

```
install_realm_from_path("realm-esg")   # once — by reference, nothing is cloned
… edit the files in ~/dev/realm-esg with your own tools …
realm_validate_path("realm-esg")       # would it load?
realm_refresh()                        # the world re-reads it
kg_query(…)                            # does it answer?
```

and `git push` from the checkout, to your own GitHub. Nothing in that requires the
appliance to write to your files, which is why the mount is read-only.

`realm_brief` over MCP explains the realm format and points at the
`realm-authoring` skill that walks through building one.

---

## Platforms

| | |
|---|---|
| **macOS** | Supported. Docker Desktop with Model Runner enabled (Settings → AI). `embabel` lands in `~/.local/bin`, which is not on `PATH` by default — the installer says so and names your shell's profile. |
| **Linux** | Supported. Model Runner needs the `docker-model-plugin` package rather than a Desktop toggle. `~/.local/bin` is usually already on `PATH`. `embabel open` uses `xdg-open`; on a headless box it prints the URL and opens nothing, which is the useful behaviour over ssh. |
| **Windows** | **WSL2 only** — see below. |

### Windows, specifically

Run the installer and `embabel` inside a **WSL2** distribution — Microsoft's Linux
environment for Windows — with Docker Desktop's WSL integration enabled for it.
Docker Desktop already uses WSL2 as its engine by default, so anyone running
Docker on Windows almost certainly has it.

This is not Windows support so much as Linux support that Windows users can
reach: the installer is a POSIX shell script and the CLI assumes POSIX paths, so
neither runs in PowerShell. A native port would mean a PowerShell installer and a
CLI that understands Windows paths — a piece of work, not a flag.

Two things that will bite otherwise:

- **Keep the appliance inside the Linux filesystem** — `~/embabel/worlds`, not
  `/mnt/c/...`. Crossing the Windows/Linux filesystem boundary is dramatically
  slower, and it costs most where it hurts most: realm checkouts, which a coding
  agent reads and rewrites in a tight loop. Clone your realms under your WSL home
  directory too, and point `embabel realms link` at that.
- **`localhost` forwards through to Windows**, so `http://localhost:4343` opens
  the console in an ordinary Windows browser. `embabel open` tries `wslview` for
  exactly this.

Two things that are macOS-specific by nature rather than by neglect:

- **Docker Desktop's file sharing.** `embabel realms link` warns when a path is
  outside the shared list, because such a mount resolves **empty** with nothing in
  any log to explain it. Linux has no equivalent restriction, so the check does not
  run there.
- **The Me app**, the menu-bar sensor, is macOS today. The appliance itself runs
  anywhere Docker does, including a Linux server.

---

## Environment

The CLI reads the same `.env` as everything else. The variables it cares about:

| Variable | |
|---|---|
| `EMBABEL_REALMS_DIR` | the parent of your realm checkouts, mounted read-only at `/realms` |
| `EMBABEL_MODE` | `me` or `worlds` — the installer reads it, and `embabel up` returns to it |
| `EMBABEL_HOME` | where the installer puts the appliance, default `~/embabel/worlds` |
| `EMBABEL_BIN_DIR` | where the installer puts `embabel`, default `~/.local/bin` |

Everything else is in [`.env.example`](.env.example), which is the reference for
ports, provider keys, and tuning.
