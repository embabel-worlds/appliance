# Working with a coding agent

Most software is used by clicking. This one is designed to be *operated* — and the
thing operating it can be a coding agent like Claude Code, working in your terminal
against your own appliance.

That is not a novelty. A world grows by acquiring capabilities, and building a
capability is programming. An agent that can read your files, write a realm, ask the
world whether it answered, and fix it when it did not, closes that loop in minutes
rather than in an afternoon of documentation.

## Connecting one

If you said yes during setup, this is already done. The wizard offers it at the end,
runs the command for you, and the agent sees your appliance in its next session.

If you skipped it, or you want to connect a second machine or a different agent, you
need two things: the URL and a token. The token is minted once during setup and never
shown again, so if you no longer have it, the simplest honest answer is to re-run
`embabel up --fresh`, or to mint a new one from the console's Coding agents tab.

```bash
claude mcp add --transport http --scope user embabel \
  http://localhost:4342/mcp --header "Authorization: Bearer <token>"
```

Other agents — Codex, Cursor, anything speaking MCP — take the same URL and the same
`Authorization` header in whatever form their config uses.

**Check it worked** by asking the agent something only your appliance knows: *"what
realms are installed?"* If it answers from your world, you are connected. If it tells
you about Embabel in general, it is answering from its own training and has not
actually reached anything.

## What the agent can and cannot touch

The connection is deliberately narrow. Over MCP the agent reaches **the appliance and
nothing else** — no shell, no filesystem, no network beyond the world. It can query the
graph, install realms, author actions and handlers, run them, and read back what
happened.

What it cannot do through that connection is look at your disk. That is on purpose: the
surface that reaches your world should not also be a way to read your machine.

Your *agent*, of course, can read your disk — that is what a coding agent is. The
difference matters when you think about what you are trusting. You already trust your
coding agent with your files. Connecting it to the appliance does not hand the
appliance the same access.

**One thing to know:** realm authoring is only offered on the **Worlds** door. On the Me
door the agent gets an assistant-shaped surface instead, because someone asking where
their invoices went should never end up reading about realm YAML. If your agent says a
realm tool does not exist, you are probably connected to Me.

## Skills — what ships with the appliance

A skill is instructions rather than code: it teaches your agent how to use this
appliance well. They run on your machine with your own tools, which is exactly why they
are skills and not tools on the MCP surface — the evidence about what you *could* build
lives on your disk, and the MCP surface deliberately cannot see your disk.

The one to start with is **scout-realms**. Point it at your estate and it surveys what
you have, works out with you where a realm would actually add value — a join onto
something the world already holds, intelligence the source system lacks, or plain
capability — and builds the ones you agree to. It asks before it reads anything, and
proposes before it writes anything.

> "Have a look at my dev directory and tell me what realms would be worth building."

## The loop that makes it fast

The reason this is quick is that nothing has to be published for the world to see it.
Point the appliance at where your realm checkouts live, once:

```bash
embabel realms link ~/dev
```

Then the cycle is: the agent edits files with your own tools, asks the world to
validate them, asks it to re-read them, and asks a question to see whether the answer
changed. No push, no release, no cache to defeat. When it is right, you `git push` from
your own checkout to your own GitHub, because the files were yours the whole time.

The mount is **read-only**, by design. You edit on the host — where your editor, your
agent and your git remote already are — and the appliance only reads.

## Working well with it

- **Ask for the outcome, not the mechanism.** "Make my GitHub issues joinable to the
  people in my world" gets a better result than naming files you think should exist.
- **Make it prove things.** The world can be queried; ask the agent to show you the
  answer rather than telling you it worked.
- **Expect it to look first.** A good session starts by asking your world what it
  already has. A realm that duplicates an existing one is the commonest waste.
- **Keep the console open.** Watching a run land in the graph while the agent narrates
  it is the fastest way to build a mental model of what the thing actually does.

## When it goes wrong

| What you see | What it usually is |
|---|---|
| The agent describes Embabel generically | Not actually connected — check `claude mcp list` |
| "Failed to connect" on every session | The token died with the volume; a `--fresh` install mints a new one and the old registration is stale |
| Realm tools are missing | You are on the Me door; Worlds is the developer one |
| A local realm's changes do nothing | It needs a refresh — or it has a build step, which only runs on clone and must be run on the host |
