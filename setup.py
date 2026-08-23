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
import base64
import getpass
import http.client
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import threading
import sys
import time
import urllib.error
import urllib.request
import zlib

def default_base() -> str:
    """Where the Me door answers for the instance in play. A function now: the
    port moves with the instance, so a constant could only ever be right once."""
    return f"http://localhost:{ports_for(port_base())['ASSISTANT_PORT']}"


def console_url() -> str:
    """WHERE A PERSON GOES in worlds mode — not the same as where the API is. The
    worlds server also serves the old Vaadin UI, and sending a new operator there
    instead of to the console makes their first impression a surface on its way out."""
    return f"http://localhost:{ports_for(port_base())['WORLDS_CONSOLE_PORT']}"


def surface_urls() -> dict:
    """Every address this instance publishes, by the name a person uses for it."""
    p = ports_for(port_base())
    return {"me": f"http://localhost:{p['ASSISTANT_PORT']}",
            "worlds": f"http://localhost:{p['WORLDS_PORT']}",
            "console": console_url(),
            "graph": f"http://localhost:{p['NEO4J_BROWSER_PORT']}",
            "bolt": f"bolt://localhost:{p['NEO4J_BOLT_PORT']}",
            "dashboards": f"http://localhost:{p['GRAFANA_PORT']}",
            "metrics": f"http://localhost:{p['PROMETHEUS_PORT']}"}
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
# What has to exist before the appliance can answer at all: the graph and the mode
# itself (plus the console, which IS the worlds surface). Together about 0.8GB.
MODE_CORE = {
    "me": ("neo4j", "assistant"),
    "worlds": ("neo4j", "worlds", "worlds-console"),
}
# Everything else, started AFTER the mode is up and reachable. None of it is a
# boot dependency — the mode services depend only on neo4j — and between them
# they are most of a first run's download, docling especially. Pulling them
# before handing over the terminal meant staring at a progress bar for several
# gigabytes to reach a login page that needed one.
DEFERRED_SERVICES = ("sandbox-image", "prometheus", "grafana", "docling")
# What each is for, in the one line the operator sees while it arrives.
DEFERRED_WHY = {
    "docling": "structured PDF and Office conversion",
    "sandbox-image": "the code sandbox",
    "grafana": "dashboards",
    "prometheus": "metrics",
}
# Operator mounts, written by the Me app's "Local files" panel: host folders the
# assistant may index, bind-mounted read-only under /local. Plain `docker compose
# up` merges this file by compose convention, but the explicit -f list used below
# switches that convention OFF, so it must be re-included by hand — for the me
# mode only, because it overrides the `assistant` service, which the worlds file
# does not define (merging it there would fabricate an image-less service).
OVERRIDE_FILE = "docker-compose.override.yml"
# Machine-local configuration: keys, timezone, world template, realms directory.
# Gitignored, and removed by --uninstall — which is the difference between that
# and --fresh, since a .env that survives means the next run asks nothing.
ENV_FILE = ".env"
# The name setup registers with MCP clients, and therefore the name --uninstall
# has to remove. One constant so the two cannot disagree.
MCP_SERVER_NAME = "embabel"
# The env var Codex reads the MCP bearer token from: `codex mcp add` stores the
# variable's NAME in its config, never the token, so the operator exports this.
CODEX_TOKEN_ENV = "EMBABEL_MCP_TOKEN"
# Every code-sandbox container carries this label (JvmInstance.JVM_LABEL_KEY on the
# server). They are created by the app THROUGH the docker socket as siblings of the
# appliance, not as compose services — so `docker compose down` does not see them.
SANDBOX_LABEL = "embabel-jvm"
# The compose project both mode files declare. Anything belonging to the appliance
# carries it as a label — which is the only reliable way to tell the appliance's
# containers from a developer's own stack, whose names start the same way.
# The DEFAULT project name. Everything reads compose_project() instead, which
# resolves the instance in play — this remains only as the name that instance
# `appliance` produces, and as the answer for anything asking before an
# instance has been chosen.
COMPOSE_PROJECT = "embabel-appliance"
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

# The collector is fixed in the appliance image. This is repeated here because setup
# must disclose the destination before it asks the operator to finish installation;
# PHONE_HOME.md and the live endpoints remain the authoritative payload views.
PHONE_HOME_ENDPOINT = "https://telemetry.embabel.com/v1/appliance"
PHONE_HOME_DOC_URL = "https://github.com/embabel-worlds/appliance/blob/main/PHONE_HOME.md"


class SetupError(Exception):
    pass


class AlreadySetUp(SetupError):
    pass


class Unreachable(SetupError):
    pass


