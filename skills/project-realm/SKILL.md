---
name: project-realm
description: Create (or extend) a project realm — one small realm per software project that REQUIRES the shared realms (code, git, forge, CI, framework, quality, logs, deployment) and adds only what is specific to that project — its identities pinned once, its own rules, the cross-realm questions only it can ask, and a skill telling coding agents which view answers what. Works for any project — Spring, Java, TypeScript, Python, anything. Use when the user wants the appliance to know a project, wants agents working on a repository to get answers fast, or says "a realm for this project".
---

# Project realm

A coding agent on a project spends most of its time on questions that already have answers somewhere:
will this push be refused, is this red build mine, who else is changing this file, what is deployed,
how is production behaving, why was this written this way. A project realm makes each of those one
call. It is THIN: the shared realms do the work, and the project realm binds them to one project.

## The rule that decides everything

**Anything true of every project goes into a shared realm; the project realm holds only what is
true of this one.** Before writing any view, ask: would this view be the same for another repository
with the names changed? If yes, it belongs upstream in the shared realm (raise a PR there, run
`capability-first` first). The project realm is:

1. `requires:` on the shared realms it builds on, with version ranges;
2. **anchor views** pinning this project's identities once (repository, code project, CI, deployment, logs);
3. **project rules**: what this repository refuses or expects that no generic realm can know;
4. **cross-realm views** only this project can ask (its deployment joined to its commits, say);
5. **a skill** for agents: question → view, with this project's arguments already filled in.

If the project realm grows views a second project would also want, move them upstream.

## Step 1 — survey the project (read, do not guess)

Read the repository root and CI config, then map each signal to a shared realm. Read each candidate
realm's `realm.yml` description before relying on it; the table is a starting point, not a contract.

| Signal in the repository | Shared realm (name in `requires:`) |
|---|---|
| Any source code | `polyglot-code` (needs `code-model`), `git` |
| A GitHub remote | `github` (needs `dev-vocabulary`) |
| `.github/workflows/` | `github-actions` |
| `pom.xml`, `build.gradle(.kts)` | `java-project` (and `java-code` / `java-source-code` where installed) |
| Spring Boot in the build | `spring` |
| `package.json` | `npm` |
| `sonar-project.properties`, a Sonar badge | `sonar` |
| Dockerfile, Kubernetes manifests, Helm | `kubernetes` |
| JVM services in production | `jvm-logs` |
| Logs in Elasticsearch / files | `elastic-logs`, `logs` |
| "Which change broke production" questions | `which-change` |
| Code summaries / understanding wanted | `code-summaries`, `code-understanding` |

The questions every agent asks of any repository — will this push be refused (`PushChecks`), how
much room is left under the size rules (`RulesHeadroom`), which tests exercise this class and the
command (`TestsFor`), who calls or reads X (`WhoCalls`) — are polyglot-code's. Which changes are in
flight is github's (`OpenPullsTouching`, `OpenPullsOverlapping`); is a red build mine is
github-actions' (`ActionsIsItMe`, `ActionsFlakyJobs`). A realm folder in the world that is not
published (no git history, no remote) is a draft or a leftover: never `require` it; find where its
views went.

Also note the project's own identities, because every later step uses them: repository
(`owner/name`), code project key (`https://github.com/owner/name` or a workspace folder), default
branch, CI workflow names, deployment target (namespace, service, image), log index or path, Sonar key.

## Step 2 — check what the world has

List installed realms and their versions (`realm_status`, or `GET /api/v1/realms`). For each required
realm: is it installed, at what version, and does it answer for this project? Run one view of each
against the project's identity before depending on it — a realm that returns nothing for this
repository is a problem to fix upstream, not something to paper over in the project realm.

