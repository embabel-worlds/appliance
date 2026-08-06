# Local discovery — learning the user from their machine

*Status: proposal. Nothing on this page is built.*

The appliance runs on the user's machine. That is normally framed as a privacy property —
your data stays home — but it is also a **capability** no cloud assistant has: the disk
is sitting right there, and the disk knows what the user works on. This proposal is about
using that, carefully, to figure out which realms matter to a given user — without
reading their email, and without a twenty-question setup wizard.

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

## First three to build

1. The git-remote scanner with recency weighting and the confirmation receipt.
2. `gh`-token adoption — free GitHub activation.
3. Existing-MCP-config detection — cheapest, highest precision, and nobody else does it.
