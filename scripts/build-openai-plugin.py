#!/usr/bin/env python3
"""The skills as a plugin archive for OpenAI's plugin directory (ChatGPT and Codex).

WHY A BUILD AND NOT A SECOND FOLDER. skills/ is already the Claude Code plugin, and a
copy of eleven skills kept beside it is a copy that drifts. OpenAI takes the same
`.claude-plugin/plugin.json` and converts it, but refuses three things the Claude plugin
needs: `userConfig` ("OpenAI doesn't run Claude installation prompts"), and an MCP server
whose address differs per person — every appliance has its own, and OpenAI's directory
lists only a server at one public https:// address it can verify. So this archive is the
skills-only submission: the same skills, re-rooted under skills/ where OpenAI looks for
them, with no MCP server and no installation prompt. People connect their own appliance
to ChatGPT or Codex as a custom MCP server, which the README in the archive says how to do.

    python3 scripts/build-openai-plugin.py [out.zip]     # default: dist/embabel-worlds-openai.zip
"""
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
# Not the plugin's content: the manifest is rewritten below, and .mcp.json is the
# per-appliance server OpenAI cannot list.
LEFT_OUT = {".claude-plugin", ".mcp.json", ".DS_Store", "__pycache__"}

# What the Claude README says about installing does not apply here, so the archive
# carries its own. Short on purpose: the per-skill sections live in skills/README.md.
README = """# Embabel Worlds skills

Skills for a coding agent working against your own Embabel appliance — a knowledge graph
and agent runtime that you install and run yourself. They survey a business for realms and
agents worth building, author views, handlers and apps, check every figure against the
source system, and diagnose a realm or an install that is not working.

The skills need an appliance to talk to. Install one with the command in
https://github.com/embabel-worlds/appliance#quick-start, then connect it as a custom MCP
server: the endpoint for coding agents is `<appliance URL>/mcp/code`, with the header
`Authorization: Bearer <API key>`. Make the key in the appliance console under
*Keys to this world*. Embabel Worlds answers on `http://localhost:11043`.

This plugin sends nothing anywhere: it is the skills, as text. What the appliance itself
sends out is listed in full at
https://github.com/embabel-worlds/appliance/blob/main/docs/guide/privacy.md.

Every skill is described in
https://github.com/embabel-worlds/appliance/blob/main/skills/README.md.
"""


def manifest() -> dict:
    with open(os.path.join(SKILLS, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        claude = json.load(f)
    # userConfig is refused outright; `skills` pointed at the Claude plugin root, and
    # the archive puts the skills in the default skills/ instead.
    return {k: v for k, v in claude.items() if k not in ("userConfig", "skills")} | {
        "description": "Skills for a coding agent working against your own Embabel appliance: "
                       "survey a business for realms and agents, author views, handlers and apps, "
                       "verify them against the source system, and diagnose what is not working.",
    }


def build(out: str) -> list[str]:
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    written = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        def add(arcname: str, data: bytes):
            z.writestr(f"embabel-worlds/{arcname}", data)
            written.append(arcname)

        add(".claude-plugin/plugin.json", (json.dumps(manifest(), indent=2) + "\n").encode())
        add("README.md", README.encode())
        with open(os.path.join(ROOT, "LICENSE"), "rb") as f:
            add("LICENSE", f.read())
        for here, dirs, files in os.walk(SKILLS):
            dirs[:] = sorted(d for d in dirs if d not in LEFT_OUT)
            for name in sorted(files):
                if name in LEFT_OUT or (here == SKILLS and name == "README.md"):
                    continue
                path = os.path.join(here, name)
                # VOICE.md stays beside the skill folders, so their `../VOICE.md` still resolves.
                with open(path, "rb") as f:
                    add("skills/" + os.path.relpath(path, SKILLS).replace(os.sep, "/"), f.read())
    return written


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "embabel-worlds-openai.zip")
    names = build(target)
    print(f"{target}: {len(names)} files, {sum(n.endswith('/SKILL.md') for n in names)} skills")
