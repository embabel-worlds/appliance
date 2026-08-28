"""A sandbox of your own: build a Dockerfile, and point the appliance at it.

The shipped sandbox carries the runtimes code-mode executes and nothing else, because
every appliance downloads it before anybody has run a line of code. That is the right
default and the wrong answer for somebody whose work needs a JDK, or R, or a private
package index.

So the appliance builds one. Put a Dockerfile at `sandbox/Dockerfile`, run
`embabel sandbox build`, and the tag it produces is written into .env as
EMBABEL_SANDBOX_IMAGE — the one name that moves both the compose pre-pull and the
container the app launches for a session.

EXTEND, DO NOT REWRITE. The example Dockerfile starts `FROM` the shipped image, so
adding a toolchain is a line or two rather than a fork that goes stale the moment the
shipped one gains something. A rewrite is still yours to make; it is just not the
thing this encourages.
"""
import os
import subprocess

from .colour import MIDDOT, TICK, bold, dim, warn
from .core import APPLIANCE_DIR, SetupError
from .dockerlib import _docker
from .settings import env_file_value, set_env_var

# Where a custom Dockerfile lives by default, and what its result is called. The tag is
# local and unqualified on purpose: it is never pushed anywhere, and a name that looks
# like a registry path invites somebody to try.
SANDBOX_DIR = "sandbox"
SANDBOX_DOCKERFILE = os.path.join(SANDBOX_DIR, "Dockerfile")
LOCAL_TAG = "embabel-sandbox:local"
IMAGE_VAR = "EMBABEL_SANDBOX_IMAGE"


def sandbox_dockerfile(path: str | None = None) -> str:
    return path or os.path.join(APPLIANCE_DIR, SANDBOX_DOCKERFILE)


def effective_image() -> tuple[str, str]:
    """The sandbox image in force, and where that was decided."""
    from_env = os.environ.get(IMAGE_VAR)
    if from_env:
        return from_env, "the environment"
    from_file = env_file_value(IMAGE_VAR)
    if from_file:
        return from_file, ".env"
    return "ghcr.io/embabel/assistant-sandbox:latest", "the shipped default"


def build_sandbox(dockerfile: str | None = None, tag: str = LOCAL_TAG,
                  no_cache: bool = False) -> str:
    """Build a custom sandbox and make it the one this appliance uses.

    The build context is the Dockerfile's own directory rather than the checkout root:
    a sandbox needs its own files and has no business reading the appliance's, and a
    context of everything would send the whole install to the daemon on every build.
    """
    path = sandbox_dockerfile(dockerfile)
    if not os.path.exists(path):
        raise SetupError(
            f"No Dockerfile at {path}.\n"
            f"  Start from the example:  cp {SANDBOX_DIR}/Dockerfile.example {SANDBOX_DOCKERFILE}"
        )
    context = os.path.dirname(os.path.abspath(path))
    print(f"  Building {bold(tag)} from {dim(path)}")
    print("  " + dim("This is your image; nothing is pushed anywhere."))

    argv = ["build", "-f", path, "-t", tag]
    if no_cache:
        argv.append("--no-cache")
    argv.append(context)
    # Inherited stdio, not captured: a docker build is minutes of output somebody
    # needs to watch, and swallowing it to re-print a summary would be the one thing
    # worse than no progress at all.
    try:
        run = subprocess.run(["docker", *argv], timeout=3600)
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        raise SetupError(f"Could not run docker build: {e}")
    if run.returncode != 0:
        raise SetupError("The sandbox build failed — the output above says why.")

    # Written to .env so it survives a restart and reaches BOTH halves: the pre-pull in
    # infra.yml and the image the app launches. Setting one without the other pulls a
    # sandbox nobody uses and then fetches a different one mid-chat.
    set_env_var(IMAGE_VAR, tag, why=(
        "# The code-mode sandbox image. Written by `embabel sandbox build`; the compose",
        "# pre-pull and the app both read it. Delete this line to go back to the shipped one.",
    ))
    return tag


def describe_sandbox() -> None:
    image, source = effective_image()
    print(f"  Sandbox image: {bold(image)}   {dim('(' + source + ')')}")

    # `docker images`, not `image inspect --format {{.Size}}`: on the containerd image
    # store those two disagree — inspect reported 0.67GB for an image `docker images`
    # called 2.61GB — and the number here should be the one the operator sees from
    # their own docker, not a second opinion four times smaller.
    local = _docker("images", "--format", "{{.Size}}", image)
    if local and local.returncode == 0 and local.stdout.strip():
        print(f"     {dim(local.stdout.strip().splitlines()[0] + ' on this machine')}")
    else:
        print(f"     {MIDDOT} " + dim("not pulled or built here yet"))

    path = sandbox_dockerfile()
    if os.path.exists(path):
        print(f"     {dim('built from ' + path)}")
    else:
        print("     " + dim(f"no {SANDBOX_DOCKERFILE} — using an image as published"))
        print("     " + dim(f"to customise:  cp {SANDBOX_DIR}/Dockerfile.example {SANDBOX_DOCKERFILE}"))


def reset_sandbox() -> None:
    """Back to the shipped image."""
    if not env_file_value(IMAGE_VAR):
        print(f"  {MIDDOT} " + dim("Already using the shipped image."))
        return
    # Set to empty rather than deleting the line: the app reads blank as "the shipped
    # one", and an operator scanning .env should see that the choice was made and undone
    # rather than wonder whether it was ever there.
    set_env_var(IMAGE_VAR, "")
    print(f"  {TICK} Back to the shipped sandbox image.")
    print("  " + warn("Restart to apply: embabel down && embabel up"))
