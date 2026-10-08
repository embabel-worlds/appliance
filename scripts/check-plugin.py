#!/usr/bin/env python3
"""skills/ is a Claude Code plugin, and the directory that lists it reads it as one.

The Claude plugin directory takes the plugin from this public repository, path skills/,
and re-reads it on every commit to main: a version that breaks one of its rules is not
published, and the listing goes on serving the last one that passed — so a mistake here
does not fail anything a person sees, it just quietly stops the listing updating. These
are the rules we can check without the directory, plus three of our own:

  - the plugin's name is its install slug and can never change once listed
  - every skill has a section in skills/README.md, which IS the listing's description,
    so a new skill is not invisible on the page people choose it from
  - the OpenAI archive (scripts/build-openai-plugin.py) carries no MCP server and no
    installation prompt, the two things that directory refuses

    python3 scripts/check-plugin.py

`claude plugin validate ./skills` is the schema check, and needs Claude Code installed;
this one needs only Python.
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "skills")
NAME = "embabel-worlds"
# The directory's own limits: a non-image file over 256 KiB, or a binary it cannot
# read, holds a version for a reviewer; system files and links block it outright.
MAX_BYTES = 256 * 1024
TEXT = (".md", ".json", ".txt", ".svg")
BLOCKED = {".DS_Store", "Thumbs.db", "desktop.ini", "__MACOSX"}

failures = []


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        failures.append(f"{os.path.relpath(path, ROOT)}: {e}")
        return {}


manifest = load(os.path.join(PLUGIN, ".claude-plugin", "plugin.json"))
mcp = load(os.path.join(PLUGIN, ".mcp.json"))
market = load(os.path.join(ROOT, ".claude-plugin", "marketplace.json"))

if manifest.get("name") != NAME:
    failures.append(f"plugin.json: name is {manifest.get('name')!r}; it is the install slug and must stay {NAME!r}")
entries = [p for p in market.get("plugins", []) if p.get("name") == NAME]
if not entries or entries[0].get("source") != "./skills":
    failures.append(f"marketplace.json: no {NAME!r} entry with source ./skills")

declared = set(manifest.get("userConfig", {}))
for ref in re.findall(r"\$\{user_config\.([A-Za-z0-9_]+)\}", json.dumps(mcp)):
    if ref not in declared:
        failures.append(f".mcp.json: ${{user_config.{ref}}} is not declared in plugin.json userConfig")
for name, server in mcp.get("mcpServers", {}).items():
    for header, value in server.get("headers", {}).items():
        if "${user_config." not in value:
            failures.append(f".mcp.json: {name}'s {header} header is not a user_config value — a credential in a file blocks the listing")

for here, dirs, files in os.walk(PLUGIN):
    for name in dirs + files:
        path = os.path.join(here, name)
        rel = os.path.relpath(path, ROOT)
        if os.path.islink(path):
            failures.append(f"{rel}: a link — the directory installs only regular files")
        if name in BLOCKED:
            failures.append(f"{rel}: a system file, which blocks the listing")
    for name in files:
        path = os.path.join(here, name)
        if name in BLOCKED or os.path.islink(path):
            continue
        rel = os.path.relpath(path, ROOT)
        if not name.endswith(TEXT):
            failures.append(f"{rel}: not a text file, so a reviewer would have to read it by hand")
        elif os.path.getsize(path) > MAX_BYTES:
            failures.append(f"{rel}: over 256 KiB, which holds the version for a reviewer")

with open(os.path.join(PLUGIN, "README.md"), encoding="utf-8") as f:
    readme = f.read()
sections = set(re.findall(r"^## (\S+)\s*$", readme, re.M))
for entry in sorted(os.listdir(PLUGIN)):
    if os.path.isfile(os.path.join(PLUGIN, entry, "SKILL.md")) and entry not in sections:
        failures.append(f"skills/README.md: no '## {entry}' section, so the listing does not mention it")

spec = importlib.util.spec_from_file_location("build", os.path.join(ROOT, "scripts", "build-openai-plugin.py"))
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
with tempfile.TemporaryDirectory() as tmp:
    out = os.path.join(tmp, "openai.zip")
    names = build.build(out)
    with zipfile.ZipFile(out) as z:
        shipped = json.loads(z.read(f"{NAME}/.claude-plugin/plugin.json"))
    if "userConfig" in shipped:
        failures.append("OpenAI archive: plugin.json still carries userConfig")
    if any(n.endswith(".mcp.json") for n in names):
        failures.append("OpenAI archive: carries .mcp.json, a per-appliance server that directory cannot list")
    skills = {n.split("/")[1] for n in names if n.startswith("skills/") and n.endswith("/SKILL.md")}
    expected = {e for e in os.listdir(PLUGIN) if os.path.isfile(os.path.join(PLUGIN, e, "SKILL.md"))}
    if skills != expected:
        failures.append(f"OpenAI archive: skills {sorted(skills ^ expected)} differ from skills/")
    if "skills/VOICE.md" not in names:
        failures.append("OpenAI archive: skills/VOICE.md missing, and six skills point at ../VOICE.md")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print(f"plugin {NAME}: {len(expected)} skills, listing and OpenAI archive consistent")
