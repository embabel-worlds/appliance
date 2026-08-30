# Tours — showing somebody what your world does

A tour drives the actual interface. It opens panels, runs your saved views, waits while
you click something yourself, and explains what just happened — in the real app, on your
real data, not in a video of somebody else's.

That is the whole reason the feature exists. The hard part of this product is not
believing the description; it is seeing a graph answer a question no single system could
answer. A tour is the shortest path from "what is this" to that moment.

Tours appear under **Tours** in both the console and the Me app. Start one and it takes
over a strip of the window, drives the app, and stops between steps — you press Next.
It never runs away from you.

## Where tours come from

Four places, and they behave identically once loaded:

| Source | Arrives when | Removable |
|---|---|---|
| A realm | you install the realm | when the realm goes |
| The world you started from | at first setup | no — it is part of the template |
| Somebody else | you import their file | yes, any time |
| You | you record or write one | yes |

A realm shipping a tour is the common case and the one worth understanding: the walk
that shows off a realm belongs *with* that realm, so installing the realm brings its
demonstration and removing it takes the demonstration away. Nothing to clean up.

## Making one

**Record it.** Press Record, do the thing you want to teach, press Stop. What you get is
a *draft*: the actions are captured exactly, and every step's narration is a `TODO` for
you to fill in. The recorder knows what you clicked; it does not know why it mattered.

**Or ask a coding agent.** If you have Claude Code wired to your world — see
[working with a coding agent](coding-agents.md) — ask it to write a tour. It has a skill
for this and can read the views and panels your world actually has, so it will not invent
targets that do not exist.

Either way the result is a small YAML file with a closed set of eight verbs
(`say`, `ask`, `open`, `set`, `invoke`, `run`, `wait`, `expect`). That constraint is
deliberate and is what makes the next section true.

## Sharing one

Press **Export** and you get the tour as a file. Send it however you send files. The
recipient presses **Import a file…** on their own Tours panel.

The exchange format and the storage format are the same thing, so what you send is
exactly what a realm would ship. There is no separate publishing step, and nothing is
uploaded anywhere — the file goes from your machine to theirs by whatever means you
choose.

An imported tour is **yours**: it is listed with the rest, it survives restarts and
upgrades (it is written into your world directory, not held in memory), and you can
delete it whenever you like. A realm's tour and your world's own tour cannot be deleted
this way, because they leave when the thing that shipped them leaves.

If you want a tour to travel *with* the capability it demonstrates, put it in a realm's
`tours/` directory instead of sending the file. Then installing the realm is the whole
distribution story. See [making your own realm](realms.md).

### What a tour you received can and cannot do

Worth knowing, because you are about to let a file somebody else wrote drive your
interface:

- **It cannot reach the network, run a command, or read your disk.** There is no verb for
  any of those. The vocabulary is closed — that is the point of it being closed.
- **It cannot change your data.** A tour may carry a condition that checks whether you
  have already done a step. Those run read-only, and a write is rejected *before* it
  executes rather than rolled back after.
- **It cannot show you an image from somebody else's server.** Tour narration may include
  a picture, but only one your own appliance is serving. An image on the author's host
  would report back that you had opened their tour; that is refused.
- **It can press buttons in your app** — but only the ones the interface itself has
  published as tour-addressable, and only while you are watching.
- **It can ask you questions.** Your answers stay in your world. Treat a tour that asks
  for a credential the way you would treat any file asking for one: nothing here needs a
  key typed into a tour.

### One thing to check before you send

A tour that runs your saved views is naming things *your* world has. If the recipient has
not installed the realm those views came from, the tour will import cleanly and then stop
partway through when it reaches one.

So say what it needs when you send it — and prefer shipping the tour inside the realm,
which makes the problem impossible.

## Starting a world that already has one

A [world template](../../WORLD_TEMPLATES.md) is a git repo that decides what a new world
starts as — its realms, its apps, its configuration. Tours ride that same cascade, so a
template can ship the walk that explains the world it just built:

```bash
curl -fsSL https://raw.githubusercontent.com/embabel-worlds/appliance/main/install.sh \
  | sh -s -- --world arts-world
```

That is one command for somebody who has never seen the product: it installs the
appliance and brings up the realms that template names. A template that also ships a
`config/tours/` directory has its tour waiting under Tours when the install finishes —
marked as the world's own, so it cannot be deleted by accident and it leaves if the world
is ever rebuilt from a different template.

This is the preferred way to hand somebody a preconfigured appliance. A template is a
named, versioned artifact you can read, pin and fix after the fact — unlike a list of
options in a link, which is configuration that evaporates the moment it is used.
