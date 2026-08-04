#!/usr/bin/env python3
"""Embabel Me appliance — first-run setup.

    ./setup.py

Walks you through creating your account and connecting a model provider. Nothing to
install: standard library only.

This client is deliberately thin. It does not know what the steps ARE — it asks the
appliance (`GET /api/v1/setup`) and renders whatever it describes, so when the appliance
gains a step this client picks it up without changing. If you would rather drive the API
yourself, everything here is plain HTTP; see /swagger-ui on your instance.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "http://localhost:4242"
TOKEN_HEADER = "X-Embabel-Setup-Token"
CONTAINER = "embabel-assistant"


class SetupError(Exception):
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
            raise SetupError(
                "This appliance is already set up. Sign in at " + base +
                "\n(To start over you would have to delete the data volume, which erases everything.)"
            )
        if e.code == 401:
            raise SetupError(f"The setup token was not accepted.\n{detail}")
        raise SetupError(detail)
    except urllib.error.URLError as e:
        raise SetupError(
            f"Could not reach the appliance at {base} ({e.reason}).\n"
            "Is it running? Try: docker compose ps"
        )


def discover_token(explicit: str | None) -> str:
    """The token is printed to the container log on first boot. Read it from there so the
    usual case needs no copy-paste at all; fall back to asking."""
    if explicit:
        return explicit
    try:
        logs = subprocess.run(
            ["docker", "logs", CONTAINER],
            capture_output=True, text=True, timeout=30,
        ).stdout
        # Last match wins: a restarted container logs it again, and the newest is current.
        matches = re.findall(r"Setup token:\s*([0-9a-f]{32,})", logs)
        if matches:
            print(f"Found the setup token in the {CONTAINER} logs.\n")
            return matches[-1]
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    print("Could not read the setup token from the container logs.")
    print(f"Find it with:  docker compose logs {CONTAINER} | grep 'Setup token'\n")
    token = input("Setup token: ").strip()
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


def run_step(base: str, token: str, step: dict) -> None:
    print(f"\n── {step['title']} " + "─" * max(0, 60 - len(step["title"])))
    if step.get("description"):
        print(f"   {step['description']}")

    while True:
        answers = {field["name"]: ask(field) for field in step["fields"]}
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
            return
        # A rejected answer is worth retrying in place — usually a typo'd key.
        print(f"\n  {result.get('detail', 'That did not work.')}\n  Let's try that step again.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up the Embabel Me appliance.")
    parser.add_argument("--url", default=DEFAULT_BASE, help=f"appliance base URL (default {DEFAULT_BASE})")
    parser.add_argument("--token", help="setup token (default: read from the container logs)")
    args = parser.parse_args()

    print("\n  Embabel Me — first-run setup")
    print("  " + "─" * 60)

    try:
        token = discover_token(args.token)
        status = call(args.url, "", token)

        pending = [step for step in status["steps"] if not step["satisfied"]]
        if not pending:
            print("  Everything is already configured.")
        for step in pending:
            run_step(args.url, token, step)

        print("\n  Finishing…", end=" ", flush=True)
        done = call(args.url, "/complete", token, {})
        print(done.get("detail", "complete"))

        username = done.get("signInAs")
        print(f"\n  Done. Sign in at {args.url}" + (f" as {username}" if username else ""))
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
