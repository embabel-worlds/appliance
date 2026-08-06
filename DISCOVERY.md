# Local discovery — learning the user from their machine

*Status: proposal. Nothing on this page is built.*

The appliance runs on the user's machine. That is normally framed as a privacy property —
your data stays home — but it is also a **capability** no cloud assistant has: the disk
is sitting right there, and the disk knows what the user works on. This proposal is about
using that, carefully, to figure out which realms matter to a given user — without
reading their email, and without a twenty-question setup wizard.

Three parts, one arc. **Discovery** learns what exists on the machine. **Mounted
directories** turn granted paths into a queryable, RAG-indexed corpus. **Event
handlers** — shipped and learned — turn what the appliance now knows into ongoing
behavior. Each step is a small consent, evidenced by the one before it.

## Part 1 — Discovery

## The framing

Disk tells you **what tools**; only the user tells you **what for**. So discovery's job
is not to replace the onboarding conversation — it is to make it short and specific.
Never ask a blank question ("do you use Jira?"); ask an evidenced one ("your branches
look like `ENG-1234` — Jira or Linear?"). Confirming is far cheaper for a user than
constructing.

The receipt matters as much as the scan:

> Found 47 repos across 3 orgs, 12 touched this month, 9 on `github.com/embabel`.
> Turn on the GitHub and Linear realms?

That is a product moment cloud assistants structurally cannot have.

## Finding git projects

Walk the usual roots — `~/dev`, `~/src`, `~/code`, `~/work`, `~/Projects`,
`~/IdeaProjects`, `~/go/src` — for `.git` directories. Then, per repo, read git
*metadata* only:

| Signal | Command | What it yields |
|---|---|---|
| Remotes | `git remote -v` | Host, org, repo. Host-agnostic by construction — GitLab, Bitbucket, Azure DevOps and GHES fall out of the same parser as GitHub. |
| Recent authorship | `git log --author=<their emails> --since=90.days` | Separates live repos from the 80% of `~/dev` that is dead clones. Recency-weight everything; show the top N, never the full inventory. |
| Branch and commit conventions | branch names, commit messages | `ENG-1234`, `PROJ-99`, `Fixes #412` — the issue tracker leaks out without touching any tracker. |
| Working state | dirty trees, unpushed commits, stashes, branches ahead of origin | What the user is doing **this week**. This is the killer signal, not the repo list — a live to-do list nobody had to be asked for. |
| Manifests | `pom.xml`, `package.json`, `Cargo.toml`, `go.mod` | Ecosystem realms. |

## Higher-precision signals than the filesystem walk

| Signal | Where | Why it is good |
|---|---|---|
| **Existing MCP config** | `~/.claude.json`, `~/.cursor/mcp.json`, Claude Desktop config | The user *hand-picked* these integrations. Highest precision on this page — a direct declaration of desired realms. |
| **`gh` auth** | `gh auth status`, `~/.config/gh/hosts.yml` | GitHub already authenticated; `gh auth token` is a realm with zero OAuth dance. Same for `glab`. |
| **`~/.gitconfig`** | | Identity and emails; `includeIf "gitdir:~/work/"` literally declares the work/personal boundary; `url.insteadOf` reveals internal hosts. |
| **IDE recent-projects** | JetBrains `options/recentProjects.xml`, VS Code `storage.json` | Curated *and* recency-ordered — better than a disk walk at ranking. |
| **Ambient config** | `~/.aws/config` profiles, `~/.kube/config` contexts, `~/.npmrc`, `~/.m2/settings.xml`, `~/.ssh/config` | Each file is a realm advertisement. Read *presence and names*, never credential values. |
| **Installed apps** | `brew list`, `/Applications` | Slack, Linear, Notion, Figma, Tower — a compact developer profile. |
| **Shell history** | `~/.zsh_history` | Behavioral truth versus aspirational installed-apps. But it is a known trove of pasted secrets — tally command *names* in-process, discard arguments, persist nothing. |

## Should GitHub be prioritized?

Yes — but for a precise reason, not by default. GitHub is the one realm where the local
scan yields identity *and* a working token for free, so the marginal activation cost is
approximately zero, and from one credential the assistant fans out to orgs, teams,
issues, PRs and review requests. Best value per consent click on the board.

Two caveats. Enterprises are often on GHES or GitLab, so build the *remote parser*
first and let GitHub be one output of it. GitHub-first must not become GitHub-only.

## macOS data — the real answer is Docker, not TCC

The appliance is containerized. TCC-protected sources — Contacts, Calendar, Mail,
Messages, Photos, Desktop and Documents — are **not reachable from a container**.
Reaching them would take a host-side companion binary, which breaks the "no JDK, no
Maven, no source checkout" promise. Resist that; for Worlds those sources are out of
scope anyway.

The better observation: **the bind mount is the consent UI.** Mounting `~/dev:ro` in
the compose file means the user has said yes to code scanning — legibly, revocably,
with no dialog and no entitlement. Cleaner than any permission prompt, and it fits how
the product already works.

Browser history (domains only, Chrome's SQLite) would be extraordinarily informative
about which SaaS dashboards the user lives in — and it is the creepiest item available.
Power-user opt-in at best; not in any default.

## Worlds versus Me

For Worlds the unit is the **org**, not the person: roll repos up to orgs and propose
org → world, integration → realm. "You work in three orgs — a world each?" For Me,
personal signals are in scope and the person is the unit.

## Trust posture

- Read the *shape* of the disk, not the code. Paths, git metadata, manifests, file
  presence — no source contents in discovery. That is a line stateable in one sentence
  and defensible thereafter.
- Emit a `scan.json` the user can read and edit. It is the model of them; make it
  inspectable and reversible.
- Nothing from the scan touches phone-home. Discovery output sits entirely under the
  counts-only posture of [PHONE_HOME.md](PHONE_HOME.md).

## Not a wizard

The scan gets 80% at first boot; a periodic re-scan then asks about deltas — "a new
org appeared last week, want a world?" Learning the user is continuous, not a setup
step. Keep a cold-start fallback too: empty `~/dev` → ask for a GitHub username and
fan out from public repos and orgs.

## First three to build (discovery)

1. The git-remote scanner with recency weighting and the confirmation receipt.
2. `gh`-token adoption — free GitHub activation.
3. Existing-MCP-config detection — cheapest, highest precision, and nobody else does it.

## Part 2 — Mounting and indexing the user's files

### Mounting directories after the fact

A bind mount cannot be added to a *running* container — that is a hard Docker limit —
but nothing requires mounts to be declared ahead of time in the shipped compose file
either. Three tiers:

1. **No mount at all — often right for discovery itself.** `me.py` and `setup.py`
   already run host-side with stdlib Python. The discovery scan (git walk, remotes,
   recency) can run *on the host* and POST `scan.json` to the app. Zero mount, zero
   restart — and the container-boundary problem from Part 1 evaporates for the
   one-shot case.
2. **`docker-compose.override.yml` — for ongoing access.** Compose merges an override
   file automatically. The assistant asks — in chat, at any time — "point me at a
   folder"; the app cannot mount it itself, so it hands the path to the host side:
   `me.py` (or `me.py mount ~/Documents/papers`) appends one `- ~/Documents/papers:/host/papers:ro`
   line to the override file and runs `docker compose up -d`. The container recreates in
   seconds; named volumes keep all data. **The override file is the consent ledger** —
   every granted path is one visible line the user can delete. Re-askable forever; no
   shipped file ever edited. This makes the bind-mount-as-consent-UI observation from
   Part 1 *dynamic*: consent granted when asked for, not pre-declared.
3. `${VAR:-...}` interpolation in the shipped compose file works for exactly one
   well-known mount point; it does not scale to "N directories the user picks". The
   override file does.

Mac caveat for tier 2: Docker Desktop's file-sharing allowlist covers `/Users` by
default, so `~/anything` works, but a repo on an external volume may need a Settings
change — `me.py` should catch that mount error and say so plainly.

### The filesystem is two surfaces, not one

The machinery mostly exists: docling converts documents, embeddings run locally via
Docker Model Runner, RAG ingestion is built, and virtual Cypher has exactly the right
abstraction — a producer backend mirrored into the graph with coverage tracking. Files
are a producer nobody has written yet.

**RAG surface — content.** `~/Documents`, PDFs, an Obsidian vault → docling converts,
the local model embeds, chunks land in the graph. The privacy story is unusually clean:
*embedding locally is the whole point of requiring Model Runner* — documents become
semantically searchable without one byte leaving the machine. That is a sentence for
the README, not an implementation detail.

**Virtual Cypher surface — structure.** A filesystem producer exposing
`(:File)-[:IN]->(:Directory)` with path, extension, size, mtime and git-repo
membership. The *shape* of the disk becomes queryable — "files modified this week in
repos I own" — and it composes with everything already in the graph: a `(:File)` under
a known repo links to the repo, the org, the world. The existing mirror doctrine
applies: mirror the **tree metadata completely** (it is `stat`, not content), because
a capped mirror is worse than no mirror; content is the thing done selectively.

That yields a three-tier ladder:

| Tier | What | Cost | Coverage |
|---|---|---|---|
| 0 | Tree metadata → virtual Cypher | trivial | **always full** for mounted paths |
| 1 | Convert + embed selected types/dirs → RAG | real (local CPU/GPU) | user-chosen, receipt shown |
| 2 | Entity extraction, linking files to people/projects in the KG | LLM calls | opt-in, high-value dirs only |

Tier 0 is also the *selection UI* for tier 1: "you have 340 PDFs under
`~/Documents/papers`, 90 markdown files in what looks like an Obsidian vault — index
those?" Evidenced questions, never blank ones.

### Watching

Bind-mount file events on the Mac (FSEvents → VirtioFS) are decent but not gospel, so
the watcher should be mtime-polling with a hash check, not inotify-faith. Poll cheap
(tier 0 is stat-only), re-index only what changed, and **fire a signal per new or
changed file** — which plugs files straight into Part 3: "new PDF landed in `papers/`
→ summarize into the graph" is just another shipped handler, dry-run by default.

### Guardrails that make it shippable

- **Read-only mounts, always.** Non-negotiable, and it makes the pitch one sentence.
- **A baked-in deny-list that consent cannot override**: `~/.ssh`, `.env*`,
  key and certificate extensions, `node_modules`, `.git` internals. Respect
  `.gitignore`; support `.embabelignore` for the rest.
- **The receipt**: after indexing, "342 files, 12 skipped by deny-list, 3 unreadable" —
  coverage surfaced, because partial-looking-complete is the real bug.
- **Phone-home unchanged**: file *counts* at most, never paths — consistent with
  [PHONE_HOME.md](PHONE_HOME.md).

### Me versus Worlds, for files

Me: a personal corpus — mounted paths belong to the person, RAG feeds chat and memory.
Worlds: a mounted directory is attached to a **specific world** ("this folder is the
`embabel` world's docs"), appears in the console's documents view, and its signals feed
team handlers. Same producer, different attachment point.

## Part 3 — Event handlers, shipped and learned

The assistant's handler model shapes everything here: a handler is TypeScript triggered
by signal-type match or cron, sandboxed, with the graph, AI judgment and realm verbs in
scope; non-autonomous handlers already run **dry-run**; and an English → validated-TS
generator already exists, briefed live from the world's real schema and sampled recent
signals. Two consequences: shipping handlers is cheap, and *proposing* handlers is
cheap.

### Out of the box — realms ship their obvious handlers, observe-only

The natural unit is not "the appliance ships handlers" — it is **each realm ships its
obvious handlers, disabled or observe-only**, so installing a realm seeds a menu rather
than silence.

**Worlds (org rail — effects visible to a team, autonomy is an admin decision):**

- *Review-requested brief* — GitHub PR signal → summarize the diff against the graph
  (who owns these files, related issues) → notify the requested reviewer.
- *Issue dedupe/triage* — new issue → query for near-duplicates and the likely owner →
  comment or label.
- *Stale-PR escalation* (cron) — PRs quiet for N days, grouped by author, one digest
  not N pings.
- *Payment failed / new customer* (Stripe realm), *new lead brief* (HubSpot realm) —
  the "enrich from the graph, then notify" shape.
- *Document landed* — new doc in a world → one-paragraph summary to the channel that
  cares.

**Me (personal rail — one recipient, quiet hours, personal channels):**

- *Morning catch-up* (cron) — overnight signals triaged into one digest; the triage
  pipeline exists, the handler is its scheduled voice.
- *Meeting prep* — calendar event ~30 minutes out → what the graph knows about
  attendees and topic.
- *Unanswered thread nudge* — an email where you asked a question, no reply in 3 days.

### Handlers learned from behavior

Because the generator makes a proposal one call away from working code, any behavioral
observation phraseable in English can become a proposed handler. Sources, in order of
signal quality:

1. **Repeated chat questions.** The user asks "anything waiting on me?" most mornings →
   "You've asked this four times this week — want it as an 8am handler?" The English of
   the proposal *is* the generator input. The cleanest loop in the product.
2. **Signal-handling records.** The attention pipeline already knows which signals the
   user acts on versus ignores. Consistently ignored type → propose a muting or digest
   handler; consistently acted-on-fast type → propose escalation to a louder channel.
3. **The discovery scan** (Part 1) — branch names like `ENG-1234` don't just suggest
   the Linear realm, they suggest the *handler*: "when a Linear issue you're assigned
   moves to blocked…"
4. **Manual-action repetition** — the same realm verb after the same signal type three
   times → propose automating exactly that pair.

The lifecycle writes itself, because the ladder is already in the code: **proposed →
enabled observe-only (dry-run) → autonomous.** A learned handler is never born
autonomous; it runs dry for a week, shows "here's what I *would* have done" receipts,
and the user promotes it. That graduation is the trust story.

### Me versus Worlds, for learning

On Me, learning is per-person and promotion is the user's own click. On Worlds,
learning can aggregate — "three people ask about deploy status every Friday" is an
org-level pattern no individual sees — but proposals go to the world admin, and
autonomy is a shared-blast-radius decision. Same generator, same ladder, different
consent authority.

## The arc

Host-side scan finds what exists → user grants paths, one override line at a time →
the tier-0 mirror makes the disk queryable → evidenced proposals drive tier-1/2
indexing → file and realm signals feed learned handlers → handlers graduate from
dry-run to autonomous. Each step small, consented, and evidenced by the previous one.
