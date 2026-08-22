# The Embabel Worlds guide

This is the guide for people using Embabel Worlds, rather than for people building it.
It assumes you can install something and follow a terminal command when one is given,
and nothing beyond that.

## What you are actually running

One command started three things that are easy to confuse.

**A knowledge graph.** Not a folder of documents and not a chat history — a graph of
things and the relationships between them. People, companies, invoices, issues,
messages, whatever you connect. The point of a graph is the joins: the assistant can
answer "which customers raised an issue in the month before they cancelled" because
those are edges, not because someone wrote a report.

**An agent runtime.** The part that does work — reads a document, watches for
something, answers a question, runs a job on a schedule. It plans, it uses tools, and
every run is inspectable after the fact.

**Realms.** The connectors. A realm teaches your world about a system you already run
— GitHub, Stripe, a Postgres database, a folder of contracts — and, crucially, how it
joins onto what the world already holds. A realm is not an import. Nothing is copied
into a mirror to go stale; the world reaches out when asked.

All three run on your machine, in Docker containers, on a graph stored in a volume you
own. There is no Embabel server in the path of anything you do.

## The two doors

The same appliance opens two ways, and which one you came through changes what you see.

**Embabel Me** is the personal assistant — your mail, your documents, your calendar,
your memory of what happened. It is a single-user panel and it is deliberately quiet
about realms and graphs.

**Embabel Worlds** is the world runtime and its console — realms, queries, handlers,
apps, models, and a chat that acts inside the world. It is the developer door, and it
is the one this guide is mostly about.

Both share one graph and one volume, which is why only one runs at a time. `embabel up`
returns to whichever you set up.

## Where to go from here

- **[Working with a coding agent](coding-agents.md)** — the appliance is designed to be
  operated by Claude Code or a similar agent, not just by clicking. This is the part
  most people underestimate.
- **[Making your own realm](realms.md)** — connecting a system nobody has connected yet,
  including the version where you describe it and an agent builds it.
- **[Running your own models](local-models.md)** — LM Studio and Ollama, what they cost
  you in quality, and how to put the sensitive work on them.
- **[What stays on your machine](privacy.md)** — the honest accounting of what leaves,
  when, and to whom, and how to check rather than trust.

If something here is wrong or unclear, it is written in
[the appliance repository](https://github.com/embabel-worlds/appliance) under
`docs/guide/` and corrections go there.
