
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

The questions live here, in embabel_setup/wizard.py, not in the appliance. The appliance
reports facts — is there an account, which providers are connected, how long must a
password be (`GET /api/v1/setup`) — and this installer decides from those what to ask, in
what order, and in what words. Each answer posts to an endpoint that does something only
the server can do. If you would rather drive the API yourself, everything here is plain
HTTP; see /swagger-ui on your instance.
"""

from __future__ import annotations

# THE VERSION GATE, BEFORE ANY embabel_setup IMPORT. The setup package is written for
# deferred annotations and runs on 3.9 — every module imports `from __future__ import
# annotations`, so the stock Mac 3.9 works. Older than that fails here as a sentence
# rather than a TypeError a new user cannot be expected to read.
import sys as _sys
if _sys.version_info < (3, 9):
    _sys.exit(
        "Embabel needs Python 3.9 or newer — this is Python "
        + _sys.version.split()[0]
        + ".\n  macOS:  brew install python@3.12   (then rerun the installer)"
        + "\n  Linux:  apt install python3.10  ·  dnf install python3.12"
    )

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
import tempfile
import textwrap
import threading
import sys
import time
import urllib.error
import urllib.request
import zlib

# The package sits beside this file. Every real entry point — this script, the
# `embabel` launcher, ./me.py and ./worlds.py — already lives in this directory,
# so Python puts it on sys.path for us. Stated rather than relied upon, because
# the failure mode of a caller that does not is an ImportError at first run.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# THE FACADE RE-EXPORTS THE PACKAGE. `embabel` loads this file by path and reads
# its namespace, ./me.py and ./worlds.py exec it, and install.sh runs those — so
# every name this file used to define has to remain reachable here. Star imports
# are the honest way to say that: the modules own the code, this file owns the
# contract.
from embabel_setup.core import (  # noqa: F401 — the facade's contract
    APPLIANCE_DIR, BOOT_WAIT_SECONDS, EMBEDDING_MODEL, ME_APP_DIR, MODE_COMPOSE, MODE_CORE,
    MODE_SERVICE, MODE_SERVICES, OVERRIDE_FILE, AlreadySetUp, SetupError, TokenRejected,
    Unreachable, prompt,
)
from embabel_setup.colour import *      # noqa: F403 — palette and marks
from embabel_setup.words import *        # noqa: F403 — copy loader
from embabel_setup.settings import *     # noqa: F403 — which appliance, and its ports
from embabel_setup.dockerlib import *    # noqa: F403 — docker, scoped to this instance
from embabel_setup.status import *       # noqa: F403 — the progress line
from embabel_setup.versions import *     # noqa: F403 — what is actually running
from embabel_setup.upgrade import *      # noqa: F403
from embabel_setup.backup import *       # noqa: F403
from embabel_setup.bugreport import *    # noqa: F403
from embabel_setup.seed import *         # noqa: F403
from embabel_setup.agents import *       # noqa: F403 — Claude Code and Codex wiring
from embabel_setup.surfaces import *     # noqa: F403 — where to go once it is up
from embabel_setup.steps import *        # noqa: F403 — asking, and posting the answers
from embabel_setup.lifecycle import *    # noqa: F403 — up, down, and away
from embabel_setup.realms import *       # noqa: F403 — realm checkouts and the world repo
from embabel_setup.capacity import *     # noqa: F403 — what docker can actually give this
from embabel_setup.samples import *      # noqa: F403 — fictional records, marked and removable
from embabel_setup.contracts import *    # noqa: F403 — ODCS contracts drafted for saved views
from embabel_setup.scenarios import *    # noqa: F403 — the world in a named state
# Imported as a module, not starred: the wizard is a small named vocabulary
# (`wizard.pending`, `wizard.MCP`) and reads better said out loud than merged
# into this file's namespace alongside forty other things.
from embabel_setup import wizard

# EXPLICITLY, because `import *` refuses names that begin with an underscore.
# That rule broke this file on main: StatusLine's constructor read _UNICODE_OK,
# the star import had silently not carried it, and STATUS is constructed at
# import time — so setup.py raised NameError before doing anything, for every
# user who picked the change up. A private name another module genuinely needs
# is a name that has to be imported by hand, or renamed. These three are used by
# this facade and by `embabel`; the rest stay inside their modules.
from embabel_setup.dockerlib import _compose, _docker


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

    print(banner("uninstall" if args.uninstall else "first-run setup"))

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
        # BEFORE anything reads it back. An install made from a branch (EMBABEL_REF, which
        # install.sh passes straight through) records that branch here, so `embabel upgrade`
        # follows it instead of replacing it with main — testing a branch is the whole reason
        # the variable exists, and an upgrade that silently undid it made it a trap.
        remember_source()
        ensure_realms_dir(mode, args.realms)

        if args.fresh:
            fresh_wipe()
        started = False
        if args.mode or args.fresh:
            # BEFORE the containers, not alongside them. The server dies without
            # this model, and dying forty seconds into a boot with a Spring
            # stack trace is a much worse way to learn that a 1.1GB file is
            # missing than being told up front that it is downloading.
            # BEFORE THE PULL, for the same reason as the embedding model below it:
            # everything after this line costs gigabytes and minutes, and "Docker has
            # 3.8 GB" is worth hearing while that is still a choice rather than a
            # post-mortem. Never a refusal — see capacity.py.
            report_capacity()
            ensure_embedding_model()
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
            # After the stream stops, so the summary is the last word on the boot
            # rather than a line the token block scrolls past.
            report_boot_warnings(container)
            print()
        status = call_when_ready(base, token)

        # Before account details, provider keys or the permanent /complete: the person
        # doing the installation sees the report contract in the flow they are already
        # following. A detached `docker compose up` cannot make a README visible.
        disclose_usage_reporting(base)

        pending = wizard.pending(status)
        if not pending:
            print("  Everything is already configured.")
        deferred = []
        api_token = None
        seed_auth = None
        for step in pending:
            result = run_step(base, token, step, use_environment=not args.ignore_env)
            if not result and provider_step(step):
                deferred.append(step)
            # START HERE, FINISH BEFORE /complete. The only credential that can
            # index anything is the one the account step just took, and it exists
            # only in the run that creates the account — waiting until after
            # /complete meant a resumed setup reached the upload with nothing to
            # authenticate as, and printed "no credential" instead of the guides.
            # HOLD THE CREDENTIAL, INDEX LATER. The only credential that can index
            # anything is the one this step just took, and it exists only in the run
            # that creates the account — so it is captured here and used after the
            # finish line. Indexing itself is deliberately NOT started yet: see the
            # note at the call site for why it cannot overlap /complete.
            if step["id"] == "account" and (result or {}).get("ok"):
                seed_auth = seed_credential(None)
            # Kept as it goes past: the server never returns this token again, and
            # seeding below needs a credential that is not the user's password.
            api_token = (result or {}).get("token") or api_token
            wire_coding_agents(result or {})

        print("\n  Finishing…", end=" ", flush=True)
        started_before = container_started_at(container) if container else ""
        try:
            done = complete_setup(base, token, container)
        except Unreachable:
            # [complete_setup] already waited for the appliance and asked it; if it
            # still says setup is open, the connection died for a reason that is not
            # the restart. Nothing below can help, and the deferred branch certainly
            # cannot — asking for a provider key would be answering a question the
            # server never asked.
            raise
        except SetupError:
            # The server may insist on a provider before it will close setup. If it
            # does, the offer to defer was a promise this client could not keep —
            # so ask for the key here rather than ending on a refusal the person
            # was just told would not happen.
            if not deferred:
                raise
            print()
            print(f"  {warn('This appliance requires a provider key to finish setup.')}")
            print("  " + dim("Its own configuration decides that, not this installer."))
            for step in deferred:
                wire_coding_agents(run_step(base, token, step, use_environment=False) or {})
            print("\n  Finishing…", end=" ", flush=True)
            done = complete_setup(base, token, container)
        print(done.get("detail", "complete"))

        username = done.get("signInAs")
        service = mode_service(container) if container else None
        # Worlds people go to the console; `base` is the server behind it.
        where = console_url() if service == "worlds" else base

        # WAIT BEFORE INVITING. /complete restarts the appliance so the model beans
        # are rebuilt with the key, and the old order printed "Sign in at …" into
        # that gap — measured at 21 seconds on this machine, during which the door
        # is shut and nothing says when it reopens. Somebody clicking immediately
        # met a dead port and concluded the install had failed.
        # Unless the restart already happened out from under us — RODE_OUT_RESTART marks
        # the answer [complete_setup] reconstructed after riding one out, and waiting
        # again would flash a status line at somebody already being served.
        if not deferred and not done.get(RODE_OUT_RESTART):
            STATUS.start("Restarting to pick up your provider key")
            wait_until_serving(container, base, started_before)
            STATUS.stop()
        # Uninstall keeps this checkout as the way back but removes its machine-wide
        # command. A successful setup must restore that command whichever door — the
        # curl installer, ./me.py or ./worlds.py — brought the operator here.
        write_cli_shim()
        print(f"\n  {TICK} Setup complete. Sign in at {url(where)}"
              + (f" as {bold(username)}" if username else ""))
        # Printed first and always — the address is the whole answer on a headless
        # box — then opened where opening means anything.
        if open_in_browser(where):
            print("  " + dim("Opening it in your browser…"))
        if deferred:
            say("no-provider-next")

        # AFTER THE FINISH LINE, ON PURPOSE. Indexing costs about a minute and none
        # of it is this installer's doing: measured against a live appliance, one
        # document takes 1.2s to chunk and embed, and uploading six at once returns
        # only 1.5x — the far end is one machine doing real work, so no amount of
        # client concurrency removes the wait.
        #
        # It therefore must not sit in front of anything. It cannot run DURING the
        # wizard either, because completing setup restarts the appliance and an
        # upload in flight when the server goes down is a document this installer
        # counted and the world does not have. So it runs here, after "Sign in
        # at …" has been printed and the door is open: the appliance is already
        # usable, and the guides arrive while somebody is looking at it.
        credential = seed_auth or seed_credential(api_token)
        if credential:
            seed_documentation(base, credential)
        else:
                print(f"  {MIDDOT} " + dim("Documentation not re-indexed this run — "
                                           "add or refresh it from Documents."))
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
        print(f"\n\n  Interrupted. Completed steps persist — pick up where you left off with:"
              f"\n    {resume_command()}\n", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
