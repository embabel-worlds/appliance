"""Files in a world's data/ folder: put them there, and see what is there.

A realm reads files by a path relative to the world's `data/` — a jar or a war for the
`jar` kind, a folder of compiled classes, a `-sources.jar`, a CSV. Getting one there
used to mean `docker cp` into /data/embabel/assistant/users/<you>/<world>/data/…, which
asks somebody to know the container's name, their own account's directory and the
world's, and was called "very complicated" by the first person who tried it.

WHY HERE AND NOT AN UPLOAD ENDPOINT. The appliance has no call that writes a file into
`data/` as it is: `POST /documents/upload` INGESTS a document — parses it, chunks it,
embeds it — and a jar is not a document. A new authenticated "write any file under
data/" endpoint is a new way into the appliance's disk, with its own rules for paths,
sizes and folders to get right, for an operator who already holds the one credential
that outranks it: this machine's docker. So this is a client of docker, as `backup` and
`trust` are, and asks for no password. The rules it does keep are the ones a realm
relies on: the file lands under `data/`, never beside it, at the name it is read by.

A folder is sent as a tar on docker's stdin rather than copied with `docker cp <dir>`,
for two reasons: version-control and tool folders (`.git`, `node_modules`) are left out,
which for a checkout is most of its size; and the destination can be named, so
`target/classes` from two projects does not land in one `classes` folder.
"""

from __future__ import annotations
import os
import subprocess
import tarfile
from typing import NamedTuple

from .core import SetupError
from .dockerlib import _docker
from .samples import admin_username

# Where every account's worlds live, inside the container: <USERS_ROOT>/<user>/<world>/.
USERS_ROOT = "/data/embabel/assistant/users"
DEFAULT_WORLD = "default"

# Never what a realm reads, and for a checkout most of what is there.
SKIPPED = {".git", ".hg", ".svn", ".idea", ".gradle", "node_modules", "__pycache__", ".DS_Store"}


class Added(NamedTuple):
    """One file or folder put into data/: the path a realm reads it by, and whether it replaced one."""
    key: str
    replaced: bool
    is_dir: bool


def clean_relative(path: str, what: str) -> str:
    """A path under data/, as a realm writes it. Refused rather than normalised when it
    could point anywhere else: a typo that walks out of data/ should stop here, not land
    a file beside the world's own configuration."""
    cleaned = path.replace("\\", "/").strip("/")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if path.startswith("/") or ".." in parts:
        raise SetupError(f"{what} '{path}' must be a folder inside data/, like jars or src/shop.")
    return "/".join(parts)


def world_root(container: str, world: str | None = None) -> tuple[str, str]:
    """(the world's directory in the container, its name), for the appliance's owner.

    The owner is who set the appliance up, read from its own state file — the person
    typing this never has to know that their directory is named after their email.
    EMBABEL_USER names somebody else, as it does for `sample`.
    """
    user = os.environ.get("EMBABEL_USER") or admin_username(container)
    if not user:
        raise SetupError("Could not tell who owns this appliance — set EMBABEL_USER.")
    listed = _docker("exec", container, "ls", "-1", f"{USERS_ROOT}/{user}")
    if not listed or listed.returncode != 0:
        raise SetupError(f"{user} has no worlds on this appliance yet — sign in once, then try again.")
    worlds = sorted(w for w in listed.stdout.split() if not w.startswith("."))
    if world:
        if world not in worlds:
            raise SetupError(f"No world called '{world}'. {user} has: {', '.join(worlds) or 'none'}.")
        chosen = world
    elif DEFAULT_WORLD in worlds:
        chosen = DEFAULT_WORLD
    elif len(worlds) == 1:
        chosen = worlds[0]
    else:
        raise SetupError(f"Which world? {user} has {', '.join(worlds)} — name one with --world.")
    return f"{USERS_ROOT}/{user}/{chosen}", chosen


def _exists(container: str, path: str) -> bool:
    run = _docker("exec", container, "test", "-e", path)
    return bool(run and run.returncode == 0)


def _is_dir(container: str, path: str) -> bool:
    run = _docker("exec", container, "test", "-d", path)
    return bool(run and run.returncode == 0)


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Root-owned, as everything else under /data is, and nothing a realm does not read."""
    if any(part in SKIPPED for part in info.name.split("/")):
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def _send(container: str, source: str, target_dir: str, name: str) -> None:
    """Stream [source] into the container as target_dir/name — `docker cp -` reads a tar."""
    proc = subprocess.Popen(["docker", "cp", "-", f"{container}:{target_dir}"],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdin, mode="w|") as tar:
            tar.add(source, arcname=name, filter=_tar_filter)
        proc.stdin.close()
    except BrokenPipeError:
        pass  # docker stopped reading; its own error, read below, says why
    # Not communicate(): it flushes the stdin closed above. Docker says little, and only once it is done.
    err = proc.stderr.read()
    proc.wait()
    if proc.returncode != 0:
        raise SetupError(f"Could not copy {os.path.basename(source)} into the appliance: "
                         f"{err.decode(errors='replace').strip() or 'docker cp failed'}")


def add_data(container: str, source: str, to: str = "", name: str | None = None,
             world: str | None = None) -> Added:
    """Put a file or a folder into the world's data/<to>/, under [name] (its own, by default).

    Something already there by that name is REPLACED, not merged. Merging is what
    `docker cp` does with a folder, and for a recompiled `target/classes` it keeps the
    class files of classes that were deleted — rows for code that no longer exists.
    """
    if not os.path.exists(source):
        raise SetupError(f"{source} does not exist.")
    to = clean_relative(to, "--to")
    name = clean_relative(name or os.path.basename(os.path.normpath(os.path.abspath(source))), "--as")
    if "/" in name:
        raise SetupError(f"--as '{name}' is a name, not a path; put the folder in --to.")
    root, _ = world_root(container, world)
    target_dir = f"{root}/data/{to}" if to else f"{root}/data"
    target = f"{target_dir}/{name}"
    made = _docker("exec", container, "mkdir", "-p", target_dir)
    if not made or made.returncode != 0:
        raise SetupError(f"Could not make data/{to} in the appliance.")
    replaced = _exists(container, target)
    if replaced:
        _docker("exec", container, "rm", "-rf", target)
    _send(container, source, target_dir, name)
    return Added(f"{to}/{name}" if to else name, replaced, os.path.isdir(source))


def list_data(container: str, path: str = "", world: str | None = None) -> tuple[str, list[str]]:
    """(the world's name, what data/<path> holds), folders marked with a trailing /."""
    path = clean_relative(path, "The path")
    root, chosen = world_root(container, world)
    target = f"{root}/data/{path}" if path else f"{root}/data"
    if not _is_dir(container, target):
        if _exists(container, target):
            return chosen, [path]
        raise SetupError(f"Nothing at data/{path} in world {chosen}.")
    run = _docker("exec", container, "ls", "-1Ap", target)
    if not run or run.returncode != 0:
        raise SetupError(f"Could not list data/{path}.")
    return chosen, [line for line in run.stdout.splitlines() if line]


def sources_beside(source: str) -> str | None:
    """The `-sources.jar` next to a jar or war, when there is one: the source a realm pairs
    with it, which is worth sending in the same breath."""
    for suffix in (".jar", ".war"):
        if source.endswith(suffix) and not source.endswith("-sources.jar"):
            candidate = source[: -len(suffix)] + "-sources.jar"
            return candidate if os.path.isfile(candidate) else None
    return None
