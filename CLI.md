# The `embabel` command

The appliance as a verb rather than a directory. Everything here was already
possible — as `cd ~/embabel-worlds && ./worlds.py`, `./setup.py --uninstall`,
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

## The short version

```bash
embabel up            # start it, and finish setup if it has not been set up
embabel status        # what is running, what is still downloading, where to go
embabel doctor        # why it is not working
embabel open          # the console, in your browser
```

---

## Reference

### `embabel up`

Start the appliance and complete first-run setup. **Safe to run at any time** —
a running mode is reconciled with the compose file rather than started twice, and
a completed setup says so instead of asking again.

| Flag | |
|---|---|
| `--worlds` | the world runtime and its console. The default |
| `--me` | the personal-assistant door |
| `--fresh` | **delete all data first** (asks), then start over |

The first run pulls roughly 0.8 GB before handing the terminal back, then
continues downloading the rest — the code sandbox, metrics, and structured
document conversion — behind you. `embabel status` says what is still arriving.

### `embabel status`

Splits what is running from what is still on its way, because during the first
quarter of an hour "not everything is up" is the normal state and a flat
container list cannot tell that apart from broken.

For the Worlds mode it ends with every surface: the console, the API, the MCP
endpoint, the graph browser, dashboards.

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

Open a surface in your browser: `console` (default), `graph`, `dashboards`, `me`.

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

### `embabel upgrade`

Pull newer images and recreate the containers. **Your data is untouched** — this
is the opposite of `up --fresh`.

### `embabel down`

Stop the appliance, keeping everything. `embabel up` brings it back.

| Flag | |
|---|---|
| `--wipe` | also **delete all data** (asks first) |

### `embabel uninstall`

Return the checkout to the state a fresh clone is in: the appliance's state, plus
this machine's configuration — `.env`, the shared-folder override, and the MCP
registration whose token died with the volume.

It also offers to remove stray code-sandbox containers. They are created by the
app through the Docker socket as siblings of the appliance rather than as compose
services, so `down` never sees them. It **asks** rather than assumes, because an
assistant you are running from an IDE owns containers carrying the same label.

Two things it deliberately keeps:

- **Images and the local embedding model.** The embedding artifact alone is over a
  gigabyte, and re-downloading one that has not changed is waste. There is no flag
  to remove them.
- **Your realm checkouts.** They are your repositories.

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

- **Keep the appliance inside the Linux filesystem** — `~/embabel-worlds`, not
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
| `EMBABEL_MODE` | `me` or `worlds`, for the installer |
| `EMBABEL_HOME` | where the installer puts the appliance, default `~/embabel-worlds` |
| `EMBABEL_BIN_DIR` | where the installer puts `embabel`, default `~/.local/bin` |

Everything else is in [`.env.example`](.env.example), which is the reference for
ports, provider keys, and tuning.
