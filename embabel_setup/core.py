"""The two things every other module needs: where the appliance is, and how it
fails. Kept apart so nothing has to import a large module to raise an error."""
import os

# Every path is absolute rather than relying on a chdir, because the Me app and
# the CLI both call in from their own working directories.
APPLIANCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SetupError(Exception):
    """Anything the operator can act on. Printed as a sentence, never a traceback."""


class AlreadySetUp(SetupError):
    """The appliance is up and configured — not a failure, and handled as such."""


class Unreachable(SetupError):
    """The door did not answer. Distinct from a refusal, which is an answer."""


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
