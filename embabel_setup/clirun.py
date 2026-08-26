"""Running it: start, stop, and why it is not working.

The verbs somebody reaches for when the appliance is misbehaving, or when they want
it to exist at all. Nothing here reimplements anything — setup.py owns the docker
checks, the staged start and the wizard, and two copies of "is Docker running" is how
they come to disagree.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys

from .cli import HERE, _emit, _sample_target, current_mode, resolve_instance, resolved_mode, run_setup, s
from .core import prompt

# ── verbs ───────────────────────────────────────────────────────────────────

def cmd_up(args) -> int:
    """Start the appliance and finish setting it up. Safe to run any time: setup.py
    reconciles a running mode rather than starting a second one."""
    return run_setup(resolved_mode(args.mode), *(["--fresh"] if args.fresh else []))


def cmd_down(args) -> int:
    mode = resolved_mode(args.mode)
    if args.wipe:
        print("  --wipe DELETES the world, the graph, documents and your account.")
        if input("  Type 'yes' to wipe: ").strip().lower() != "yes":
            print("  Nothing was touched.")
            return 1
        s.take_everything_down()
        print("  Wiped.")
        return 0
    s._compose(mode, "stop")
    print(f"  Stopped the {mode} mode. Your data is untouched — `embabel up` brings it back.")
    return 0


def cmd_status(args) -> int:
    """What is running, what is still arriving, and where to go.

    The staged start means "not everything is up" is a NORMAL state for the first
    quarter of an hour, so this reports the two groups separately. A flat container
    list cannot tell "still downloading" from "broken", which is the only question
    being asked.
    """
    mode = current_mode()
    if not mode:
        print("  Nothing is running.  `embabel up` starts it.")
        return 0

    container = s.find_mode_container(mode)
    base = s.container_base_url(container) if container else None
    print(f"  Mode: {mode}" + (f"   API: {base}" if base else ""))

    running = set()
    run = s._docker("ps", "--format", "{{.Names}}")
    if run and run.returncode == 0:
        running = {line.strip() for line in run.stdout.splitlines() if line.strip()}

    core = [c["name"] for c in s.appliance_containers() if c["state"] == "running"]
    print(f"\n  Core            {len(core)} container(s) up")

    # Named individually because the interesting answer is WHICH one is missing.
    pending = []
    for name, why in (("embabel-appliance-docling", "structured PDF and Office conversion"),
                      ("embabel-appliance-grafana", "dashboards"),
                      ("embabel-appliance-prometheus", "metrics")):
        if name not in running:
            pending.append(why)
    if pending:
        print(f"  Still arriving  {', '.join(pending)}")
        print("                  (downloading in the background; the appliance works without them,")
        print("                   but a PDF added now ingests as flat text)")
    else:
        print("  Extras          all up")

    if base:
        print()
        (s.print_worlds_surfaces if mode == "worlds" else s.print_me_surfaces)(base)
    return 0


def docker_config_path() -> str:
    """Where the Docker CLI reads its config, which is not always `~/.docker`.

    DOCKER_CONFIG names a DIRECTORY — the CLI reads `config.json` inside it. Reading the
    default while docker reads somewhere else would make this checker pass on exactly the
    machine it exists to catch, which is worse than not checking at all.
    """
    root = os.environ.get("DOCKER_CONFIG") or os.path.join(os.path.expanduser("~"), ".docker")
    return os.path.join(root, "config.json")


def shorten_home(path: str) -> str:
    """`~/.docker/config.json` rather than the whole thing — these paths are printed in a
    terminal somebody is reading, and the home prefix is noise they already know."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def docker_credential_helpers() -> list[tuple[str, str]]:
    """Every credential helper docker's config names, with where it was named.

    `credsStore` applies to all registries; `credHelpers` maps particular ones. Both are
    checked, because a helper named for a registry this appliance never pulls from still
    cannot break anything, and one named globally breaks everything.

    Unreadable or absent config is not a problem — that is the normal state of a machine
    that has never logged in to a registry — so it yields nothing rather than a warning.
    """
    path = docker_config_path()
    shown = shorten_home(path)
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(config, dict):
        return []
    named = []
    store = config.get("credsStore")
    if isinstance(store, str) and store:
        named.append((store, f"credsStore in {shown}"))
    helpers = config.get("credHelpers")
    if isinstance(helpers, dict):
        for registry, helper in sorted(helpers.items()):
            if isinstance(helper, str) and helper and helper not in [n for n, _ in named]:
                named.append((helper, f"credHelpers[{registry}] in {shown}"))
    return named


