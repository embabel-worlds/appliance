"""The project workspace: the folder of your code the appliance may read, mounted read-only.

A project key — `shop`, or a repository URL — names a project whose build and source the
server reads when a question reaches them. A repository it can fetch; a folder on this
machine it cannot see, because the server runs in a container. Naming a folder here mounts
it read-only at /workspace, for project keys alone (workspace-<mode>.yml says why that and
nothing more). One folder, not a list: point it at the directory your projects sit under.
"""
from __future__ import annotations
import os

from .colour import MIDDOT, TICK, bold, dim, warn
from .core import SetupError
from .dockerlib import WORKSPACE_VAR, configured_workspace
from .settings import embabel_command, env_file_value, set_env_var


def choose_workspace(folder: str) -> str:
    """Name [folder] as the workspace, absolute, in .env. Refuses one that does not exist —
    docker would mount an empty root-owned folder in its place — and says so for $HOME
    itself, which would put every file you own in front of the server."""
    path = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(path):
        raise SetupError(f"{path} is not a folder on this machine")
    if path == os.path.expanduser("~") or path == os.path.abspath(os.sep):
        print("  " + warn(f"{path} is everything you own. A folder your projects sit under is enough."))
    set_env_var(WORKSPACE_VAR, path, (f"# {WORKSPACE_VAR}: your code, mounted read-only at /workspace for "
                                      "project keys (embabel workspace). Empty for none.",))
    return path


def clear_workspace() -> None:
    if not env_file_value(WORKSPACE_VAR):
        print(f"  {MIDDOT} " + dim("No workspace set."))
        return
    # Emptied rather than deleted, as the embedding model is: whoever reads .env should see
    # that a folder was named and taken away.
    set_env_var(WORKSPACE_VAR, "")
    print(f"  {TICK} Workspace cleared — the server will no longer see your code.")
    print("  " + warn(f"Restart to apply: {embabel_command('down')} && {embabel_command('up')}"))


def describe_workspace() -> None:
    named = env_file_value(WORKSPACE_VAR)
    mounted = configured_workspace()
    if mounted:
        print(f"  {TICK} {bold(mounted)} is mounted read-only at /workspace for project keys.")
        print("  " + dim("A project in it is named by its folder: `shop` reads " + os.path.join(mounted, "shop") + "."))
    elif named:
        print("  " + warn(f".env names {named}, which is not a folder here, so nothing is mounted."))
    else:
        print(f"  {MIDDOT} " + dim(f"No workspace. `{embabel_command('workspace')} use <folder>` mounts one, read-only."))
