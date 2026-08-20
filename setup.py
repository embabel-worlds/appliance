#!/usr/bin/env python3
"""Embabel appliance — first-run setup.

    ./setup.py

Walks you through creating your account and connecting a model provider. Nothing to
install: standard library only. Works for either mode — it finds the running
container (Me or Worlds), its port, and its setup token by itself.

If OPENAI_API_KEY or ANTHROPIC_API_KEY is already exported in your shell, the provider
step uses it instead of asking. `--ignore-env` always asks.

Forgot the password? `--reset-password` recreates the operator account and keeps
every piece of data — see reset_credentials for how and why that is sound.

This client is deliberately thin. It does not know what the steps ARE — it asks the
appliance (`GET /api/v1/setup`) and renders whatever it describes, so when the appliance
gains a step this client picks it up without changing. If you would rather drive the API
yourself, everything here is plain HTTP; see /swagger-ui on your instance.
"""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://localhost:4242"
TOKEN_HEADER = "X-Embabel-Setup-Token"
# The appliance's two modes, by compose SERVICE name. Containers are found through
# their compose service label rather than a hardcoded container_name, so plain
# `./setup.py` works for whichever mode is up — and keeps working if a name or
# project prefix ever changes.
MODE_SERVICES = ("assistant", "worlds")
# Mode name (what a person types) -> compose file + service. `./worlds.py` and
# `./setup.py worlds` ride these to make first run a single command.
MODE_COMPOSE = {"me": "docker-compose-me.yml", "worlds": "docker-compose-worlds.yml"}
MODE_SERVICE = {"me": "assistant", "worlds": "worlds"}
# Operator mounts, written by the Me app's "Local files" panel: host folders the
# assistant may index, bind-mounted read-only under /local. Plain `docker compose
# up` merges this file by compose convention, but the explicit -f list used below
# switches that convention OFF, so it must be re-included by hand — for the me
# mode only, because it overrides the `assistant` service, which the worlds file
# does not define (merging it there would fabricate an image-less service).
OVERRIDE_FILE = "docker-compose.override.yml"
# The Me app — the native menu-bar sensor (plain JavaScript on Electron, no
# build step). Me onboarding ends by offering to start it.
ME_APP_DIR = "me-app"
TOKEN_PATTERN = re.compile(r"Setup token:\s*([0-9a-f]{32,})")
# How long to keep watching a booting container for its token before asking.
BOOT_WAIT_SECONDS = 120
# Where the appliance keeps its operator account, inside the container: the
# credential file (bcrypt hashes) and the setup record. --reset-password
# deletes exactly these two; everything else on the volume is data.
ADMIN_DIR = "/data/embabel/assistant/admin"

# Provider -> the variable that provider's key lives in, mirroring ProviderValidator's
# PROVIDERS map on the server. If a provider is added there and not here, nothing breaks:
# it is simply not offered from the environment, and gets asked for as before.
PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# The GitHub token names the assistant itself checks, in its order
# (WorldBootstrap.resolveGitHubToken). A private realm or world template is cloned over HTTPS with
# this as the username, so without one the clone 404s and the realm is quietly absent.
GITHUB_TOKEN_VARS = ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN")


class SetupError(Exception):
    pass


class AlreadySetUp(SetupError):
    pass


class Unreachable(SetupError):
    pass


class TokenRejected(SetupError):
    """The setup token itself was refused — no answer to any step can fix that."""


# ── plumbing ────────────────────────────────────────────────────────────────

def call(base: str, path: str, token: str, payload: dict | None = None) -> dict:
    url = f"{base}/api/v1/setup{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header(TOKEN_HEADER, token)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body).get("detail") or json.loads(body).get("error") or body
        except ValueError:
            detail = body
        if e.code == 410:
            raise AlreadySetUp(
                "This appliance is already set up. Sign in at " + base +
                "\n(Forgot the password? --reset-password recreates the account and keeps all data.)"
            )
        if e.code == 401:
            raise TokenRejected(f"The setup token was not accepted.\n{detail}")
        raise SetupError(detail)
    except urllib.error.URLError as e:
        raise Unreachable(
            f"Could not reach the appliance at {base} ({e.reason}).\n"
            "Is it running? Try: docker compose ps"
        )
    except (http.client.HTTPException, ConnectionError, TimeoutError, OSError) as e:
        # DURING BOOT, "up" is a spectrum: Docker's port proxy accepts the TCP
        # connection before the app listens, then hangs up — RemoteDisconnected,
        # reset, or a stalled read, none of which urllib wraps in URLError. All of
        # them mean the same thing here: not reachable YET. The boot-wait loop in
        # discover_token owns retrying; crashing out of it turned a normal first
        # boot into a stack trace.
        raise Unreachable(
            f"Could not reach the appliance at {base} ({e.__class__.__name__}: {e}).\n"
            "Is it running? Try: docker compose ps"
        )


