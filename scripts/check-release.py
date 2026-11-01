#!/usr/bin/env python3
"""One setting says which build of the server an appliance follows, and it is kept.

WHY THIS EXISTS. EMBABEL_RELEASE is turned into an image tag in three places that must
agree — the CLI (release.py), the compose files, and what the server is told — and the
mistakes it can make are quiet ones: `stable` pulled as a tag that does not exist, a
sandbox from one release beside a server from another, a legacy EMBABEL_VERSION that
silently outranks the release somebody just chose, a choice made at install that the
first upgrade forgets, or a step back onto an older server that nothing stopped. These
are the cases that decide each of those.

Nothing here talks to Docker: the .env is a temporary file and image facts are dicts.

    python3 scripts/check-release.py
"""
import contextlib
import io
import os
import re
import sys
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from embabel_setup import release, settings  # noqa: E402
from embabel_setup.core import SetupError  # noqa: E402
from embabel_setup.cliparser import build_parser  # noqa: E402

failures = []


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


# ── what a person may type ───────────────────────────────────────────────────
for typed, kept in (("stable", "stable"), ("Beta", "beta"), ("nightly", "nightly"), ("canary", "canary"),
                    ("latest", "stable"), ("edge", "canary"), ("0.4.1", "0.4.1"), ("v0.4.1", "0.4.1"),
                    ("0.5.0-rc.2", "0.5.0-rc.2"), ("sha-24b7301", "sha-24b7301")):
    try:
        got = release.parse_release(typed)
    except SetupError as e:
        got = f"refused: {e}"
    check(got == kept, f"`{typed}` should be kept as {kept}, got {got}")
for typed in ("", "stabel", "0.4", "0.4.1-SNAPSHOT", "sha-24b73", "my-build", "../latest", "0.4.1; rm"):
    try:
        release.parse_release(typed)
        failures.append(f"`{typed}` was accepted as a release")
    except SetupError:
        pass

for name, tag in (("stable", "latest"), ("beta", "beta"), ("nightly", "nightly"), ("canary", "canary"),
                  ("0.4.1", "0.4.1"), ("sha-24b7301", "sha-24b7301")):
    check(release.release_tag(name) == tag, f"{name} resolves to :{release.release_tag(name)}, not :{tag}")
check(set(release.ABOUT) == set(release.RELEASES), "every release needs its sentence in ABOUT")


# ── which build is older ─────────────────────────────────────────────────────
def facts(tag=None, version=None, created=None, id=None):
    return {"tag": tag, "version": version, "created": created, "id": id}


cases = (
    # Release numbers decide when both have one; a candidate comes before its release.
    (facts("0.4.0", created="2026-11-02T00:00:00Z"), facts("0.4.1", created="2026-11-01T00:00:00Z"), True),
    (facts("0.4.1"), facts("0.4.0"), False),
    (facts("0.5.0-rc.1"), facts("0.5.0"), True),
    (facts("0.5.0-rc.2"), facts("0.5.0-rc.1"), False),
    (facts("0.10.0"), facts("0.9.9"), False),
    # A stream has only its build time; fractions of different lengths still sort as time.
    (facts("latest", "sha-aaaaaaa", "2026-11-01T16:06:39.912Z"),
     facts("canary", "sha-bbbbbbb", "2026-11-01T16:07:38.747491531Z"), True),
    (facts("canary", created="2026-11-01T16:07:38Z"), facts("latest", created="2026-11-01T16:07:38.5Z"), True),
    (facts("nightly", created="2026-11-02T01:00:00Z"), facts("latest", created="2026-11-01T16:00:00Z"), False),
    # The same image is never older than itself, whatever the tags say.
    (facts("0.4.0", id="sha256:x"), facts("0.4.1", id="sha256:x"), False),
    # Nothing to go on is not a verdict.
    (facts("latest"), facts("canary"), None),
)
for target, current, expected in cases:
    got = release.older(target, current)
    check(got is expected, f"older({target}, {current}) is {got}, not {expected}")


