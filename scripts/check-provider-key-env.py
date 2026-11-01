#!/usr/bin/env python3
"""Provider key variables reach a container bare, or not at all.

The appliance's own server reads its model keys from the credential store in the data
volume and never from its environment. The compose files forwarded OPENAI_API_KEY and
ANTHROPIC_API_KEY as `${VAR:-}` anyway, with comments saying an absent key crashed the
server and an empty one shadowed the stored key. Neither has been true since the image
stopped baking the provider starters (embabel/me#1848), and the comments sent a diagnosis
of "every role answers with the setup-required placeholder" looking at the wrong line.

Bare is the honest form for what still reads them — Open WebUI and the code sandbox — and
`${VAR:-}` coming back would bring the old story back with it. So this pins both halves:
every service that forwards one forwards it bare, and the appliance service still forwards
both, because the sandbox it starts is handed ANTHROPIC_API_KEY from its environment.

Standard library only, so it reads the one shape these files use rather than YAML.
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROVIDER_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
SERVICE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
ENTRY = re.compile(r"^\s+-\s+(" + "|".join(PROVIDER_KEYS) + r")(=.*)?\s*$")


def forwarded(path):
    """service -> {variable: the text after the name, '' when bare}."""
    found, name = {}, None
    with open(path) as f:
        for line in f:
            started = SERVICE.match(line)
            if started:
                name = started.group(1)
                continue
            entry = ENTRY.match(line)
            if entry and name is not None:
                found.setdefault(name, {})[entry.group(1)] = entry.group(2) or ""
    return found


problems = []
for path in sorted(glob.glob(os.path.join(ROOT, "*.yml"))):
    for service, variables in forwarded(path).items():
        for variable, value in variables.items():
            if value:
                problems.append(
                    f"{os.path.basename(path)}: `{service}` forwards {variable}{value}. Forward it bare: "
                    f"the server never reads a provider key from its environment, so an empty value "
                    f"protects nothing (see the provider-key note in docker-compose-worlds.yml)."
                )

for compose, service in (("docker-compose-worlds.yml", "worlds"), ("docker-compose-me.yml", "assistant")):
    have = forwarded(os.path.join(ROOT, compose)).get(service, {})
    missing = [v for v in PROVIDER_KEYS if v not in have]
    if missing:
        problems.append(f"{compose}: `{service}` no longer forwards {', '.join(missing)}; the code sandbox reads ANTHROPIC_API_KEY from it")

if problems:
    sys.exit("\n".join(problems))

print("provider key variables are forwarded bare")
