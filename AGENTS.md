# Working on this repo

This file is for coding agents working ON the appliance codebase — the Python setup, the compose
files, the docs, the skills. If you are here because an MCP server named `embabel` is connected
to your session, that is the OTHER document: `AGENT_GUIDE.md` is the client guide, and
`skills/*/SKILL.md` are the full runbooks.

Conventions here (`CLAUDE.md` is the canonical statement; these are the ones that bite):

- **Use a real library for a solved problem.** A hand-rolled parser is fragile in exactly the
  cases nobody tested.
- **Comments say why, not what** — and match the density and voice of the file you are editing.
  This codebase explains its decisions and its refusals, deliberately.
- **Block comments, not walls of `//`** in JS; module docstrings in Python.
- **Don't commit unless asked.** Make the change; leave the committing to the user.
- Compose files carry load-bearing comments (bare env vars vs `${VAR:-}`, read-only mounts);
  read the comment above a line before changing the line.
