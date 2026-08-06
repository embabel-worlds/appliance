#!/usr/bin/env python3
"""Embabel appliance — first-run setup.

    ./setup.py

Walks you through creating your account and connecting a model provider. Nothing to
install: standard library only. Works for either door — it finds the running
container (Me or Worlds), its port, and its setup token by itself.

If OPENAI_API_KEY or ANTHROPIC_API_KEY is already exported in your shell, the provider
step uses it instead of asking. `--ignore-env` always asks.

This client is deliberately thin. It does not know what the steps ARE — it asks the
appliance (`GET /api/v1/setup`) and renders whatever it describes, so when the appliance
gains a step this client picks it up without changing. If you would rather drive the API
yourself, everything here is plain HTTP; see /swagger-ui on your instance.
"""

from __future__ import annotations

import argparse
import getpass
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
# The appliance's two doors, by compose SERVICE name. Containers are found through
# their compose service label rather than a hardcoded container_name, so plain
# `./setup.py` works for whichever door is up — and keeps working if a name or
# project prefix ever changes.
DOOR_SERVICES = ("assistant", "worlds")
TOKEN_PATTERN = re.compile(r"Setup token:\s*([0-9a-f]{32,})")
# How long to keep watching a booting container for its token before asking.
BOOT_WAIT_SECONDS = 120

# Provider -> the variable that provider's key lives in, mirroring ProviderValidator's
# PROVIDERS map on the server. If a provider is added there and not here, nothing breaks:
# it is simply not offered from the environment, and gets asked for as before.
PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class SetupError(Exception):
    pass


class AlreadySetUp(SetupError):
    pass


class Unreachable(SetupError):
    pass


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
                "\n(To start over you would have to delete the data volume, which erases everything.)"
            )
        if e.code == 401:
            raise SetupError(f"The setup token was not accepted.\n{detail}")
        raise SetupError(detail)
    except urllib.error.URLError as e:
        raise Unreachable(
            f"Could not reach the appliance at {base} ({e.reason}).\n"
            "Is it running? Try: docker compose ps"
        )


def _docker(*argv: str, timeout: int = 30) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def find_door_container() -> str | None:
    """The running door's container, by compose service label."""
    found = []
    for service in DOOR_SERVICES:
        run = _docker("ps", "--filter", f"label=com.docker.compose.service={service}",
                      "--format", "{{.Names}}")
        if run is not None and run.returncode == 0:
            found += run.stdout.split()
    if len(found) > 1:
        # Both doors up is a misconfiguration in itself (see the compose headers) —
        # say so, but setup still has to happen somewhere.
        print(f"  Both doors are running ({', '.join(found)}) — run one at a time.")
        print(f"  Setting up {found[0]}.")
    return found[0] if found else None


def container_base_url(container: str) -> str | None:
    """The door's URL from its own SERVER_PORT. The compose files keep the host and
    container ports equal by design, so the container's port IS the published one."""
    run = _docker("inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", container)
    if run is not None and run.returncode == 0:
        for line in run.stdout.splitlines():
            if line.startswith("SERVER_PORT="):
                return f"http://localhost:{line.split('=', 1)[1].strip()}"
    return None


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
            f"No appliance is running: no door container was found and {base} does not answer.\n"
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
        except SetupError as e:
            print("\n")
            raise
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up the Embabel appliance.")
    parser.add_argument("--url", default=None,
                        help=f"appliance base URL (default: detected from the running door, else {DEFAULT_BASE})")
    parser.add_argument("--token", help="setup token (default: read from the container logs)")
    parser.add_argument(
        "--ignore-env",
        action="store_true",
        help=f"always ask, even if {' or '.join(PROVIDER_ENV.values())} is set",
    )
    args = parser.parse_args()

    print("\n  Embabel appliance — first-run setup")
    print("  " + "─" * 60)

    try:
        container = find_door_container()
        base = args.url or (container_base_url(container) if container else None) or DEFAULT_BASE
        if container:
            print(f"  Setting up {container} at {base}\n")

        token = discover_token(base, container, args.token)
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
        return 0
    except SetupError as e:
        print(f"\n  {e}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Half-finished setup is fine: completed steps persist, so re-running resumes.
        print("\n\n  Interrupted. Re-run this script to pick up where you left off.\n", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
