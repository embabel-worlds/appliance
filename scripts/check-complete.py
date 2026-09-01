#!/usr/bin/env python3
"""Two things the installer must not get wrong about a call that goes quiet.

FINISHING SETUP survives the restart that finishing setup causes, and A TIMEOUT ON THE
PROVIDER STEP does not blame the appliance for a provider that never answered. Both were
reported as "Could not reach the appliance" — the same sentence for a success and for
somebody else's firewall — and both cost a colleague a failed install.

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

from embabel_setup import seed, steps, wizard                        # noqa: E402
from embabel_setup.core import AlreadySetUp, Timeout, Unreachable    # noqa: E402

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


def provider_step_has_its_own_clock():
    """The step that waits on a third party says so, and gets longer than the rest.

    Both facts live on the step rather than in run_step's body, so this checks the
    contract between them: a wizard that stops declaring it silently goes back to
    sixty seconds and to blaming the appliance.
    """
    step = [s for s in wizard.steps({"supportedProviders": ["openai"]}) if s["id"] == wizard.PROVIDER][0]
    assert step.get("waitsOnProvider"), "the provider step no longer declares that it waits on a provider"
    assert steps.provider_of("sk-ant-abc") == "Anthropic", "an Anthropic key is named as OpenAI"
    assert steps.provider_of("sk-abc") == "OpenAI"
    assert steps.provider_of(None) == "your provider", "an absent key must not guess a provider"
    assert steps.PROVIDER_TIMEOUT_SECONDS > steps.CALL_TIMEOUT_SECONDS, (
        f"{steps.PROVIDER_TIMEOUT_SECONDS}s is no longer than the {steps.CALL_TIMEOUT_SECONDS}s "
        "budget it exists to widen")


def a_timeout_does_not_blame_the_appliance():
    """The whole point. A provider that never answers is not an appliance that is down,
    and the sentence a person reads has to be able to tell them apart."""
    named = str(steps._timed_out("http://appliance", 180, "OpenAI"))
    assert "THE APPLIANCE IS FINE" in named, named
    assert "OpenAI" in named, named
    assert "api.openai.com" in named, "the message does not hand over a way to check egress"
    assert "Could not reach the appliance" not in named, named
    # Without a third party there IS nobody else to blame, but it still must not claim
    # the appliance is absent — it answered the connection, it just took too long.
    bare = str(steps._timed_out("http://appliance", 60, None))
    assert "did not answer within 60s" in bare, bare
    assert "Could not reach the appliance" not in bare, bare


def a_timeout_is_still_an_unreachable():
    """Every existing `except Unreachable` has to keep catching these, or the new type
    escapes handlers written before it existed."""
    assert issubclass(Timeout, Unreachable)


seed.remember_account("rod", "not-the-real-password")
EXTRA = (
    ("a normal answer is passed through unmarked", normal_answer_is_untouched),
    ("the provider step declares its own clock and who it waits on", provider_step_has_its_own_clock),
    ("a timeout names the provider, never the appliance", a_timeout_does_not_blame_the_appliance),
    ("a timeout is still caught by every existing Unreachable handler", a_timeout_is_still_an_unreachable),
)
for name, case in CASES + EXTRA:
    try:
        case()
        print(f"  ✓ {name}")
    except AssertionError as e:
        print(f"  ✗ {name}: {e}")
        failures.append(name)

sys.exit(1 if failures else 0)