def _docker(*argv: str, timeout: int = 30) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def running_modes() -> dict[str, str]:
    """Running mode containers, keyed by compose service name."""
    found = {}
    for service in MODE_SERVICES:
        run = _docker("ps", "--filter", f"label=com.docker.compose.service={service}",
                      "--format", "{{.Names}}")
        if run is not None and run.returncode == 0:
            for name in run.stdout.split():
                found[service] = name
    return found


def find_mode_container(prefer: str | None = None) -> str | None:
    """The running mode's container. When a mode was explicitly asked for, PREFER it:
    announcing "setting up embabel-assistant" and then refusing because the caller
    asked for worlds is a small masterpiece of unhelpfulness."""
    modes = running_modes()
    if prefer and MODE_SERVICE.get(prefer) in modes:
        return modes[MODE_SERVICE[prefer]]
    if len(modes) > 1:
        print(f"  Both modes are running ({', '.join(modes.values())}) — run one at a time.")
    return next(iter(modes.values()), None)


def mode_service(container: str) -> str | None:
    """Which mode this container is — the compose service name from its label."""
    run = _docker("inspect", "-f", '{{index .Config.Labels "com.docker.compose.service"}}', container)
    return run.stdout.strip() if run is not None and run.returncode == 0 else None


def print_worlds_surfaces(base: str) -> None:
    """Worlds onboarding ends at the way in, not at "done": every
    surface a worlds operator reaches next, in one block. The API/MCP lines use the
    mode's real detected port; the rest are the compose defaults (.env moves them)."""
    print("  \u2500\u2500 Your Worlds surfaces " + "\u2500" * 38)
    print("  Console        http://localhost:4343   \u2190 START HERE")
    print("                 The Worlds console: realms, documents, keys, views, chat.")
    print("                 Opens with the commissioning sequence.")
    print()
    print(f"  API / UI       {base}   (the server itself)")
    print(f"  MCP endpoint   {base}/mcp")
    print("                 Authorization: Bearer \u2014 the token this setup just minted,")
    print("                 stored at /data/embabel/assistant/admin/providers.env")
    print("  TUI            docker compose -f docker-compose-worlds.yml run --rm tui")
    print("  Graph          http://localhost:4243  (neo4j / NEO4J_PASSWORD, default embabel-assistant)")
    print("  Dashboards     http://localhost:4246   \u00b7   Metrics  http://localhost:4247")
    print()


