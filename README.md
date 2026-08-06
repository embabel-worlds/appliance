# Embabel Me — appliance

Run the whole product on your own machine with `docker compose up`. No JDK, no Maven,
no Node, no source checkout.

Your data and your code stay on your machine. The assistant talks to the model provider
*you* configure, with *your* key, directly — nothing routes through Embabel.

## Quick start

```bash
git clone https://github.com/embabel/appliance.git && cd appliance

./me.py         # Embabel Me — the personal assistant   → http://localhost:4242
./worlds.py     # Embabel Worlds — the world runtime     → http://localhost:4343
```

One command is the whole thing: it starts the door (pulling images on first run),
shows you the boot, walks you through your account and model-provider key, and ends
by telling you exactly where to go. Add `--fresh` to wipe everything and start over.

**Prerequisite: Docker Model Runner.** Embeddings run locally, so the appliance needs
it enabled — `docker desktop enable model-runner` (Docker Desktop 4.40+, or Settings →
AI), or the `docker-model-plugin` package on Docker Engine. Everything else is pulled.

`setup.py` needs nothing installed — Python 3 standard library only, no flags. It finds
the running door, its port, and its setup token by itself (waiting out a still-booting
app rather than failing), walks you through each step, and checks your API key against
the provider before storing it, so a mistyped key fails there and then rather than at your
first message.

You do not have to edit any file before starting. `.env` exists for optional extras
(integration keys, ports, tuning) — copy `.env.example` if and when you need one.

Watch the first boot with `docker compose logs -f assistant`.

```bash
docker compose ps                    # what's running
docker compose logs -f assistant     # follow the app
docker compose down                  # stop, KEEP your data
docker compose down -v               # stop and DELETE everything
```

## Two doors — Me and Worlds

The appliance is one product with two fronts, running the **same image over the
same data**:

| Door | Start with | Open | What it is |
|---|---|---|---|
| **Me** | `./me.py` | **4242** | the personal assistant — web UI, chat, memory |
| **Worlds** | `./worlds.py` | **4343** — the console | the world runtime — realms, documents, keys, views, chat, MCP, automations |

For Worlds, **4343 is the front door**: the console. The server itself is on 4342 —
that is the API and MCP endpoint, not where you go to look at anything.

`docker-compose.yml` is an alias for `docker-compose-me.yml`, so plain
`docker compose up` opens the Me door. Everything door-agnostic — the graph, the
local embedding model, document conversion, metrics — lives in `infra.yml`,
included by both.

First-run setup is the same `./setup.py` for either door, with no flags: it finds
the running container by its compose service label and takes the port and setup
token from there.

Prefer a terminal? Each door carries the TUI as a run-on-demand service —
`docker compose run --rm tui` (add `-f docker-compose-worlds.yml` for Worlds,
where it opens in worlds mode: no Chat tab, since the world runtime has no
personal-assistant surface).

Because the doors share one graph and one data volume, two rules hold:

1. **Run one door at a time.** Both up at once means two identical JVMs running
   the singleton background work — cron firing twice, the same Telegram bot
   token connected twice (an API conflict, not just waste).
2. **`EMBABEL_VERSION` and the embedding model move both doors together** — they
   are set once, in `.env` and `infra.yml`, deliberately.

## Ports — the 42 block

`4242` is the one to remember; the rest count up from it.

| Service | Port | What it is |
|---|---|---|
| assistant | **4242** | the product: web UI, REST API, tool gateway, MCP endpoint |
| neo4j | 4243 / 4244 | the knowledge graph — browser and Bolt |
| docling | — | PDF/DOCX/PPTX/XLSX → structured markdown, internal only |
| open-webui | 4245 | optional alternative chat front-end, off by default |
| grafana | 4246 | dashboards over the assistant's metrics — on by default |
| prometheus | 4247 | scrapes and stores those metrics, 15 days by default |

Everything binds to `127.0.0.1` — reachable from your machine, not from the network.
This leaves `8042` free, so a development checkout and an appliance can run side by side.

### Signing in to each

