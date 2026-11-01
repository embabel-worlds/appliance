"""Onto the newest published build of the release this install follows: the checkout
as well as the images. Or onto another release, which is the same move with a check.

Nothing here builds. The compose files are pull-only by design, so this verb
lands on what the registry publishes — and refuses, unless told, when that turns
out to be OLDER than what is running (see upgrade).
"""

from __future__ import annotations
import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request

from .colour import dim
from .core import APPLIANCE_DIR, ME_APP_DIR, SetupError
from .settings import embabel_command, env_file, source_ref, source_repo

from .dockerlib import _compose, _docker, find_mode_container, images_for, retire_monitoring, retire_sql_sidecar
from .release import (DEFAULT_RELEASE, describe_release, followed_release, legacy_pin, older, parse_release,
                      recorded_release, release_tag, remember_release, use_release)
from .versions import composed_image, image_facts, image_identity, mode_image, running_image
from .bolt import ensure_public_host
from .pins import pin_before_start

def _head() -> str | None:
    run = subprocess.run(["git", "-C", APPLIANCE_DIR, "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    return run.stdout.strip() if run.returncode == 0 else None
# What `curl … | sh` installs from, and what an upgrade of that install re-reads.
#
# READ PER CALL, not once at import: the ref lives in .env for an install that was made
# from a branch, and a module constant captured whatever the shell happened to export —
# so an upgrade run without that variable quietly pulled main over somebody's branch.
# [source_ref] and [source_repo] look at the environment first, then .env, then the default.

# What "the CLI" is, for deciding whether a refresh actually changed anything the
# operator would notice. Not the whole tree: docs and copy move constantly and
# saying "42 files" tells nobody whether the command they just ran is different.
CLI_PATHS = ("setup.py", "embabel", "embabel_setup", "install.sh")


def _cli_fingerprint() -> str:
    """One hash over the files that make up the command, so a refresh can say
    whether it moved rather than claiming it did."""
    digest = hashlib.sha256()
    for name in CLI_PATHS:
        path = os.path.join(APPLIANCE_DIR, name)
        files = []
        if os.path.isdir(path):
            for root, _dirs, entries in os.walk(path):
                files += [os.path.join(root, e) for e in sorted(entries) if e.endswith(".py")]
        elif os.path.exists(path):
            files = [path]
        for f in sorted(files):
            try:
                with open(f, "rb") as fh:
                    digest.update(fh.read())
            except OSError:
                continue
    return digest.hexdigest()


def refresh_from_tarball() -> tuple[bool, str]:
    """Re-download the appliance over itself, for an install that came from curl.

    A CURL INSTALL HAD NO WAY TO UPDATE ITS OWN CLI. install.sh extracts a tarball,
    so there is no `.git` to fast-forward, and upgrade said "images only" and moved
    on — which meant every fix to setup.py or the `embabel` command was unreachable
    to exactly the people who installed the recommended way. They got new images
    driven by an old installer, indefinitely.

    Extraction only ever writes files the tarball contains, so `.env`, realms/ and
    anything else local survives untouched — none of them are in it.
    """
    repo, ref = source_repo(), source_ref()
    url = f"https://codeload.github.com/{repo}/tar.gz/{ref}"
    before = _cli_fingerprint()
    workspace = tempfile.mkdtemp(prefix="embabel-upgrade-")
    tarball = os.path.join(workspace, "appliance.tar.gz")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            if response.status != 200:
                return False, f"checkout NOT updated (GitHub answered {response.status})"
            with open(tarball, "wb") as f:
                shutil.copyfileobj(response, f)
        run = subprocess.run(
            ["tar", "xzf", tarball, "-C", APPLIANCE_DIR, "--strip-components=1"],
            capture_output=True, text=True, timeout=180,
        )
        if run.returncode != 0:
            return False, f"checkout NOT updated ({(run.stderr or '').strip()[:120]})"
    except Exception as e:
        # Never fatal: the images below are the bigger half of an upgrade, and a
        # network that failed here will have failed there too, more loudly.
        return False, f"checkout NOT updated ({e})"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    if _cli_fingerprint() == before:
        return False, f"checkout already current ({repo}@{ref})"
    return True, (f"checkout refreshed from {repo}@{ref}"
                  "\n    the `embabel` command itself changed — this run is still the old one")


def pull_checkout() -> tuple[bool, str]:
    """Fast-forward the checkout. Returns (moved, what to tell the operator)."""
    if not os.path.isdir(os.path.join(APPLIANCE_DIR, ".git")):
        return refresh_from_tarball()
    before = _head()
    run = subprocess.run(["git", "-C", APPLIANCE_DIR, "pull", "--ff-only"],
                         capture_output=True, text=True, timeout=180)
    if run.returncode != 0:
        # The two ordinary reasons, named: local edits, or no upstream. Either
        # way this is a note, not a failure — the images below still move.
        reason = (run.stderr or run.stdout).strip().splitlines()
        return False, f"checkout NOT updated ({reason[-1] if reason else 'git declined'})"
    after = _head()
    if before and after and before != after:
        changed = subprocess.run(["git", "-C", APPLIANCE_DIR, "diff", "--name-only", f"{before}..{after}"],
                                 capture_output=True, text=True)
        files = changed.stdout.split() if changed.returncode == 0 else []
        note = f"checkout updated {before[:7]} → {after[:7]} ({len(files)} file(s))"
        if any(f.startswith(f"{ME_APP_DIR}/") for f in files):
            # dist/ is built from these, and nothing in the run path rebuilds it.
            note += "\n    the Me app changed — rebuild it: npm --prefix me-app run build"
        return True, note
    return False, "checkout already current"


class DowngradeRefused(SetupError):
    """The build a release resolves to is older than the one running, and nobody said
    --allow-downgrade. The appliance is left exactly as it was. [current] and [arriving]
    are versions.image_facts dicts; the message is downgrade_message's."""

    def __init__(self, release: str, current: dict, arriving: dict):
        super().__init__(f"{release} is older than the running server")
        self.release, self.current, self.arriving = release, current, arriving


def upgrade(mode: str, release: str | None = None, allow_downgrade: bool = False) -> dict:
    """Latest checkout, the newest build of the release this install follows, and the
    containers actually running it. [release] moves the install to another release first.

    The digests are read on both sides rather than trusting `up -d` to have done
    something: "pulled" and "the container is now on it" are different claims,
    and only the second one is the thing anybody wanted.

    NEVER BACKWARDS WITHOUT BEING TOLD. A release change can land on an OLDER server —
    canary to stable, a pin to last month's release — and an older server may not read
    what the newer one wrote: its schema, its stores, the shape of a world it has never
    seen. So the target is pulled and compared with the running build BEFORE anything is
    recreated or written to .env, and an older one stops here with both named, unless
    [allow_downgrade]. Same build or newer goes ahead without a word.
    """
    # Before anything moves, so a typo costs nothing.
    target = parse_release(release) if release else followed_release()
    # The target, for this run only, until it has been checked: compose_env reads it, so
    # every compose call below — the pull above all — is about the target, while .env
    # still says what it said.
    if release:
        use_release(target)
    notes = []
    _moved, note = pull_checkout()
    notes.append(note)

    # Read BEFORE the pull, because the pull is what moves the tag underneath it.
    current = running_image(mode)
    before = image_identity(mode_image(mode) or "")
    held = _image_ids(images_for(mode))

    if _compose(mode, "pull").returncode != 0:
        use_release(None)
        hint = ""
        if release and target != DEFAULT_RELEASE:
            # The likeliest reason a release that parses cannot be pulled: it is not published
            # (yet). Said, because "pull failed" alone reads like a broken appliance.
            hint = (f"\n  If {target} is not published yet, nothing changed: this appliance still follows "
                    f"what it did.\n  `{embabel_command('release', DEFAULT_RELEASE)}` follows the release.")
        raise SetupError("docker compose pull failed — see the output above." + hint)
    arriving = image_facts(composed_image(mode))
    stepping_back = bool(current and arriving) and older(arriving, current) is True
    if stepping_back and not allow_downgrade:
        use_release(None)
        _keep_running_tag(current, arriving)
        raise DowngradeRefused(target, current, arriving)

    if release:
        changed = remember_release(target)
        if changed:
            notes.append(f"now following {target} ({describe_release(target)}) — "
                         + ", ".join(changed) + f" in {env_file()}")
    # The images are new and nothing has been recreated yet — the moment to pin a row
    # that waits on the image, before the first boot on it (pins.pin_before_start).
    pin_before_start(mode)
    # And the name Bolt is advertised by, which compose reads from .env (see bolt.py).
    ensure_public_host(mode)
    if _compose(mode, "up", "-d").returncode != 0:
        raise SetupError("docker compose up failed — see the output above.")
    retire_monitoring(mode)
    retire_sql_sidecar()
    reclaimed = _remove_superseded(held, _image_ids(images_for(mode)))
    if reclaimed:
        notes.append(reclaimed)

    after = image_identity(mode_image(mode) or "")
    if before.get("digest") and after.get("digest") and before["digest"] != after["digest"]:
        notes.append(f"server image {before['digest'][7:19]} → {after['digest'][7:19]}")
        if stepping_back:
            # Asked for, and still worth a line where it will be read afterwards.
            notes.append(f"NOTE: stepped back onto an OLDER server ({_named(arriving)}), as --allow-downgrade asked.\n"
                         f"    If it cannot read what the newer one wrote, `{embabel_command('restore')}` "
                         "puts the backup back.")
        else:
            notes.append("give the server a moment to come back")
    elif after.get("digest"):
        notes.append("server image already current")

    # `up -d` recreates a container whose image changed, but "pulled" and "the
    # container is now running it" are different claims and only the second is
    # what anybody wanted. So check, rather than assume.
    container = find_mode_container(mode)
    if container and not _same_image(container, mode_image(mode)):
        notes.append(f"WARNING: {container} is NOT running the pulled image.\n"
                     f"    `{embabel_command('down')}` then `{embabel_command('up')}` will land it.")
    return {"mode": mode, "release": followed_release(), "notes": notes, "digest": after.get("digest")}


def _keep_running_tag(current: dict, arriving: dict) -> None:
    """Point the tag back at the running build when the refused one arrived under the SAME name.

    A pull moves a tag whether or not anything is recreated on it. When the running server
    was created from `:latest` and the registry's `:latest` turned out older — a local build,
    or a release withdrawn — refusing the upgrade left the name on the older image, and the
    next `embabel up` would have started it after all. Retagging the running image keeps
    "kept the current one" true past this command.
    """
    if current.get("id") and current.get("image") and current.get("image") == arriving.get("image"):
        _docker("tag", current["id"], current["image"], timeout=30)


def _named(facts: dict) -> str:
    """One build, the way a person compares two: tag, what it calls itself, when it was built."""
    parts = [facts.get("image") or "?"]
    if facts.get("version") and facts.get("version") != facts.get("tag"):
        parts.append(facts["version"])
    if facts.get("created"):
        parts.append("built " + facts["created"][:16].replace("T", " "))
    return ", ".join(parts)


def downgrade_message(refused: DowngradeRefused, retry: str) -> str:
    """What `embabel release` and `embabel upgrade` say when they keep the current server.
    [retry] is the command that was run, which is the command to run with the flag."""
    return "\n".join([
        f"{refused.release} is OLDER than the server this appliance is running:",
        "",
        f"    running   {_named(refused.current)}",
        f"    {refused.release:<9} {_named(refused.arriving)}",
        "",
        "  Nothing changed: the running server stays, and .env still follows what it followed.",
        "  An older server may not read what a newer one wrote. To step back anyway, take a",
        "  backup first, so there is a way forward again:",
        "",
        f"    {embabel_command('backup')}",
        f"    {retry} --allow-downgrade",
    ])


def release_report(mode: str) -> dict:
    """What `embabel release` shows: what this install follows, and what is actually running.

    The two can differ, and the difference is the point: the release is a stream, the
    running build is wherever the last upgrade left it, and a legacy pin can outrank both.
    """
    recorded = recorded_release()
    try:
        followed, problem = followed_release(), None
    except SetupError as e:
        followed, problem = None, str(e)
    pin = legacy_pin()
    return {
        "mode": mode,
        "release": followed,
        "recorded": recorded,
        "about": describe_release(followed) if followed else None,
        "tag": release_tag(followed) if followed else None,
        "problem": problem,
        # A legacy EMBABEL_VERSION that outranks the release, and where it was set.
        "legacyPin": {"tag": pin[0], "from": pin[1]} if pin else None,
        "running": running_image(mode),
    }
def _same_image(container: str, image: str | None) -> bool:
    """Is this container running THAT image, by local image id rather than by tag?
    A tag is a moving name; two containers can both say `:latest` and be
    different builds, which is the whole failure this check exists to catch."""
    if not image:
        return True
    running = _docker("inspect", container, "--format", "{{.Image}}", timeout=15)
    wanted = _docker("image", "inspect", image, "--format", "{{.Id}}", timeout=15)
    if not running or not wanted or running.returncode != 0 or wanted.returncode != 0:
        return True  # cannot tell; do not cry wolf
    return running.stdout.strip() == wanted.stdout.strip()


def _image_ids(images: list[str]) -> dict[str, str]:
    """Local image id behind each reference the mode runs. A reference not pulled
    yet has no id and is simply absent — there is nothing of it to supersede."""
    ids = {}
    for ref in images:
        run = _docker("image", "inspect", ref, "--format", "{{.Id}}", timeout=15)
        if run and run.returncode == 0 and run.stdout.strip():
            ids[ref] = run.stdout.strip()
    return ids


def _remove_superseded(before: dict[str, str], after: dict[str, str]) -> str | None:
    """Delete the images this upgrade just moved the appliance's tags off.

    EVERY UPGRADE OF A `:latest` INSTALL LEFT ITS PREVIOUS IMAGES BEHIND — the server,
    the sandbox and Grafana, about 4.5 GB a time — and nothing ever took them back, so
    an appliance that kept itself current slowly filled the Docker disk.

    Only those ids, and only once nothing else wants them: no tag left on the image —
    a local build tagged `:memory` as well as `:latest` keeps its `:memory` — and no
    container, running or stopped, made from it. Both are checked here rather than
    left to `docker image rm` to refuse, because under the containerd image store it
    does not: removing by id drops every tag the image still carries. This is why the
    reclaim lives in `upgrade` rather than `prune`, which does not touch images (see
    `cmd_prune`): here the appliance knows which images were its own a minute ago.
    """
    gone = {before[ref] for ref in before if after.get(ref) and after[ref] != before[ref]}
    # A count and not a byte total: an image's size includes layers it shares with
    # its successor, so summing them would claim space that was never freed.
    removed = 0
    for image_id in sorted(gone):
        if not _unwanted(image_id):
            continue
        run = _docker("image", "rm", image_id, timeout=60)
        if run and run.returncode == 0:
            removed += 1
    return f"removed {removed} superseded image(s)" if removed else None


def _unwanted(image_id: str) -> bool:
    """No tag names it and no container uses it. Anything unreadable counts as wanted."""
    tags = _docker("image", "inspect", image_id, "--format", "{{len .RepoTags}}", timeout=15)
    if not tags or tags.returncode != 0 or tags.stdout.strip() != "0":
        return False
    users = _docker("ps", "-aq", "--filter", f"ancestor={image_id}", timeout=15)
    return bool(users) and users.returncode == 0 and not users.stdout.strip()
