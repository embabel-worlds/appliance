#!/usr/bin/env python3
"""A health check may only read variables its own container has.

In a compose file `$$` hands an expansion to the container's shell, so `$${NAME}` in a health
check is looked up INSIDE the container. A name that only the host has is simply unset there,
and a `:-default` after it hides that: the check runs, with the wrong value.

That is how Neo4j's check came to knock with the default password on every install that had
set its own. The database took the host's NEO4J_PASSWORD through NEO4J_AUTH; the check asked
the container for NEO4J_PASSWORD, which it was never given. The install waited six minutes on
a database that had started in five seconds, and then failed.

Standard library only, so it parses the two shapes these files use rather than YAML.
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
DECLARED = re.compile(r"^\s+-\s+([A-Za-z_][A-Za-z0-9_]*)=")
ASKED = re.compile(r"\$\$\{?([A-Za-z_][A-Za-z0-9_]*)")

# What a shell or an image provides without the service declaring it.
AMBIENT = {"HOME", "PATH", "HOSTNAME", "PWD"}


def services(path):
    """Each service's declared environment names and its health check line, in file order."""
    found, name = {}, None
    with open(path) as f:
        for line in f:
            started = SERVICE.match(line)
            if started:
                name = started.group(1)
                found[name] = {"declared": set(), "tests": []}
                continue
            if name is None:
                continue
            declared = DECLARED.match(line)
            if declared:
                found[name]["declared"].add(declared.group(1))
            if line.lstrip().startswith("test:"):
                found[name]["tests"].append(line.strip())
    return found


problems = []
for path in sorted(glob.glob(os.path.join(ROOT, "*.yml"))):
    for service, seen in services(path).items():
        for test in seen["tests"]:
            for asked in ASKED.findall(test):
                if asked not in seen["declared"] and asked not in AMBIENT:
                    problems.append(
                        f"{os.path.basename(path)}: the health check of `{service}` reads ${asked}, "
                        f"which the service does not declare. Inside the container it is unset."
                    )

if problems:
    sys.exit("\n".join(problems))

infra = services(os.path.join(ROOT, "infra.yml"))
neo4j_tests = infra.get("neo4j", {}).get("tests", [])
assert neo4j_tests, "infra.yml no longer gates the app on a Neo4j health check"
assert any("NEO4J_AUTH" in test for test in neo4j_tests), \
    "Neo4j's health check must take its password from NEO4J_AUTH, the one place the container has it"

print("health checks read only what their containers have")