| Service | How |
|---|---|
| assistant | the account you created during setup — see [Who can log in](#who-can-log-in) |
| neo4j | user `neo4j`, password `NEO4J_PASSWORD` (default `embabel-assistant`). The connect form comes pre-filled with `neo4j://localhost:4244` — Bolt on its host-published port; the usual 7687 is not published. Leave it as it is |
| open-webui | create an account on first visit; the first account is the admin |
| grafana | no login — anyone who can reach 4246 is admin, which is safe only because it binds to `127.0.0.1` |
| prometheus | no login |

For scripted graph queries, use the shell inside the container — nothing to install:

```bash
docker exec -it embabel-appliance-neo4j cypher-shell -u neo4j -p embabel-assistant
```

If you move the assistant off 4242, set `ASSISTANT_PORT` and nothing else: the container
port moves with it deliberately. The code sandbox is handed a callback URL built from the
server port, so a host port that differs from the container port would leave every
code-execution call dialling a port nothing listens on.

---

# Environment reference

Everything is configured through `.env` in this directory. Nothing is baked into an
image, so editing `.env` and running `docker compose up -d` applies any change.

Values are read by the container at start. Quote nothing unless the value contains
spaces, and never add trailing comments on a value line — `KEY=value # note` makes the
comment part of the value.

## Required — at least one model provider

The assistant cannot answer without one of these.

| Variable | Notes |
|---|---|
| `OPENAI_API_KEY` | The default provider for the shipped model configuration. |
| `ANTHROPIC_API_KEY` | Used by the app builder for stronger code generation, and available to any action configured for it. |

**You normally do not set these here** — `./setup.py` collects a key, validates it against
the provider, and stores it inside the data volume. If either variable is already exported
in the shell you run it from, it uses that instead of asking; `./setup.py --ignore-env`
always asks. Note that is your *shell's* environment, not `.env` — a key set only in `.env`
reaches the appliance but is invisible to the script, so setup still asks for one. Use `.env` if you would rather supply a
key up front (an air-gapped build, a scripted deployment, or to add a second provider after
setup); a key set here is picked up at boot exactly the same way.

Set both if you have both — model choice is per-action, so different parts of the product
route to different providers.

## Authorization fallback

Both doors default `ORG_ROLE_FALLBACK` to `admin` when no external organization role
provider is configured. This lets the setup-created operator use admin-gated local
surfaces. The fallback applies to **every authenticated principal**, not only that operator.
To opt out, set `ORG_ROLE_FALLBACK=user` in `.env`; every authenticated principal will then
receive the user role unless an external organization role provider resolves roles instead.

## Local embeddings — Docker Model Runner

Chat needs a provider key; **embeddings do not**. The compose file declares a local
embedding model (`ai/qwen3-embedding:0.6B-F16`, ~1.2GB, pulled like any image — the tag
is pinned because `latest` is a different, 4B model) that Docker Model
Runner serves as a host-side process — on Apple silicon that means Metal GPU
acceleration, and on every platform it means memory and document search cost no API
tokens and work offline.

This makes Docker Model Runner a requirement of the appliance:

- **Docker Desktop 4.40+** — enable it with `docker desktop enable model-runner`
  (or Settings → AI → *Enable Docker Model Runner*).
- **Docker Engine on Linux** — install the `docker-model-plugin` package from
  Docker's repository.

`docker compose up` fails fast with a clear message if the Model Runner is missing.
If you cannot run it, set `ASSISTANT_EMBEDDING_MODEL=text-embedding-3-small` in `.env`
to embed via OpenAI instead, and remove the `models:` element from the compose file.

**Switching embedding models on an existing install is a migration.** Stored vectors
were produced by the previous model and the Neo4j vector indexes are dimension-bound,
so after changing `ASSISTANT_EMBEDDING_MODEL` run the re-embed from *Settings →
Embedding model*. Until then, search results will be inconsistent.

## MCP access

The assistant is an MCP server, so Claude Code, Claude Desktop, Open WebUI and any other
MCP-aware client can drive it.

**You normally configure nothing here.** First-run setup's "Connect coding agents" step
mints a bearer token bound to the account you create, stores it in the data volume, and —
if the `claude` CLI is on your PATH — offers to run `claude mcp add` for you on the spot.

The `.env` variables exist for scripted deployments that skip the wizard, or to supply a
token before first boot:

| Variable | Default | Notes |
|---|---|---|
| `EMBABEL_MCP_API_TOKEN` | empty | Pre-set bearer token for `/mcp` and `/sse` (`openssl rand -hex 32`). A setup-minted token **takes precedence** — adding a value here after setup has minted one has no effect. |
| `EMBABEL_MCP_API_TOKEN_USER` | empty | Which user's world a pre-set token acts as. Both must be set together; there is no implicit default user. |

To wire a client manually (Claude Code shown; the URL + header work for any client):

```bash
claude mcp add --transport http --scope user embabel http://localhost:4242/mcp \
  --header "Authorization: Bearer <your token>"
```

A setup-minted token lives in the data volume at
`/data/embabel/assistant/admin/providers.env` — read it from there to wire additional
clients, or edit that file (and restart) to rotate it.

## Optional — integration credentials

Each unlocks a capability and is inert when empty; the assistant simply does not offer
what it has no credential for. These are resolved at the integration layer and are
**never injected into a prompt** — the assistant can call a tool that needs a token
without the model ever seeing the token.

| Variable | Unlocks |
|---|---|
| `BRAVE_API_KEY` | web search |
| `GITHUB_TOKEN` | the GitHub realm — issues, pull requests, repositories |
| `TELEGRAM_BOT_TOKEN` | chatting with the assistant from Telegram |
| `SLACK_APP_TOKEN` | chatting with the assistant from Slack |

Credentials for realms you install later don't need to go here: each world has its own
credential store, settable from the Channels tab in the World drawer or by editing that
world's `data/secrets.env`. Use `.env` for things the whole server needs.

## Ports

| Variable | Default |
|---|---|
| `ASSISTANT_PORT` | `4242` |
| `NEO4J_BROWSER_PORT` | `4243` |
| `NEO4J_BOLT_PORT` | `4244` |
| `OPEN_WEBUI_PORT` | `4245` |
| `GRAFANA_PORT` | `4246` |
| `PROMETHEUS_PORT` | `4247` |

All bind to `127.0.0.1`. Read [Who can log in](#who-can-log-in) before changing that.

## Optional services

`COMPOSE_PROFILES` is one comma-separated list and it is the whole truth — compose
reconciles the project to exactly those profiles on every `up`.

| Profile | What it starts |
|---|---|
| `openwebui` | Open WebUI on 4245 with the assistant pre-wired as an MCP tool server. Create an account on first visit; the first account is the admin. |

Grafana and Prometheus are **not** profile-gated — they are ordinary services and start
with everything else. A compose profile can only ever be *off* by default, since compose
activates one only when `COMPOSE_PROFILES` names it, and this appliance is designed to run
with no `.env` at all. To turn the dashboards off, delete those two services from
`docker-compose.yml`.

| Variable | Notes |
|---|---|
| `OPEN_WEBUI_SECRET_KEY` | Stable key so Open WebUI's encrypted credentials survive a container recreate. Any random string. |
| `OPEN_WEBUI_DEFAULT_MODEL` | Default chat model in Open WebUI (default `gpt-4.1`). |
| `PROMETHEUS_RETENTION` | How long samples are kept (default `15d`). Costs disk in the Prometheus volume. |

Set profiles **in `.env`**, not as an ad-hoc `--profile` flag. Compose reconciles the
project to the active profile set on every `up`, so a later plain `docker compose up -d`
would silently stop anything started under an ad-hoc profile.

### Metrics

Open <http://localhost:4246>. It lands on **Surface Health**; six more dashboards — LLM,
MCP Surface, HTTP & JVM, Graph & Tenancy, Code Mode & Sandbox, Virtual Cypher — are in
the dashboard list. There is no login: like every other port here it binds to
`127.0.0.1`, so anyone who can reach 4246 is an admin.

The dashboards ship inside the Grafana image and move with `EMBABEL_VERSION`, so they
always match the metrics the running assistant emits. That means they are read-only —
panel edits in the UI won't persist across a restart. Prometheus scrapes
`/actuator/prometheus` on the assistant every 10 seconds over the compose network.

## Tuning and infrastructure

| Variable | Default | Why you'd change it |
|---|---|---|
| `EMBABEL_VERSION` | pinned in the compose file | Pin to a specific release instead of tracking the default. |
| `ASSISTANT_DOC_CONVERTER` | `docling` | `none` uses plain text extraction and makes the multi-GB docling image unnecessary — faster to start, worse fidelity on tables and figures in PDFs. |
| `ASSISTANT_PUBLIC_BASE_URL` | `http://localhost:4242` | The externally visible origin. OAuth callbacks, MCP resource indicators and app links compose from it. Set it when running behind a proxy or on another host. No trailing slash. |
| `JAVA_OPTS` | `-XX:MaxRAMPercentage=75` | JVM memory. |
| `NEO4J_PASSWORD` | `embabel-assistant` | Change before the appliance is anything but local. |
| `NEO4J_HEAP` | `2G` | Raise for a large knowledge graph. |

---

# Operating it

## Who can log in

Only the account you create during setup. Its password is stored as a bcrypt hash in the
data volume, and the appliance authenticates against that and nothing else — the built-in
demo identities (`alice`, `ben`, `nina`) exist as data but cannot sign in.

Until setup completes, **nobody** can sign in, and the setup API itself requires the token
from the container log. Once it completes, that API returns `410 Gone` permanently: there
is no second chance to create an administrator, with or without the token.

Ports still bind to `127.0.0.1` by default. Change that deliberately.

### Starting over

Setup runs once per data volume. `docker compose down -v` erases everything — worlds,
documents, graph — and gives you a fresh appliance asking to be set up again.

## What the assistant can reach

Worth knowing before you run it:

- **Your model provider key is used from your machine**, directly against OpenAI or
  Anthropic. Your data and your code are not sent to Embabel.
- **The appliance reports anonymous usage data to Embabel every 24 hours.** Counts and
  versions — never your data or code. There is no opt-out flag; instead, every field is
  listed in [PHONE_HOME.md](PHONE_HOME.md) and your own instance will show you the exact
  JSON it last sent. See [Usage reporting](#usage-reporting) below.
- **The assistant controls your host Docker daemon.** `/var/run/docker.sock` is mounted
  so it can run its per-user code sandbox — that is how it executes generated code.
  Control of the daemon is root-equivalent on the host. This is inherent to running a
  code sandbox; it is stated plainly so it is a decision rather than a surprise.

## Usage reporting

The appliance sends one small JSON report to Embabel every 24 hours: an installation UUID,
the version, host dimensions, and counts — users, worlds, realms, graph nodes, documents.
Never names, titles, content, queries, credentials or file paths, of anything. Realm names
in particular are counted but never sent.

**[PHONE_HOME.md](PHONE_HOME.md) is the complete field list.** You do not have to take it
on trust — ask your own instance:

```bash
curl -u <you> http://localhost:4242/api/v1/phone-home           # exactly what was last sent
curl -u <you> http://localhost:4242/api/v1/phone-home/preview   # what would be sent now
```

The `json` field is the literal request body, so it matches a packet capture byte for byte.
The first report is 10 minutes after startup, so a short evaluation never reports at all,
and an unreachable collector is dropped silently rather than slowing anything down.

There is no configuration flag to disable it, and the collector address is fixed in the
image rather than exposed in `.env` — a variable you could blank would be an opt-out by
another name, and we would rather say so plainly than ship one quietly. If your
environment forbids outbound telemetry, block the endpoint at your network.

## Your data

Two Docker volumes hold everything that matters:

| Volume | Contents |
|---|---|
| `embabel_assistant_data` | worlds — your configuration, documents, artifacts, apps |
| `embabel_appliance_neo4j_data` | the knowledge graph |

`docker compose down` keeps both. `docker compose down -v` destroys both. To back up:

```bash
docker run --rm -v embabel-appliance_embabel_assistant_data:/data \
  -v "$PWD:/backup" alpine tar czf /backup/embabel-data.tgz -C /data .
```

Three more volumes hold state you can throw away — `embabel_appliance_open_webui_data`,
`embabel_appliance_prometheus_data` (metric history) and `embabel_appliance_grafana_data`
(Grafana's own database; the dashboards live in the image, not here). Losing them costs
you chat history in Open WebUI and past metrics, nothing you authored.

## Upgrading

```bash
git pull                  # pick up any compose changes
docker compose pull       # fetch the new images
docker compose up -d
```

Your volumes survive. Pin `EMBABEL_VERSION` in `.env` if you'd rather control when you
move between versions.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `setup.py` cannot find the token | It already waits out a booting app, so the running container's log truly lacks one. `docker compose restart assistant` (or `worlds`) — the token is printed afresh on every boot until setup completes |
| `setup.py` reports 410 Gone | This appliance is already set up — just sign in |
| Login rejects the account you created | Setup was not completed; re-run `./setup.py` |
| Neo4j browser won't connect or log in | The user is `neo4j` — `embabel-assistant` is the default *password*. And the connect URL must be `neo4j://localhost:4244` (pre-filled; 7687 is not published to the host) |
| Login page loads but chat never answers | No provider key took effect. The appliance restarts once at the end of setup for exactly this reason — check it came back with `docker compose ps` |
| `docker compose up` fails on an image pull | Not authenticated to ghcr.io — see [Registry access](#registry-access) |
| First code-execution turn hangs for minutes | The sandbox image is still downloading. `docker pull embabel/assistant-sandbox:latest` |
| `assistant` restarts during boot | Neo4j isn't healthy yet; it settles on its own. `docker compose logs neo4j` |
| Uploading a PDF fails | docling isn't up. `docker compose ps`, or set `ASSISTANT_DOC_CONVERTER=none` |
| An MCP client gets 401 | `EMBABEL_MCP_API_TOKEN` is empty, or the client's header doesn't match it |
| Nothing on 4246 | `docker compose ps` — if `grafana` isn't listed, it failed to pull. The image ships at the same `EMBABEL_VERSION` as the assistant |
| Dashboards load but every panel is empty | Prometheus can't reach the assistant. `curl localhost:4247/api/v1/targets` — the `embabel-assistant` target should be `up` |
| Port already in use | Change the port variables in `.env` |

When reporting a problem, `docker compose logs assistant > assistant.log` captures what's
needed. Check it for keys before sharing it.
