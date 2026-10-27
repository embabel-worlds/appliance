#!/usr/bin/env python3
"""Your code reaches the server only when you name it, only read-only, and only for project keys.

WHY THIS EXISTS. `embabel workspace use ~/code` puts a folder of the owner's code in front of
the server. Three things must stay true for that to be safe to offer: nothing is mounted until a
folder is named (the default composition is untouched), what is mounted cannot be written, and
the server is told it is the PROJECT workspace — the one setting only project keys read — rather
than a trusted root every local read would follow. And a folder that has gone must not be
mounted, because docker would create an empty root-owned one in its place.

Nothing here talks to Docker: the overlays are read as YAML and the command line is inspected.

    python3 scripts/check-workspace.py
"""
import os
import sys
import tempfile
from unittest.mock import patch

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from embabel_setup import dockerlib, settings, workspace  # noqa: E402
from embabel_setup.core import SetupError  # noqa: E402

failures = []


def check(ok: bool, what: str) -> None:
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def overlays(mode: str) -> list[str]:
    return [a for a in dockerlib.compose_command(mode, "config") if a.endswith(".yml")]


code = tempfile.mkdtemp()
env = os.path.join(tempfile.mkdtemp(), ".env")
with patch.object(settings, "env_path", lambda name=None: env), patch.dict(os.environ, {}, clear=False):
    os.environ.pop(dockerlib.WORKSPACE_VAR, None)
    for mode in ("worlds", "me"):
        check(dockerlib.workspace_file(mode) not in overlays(mode), f"{mode}: nothing mounted until a folder is named")

    os.environ[dockerlib.WORKSPACE_VAR] = code
    for mode in ("worlds", "me"):
        check(dockerlib.workspace_file(mode) in overlays(mode), f"{mode}: a named folder composes the overlay in")
        with open(dockerlib.workspace_file(mode)) as f:
            services = yaml.safe_load(f)["services"]
        (service,) = services.values()
        (mount,) = service["volumes"]
        check(mount["target"] == "/workspace" and mount["read_only"] is True, f"{mode}: mounted read-only at /workspace")
        check(service["environment"] == ["ASSISTANT_VIRTUALCYPHER_PROJECT_WORKSPACE=/workspace"],
              f"{mode}: the server is told it is the project workspace, and nothing else")

    os.environ[dockerlib.WORKSPACE_VAR] = os.path.join(code, "gone")
    check(dockerlib.workspace_file("worlds") not in overlays("worlds"), "a folder that has gone is not mounted")
    os.environ.pop(dockerlib.WORKSPACE_VAR)

    try:
        workspace.choose_workspace(os.path.join(code, "gone"))
        check(False, "a folder that does not exist is refused")
    except SetupError:
        check(True, "a folder that does not exist is refused")
    chosen = workspace.choose_workspace(code)
    with open(env) as f:
        written = f.read()
    check(chosen == os.path.abspath(code) and f"{dockerlib.WORKSPACE_VAR}={chosen}" in written, ".env names the folder, absolute")
    check(f"# {dockerlib.WORKSPACE_VAR}:" in written, ".env says what the line is for")
    workspace.clear_workspace()
    with open(env) as f:
        check(f"{dockerlib.WORKSPACE_VAR}=\n" in f.read(), "off empties the line rather than deleting it")

print()
if failures:
    print(f"{len(failures)} failed")
    sys.exit(1)
print("all passed")
