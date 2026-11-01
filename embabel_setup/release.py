"""Which build of the server this install follows: one setting, EMBABEL_RELEASE.

THE FOUR RELEASES, and who each is for:

    stable    the release most people should run. The image tag is `latest`.
    beta      release candidates — try what is coming before it is the release.
    nightly   yesterday's main, built once a day after it passed CI.
    canary    every merge to main, as it lands. It may break.

or an exact build: a release (`0.4.1`), a release candidate (`0.5.0-rc.2`) or one main
build (`sha-24b7301`). A pin does not move until somebody moves it.

ONE SETTING, NOT A TAG. The registry spells stable `latest`, and that is the registry's
business: a person chooses what kind of build to follow, and this module turns the choice
into the tag compose pulls. Compose cannot map one word onto another, so the CLI does it and
hands compose the result as EMBABEL_IMAGE_TAG (dockerlib.compose_env), the way it hands over
the port block. A hand-run `docker compose up` that has not been given it runs `latest`,
which is stable — the default, rather than something nobody chose.

PERSISTED, UNLIKE THE OLD PIN. pins.py leaves image versions out on purpose: they move with
the checkout, over data they do not own. That still holds for the DEFAULT — an install that
says nothing follows stable, and stable moves. What is new is that following something else
is a choice a person made, so it is written into .env the moment they make it — at install,
or by `embabel release` — and every later `up` and `upgrade` keeps following it. A choice
held only in the shell that ran the installer was gone by the first upgrade.

READ FROM .env, NOT THE ENVIRONMENT, once the install has a line. The environment is how a
release is CHOSEN at install (`EMBABEL_RELEASE=beta … | sh`), and that is the only time it is
read: afterwards a variable left exported in some shell would move the server underneath an
appliance without the downgrade check `embabel release` makes — a step back onto an older
image is the one move that can leave a graph written by a newer server in front of a server
that cannot read it.

EMBABEL_VERSION IS THE LEGACY SPELLING, and still works: a tag in .env or the environment
outranks the release for the server and Grafana images, exactly as it always did, so an
install pinned that way keeps running what it ran. It takes any tag, a local build's
included, which is why the sandbox does not follow it — no `assistant-sandbox:my-build`
exists to pull. Choosing a release with `embabel release` retires the line, because two
answers to one question is how somebody ends up running the one they did not mean.
"""

from __future__ import annotations
import os
import re

from .core import SetupError
from .settings import _dotenv_assignment, embabel_command, env_file_value, env_path, set_env_var

RELEASE_VAR = "EMBABEL_RELEASE"
# The legacy pin. Outranks the release where it is set; see the header.
LEGACY_VAR = "EMBABEL_VERSION"
# What compose reads: the tag the release resolves to. Computed, never stored.
IMAGE_TAG_VAR = "EMBABEL_IMAGE_TAG"

DEFAULT_RELEASE = "stable"
# Release -> the tag every Embabel image publishes for it.
RELEASES = {"stable": "latest", "beta": "beta", "nightly": "nightly", "canary": "canary"}
# What each one is, in a sentence somebody choosing can act on. `embabel release` prints it.
ABOUT = {
    "stable": "the release most people should run",
    "beta": "release candidates — try what is coming next",
    "nightly": "yesterday's main, once it passed CI",
    "canary": "every merge to main as it lands; it may break",
}
# Other spellings of a release, accepted on the way in and never written. `latest` is what
# the registry calls stable; `edge` was the canary tag's name before it had that one, and
# the registry keeps publishing it as an alias for a while.
ALIASES = {"latest": "stable", "edge": "canary"}

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")
# What the registry publishes for one main build: `sha-` and seven hex digits.
BUILD = re.compile(r"^sha-[0-9a-f]{7}$")

# A release chosen for THIS run before it is written down — `embabel release` pulls the
# target and checks it is not a step back before .env says anything. See use_release.
_override: str | None = None


def parse_release(value: str | None) -> str:
    """The release a person asked for, in the form .env keeps — or SetupError saying why not.

    Strict, because this value becomes an image tag pulled into somebody's appliance: a typo
    should fail here, with the list of what is valid, rather than as a pull of a tag that
    does not exist. Anything else — a local build, a branch image — is still reachable,
    through EMBABEL_VERSION, which takes any tag and checks nothing.
    """
    raw = (value or "").strip()
    lowered = raw.lower()
    if lowered in RELEASES:
        return lowered
    if lowered in ALIASES:
        return ALIASES[lowered]
    # Git tags carry a `v`; image tags do not, and somebody copying one from a release page
    # means the same build.
    if re.match(r"^v\d", raw):
        raw = raw[1:]
    if SEMVER.match(raw) or BUILD.match(raw):
        return raw
    raise SetupError(
        f"'{value}' is not a release. Use one of: stable, beta, nightly, canary — or an exact "
        "build, X.Y.Z, X.Y.Z-rc.N or sha-<7 hex digits>."
    )


