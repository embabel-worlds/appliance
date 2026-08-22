# Running your own models

The appliance talks to whichever model provider *you* configure, with *your* key,
directly. Nothing routes through Embabel. But a key still means your text reaches
OpenAI or Anthropic, and for some work — a contract, a medical letter, a salary review
— that is the wrong trade at any price.

You can run models on your own machine instead, and mix: the sensitive job local, the
hard job hosted.

## What you already have

**Embeddings are local by default and always have been.** Every document you add is
turned into vectors by a model running on your own hardware via Docker Model Runner —
which is why Model Runner is a prerequisite rather than an option. Document search and
memory therefore cost no tokens and send nothing anywhere, before you configure a
single thing.

That is the bulk of the volume. A large document library is thousands of embedding
calls and a handful of chat calls.

## Adding LM Studio or Ollama

Install either one and start it as you normally would. Load a model. The appliance
looks for both on your host at their usual ports, so on a normal setup there is nothing
to configure.

Then **restart the appliance**:

```bash
embabel down && embabel up
```

This is not optional and it is the commonest confusion: the appliance registers models
at **boot**. A model you load in LM Studio afterwards is invisible until the next
start. Restarting is cheap and your data is untouched — only the app container is
recreated.

Your models then appear in **Models**, grouped first and marked `local`.

### If they are on unusual ports, or another machine

`.env` takes both:

```bash
OLLAMA_BASE_URL=http://192.168.1.20:11434
LMSTUDIO_BASE_URL=http://host.docker.internal:1234
```

`host.docker.internal` is how a container reaches your machine — inside the container,
`localhost` is the container itself, which is why a server you can see in your browser
is invisible to the appliance without it.

## Choosing what runs where

Nothing routes to a local model just because it exists. The **Models** tab lists the
jobs the appliance does — chat, everyday work, code, document search — and you pick a
model per job. That granularity is the whole point: put document work and memory
extraction on a local model, where the volume and the sensitivity both are, and leave
the hardest reasoning hosted until you decide otherwise.

Two behaviours worth knowing before you wonder why nothing happened:

- **Changing a job's model takes effect immediately.** The next piece of work uses it.
- **Changing the appliance-wide default restarts the appliance**, because it is read at
  boot. The console says so on the button.

## What it costs you

Honesty is more useful here than encouragement.

A 7–14B model on a laptop is genuinely good at summarising, extracting entities,
classifying, and answering from documents you have given it. It is noticeably worse at
long multi-step reasoning, at writing correct code, and at holding a complicated
instruction without drifting. Work that involves planning across many steps is where
you will feel the gap first.

The practical shape most people land on: **local for volume and sensitivity, hosted for
difficulty.** You are not choosing a side once — you are choosing per job, and you can
change your mind after seeing the output.

Two other costs, stated plainly: local models use your RAM and your battery, and a big
one will make everything else on the machine slower while it works.

## Fully local

If you want nothing leaving at all: set every job in **Models** to a local model, leave
embeddings on their local default, and remove your provider keys from `.env`. The
appliance will run — the graph, realms, documents, handlers, scheduled work and the
console are all local machinery — and the quality ceiling becomes whatever your
hardware runs.

What still leaves, in that configuration, is usage reporting — counts and versions, no
content — and any realm you have installed that talks to an external API, because that
is what you installed it to do. Both are covered in
[what stays on your machine](privacy.md), including how to switch the first off.
