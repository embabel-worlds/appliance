# Embabel Me — appliance

Run the whole product on your own machine with `docker compose up`. No JDK, no Maven,
no Node, no source checkout.

Your data and your code stay on your machine. The assistant talks to the model provider
*you* configure, with *your* key, directly — nothing routes through Embabel.

## Quick start

```bash
git clone https://github.com/embabel/appliance.git && cd appliance
docker compose up -d       # first boot pulls images; give it a few minutes
./setup.py                 # create your account, connect a model provider
open http://localhost:4242
```

`setup.py` needs nothing installed — Python 3 standard library only. It finds the setup
token in the container log, walks you through each step, and checks your API key against
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

### Registry access

The Embabel images are private while the appliance is in preview. Authenticate once
with a GitHub personal access token that has `read:packages`:

```bash
echo $GITHUB_PAT | docker login ghcr.io -u <your-github-username> --password-stdin
```

## Ports — the 42 block

`4242` is the one to remember; the rest are +1/+2.

| Service | Port | What it is |
|---|---|---|
| assistant | **4242** | the product: web UI, REST API, tool gateway, MCP endpoint |
| neo4j | 4243 / 4244 | the knowledge graph — browser and Bolt |
| docling | — | PDF/DOCX/PPTX/XLSX → structured markdown, internal only |
| open-webui | 4245 | optional alternative chat front-end, off by default |

Everything binds to `127.0.0.1` — reachable from your machine, not from the network.
This leaves `8042` free, so a development checkout and an appliance can run side by side.

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
the provider, and stores it inside the data volume. Use `.env` if you would rather supply a
key up front (an air-gapped build, a scripted deployment, or to add a second provider after
setup); a key set here is picked up at boot exactly the same way.

Set both if you have both — model choice is per-action, so different parts of the product
route to different providers.

## MCP access

The assistant is an MCP server, so Claude Code, Claude Desktop, Open WebUI and any other
MCP-aware client can drive it.

| Variable | Default | Notes |
|---|---|---|
| `EMBABEL_MCP_API_TOKEN` | empty | Bearer token for `/mcp` and `/sse`. **Empty means no client can connect** — the endpoints reject every bearer request. Generate one with `openssl rand -hex 32`. |
| `EMBABEL_MCP_API_TOKEN_USER` | `alice` | Which user's world an MCP client acts as. It sees that user's data, tools and integrations. |

Then point a client at `http://localhost:4242/mcp`:

```bash
claude mcp add --transport http embabel http://localhost:4242/mcp \
  --header "Authorization: Bearer <your token>"
```

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

All bind to `127.0.0.1`. Read [Who can log in](#who-can-log-in) before changing that.

## Optional services

| Variable | Notes |
|---|---|
| `COMPOSE_PROFILES` | `openwebui` starts Open WebUI on 4245 with the assistant pre-wired as an MCP tool server. Create an account on first visit; the first account is the admin. |
| `OPEN_WEBUI_SECRET_KEY` | Stable key so Open WebUI's encrypted credentials survive a container recreate. Any random string. |
| `OPEN_WEBUI_DEFAULT_MODEL` | Default chat model in Open WebUI (default `gpt-4.1`). |

Set the profile **in `.env`**, not as an ad-hoc `--profile` flag. Compose reconciles the
project to the active profile set on every `up`, so a later plain `docker compose up -d`
would silently stop anything started under an ad-hoc profile.

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

Two Docker volumes hold everything:

| Volume | Contents |
|---|---|
| `embabel_assistant_data` | worlds — your configuration, documents, artifacts, apps |
| `embabel_appliance_neo4j_data` | the knowledge graph |

`docker compose down` keeps both. `docker compose down -v` destroys both. To back up:

```bash
docker run --rm -v embabel-appliance_embabel_assistant_data:/data \
  -v "$PWD:/backup" alpine tar czf /backup/embabel-data.tgz -C /data .
```

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
| `setup.py` says it cannot find the token | The app is still starting. `docker compose logs assistant \| grep "Setup token"` |
| `setup.py` reports 410 Gone | This appliance is already set up — just sign in |
| Login rejects the account you created | Setup was not completed; re-run `./setup.py` |
| Login page loads but chat never answers | No provider key took effect. The appliance restarts once at the end of setup for exactly this reason — check it came back with `docker compose ps` |
| `docker compose up` fails on an image pull | Not authenticated to ghcr.io — see [Registry access](#registry-access) |
| First code-execution turn hangs for minutes | The sandbox image is still downloading. `docker pull embabel/assistant-sandbox:latest` |
| `assistant` restarts during boot | Neo4j isn't healthy yet; it settles on its own. `docker compose logs neo4j` |
| Uploading a PDF fails | docling isn't up. `docker compose ps`, or set `ASSISTANT_DOC_CONVERTER=none` |
| An MCP client gets 401 | `EMBABEL_MCP_API_TOKEN` is empty, or the client's header doesn't match it |
| Port already in use | Change the port variables in `.env` |

When reporting a problem, `docker compose logs assistant > assistant.log` captures what's
needed. Check it for keys before sharing it.
