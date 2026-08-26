"""The two things every other module needs: where the appliance is, and how it
fails. Kept apart so nothing has to import a large module to raise an error."""

from __future__ import annotations
import os
import re
import sys

# Every path is absolute rather than relying on a chdir, because the Me app and
# the CLI both call in from their own working directories.
APPLIANCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SetupError(Exception):
    """Anything the operator can act on. Printed as a sentence, never a traceback."""


class AlreadySetUp(SetupError):
    """The appliance is up and configured — not a failure, and handled as such."""


class Unreachable(SetupError):
    """The door did not answer. Distinct from a refusal, which is an answer."""


class Timeout(Unreachable):
    """The appliance took longer to answer than we were willing to wait.

    A SUBCLASS of Unreachable, so every `except Unreachable` still catches it — but a
    distinct type, because the two are not the same fact and must not read the same. A
    refused connection means nothing is listening. A timeout means something IS, and is
    busy; on the provider step it is usually busy waiting on the provider, and calling
    that "could not reach the appliance" sent at least one person hunting a Docker
    problem that did not exist.
    """


class TokenRejected(SetupError):
    """The setup token was wrong."""


# THE MODE VOCABULARY, here because both settings and dockerlib speak it and
# neither owns it: settings resolves which mode was configured, dockerlib runs
# compose against it. Putting these in either one made the other import it, and
# the cycle showed up as configured_mode losing MODE_COMPOSE.
# The appliance's two modes, by compose SERVICE name. Containers are found through
# their compose service label rather than a hardcoded container_name, so plain
# `./setup.py` works for whichever mode is up — and keeps working if a name or
# project prefix ever changes.
MODE_SERVICES = ("assistant", "worlds")

# Mode name (what a person types) -> compose file + service. `./worlds.py` and
# `./setup.py worlds` ride these to make first run a single command.
MODE_COMPOSE = {"me": "docker-compose-me.yml", "worlds": "docker-compose-worlds.yml"}

MODE_SERVICE = {"me": "assistant", "worlds": "worlds"}

# What has to exist before the appliance can answer at all: the graph and the mode
# itself (plus the console, which IS the worlds surface). Together about 0.8GB.
MODE_CORE = {
    "me": ("neo4j", "assistant"),
    "worlds": ("neo4j", "worlds", "worlds-console"),
}

# Operator mounts, written by the Me app's "Local files" panel: host folders the
# assistant may index, bind-mounted read-only under /local. Plain `docker compose
# up` merges this file by compose convention, but the explicit -f list used below
# switches that convention OFF, so it must be re-included by hand — for the me
# mode only, because it overrides the `assistant` service, which the worlds file
# does not define (merging it there would fabricate an image-less service).
OVERRIDE_FILE = "docker-compose.override.yml"


# How long to wait for a container that is still booting. Here because both the
# token hunt and the restart wait need it and neither owns it.
BOOT_WAIT_SECONDS = 120


# ── plumbing ────────────────────────────────────────────────────────────────

# Terminal escape sequences that PASTING smuggles into an answer. A modern terminal
# wraps every paste in bracketed-paste markers (ESC[200~ … ESC[201~), and input()
# reading /dev/tty keeps them — so a pasted username was stored with "[200~" glued
# to its front and sign-in failed against the name the user thought they chose
# (observed on a fresh install, 2026-08-25). CSI covers those markers, arrow keys
# and colour codes; the control-character sweep afterwards catches any stray ESC.
_TERMINAL_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _sanitize(raw: str) -> str:
    cleaned = _TERMINAL_ESCAPES.sub("", raw)
    return "".join(ch for ch in cleaned if ch >= " " or ch == "\t")


def prompt(text: str) -> str:
    """prompt(), with the one failure it has in this program handled.

    Setup is interactive by design, and the way it is invoked most often —
    `curl … | sh` — hands it a stdin that is already at EOF, because the shell
    read the installer from that same pipe. The result was EOFError surfacing as
    a traceback under the first question it asked, which reads as the software
    being broken rather than as a terminal being absent.

    install.sh now reattaches /dev/tty before handing over, so this is the second
    line of defence — for a genuinely non-interactive run, where the right answer
    is to say which command to run by hand.
    """
    try:
        return _sanitize(input(text))
    except EOFError:
        raise SetupError(
            "No terminal to ask on — setup needs to ask you a few questions.\n"
            "Run it directly:  cd ~/embabel/worlds && ./worlds.py   (or ./me.py)"
        )


# The Me app — the native menu-bar sensor (plain JavaScript on Electron, no
# build step). Me onboarding ends by offering to start it.
ME_APP_DIR = "me-app"


# The embedding model this appliance requires, by exact tag. Stated here as well
# as in infra.yml because doctor has to check for it before compose is involved,
# and because the tag is load-bearing: `latest` on that repository is the 4B
# variant with different dimensions, and embeddings are sticky once written.
EMBEDDING_MODEL = "ai/qwen3-embedding:0.6B-F16"
