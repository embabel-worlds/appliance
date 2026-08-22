# Making your own realm

A realm is how your world learns about a system you already run. Once installed, the
things in that system become things in your graph — queryable, joinable, and available
to anything the world does.

The word to hold onto is **join**. A realm that brings in data nothing else can connect
to is a second database you now maintain. A realm that shares an identifier with what
your world already knows — an email address, a company name, a repository — makes every
existing thing more useful the moment it lands.

## What a realm actually is

A directory with a `realm.yml` in it, and some files it names. Nothing is compiled;
most realms are declarations rather than code.

| | |
|---|---|
| **Types** | the things this realm knows about, and how they join onto what the world already holds |
| **Actions** | things it can do — fetch, search, create, update |
| **Views** | saved questions worth asking more than once |
| **Handlers** | reactions: when this happens, do that |
| **Apps** | small purpose-built surfaces over the above |

You do not need all of them. A useful realm can be one type file that says "these
records key on email address, and the world already has people keyed on email address."

## Nothing is copied

This is the part that surprises people who have integrated systems before. A realm does
not import your data into the graph and leave you with a copy going stale. It teaches
the world how to *reach* the system, and the world reaches out when a question needs
it. What lands in the graph is the shape of things and the joins between them.

The consequence is the good kind: there is no sync to break, no import job to schedule,
and no window during which your world is confidently wrong.

## The fast way: ask an agent

The realistic path for most people is to describe what you want and let a coding agent
build it. The appliance ships a skill for exactly this — see
[working with a coding agent](coding-agents.md) — and the loop is short enough that
being wrong twice costs less than planning once.

> "I have a Postgres database of subscriptions keyed on customer email. Build me a realm
> that joins it to the people already in my world."

What a good agent does next is worth knowing, because you should expect it: it asks
your world what it already holds, finds the anchor the join should hang off, writes the
realm into a checkout on your disk, asks the world to validate it, asks it to re-read
it, and then runs a query to show you the join actually answering. If it skipped
straight to writing files, ask it to show you the query.

## The manual way

Clone or create a realm directory where your checkouts live, and tell the appliance
where that is — once:

```bash
embabel realms link ~/dev
```

A world then loads it by path rather than by fetching it:

```yaml
# config/realms.yml, in your world
- name: subscriptions
  path: /realms/realm-subscriptions
```

The full format — every field, every type of file, worked examples — is the
[realm specification](https://worlds.embabel.com/spec/), and `realm_brief` over MCP
gives an agent the same thing without you pasting it.

## Two things that catch everyone

**The mount is read-only.** You edit on the host; the appliance only reads. That is not
a limitation to work around — it is what keeps your realm yours, in your git remote,
edited with your own tools.

**A build step only runs on clone.** If a realm declares an npm or wasm build, that
build happens when the appliance *clones* it — so it never fires for a realm you are
editing locally. Declarative realms need nothing. A realm with a build step must be
built on the host first, and forgetting this looks exactly like the realm being broken.

## Knowing whether it worked

Ask the world a question that requires the join. Not "is the realm installed" — that
tells you a file parsed. A realm is working when a question that needed two systems
gets one answer:

> "Which of my GitHub contributors have an open invoice?"

If that answers, the realm did its job. If it answers *emptily*, the join is usually
the problem rather than the data: the identifier on one side is not the identifier on
the other, and that is worth checking before anything else.

## Sharing it

A realm is a git repository. Push it to your own GitHub and anyone can install it by
name or URL, including you on another machine. Realms with a build step should be
published with that build declared, so a clone installs cleanly for whoever comes next.

If a realm is genuinely private — an internal system, a credentialled API — it works
the same way from a private repository, given a GitHub token the appliance can read.
