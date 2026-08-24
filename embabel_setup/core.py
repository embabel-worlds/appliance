"""The two things every other module needs: where the appliance is, and how it
fails. Kept apart so nothing has to import a large module to raise an error."""
import glob
import os
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
        return input(text)
    except EOFError:
        raise SetupError(
            "No terminal to ask on — setup needs to ask you a few questions.\n"
            "Run it directly:  cd ~/embabel/worlds && ./worlds.py   (or ./me.py)"
        )


def prompt_path(text: str) -> str:
    """[prompt], with Tab completing directory names.

    A QUESTION THAT WANTS A PATH SHOULD COMPLETE ONE. Typing an absolute path
    from memory, blind, into a wizard is the moment people paste something with
    a typo in it and get told their directory does not exist — when the shell
    they came from would have completed it for them.

    `readline` is stdlib and `input()` picks it up merely by its being imported,
    so this costs no dependency, which matters: the appliance installs through
    `curl … | sh` with nothing but a system python3, and a real TUI file browser
    would mean shipping a package to ask one question.

    Restores whatever completer was installed before, because this is a shared,
    process-global hook and the next question is not about paths.
    """
    try:
        import readline
    except ImportError:
        return prompt(text)  # Windows, or a python built without it

    def complete(partial: str, state: int) -> str | None:
        # Directories only, with a trailing separator so a second Tab descends.
        expanded = os.path.expanduser(partial)
        found = [p + os.sep for p in glob.glob(expanded + "*") if os.path.isdir(p)]
        # Give back the ~ they typed rather than the expansion: rewriting the
        # line under someone mid-type reads as the prompt fighting them.
        if partial.startswith("~"):
            home = os.path.expanduser("~")
            found = [("~" + p[len(home):]) if p.startswith(home) else p for p in found]
        return found[state] if state < len(found) else None

    previous, delims = readline.get_completer(), readline.get_completer_delims()
    readline.set_completer(complete)
    # Only whitespace splits a word here. The default delimiters include `/` and
    # `-`, which is right for identifiers and wrong for every path ever typed:
    # completion would restart at each slash and offer the wrong directory.
    readline.set_completer_delims(" \t\n")
    # macOS ships a libedit-backed readline that does not speak this config
    # syntax — the same two lines every stdlib-only CLI carries.
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
    try:
        return prompt(text)
    finally:
        readline.set_completer(previous)
        readline.set_completer_delims(delims)


# The Me app — the native menu-bar sensor (plain JavaScript on Electron, no
# build step). Me onboarding ends by offering to start it.
ME_APP_DIR = "me-app"


# The embedding model this appliance requires, by exact tag. Stated here as well
# as in infra.yml because doctor has to check for it before compose is involved,
# and because the tag is load-bearing: `latest` on that repository is the 4B
# variant with different dimensions, and embeddings are sticky once written.
EMBEDDING_MODEL = "ai/qwen3-embedding:0.6B-F16"