Reconcile one number per realm against the source itself, not just "rows came back": the commit
count the git realm sees against the forge's, the open pull requests against the forge's list, the
endpoints against the controllers. On petclinic the git realm answered every view — from 3 commits
of 1,055, a shallow-clone defect in the engine that only the reconciliation showed (me#4086).

## Step 3 — scaffold

```
realm-project-<name>/
  realm.yml
  views/anchors.yml       # node views pinning this project's identities
  views/project.yml       # cross-realm and rule views only this project needs
  reference/rules.md      # this repository's own rules, in prose, with where each is enforced
  skills/<name>/SKILL.md  # the agent's guide: question → view, arguments filled in
  README.md
```

```yaml
# realm.yml
name: project-<name>
description: |
  <Project> as one realm: its repository, code, CI, deployment and logs pinned once, its own rules,
  and the questions agents working on it ask, each answered by one view.
version: 0.1.0
maturity: experimental
realmLabel: Project<Name>
requires:
  # Ranges stop before 1.0.0: these realms move together; a minor bump should not hold this back.
  github: "[0.9.0,1.0.0)"
  github-actions: "[0.3.0,1.0.0)"
  polyglot-code: "[0.1.0,1.0.0)"
  code-model: "[0.1.0,1.0.0)"
  # … only what Step 1 found
```

Require EVERY realm whose labels a view, join or skill row reads, not only the ones you think of as
dependencies: a view naming a label no installed realm declares is refused, and the project realm
goes degraded wherever that realm is missing. Take each lower bound from the version installed now
(Step 2). A plain version is a soft preference; brackets are a hard range. An unmet requirement
holds the project realm back with a named problem, which is correct: say so rather than dropping the
requirement.

## Step 4 — anchor views: the project's identities, once

A view whose body returns ONE bare node is a node view, and its name works as a label in any query.
That is how the project's identities are pinned in one place:

```yaml
- name: <Name>Repository
  description: "<Project>'s GitHub repository. Use as a label: MATCH (r:<Name>Repository)-[:HAS_PULL_REQUEST]->(pr)."
  cypher: |
    MATCH (r:GitHubRepository {full_name: '<owner>/<repo>'}) RETURN r

- name: <Name>Code
  description: "<Project>'s code at the default branch. MATCH (p:<Name>Code)-[:DECLARES]->(d:SourceDeclaration)."
  cypher: |
    MATCH (p:CodeProject {key: 'https://github.com/<owner>/<repo>'}) RETURN p
```

One per dependency anchor the project uses. Keep them literal: a literal key is what lets the engine
push it to the source and share the cache.

## Step 5 — project rules

Every repository refuses things no generic tool knows: architecture tests, inventories, write
funnels, lint rules turned up beyond defaults, commit conventions. Read them FROM THE REPOSITORY —
lint config (detekt, PMD, Checkstyle, ESLint, ruff), architecture tests, pre-push hooks, CONTRIBUTING,
CLAUDE.md / AGENTS.md — and write `reference/rules.md`: each rule, what violates it, where it is
enforced (file and test), and how to satisfy it. Where a generic view already detects a class of
violation (for example `PushChecks` in polyglot-code), the skill maps its findings to these rules.
A rule the generic views cannot detect is a candidate for a generic capability upstream — name the
gap, do not hard-code a detector in the project realm.

## Step 6 — cross-realm views only this project needs

Write a view here only when it joins this project's anchors across realms in a way no other project
would reuse as-is — for example, which built image carries which pull request, from the project's own
image workflow and tag convention (embabel/me's `MeImages`). Often there is none: petclinic's realm
has only anchors, rules and a skill, and that is the right size. When such a view needs a field the
shared realm does not expose, add it upstream (embabel/me needed the run's whole commit title, which
GitHub truncates in `display_title`). Every view: a `description` whose first sentence says what it
answers in 80 characters (that is all the view list shows), then what it leaves out, parameters with
defaults set to this project's values, filters on projected variables, a stated cache/staleness, and
an empty case that says why it is empty.

Every view that can come back empty ends with one row saying so (`collect`, then `UNWIND CASE WHEN
size(x) = 0 THEN [{…sentence…}] ELSE x END`): the engine reports a read that matched nothing as a
failure (`FETCHED_NOT_MATCHED`), which an agent cannot tell from a broken view.

## Step 7 — the agent skill

`skills/<name>/SKILL.md` is the part agents read. Lead with the moments of work, not the views:

| Moment | Call | Arguments for this project |
|---|---|---|
| Before editing files | `OpenPullsTouching` | `repo: owner/name, paths: …, exceptBranch: <your branch>` |
| Before pushing | `PushChecks` per changed file, then `reference/rules.md` | `project: <key>, path: …` |
| Build is red | `ActionsIsItMe`; if `only on this branch`, `ActionsFlakyJobs` | `repo, branch` |
| Who calls / reads X | `WhoCalls`, `WhereDefined`, `Impact` | `project: <key>` |
| What is deployed, is it healthy | the deployment and log views found in Step 1 | … |

Fill every argument with the project's real values; an agent should be able to copy a row, and
run every row before shipping it — on embabel/me two rows were wrong as first written (`GitBlame`
takes `<project key>!<path>`; a settings view took `actuator`, not `project`). Leave out rows that
do not apply: `PushChecks`' literal-joining and qualified-name checks read Kotlin, and `RulesHeadroom`'s
caps are one repository's defaults, so a Java project with Checkstyle rules gets its own rows in
`reference/rules.md` instead. Copy
the same skill into the repository itself (`.claude/skills/ask-the-appliance/SKILL.md` or the
project's agent-instructions file) so agents find it without knowing the realm exists.

## Step 8 — validate, audit, install, then test like a user

Before installing: `embabel realm validate <dir> --strict` (zero errors; strict fails on keys the
host would silently drop) and `embabel realm audit <dir> -v` (aim for 100, document what remains).
Then install from its folder (`install_realm_from_path`, or `realm_write` + `realm_install`), check
`realm_status` and the realm's problems, then follow `relentless-testing`: run every view with its
defaults and each parameter varied; reconcile at least one figure per view against the source
(GitHub's API, `git`, the cluster); hold first-call and warm times to a stated budget and report them.
A view that has never returned rows in front of you does not exist yet. Ship it as `tests/verify.sh`
to the battery contract in `relentless-testing`: every view by name on its defaults, one figure per
view reconciled against the source, a cost bound for each pinned identity, last line exactly
`ALL CHECKS PASS`.

## Known edges (check they still hold)

- A realm installed from git at `ref: main` is pinned by digest; a plain refresh does not move it.
  After a shared realm merges, update it explicitly (`POST /api/v1/realms/<name>/update`).
- `polyglot-code` uses `code-model`'s types without declaring `requires:` on it; require both.
- Editing a producer re-keys its cache: a shared realm's change can make the next call cold.
- A `LIMIT` before a hop bounds that hop only in a query's first part; a view that needs two bounded
  reads (CI history and the newest runs, say) is refused today. Call two views instead.
- A complete empty read can still be flagged `PARTIAL` (a repository with no open pull requests);
  say what the forge itself shows when that happens.
- `FailuresAndTheirChanges` can be refused when its blame read stops at a fetch limit; `WhichChange`
  per failure fingerprint answers the same question one failure at a time.
- A log view's `logs` argument is a path under the world's data, e.g. `logs/clinic`. A service's live
  log outside the world's data (the appliance's own) is not readable by any realm today.

## Do not

- Copy a shared realm's view into the project realm to change its defaults — the skill fills arguments.
- Hard-code a rule detector for one repository; read the rule, and push detection upstream.
- Ship a view or skill row you have not run against the project.
- Put secrets, tokens or personal data in the realm; credentials stay in the appliance's wallet.