class TokenRejected(SetupError):
    """The setup token itself was refused — no answer to any step can fix that."""


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
                "This appliance is already set up.\n"
                f"Worlds: the console at {console_url()}   ·   Me: {base}"
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
    """Running mode containers of THIS instance, keyed by compose service name.

    Both labels, always. The service label alone stopped being an identity the
    moment a second instance could exist — every instance has a container
    labelled service=worlds, and matching on that would have let a backup stop
    somebody else's appliance and copy the wrong graph.
    """
    found = {}
    for service in MODE_SERVICES:
        run = _docker("ps",
                      "--filter", f"label=com.docker.compose.project={compose_project()}",
                      "--filter", f"label=com.docker.compose.service={service}",
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
    print(f"  Console        {surface_urls()['console']}   \u2190 START HERE")
    print("                 The Worlds console: realms, documents, keys, views, chat.")
    print("                 Opens with the commissioning sequence.")
    print()
    print(f"  API            {base}   (the server the console talks to)")
    print(f"  MCP endpoint   {base}/mcp")
    print("                 Authorization: Bearer \u2014 the token this setup just minted,")
    print("                 stored at /data/embabel/assistant/admin/providers.env")
    print(f"  Graph          {surface_urls()['graph']}  (neo4j / NEO4J_PASSWORD, default embabel-assistant)")
    print(f"  Dashboards     {surface_urls()['dashboards']}   \u00b7   Metrics  {surface_urls()['metrics']}")
    print()


def print_me_surfaces(base: str) -> None:
    """Where a Me operator goes next.

    The worlds mode has always ended on a block like this; Me ended on the app
    offer alone, which left the assistant's own web UI, its MCP endpoint and the
    graph undiscoverable from the terminal that had just configured all three.
    """
    print("  \u2500\u2500 Your Me surfaces " + "\u2500" * 41)
    print(f"  Assistant      {base}   \u2190 START HERE")
    print("                 Chat, documents and memories, in the browser.")
    print()
    print(f"  MCP endpoint   {base}/mcp")
    print("                 Authorization: Bearer \u2014 the token this setup just minted,")
    print("                 stored at /data/embabel/assistant/admin/providers.env")
    print(f"  Graph          {surface_urls()['graph']}  (neo4j / NEO4J_PASSWORD, default embabel-assistant)")
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
        answer = prompt("  Start it now? [Y/n]: ").strip().lower()
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
    answer = prompt("  Start it now? [Y/n]: ").strip().lower()
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
    # The port block, computed rather than trusted. The settings file carries it
    # too — this is what makes a hand-edited or half-written file produce a
    # working appliance on the right ports instead of a confusing half-collision.
    for var, value in ports_for(port_base()).items():
        env[var] = str(value)
    env["EMBABEL_INSTANCE"] = instance()
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
    # -p and --env-file are what make a second instance a second instance: the
    # project prefixes every container, volume and network, and the settings file
    # carries that instance's port block. For the default instance both resolve to
    # exactly what compose would have done on its own.
    # --env-file ONLY IF IT EXISTS. Compose treats a named-but-missing env file as
    # an error, and a fresh clone has no .env yet — passing it unconditionally
    # made every compose call fail on exactly the path a new user is on, and
    # again after `uninstall`. Omitting it is also correct: compose then reads
    # `.env` from the project directory by convention, which for the default
    # instance is the same file.
    cmd = ["docker", "compose", "-p", compose_project()]
    if os.path.exists(env_path()):
        cmd += ["--env-file", env_path()]
    cmd += ["-f", MODE_COMPOSE[mode]]
    if mode == "me" and os.path.exists(OVERRIDE_FILE):
        cmd += ["-f", OVERRIDE_FILE]
    cmd += argv
    try:
        return subprocess.run(cmd, capture_output=capture, text=True, env=compose_env())
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        raise SetupError(f"docker compose failed: {e}")


def follow_boot_log(container: str) -> subprocess.Popen | None:
    """Stream the app's OPERATOR CONSOLE during first boot, and nothing else.

    The app prints a designed block — bordered with box rule — carrying the setup
    token and what to do next. Around it a JVM narrates itself: the Spring banner,
    the ASCII art, sixty-odd INFO lines, and a listing of every model the machine
    can see. Piping all of that to a first-time terminal buried the one part
    written for a person, and read as something having gone wrong.

    So: print the bordered block, and any WARN or ERROR, and drop the rest. A boot
    that fails still says so; a boot that works says only what it meant to.
    """
    try:
        proc = subprocess.Popen(
            ["docker", "logs", "-f", "--tail", "0", container],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except (subprocess.SubprocessError, OSError):
        return None  # the log is a nicety; setup does not depend on it

    def pump() -> None:
        inside = False
        for line in proc.stdout:
            line = line.rstrip("\n")
            border = line.strip().startswith("═") and len(line.strip()) > 8
            if border:
                inside = not inside
                print(line)
                continue
            if inside or " WARN " in line or " ERROR " in line:
                print(line)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    return proc


def start_deferred(mode: str) -> subprocess.Popen | None:
    """Pull and start everything that is not needed to answer, behind the user.

    Started detached and NOT waited on: the wizard is the next thing to happen and
    it takes minutes, which is exactly the window this needs. Its output goes
    nowhere on purpose — two progress bars fighting over one terminal is worse
    than no progress bar, and `docker compose ps` tells the truth at any time.

    Nothing here is a boot dependency, so a failure is not fatal: the services are
    reconciled by the `up -d` that every later run performs, and the closing
    message says what was still on its way.
    """
    coming = ", ".join(DEFERRED_WHY[name] for name in DEFERRED_SERVICES if name in DEFERRED_WHY)
    print(f"  Downloading in the background: {coming}.")
    print("  You can start using the appliance now — check on them any time with")
    print(f"    docker compose -f {MODE_COMPOSE[mode]} ps\n")
    cmd = ["docker", "compose", "-f", MODE_COMPOSE[mode]]
    if mode == "me" and os.path.exists(OVERRIDE_FILE):
        cmd += ["-f", OVERRIDE_FILE]
    cmd += ["up", "-d", *DEFERRED_SERVICES]
    try:
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=compose_env())
    except (subprocess.SubprocessError, OSError):
        return None  # not fatal: the next run reconciles


def warn_if_conversion_pending() -> None:
    """Say so if docling has not arrived yet, because the difference is visible.

    Without it a PDF still ingests — the pipeline falls back to flat Tika text —
    but tables come out mangled, figures are lost, and a SCANNED document yields
    almost nothing, since the OCR lives in docling. That degradation is otherwise
    an ERROR in a log nobody has open, and the user's conclusion is that the
    product is bad at PDFs rather than that it is still downloading.
    """
    run = _docker("ps", "--filter", "name=embabel-appliance-docling", "--format", "{{.Names}}")
    if run and run.returncode == 0 and run.stdout.strip():
        return
    print("  Structured document conversion is still downloading (about 2GB).")
    print("  Documents added before it finishes ingest as plain text — tables flattened,")
    print("  and a scanned PDF may yield nothing. Re-add anything important afterwards.\n")


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


def ensure_wallet_key() -> None:
    """Give this appliance a master key for its wallet, once, and keep it.

    The wallet holds what you connect WITH: an OAuth app's client id and secret,
    a realm's API key, the tokens that come back from authorizing. It is always
    encrypted, and the key is `EMBABEL_KEY_SECRET`. Without one the server
    generates a key per start — so everything you typed decrypts to nothing after
    a restart and the appliance reports an EMPTY wallet rather than an error. You
    would re-enter your GitHub credentials every time you restarted, and never be
    told why.

    Written to .env rather than held in the image, because it must outlive both:
    `--fresh` wipes the volumes and this survives, which is what makes a wallet
    written before the wipe still readable after it.

    NEVER REGENERATED. A new key does not unlock what the old one wrote — it makes
    every stored credential undecryptable — so an existing value of any kind wins,
    exactly like TZ above."""
    if os.environ.get("EMBABEL_KEY_SECRET"):
        return  # exported in the shell — compose sees it directly
    if os.path.exists(env_file()):
        with open(env_file()) as f:
            if any(line.strip().startswith("EMBABEL_KEY_SECRET=") for line in f):
                return
    # 32 bytes, base64 — AES-256, the length WalletEncryptionConfiguration validates.
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    with open(env_file(), "a") as f:
        f.write(
            "\n# The key your wallet is encrypted with, generated once by setup.py.\n"
            "# KEEP IT. Changing or losing it does not lock you out of the appliance —\n"
            "# it makes every credential already stored undecryptable, and you re-enter\n"
            "# them. Back it up with anything else you would not want to retype.\n"
            f"EMBABEL_KEY_SECRET={key}\n"
        )
    os.chmod(env_file(), 0o600)
    print("  Generated EMBABEL_KEY_SECRET in .env — your saved credentials now survive a restart.")


def set_env_var(key: str, value: str, why: tuple[str, ...] = ()) -> None:
    """Write `key=value` into .env, preserving everything else there.

    .env rather than a compose edit, always: the compose files stay pull-only, and
    a machine-local answer — a template, a checkout directory, which door you came
    through — is exactly what .env is for. An existing line is replaced where it
    sits, so the file keeps whatever shape its owner gave it; [why] is the comment
    written above a line that is new.
    """
    lines: list[str] = []
    if os.path.exists(env_file()):
        with open(env_file()) as f:
            lines = f.read().splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            break
    else:
        lines += ["", *why, f"{key}={value}"]
    with open(env_file(), "w") as f:
        f.write("\n".join(lines) + "\n")


def configured_mode() -> str | None:
    """Which door this appliance was last set up as, from .env.

    The installer's default is Me and the CLI's is Worlds, so without this a
    `embabel down` followed by `embabel up` quietly started the OTHER product on
    the same graph — a person who installed an assistant got a world runtime back,
    with nothing anywhere saying why.
    """
    value = os.environ.get("EMBABEL_MODE", "").strip()
    if value in MODE_COMPOSE:
        return value
    if os.path.exists(env_file()):
        with open(env_file()) as f:
            for line in f:
                if line.strip().startswith("EMBABEL_MODE="):
                    value = line.split("=", 1)[1].strip()
                    return value if value in MODE_COMPOSE else None
    return None


def remember_mode(mode: str) -> None:
    """Record the door, so the CLI can come back to it rather than to its default."""
    if configured_mode() == mode:
        return
    set_env_var("EMBABEL_MODE", mode, (
        "# The door this appliance was last started as. `embabel up` returns to it",
        "# instead of its own default; starting the other mode rewrites this line.",
    ))


def take_everything_down() -> None:
    """Every service and volume in the project, whichever mode was last up. Both
    mode files merged so nothing is missed — shared by --fresh and --uninstall,
    which differ in what they take with them, not in how they stop.

    Compose's own output is CAPTURED rather than shown. It ends on lines like
    "! Network embabel-appliance_default  Resource is still in use", which is not
    a failure — it is compose declining to break a network that a container of
    yours, from some other project, is attached to. Shown raw as the last thing
    before "Done", it reads as the uninstall having failed. So: run it quietly,
    then report what is actually gone by looking.
    """
    # -p, or this tears down the DEFAULT instance whichever one you asked for —
    # the mode files carry `name: embabel-appliance`, so without it every
    # instance's uninstall would delete the same appliance.
    cmd = ["docker", "compose", "-p", compose_project()]
    if os.path.exists(env_path()):  # missing is normal mid-uninstall; see _compose
        cmd += ["--env-file", env_path()]
    cmd += ["-f", MODE_COMPOSE["me"], "-f", MODE_COMPOSE["worlds"],
            "down", "--volumes", "--remove-orphans"]
    run = subprocess.run(cmd, capture_output=True, text=True)

    # BY PROJECT LABEL, not by name. Matching "embabel-" caught embabel-assistant-neo4j
    # and embabel-assistant-docling — a developer's own stack from the assistant repo,
    # a different compose project entirely — and reported somebody else's healthy
    # containers as an uninstall that had failed.
    left = _docker("ps", "-a", "--filter", f"label=com.docker.compose.project={compose_project()}",
                   "--format", "{{.Names}}")
    remaining = [n.strip() for n in (left.stdout.splitlines() if left and left.returncode == 0 else [])
                 if n.strip()]
    if remaining:
        print(f"  ! {len(remaining)} container(s) would not stop: {', '.join(remaining)}")
        print(f"    {(run.stderr or run.stdout).strip().splitlines()[-1] if (run.stderr or run.stdout).strip() else ''}")
    else:
        print("  Containers and volumes removed.")

    # The network is the one thing a foreign container can hold open. Name the
    # holder: "resource is still in use" is a fact about somebody's OTHER project,
    # and without the name it reads as this uninstall having left a mess.
    net = _docker("network", "inspect", f"{compose_project()}_default",
                  "--format", "{{range .Containers}}{{.Name}} {{end}}")
    if net and net.returncode == 0 and net.stdout.strip():
        holders = net.stdout.split()
        print(f"  Kept the network: {', '.join(holders)} is attached to it.")
        print("    Not ours to disconnect — it belongs to another compose project.")


def fresh_wipe() -> None:
    """--fresh: delete the whole appliance state after saying exactly what dies."""
    print("  --fresh DELETES the appliance's entire state:")
    print("    account and password, world, knowledge graph, documents, dashboards.")
    print("  Images and the local embedding model survive; nothing else does.")
    answer = prompt("  Type 'yes' to wipe: ").strip().lower()
    if answer != "yes":
        raise SetupError("Not wiped — nothing was touched.")
    take_everything_down()
    print()


# ── instances ───────────────────────────────────────────────────────────────
#
# ONE APPLIANCE IS THE NORMAL CASE, and nothing about this section should be
# visible to somebody who has one. There is no --instance to learn, no name to
# invent, no flag in `embabel --help`: the default instance is called
# `appliance`, its compose project is `embabel-appliance`, its settings are
# `.env`, and that is the whole story until somebody installs a second one.
#
# A SECOND ONE is what turns the machinery on. Instances differ in exactly three
# things, and everything else follows from them:
#
#   the PROJECT     embabel-<instance> — compose prefixes every volume, network
#                   and container with it, so two instances share nothing by
#                   accident. This is also why no service may declare
#                   container_name: a fixed name is global to the docker daemon
#                   and would collide on the second install.
#   the ENV FILE    .env for the default, .env.<instance> beside it. One
#                   checkout, several settings files — rather than several
#                   checkouts, each with its own copy of this script free to
#                   drift from the others.
#   the PORT BASE   sixteen consecutive host ports, EMBABEL_PORT_BASE + offset.
#                   Allocated when the instance is created, never guessed later.
#
# WHY 11042 (see PORTS below). Babel is Genesis 11. 42 is the Answer, and the
# Babel fish is out of the same book — so the number says the product's name
# rather than punning on one syllable of it. It is unassigned by IANA, absent
# from /etc/services, and sits below the Linux ephemeral floor of 32768, which
# 0xBABE (47806) does not — that one would have failed intermittently forever.

DEFAULT_INSTANCE = "appliance"
# 4242 was inherited and contended: OpenTSDB, Quassel and CrashPlan all default
# there, and CrashPlan takes 4242 AND 4243 — the assistant and the Neo4j browser.
DEFAULT_PORT_BASE = 11042
PORT_BLOCK = 16
# Offset within the block -> the variable the compose files read. Adding a
# service means taking the next free offset here; the spare tail is why there
# are sixteen and not eight.
PORT_OFFSETS = {
    "ASSISTANT_PORT": 0,        # the Me front door
    "WORLDS_PORT": 1,           # the worlds API
    "WORLDS_CONSOLE_PORT": 2,   # the Worlds front door
    "NEO4J_BROWSER_PORT": 3,
    "NEO4J_BOLT_PORT": 4,
    "GRAFANA_PORT": 5,
    "PROMETHEUS_PORT": 6,
    "OPEN_WEBUI_PORT": 7,
}
# Instance 24 would land on 11434, which is Ollama's — and the compose files
# talk to Ollama there. A ceiling nobody will reach, stated so it cannot be hit
# by surprise.
MAX_INSTANCES = 23

# Which instance THIS process is talking to. Set once, by the CLI, before any
# verb runs. A module-level value rather than a parameter on forty functions
# because it models something true: one process, one appliance.
_instance = DEFAULT_INSTANCE


# What compose accepts as a project name: lowercase, starting alphanumeric, then
# letters, digits, underscore, hyphen. The name also becomes a FILENAME
# (.env.<name>), so the same rule keeps `--instance ../../etc/passwd` from being
# a path and `--instance "my world"` from being two shell words.
INSTANCE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def use_instance(name: str) -> None:
    """Choose the instance for this process, refusing a name that cannot be one.

    Validated HERE rather than at the flag, because setup.py is also entered
    directly and through the environment — three doors, one gate.
    """
    global _instance
    name = (name or DEFAULT_INSTANCE).strip()
    if not INSTANCE_NAME.match(name):
        raise SetupError(
            f"'{name}' cannot be an instance name. Use lowercase letters, digits, "
            "'-' and '_', starting with a letter or digit — it becomes a docker "
            "project name and a settings filename."
        )
    if len(name) > 40:
        raise SetupError(f"'{name}' is too long for an instance name (40 characters).")
    _instance = name


def instance() -> str:
    return _instance


def compose_project(name: str | None = None) -> str:
    """The compose project, which is the real identity of an instance."""
    return f"embabel-{name or _instance}"


def env_file(name: str | None = None) -> str:
    """Settings for an instance. The default's is plain `.env`, because the
    common case must not be made to look like a special case."""
    name = name or _instance
    return ENV_FILE if name == DEFAULT_INSTANCE else f"{ENV_FILE}.{name}"


def env_path(name: str | None = None) -> str:
    return os.path.join(APPLIANCE_DIR, env_file(name))


def port_base(name: str | None = None) -> int:
    value = env_file_value("EMBABEL_PORT_BASE", name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_PORT_BASE


def ports_for(base: int) -> dict:
    return {var: base + offset for var, offset in PORT_OFFSETS.items()}


def installed_instances() -> list[str]:
    """Every instance this machine knows about — from its settings file, and
    from docker, because an instance whose .env was deleted still has volumes
    and still answers, and hiding it would be the unhelpful kind of tidy."""
    found = set()
    for entry in os.listdir(APPLIANCE_DIR):
        if entry == ENV_FILE:
            found.add(DEFAULT_INSTANCE)
        elif entry.startswith(f"{ENV_FILE}.") and not entry.endswith((".example", ".before-restore")):
            found.add(entry[len(ENV_FILE) + 1:])
    run = _docker("ps", "-a", "--format", "{{.Label \"com.docker.compose.project\"}}", timeout=20)
    if run and run.returncode == 0:
        for line in run.stdout.split():
            if line.startswith("embabel-"):
                found.add(line[len("embabel-"):])
    return sorted(found)


def ensure_port_block() -> int:
    """Give this instance a port block if it does not have one yet.

    Written at CREATION and never recomputed, because a base that is derived
    fresh each run is a base that moves when a sibling instance is removed —
    every bookmark, every registered MCP URL and every `.env` in a realm
    checkout would silently start pointing at a different appliance.
    """
    existing = env_file_value("EMBABEL_PORT_BASE")
    if existing:
        return int(existing)
    # The default instance is the ANCHOR and always owns the first block. Handing
    # it one off the free list let a sibling push the primary appliance off the
    # port every bookmark, MCP registration and realm checkout already names.
    base = DEFAULT_PORT_BASE if instance() == DEFAULT_INSTANCE else free_port_base()
    set_env_var("EMBABEL_PORT_BASE", str(base), (
        "# The sixteen host ports this appliance owns, allocated when it was",
        "# created. Everything else is this + a fixed offset. Do not edit it to",
        "# move one service — set that service's own PORT variable instead.",
    ))
    if instance() != DEFAULT_INSTANCE or base != DEFAULT_PORT_BASE:
        print(f"  Instance '{instance()}' uses ports {base}-{base + PORT_BLOCK - 1}.")
    return base


def free_port_base() -> int:
    """The next unused block. Bases in use are read from the instances that
    exist rather than counted, so removing the second of three instances frees
    its block instead of stranding it."""
    used = {port_base(name) for name in installed_instances() if name != instance()}
    used.add(DEFAULT_PORT_BASE)  # reserved for the default instance, always
    for n in range(MAX_INSTANCES + 1):
        candidate = DEFAULT_PORT_BASE + n * PORT_BLOCK
        if candidate not in used:
            return candidate
    raise SetupError(
        f"All {MAX_INSTANCES + 1} port blocks are in use. The next one would collide "
        f"with Ollama on 11434, which this appliance talks to."
    )


# ── upgrade ─────────────────────────────────────────────────────────────────
#
# TWO THINGS MOVE, and for a long time the CLI moved only one. The images are
# most of the appliance, but the CHECKOUT is the rest of it — the compose files,
# the Neo4j tag they pin, this script, the skills. An upgrade that pulled images
# and left the checkout behind ran new servers against old plumbing, and the Me
# app's menu (which did pull the checkout) and `embabel upgrade` meant different
# things by the same word.
#
# ONTO THE LATEST BUILD, never a local one. Nothing here builds an image: the
# compose files are pull-only by design — see the note at the top of
# docker-compose.yml — and this verb's whole job is to land on what the registry
# publishes. A locally-built image being replaced is therefore the CORRECT
# outcome, but it is still surprising, so it is reported rather than silent.
#
# --ff-only, ALWAYS. A dirty or diverged checkout is left exactly as it is and
# the images still update. An upgrade command that rebases somebody's work, or
# that refuses to do the other half because of it, is worse than one that says
# what it skipped.


def _head() -> str | None:
    run = subprocess.run(["git", "-C", APPLIANCE_DIR, "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    return run.stdout.strip() if run.returncode == 0 else None


def pull_checkout() -> tuple[bool, str]:
    """Fast-forward the checkout. Returns (moved, what to tell the operator)."""
    if not os.path.isdir(os.path.join(APPLIANCE_DIR, ".git")):
        return False, "not a git checkout — images only"
    before = _head()
    run = subprocess.run(["git", "-C", APPLIANCE_DIR, "pull", "--ff-only"],
                         capture_output=True, text=True, timeout=180)
    if run.returncode != 0:
        # The two ordinary reasons, named: local edits, or no upstream. Either
        # way this is a note, not a failure — the images below still move.
        reason = (run.stderr or run.stdout).strip().splitlines()
        return False, f"checkout NOT updated ({reason[-1] if reason else 'git declined'})"
    after = _head()
    if before and after and before != after:
        changed = subprocess.run(["git", "-C", APPLIANCE_DIR, "diff", "--name-only", f"{before}..{after}"],
                                 capture_output=True, text=True)
        files = changed.stdout.split() if changed.returncode == 0 else []
        note = f"checkout updated {before[:7]} → {after[:7]} ({len(files)} file(s))"
        if any(f.startswith(f"{ME_APP_DIR}/") for f in files):
            # dist/ is built from these, and nothing in the run path rebuilds it.
            note += "\n    the Me app changed — rebuild it: npm --prefix me-app run build"
        return True, note
    return False, "checkout already current"


def upgrade(mode: str) -> dict:
    """Latest checkout, latest published images, containers actually running them.

    The digests are read on both sides rather than trusting `up -d` to have done
    something: "pulled" and "the container is now on it" are different claims,
    and only the second one is the thing anybody wanted.
    """
    notes = []
    _moved, note = pull_checkout()
    notes.append(note)

    # Read BEFORE the pull, because the pull is what moves the tag underneath it.
    before = image_identity(mode_image(mode) or "")

    if _compose(mode, "pull").returncode != 0:
        raise SetupError("docker compose pull failed — see the output above.")
    if _compose(mode, "up", "-d").returncode != 0:
        raise SetupError("docker compose up failed — see the output above.")

    after = image_identity(mode_image(mode) or "")
    if before.get("digest") and after.get("digest") and before["digest"] != after["digest"]:
        notes.append(f"server image {before['digest'][7:19]} → {after['digest'][7:19]}")
        # A locally-built image can be NEWER than what the registry serves,
        # because snapshot tags publish on release rather than on every push.
        # Landing on the published build is exactly what this verb is for, so
        # this is a NOTE and not a refusal — but a local build vanishing without
        # a word is the kind of surprise people spend an afternoon on.
        if before.get("created") and after.get("created") and after["created"] < before["created"]:
            notes.append("NOTE: the published image is OLDER than the one that was running.\n"
                         "    If that was your own build, this replaced it — which is what\n"
                         "    `upgrade` means. Rebuild it if you wanted it back.")
        else:
            notes.append("give the server a moment to come back")
    elif after.get("digest"):
        notes.append("server image already current")

    # `up -d` recreates a container whose image changed, but "pulled" and "the
    # container is now running it" are different claims and only the second is
    # what anybody wanted. So check, rather than assume.
    container = find_mode_container(mode)
    if container and not _same_image(container, mode_image(mode)):
        notes.append(f"WARNING: {container} is NOT running the pulled image.\n"
                     "    `embabel down` then `embabel up` will land it.")
    return {"mode": mode, "notes": notes, "digest": after.get("digest")}


def _same_image(container: str, image: str | None) -> bool:
    """Is this container running THAT image, by local image id rather than by tag?
    A tag is a moving name; two containers can both say `:0.2.0-SNAPSHOT` and be
    different builds, which is the whole failure this check exists to catch."""
    if not image:
        return True
    running = _docker("inspect", container, "--format", "{{.Image}}", timeout=15)
    wanted = _docker("image", "inspect", image, "--format", "{{.Id}}", timeout=15)
    if not running or not wanted or running.returncode != 0 or wanted.returncode != 0:
        return True  # cannot tell; do not cry wolf
    return running.stdout.strip() == wanted.stdout.strip()


# ── what is on the host, and getting rid of what should not be ──────────────

def appliance_containers() -> list[dict]:
    """Every container belonging to this compose project, running or not.

    BY PROJECT LABEL, never by name prefix. A developer's own stack from the
    assistant repo is called embabel-assistant-something too, and reporting
    their containers as the appliance's is how `uninstall` once claimed to have
    failed — see take_everything_down for the same lesson learned the same way.
    """
    run = _docker("ps", "-a", "--filter", f"label=com.docker.compose.project={compose_project()}",
                  "--format", "{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Image}}", timeout=20)
    if not run or run.returncode != 0:
        return []
    found = []
    for line in run.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            found.append(dict(zip(("name", "state", "status", "image"), (p.strip() for p in parts))))
    return sorted(found, key=lambda c: c["name"])


def prune_sandboxes(names: list[str]) -> int:
    """Remove the named sandbox containers. The CALLER decides which and asks —
    see remove_stray_sandboxes for why this must never guess: a developer running
    an assistant from an IDE has sandboxes carrying the same label, and killing
    those mid-session is the exact bug the per-jvm scoping exists to prevent."""
    if not names:
        return 0
    run = _docker("rm", "-f", *names, timeout=120)
    return len(names) if run and run.returncode == 0 else 0


# ── bug report ──────────────────────────────────────────────────────────────
#
# One folder somebody can attach to an issue, instead of six rounds of "and can
# you also send…". The contents are chosen by what has actually been asked for
# in those rounds: which images, which commit, what docker says, what the
# service said before it stopped saying anything.
#
# WHAT IT MUST NOT CONTAIN. This appliance holds somebody's email, contacts and
# documents, so a diagnostic bundle is a data-exfiltration shape if it is
# careless. Two rules, both enforced here rather than left to a warning:
#
#   - .env values are NEVER copied. The KEYS are, because "is OPENAI_API_KEY
#     set" is a real diagnostic question and "what is it" never is.
#   - Logs are filtered to WARN, ERROR and stack traces by DEFAULT. An INFO
#     line in this server can carry a document title, a contact's name, or the
#     text of a query somebody typed. The full log is available behind a flag
#     that says what it is, so including it is a decision somebody made.
#
# The bundle is left as a FOLDER and a zip beside it, so it can be read before
# it is sent. A bundle you cannot inspect is one people send blind or not at all.

BUGREPORT_LOG_LINES = 2000
# Lines worth keeping from a JVM log without keeping the JVM log. Anchored to
# the level field Spring writes, plus the shapes a stack trace takes.
LOG_INTERESTING = re.compile(
    r"\b(WARN|ERROR|FATAL|SEVERE)\b|^\s+at\s+[\w$.]+\(|^(Caused by|Suppressed):|Exception|Error:")


def _redacted_env() -> str:
    """Which settings exist and whether they have a value — never the value.

    A key with an empty value and a key that is absent are DIFFERENT bugs, and
    the whole point of this file is telling them apart, so both are reported.
    """
    path = env_path()
    if not os.path.exists(path):
        return f"# no {env_file()} — this appliance has not been set up here\n"
    lines = [f"# {env_file()}, VALUES REMOVED. Key, then whether it holds anything.\n"]
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            lines.append(f"{key.strip()} = {f'set ({len(value)} chars)' if value else 'EMPTY'}\n")
    if len(lines) == 1:
        lines.append("# (the file exists but holds no settings)\n")
    return "".join(lines)


def _container_log(name: str, everything: bool) -> str:
    run = _docker("logs", "--tail", str(BUGREPORT_LOG_LINES), name, timeout=60)
    if not run:
        return "(could not read this container's log)\n"
    text = (run.stdout or "") + (run.stderr or "")
    if everything:
        return text
    kept = [line for line in text.splitlines() if LOG_INTERESTING.search(line)]
    header = (f"# FILTERED to warnings, errors and stack traces — {len(kept)} of "
              f"{len(text.splitlines())} lines from the last {BUGREPORT_LOG_LINES}.\n"
              f"# An INFO line here can carry a document title or somebody's name, so the\n"
              f"# full log is only included with `embabel bugreport --all-logs`.\n\n")
    return header + "\n".join(kept) + "\n"


def bug_report(dest_dir: str, extra: dict, everything: bool = False) -> str:
    """Collect a diagnostic bundle into a new timestamped folder, and zip it.

    `extra` is text the CALLER already has — the CLI's own doctor and status
    output. Re-deriving those here would be a second implementation of both,
    free to disagree with what the operator was just shown on screen.
    """
    dest = os.path.join(os.path.abspath(os.path.expanduser(dest_dir)),
                        f"embabel-bugreport-{instance()}-{backup_timestamp()}")
    os.makedirs(dest, exist_ok=True)

    def write(name: str, text: str) -> None:
        with open(os.path.join(dest, name), "w") as f:
            f.write(text)

    for name, text in extra.items():
        write(name, text)

    write("versions.json", json.dumps(appliance_versions(), indent=2) + "\n")
    write("env-keys.txt", _redacted_env())

    containers = appliance_containers()
    write("containers.txt", "".join(
        f"{c['name']:<38} {c['state']:<10} {c['status']:<28} {c['image']}\n" for c in containers)
        or "(no containers belonging to this appliance)\n")

    write("sandboxes.txt", "".join(f"{n}\n" for n in stray_sandbox_containers(mine_only=False))
          or "(none)\n")

    for section, argv in (("docker-info.txt", ("info",)),
                          ("docker-disk.txt", ("system", "df", "-v")),
                          ("docker-model.txt", ("model", "status"))):
        run = _docker(*argv, timeout=60)
        write(section, (run.stdout + run.stderr) if run else "(command failed)\n")

    os.makedirs(os.path.join(dest, "logs"), exist_ok=True)
    for container in containers:
        write(os.path.join("logs", f"{container['name']}.log"),
              _container_log(container["name"], everything))

    write("README.txt",
          "Embabel appliance bug report — " + time.strftime("%c") + "\n\n"
          "Attach the .zip beside this folder to your issue. Read it first if you\n"
          "like — that is why it is left unpacked.\n\n"
          "WHAT IS NOT HERE: no .env VALUES (env-keys.txt lists the keys and whether\n"
          "each holds anything, never what), no documents, no graph contents.\n\n"
          + ("LOGS ARE COMPLETE in this bundle — it was taken with --all-logs. An INFO\n"
             "line can carry a document title, a contact's name, or a query somebody\n"
             "typed. Read logs/ before sending this to anyone.\n"
             if everything else
             "LOGS ARE FILTERED to warnings, errors and stack traces. If a maintainer\n"
             "needs more, `embabel bugreport --all-logs` includes everything — read it\n"
             "before sending, because INFO lines can carry personal data.\n"))

    archive = shutil.make_archive(dest, "zip", root_dir=dest)
    return archive


# ── what is actually running ────────────────────────────────────────────────
#
# FOUR LAYERS DIFFER, and only one of them is the thing people say out loud.
# EMBABEL_VERSION defaults to a SNAPSHOT tag, which is a name rather than an
# identity — two machines both "on 0.2.0-SNAPSHOT" can be weeks apart. What
# pins an install is the image DIGEST, and what pins the code inside it is the
# commit the jar was built from.
#
# NOT AN HTTP CALL, deliberately. The server does answer this — /actuator/info
# carries the same build and git blocks — but it is authenticated, and more to
# the point the moment somebody needs the version is the moment the appliance
# will not boot, is wedged, or is halfway through an upgrade. An endpoint
# answers none of those. Everything below reads the image and the jar, and
# works with the container stopped.
#
# READING THE JAR CHEAPLY. The appliance jar is ~400MB and the container has no
# unzip, no python and a JRE with no `jar` tool. So: read the zip's central
# directory off the END of the file, find the one entry's offset, and `dd` out
# its couple of hundred bytes. Three small reads instead of copying 400MB to
# learn six lines.

# Where the git and build metadata live inside the Spring Boot jar. The Maven
# build bakes both in (git-commit-id-maven-plugin, spring-boot-maven-plugin).
JAR_PATH = "/app/assistant.jar"
JAR_GIT_ENTRY = "BOOT-INF/classes/git.properties"
JAR_BUILD_ENTRY = "META-INF/build-info.properties"


def _run_in(target: str, is_container: bool, *args: str, binary: bool = False):
    """A command against a running container if there is one, else against the
    image itself. A stopped appliance still has an image, and `version` has to
    answer for a stopped appliance — that is most of why anyone asks."""
    if is_container:
        argv = ["docker", "exec", target, *args]
    else:
        argv = ["docker", "run", "--rm", "--entrypoint", args[0], target, *args[1:]]
    try:
        return subprocess.run(argv, capture_output=True, timeout=60,
                              text=not binary)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def _jar_entry(target: str, is_container: bool, entry: str) -> str | None:
    """One file out of the jar, without moving the jar.

    Zip stores its index at the END, so the tail gives every entry's offset;
    then a single seek reads that entry's bytes. Zip64 because the jar is well
    past 4GB worth of entries' worth of offsets on some builds — the classic
    end-of-central-directory records 0xFFFFFFFF and defers to the Zip64 one.
    """
    tail = _run_in(target, is_container, "sh", "-c", f"tail -c 70000 {JAR_PATH}", binary=True)
    if not tail or tail.returncode != 0:
        return None
    data = tail.stdout
    marker = data.rfind(b"PK\x05\x06")
    if marker < 0:
        return None
    try:
        _size, offset = struct.unpack("<II", data[marker + 12:marker + 20])
        if offset == 0xFFFFFFFF:
            zip64 = data.rfind(b"PK\x06\x06")
            _size, offset = struct.unpack("<QQ", data[zip64 + 40:zip64 + 56])

        block = 65536
        got = _run_in(target, is_container, "dd", f"if={JAR_PATH}", f"bs={block}",
                      f"skip={offset // block}", "status=none", binary=True)
        if not got or got.returncode != 0:
            return None
        directory = got.stdout[offset % block:]
        at = directory.find(entry.encode())
        if at < 0:
            return None
        header = directory[at - 46:at]
        method = struct.unpack("<H", header[10:12])[0]
        compressed = struct.unpack("<I", header[20:24])[0]
        local = struct.unpack("<I", header[42:46])[0]

        block = 4096
        got = _run_in(target, is_container, "dd", f"if={JAR_PATH}", f"bs={block}",
                      f"skip={local // block}", "count=8", "status=none", binary=True)
        if not got or got.returncode != 0:
            return None
        raw = got.stdout[local % block:]
        name_len, extra_len = struct.unpack("<HH", raw[26:30])
        start = 30 + name_len + extra_len
        body = raw[start:start + compressed]
        # 8 is DEFLATE, 0 is STORED; a raw stream, so a negative window size.
        text = zlib.decompress(body, -15) if method == 8 else body
        return text.decode("utf8", "replace")
    except (struct.error, zlib.error, IndexError):
        return None


def parse_properties(text: str | None) -> dict:
    """A .properties file, enough for the two the jar carries. Not a general
    parser: these are machine-generated, and the only escaping in them is the
    plugin's backslash before ':' in timestamps and commit messages."""
    found = {}
    for line in (text or "").splitlines():
        if not line.strip() or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        found[key.strip()] = value.replace("\\:", ":").replace("\\=", "=").strip()
    return found


def image_identity(image: str) -> dict:
    """The tag as written, and the digest that tag currently means.

    The DIGEST is the answer to "which build is this" — the tag moves, and for
    an unpinned SNAPSHOT it moves often. `created` is the image's build time,
    which is how you tell a pull from last night from one from March.
    """
    run = _docker("image", "inspect", image, "--format",
                  "{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}\t{{.Created}}", timeout=20)
    if not run or run.returncode != 0:
        return {"image": image, "digest": None, "created": None}
    digest, _, created = run.stdout.strip().partition("\t")
    return {"image": image, "digest": digest.partition("@")[2] or None, "created": created or None}


def source_identity(mode: str) -> dict:
    """The commit the running appliance was BUILT from, read out of its jar.

    Prefers the live container — `docker exec` costs nothing when one is up.
    Falls back to the image, which starts a throwaway container for two reads.
    """
    container = find_mode_container(mode)
    target, is_container = (container, True) if container else (mode_image(mode), False)
    if not target:
        return {}
    git = parse_properties(_jar_entry(target, is_container, JAR_GIT_ENTRY))
    build = parse_properties(_jar_entry(target, is_container, JAR_BUILD_ENTRY))
    return {
        # `git.commit.id` is the full SHA; older builds carry only the abbrev,
        # because the Maven plugin was filtering the full one out by a name it
        # does not emit. Both are reported so a backup taken before that fix is
        # still readable rather than blank.
        "commit": git.get("git.commit.id") or git.get("git.commit.id.abbrev"),
        "abbrev": git.get("git.commit.id.abbrev"),
        "branch": git.get("git.branch"),
        "subject": git.get("git.commit.message.short"),
        "committed": git.get("git.commit.time"),
        # A build cut from a working tree with uncommitted changes. The commit
        # above then names where the build STARTED, not what is in it — which
        # is worth saying out loud rather than leaving to be discovered.
        "dirty": git.get("git.dirty") == "true",
        "version": build.get("build.version"),
        "built": build.get("build.time"),
    }


def mode_image(mode: str) -> str | None:
    """The image THIS mode's service runs.

    A running container is asked directly; otherwise compose resolves it, with
    its ${EMBABEL_VERSION:-...} defaults applied. The service is looked up BY
    KEY in the rendered config rather than by matching image lines — `--images`
    prints every service's image, and the worlds service and the docling image
    (ghcr.io/embabel-worlds/...) share enough of a substring that a text match
    reports the wrong one.
    """
    container = find_mode_container(mode)
    if container:
        run = _docker("inspect", container, "--format", "{{.Config.Image}}", timeout=15)
        if run and run.returncode == 0 and run.stdout.strip():
            return run.stdout.strip()
    run = _compose(mode, "config", "--format", "json", capture=True)
    if run is None or run.returncode != 0:
        return None
    try:
        return json.loads(run.stdout)["services"][MODE_SERVICE[mode]]["image"]
    except (ValueError, KeyError, TypeError):
        return None


def checkout_identity() -> dict:
    """This repo — the pin for everything that is NOT in an image: the compose
    files, the Neo4j tag they name, setup.py, the skills."""
    def git(*args: str) -> str | None:
        run = subprocess.run(["git", "-C", APPLIANCE_DIR, *args], capture_output=True, text=True)
        return run.stdout.strip() if run.returncode == 0 else None

    dirty = git("status", "--porcelain")
    return {
        "commit": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(dirty),
    }


def appliance_versions(mode: str | None = None) -> dict:
    """Every layer that can differ between two installs, in one dict. Shared by
    `embabel version` and the backup manifest, so a backup records exactly what
    `version` would have printed on the day it was taken."""
    mode = mode or backup_mode()
    image = mode_image(mode)
    return {
        "mode": mode,
        "checkout": checkout_identity(),
        "appliance": image_identity(image) if image else {},
        "source": source_identity(mode),
        "neo4j": image_identity(neo4j_image() or ""),
    }


def neo4j_image() -> str | None:
    """Pinned in the tracked compose files rather than in .env, so the CHECKOUT
    is its version — but read it rather than restating it here."""
    container = "embabel-appliance-neo4j"
    run = _docker("inspect", container, "--format", "{{.Config.Image}}", timeout=15)
    if run and run.returncode == 0 and run.stdout.strip():
        return run.stdout.strip()
    with open(os.path.join(APPLIANCE_DIR, "infra.yml")) as f:
        for line in f:
            if line.strip().startswith("image: neo4j:"):
                return line.split("image:", 1)[1].strip()
    return None


# ── backup and restore ──────────────────────────────────────────────────────
#
# WHAT A BACKUP IS. Everything a person would grieve over lives in two named
# volumes — embabel_assistant_data (worlds, documents, artifacts, credentials)
# and embabel_appliance_neo4j_data (the knowledge graph) — plus the host-side
# files that make this checkout THIS appliance. A backup is a plain folder
# holding a cold tarball of each volume, those files, and a manifest saying
# what made it. A folder rather than one enveloping archive on purpose: the
# tarballs inside are already compressed, and re-archiving gigabytes buys a
# second wait and nothing else.
#
# COLD ON PURPOSE. Community Neo4j has no online backup. So whichever mode is
# running is stopped, the volumes are copied at rest, and the SAME mode is
# brought back up — a copy taken under a live graph is corrupt in exactly the
# cases that make somebody reach for a backup.
#
# The bytes never cross a bind mount: a helper container tars the volume to
# stdout and this process streams that to a file (and back, on restore). So
# Docker Desktop's file-sharing list never has an opinion about where backups
# may live, and a backup can be written to an external disk.
#
# THIS IS HOST WORK, not server work — a container cannot copy the volume it is
# running from. It lives here, beside the other lifecycle verbs, because the Me
# app's menu and `embabel backup` must not be two implementations that disagree.

# The keys as the compose files declare them; the REAL names carry the project
# prefix, which is pinned in docker-compose.yml precisely so they cannot move
# out from under an install.
BACKUP_VOLUMES = (
    ("embabel_assistant_data", "assistant-data.tgz", "worlds and documents"),
    ("embabel_appliance_neo4j_data", "neo4j-data.tgz", "the knowledge graph"),
)
# Host-side state that shapes the appliance. Without .env a restored graph is
# unreachable — Neo4j's password lives IN its volume and the appliance's copy of
# it lives here. secrets.env holds the realm API credentials the compose files
# load by `env_file:`; a restore without it comes back with every authenticating
# realm silently dead, which is a worse outcome than a refusal.
def backup_config_files() -> tuple[str, ...]:
    """A function, not a constant: which settings file belongs to this appliance
    depends on which instance it is."""
    return (env_file(), OVERRIDE_FILE, "secrets.env")
# The Me app writes the override and stamps it (mounts.ts). A file WITHOUT the
# stamp was written by a person, and a restore does not eat their work.
OVERRIDE_MARKER = "# Written by Embabel Me"
# Has tar, weighs a few MB, and is pinned so a backup taken next year is cut by
# the same tool as one taken today.
BACKUP_HELPER_IMAGE = "alpine:3.22"
BACKUP_MANIFEST = "manifest.json"
# Copying a graph can legitimately take a long time. This is a backstop against
# a wedged docker CLI, not a pace expectation.
BACKUP_STREAM_TIMEOUT = 30 * 60
# Where backups go when nobody says. Under $HOME, not the checkout: --uninstall
# removes the checkout, and a backup that an uninstall deletes is not a backup.
DEFAULT_BACKUP_DIR = os.path.expanduser("~/embabel-backups")
# Every path here is absolute rather than relying on the chdir that main() does,
# because the Me app will call these through the CLI from its own directory.
APPLIANCE_DIR = os.path.dirname(os.path.abspath(__file__))


def volume_name(key: str) -> str:
    """The real Docker volume name — compose prefixes every volume with the project."""
    return f"{compose_project()}_{key}"


def volume_exists(key: str) -> bool:
    run = _docker("volume", "inspect", volume_name(key), timeout=15)
    return run is not None and run.returncode == 0


def require_docker() -> None:
    """The volumes are only reachable through the daemon, so say that rather than
    letting each of the calls below fail separately with its own wording."""
    run = _docker("info", timeout=20)
    if run is None or run.returncode != 0:
        raise SetupError("Docker is not running — the appliance's volumes are only reachable through it.")


def running_mode_names() -> list[str]:
    """The modes that are up, as mode names rather than compose service names."""
    by_service = {service: mode for mode, service in MODE_SERVICE.items()}
    return [by_service[service] for service in running_modes() if service in by_service]


def backup_mode() -> str:
    """Which mode's compose file to stop, start and create volumes with.

    Whatever is running, else whatever was set up here, else me — and me is the
    fallback rather than worlds because its file is the one that defines BOTH
    volumes and can therefore create them from nothing on a fresh machine.
    """
    running = running_mode_names()
    return running[0] if running else (configured_mode() or "me")


def _stream_volume(argv: list[str], path: str, *, into_volume: bool) -> tuple[bool, str]:
    """Run docker with one end of the pipe on a host file — how a volume leaves the
    machine as a tarball, and comes back, without a bind mount in between."""
    try:
        with open(path, "rb" if into_volume else "wb") as f:
            run = subprocess.run(
                ["docker", *argv],
                stdin=f if into_volume else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL if into_volume else f,
                stderr=subprocess.PIPE,
                timeout=BACKUP_STREAM_TIMEOUT,
            )
        return run.returncode == 0, run.stderr.decode(errors="replace").strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        return False, str(e)


def _tail(text: str, limit: int = 300) -> str:
    """The end of a long output, visibly truncated — never chopped in silence."""
    return f"…{text[-limit:]}" if len(text) > limit else text


def backup_timestamp() -> str:
    """Local time, filesystem-plain: 2026-08-22-1430. A backup is named by when it was taken."""
    return time.strftime("%Y-%m-%d-%H%M")


def inspect_backup(backup_dir: str) -> dict:
    """Is this folder a backup we can restore? Returns its manifest.

    Read BEFORE the confirmation prompt, so the prompt can name the DATE of the
    backup about to replace an appliance. A folder name is not what somebody
    should be confirming.
    """
    try:
        with open(os.path.join(backup_dir, BACKUP_MANIFEST)) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        raise SetupError(f"No readable {BACKUP_MANIFEST} in {backup_dir} — that is not an Embabel backup.")
    # Both volumes or nothing: a graph without its documents (or the reverse) is
    # an appliance that contradicts itself, which is worse than a refusal.
    for _key, filename, _what in BACKUP_VOLUMES:
        if not os.path.exists(os.path.join(backup_dir, filename)):
            raise SetupError(f"Backup is incomplete — {filename} is missing.")
    return manifest


def list_backups(parent: str) -> list[tuple[str, dict]]:
    """Every restorable backup directly under a folder, newest first. Unreadable
    entries are skipped rather than reported — this is a listing, not a doctor."""
    found = []
    for entry in sorted(os.listdir(parent)) if os.path.isdir(parent) else []:
        path = os.path.join(parent, entry)
        if not os.path.isdir(path):
            continue
        try:
            found.append((path, inspect_backup(path)))
        except SetupError:
            continue
    return sorted(found, key=lambda pair: pair[1].get("createdAt", ""), reverse=True)


def _write_manifest(dest: str, saved: list[str], mode: str) -> None:
    """Enough to answer, a year from now, "what is this and can I restore it here".

    The identity recorded is the one `embabel version` prints, and for the same
    reason: the TAG is a name that moves, so a manifest saying "0.2.0-SNAPSHOT"
    dates a backup to nothing. The image digest and the commit the jar was built
    from do not move, and between them they say exactly what wrote these bytes.
    """
    manifest = {
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "instance": instance(),
        "mode": mode,
        "files": saved,
        "versions": appliance_versions(mode),
    }
    with open(os.path.join(dest, BACKUP_MANIFEST), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    with open(os.path.join(dest, "README.txt"), "w") as f:
        f.write(
            f"Embabel appliance backup — {time.strftime('%c')}\n\n"
            "Everything this appliance knew, at rest: the knowledge graph, worlds,\n"
            "documents, and the settings that shaped them. Restore it with:\n\n"
            f"    embabel --instance {instance()} restore {os.path.abspath(dest)}\n\n"
            "manifest.json records the exact images and commit that wrote these bytes.\n\n"
            "The .env and secrets.env here carry credentials — the database password,\n"
            "API keys, realm tokens. Treat this folder like the keys it holds.\n"
        )


def _copy_out(dest: str, mode: str) -> None:
    """The copy itself, between the stop and the restart."""
    saved = []
    for key, filename, what in BACKUP_VOLUMES:
        name = volume_name(key)
        if not volume_exists(key):
            raise SetupError(f"Volume {name} does not exist — has the appliance ever run on this machine?")
        ok, err = _stream_volume(
            ["run", "--rm", "-v", f"{name}:/from:ro", BACKUP_HELPER_IMAGE, "tar", "czf", "-", "-C", "/from", "."],
            os.path.join(dest, filename), into_volume=False)
        if not ok:
            raise SetupError(f"Backing up {what} failed: {_tail(err)}")
        size = os.path.getsize(os.path.join(dest, filename)) / 1e6
        print(f"    {what}  {size:,.0f} MB")
        saved.append(filename)

    for filename in backup_config_files():
        if os.path.exists(os.path.join(APPLIANCE_DIR, filename)):
            shutil.copyfile(os.path.join(APPLIANCE_DIR, filename), os.path.join(dest, filename))
            saved.append(filename)
    _write_manifest(dest, saved, mode)


def back_up(dest_dir: str = DEFAULT_BACKUP_DIR) -> str:
    """Copy the appliance into a new timestamped folder under dest_dir."""
    require_docker()
    mode = backup_mode()
    # Bring-back is decided by what is running NOW, never assumed: backing up a
    # deliberately stopped appliance must not be the thing that starts it.
    was_running = running_mode_names()

    dest = os.path.join(os.path.abspath(os.path.expanduser(dest_dir)),
                        f"embabel-backup-{instance()}-{backup_timestamp()}")
    os.makedirs(dest, exist_ok=True)

    if was_running:
        print(f"  Stopping the {mode} mode — a graph copied while it is live is a graph that will not restore.")
        for stopping in was_running:
            _compose(stopping, "stop", capture=True)

    try:
        _copy_out(dest, mode)
    finally:
        # The appliance comes back whether the copy worked or not. A failed backup
        # must never be the reason somebody's assistant is down.
        for restarting in was_running:
            _compose(restarting, "up", "-d", capture=True)
    return dest


def restore(backup_dir: str) -> str:
    """Replace this appliance's data and configuration with a backup's.

    Destructive by definition, and nothing here asks — the CALLER owns the
    confirmation, because the Me app's dialog and the CLI's prompt are the same
    decision asked in two different rooms.
    """
    manifest = inspect_backup(backup_dir)
    require_docker()
    came_from = manifest.get("instance")
    if came_from and came_from != instance():
        # Allowed, and useful — cloning one appliance into a scratch one is half
        # the reason to have two. But it overwrites a DIFFERENT appliance than
        # the backup came from, so it is said out loud rather than discovered.
        print(f"  This backup was taken from '{came_from}'; restoring it into "
              f"'{instance()}'.")
    mode = manifest.get("mode") or backup_mode()
    was_running = running_mode_names()
    for stopping in was_running or [mode]:
        _compose(stopping, "stop", capture=True)

    # Config first, volumes second: .env decides the ports and the Neo4j password
    # the restored graph was created under, so compose must be reading the
    # backup's copy by the time anything comes back up. What is replaced is set
    # ASIDE, not deleted — one .before-restore per file, kept until the next
    # restore overwrites it. Restoring means the backup's world, so a file the
    # backup does NOT have is set aside too.
    for filename in backup_config_files():
        current = os.path.join(APPLIANCE_DIR, filename)
        replacement = os.path.join(backup_dir, filename)
        if os.path.exists(current):
            if filename == OVERRIDE_FILE:
                with open(current) as f:
                    if not f.read().startswith(OVERRIDE_MARKER):
                        raise SetupError(f"{filename} was hand-written, not generated — "
                                         "move it aside yourself, then restore again.")
            os.replace(current, f"{current}.before-restore")
        if os.path.exists(replacement):
            shutil.copyfile(replacement, current)

    # A fresh machine has no volumes yet. Let COMPOSE create them: a volume made
    # by `docker run` lacks compose's project labels, and `up` on some versions
    # refuses to adopt it. `up --no-start` also pulls images, so a first restore
    # on a clean machine is a long one; on an existing install this never runs.
    if any(not volume_exists(key) for key, _filename, _what in BACKUP_VOLUMES):
        print("  Creating the appliance's volumes (this pulls images — it can take a while).")
        _compose(mode, "up", "--no-start")

    for key, filename, what in BACKUP_VOLUMES:
        print(f"    {what}")
        ok, err = _stream_volume(
            ["run", "--rm", "-i", "-v", f"{volume_name(key)}:/to", BACKUP_HELPER_IMAGE,
             "sh", "-c", "find /to -mindepth 1 -delete && tar xzf - -C /to"],
            os.path.join(backup_dir, filename), into_volume=True)
        if not ok:
            raise SetupError(f"Restoring {what} failed: {_tail(err)}")

    # Up unconditionally, even if nothing was running when this started. Backup
    # is the verb that must not change what is up; restore is the verb whose
    # whole point is the restored appliance, and leaving it stopped would be
    # answering "put my data back" with a machine that says nothing.
    _compose(mode, "up", "-d", capture=True)
    return manifest.get("createdAt", "an unknown time")


# The server stamps every sandbox with this (JvmInstance.INSTANCE_LABEL_KEY),
# naming the appliance it belongs to. The jvm label beside it cannot serve: its
# value is a per-process UUID, so a sandbox outlived by its JVM — the only kind
# worth pruning — matches nothing the host can name.
SANDBOX_INSTANCE_LABEL = "embabel-instance"


def stray_sandbox_containers(mine_only: bool = True) -> list[str]:
    """Sandbox containers still on the host, by name.

    THIS instance's, by the label the server stamps on them. Sandboxes carry no
    compose project — they are siblings created through the docker socket, not
    services — so without that label the only filter available was the bare
    `embabel-jvm` key, which matches every appliance's and every IDE run's alike.
    Pass mine_only=False to see all of them, which is what a bug report wants.

    The server sweeps these itself, but only two of the three cases: on shutdown it
    removes containers matching ITS OWN jvm id, and on startup it reaps EXITED ones
    from any jvm. A RUNNING sandbox whose jvm died without its shutdown hook — a
    kill -9, a crashed Docker VM, a `down` that timed out into SIGKILL — is caught
    by neither, and holds its memory until somebody notices.
    """
    argv = ["ps", "-a", "--filter", f"label={SANDBOX_LABEL}"]
    if mine_only:
        argv += ["--filter", f"label={SANDBOX_INSTANCE_LABEL}={instance()}"]
    run = _docker(*argv, "--format", "{{.Names}}")
    if not run or run.returncode != 0:
        return []
    return [line.strip() for line in run.stdout.splitlines() if line.strip()]


def remove_stray_sandboxes() -> None:
    """Offer to remove them, rather than just doing it.

    A developer running an assistant from an IDE has sandbox containers carrying the
    same label and a different jvm id, and killing those mid-session is precisely
    the bug the per-jvm scoping exists to prevent ("container is not running", from
    a test jvm nuking a dev session). This script cannot tell the two apart, so it
    asks instead of guessing.
    """
    strays = stray_sandbox_containers()
    if not strays:
        return
    print(f"\n  {len(strays)} code-sandbox container(s) are still on the host:")
    for name in strays[:8]:
        print(f"    {name}")
    if len(strays) > 8:
        print(f"    … and {len(strays) - 8} more")
    print("  They are siblings of the appliance, not part of it, so `down` left them.")
    print("  If you are running an assistant from an IDE, ITS sandboxes are in this list.")
    answer = prompt("  Remove them? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        print("  Left alone.")
        return
    run = _docker("rm", "-f", *strays, timeout=60)
    if run and run.returncode == 0:
        print(f"  Removed {len(strays)} sandbox container(s).")


CODEX_AGENTS_FILE = os.path.expanduser("~/.codex/AGENTS.md")
AGENTS_BLOCK_BEGIN = "<!-- BEGIN embabel appliance -->"
AGENTS_BLOCK_END = "<!-- END embabel appliance -->"


def codex_agents_block() -> str:
    """The global-guidance block for Codex, pointing back at this checkout.

    A ROUTER, not a copy: the canonical guidance is AGENTS.md at the repo root and
    the full runbooks are skills/*/SKILL.md, all readable by any agent from the
    absolute path below. Embedding the path is the point — global guidance applies
    in sessions on OTHER projects, where a relative path means nothing.
    """
    checkout = os.path.abspath(os.path.dirname(__file__))
    return (
        f"{AGENTS_BLOCK_BEGIN}\n"
        f"## Embabel appliance\n"
        f"The MCP server named '{MCP_SERVER_NAME}' is an Embabel appliance — the user's world\n"
        f"runtime (data, realms, saved views, apps). Before working with it, read\n"
        f"`{checkout}/AGENT_GUIDE.md` — the first-calls list there saves failed guesses, and it\n"
        f"routes to full runbooks under `{checkout}/skills/` for realm prospecting,\n"
        f"realm diagnosis, calling the server from apps, building world-served apps, and\n"
        f"interrogating a world.\n"
        f"{AGENTS_BLOCK_END}\n"
    )


def install_codex_agents_block(target: str = CODEX_AGENTS_FILE) -> None:
    """Put (or refresh) the marked block in Codex's global AGENTS.md, idempotently.

    Only the region between our markers is ever touched; everything else in the
    file is somebody's own guidance and stays byte-for-byte.
    """
    block = codex_agents_block()
    existing = ""
    if os.path.exists(target):
        with open(target) as f:
            existing = f.read()
    if AGENTS_BLOCK_BEGIN in existing and AGENTS_BLOCK_END in existing:
        head, rest = existing.split(AGENTS_BLOCK_BEGIN, 1)
        _, tail = rest.split(AGENTS_BLOCK_END, 1)
        updated = head + block.rstrip("\n") + tail
    else:
        joiner = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        updated = existing + joiner + block
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as f:
        f.write(updated)


def remove_codex_agents_block(target: str = CODEX_AGENTS_FILE) -> bool:
    """Remove OUR marked block from Codex's global AGENTS.md; everything else stays.
    Returns True when a block was actually removed."""
    if not os.path.exists(target):
        return False
    with open(target) as f:
        existing = f.read()
    if AGENTS_BLOCK_BEGIN not in existing or AGENTS_BLOCK_END not in existing:
        return False
    head, rest = existing.split(AGENTS_BLOCK_BEGIN, 1)
    _, tail = rest.split(AGENTS_BLOCK_END, 1)
    updated = (head.rstrip("\n") + "\n" + tail.lstrip("\n")).strip("\n")
    with open(target, "w") as f:
        f.write(updated + "\n" if updated else "")
    return True


def env_file_value(key: str, name: str | None = None) -> str | None:
    """One value from an instance's settings, or None. Defaults to the current
    instance; `name` is for reading ANOTHER one, which is how the port allocator
    finds out which blocks are already spoken for. The file may already be gone
    during teardown."""
    path = env_path(name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith(f"{key}="):
                return stripped.split("=", 1)[1].strip() or None
    return None


def this_appliance_urls() -> set[str]:
    """Every MCP URL that means THIS install, normalized for comparison.

    Both modes count: Me and Worlds are the same checkout, the same volume and the
    same account, so a registration against either port belongs to this appliance.
    A configured public base URL counts too — that is what a remote client was
    wired with.
    """
    bases = [
        surface_urls()["me"],
        surface_urls()["worlds"],
        surface_urls()["me"].replace("localhost", "127.0.0.1"),
        surface_urls()["worlds"].replace("localhost", "127.0.0.1"),
    ]
    for key in ("ASSISTANT_PUBLIC_BASE_URL", "WORLDS_PUBLIC_BASE_URL"):
        value = env_file_value(key)
        if value:
            bases.append(value)
    return {base.rstrip("/").lower() + "/mcp" for base in bases}


def registered_mcp_url(cli: str, name: str) -> str | None:
    """The URL a client has registered under [name], from its own `mcp get` output.

    Both CLIs print a `URL:`/`url:` line; anything else (entry absent, output
    reshaped) comes back None, which callers treat as "cannot tell".
    """
    try:
        run = subprocess.run([cli, "mcp", "get", name],
                             capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return None
    if run.returncode != 0:
        return None
    for line in run.stdout.splitlines():
        match = re.match(r"\s*url:\s*(\S+)", line, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def unwire_coding_agents() -> None:
    """Drop the MCP registration setup minted — and ONLY that one, verified by URL.

    The token is issued once and never returned again, so a registration that
    outlives its volume is not stale config — it is a client pointed at an
    appliance that cannot authenticate it, failing on every session start with
    nothing to say why. Removing the account without removing this is how you end
    up with `embabel: Failed to connect` in `claude mcp list` and no idea when it
    broke.

    But the NAME is not proof of ownership: '{MCP_SERVER_NAME}' in a client may
    point at a different appliance — a remote one, another checkout on other
    ports. Removing by name alone would take out a registration this uninstall
    never created. So each client is asked what URL it has, and only an entry
    pointing at this install is removed; anything else is left standing and said
    so. "Cannot tell" also leaves it standing — deleting on uncertainty is the
    wrong default for someone else's config.

    Must run while .env still exists: this install's ports live there.
    """
    ours = this_appliance_urls()
    for name, cli in (("Claude Code", shutil.which("claude")), ("Codex", shutil.which("codex"))):
        if not cli:
            continue
        url = registered_mcp_url(cli, MCP_SERVER_NAME)
        if url is None:
            continue
        if url.rstrip("/").lower() not in ours:
            print(f"  Left {name}'s '{MCP_SERVER_NAME}' registration alone — it points at {url}, not this appliance.")
            continue
        try:
            run = subprocess.run([cli, "mcp", "remove", MCP_SERVER_NAME],
                                 capture_output=True, text=True, timeout=30)
        except (subprocess.SubprocessError, OSError):
            continue
        if run.returncode == 0:
            print(f"  Removed the '{MCP_SERVER_NAME}' MCP server from {name}.")
    try:
        if remove_codex_agents_block():
            print(f"  Removed the appliance block from {CODEX_AGENTS_FILE}.")
    except OSError:
        pass


def cli_shim_paths() -> list[str]:
    """Every place install.sh could have written the `embabel` launcher.

    `$EMBABEL_BIN_DIR` first because an installer run with it set put the shim there, then the
    default. Both are checked rather than one, so an uninstall undoes an install that used either.
    """
    seen: list[str] = []
    for directory in (os.environ.get("EMBABEL_BIN_DIR"), os.path.join(os.path.expanduser("~"), ".local", "bin")):
        if directory and directory not in seen:
            seen.append(directory)
    return [os.path.join(d, "embabel") for d in seen]


def is_our_shim(path: str) -> bool:
    """Whether [path] is the forwarder install.sh wrote for THIS installation.

    NEVER delete an `embabel` on PATH just because it is called `embabel`. It is not a rare name,
    and install.sh warns when it finds one already ahead of ours. An uninstall that removed
    somebody's other tool because the names matched would be a far worse bug than the one this
    fixes.

    So: it must be a small file, it must carry the line install.sh writes, and it must name the
    directory this script is running from. Anything else is left alone.
    """
    try:
        if os.path.getsize(path) > 4096:
            return False
        with open(path) as f:
            body = f.read()
    except OSError:
        return False
    here = os.path.dirname(os.path.abspath(__file__))
    return "Written by install.sh" in body and here in body


def remove_cli_shim() -> None:
    """Take the `embabel` command off PATH — the one install.sh put there.

    Without this, uninstall left a live command pointing at a directory it had just emptied, so
    `embabel status` failed in a way that read as a broken product rather than a completed
    uninstall.
    """
    removed = []
    for path in cli_shim_paths():
        if not os.path.exists(path):
            continue
        if not is_our_shim(path):
            print(f"  Left {path} alone — it is not the launcher this installation wrote.")
            continue
        try:
            os.remove(path)
            removed.append(path)
            print(f"  Removed {path}.")
        except OSError as e:
            print(f"  Could not remove {path}: {e}")

    # A DIFFERENT `embabel` may still answer, and saying so is the point: otherwise the next
    # `which embabel` shows a hit and the uninstall looks like it failed again, which is exactly
    # the confusion this function exists to end.
    if removed:
        survivor = shutil.which("embabel")
        if survivor:
            print(f"  NOTE: another 'embabel' is still on your PATH: {survivor}")
            print("        That one is not ours, and it has been left alone.")


def uninstall() -> None:
    """--uninstall: back to the state a fresh clone is in.

    Everything --fresh removes, plus the machine-local configuration it leaves
    behind — which is the whole point. Re-running setup after --fresh never asks
    for a provider key, a timezone or a realms directory, because .env still has
    them, so it exercises none of the path a new user walks. That makes --fresh
    the wrong tool for testing the thing developers most need to test.

    Images and the local embedding model are KEPT, deliberately and always: the
    embedding artifact alone is over a gigabyte and re-downloading one that has
    not changed is pure waste. Nothing here offers to remove them.

    Realm checkouts are not touched either. They are their own repositories and
    somebody's work in progress; this script has no business deleting them.

    The `embabel` COMMAND does go, though — see [remove_cli_shim]. install.sh puts a
    launcher on PATH, so an uninstall that left it there left a live command pointing at a
    directory it had just emptied. The installation DIRECTORY stays: this script is running
    from it, and `./worlds.py` sets up again from there.
    """
    if len(installed_instances()) > 1:
        print(f"  --uninstall removes the '{instance()}' appliance. Others here are untouched:")
        print(f"    {', '.join(n for n in installed_instances() if n != instance())}")
    else:
        print("  --uninstall returns this checkout to the state a fresh clone is in.")
    print("\n  DELETED:")
    print("    the appliance's entire state — account, world, graph, documents, dashboards")
    print(f"    {env_file()} — your provider key, timezone, and realms directory")
    print(f"    {OVERRIDE_FILE} — the folders shared with the assistant")
    print(f"    the '{MCP_SERVER_NAME}' MCP registration — only where it points at THIS appliance")
    for path in cli_shim_paths():
        if os.path.exists(path) and is_our_shim(path):
            print(f"    {path} — the 'embabel' command install.sh put on your PATH")
            break
    print("    any stray code-sandbox container (asked separately — a dev JVM may own one)")
    print("\n  KEPT:")
    print("    images and the local embedding model — over a gigabyte, and unchanged")
    print("    realms/ and any realm checkout — your repositories, not ours")
    print("    this directory itself: `./worlds.py` sets up again from here\n")
    answer = prompt("  Type 'yes' to uninstall: ").strip().lower()
    if answer != "yes":
        raise SetupError("Not uninstalled — nothing was touched.")

    take_everything_down()
    remove_stray_sandboxes()
    # Before .env goes: unwiring verifies each registration's URL against this
    # install's ports, and those ports live in .env.
    unwire_coding_agents()
    for name in (env_file(), OVERRIDE_FILE):
        if os.path.exists(name):
            os.remove(name)
            print(f"  Removed {name}.")
        else:
            # Silence here made a re-run look like nothing happened at all.
            print(f"  No {name} to remove.")

    # The `embabel` command is one per MACHINE, not one per instance. Taking it
    # away while another appliance is still installed would uninstall one thing
    # and break a different one — so it goes only with the last of them.
    remaining = [n for n in installed_instances() if n != instance()]
    if remaining:
        print(f"\n  Kept the 'embabel' command: {', '.join(remaining)} still installed here.")
        print(f"  Done — instance '{instance()}' is gone.\n")
    else:
        remove_cli_shim()
        print("\n  Done — this checkout is back to the state a fresh clone is in.")
        print("  `embabel up` sets it up again from here.\n")


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
    ensure_wallet_key()
    remember_mode(mode)
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
        answer = prompt(f"  Stop the {other_mode} mode and continue? [Y/n]: ").strip().lower()
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
    print(f"  Starting the {mode} mode — pulling what it needs to answer.\n")
    run = _compose(mode, "up", "-d", *MODE_CORE[mode])
    if run.returncode != 0:
        raise SetupError(f"docker compose up failed for the {mode} mode.")
    print()
    start_deferred(mode)
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
    answer = prompt("  Reset the account and re-run setup? [y/N]: ").strip().lower()
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


def call_when_ready(base: str, token: str) -> dict:
    """The first real call, retried while the appliance finishes starting.

    The setup token is printed to the log EARLY — measured at ~10s before
    "Started ... in 24.977 seconds" — so finding it does not mean the HTTP surface
    is up. Calling straight through raised Unreachable, and because Unreachable is
    a SetupError that ended the whole run with "Could not reach the appliance
    (RemoteDisconnected)" moments after cheerfully announcing it had found the
    token. A first install failed on a race, and the message blamed the network.
    """
    deadline = time.monotonic() + BOOT_WAIT_SECONDS
    announced = False
    while True:
        try:
            return call(base, "", token)
        except Unreachable:
            if time.monotonic() >= deadline:
                raise
            if not announced:
                print("  Waiting for the appliance to finish starting…")
                announced = True
            time.sleep(2)


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
    token = prompt("  Setup token: ").strip()
    if not token:
        raise SetupError("A setup token is required.")
    return token


# ── rendering ───────────────────────────────────────────────────────────────

def disclose_usage_reporting(base: str) -> None:
    """Put the complete report shape in the first-run path, before setup closes.

    A README and a startup log are operator surfaces, but neither proves the person
    completing an interactive install saw the disclosure. This is deliberately a
    client-side rendering rather than another setup answer: current servers already
    expose the report, and setup.py has to remain compatible with those releases.

    Successful setup is the persistence boundary. Once /complete closes the setup API
    this function is never reached again; an interrupted setup shows it again on resume,
    which is preferable to remembering an acknowledgement for an incomplete install.
    """
    print("\n── Usage reporting " + "─" * 42)
    print("  This appliance sends an installation usage report to Embabel 10 minutes")
    print("  after startup, then every 24 hours. A random installation ID lets Embabel")
    print("  distinguish this installation over time; it is not derived from you or")
    print("  your machine.")
    print(f"\n  Destination: {PHONE_HOME_ENDPOINT}")
    print("\n  The complete JSON shape is:")
    print("    installation: installationId, firstSeen, counter, sentAt")
    print("    runtime:      version, packaging, uptimeSeconds")
    print("    host:         os, arch, processors, totalMemoryMb, jvmMaxHeapMb")
    print("    scale:        users, worlds, realms, nodes, relationships, labels,")
    print("                  documents, chunks")
    print("    activity:     http.server.requests, gen_ai.client.operation,")
    print("                  codemode.script, sandbox.session, kg.ask.refusal,")
    print("                  kg.query.warnings (numeric deltas only)")
    print("    modelProviders: configured provider names only")
    print("\n  It never sends content, prompts, responses, queries, names, email addresses,")
    print("  credentials, file paths, model IDs, realm names, or document names. The")
    print("  collector can observe the source IP of the HTTP connection, but the address")
    print("  is not a field in the report.")
    print(f"\n  Full field-by-field disclosure: {PHONE_HOME_DOC_URL}")
    print("  After signing in, inspect your installation itself:")
    print(f"    {base}/api/v1/phone-home/preview   what would be sent now")
    print(f"    {base}/api/v1/phone-home           literal JSON last sent")
    print("\n  Reporting has no configuration opt-out. If outbound telemetry is forbidden,")
    print("  block the destination at your network.")

    answer = prompt("\n  Continue setup? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        raise SetupError(
            "Setup paused before completion. Re-run this command when you are ready to continue."
        )


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
            raw = prompt(f"  Choose 1-{len(options)}" + (f" [{default}]: " if default else ": ")).strip()
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
            value = prompt(f"  {label}{suffix}").strip() or (default or "")
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


def confirm_answers(step: dict, answers: dict) -> bool:
    """Echo what is about to be submitted; True to go ahead, False to redo the step.

    Secrets are shown as their length, not their value: this runs in terminals
    people screen-share, and "48 characters" is enough to catch the paste that
    picked up half a line.
    """
    shown = []
    for field in step["fields"]:
        value = answers.get(field["name"])
        if value in (None, ""):
            continue
        label = field.get("label") or field["name"]
        if field.get("secret") or "password" in field["name"].lower() or "key" in field["name"].lower():
            shown.append(f"    {label}: ({len(value)} characters)")
        else:
            shown.append(f"    {label}: {value}")
    if not shown:
        return True
    print()
    for line in shown:
        print(line)
    return prompt("  Correct? [Y/n]: ").strip().lower() in ("", "y", "yes")


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

        # A LAST LOOK BEFORE IT IS PERMANENT. The server accepts a step once and
        # refuses to reopen setup afterwards (410, by design), so a username typed
        # with a typo was permanent the instant Enter was pressed — and the only way
        # back was --reset-password, which is not a thing anybody guesses while
        # staring at their own misspelt name. Only asked where re-entry actually
        # helps: a value the SERVER rejects already loops in place below, and a
        # value only the user can judge is the one nothing else can catch.
        if not prefilled and confirm_answers(step, answers) is False:
            continue

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
    """Offer to point Claude Code and Codex at the appliance, using the token the mcp step just
    minted. The token exists in this process exactly once — the server never returns
    it again — so this is the moment to hand it to a client.

    `claude mcp add` only writes config; the token itself goes live when setup
    completes and the appliance restarts, and the closing message says so."""
    token, url = result.get("token"), result.get("url")
    if not token or not url:
        return

    print("\n── Wire up coding agents " + "─" * 37)
    wired = False
    claude = shutil.which("claude")
    if claude:
        answer = prompt("  Point Claude Code at this appliance now (user scope)? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            try:
                run = subprocess.run(
                    [claude, "mcp", "add", "--transport", "http", "--scope", "user",
                     MCP_SERVER_NAME, url, "--header", f"Authorization: Bearer {token}"],
                    capture_output=True, text=True, timeout=60,
                )
                if run.returncode == 0:
                    print(f"  Claude Code wired as '{MCP_SERVER_NAME}' — new sessions will see the appliance.")
                    wired = True
                else:
                    print(f"  claude mcp add failed: {(run.stderr or run.stdout).strip()[:200]}")
            except (subprocess.SubprocessError, OSError) as e:
                print(f"  Could not run claude: {e}")
    else:
        print("  Claude Code CLI not found on PATH.")

    # Codex reads the bearer token from an ENVIRONMENT VARIABLE at session start —
    # `codex mcp add` records only the variable's NAME, never the token itself. So
    # wiring Codex is two moves: register the server, then get the export into the
    # operator's shell profile. The second half cannot be done for them silently
    # (editing someone's shell profile uninvited is not this script's place), so it
    # is printed as the one line they must add — loudly, because a registration
    # whose variable is unset fails on every session with nothing to say why.
    codex = shutil.which("codex")
    if codex:
        answer = prompt("  Point Codex at this appliance too? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            try:
                existing = registered_mcp_url(codex, MCP_SERVER_NAME)
                if existing and existing.rstrip("/").lower() != url.rstrip("/").lower() + "/mcp" \
                        and existing.rstrip("/").lower() != url.rstrip("/").lower():
                    print(f"  (replacing Codex's '{MCP_SERVER_NAME}' entry, which pointed at {existing})")
                subprocess.run([codex, "mcp", "remove", MCP_SERVER_NAME],
                               capture_output=True, text=True, timeout=30)
                run = subprocess.run(
                    [codex, "mcp", "add", MCP_SERVER_NAME, "--url", url,
                     "--bearer-token-env-var", CODEX_TOKEN_ENV],
                    capture_output=True, text=True, timeout=60,
                )
                if run.returncode == 0:
                    print(f"  Codex wired as '{MCP_SERVER_NAME}'. ONE STEP REMAINS — Codex reads the")
                    print(f"  token from ${CODEX_TOKEN_ENV}, so add this line to your shell profile:")
                    print(f"    export {CODEX_TOKEN_ENV}=\"{token}\"")
                    try:
                        install_codex_agents_block()
                        print(f"  Added appliance guidance to {CODEX_AGENTS_FILE} (a marked block; the rest of the file is untouched).")
                    except OSError as e:
                        print(f"  Could not update {CODEX_AGENTS_FILE}: {e}")
                    wired = True
                else:
                    print(f"  codex mcp add failed: {(run.stderr or run.stdout).strip()[:200]}")
            except (subprocess.SubprocessError, OSError) as e:
                print(f"  Could not run codex: {e}")

    if wired:
        return

    # Manual fallback — also what Cursor and other MCP clients copy from. Printing
    # the token is deliberate: this is the operator's own machine and the only time
    # it is available.
    print("  Wire any MCP client manually:")
    print(f"    URL:    {url}")
    print(f"    Header: Authorization: Bearer {token}")
    print(f"  (Claude Code: claude mcp add --transport http --scope user {MCP_SERVER_NAME} "
          f"{url} --header \"Authorization: Bearer <token>\")")
    print(f"  (Codex:       codex mcp add {MCP_SERVER_NAME} --url {url} "
          f"--bearer-token-env-var {CODEX_TOKEN_ENV}, then export {CODEX_TOKEN_ENV})")


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
    print("  A realm is a set of capabilities that extends a world. If you are writing")
    print("  one, the appliance can read it straight off this machine, with nothing")
    print("  published anywhere first. Give the directory your realms live IN, so that")
    print("  adding another is a copy rather than a change here.\n")

    default = os.path.realpath("realms")
    for _ in range(3):
        answer = prompt(f"  Realm checkouts directory [{default}]: ").strip()
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
                        help="appliance base URL (default: detected from the running mode, else this instance's Me port)")
    parser.add_argument("--token", help="setup token (default: read from the container logs)")
    parser.add_argument(
        "--world",
        help="world template NEW worlds start from: a git URL, owner/repo on GitHub, "
             "or a bare name in the embabel org. Written to .env as "
             "ASSISTANT_BOOTSTRAP_WORLD; existing worlds are never reshaped",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the appliance's state AND this machine's configuration (.env, shared "
             "folders, the MCP registration), returning the checkout to a fresh-clone state. "
             "Images, the local embedding model and your realm checkouts are kept",
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

    # Which appliance this run is about, before anything reads a settings file or
    # names a container. The CLI sets it for child processes; a direct ./me.py or
    # ./worlds.py inherits whatever the shell exports, and the default otherwise.
    use_instance(os.environ.get("EMBABEL_INSTANCE") or DEFAULT_INSTANCE)
    # Before compose is invoked for anything: a new instance without a block
    # would fall back to the default base and collide with the first appliance.
    if not args.uninstall:
        ensure_port_block()

    # Line-buffered even when stdout is a pipe. Subprocesses (compose, docker logs)
    # write to the same descriptor unbuffered, so Python's default block buffering
    # made a captured run read out of order — narration arriving after the output
    # it was meant to introduce.
    sys.stdout.reconfigure(line_buffering=True)

    print("\n  Embabel appliance — " + ("uninstall" if args.uninstall else "first-run setup"))
    print("  " + "─" * 60)

    follower = None
    try:
        # Compose files live next to this script; docker compose needs their directory.
        os.chdir(os.path.dirname(os.path.abspath(__file__)))

        # BEFORE the mode starts: the container reads .env at creation, and the
        # template only matters when a world is first built.
        # Before everything: it ends the run rather than setting anything up, and
        # it must not be preceded by writes to the .env it is about to delete.
        if args.uninstall:
            uninstall()
            return 0

        # What a bare `./setup.py --fresh` means: the door this machine was last
        # set up as, not a guess. Wiping a worlds appliance and bringing back the
        # Me door on top of its graph is not what anybody typing --fresh meant.
        mode = args.mode or configured_mode() or "me"
        if args.world:
            set_bootstrap_world(args.world)
        ensure_realms_dir(mode, args.realms)

        if args.fresh:
            fresh_wipe()
        started = False
        if args.mode or args.fresh:
            started = ensure_mode(mode)

        container = find_mode_container(args.mode)
        base = args.url or (container_base_url(container) if container else None) or default_base()
        if args.reset_password:
            if not container:
                raise SetupError(
                    "No mode is running to reset. Start one first:  ./me.py  or  ./worlds.py"
                )
            reset_credentials(container, base)
        if container:
            # NO URL HERE. A link this early in a wizard is an invitation: people
            # follow it, land on a surface that is not set up yet — and, on the
            # worlds mode, on the old Vaadin UI rather than the console — and come
            # back having answered nothing. Where to go is the LAST thing this
            # prints, once there is somewhere worth going.
            #
            # Unless the operator named a URL themselves, in which case echoing it
            # is not an invitation but a confirmation of which appliance is about
            # to be configured, which is exactly what they asked to control.
            if args.url:
                print(f"  Setting up {container} at {args.url}\n")
            else:
                print(f"  Setting up {container}…\n")

        if started and container:
            # First boot is a designed surface: show the operator console the app
            # prints, and only that, while we wait for the setup token.
            follower = follow_boot_log(container)

        token = discover_token(base, container, args.token)
        if follower:
            follower.terminate()
            follower = None
            print()
        status = call_when_ready(base, token)

        # Before account details, provider keys or the permanent /complete: the person
        # doing the installation sees the report contract in the flow they are already
        # following. A detached `docker compose up` cannot make a README visible.
        disclose_usage_reporting(base)

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
        service = mode_service(container) if container else None
        # Worlds people go to the console; `base` is the server behind it.
        where = console_url() if service == "worlds" else base
        print(f"\n  Done. Sign in at {where}" + (f" as {username}" if username else ""))
        print("  The appliance is restarting to pick up your provider key — give it a moment.\n")
        warn_if_conversion_pending()
        if service == "worlds":
            print_worlds_surfaces(base)
        elif service == "assistant":
            print_me_surfaces(base)
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
