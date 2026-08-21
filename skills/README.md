# Skills

Claude Code skills that ship with the appliance. A skill is instructions, not code:
it teaches a coding agent how to use this appliance well, and it runs on the user's
machine with the user's own tools.

That last part is why these are skills and not MCP tools. The MCP surface reaches
the appliance and deliberately reaches nothing else — no filesystem, no shell. But
the evidence about what a person *could* build a realm from is on their disk: the
`docker-compose.yml` with a Postgres in it, the OpenAPI spec, the half-finished
realm checkout. A skill can see that; a tool on the MCP surface cannot, and
shouldn't be able to.

## scout-realms

Surveys the directories you approve, works out with you which of your systems
deserve a realm, and installs the ones you agree to. It asks before it reads
anything, and proposes before it writes anything.

Use it when a fresh appliance is a blank page: `learn_sources` on a new install
returns nothing, because nothing has been connected yet, and this is the thing
that finds what to connect.

## Installing

Symlink, so a `git pull` updates the skill too:

```sh
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/scout-realms" ~/.claude/skills/scout-realms
```

Then ask Claude Code what realms you could build. Copy the directory instead of
linking it if you would rather pin the version you have.
