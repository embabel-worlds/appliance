#!/usr/bin/env python3
"""Every compose call for an instance names that instance's project, settings and port block.

Installing a second instance once recreated the DEFAULT instance's docling and
sandbox image: the background `up` for the deferred services left out `-p`, so
compose fell back to the `name: embabel-appliance` in the mode files. Nothing here
talks to Docker — every subprocess is replaced and only the command lines are read.
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from embabel_setup import dockerlib, lifecycle, settings  # noqa: E402


def flag(cmd, name):
    """The value after a flag, or None when the flag is absent."""
    return cmd[cmd.index(name) + 1] if name in cmd else None


def check_instance(name, env_name, port_base):
    """Run each compose path as `name` and check what it would have executed."""
    project = f"embabel-{name}"
    env_path = os.path.join(settings.APPLIANCE_DIR, env_name)
    seen = []

    def run(cmd, **kwargs):
        seen.append((list(cmd), kwargs.get("env")))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def docker(*argv, **_kwargs):
        seen.append((["docker", *argv], None))
        return subprocess.CompletedProcess(argv, 0, "", "")

    settings.use_instance(name)
    with patch.object(dockerlib.subprocess, "run", side_effect=run), \
            patch.object(lifecycle.subprocess, "Popen", side_effect=run), \
            patch.object(dockerlib, "_docker", side_effect=docker), \
            patch.object(lifecycle, "_docker", side_effect=docker), \
            patch("builtins.print"):
        lifecycle.start_deferred("me")
        for verb in (("stop",), ("logs", "--tail", "50", "assistant"), ("pull",), ("up", "-d"),
                     ("rm", "--stop", "--force", "grafana"), ("config", "--format", "json")):
            dockerlib._compose("worlds", *verb, capture=True)
        dockerlib.take_everything_down()
        lifecycle.warn_if_conversion_pending()

    composes = [(cmd, env) for cmd, env in seen if cmd[:2] == ["docker", "compose"]]
    assert len(composes) == 8, f"expected 8 compose calls, saw {len(composes)}: {composes}"
    for cmd, env in composes:
        line = " ".join(cmd)
        assert flag(cmd, "-p") == project, f"{line}: project is not {project}"
        assert flag(cmd, "--env-file") == env_path, f"{line}: settings file is not {env_path}"
        assert "-f" in cmd, f"{line}: no compose file"
        assert env is not None, f"{line}: ran without the instance's environment"
        assert env["EMBABEL_INSTANCE"] == name, f"{line}: EMBABEL_INSTANCE={env['EMBABEL_INSTANCE']}"
        assert env["ASSISTANT_PORT"] == str(port_base), f"{line}: ASSISTANT_PORT={env['ASSISTANT_PORT']}"

    deferred = composes[0][0]
    assert deferred[-len(dockerlib.deferred_services()) - 2:] == ["up", "-d", *dockerlib.deferred_services()], \
        f"start_deferred ran {' '.join(deferred)}"
    assert "docker-compose-me.yml" in deferred, "start_deferred left out the mode's compose file"
    down = composes[-1][0]
    assert {"docker-compose-me.yml", "docker-compose-worlds.yml"} <= set(down), "down left out a mode file"

    # The docling check finds this instance's container by its labels, not by
    # the default project's container name.
    ps = [cmd for cmd, _ in seen if cmd[:2] == ["docker", "ps"] and "label=com.docker.compose.service=docling" in cmd]
    assert ps, "warn_if_conversion_pending did not look for docling by label"
    assert f"label=com.docker.compose.project={project}" in ps[0], f"docling lookup ran {' '.join(ps[0])}"
    assert not any(arg.startswith("name=") for arg in ps[0]), "docling lookup goes by container name"


def check_no_other_compose_commands():
    """compose_command is the only place a compose command line is put together."""
    built = re.compile(r'"docker",\s*"compose"|_docker\(\s*"compose"(?!,\s*"version")')
    sources = [*pathlib.Path("embabel_setup").glob("*.py"), pathlib.Path("worlds.py"), pathlib.Path("setup.py")]
    offenders = []
    for path in sources:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in built.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            if path.name == "dockerlib.py":
                start = text.index("def compose_command(")
                end = text.index("\ndef ", start + 1)
                if start <= match.start() < end:
                    continue
            offenders.append(f"{path}:{line}")
    assert not offenders, f"compose commands built outside dockerlib.compose_command: {offenders}"


with tempfile.TemporaryDirectory() as root:
    with open(os.path.join(root, ".env"), "w") as f:
        f.write("EMBABEL_PORT_BASE=11042\n")
    with open(os.path.join(root, ".env.fresh"), "w") as f:
        f.write("EMBABEL_PORT_BASE=11058\nEMBABEL_MONITORING=on\n")

    original_dir = settings.APPLIANCE_DIR
    original_instance = settings.instance()
    try:
        settings.APPLIANCE_DIR = root
        with patch.object(dockerlib, "github_token", return_value=None), \
                patch.object(dockerlib, "memory_env", return_value={}):
            check_instance("fresh", ".env.fresh", 11058)
            check_instance(settings.DEFAULT_INSTANCE, ".env", 11042)
    finally:
        settings.APPLIANCE_DIR = original_dir
        settings.use_instance(original_instance)

check_no_other_compose_commands()

print("ok: every compose call names its instance's project, settings and port block")