# ── the compose files resolve what the CLI resolves ──────────────────────────
SERVER = "${EMBABEL_VERSION:-${EMBABEL_IMAGE_TAG:-latest}}"
SANDBOX = "${EMBABEL_SANDBOX_IMAGE:-ghcr.io/embabel/assistant-sandbox:${EMBABEL_IMAGE_TAG:-latest}}"
texts = {}
for name in ("docker-compose-worlds.yml", "docker-compose-me.yml", "infra.yml"):
    with open(os.path.join(ROOT, name)) as f:
        texts[name] = f.read()
for name in ("docker-compose-worlds.yml", "docker-compose-me.yml"):
    text = texts[name]
    check(f"image: ghcr.io/embabel/assistant:{SERVER}" in text, f"{name}: the server image does not follow the release")
    check(f"ASSISTANT_SANDBOX_IMAGE={SANDBOX}" in text,
          f"{name}: the app is not handed the sandbox infra.yml pre-pulls")
    check(re.search(r"- EMBABEL_RELEASE=\$\{EMBABEL_RELEASE:-stable\}", text) is not None,
          f"{name}: the server is not told which release it follows")
check(f"image: {SANDBOX}" in texts["infra.yml"], "infra.yml: the sandbox pre-pull does not follow the release")
check(f"image: ghcr.io/embabel/assistant-grafana:{SERVER}" in texts["infra.yml"],
      "infra.yml: Grafana does not move with the server")
for name, text in texts.items():
    # Every Embabel image goes through the release; a bare `:latest` left behind is one that does not.
    for line in re.findall(r"image:\s*\S*ghcr\.io/embabel/assistant\S*", text):
        check("EMBABEL_IMAGE_TAG" in line, f"{name}: `{line}` does not follow the release")


# ── one .env, read and written ───────────────────────────────────────────────
@contextlib.contextmanager
def appliance(env_text: str = "", environ: dict | None = None):
    """A checkout with this .env, and this process environment."""
    with tempfile.TemporaryDirectory() as checkout:
        if env_text is not None:
            with open(os.path.join(checkout, ".env"), "w") as f:
                f.write(env_text)
        clean = {k: v for k, v in os.environ.items() if k not in ("EMBABEL_RELEASE", "EMBABEL_VERSION")}
        with patch.object(settings, "APPLIANCE_DIR", checkout), patch.dict(os.environ, {**clean, **(environ or {})},
                                                                           clear=True):
            settings.use_instance(settings.DEFAULT_INSTANCE)
            release.use_release(None)
            try:
                yield os.path.join(checkout, ".env")
            finally:
                release.use_release(None)


def read(path: str) -> str:
    with open(path) as f:
        return f.read()


with appliance(""):
    check(release.followed_release() == "stable", "an install whose .env says nothing should follow stable")
    check(release.compose_release_env() == {"EMBABEL_IMAGE_TAG": "latest", "EMBABEL_VERSION": "",
                                            "EMBABEL_RELEASE": "stable"},
          f"compose is handed the wrong images for stable: {release.compose_release_env()}")

with appliance("EMBABEL_RELEASE=canary\n"):
    check(release.compose_release_env()["EMBABEL_IMAGE_TAG"] == "canary", "canary in .env is not the tag compose gets")

# After install the environment does not move the server: that is `embabel release`'s, with its check.
with appliance("EMBABEL_RELEASE=stable\n", {"EMBABEL_RELEASE": "canary"}):
    check(release.followed_release() == "stable", "an EMBABEL_RELEASE left in the shell moved an installed appliance")

with appliance("EMBABEL_RELEASE=stabel\n"):
    try:
        release.followed_release()
        failures.append("a release .env cannot name was followed anyway")
    except SetupError as e:
        check("stabel" in str(e), f"the refusal does not say what .env has: {e}")

# The legacy pin outranks the release, from the environment first — and says so.
with appliance("EMBABEL_RELEASE=beta\nEMBABEL_VERSION=0.3.0\n"):
    env = release.compose_release_env()
    check(env == {"EMBABEL_IMAGE_TAG": "beta", "EMBABEL_VERSION": "0.3.0", "EMBABEL_RELEASE": "0.3.0"},
          f"a legacy pin in .env is not what compose and the server see: {env}")
    check(release.effective_tag() == "0.3.0", "the legacy pin does not outrank the release")
    check(release.version_pin_conflict() == ("0.3.0", "beta"), f"the conflict is not named: {release.version_pin_conflict()}")
