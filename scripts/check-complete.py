#!/usr/bin/env python3
"""Finishing setup survives the restart that finishing setup causes.

`/complete` is the one call in the install where the connection dying is the EXPECTED
outcome: the server persists completion, then schedules its own exit two seconds later
so the model beans are rebuilt with the provider key. For a while this client read that
dead socket as a failure and threw away an install that had already succeeded — and only
for people who supplied a key, since a key is what makes the restart happen at all. Two
people hit it before anyone worked out that the message was blaming the network for a
success.

The fix has to be checked in BOTH directions or it is not a check at all: a client that
calls every dropped connection a restart would hide the crash it is supposed to report.

    python3 scripts/check-complete.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embabel_setup import seed, steps                       # noqa: E402
from embabel_setup.core import AlreadySetUp, Unreachable    # noqa: E402

failures = []


def arrange(answer, appliance_says, comes_back=True):
    """Stand in for the three things complete_setup leans on: the HTTP call, the wait,
    and the probe that asks the appliance what it thinks its own state is."""
    def call(base, path, token, payload=None, method="POST"):
        if isinstance(answer, Exception):
            raise answer
        return answer

    def probe(base):
        if isinstance(appliance_says, Exception):
            raise appliance_says
        return appliance_says

    steps.call = call
    steps.probe = probe
    steps.wait_until_serving = lambda container, base, was: comes_back
    steps.container_started_at = lambda container: "before"
    steps.STATUS.start = lambda *a, **k: None
    steps.STATUS.stop = lambda *a, **k: None


def expect_complete(what):
    done = steps.complete_setup("http://appliance", "token", "container")
    assert done.get("ok") is True, done
    assert done.get(steps.RODE_OUT_RESTART) is True, done
    assert done.get("signInAs") == "rod", f"{what}: the username was lost with the answer"


def expect_refusal(what):
    try:
        steps.complete_setup("http://appliance", "token", "container")
    except Unreachable:
        return
    raise AssertionError(f"{what} was reported as a completed setup")


CASES = (
    ("a dropped connection, then an appliance that says setup is CLOSED, is the restart",
     lambda: (arrange(Unreachable("RemoteDisconnected"), AlreadySetUp("410")),
              expect_complete("the restart"))),
    ("a dropped connection, then an appliance that says setup is still OPEN, is a failure",
     lambda: (arrange(Unreachable("RemoteDisconnected"), "pending"),
              expect_refusal("an appliance still waiting for setup"))),
    ("an appliance that never comes back is a failure",
     lambda: (arrange(Unreachable("RemoteDisconnected"), "unreachable", comes_back=False),
              expect_refusal("an appliance that never answered again"))),
)


def normal_answer_is_untouched():
    arrange({"ok": True, "detail": "setup complete", "restarting": True, "signInAs": "rod"}, "pending")
    done = steps.complete_setup("http://appliance", "token", "container")
    # The SERVER's `restarting` is on every successful answer and means the restart is
    # still ahead. Marking this one as ridden-out would skip the wait on the normal path
    # and hand somebody a URL that dies a second later.
    assert steps.RODE_OUT_RESTART not in done, done
    assert done["detail"] == "setup complete", done


seed.remember_account("rod", "not-the-real-password")
for name, case in CASES + (("a normal answer is passed through unmarked", normal_answer_is_untouched),):
    try:
        case()
        print(f"  ✓ {name}")
    except AssertionError as e:
        print(f"  ✗ {name}: {e}")
        failures.append(name)

sys.exit(1 if failures else 0)
