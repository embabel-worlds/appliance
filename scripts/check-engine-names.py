#!/usr/bin/env python3
"""An appliance on the Embabel engine is never told it runs Neo4j.

WHY THIS EXISTS. Neo4j is one engine an operator can choose, and new installs start on
the Embabel engine, where there is no Neo4j at all. What the CLI and doctor.sh print was
written while Neo4j was the only engine, and a line that says Neo4j to somebody who does
not run it is false — a Neo4j Browser that does not exist, a heap to set on a database
that is not there. These are the outputs a person on the Embabel engine reads: every
verb's help, `embabel open graph`, `embabel version`, and doctor.sh's memory advice. Each
may name Neo4j only where it plainly scopes itself to the Neo4j option.

Nothing here talks to Docker: doctor.sh runs against a stub `docker` on PATH.

    python3 scripts/check-engine-names.py
"""
import argparse
import contextlib
import io
import os
import re
import stat
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from embabel_setup import cli, clicare, clirun, settings, versions  # noqa: E402
from embabel_setup.cliparser import build_parser  # noqa: E402

failures = []
NEO4J = re.compile("neo4j", re.IGNORECASE)

# A line may name Neo4j when it says, in the line, that it is about the Neo4j option.
SCOPED = (
    "on a Neo4j appliance",                       # `embabel open graph`, which only Neo4j has
    "move a Neo4j graph into the Embabel engine",  # `embabel graph`, the way off Neo4j
    "Neo4j, or a server store, onto the engine",   # its migrate example
)


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


def unscoped(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if NEO4J.search(line) and not any(s in line for s in SCOPED)]


@contextlib.contextmanager
def embabel_install():
    """A scratch checkout whose .env chose the Embabel engine."""
    with tempfile.TemporaryDirectory() as checkout:
        with open(os.path.join(checkout, ".env"), "w") as f:
            f.write("EMBABEL_PORT_BASE=11042\nGRAPH_TYPE=EMBABEL\n")
        clean = {k: v for k, v in os.environ.items() if k != settings.GRAPH_TYPE_VAR}
        with patch.object(settings, "APPLIANCE_DIR", checkout), patch.dict(os.environ, clean, clear=True):
            settings.use_instance(settings.DEFAULT_INSTANCE)
            yield


def printed(fn, *args) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        fn(*args)
    return out.getvalue()


# ── help: the same on every engine, so it may name Neo4j only as the option ──
# Wide, so argparse does not wrap a scoped line away from its scope.
os.environ["COLUMNS"] = "400"
parser = build_parser()
helps = {"embabel": parser.format_help()}
helps.update({f"embabel {verb}": sub.format_help() for verb, (sub, _help) in cli._subparsers(parser).items()})
for name, text in helps.items():
    check(not unscoped(text), f"`{name} --help` names Neo4j outside the Neo4j option: {unscoped(text)}")

with embabel_install():
    check(settings.graph_engine() == "EMBABEL", f"the scratch install reads as {settings.graph_engine()}")

    # ── `embabel open graph`: Neo4j Browser, which this engine has none of ──
    with patch.object(clirun, "resolved_mode", return_value="worlds"):
        said = printed(clirun.cmd_open, argparse.Namespace(what="graph", mode=None))
    check(said.strip() != "", "`embabel open graph` said nothing on the Embabel engine")
    check(not NEO4J.search(said), f"`embabel open graph` names Neo4j on the Embabel engine: {said!r}")

    # ── `embabel version`: the graph row ──
    check(versions.neo4j_image() is None, "an Embabel install reports a Neo4j image")
    shown = {"mode": "worlds", "release": "stable", "checkout": {"commit": "0" * 40, "branch": "main"},
             "appliance": {}, "source": {}, "engine": "EMBABEL", "neo4j": {}}
    with patch.object(clicare.s, "appliance_versions", return_value=shown), \
            patch.object(clicare.s, "version_pin_conflict", return_value=None):
        said = printed(clicare.cmd_version, argparse.Namespace(mode=None, json=False))
    check("the Embabel engine" in said, f"`embabel version` does not name the engine: {said!r}")
    check(not NEO4J.search(said), f"`embabel version` names Neo4j on the Embabel engine: {said!r}")


# ── doctor.sh: its low-memory advice, which names a Neo4j heap only to a Neo4j install ──
def doctor(graph_type: str) -> str:
    with tempfile.TemporaryDirectory() as home:
        os.makedirs(os.path.join(home, "embabel", "worlds"))
        with open(os.path.join(home, "embabel", "worlds", ".env"), "w") as f:
            f.write(f"EMBABEL_PORT_BASE=19990\nGRAPH_TYPE={graph_type}\n")
        stub = os.path.join(home, "bin")
        os.makedirs(stub)
        # 4 GB of memory, so the advice prints; nothing else, so the script stops at "no containers".
        with open(os.path.join(stub, "docker"), "w") as f:
            f.write('#!/bin/sh\ncase "$*" in *MemTotal*) echo 4294967296 ;; esac\nexit 0\n')
        os.chmod(os.path.join(stub, "docker"), stat.S_IRWXU)
        env = {"HOME": home, "PATH": f"{stub}:/usr/bin:/bin", "NO_COLOR": "1"}
        return subprocess.run(["sh", "doctor.sh"], capture_output=True, text=True, env=env, timeout=120).stdout


said = doctor("EMBABEL")
check("about 4 GB" in said, f"doctor.sh did not reach its memory advice: {said[-600:]!r}")
check(not NEO4J.search(said), f"doctor.sh names Neo4j to an Embabel install: {unscoped(said)}")
said = doctor("NEO4J")
check("NEO4J_HEAP=1G" in said, "doctor.sh no longer tells a Neo4j install how to halve its heap")

if failures:
    print("check-engine-names: FAILED")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print(f"check-engine-names: ok ({len(helps)} help texts, open, version, doctor.sh)")