with appliance("EMBABEL_VERSION=latest\n"):
    check(release.version_pin_conflict() is None, "a pin naming stable's own tag is not a conflict")
with appliance("EMBABEL_VERSION=0.3.0\n", {"EMBABEL_VERSION": "my-build"}):
    check(release.effective_tag() == "my-build", "the environment's legacy pin should win, as it does for compose")

# Choosing a release for one run blanks the pin for compose: the pull is the target's.
with appliance("EMBABEL_VERSION=0.3.0\n"):
    release.use_release("0.4.1")
    env = release.compose_release_env()
    check(env == {"EMBABEL_IMAGE_TAG": "0.4.1", "EMBABEL_VERSION": "", "EMBABEL_RELEASE": "0.4.1"},
          f"a release chosen for the run does not reach compose: {env}")
    check("EMBABEL_RELEASE" not in read(release.env_path()), "choosing for the run wrote .env before the check")

# Remembering writes the release and retires the pin, keeping the line readable.
with appliance("# mine\nEMBABEL_VERSION=0.3.0\nOTHER=x\n") as path:
    changed = release.remember_release("beta")
    text = read(path)
    check(release.recorded_release() == "beta", f".env does not record the release: {text}")
    check(release.legacy_pin() is None, f"the legacy pin still outranks the release: {text}")
    check("# EMBABEL_VERSION=0.3.0" in text and "OTHER=x" in text and "# mine" in text,
          f"retiring the pin lost what was around it: {text}")
    check(any("beta" in c for c in changed) and any("0.3.0" in c for c in changed), f"what changed is not said: {changed}")
    check(release.remember_release("beta") == [], "remembering the same release again changed something")

# Setup: a new install writes the request, or stable; an existing one is told, not moved.
with appliance(None) as path:
    note = release.choose_release(None, creating=True)
    check(note is None and release.recorded_release() == "stable", "a new install did not record stable")
with appliance(None):
    release.choose_release("canary", creating=True)
    check(release.recorded_release() == "canary", "a new install did not record the release it asked for")
with appliance("EMBABEL_RELEASE=stable\n"):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        note = release.choose_release("canary", creating=False)
    check(release.recorded_release() == "stable", "setup moved an installed appliance's release")
    check(note and "release canary" in note, f"setup did not say how to move it: {note}")
with appliance("EMBABEL_RELEASE=beta\n"):
    check(release.choose_release("beta", creating=False) is None, "asking for the release in force is not news")
with appliance(None, {"EMBABEL_RELEASE": "nightly"}):
    check(release.requested_release(None) == "nightly", "EMBABEL_RELEASE in the environment is not a request")
    check(release.requested_release("beta") == "beta", "--release does not outrank the environment")


# ── the command surface ──────────────────────────────────────────────────────
parser = build_parser()
args = parser.parse_args(["release"])
check(args.value is None and not args.allow_downgrade and not args.json, f"bare `release` has the wrong defaults: {args}")
args = parser.parse_args(["release", "0.4.0", "--allow-downgrade"])
check(args.value == "0.4.0" and args.allow_downgrade, "`release 0.4.0 --allow-downgrade` lost a part")
args = parser.parse_args(["upgrade", "--release", "nightly", "--allow-downgrade"])
check(args.release == "nightly" and args.allow_downgrade, "`upgrade --release nightly --allow-downgrade` lost a part")
args = parser.parse_args(["upgrade"])
check(args.release is None and not args.allow_downgrade, "plain `upgrade` should follow the saved release")
with open(os.path.join(ROOT, "setup.py")) as f:
    check('"--release"' in f.read(), "setup.py (./worlds.py, ./me.py, install.sh) takes no --release")

if failures:
    print("\n".join("FAIL " + f for f in failures))
    sys.exit(1)
print("ok — one release, resolved the same by the CLI and compose, kept in .env, never stepped back silently")
