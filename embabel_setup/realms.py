"""Realm checkouts, and the world a new appliance is cloned from.

A realm checkout directory is the operator's own repositories, shared into the
container so the appliance can load realms from disk rather than only from the
directory. It is asked for once and remembered in .env.

Nothing here deletes a checkout, ever — they are somebody's work in progress and
this installer has no business touching them.
"""
import os
import re
import subprocess
import sys

from .colour import MIDDOT, TICK, bold, dim, url, warn
from .core import APPLIANCE_DIR, SetupError, prompt_path
from .settings import env_file_value, set_env_var
from .colour import heading
from .words import say

# Docker Desktop shares these host prefixes with containers out of the box. A
# bind mount from ANYWHERE else resolves to an EMPTY directory rather than an
# error, which is the worst failure mode this feature has: everything starts,
# nothing is visible, and there is nothing in any log to explain it. So warn at
# the moment the path is chosen, while the operator still knows what they typed.
DOCKER_SHARED_PREFIXES = ("/Users", "/Volumes", "/private", "/tmp", "/var/folders")


def resolve_world_repo(spec: str) -> str:
    """A world-template repo from what a person can type — or paste from a link.

    A full URL passes through untouched; `owner/repo` lands on GitHub; a BARE name
    resolves only inside the embabel org, so a short name in a mailed instruction
    cannot be squatted elsewhere. Whatever arrives, the resolved URL is echoed
    before anything uses it: a world template ships behavior, and the operator
    should see exactly whose."""
    if spec.startswith(("http://", "https://", "git@", "file://")):
        return spec
    if "/" in spec:
        return f"https://github.com/{spec}.git"
    return f"https://github.com/embabel/{spec}.git"


def set_bootstrap_world(spec: str) -> None:
    """Write ASSISTANT_BOOTSTRAP_WORLD into .env."""
    repo = resolve_world_repo(spec)
    set_env_var("ASSISTANT_BOOTSTRAP_WORLD", repo,
                ("# World template new worlds are cloned from (set by ./setup.py --world).",))
    print(f"  World template: {repo}")
    print("  (applies when a world is FIRST created — existing worlds keep their shape)\n")


def inspect_realms_dir(raw: str) -> tuple[str | None, list[str], list[str]]:
    """Look at a candidate realms directory and say what is there.

    Returns (resolved absolute path or None, realm names visible, notes to print).
    None means the path cannot be used and the first note says why — nothing is
    raised, because the interactive path wants to ask again rather than exit.
    """
    path = os.path.realpath(os.path.expanduser(raw.strip()))
    notes: list[str] = []

    if not os.path.exists(path):
        return None, [], [f"{path} does not exist."]
    if not os.path.isdir(path):
        return None, [], [f"{path} is not a directory."]
    if not os.access(path, os.R_OK | os.X_OK):
        return None, [], [f"{path} is not readable."]

    # The likeliest mistake by a distance: pointing at the realm instead of at
    # the directory the realms live in. Catch it by name, because the mount would
    # otherwise succeed and simply show nothing.
    if os.path.exists(os.path.join(path, "realm.yml")):
        return None, [], [
            f"{path} is itself a realm — this wants the directory your realms live IN,",
            f"so that adding one is a clone rather than an edit here. Try: {os.path.dirname(path)}",
        ]

    realms = sorted(
        entry for entry in os.listdir(path)
        if os.path.exists(os.path.join(path, entry, "realm.yml"))
    )

    if sys.platform == "darwin" and not path.startswith(DOCKER_SHARED_PREFIXES):
        notes.append(
            "Docker Desktop does not share this path by default — the mount would be EMPTY."
        )
        notes.append(
            "Add it under Settings -> Resources -> File sharing, or choose a path under /Users."
        )
    return path, realms, notes


def set_realms_dir(path: str) -> None:
    """Write EMBABEL_REALMS_DIR into .env."""
    set_env_var("EMBABEL_REALMS_DIR", path, (
        "# Realm checkouts on this machine, mounted read-only at /realms.",
        "# See realms/README.md; ./worlds.py asked for this on first run.",
    ))


def announce_realms(path: str, realms: list[str], notes: list[str]) -> None:
    print(f"  Realm checkouts: {path}")
    if realms:
        shown = ", ".join(realms[:6]) + (f" and {len(realms) - 6} more" if len(realms) > 6 else "")
        print(f"  {len(realms)} realm{'s' if len(realms) != 1 else ''} visible: {shown}")
    else:
        print("  No realms there yet — clone one in and it is visible on the next start.")
    for note in notes:
        print(f"  {warn('!')} {note}")
    print()
    say("realms-linked")
    print()


def ensure_realms_dir(mode: str, explicit: str | None) -> None:
    """Point the containers at realm checkouts on this machine.

    Must run BEFORE the mode starts: compose reads .env when it creates the
    container, so a value written afterwards applies to the next start, not this
    one — the same reason the world template is set up here.

    Asked only in the worlds mode, and only once. Worlds is the door a realm
    author comes through, and a question every run is a question people learn to
    hit Enter through without reading.
    """
    if explicit:
        path, realms, notes = inspect_realms_dir(explicit)
        if not path:
            raise SetupError("--realms: " + " ".join(notes))
        set_realms_dir(path)
        announce_realms(path, realms, notes)
        return

    if os.environ.get("EMBABEL_REALMS_DIR"):
        return  # exported in the shell — compose sees it directly
    if os.path.exists(".env"):
        with open(".env") as f:
            if any(line.strip().startswith("EMBABEL_REALMS_DIR=") for line in f):
                return  # already answered, either way
    if mode != "worlds" or not sys.stdin.isatty():
        return

    print("\n" + heading("Working on realms"))
    say("realms")
    print()

    default = os.path.realpath("realms")
    for _ in range(3):
        # prompt_path, not prompt: this is the one question in setup that wants a
        # path typed from memory, and Tab completing it is the difference between
        # a guess and a choice. The bracketed default is what Enter takes — said
        # in words above too, because a `[default]` convention is only obvious to
        # people who already know it.
        answer = prompt_path(f"  Realm checkouts directory [{default}]: ").strip()
        path, realms, notes = inspect_realms_dir(answer or default)
        if path:
            set_realms_dir(path)
            announce_realms(path, realms, notes)
            return
        for note in notes:
            print(f"  {note}")
        print()
    print("  Skipping — set EMBABEL_REALMS_DIR in .env when you want it.\n")
