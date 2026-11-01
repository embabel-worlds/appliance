"""`embabel realm validate`: would this realm checkout load? Answered with no appliance running.

`realm_validate` lived only behind MCP, inside a running appliance — so a realm repository
taking pull requests from forks had no way to make "does it load?" a required check: a CI
runner reaches neither. The server image carries a headless form of the same checks, as a
second command (`realm-validate`), and this runs it in a throwaway container:

    docker run --rm --entrypoint realm-validate -v <checkout>:/realms/<name>:ro <image> /realms/<name>

THE IMAGE IS THIS APPLIANCE'S, at the release it follows, so the answer is the one the server you
are about to install into would give — not some other build's opinion. A legacy EMBABEL_VERSION
pin, in the environment or .env, outranks the release exactly as it does for compose (see
release.py). `--image` overrides for a local build.

Nothing running is touched: no compose project, no volume, no network of the appliance's. The
checkout and every `--with` directory are mounted READ-ONLY; validation builds into a copy
inside the container.
"""

from __future__ import annotations
import os
import subprocess

from .release import effective_tag

ENTRYPOINT = "realm-validate"
DEFAULT_IMAGE_REPO = "ghcr.io/embabel/assistant"

# docker run's own failures, as opposed to the validator's verdict (0 passes, 1 fails, 2 could
# not validate). 125: docker could not create the container — usually an image it cannot pull.
# 127: the image has no `realm-validate` — it was built before the command existed.
DOCKER_COULD_NOT_RUN = 125
NO_SUCH_COMMAND = 127


def appliance_image(mode: str) -> str:
    """The server image this appliance runs: the tag compose resolves for it, through
    release.effective_tag. Both modes run the same image, so [mode] does not change it."""
    return f"{DEFAULT_IMAGE_REPO}:{effective_tag()}"


def docker_argv(realm: str, with_dirs: list[str], image: str, *,
                as_json: bool = False, strict: bool = False) -> list[str]:
    """The `docker run` that validates [realm] beside [with_dirs].

    Mounted under their own names, so the report names the checkout the way the person
    typed it rather than as `/realm`; each dependency under its own index, so two
    `realm-code-model` checkouts from different branches cannot land on one path.
    """
    realm = os.path.abspath(realm)
    target = f"/realms/{os.path.basename(realm.rstrip(os.sep)) or 'realm'}"
    argv = ["docker", "run", "--rm", "--entrypoint", ENTRYPOINT, "-v", f"{realm}:{target}:ro"]
    tail = [target]
    for i, dep in enumerate(with_dirs):
        dep = os.path.abspath(dep)
        mounted = f"/with/{i}/{os.path.basename(dep.rstrip(os.sep)) or 'realm'}"
        argv += ["-v", f"{dep}:{mounted}:ro"]
        tail += ["--with", mounted]
    if as_json:
        tail.append("--json")
    if strict:
        tail.append("--strict")
    return argv + [image] + tail


def run_validation(realm: str, with_dirs: list[str], image: str, *,
                   as_json: bool = False, strict: bool = False) -> int:
    """Validate, streaming the report, and exit with the validator's own status.

    The report is not captured: it is the output, and a CI log or a person wants it as the
    validator prints it. Only docker's own failures get a line from here, because those are
    the ones whose message names docker rather than the realm.
    """
    missing = [d for d in [realm, *with_dirs] if not os.path.isdir(d)]
    if missing:
        print(f"  Not a directory: {', '.join(missing)}")
        return 2
    try:
        status = subprocess.run(docker_argv(realm, with_dirs, image, as_json=as_json, strict=strict)).returncode
    except FileNotFoundError:
        print("  Docker is not installed, and the validator runs in the appliance's image.")
        return 2
    if status == NO_SUCH_COMMAND:
        print(f"  {image} predates `realm-validate`. Upgrade the appliance, or name a newer image with --image.")
        return 2
    if status == DOCKER_COULD_NOT_RUN:
        print(f"  Docker could not run {image} — is Docker running, and can it pull that image?")
        return 2
    return status
