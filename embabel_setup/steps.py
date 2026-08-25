"""Asking the setup questions, and posting the answers.

The QUESTIONS themselves live in wizard.py — this is the conversation: how a
field is rendered, what a rejected answer does, and the HTTP underneath. Two
rules run through it.

A rejected answer retries IN PLACE. Ending the whole wizard because a password
was four characters long, and making somebody re-answer the steps they already
got right, is a hostile way to meet a new user.

An answer the SERVER cannot check gets confirmed before it is sent. The server
accepts a step once and then refuses to reopen setup, so a username typed with a
typo is permanent the instant Enter is pressed.
"""
import base64
import getpass
import http.client
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

from .colour import MIDDOT, TICK, bold, dim, url, warn
from .core import (APPLIANCE_DIR, BOOT_WAIT_SECONDS, AlreadySetUp, SetupError,
                   TokenRejected, Unreachable, prompt)
from .dockerlib import _compose, _docker, boot_failure
from .settings import (PHONE_HOME_DOC_URL, PHONE_HOME_ENDPOINT, console_url,
                       phone_home_on, resume_command, set_env_var)
from .status import STATUS, boot_phase
from .colour import heading
from .words import say
from .seed import remember_account, remember_provider_key
from . import wizard

TOKEN_HEADER = "X-Embabel-Setup-Token"


TOKEN_PATTERN = re.compile(r"Setup token:\s*([0-9a-f]{32,})")


# Provider -> the variable that provider's key lives in, mirroring ProviderValidator's
# PROVIDERS map on the server. If a provider is added there and not here, nothing breaks:
# it is simply not offered from the environment, and gets asked for as before.
PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


# WHAT THIS APPLIANCE PREFERS where the server offers a choice and has no opinion
# worth imposing. The step definitions come from the server, which serves more
# than this deployment, so the default it ships is a general one; this is the
# appliance's own answer, applied only when the value is genuinely on offer.
#
# Pressing Enter is the commonest thing anybody does at a prompt, so the default
# is not a cosmetic detail — it is the answer most installs will actually give.
PREFERRED_DEFAULTS = {"provider": "openai"}


def call(base: str, path: str, token: str, payload: dict | None = None,
         method: str | None = None) -> dict:
    url = f"{base}/api/v1/setup{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method or ("POST" if data else "GET"))
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


def remember_client_state(base: str, token: str, notes: dict) -> None:
    """Leave a note for the next run of this installer, in the appliance's data volume.

    The server stores these and hands them back without reading them, which is
    what makes them ours: "we offered MCP and were told no" is a fact about a
    conversation the server was not part of, and it must survive `--resume`
    without the server growing an opinion about it.

    Best effort on purpose. A note that fails to save costs one repeated
    question; failing the install over it would cost the whole install.
    """
    try:
        call(base, "/state", token, notes, method="PUT")
    except SetupError:
        pass


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
            answer = call(base, "", token)
            STATUS.stop()
            return answer
        except Unreachable:
            if time.monotonic() >= deadline:
                raise
            if not announced:
                STATUS.start("Waiting for the appliance to answer")
                announced = True
            time.sleep(2)


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
                STATUS.stop(f"  {TICK} The appliance is up. Setup token read from its log.\n")
                return token
        state = probe(base)  # raises AlreadySetUp — the friendliest outcome
        if state == "pending":
            # The API answers but the current container log has no token — a remote
            # appliance with no local docker, or a log that lost it. Ask below.
            break
        # A death ends the wait immediately. Spinning out the full two minutes and
        # then blaming the token is the worst of both: slow AND wrong.
        died = boot_failure(container)
        if died:
            STATUS.stop()
            raise SetupError(
                f"The appliance failed to start.\n  {died}\n\n"
                f"  Full log:  docker logs {container}\n"
                f"  Then:      embabel doctor"
            )
        if container is None or time.monotonic() >= deadline:
            break
        if not announced:
            STATUS.start(boot_phase(container, base))
            announced = True
        else:
            STATUS.set(boot_phase(container, base))
        time.sleep(3)

    STATUS.stop()
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
    # NOTHING TO DISCLOSE WHEN NOTHING IS SENT. Reporting is off by default now,
    # and a page explaining a transmission that will not happen is worse than
    # silence: it teaches people the appliance phones home when it does not.
    if not phone_home_on():
        return

    print("\n" + heading("Usage reporting"))
    say("usage-reporting", endpoint=PHONE_HOME_ENDPOINT,
        doc_url=PHONE_HOME_DOC_URL, base=base)

    answer = prompt("\n  Continue setup? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        raise SetupError(
            "Setup paused before completion. Nothing is lost — the appliance is "
            f"installed and running.\n  Continue with:  {resume_command()}"
        )


def yes_no_options(options: list) -> tuple[str, str] | None:
    """The (yes, no) pair when these options ARE a yes/no, else None.

    Matched on the values rather than on the field name, so any server-side
    two-option boolean gets the idiomatic prompt without this file learning its
    name. Order-insensitive: "no"/"yes" is the same question.
    """
    if len(options) != 2:
        return None
    lowered = [str(option).strip().lower() for option in options]
    if set(lowered) != {"yes", "no"}:
        return None
    return (options[lowered.index("yes")], options[lowered.index("no")])