def me_app_settings_file() -> str | None:
    """Where the Me app keeps its settings — Electron's per-app userData directory,
    named for the app itself (`app.setName('Embabel Me')`). Only the platforms the
    app runs on are mapped; anywhere else, seeding is silently skipped."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Embabel Me", "settings.json")
    if sys.platform.startswith("linux"):
        config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        return os.path.join(config, "Embabel Me", "settings.json")
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return os.path.join(appdata, "Embabel Me", "settings.json") if appdata else None
    return None


def seed_me_app_settings(base: str, username: str | None) -> None:
    """Hand the Me app what setup already established: which mode to talk to, and
    who the user is. Retyping a URL and username the wizard just set up is pure
    friction — and the URL is worth seeding even at the default, because a
    non-default ASSISTANT_PORT would otherwise leave the app pointed at nothing.

    NEVER the password. Setup knows it, but a wizard silently writing a credential
    to a file the user did not choose is a different act from that user typing it
    into an app they can see — one keystroke of theirs is a fair price for that
    line staying honest.

    Only fills what is MISSING: an existing setting is the user's own answer, and
    a later run must not overwrite it with an assumption."""
    path = me_app_settings_file()
    if path is None:
        return
    try:
        current = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                current = json.load(f)
        if not isinstance(current, dict):
            return  # a settings file we do not recognise is not ours to rewrite
        seeded = dict(current)
        if not seeded.get("baseUrl"):
            seeded["baseUrl"] = base
        if username and not seeded.get("username"):
            seeded["username"] = username
        if seeded == current:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(seeded, f, indent=2)
    except (OSError, ValueError):
        # Seeding is a courtesy; the app asks for these anyway. Never fail setup
        # over a settings file.
        pass


def packaged_me_app() -> str | None:
    """The built app, if this machine has one — installed, or sitting in the repo's
    own release directory after `npm run package`. Returns a path `open -a` takes."""
    if sys.platform != "darwin":
        return None
    candidates = [
        "/Applications/Embabel Me.app",
        os.path.expanduser("~/Applications/Embabel Me.app"),
    ]
    release = os.path.join(ME_APP_DIR, "release")
    if os.path.isdir(release):
        for entry in sorted(os.listdir(release)):
            candidates.append(os.path.join(release, entry, "Embabel Me.app"))
    return next((c for c in candidates if os.path.isdir(c)), None)


def launch_me_app(base: str, username: str | None = None) -> None:
    """Me onboarding ends in the Me app, the way worlds onboarding ends at the
    console: the appliance thinks, the app senses, and a new user should meet
    both. The app is plain JavaScript on Electron — no build step — so `npm
    install` (run here on first use, for Electron itself) is the whole cost."""
    if not os.path.isdir(ME_APP_DIR):
        return  # a checkout without the app (or a remote setup) — nothing to offer
    seed_me_app_settings(base, username)
    print("\n  ── The Me app " + "─" * 47)
    print("  Your appliance thinks; the Me app senses. It sits in your menu bar,")
    print(f"  reads local signals, and sends only what you approve to {base}.")
    # A packaged build wins when there is one: it carries the app's own identity,
    # so macOS names IT in permission prompts ("Embabel Me wants to control Google
    # Chrome") instead of naming Electron, and the user gets no terminal, no npm,
    # and no build step. `npm start` stays the developer path.
    packaged = packaged_me_app()
    if packaged:
        answer = input("  Start it now? [Y/n]: ").strip().lower()
        if answer not in ("", "y", "yes"):
            print(f'  Whenever you like:  open -a "{packaged}"')
            return
        subprocess.Popen(["open", "-a", packaged], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('  Starting — look for "Me" in your menu bar. The appliance URL and your username')
        print("  are filled in already; enter your password and it will offer its first scan.")
        return

    npm = shutil.which("npm")
    if npm is None:
        print("  It needs Node.js (https://nodejs.org). Once that is installed:")
        print("      cd me-app && npm start")
        return
    answer = input("  Start it now? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        print("  Whenever you like:  cd me-app && npm start")
        return
    if not os.path.isdir(os.path.join(ME_APP_DIR, "node_modules", "electron")):
        # The app's one dependency, dev-time only. Foreground, so a failure is
        # visible and a slow download narrates itself instead of hanging mute.
        print("  First run — fetching Electron (npm install)…")
        run = subprocess.run([npm, "install"], cwd=ME_APP_DIR)
        if run.returncode != 0:
            print("  npm install failed — fix the error above, then: cd me-app && npm start")
            return
    # Detached, output dropped: the app outlives this wizard, and Electron's
    # chatter has no business in the terminal being handed back.
    subprocess.Popen([npm, "start"], cwd=ME_APP_DIR, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('  Starting — look for "Me" in your menu bar. The appliance URL and your username')
    print("  are filled in already; enter your password and it will offer its first scan.")


def container_base_url(container: str) -> str | None:
    """The mode's URL from its own SERVER_PORT. The compose files keep the host and
    container ports equal by design, so the container's port IS the published one."""
    run = _docker("inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", container)
    if run is not None and run.returncode == 0:
        for line in run.stdout.splitlines():
            if line.startswith("SERVER_PORT="):
                return f"http://localhost:{line.split('=', 1)[1].strip()}"
    return None


def github_token() -> tuple[str, str] | None:
    """A GitHub token for cloning PRIVATE realms and world templates, and where it came from.

    Same courtesy the provider keys get in `from_environment`: a developer who already has one
    should not be asked for it. The difference is where it lives. An OpenAI key is an env var or
    nothing, but a GitHub token on a working machine is usually in the `gh` CLI's own store and NOT
    exported — so the env vars are checked first (any of the three the assistant reads) and `gh` is
    the fallback that makes it automatic for most people.

    Returns None when there is nothing to find, which is the ordinary case for a user who only ever
    installs public realms. Nothing is written to disk: see `compose_env`.
    """
    for var in GITHUB_TOKEN_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value, f"${var}"
    try:
        run = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None  # no gh, or it misbehaved — a public-realm install is unaffected
    token = run.stdout.strip() if run.returncode == 0 else ""
    return (token, "the gh CLI") if token else None


def compose_env() -> dict:
    """The environment `docker compose` runs with.

    A discovered token is passed to the compose PROCESS rather than written to `.env`, and that is
    deliberate. `.env` is gitignored, but it is still a plaintext credential sitting in a checkout,
    and the timezone precedent does not carry: a zone is not a secret. The cost is that a later
    hand-run `docker compose up` gets no token unless the operator exports one — which is why the
    line printed below names the variable rather than just saying it worked.
    """
    env = dict(os.environ)
    found = github_token()
    if found:
        env["GITHUB_TOKEN"] = found[0]
    return env


