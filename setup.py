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
import subprocess
import threading
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
# WHERE A PERSON GOES, per mode — which is not the same as where the API is. The
# worlds server on 4342 also serves the old Vaadin UI, and sending a new operator
# there instead of to the console means their first impression is a surface that
# is on its way out.
CONSOLE_PORT = 4343
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
                f"Worlds: the console at http://localhost:{CONSOLE_PORT}   ·   Me: {base}"
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
    print(f"  API            {base}   (the server the console talks to)")
    print(f"  MCP endpoint   {base}/mcp")
    print("                 Authorization: Bearer \u2014 the token this setup just minted,")
    print("                 stored at /data/embabel/assistant/admin/providers.env")
    print("  Graph          http://localhost:4243  (neo4j / NEO4J_PASSWORD, default embabel-assistant)")
    print("  Dashboards     http://localhost:4246   \u00b7   Metrics  http://localhost:4247")
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
    print("  Graph          http://localhost:4243  (neo4j / NEO4J_PASSWORD, default embabel-assistant)")
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
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            if any(line.strip().startswith("EMBABEL_KEY_SECRET=") for line in f):
                return
    # 32 bytes, base64 — AES-256, the length WalletEncryptionConfiguration validates.
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    with open(ENV_FILE, "a") as f:
        f.write(
            "\n# The key your wallet is encrypted with, generated once by setup.py.\n"
            "# KEEP IT. Changing or losing it does not lock you out of the appliance —\n"
            "# it makes every credential already stored undecryptable, and you re-enter\n"
            "# them. Back it up with anything else you would not want to retype.\n"
            f"EMBABEL_KEY_SECRET={key}\n"
        )
    os.chmod(ENV_FILE, 0o600)
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
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            lines = f.read().splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            break
    else:
        lines += ["", *why, f"{key}={value}"]
    with open(ENV_FILE, "w") as f:
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
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
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
    cmd = ["docker", "compose", "-f", MODE_COMPOSE["me"], "-f", MODE_COMPOSE["worlds"],
           "down", "--volumes", "--remove-orphans"]
    run = subprocess.run(cmd, capture_output=True, text=True)

    # BY PROJECT LABEL, not by name. Matching "embabel-" caught embabel-assistant-neo4j
    # and embabel-assistant-docling — a developer's own stack from the assistant repo,
    # a different compose project entirely — and reported somebody else's healthy
    # containers as an uninstall that had failed.
    left = _docker("ps", "-a", "--filter", f"label=com.docker.compose.project={COMPOSE_PROJECT}",
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
    net = _docker("network", "inspect", "embabel-appliance_default",
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


def stray_sandbox_containers() -> list[str]:
    """Sandbox containers still on the host, by name.

    The server sweeps these itself, but only two of the three cases: on shutdown it
    removes containers matching ITS OWN jvm id, and on startup it reaps EXITED ones
    from any jvm. A RUNNING sandbox whose jvm died without its shutdown hook — a
    kill -9, a crashed Docker VM, a `down` that timed out into SIGKILL — is caught
    by neither, and holds its memory until somebody notices.
    """
    run = _docker("ps", "-a", "--filter", f"label={SANDBOX_LABEL}", "--format", "{{.Names}}")
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
        f"`{checkout}/AGENTS.md` — the first-calls list there saves failed guesses, and it\n"
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


def env_file_value(key: str) -> str | None:
    """One value from .env, or None. The file may already be gone during teardown."""
    if not os.path.exists(ENV_FILE):
        return None
    with open(ENV_FILE) as f:
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
        f"http://localhost:{env_file_value('ASSISTANT_PORT') or '4242'}",
        f"http://localhost:{env_file_value('WORLDS_PORT') or '4342'}",
        f"http://127.0.0.1:{env_file_value('ASSISTANT_PORT') or '4242'}",
        f"http://127.0.0.1:{env_file_value('WORLDS_PORT') or '4342'}",
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
    print("  --uninstall returns this checkout to the state a fresh clone is in.")
    print("\n  DELETED:")
    print("    the appliance's entire state — account, world, graph, documents, dashboards")
    print(f"    {ENV_FILE} — your provider key, timezone, and realms directory")
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
    for name in (ENV_FILE, OVERRIDE_FILE):
        if os.path.exists(name):
            os.remove(name)
            print(f"  Removed {name}.")
        else:
            # Silence here made a re-run look like nothing happened at all.
            print(f"  No {name} to remove.")
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
                        help=f"appliance base URL (default: detected from the running mode, else {DEFAULT_BASE})")
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
        base = args.url or (container_base_url(container) if container else None) or DEFAULT_BASE
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
        where = f"http://localhost:{CONSOLE_PORT}" if service == "worlds" else base
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