def ask(field: dict) -> str:
    label = field.get("label") or field["name"]
    default = field.get("default")
    options = field.get("options") or []
    required = field.get("required", True)

    # This appliance's preference wins, but only over a choice that is actually
    # offered — a preference for something the server does not list would make
    # Enter do nothing, which is worse than any default.
    preferred = PREFERRED_DEFAULTS.get(field["name"])
    if preferred and (not options or preferred in options):
        default = preferred

    # A YES/NO IS A YES/NO. The server describes this field as a CHOICE of two,
    # which is the truth about its VALUES and not an instruction about how to
    # draw it — the installer owns the questions, the appliance owns the facts
    # (84cbe67). Rendered literally it became "1) yes / 2) no / Choose 1-2",
    # the only numbered menu in a wizard whose every other yes/no is [Y/n], and
    # then it was confirmed as well.
    if field["type"] == "CHOICE" and yes_no_options(options):
        yes, no = yes_no_options(options)
        default_yes = (default or yes).lower() != no
        while True:
            raw = prompt(f"\n  {label}? " + ("[Y/n]: " if default_yes else "[y/N]: ")).strip().lower()
            if not raw:
                return yes if default_yes else no
            if raw in ("y", "yes"):
                return yes
            if raw in ("n", "no"):
                return no
            print("  Answer y or n.")

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
    back in. Keyed off the field names rather than the step id, so it does nothing at
    all for a step that lacks them.

    Note this reads the environment of the shell running THIS script, which is not the
    container's. A key set only in `.env` reaches the appliance but not here, and is
    still asked for: the appliance reports a provider as connected once it holds the
    key itself, and an env var it was handed at boot is not that.
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
        print(f"  {dim('Enter takes the default.')}")
        provider = ask(next(f for f in step["fields"] if f["name"] == "provider"))
        if provider not in available:
            return {}
    return {"provider": provider, "apiKey": available[provider]}


def confirm_answers(step: dict, answers: dict) -> str | None:
    """A last look before the step is permanent. Returns the field to redo, or None to send.

    The server accepts a step once and refuses to reopen setup (410, by design), so
    a username typed with a typo was permanent the instant Enter was pressed.

    WHAT `n` USED TO MEAN was "ask me everything again" — including the password,
    which was never in doubt, and which somebody re-typed because they wanted to
    change their display name. So the answer names the field: one number, one
    question, and the rest stand.

    A PASSWORD IS NOT SHOWN AT ALL, not even as "OK". There is nothing in it to
    check — it was typed blind, and it is the one field re-asking is cheap for —
    and a line carrying no information invites the reader to skim the ones that do.
    A KEY still shows its length, because a truncated paste is its whole failure mode.
    """
    shown = []
    for field in step["fields"]:
        name = field["name"]
        value = answers.get(name)
        if value in (None, "") or "password" in name.lower():
            continue
        label = field.get("label") or name
        if field.get("secret") or "key" in name.lower():
            shown.append((name, label, f"({len(value)} characters)"))
        else:
            shown.append((name, label, value))
    # NOTHING TO CONFIRM is not a question. One field the user just typed, or a
    # step whose only answer was a menu choice they made deliberately, does not
    # need a second opinion — and a confirmation nobody can fail is ceremony.
    if len(shown) < 2:
        return None
    print()
    for index, (_, label, rendered) in enumerate(shown, 1):
        print(f"    {index}) {label}: {rendered}")
    while True:
        raw = prompt("  Enter to continue, or a number to change: ").strip()
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(shown):
            return shown[int(raw) - 1][0]
        print(f"  Enter, or 1-{len(shown)}.")


def provider_step(step: dict) -> bool:
    """Is this the model-provider step?

    Keyed off the server's own field names, like from_environment beside it, so a
    step added server-side is unaffected. NO env condition any more: the question
    is asked on every run, because which key this appliance connects with is the
    operator's to choose — silently taking whatever the shell exported made that
    choice for them.
    """
    return {"provider", "apiKey"} <= {field["name"] for field in step["fields"]}


