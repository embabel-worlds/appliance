"""The `embabel` command: which file on this machine is ours, and what to tell people to type.

The name is not rare. A user had an older, unrelated program called `embabel` on PATH; the
installer rightly left it alone, and then every line it printed — `embabel up`, `embabel
uninstall` — ran that other program. Setup stalled, and there was no way to remove the
appliance that the screen admitted to. So this module answers two questions, and everything
that prints a command asks it first: is the `embabel` PATH finds the one that drives THIS
appliance, and if not, what is the full path that does.

Nothing here imports the rest of the package. settings, lifecycle and uninstall all need it,
and a module that needed them back would be a cycle.
"""

from __future__ import annotations
import os
import shlex
import shutil

# NEVER CHANGE THESE STRINGS. They are the ownership markers carried by every launcher the
# installers have ever written — install.sh's heredoc and setup.py's writer use the first,
# install.ps1's cmd shim the second — and uninstall must keep recognising those after the
# writers move or change.
SHIM_OWNERSHIP_MARKER = "Written by install.sh"
WINDOWS_SHIM_MARKER = "Written by install.ps1"


def cli_shim_paths() -> list[str]:
    """Every place setup could have written the `embabel` launcher.

    `$EMBABEL_BIN_DIR` first because an installer run with it set put the shim there, then the
    default. Both are checked rather than one, so an uninstall undoes an install that used either.
    """
    seen: list[str] = []
    for directory in (os.environ.get("EMBABEL_BIN_DIR"), os.path.join(os.path.expanduser("~"), ".local", "bin")):
        if directory and directory not in seen:
            seen.append(directory)
    return [os.path.join(d, "embabel") for d in seen]


def windows_shim_paths() -> list[str]:
    """Where install.ps1 puts its `embabel.cmd`. Empty off Windows: nothing writes one there."""
    if os.name != "nt":
        return []
    seen: list[str] = []
    local = os.environ.get("LOCALAPPDATA")
    for directory in (os.environ.get("EMBABEL_BIN_DIR"),
                      os.path.join(local, "Programs", "embabel", "bin") if local else None):
        if directory and directory not in seen:
            seen.append(directory)
    return [os.path.join(d, "embabel.cmd") for d in seen]


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def owns(path: str, checkout: str) -> bool:
    """Whether [path] is a launcher an installer wrote for the appliance in [checkout].

    NEVER treat an `embabel` as ours just because it is called `embabel`. An uninstall that
    removed somebody's other tool because the names matched would be a far worse bug than any
    this fixes. So: it must be a small file, it must carry an installer's ownership line, and it
    must name [checkout]. Anything else is somebody else's. A link straight to the checkout's own
    launcher is ours too — that is the same file, not a namesake.
    """
    try:
        if _same_path(path, os.path.join(checkout, "embabel")):
            return True
        if os.path.getsize(path) > 4096:
            return False
        # errors="replace": a sibling on PATH may be a compiled binary or another tool's UTF-8,
        # and on Windows the default codepage turned that into a UnicodeDecodeError that killed
        # uninstall. Only two ASCII strings are looked for, so decoding losses cannot matter.
        with open(path, encoding="utf-8", errors="replace") as f:
            body = f.read()
    except OSError:
        return False
    if SHIM_OWNERSHIP_MARKER not in body and WINDOWS_SHIM_MARKER not in body:
        return False
    # Case-folded on Windows only, where install.ps1 writes the directory as the user typed it.
    here = os.path.realpath(checkout)
    if os.name == "nt":
        return os.path.normcase(here) in os.path.normcase(body) or os.path.normcase(checkout) in os.path.normcase(body)
    return here in body


def foreign_embabel(checkout: str) -> str | None:
    """The `embabel` PATH would run, when it is NOT this appliance's — else None.

    None also when nothing answers to the name at all: that is "not on PATH yet", which setup
    already explains in its own words, not a conflict.
    """
    found = shutil.which("embabel")
    if found and not owns(found, checkout):
        return found
    return None


def launcher_by_path(checkout: str) -> str:
    """This appliance's launcher, as a full command line that cannot be mistaken for another.

    On Windows the checkout's `embabel` is a Python file with no extension, which no shell runs
    by name, so the cmd shim install.ps1 wrote is preferred while it exists, then the launcher
    through the Python launcher.
    """
    if os.name == "nt":
        for shim in windows_shim_paths():
            if os.path.exists(shim) and owns(shim, checkout):
                return f'"{shim}"' if " " in shim else shim
        return f'py -3 "{os.path.join(checkout, "embabel")}"'
    return shlex.quote(os.path.join(checkout, "embabel"))


def launcher(checkout: str) -> str:
    """What to print where a command starts: the bare name only when the bare name is ours.

    Not on PATH at all also means the full path. "Use the `embabel` command" in a shell that has
    no such command is an instruction that fails, and one that runs a stranger's program fails
    worse — it does something.
    """
    found = shutil.which("embabel")
    if found and owns(found, checkout):
        return "embabel"
    return launcher_by_path(checkout)
