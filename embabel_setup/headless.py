"""Finish setup with no person in the room — CI, a provisioning tool, a fleet.

WHY THIS EXISTS BESIDE THE CONSOLE HAND-OFF, rather than replacing it. The worlds
mode hands over to the console because the console asks better: it checks a
provider key against the provider as you paste it, derives a username from your
name, and reports the world build in phases. None of that helps a pipeline, which
has no browser and no hands — and an acceptance test that cannot finish an install
cannot test anything that comes after one.

WHAT IT DELIBERATELY DOES NOT DO: connect a model provider. `POST /setup/provider`
makes a real completion call against the provider, with no stub and no property
that skips it, so a keyless appliance is the only shape that installs without
money, a secret, and somebody else's uptime in the path. The server supports
exactly that — completion needs a credential and nothing else — and a key can be
added afterwards with `embabel` or the console. Supply EMBABEL_SETUP_PROVIDER_KEY
only when connecting a provider is the thing being tested.

THE CONTRACT:

    EMBABEL_ACCEPT_TRIAL_TERMS     accept the free trial terms           required
    EMBABEL_SETUP_USER             the operator username                 required
    EMBABEL_SETUP_PASSWORD_FILE    a file holding the password           required*
    EMBABEL_SETUP_PASSWORD         the password itself                   *or this
    EMBABEL_SETUP_FULL_NAME        display name, what a world matches    optional
    EMBABEL_SETUP_MCP              mint an MCP token (1/true/yes)        optional
    EMBABEL_SETUP_PROVIDER_KEY     a model provider key to connect       optional

The terms variable is separate from everything else here, and deliberately not
implied by --headless: accepting terms is an act, and a flag that means "install
without asking" must not also mean "agree on my behalf". Unset, the run stops at
the terms with a sentence naming the variable — which is also how an operator
discovers the terms exist.

A FILE IS PREFERRED FOR THE PASSWORD, and the variable is the fallback rather than
the other way round. An environment variable is readable by every process that can
see the environment, survives into `docker inspect` and into any child process, and
is the thing a CI log prints when somebody debugs with `env`. A file can be mode
0600 and deleted; in GitHub Actions it is one `Set-Content`/`printf` away from a
secret that never enters the environment at all.

IDEMPOTENT ON PURPOSE. A second run against a finished install prints what is
already true and exits 0: re-running a provisioning step must not be an error, and
410 Gone is the server agreeing. An account that already exists is likewise taken
as done rather than as a conflict — `GET /setup` is asked first, so the decision
comes from facts rather than from a failed write.
"""

from __future__ import annotations

import os

from .colour import TICK, dim, heading, url
from .core import SetupError
from .settings import console_url
from .steps import call, complete_setup
from .surfaces import print_worlds_surfaces
from . import wizard

# What counts as "yes" in an environment variable. Lowercased before comparison.
_TRUE = ("1", "true", "yes", "on")


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUE


def password_from_environment() -> str:
    """The password, from a file if one is named and from the variable otherwise.

    Read whole and stripped of ONE trailing newline only: `printf 'pw' > file` and
    `echo pw > file` must mean the same password, while a password that genuinely
    ends in a space is still intact. A file that exists and is empty is a mistake
    worth naming — it is what a failed secret expansion leaves behind.
    """
    path = (os.environ.get("EMBABEL_SETUP_PASSWORD_FILE") or "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                secret = f.read()
        except OSError as e:
            raise SetupError(f"EMBABEL_SETUP_PASSWORD_FILE: cannot read {path} ({e.strerror}).")
        secret = secret[:-1] if secret.endswith("\n") else secret
        if not secret:
            raise SetupError(
                f"EMBABEL_SETUP_PASSWORD_FILE: {path} is empty.\n"
                "  A secret that expanded to nothing leaves exactly this."
            )
        return secret
    secret = os.environ.get("EMBABEL_SETUP_PASSWORD") or ""
    if not secret:
        raise SetupError(
            "--headless needs the account's password.\n"
            "  EMBABEL_SETUP_PASSWORD_FILE=/path/to/file   (preferred — a file can be 0600 "
            "and deleted, and never reaches `docker inspect` or a CI log)\n"
            "  EMBABEL_SETUP_PASSWORD=…                    (fallback)"
        )
    return secret


def account_from_environment(facts: dict) -> dict:
    """The /setup/account payload, validated against the SERVER'S OWN rules.

    Checked here as well as there so a refusal names the variable that is wrong
    instead of arriving as an HTTP 400 from a call the operator did not make. The
    numbers come from `GET /setup` rather than from constants in this file — the
    server owns them, and a client that hard-codes 8 will be wrong the week they
    change.

    72 BYTES, NOT CHARACTERS: bcrypt truncates past that, so the server refuses it
    outright, and a passphrase of emoji or accented letters hits the limit sooner
    than its length suggests.
    """
    user = (os.environ.get("EMBABEL_SETUP_USER") or "").strip()
    if not user:
        raise SetupError(
            "--headless needs the account's username.\n"
            "  EMBABEL_SETUP_USER=operator"
        )
    if any(ch < " " or ch == "\x7f" for ch in user):
        raise SetupError("EMBABEL_SETUP_USER: contains a control character.")

    secret = password_from_environment()
    least = facts.get("minPasswordLength") or 8
    most = facts.get("maxPasswordBytes") or 72
    if len(secret) < least:
        raise SetupError(
            f"The password is {len(secret)} characters; this appliance requires at least {least}."
        )
    if len(secret.encode("utf-8")) > most:
        raise SetupError(
            f"The password is {len(secret.encode('utf-8'))} bytes; this appliance accepts at "
            f"most {most} (bcrypt truncates past that, so the server refuses it)."
        )

    payload = {"username": user, "password": secret}
    full_name = (os.environ.get("EMBABEL_SETUP_FULL_NAME") or "").strip()
    if full_name:
        payload["displayName"] = full_name
    return payload


def complete_headless(base: str, token: str, container: str | None, facts: dict) -> int:
    """Create the account, optionally mint an MCP token, and close setup.

    `facts` is the `GET /setup` body the caller already has — the password rules
    and `hasAccount` both come from it, so this makes no call the caller has
    already made.

    ORDER IS THE SERVER'S: the account must exist before /mcp (the token binds to
    it) and before /complete (which refuses without a credential). Everything else
    is optional and skipped.
    """
    print("\n" + heading("Finishing setup without a browser"))

    if facts.get("hasAccount"):
        # Not a conflict. A provisioning step that runs twice must be allowed to.
        print(f"  {TICK} An account already exists — keeping it.")
    else:
        payload = account_from_environment(facts)
        answer = call(base, f"/{wizard.ACCOUNT}", token, payload)
        # The DETAIL, not the payload: nothing here should be able to print a
        # password, and the server's own sentence names the account it created.
        print(f"  {TICK} {answer.get('detail') or 'Account created'}")

    key = (os.environ.get("EMBABEL_SETUP_PROVIDER_KEY") or "").strip()
    if key:
        # The one step that reaches the internet, and the reason it is opt-in: the
        # server validates the key with a real completion against the provider.
        answer = call(base, f"/{wizard.PROVIDER}", token, {"apiKey": key},
                      waiting_on="the model provider")
        print(f"  {TICK} {answer.get('detail') or 'Provider connected'}")
    else:
        print(f"  {dim('·')} No provider key given — the appliance installs without one.")

    if _flag("EMBABEL_SETUP_MCP"):
        answer = call(base, f"/{wizard.MCP}", token)
        # The token comes back ONCE and is never readable again, so it is printed
        # here deliberately: a pipeline that asked for it has nowhere else to get
        # it. It only becomes usable after the appliance next restarts — it
        # arrives through providers.env, which Spring imports at boot.
        minted = answer.get("token")
        print(f"  {TICK} {answer.get('detail') or 'MCP access enabled'}")
        if minted:
            print(f"      token: {minted}")
            print(f"      {dim('usable after the next restart — it is imported at boot')}")

    complete_setup(base, token, container)
    print(f"  {TICK} Setup complete.")

    print_worlds_surfaces(base)
    print(f"  Console: {url(console_url())}\n")
    return 0


def already_complete(base: str) -> int:
    """What a second --headless run prints when the appliance is already set up.

    Exit 0, because "already done" is the outcome the caller wanted. The console
    address is the one useful thing left to say.
    """
    print("\n" + heading("Already set up"))
    print(f"  {TICK} This appliance has completed setup; nothing to do.")
    print(f"  Console: {url(console_url())}")
    print(f"  {dim('Forgot the password? --reset-password recreates the account and keeps all data.')}\n")
    return 0