def announce_github_token() -> None:
    """Say once, before the containers start, whether private realms will resolve. Never print the
    token itself — this runs in terminals people screen-share."""
    found = github_token()
    if not found:
        return
    print(f"  Found a GitHub token ({found[1]}) — private realms and world templates will clone.")
    print("  It is passed to the containers for this run only, never written to .env.\n")


def _compose(mode: str, *argv: str, capture: bool = False):
    """docker compose against the mode's file, from the appliance directory.
    capture=False inherits stdout/stderr — pulls and boots narrate themselves."""
    cmd = ["docker", "compose", "-f", MODE_COMPOSE[mode]]
    if mode == "me" and os.path.exists(OVERRIDE_FILE):
        cmd += ["-f", OVERRIDE_FILE]
    cmd += argv
    try:
        return subprocess.run(cmd, capture_output=capture, text=True, env=compose_env())
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        raise SetupError(f"docker compose failed: {e}")


def host_timezone() -> str | None:
    """The host's IANA zone name (e.g. Australia/Sydney), or None if unknowable.
    /etc/localtime is a symlink into a zoneinfo tree on macOS and most Linuxes;
    Debian-family systems also write the name to /etc/timezone."""
    try:
        target = os.readlink("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        with open("/etc/timezone") as f:
            return f.read().strip() or None
    except OSError:
        return None


def ensure_timezone() -> None:
    """Hand the containers the host's timezone. The images default to UTC, which
    puts every "now" the assistant utters — and every schedule it keeps — hours
    off for most of the planet. Compose interpolates TZ from the shell or .env;
    detect the zone once and write it to .env if the operator hasn't chosen one
    (either kind of existing choice wins and is never rewritten)."""
    if os.environ.get("TZ"):
        return  # exported in the shell — compose sees it directly
    if os.path.exists(".env"):
        with open(".env") as f:
            if any(line.strip().startswith("TZ=") for line in f):
                return
    zone = host_timezone()
    if not zone:
        return  # undetectable: the containers stay on UTC, as before
    with open(".env", "a") as f:
        f.write(
            "\n# Host timezone, detected by setup.py — without it the containers run UTC\n"
            "# and the assistant's clock is hours off. Edit or remove freely.\n"
            f"TZ={zone}\n"
        )
    print(f"  Wrote TZ={zone} to .env — the appliance will keep your local time.")


def fresh_wipe() -> None:
    """--fresh: delete the whole appliance state after saying exactly what dies.
    Both mode files merged, so every service and volume in the project goes,
    whichever mode was last up."""
    print("  --fresh DELETES the appliance's entire state:")
    print("    account and password, world, knowledge graph, documents, dashboards.")
    print("  Images and the local embedding model survive; nothing else does.")
    answer = input("  Type 'yes' to wipe: ").strip().lower()
    if answer != "yes":
        raise SetupError("Not wiped — nothing was touched.")
    cmd = ["docker", "compose", "-f", MODE_COMPOSE["me"], "-f", MODE_COMPOSE["worlds"],
           "down", "--volumes", "--remove-orphans"]
    subprocess.run(cmd)
    print()


def ensure_mode(mode: str) -> bool:
    """Bring the chosen mode up. Returns True if it was not already running — the
    caller then follows the container log, because the first boot is a designed
    experience (the operator console), not a thing to hide.

    Also the moment the host's timezone reaches .env: it must exist before the
    `up -d` below, and `up -d` then applies it — compose sees the changed
    environment and recreates the mode, so a UTC container heals on re-run.

    ALWAYS runs `compose up -d`, even when the mode is already up. "The
    container is running" does NOT mean "this compose file is applied": a service
    added since the last run (the console was, once) would never be created, and
    the operator sees a service in their YAML with nothing behind it. `up -d`
    is idempotent — it reconciles and leaves running containers alone."""
    ensure_timezone()
    announce_github_token()
    modes = running_modes()
    other = next(((svc, name) for svc, name in modes.items() if svc != MODE_SERVICE[mode]), None)
    if other:
        # Asked for this mode while the other one is up. Offer the precise fix rather
        # than "docker compose down", which is ambiguous (which file?) and heavier
        # than needed — it would take the shared graph and metrics down too.
        other_mode = "me" if other[0] == "assistant" else "worlds"
        print(f"  The {other_mode} mode is running ({other[1]}), and only one mode may run at a time")
        print("  — they share one graph, so two would duplicate every scheduled job.\n")
        answer = input(f"  Stop the {other_mode} mode and continue? [Y/n]: ").strip().lower()
        if answer not in ("", "y", "yes"):
            raise SetupError(
                f"Left the {other_mode} mode running. Stop it when you are ready:\n"
                f"    docker compose -f {MODE_COMPOSE[other_mode]} stop {other[0]}"
            )
        _compose(other_mode, "stop", other[0])
        print()
    running = modes.get(MODE_SERVICE[mode])
    if running:
        print(f"  The {mode} mode is running — reconciling with the compose file.\n")
        _compose(mode, "up", "-d")
        print()
        return False
    print(f"  Starting the {mode} mode — first run pulls images, give it a few minutes.\n")
    run = _compose(mode, "up", "-d")
    if run.returncode != 0:
        raise SetupError(f"docker compose up failed for the {mode} mode.")
    print()
    return True


def reset_credentials(container: str, base: str) -> None:
    """Forgotten password: reopen first-run setup WITHOUT touching the data.

    The operator account lives in two small files under the volume's admin
    directory — the credential hashes and the completed-setup record. The
    appliance refuses to reopen setup over the API by design (410 Gone,
    forever), but whoever controls docker on this host already owns the
    appliance, so host-side recovery is honest: delete those two files, restart
    the container, and the normal wizard runs again. The graph, documents and
    memories live elsewhere on the volume and are untouched."""
    print("  Resetting the operator account: this deletes the username/password")
    print("  and walks first-run setup again. Everything the appliance knows —")
    print("  graph, documents, memories — is kept. Have your model-provider key")
    print("  handy (or exported); the wizard verifies it again.")
    answer = input("  Reset the account and re-run setup? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        raise SetupError("Not reset — nothing was touched.")
    run = _docker("exec", container, "rm", "-f",
                  f"{ADMIN_DIR}/.credentials.yml", f"{ADMIN_DIR}/.setup.json")
    if run is None or run.returncode != 0:
        detail = run.stderr.strip() if run is not None else "docker is not available"
        raise SetupError(f"Could not remove the account files from {container}:\n{detail}")
    print("  Account removed — restarting the appliance to reopen setup…")
    restart = _docker("restart", container, timeout=180)
    if restart is None or restart.returncode != 0:
        raise SetupError(f"docker restart {container} failed.")
    # Hand back to the wizard only once setup is actually answering again;
    # otherwise the token (still in the old log) is found instantly and the
    # first API call lands on a container that is mid-boot.
    deadline = time.monotonic() + BOOT_WAIT_SECONDS
    print("  Waiting for the appliance to come back…\n")
    while True:
        state = probe(base)  # AlreadySetUp here would mean the files came back
        if state == "pending":
            return
        if time.monotonic() >= deadline:
            raise SetupError(
                f"The appliance did not come back within {BOOT_WAIT_SECONDS}s.\n"
                f"Watch it with:  docker logs -f {container}"
            )
        time.sleep(3)


def token_from_logs(container: str) -> str | None:
    run = _docker("logs", container)
    if run is None or run.returncode != 0:
        return None
    # BOTH streams: which one the app logs to is a packaging detail, and it must
    # never decide whether setup works.
    matches = TOKEN_PATTERN.findall(run.stdout + run.stderr)
    # Last match wins: a restarted container logs it again, and the newest is current.
    return matches[-1] if matches else None


def probe(base: str) -> str:
    """'pending' if the appliance is up and waiting for setup, 'unreachable' if not up.
    Raises AlreadySetUp — checked HERE, before any token hunting, so an appliance that
    is simply done says "sign in" instantly instead of sending anyone log-spelunking."""
    try:
        call(base, "", "probe")
        return "pending"
    except AlreadySetUp:
        raise
    except Unreachable:
        return "unreachable"
    except SetupError:
        return "pending"  # 401: up, and wants the real token


def discover_token(base: str, container: str | None, explicit: str | None) -> str:
    """The token is printed to the container log on every boot until setup completes.
    Read it from there so the usual case needs no copy-paste at all; wait out a
    container that is still booting; ask only when there is genuinely no way to know."""
    if explicit:
        return explicit

    deadline = time.monotonic() + BOOT_WAIT_SECONDS
    announced = False
    while True:
        if container:
            token = token_from_logs(container)
            if token:
                print(f"  Found the setup token in the {container} log.\n")
                return token
        state = probe(base)  # raises AlreadySetUp — the friendliest outcome
        if state == "pending":
            # The API answers but the current container log has no token — a remote
            # appliance with no local docker, or a log that lost it. Ask below.
            break
        if container is None or time.monotonic() >= deadline:
            break
        if not announced:
            print(f"  The appliance is still starting — watching the {container} log "
                  f"for its setup token (up to {BOOT_WAIT_SECONDS}s)…")
            announced = True
        time.sleep(3)

    if container is None and state == "unreachable":
        raise SetupError(
            f"No appliance is running: no mode container was found and {base} does not answer.\n"
            "Start one first:  docker compose up -d"
        )
    print("  Could not find the setup token automatically.")
    if container:
        print(f"  It is printed in the container log:  docker logs {container} 2>&1 | grep 'Setup token'")
    token = input("  Setup token: ").strip()
    if not token:
        raise SetupError("A setup token is required.")
    return token


# ── rendering ───────────────────────────────────────────────────────────────

def ask(field: dict) -> str:
    label = field.get("label") or field["name"]
    default = field.get("default")
    options = field.get("options") or []
    required = field.get("required", True)

    if field["type"] == "CHOICE" and options:
        print(f"\n  {label}:")
        for index, option in enumerate(options, 1):
            marker = "  (default)" if option == default else ""
            print(f"    {index}) {option}{marker}")
        while True:
            raw = input(f"  Choose 1-{len(options)}" + (f" [{default}]: " if default else ": ")).strip()
            if not raw and default:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1]
            if raw in options:
                return raw
            print("  Not one of the options.")

    while True:
        if field["type"] == "SECRET":
            # Never echoed, and never lands in shell history.
            value = getpass.getpass(f"  {label}: ")
        else:
            suffix = f" [{default}]: " if default else ": "
            value = input(f"  {label}{suffix}").strip() or (default or "")
        if value or not required:
            return value
        print("  Required.")


def from_environment(step: dict) -> dict:
    """Answers this step can take from the shell rather than from the operator.

    A developer who already has OPENAI_API_KEY exported should not be made to paste it
    back in. This is the one place the client knows anything about a specific step — it
    keys off the server's own field names (`provider`, `apiKey`) and does nothing at all
    for a step that lacks them, so an unrelated step added server-side is unaffected.

    Note this reads the environment of the shell running THIS script, which is not the
    container's. A key set only in `.env` reaches the appliance but not here, and is still
    asked for; that is a server-side question about when the provider step counts as
    satisfied, not something a client can see.
    """
    names = {field["name"] for field in step["fields"]}
    if not {"provider", "apiKey"} <= names:
        return {}

    available = {
        provider: os.environ[var]
        for provider, var in PROVIDER_ENV.items()
        if os.environ.get(var, "").strip()
    }
    if not available:
        return {}
    if len(available) == 1:
        provider = next(iter(available))
    else:
        # Both are set, and which one to connect first is a real choice — ask that much,
        # then still skip the key.
        print("\n  Found keys for both providers in your environment.")
        provider = ask(next(f for f in step["fields"] if f["name"] == "provider"))
        if provider not in available:
            return {}
    return {"provider": provider, "apiKey": available[provider]}


def run_step(base: str, token: str, step: dict, use_environment: bool = True) -> dict:
    print(f"\n── {step['title']} " + "─" * max(0, 60 - len(step["title"])))
    if step.get("description"):
        print(f"   {step['description']}")

    while True:
        # Only on the FIRST attempt. A retry means the server rejected these answers, and
        # re-offering the same environment value would loop forever on a stale key.
        prefilled = from_environment(step) if use_environment else {}
        use_environment = False
        if prefilled:
            print(f"\n  Using {PROVIDER_ENV[prefilled['provider']]} from your environment.")
        answers = {
            field["name"]: prefilled.get(field["name"]) or ask(field)
            for field in step["fields"]
        }
        print("\n  Working…", end=" ", flush=True)
        try:
            result = call(base, f"/{step['id']}", token, answers)
        except (AlreadySetUp, Unreachable, TokenRejected):
            # The appliance or the session is the problem, not the answer — no amount
            # of retyping helps.
            print("\n")
            raise
        except SetupError as e:
            # A REJECTED ANSWER: server-side validation (password rules, malformed key).
            # Retry IN PLACE. Ending the whole wizard — and making someone re-run it and
            # re-answer the steps they already got right — because a password was four
            # characters long is a hostile way to meet a new user.
            print(f"\n  {e}\n  Let's try that step again.")
            continue
        if result.get("ok"):
            print(result.get("detail", "done"))
            models = result.get("models")
            if models:
                # Proof the key really works: these came back from the provider just now.
                shown = ", ".join(models[:6])
                more = f" (+{len(models) - 6} more)" if len(models) > 6 else ""
                print(f"  {len(models)} models available: {shown}{more}")
            return result
        # A rejected answer is worth retrying in place — usually a typo'd key.
        print(f"\n  {result.get('detail', 'That did not work.')}\n  Let's try that step again.")
        if prefilled:
            print("  (that key came from your environment — you'll be asked for one now)")


def wire_coding_agents(result: dict) -> None:
    """Offer to point Claude Code at the appliance, using the token the mcp step just
    minted. The token exists in this process exactly once — the server never returns
    it again — so this is the moment to hand it to a client.

    `claude mcp add` only writes config; the token itself goes live when setup
    completes and the appliance restarts, and the closing message says so."""
    token, url = result.get("token"), result.get("url")
    if not token or not url:
        return

    print("\n── Wire up Claude Code " + "─" * 39)
    claude = shutil.which("claude")
    if claude:
        answer = input("  Point Claude Code at this appliance now (user scope)? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            try:
                run = subprocess.run(
                    [claude, "mcp", "add", "--transport", "http", "--scope", "user",
                     "embabel", url, "--header", f"Authorization: Bearer {token}"],
                    capture_output=True, text=True, timeout=60,
                )
                if run.returncode == 0:
                    print("  Claude Code wired as 'embabel' — new sessions will see the appliance.")
                    return
                print(f"  claude mcp add failed: {(run.stderr or run.stdout).strip()[:200]}")
            except (subprocess.SubprocessError, OSError) as e:
                print(f"  Could not run claude: {e}")
    else:
        print("  Claude Code CLI not found on PATH.")

    # Manual fallback — also what Codex/Cursor users copy from. Printing the token is
    # deliberate: this is the operator's own machine and the only time it is available.
    print("  Wire any MCP client manually:")
    print(f"    URL:    {url}")
    print(f"    Header: Authorization: Bearer {token}")
    print("  (Claude Code: claude mcp add --transport http --scope user embabel "
          f"{url} --header \"Authorization: Bearer <token>\")")


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
    """Write ASSISTANT_BOOTSTRAP_WORLD into .env, preserving everything else there.
    .env rather than a compose edit: the compose files stay pull-only, and this is
    exactly the kind of machine-local setting .env exists for."""
    repo = resolve_world_repo(spec)
    lines = []
    if os.path.exists(".env"):
        with open(".env") as f:
            lines = f.read().splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith("ASSISTANT_BOOTSTRAP_WORLD="):
            lines[index] = f"ASSISTANT_BOOTSTRAP_WORLD={repo}"
            replaced = True
    if not replaced:
        lines += ["", "# World template new worlds are cloned from (set by ./setup.py --world).",
                  f"ASSISTANT_BOOTSTRAP_WORLD={repo}"]
    with open(".env", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  World template: {repo}")
    print("  (applies when a world is FIRST created — existing worlds keep their shape)\n")


# Docker Desktop shares these host prefixes with containers out of the box. A
# bind mount from ANYWHERE else resolves to an EMPTY directory rather than an
# error, which is the worst failure mode this feature has: everything starts,
# nothing is visible, and there is nothing in any log to explain it. So warn at
# the moment the path is chosen, while the operator still knows what they typed.
DOCKER_SHARED_PREFIXES = ("/Users", "/Volumes", "/private", "/tmp", "/var/folders")


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
    """Write EMBABEL_REALMS_DIR into .env, preserving everything else there.
    Same reasoning as the world template: the compose files stay pull-only, and
    where this machine keeps its checkouts is exactly what .env is for."""
    lines = []
    if os.path.exists(".env"):
        with open(".env") as f:
            lines = f.read().splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith("EMBABEL_REALMS_DIR="):
            lines[index] = f"EMBABEL_REALMS_DIR={path}"
            replaced = True
    if not replaced:
        lines += ["", "# Realm checkouts on this machine, mounted read-only at /realms.",
                  "# See realms/README.md; ./worlds.py asked for this on first run.",
                  f"EMBABEL_REALMS_DIR={path}"]
    with open(".env", "w") as f:
        f.write("\n".join(lines) + "\n")


def announce_realms(path: str, realms: list[str], notes: list[str]) -> None:
    print(f"  Realm checkouts: {path}")
    if realms:
        shown = ", ".join(realms[:6]) + (f" and {len(realms) - 6} more" if len(realms) > 6 else "")
        print(f"  {len(realms)} realm{'s' if len(realms) != 1 else ''} visible: {shown}")
    else:
        print("  No realms there yet — clone one in and it is visible on the next start.")
    for note in notes:
        print(f"  ! {note}")
    print("  Load one with a path entry in a world's config/realms.yml:  path: /realms/<dir>\n")


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

    print("\n── Working on realms " + "─" * 41)
    print("  A realm is a git repository of declarative capability. If you are writing")
    print("  one, the appliance can read it straight off this machine — no commit, no")
    print("  push, no waiting for a clone. Give the directory your checkouts live IN,")
    print("  so that adding another is a clone rather than a change here.\n")

    default = os.path.realpath("realms")
    for _ in range(3):
        answer = input(f"  Realm checkouts directory [{default}]: ").strip()
        path, realms, notes = inspect_realms_dir(answer or default)
        if path:
            set_realms_dir(path)
            announce_realms(path, realms, notes)
            return
        for note in notes:
            print(f"  {note}")
        print()
    print("  Skipping — set EMBABEL_REALMS_DIR in .env when you want it.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up the Embabel appliance.")
    parser.add_argument("mode", nargs="?", choices=tuple(MODE_COMPOSE),
                        help="which mode to set up — starts it if nothing is running "
                             "(default: whichever mode is already up)")
    parser.add_argument("--fresh", action="store_true",
                        help="DELETE all appliance state first (asks for confirmation), then start fresh")
    parser.add_argument("--reset-password", action="store_true",
                        help="forgot the password: recreate the operator account "
                             "(asks for confirmation) and keep all data")
    parser.add_argument("--url", default=None,
                        help=f"appliance base URL (default: detected from the running mode, else {DEFAULT_BASE})")
    parser.add_argument("--token", help="setup token (default: read from the container logs)")
    parser.add_argument(
        "--world",
        help="world template NEW worlds start from: a git URL, owner/repo on GitHub, "
             "or a bare name in the embabel org. Written to .env as "
             "ASSISTANT_BOOTSTRAP_WORLD; existing worlds are never reshaped",
    )
    parser.add_argument(
        "--realms",
        help="directory your realm checkouts live IN, mounted read-only at /realms so a "
             "world can load one with `path:` instead of cloning it. Written to .env as "
             "EMBABEL_REALMS_DIR; checked before it is written",
    )
    parser.add_argument(
        "--ignore-env",
        action="store_true",
        help=f"always ask, even if {' or '.join(PROVIDER_ENV.values())} is set",
    )
    args = parser.parse_args()

    print("\n  Embabel appliance — first-run setup")
    print("  " + "─" * 60)

    follower = None
    try:
        # Compose files live next to this script; docker compose needs their directory.
        os.chdir(os.path.dirname(os.path.abspath(__file__)))

        # BEFORE the mode starts: the container reads .env at creation, and the
        # template only matters when a world is first built.
        if args.world:
            set_bootstrap_world(args.world)
        ensure_realms_dir(args.mode or "me", args.realms)

        if args.fresh:
            fresh_wipe()
        started = False
        if args.mode or args.fresh:
            started = ensure_mode(args.mode or "me")

        container = find_mode_container(args.mode)
        base = args.url or (container_base_url(container) if container else None) or DEFAULT_BASE
        if args.reset_password:
            if not container:
                raise SetupError(
                    "No mode is running to reset. Start one first:  ./me.py  or  ./worlds.py"
                )
            reset_credentials(container, base)
        if container:
            print(f"  Setting up {container} at {base}\n")

        if started and container:
            # First boot is a designed surface: stream the operator console while
            # we wait for the setup token, then hand the terminal back to the wizard.
            follower = subprocess.Popen(["docker", "logs", "-f", "--tail", "0", container])

        token = discover_token(base, container, args.token)
        if follower:
            follower.terminate()
            follower = None
            print()
        status = call(base, "", token)

        pending = [step for step in status["steps"] if not step["satisfied"]]
        if not pending:
            print("  Everything is already configured.")
        for step in pending:
            result = run_step(base, token, step, use_environment=not args.ignore_env)
            wire_coding_agents(result or {})

        print("\n  Finishing…", end=" ", flush=True)
        done = call(base, "/complete", token, {})
        print(done.get("detail", "complete"))

        username = done.get("signInAs")
        print(f"\n  Done. Sign in at {base}" + (f" as {username}" if username else ""))
        print("  The appliance is restarting to pick up your provider key — give it a moment.\n")
        service = mode_service(container) if container else None
        if service == "worlds":
            print_worlds_surfaces(base)
        elif service == "assistant":
            launch_me_app(base, username)
        return 0
    except AlreadySetUp as e:
        # Not a failure: the appliance is up and configured. For the Me mode,
        # re-running ./me.py is how people come back — so still end at the app.
        if follower:
            follower.terminate()
        print(f"\n  {e}\n")
        if container and mode_service(container) == "assistant":
            # Already set up, so no wizard ran and no username was minted here —
            # anything the app already has stays untouched.
            launch_me_app(base)
        return 0
    except SetupError as e:
        if follower:
            follower.terminate()
        print(f"\n  {e}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if follower:
            follower.terminate()
        # Half-finished setup is fine: completed steps persist, so re-running resumes.
        print("\n\n  Interrupted. Re-run this script to pick up where you left off.\n", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
