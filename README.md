# Embabel Me — appliance

Run the whole product on your own machine with `docker compose up`. No JDK, no Maven,
no Node, no source checkout.

Your data and your code stay on your machine. The assistant talks to the model provider
*you* configure, with *your* key, directly — nothing routes through Embabel.

## Quick start

```bash
git clone https://github.com/embabel/appliance.git && cd appliance
cp .env.example .env       # then put your OPENAI_API_KEY in it
docker compose up -d
open http://localhost:4242
```

Sign in as `alice` / `test` (see [Who can log in](#who-can-log-in)).

First boot takes a few minutes: it pulls images, provisions your world, and builds the
graph indexes. Watch it come up with `docker compose logs -f assistant`.

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
| `OPENAI_API_KEY` | The default provider for the shipped model configuration. Start here. |
| `ANTHROPIC_API_KEY` | Used by the app builder for stronger code generation, and available to any action configured for it. |

Set both if you have both — model choice is per-action, so different parts of the
product route to different providers.

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

Sign-in is currently **development-grade**: the built-in users (`alice`, `ben`, `nina`)
all have the password `test`. That is why every port binds to `127.0.0.1`, and it is why
you should not expose this to a network or the internet as it stands. Treat the appliance
as single-operator, on your own machine, for now. A real first-run credential is coming.

## What the assistant can reach

Worth knowing before you run it:

- **Your model provider key is used from your machine**, directly against OpenAI or
  Anthropic. Your data and your code are not sent to Embabel.
- **The assistant controls your host Docker daemon.** `/var/run/docker.sock` is mounted
  so it can run its per-user code sandbox — that is how it executes generated code.
  Control of the daemon is root-equivalent on the host. This is inherent to running a
  code sandbox; it is stated plainly so it is a decision rather than a surprise.

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
| Login page loads but chat never answers | No model provider key in `.env`. Check with `docker compose exec assistant sh -c 'echo ${OPENAI_API_KEY:+set}'` |
| `docker compose up` fails on an image pull | Not authenticated to ghcr.io — see [Registry access](#registry-access) |
| First code-execution turn hangs for minutes | The sandbox image is still downloading. `docker pull embabel/assistant-sandbox:latest` |
| `assistant` restarts during boot | Neo4j isn't healthy yet; it settles on its own. `docker compose logs neo4j` |
| Uploading a PDF fails | docling isn't up. `docker compose ps`, or set `ASSISTANT_DOC_CONVERTER=none` |
| An MCP client gets 401 | `EMBABEL_MCP_API_TOKEN` is empty, or the client's header doesn't match it |
| Port already in use | Change the port variables in `.env` |

When reporting a problem, `docker compose logs assistant > assistant.log` captures what's
needed. Check it for keys before sharing it.
