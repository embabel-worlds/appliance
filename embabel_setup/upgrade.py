"""Onto the latest published build: the checkout as well as the images.

Nothing here builds. The compose files are pull-only by design, so this verb
lands on what the registry publishes and says so when that turns out to be older
than a local build it replaced.
"""
import os
import subprocess

from .colour import dim
from .core import APPLIANCE_DIR, SetupError
from .dockerlib import _compose, _docker, find_mode_container
from .versions import image_identity, mode_image

def _head() -> str | None:
    run = subprocess.run(["git", "-C", APPLIANCE_DIR, "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    return run.stdout.strip() if run.returncode == 0 else None
def pull_checkout() -> tuple[bool, str]:
    """Fast-forward the checkout. Returns (moved, what to tell the operator)."""
    if not os.path.isdir(os.path.join(APPLIANCE_DIR, ".git")):
        return False, "not a git checkout — images only"
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
def upgrade(mode: str) -> dict:
    """Latest checkout, latest published images, containers actually running them.

    The digests are read on both sides rather than trusting `up -d` to have done
    something: "pulled" and "the container is now on it" are different claims,
    and only the second one is the thing anybody wanted.
    """
    notes = []
    _moved, note = pull_checkout()
    notes.append(note)

    # Read BEFORE the pull, because the pull is what moves the tag underneath it.
    before = image_identity(mode_image(mode) or "")

    if _compose(mode, "pull").returncode != 0:
        raise SetupError("docker compose pull failed — see the output above.")
    if _compose(mode, "up", "-d").returncode != 0:
        raise SetupError("docker compose up failed — see the output above.")

    after = image_identity(mode_image(mode) or "")
    if before.get("digest") and after.get("digest") and before["digest"] != after["digest"]:
        notes.append(f"server image {before['digest'][7:19]} → {after['digest'][7:19]}")
        # A locally-built image can be NEWER than what the registry serves,
        # because snapshot tags publish on release rather than on every push.
        # Landing on the published build is exactly what this verb is for, so
        # this is a NOTE and not a refusal — but a local build vanishing without
        # a word is the kind of surprise people spend an afternoon on.
        if before.get("created") and after.get("created") and after["created"] < before["created"]:
            notes.append("NOTE: the published image is OLDER than the one that was running.\n"
                         "    If that was your own build, this replaced it — which is what\n"
                         "    `upgrade` means. Rebuild it if you wanted it back.")
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
                     "    `embabel down` then `embabel up` will land it.")
    return {"mode": mode, "notes": notes, "digest": after.get("digest")}
def _same_image(container: str, image: str | None) -> bool:
    """Is this container running THAT image, by local image id rather than by tag?
    A tag is a moving name; two containers can both say `:0.2.0-SNAPSHOT` and be
    different builds, which is the whole failure this check exists to catch."""
    if not image:
        return True
    running = _docker("inspect", container, "--format", "{{.Image}}", timeout=15)
    wanted = _docker("image", "inspect", image, "--format", "{{.Id}}", timeout=15)
    if not running or not wanted or running.returncode != 0 or wanted.returncode != 0:
        return True  # cannot tell; do not cry wolf
    return running.stdout.strip() == wanted.stdout.strip()