def release_tag(release: str) -> str:
    """The image tag a release resolves to: the named ones by table, a pin as itself."""
    return RELEASES.get(release, release)


def is_pin(release: str) -> bool:
    """Whether a release names one build rather than a stream of them."""
    return release not in RELEASES


def describe_release(release: str) -> str:
    """One line on what following this means."""
    if release in ABOUT:
        return ABOUT[release]
    if "-rc." in release:
        return "one release candidate, until you choose another"
    if BUILD.match(release):
        return "one build of main, until you choose another"
    return "one release, until you choose another"


def recorded_release() -> str | None:
    """What .env says this install follows, or None when it says nothing.

    Nothing is an answer too — stable — for an install that predates the setting. A value
    that no longer parses is reported rather than fixed: somebody wrote it, and guessing what
    they meant would be choosing their server for them.
    """
    value = (env_file_value(RELEASE_VAR) or "").strip()
    return value or None


def followed_release() -> str:
    """The release in force for this run: one chosen for the run, else .env, else stable."""
    if _override:
        return _override
    recorded = recorded_release()
    if not recorded:
        return DEFAULT_RELEASE
    try:
        return parse_release(recorded)
    except SetupError as e:
        raise SetupError(f"{env_path()} says {RELEASE_VAR}={recorded}. {e}")


def use_release(release: str | None) -> None:
    """Follow [release] for the rest of this process without writing it down — or stop.

    How `embabel release` pulls a target and compares it with what is running before .env
    commits to it: compose_env reads the override, so the pull is the pull of the target.
    """
    global _override
    _override = parse_release(release) if release else None


def legacy_pin() -> tuple[str, str] | None:
    """An EMBABEL_VERSION in force — (tag, where it was set) — or None.

    The environment first, then .env, which is how compose resolves it: the variable is
    read by compose itself, not by this module. While a release is being chosen for this
    run, neither counts — that is a choice made after the pin, and compose_env blanks it.
    """
    if _override:
        return None
    from_env = os.environ.get(LEGACY_VAR, "").strip()
    if from_env:
        return from_env, "the environment"
    from_file = (env_file_value(LEGACY_VAR) or "").strip()
    return (from_file, env_path()) if from_file else None


def effective_tag() -> str:
    """The tag the server image is pulled at: the legacy pin where there is one, else the release's."""
    pinned = legacy_pin()
    return pinned[0] if pinned else release_tag(followed_release())


def version_pin_conflict(mode: str | None = None) -> tuple[str, str] | None:
    """A legacy EMBABEL_VERSION in .env naming a different tag from the release this install follows.

    The pin wins — correctly, an explicit tag should beat a stream — but silently, which is
    the problem: `embabel release` says beta, the server runs whatever the pin left behind
    from some earlier experiment, and nothing on screen says which half was which.

    Returns (pinned, the release and its tag) when they disagree; None when there is no pin
    in .env or it names the release's own tag. The environment's pin is not reported: that
    is a choice made for the run, by whoever exported it. [mode] is accepted for the callers
    that ask per mode; both modes run the same image.
    """
    pinned = (env_file_value(LEGACY_VAR) or "").strip()
    try:
        release = followed_release()
    except SetupError:
        return None
    expected = release_tag(release)
    if not pinned or pinned == expected:
        return None
    return pinned, (expected if expected == release else f"{release} (:{expected})")


def reported_release() -> str:
    """What the server is told it follows. The legacy pin's tag where one wins, because that
    is what is running; the release otherwise."""
    pinned = legacy_pin()
    return pinned[0] if pinned else followed_release()


def compose_release_env() -> dict[str, str]:
    """The variables compose needs to resolve this install's images. See dockerlib.compose_env.

    EMBABEL_VERSION is passed through resolved — and blank while a release is being chosen,
    since the process environment outranks the --env-file for compose, and the pin in .env
    would otherwise win the very pull that is meant to replace it.
    """
    pinned = legacy_pin()
    return {
        IMAGE_TAG_VAR: release_tag(followed_release()),
        LEGACY_VAR: pinned[0] if pinned else "",
        RELEASE_VAR: reported_release(),
    }