def cmd_doctor(args) -> int:
    """Everything that has actually gone wrong for somebody, checked in one place.

    Each line here is a real failure with a real diagnosis attached, because the
    ones that cost time are the ones that fail SILENTLY — a mount outside Docker's
    file sharing that resolves empty, a private image, a Model Runner that is off.
    """
    problems = 0
    print(f"  Appliance directory: {HERE}\n")

    def check(label: str, ok: bool, fix: str = "") -> None:
        nonlocal problems
        print(f"  {s.TICK if ok else s.CROSS}  {label}")
        if not ok:
            problems += 1
            if fix:
                print(f"     {s.dim(fix)}")

    check("docker installed", shutil.which("docker") is not None,
          "Install Docker Desktop: https://docs.docker.com/get-started/get-docker/")
    daemon = s._docker("info")
    check("docker running", bool(daemon and daemon.returncode == 0),
          "Start Docker Desktop, then run this again.")
    compose = s._docker("compose", "version")
    check("docker compose v2", bool(compose and compose.returncode == 0),
          "Update Docker Desktop, or install the compose plugin.")
    # THE CREDENTIAL HELPER, which fails in a way that looks nothing like its cause.
    #
    # `~/.docker/config.json` can name a helper binary — `credsStore: desktop` is what
    # Docker Desktop writes — and the CLI then runs that binary for EVERY registry,
    # including anonymous pulls of public images. If the binary is not on PATH the pull
    # dies with `error getting credentials - err: exec: "docker-credential-desktop":
    # executable file not found in $PATH` before a single byte is fetched, and the
    # appliance's own message on top of it is the useless "docker compose up failed".
    #
    # Docker Desktop installs the symlink into /usr/local/bin, and declining the admin
    # prompt that asks to do so is enough to leave a machine in exactly this state.
    for helper, where in docker_credential_helpers():
        check(f"docker credential helper '{helper}' on PATH", shutil.which(f"docker-credential-{helper}") is not None,
              f"Named by {where}, but docker-credential-{helper} is not on PATH, so every pull "
              f"fails before it starts.\n"
              f"       Put Docker Desktop's own bin directory on your PATH:\n"
              f"         export PATH=\"$PATH:/Applications/Docker.app/Contents/Resources/bin\"\n"
              f"       or drop the helper — these images are public and need no credentials:\n"
              f"         remove the \"credsStore\" line from {shorten_home(docker_config_path())}")

    runner = s._docker("model", "status")
    check("Docker Model Runner (embeddings run locally)", bool(runner and runner.returncode == 0),
          "Enable it in Docker Desktop (Settings → AI), or: docker desktop enable model-runner")

    # THE EMBEDDING MODEL IS A HARD REQUIREMENT, not a nicety: the server builds
    # its embedding bean at startup and refuses to start without one, which
    # surfaces as an UnsatisfiedDependencyException and a stack trace naming
    # Spring rather than a missing download. Checked by name, because the tag
    # matters — `latest` on that repository is the 4B variant with different
    # dimensions, and embeddings are sticky once written.
    if runner and runner.returncode == 0:
        listed = s._docker("model", "list")
        have = listed.stdout if listed and listed.returncode == 0 else ""
        wanted = s.EMBEDDING_MODEL
        check(f"embedding model {wanted}", wanted.split("/")[-1].split(":")[0] in have
              and wanted.split(":")[-1] in have,
              f"Not pulled yet. The appliance cannot start without it:\n"
              f"       docker model pull {wanted}")

    # NOT a check: a fresh checkout has no .env and is not broken. Flagging it with
    # a cross and then explaining that the cross does not mean anything is how a
    # doctor teaches people to ignore its crosses.
    env_path = s.env_path()
    if os.path.exists(env_path):
        print(f"  {s.TICK}  {s.env_file()} present")
    else:
        print(f"  {s.MIDDOT}  " + s.dim(f"no {s.env_file()} yet — this appliance has not been set up here (`embabel up`)"))

    realms = os.environ.get("EMBABEL_REALMS_DIR")
    if not realms and os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("EMBABEL_REALMS_DIR="):
                    realms = line.split("=", 1)[1].strip()
    if realms:
        path, found, notes = s.inspect_realms_dir(realms)
        check(f"realm checkouts at {realms}", path is not None, " ".join(notes))
        if path:
            print(f"     {len(found)} realm(s) visible")
    else:
        print(f"  {s.MIDDOT}  " + s.dim("no realm checkouts linked   (embabel realms link <dir>)"))

    strays = s.stray_sandbox_containers()
    if strays:
        print(f"  {s.MIDDOT}  " + s.dim(f"{len(strays)} stray code-sandbox container(s) — `embabel prune` removes them"))

    print()
    if not problems:
        print("  " + s.good("All good."))
    else:
        print("  " + s.warn(f"{problems} problem{'s' if problems > 1 else ''} above")
              + " — each line says what to do.")
    return 1 if problems else 0