def run_step(base: str, token: str, step: dict, use_environment: bool = True) -> dict:
    print("\n" + heading(step["title"]))
    # The step's own words, from copy/. `description` stays supported for a step
    # this installer does not know — the server can add one, and it arrives as a
    # string rather than as a file we ship.
    if step.get("copy"):
        say(step["copy"])
    elif step.get("description"):
        print(f"   {step['description']}")

    # A KEY IS NOT A TOLL GATE. Asking for a paid credential before anybody has
    # seen the product work is the commonest way a good tool loses an evaluator,
    # and it is unnecessary here: embeddings are local and always have been, so
    # documents, search, memory, realms, views and handlers all work with no key
    # at all. The step stays unsatisfied, truthfully — the server reports that as
    # a fact and closes setup anyway; re-running setup, or the Models tab, picks
    # it up later.
    # ONE PROMPT THAT TAKES THE KEY, not permission to ask for one.
    #
    # It used to ask "Connect a provider now? [Y/n]" two lines under a description
    # that says "Paste a key" — so a key was pasted, echoed in clear text into
    # scrollback, and then read as YES by `answer in ("n","no")`, under which
    # everything that is not "no" is yes. A prompt that CAN receive a secret must
    # hide its input, and a prompt that wants a thing should take the thing.
    #
    # Asked on EVERY run, including when a key is already exported: whose key this
    # appliance connects with is the operator's choice, and taking the shell's
    # silently made it ours.
    forced: dict | None = None
    if provider_step(step):
        say("provider-choice")
        # THE ENVIRONMENT READ DIRECTLY, not through from_environment — which asks
        # its OWN menu when both keys are exported, and a menu followed by this
        # prompt is two questions where the point was to have one. The appliance's
        # preferred provider decides, and the other is named in passing so nobody
        # wonders whether it was seen.
        exported = {p: os.environ[var] for p, var in PROVIDER_ENV.items()
                    if os.environ.get(var, "").strip()} if use_environment else {}
        chosen = (PREFERRED_DEFAULTS.get("provider") if PREFERRED_DEFAULTS.get("provider") in exported
                  else next(iter(exported), None))
        environment = {"provider": chosen, "apiKey": exported[chosen]} if chosen else {}
        source = PROVIDER_ENV[chosen] if chosen else None
        others = [PROVIDER_ENV[p] for p in exported if p != chosen]
        if others:
            print(f"\n  {MIDDOT} " + dim(f"{' and '.join(others)} also exported — paste a key here to use one instead."))
        while True:
            asked = getpass.getpass(
                f"\n  Enter to use {source} from your environment, or paste another key, "
                "or 'n' to skip: " if environment
                else "\n  Paste your model-provider key, or 'n' to skip: ").strip()
            if asked.lower() in ("n", "no"):
                print(f"\n  {TICK} Starting without a provider key. "
                      + dim("Add one any time: `embabel up` asks again."))
                return {}
            if asked:
                # A pasted key IS the answer. The server detects whose it is — the
                # provider field says so itself — so it is never asked for.
                forced = {"apiKey": asked}
                break
            if environment:
                print(f"  Using {source}.")
                forced = environment
                break
            print("  A key, or 'n' to skip.")

    while True:
        # Only on the FIRST attempt. A retry means the server rejected these answers, and
        # re-offering the same environment value would loop forever on a stale key.
        prefilled = forced if forced is not None else (
            from_environment(step) if use_environment else {})
        use_environment = False
        # Cleared after one attempt: a REJECTED key must be asked for again, not
        # re-posted forever — the same rule the environment value already follows.
        forced = None
        if prefilled and "provider" in prefilled and step["id"] != wizard.MCP:
            print(f"\n  Using {PROVIDER_ENV[prefilled['provider']]} from your environment.")
        # `in`, not `or`: a value the caller supplied stands even when it is empty,
        # and a field it did not supply is the only one worth asking about.
        answers = {
            field["name"]: prefilled[field["name"]] if field["name"] in prefilled else ask(field)
            for field in step["fields"]
        }
        if {"username", "password"} <= set(answers):
            # Held for the length of this process only, and used for exactly one
            # thing: the documentation upload after setup. The alternative was to
            # depend on the MCP step's minted token, which is not always minted —
            # so seeding silently did nothing, which is how it shipped broken.
            remember_account(answers["username"], answers["password"])
        if {"provider", "apiKey"} <= set(answers) and answers.get("apiKey"):
            # Persisted only once the SERVER has accepted it, further down — a
            # rejected key written to .env would be handed to every later boot.
            pending_key = (answers.get("provider") or "openai", answers["apiKey"])
        else:
            pending_key = None

        # A LAST LOOK BEFORE IT IS PERMANENT. The server accepts a step once and
        # refuses to reopen setup afterwards (410, by design), so a username typed
        # with a typo was permanent the instant Enter was pressed — and the only way
        # back was --reset-password, which is not a thing anybody guesses while
        # staring at their own misspelt name. Only asked where re-entry actually
        # helps: a value the SERVER rejects already loops in place below, and a
        # value only the user can judge is the one nothing else can catch.
        # ONE FIELD, not the step. `continue` here re-asked everything, which is
        # what made a display-name typo cost a re-typed password.
        while not prefilled:
            redo = confirm_answers(step, answers)
            if redo is None:
                break
            field = next(f for f in step["fields"] if f["name"] == redo)
            answers[redo] = ask(field)
            if {"username", "password"} <= set(answers):
                remember_account(answers["username"], answers["password"])

        # DECLINING IS NOT A SERVER CALL. There is no "no" endpoint any more: the
        # appliance mints a token when asked and knows nothing about having been
        # offered. So we record our own note and never post.
        if step["id"] == wizard.MCP:
            if (answers.get("enable") or "yes").strip().lower() not in ("y", "yes"):
                remember_client_state(base, token, {wizard.MCP_DECLINED: "yes"})
                print(f"\n  {TICK} MCP left off. "
                      + dim("Re-run setup if you want it later."))
                return {}
            answers = {}

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
            if pending_key:
                remember_provider_key(*pending_key)
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