def remember_release(release: str) -> list[str]:
    """Write the release into .env, and retire a legacy pin that would outrank it.

    Returns what it changed, for the caller to say. The pin is commented out rather than
    deleted: an operator reading .env afterwards sees what was there and what replaced it.
    """
    changed = []
    if recorded_release() != release:
        set_env_var(RELEASE_VAR, release, (
            "# Which build of the server this appliance follows: stable, beta, nightly, canary,",
            "# or one exact build (X.Y.Z, X.Y.Z-rc.N, sha-xxxxxxx). `embabel release` changes it,",
            "# checking first that the change is not a step back onto an older server.",
        ))
        changed.append(f"{RELEASE_VAR}={release}")
    retired = _retire_legacy_pin(release)
    if retired:
        changed.append(f"{LEGACY_VAR}={retired} commented out")
    return changed


def requested_release(flag: str | None) -> str | None:
    """The release asked for at setup — `--release`, else EMBABEL_RELEASE in the environment —
    parsed; None when nobody asked. SetupError for one that is not a release."""
    asked = (flag or os.environ.get(RELEASE_VAR, "")).strip()
    return parse_release(asked) if asked else None


def choose_release(requested: str | None, creating: bool) -> str | None:
    """Setup's half: write the release a NEW install follows. Returns a note to print, or None.

    NO QUESTION, EVER. Setup is what `curl … | sh` runs, and on that path stdin is the pipe
    the shell is reading the installer from (see setup.hand_over_to_console): a prompt there
    hangs or answers itself. So the release is a flag or a variable, stable when neither says,
    and written down before the first pull — it decides which images that pull fetches.

    An install that exists keeps what it follows. Setup is also `embabel up`, run by scripts
    and cron, and changing the server underneath a running appliance belongs to `embabel
    release`, which checks first that the change is not a step back. So a different request
    here is answered with that command rather than acted on.
    """
    if creating:
        remember_release(requested or DEFAULT_RELEASE)
        return None
    if not requested:
        return None
    try:
        following = followed_release()
    except SetupError:
        following = None
    if requested == following:
        return None
    return (f"This appliance follows {following or 'a release .env cannot name'}, and setup does not "
            f"change an installed appliance's release.\n"
            f"    `{embabel_command('release', requested)}` moves it to {requested}, checking first "
            "that it is not older than what runs now.")


def _retire_legacy_pin(release: str) -> str | None:
    """Comment out every EMBABEL_VERSION line in .env. Returns the value that was in force."""
    if not os.path.exists(env_path()):
        return None
    with open(env_path()) as f:
        lines = f.read().splitlines()
    found = None
    for i, line in enumerate(lines):
        parsed = _dotenv_assignment(line)
        if parsed and parsed[0] == LEGACY_VAR:
            found = parsed[1] or found
            lines[i] = f"# {line.strip()}   (replaced by {RELEASE_VAR}={release})"
    if found is None:
        return None
    with open(env_path(), "w") as f:
        f.write("\n".join(lines) + "\n")
    return found


# ── which build is newer ─────────────────────────────────────────────────────

def semantic(version: str | None) -> tuple[int, int, int, int, int] | None:
    """X.Y.Z or X.Y.Z-rc.N as something that sorts: a release after every one of its candidates."""
    found = SEMVER.match((version or "").strip().lstrip("v"))
    if not found:
        return None
    major, minor, patch, rc = found.groups()
    return int(major), int(minor), int(patch), 0 if rc else 1, int(rc or 0)


_TIMESTAMP = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(\.\d+)?")


def instant(created: str | None) -> tuple | None:
    """An image's build time as something that sorts.

    Parsed rather than compared as text: the label carries three digits of fraction and
    Docker's own field nine, and `…39Z` against `…39.5Z` compares `Z` with `.` as characters,
    which is not time. UTC by convention — both are written in Z.
    """
    found = _TIMESTAMP.match((created or "").strip())
    if not found:
        return None
    *whole, fraction = found.groups()
    return tuple(int(x) for x in whole) + (float(fraction or 0),)


def older(target: dict, current: dict) -> bool | None:
    """Whether [target] is an older build than [current] — None when nothing says.

    Each is what upgrade.image_facts reads: the tag, the version label, the build time, the
    image id. The same image is never older than itself. Two release numbers decide it when
    both have one — that is the order releases are MEANT to go in, and a patch to an old
    line built yesterday is still behind the newer line. Otherwise the build time decides,
    which is the only order a stream like canary has.
    """
    if target.get("id") and target.get("id") == current.get("id"):
        return False
    versions = [semantic(facts.get("tag")) or semantic(facts.get("version")) for facts in (target, current)]
    if all(versions):
        return versions[0] < versions[1]
    times = [instant(facts.get("created")) for facts in (target, current)]
    if all(times):
        return times[0] < times[1]
    return None