def cmd_logs(args) -> int:
    mode = resolved_mode(args.mode)
    service = args.service or s.MODE_SERVICE[mode]
    return s._compose(mode, "logs", *(["-f"] if args.follow else []), "--tail", str(args.tail), service).returncode


def cmd_open(args) -> int:
    """A surface, in a browser. With nothing named, the surface is THIS appliance's
    front door — the console for worlds, the assistant for me. Defaulting to the
    console regardless sent every Me user to a port with nothing behind it."""
    urls = s.surface_urls()
    what = args.what or ("me" if resolved_mode(None) == "me" else "console")
    url = urls[what]
    print(f"  {url}")
    # The URL is printed FIRST and unconditionally: on a headless box, or over ssh,
    # there is no browser to open and the address is the whole answer. The opening
    # itself is setup.py's, so `embabel open` and the end of setup agree about when
    # a browser is the wrong answer.
    s.open_in_browser(url)
    return 0


def cmd_ps(args) -> int:
    """What this appliance has on the host right now.

    THREE GROUPS, because they fail differently. The appliance's own containers
    are the product. The deferred extras are allowed to be missing for the first
    quarter of an hour and are not a fault. Code sandboxes are siblings created
    through the docker socket, not compose services — `down` does not take them,
    which is exactly why they need somewhere to be visible.
    """
    containers = s.appliance_containers()
    if args.json:
        print(json.dumps({"containers": containers, "sandboxes": s.stray_sandbox_containers()}, indent=2))
        return 0

    if not containers:
        print("\n  Nothing of this appliance is on the host.  `embabel up` starts it.\n")
        return 0

    print()
    width = max(len(c["name"]) for c in containers)
    for c in containers:
        up = c["state"] == "running"
        mark = s.good(s.BULLET) if up else s.MIDDOT
        line = f"{c['name']:<{width}}  {c['status']}"
        print(f"  {mark} " + (line if up else s.dim(line)))

    missing = [name for name in s.DEFERRED_WHY
               if not any(name in c["name"] for c in containers)]
    if missing:
        print("\n  Not here yet   " + ", ".join(s.DEFERRED_WHY[m] for m in missing))
        print("                 (deferred; they download after the appliance is up)")

    strays = s.stray_sandbox_containers()
    if strays:
        print(f"\n  Code sandboxes   {len(strays)}")
        for name in strays[:8]:
            print(f"    {name}")
        if len(strays) > 8:
            print(f"    … and {len(strays) - 8} more")
        print("  Siblings of the appliance, not compose services — `embabel prune` clears them.")
    print()
    return 0


def cmd_prune(args) -> int:
    """Remove what the appliance left behind but does not own.

    Sandboxes only. NOT `docker system prune`, and not dangling images: this
    command runs on a developer's machine where most of what docker considers
    garbage belongs to somebody else's work, and a cleanup verb that reaches
    past its own project is a cleanup verb people learn not to run.
    """
    strays = s.stray_sandbox_containers()
    if not strays:
        elsewhere = s.stray_sandbox_containers(mine_only=False)
        if elsewhere:
            print(f"  Nothing to prune for '{s.instance()}'. "
                  f"({len(elsewhere)} sandbox(es) here belong to another instance.)")
        else:
            print("  Nothing to prune — no code-sandbox containers on the host.")
        return 0

    print(f"\n  {len(strays)} code-sandbox container(s):")
    for name in strays[:12]:
        print(f"    {name}")
    if len(strays) > 12:
        print(f"    … and {len(strays) - 12} more")
    # The warning that makes this safe. These carry a label, not an owner, and
    # this command cannot tell an orphan from a live IDE session.
    others = len(s.stray_sandbox_containers(mine_only=False)) - len(strays)
    print(f"\n  These belong to '{s.instance()}' — the server labels every sandbox with the")
    print("  appliance that created it, so no other instance's sessions are listed here.")
    if others:
        print(f"  ({others} more on this host belong elsewhere and are left alone. A server run")
        print("   from an IDE labels its own 'standalone' and is never in this list.)")
    if not args.yes and input("\n  Remove them? [y/N]: ").strip().lower() not in ("y", "yes"):
        print("  Left alone.")
        return 1
    removed = s.prune_sandboxes(strays)
    print(f"  Removed {removed} of {len(strays)}.")
    return 0 if removed == len(strays) else 1
