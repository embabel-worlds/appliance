#!/usr/bin/env python3
"""A first install must ask for the trial terms, however its mode was chosen.

`EMBABEL_MODE=worlds` is how the documented one-line install picks a door. setup.py
read "has this machine installed before?" from configured_mode(), which answers from
the environment first, so every install that named its mode looked already set up and
skipped the terms entirely. The question now comes from .env alone.
"""
import contextlib
import io
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embabel_setup import settings, steps  # noqa: E402
from embabel_setup.core import SetupError  # noqa: E402


def write_env(root: str, text: str) -> None:
    with open(os.path.join(root, ".env"), "w") as f:
        f.write(text)


original_dir = settings.APPLIANCE_DIR
try:
    with tempfile.TemporaryDirectory() as root, \
            patch.dict(os.environ, {"EMBABEL_MODE": "worlds"}):
        settings.APPLIANCE_DIR = root

        # The bug: a fresh machine with the mode named in the environment.
        assert steps.should_ask_trial_terms(), \
            "a first install with EMBABEL_MODE set skipped the trial terms"
        # The mode itself is still honoured for choosing the door.
        assert settings.configured_mode() == "worlds", "EMBABEL_MODE no longer picks the door"

        # Only the port block written so far, as on a real first run.
        write_env(root, "EMBABEL_PORT_BASE=11042\n")
        assert steps.should_ask_trial_terms(), "a .env without a recorded mode counted as installed"

        # Accepting records it, so an interrupted run does not ask twice.
        with patch.object(steps, "prompt", return_value="y"), contextlib.redirect_stdout(io.StringIO()):
            steps.accept_trial_terms()
        assert settings.terms_accepted(), "accepting the terms was not recorded in .env"
        assert not steps.should_ask_trial_terms(), "an accepted install was asked again"
        with open(os.path.join(root, ".env")) as f:
            assert "EMBABEL_PORT_BASE=11042" in f.read(), "recording acceptance lost existing settings"

    with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=False):
        os.environ.pop("EMBABEL_MODE", None)
        settings.APPLIANCE_DIR = root

        # Declining stops setup and records nothing.
        write_env(root, "EMBABEL_PORT_BASE=11042\n")
        with patch.object(steps, "prompt", return_value="n"), contextlib.redirect_stdout(io.StringIO()):
            try:
                steps.accept_trial_terms()
                raise AssertionError("declining the terms did not stop setup")
            except SetupError:
                pass
        assert not settings.terms_accepted(), "declined terms were recorded as accepted"
        assert steps.should_ask_trial_terms(), "a declined install will not be asked again"

        # An install made before acceptance was recorded: .env holds its mode. `embabel up`
        # runs from scripts and cron with no terminal, so it must not start asking now.
        write_env(root, "EMBABEL_PORT_BASE=11042\nEMBABEL_MODE=worlds\n")
        assert not steps.should_ask_trial_terms(), "an existing install would be asked on every up"

    # The answer given here reaches the appliance, so the console's Welcome does not ask again.
    with tempfile.TemporaryDirectory() as root:
        settings.APPLIANCE_DIR = root
        same = {"termsVersion": "2026-10-13", "termsAccepted": False, "termsUrl": steps.TRIAL_TERMS_DOC_URL}
        sent = []

        def record(*args, **kwargs):
            sent.append(args)
            return {"ok": True}

        with patch.object(steps, "call", side_effect=record):
            steps.record_trial_terms_on_appliance("http://x", "t", same)
            assert not sent, "terms not accepted here were recorded on the appliance"

            write_env(root, "EMBABEL_TERMS_ACCEPTED=2026-10-30\n")
            steps.record_trial_terms_on_appliance("http://x", "t", same)
            assert sent == [("http://x", "/terms", "t", {"version": "2026-10-13"})], \
                f"the appliance was not told the terms in force were accepted: {sent}"

            sent.clear()
            steps.record_trial_terms_on_appliance("http://x", "t", {**same, "termsAccepted": True})
            steps.record_trial_terms_on_appliance("http://x", "t", {**same, "termsVersion": None})
            steps.record_trial_terms_on_appliance("http://x", "t", {**same, "termsUrl": "https://example.com/other"})
            assert not sent, f"recorded when already accepted, unconfigured, or a different document: {sent}"

        # Terms issued after this install accepted its terms are not reported as agreed to.
        sent.clear()
        with patch.object(steps, "call", side_effect=record):
            steps.record_trial_terms_on_appliance("http://x", "t", {**same, "termsVersion": "2026-12-01"})
        assert not sent, f"an acceptance older than the terms was reported as agreeing to them: {sent}"

        # A refusal or an unreachable appliance leaves it to the console, and never stops setup.
        with patch.object(steps, "call", side_effect=SetupError("refused")):
            steps.record_trial_terms_on_appliance("http://x", "t", same)
finally:
    settings.APPLIANCE_DIR = original_dir

# The appliance is told only when its terms are the ones shown here, so a compose default
# that drifted from TRIAL_TERMS_DOC_URL would quietly put the second question back.
for compose in ("docker-compose-me.yml", "docker-compose-worlds.yml"):
    with open(os.path.join(original_dir, compose)) as f:
        assert f"ASSISTANT_SETUP_TERMS_URL=${{ASSISTANT_SETUP_TERMS_URL:-{steps.TRIAL_TERMS_DOC_URL}}}" in f.read(), \
            f"{compose} configures the appliance with terms other than the ones setup shows"

print("ok: trial terms are asked on every first install, once, and the appliance is told")
