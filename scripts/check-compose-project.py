#!/usr/bin/env python3
"""Every compose call for an instance names that instance's project, settings and port block,
and so does every command the appliance prints for somebody to run.

Installing a second instance once recreated the DEFAULT instance's docling and
sandbox image: the background `up` for the deferred services left out `-p`, so
compose fell back to the `name: embabel-appliance` in the mode files. Nothing here
talks to Docker — every subprocess is replaced and only the command lines are read.
"""
import os
import pathlib
import ast
import io
import re
import subprocess
import sys
import tempfile
import tokenize
import urllib.error
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from embabel_setup import dockerlib, lifecycle, settings, steps  # noqa: E402
from embabel_setup.core import SetupError, Unreachable  # noqa: E402


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


def collect_hints():
    """Every printed hint that suggests a command, as text."""
    hints = [settings.resume_command(), str(steps._timed_out("http://localhost:1", 5, None))]
    printed = []
    with patch.object(lifecycle.subprocess, "Popen"), \
            patch("builtins.print", side_effect=lambda *a, **_k: printed.append(" ".join(map(str, a)))):
        lifecycle.start_deferred("me")
    hints.append("\n".join(printed))
    for failure in (urllib.error.URLError(ConnectionRefusedError("refused")), ConnectionResetError("reset")):
        with patch.object(steps.urllib.request, "urlopen", side_effect=failure):
            try:
                steps.call("http://localhost:1", "/status", "token")
            except Unreachable as e:
                hints.append(str(e))
    with patch.object(steps, "probe", return_value="unreachable"), \
            patch.object(steps, "boot_failure", return_value=None), patch.object(steps.STATUS, "stop"):
        try:
            steps.discover_token("http://localhost:1", None, None)
        except SetupError as e:
            hints.append(str(e))
    return hints


def check_hints(name):
    """A hint names this instance and never suggests raw docker compose: the
    `embabel` command does that with the settings `up` uses. Before the command
    is on PATH, the hint is the checkout's own launcher."""
    default = name == settings.DEFAULT_INSTANCE
    settings.use_instance(name)
    for installed in ("/usr/local/bin/embabel", None):
        with patch("shutil.which", return_value=installed):
            hints = collect_hints()
        assert len(hints) == 6, f"expected 6 hints, collected {len(hints)}: {hints}"
        text = "\n".join(hints)
        assert "docker compose" not in text, f"a hint suggests raw docker compose:\n{text}"
        cli = re.findall(r"(\S*)\bembabel ((?:--instance \S+ )?)(?:up|status|logs|doctor)\b", text)
        assert len(cli) >= 5, f"expected the embabel verbs, saw {cli} in:\n{text}"
        for launcher, flag in cli:
            expected = "" if installed else os.path.join(settings.APPLIANCE_DIR, "")
            assert launcher == expected, f"hint runs {launcher}embabel, expected {expected}embabel"
            assert flag == ("" if default else f"--instance {name} "), f"embabel hint for {name}: {flag!r}"
        if not installed and not default:
            assert f"EMBABEL_INSTANCE={name} " in hints[0], f"resume hint omits the instance: {hints[0]}"


def check_no_literal_compose_hints():
    """No string in the code spells out a compose command for somebody to run;
    hints come from embabel_command, which names the instance."""
    hint = re.compile(r"docker compose (?:-f\b|-p\b|ps\b|logs\b|up -d\b|stop\b|down\b|exec\b|restart\b)")
    offenders = []
    for path in [*pathlib.Path("embabel_setup").glob("*.py"), pathlib.Path("setup.py"), pathlib.Path("worlds.py")]:
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for literal in re.findall(r'"[^"]*"|\'[^\']*\'', stripped):
                if hint.search(literal):
                    offenders.append(f"{path}:{number}")
    assert not offenders, f"compose commands spelled out in printed text: {offenders}"


def check_no_default_container_names():
    """No file names a container, network or volume of the default appliance by
    its full name. Those names come from the instance's project: in code through
    compose_project and service_container, in docs through `$P` or `<instance>`."""
    pinned = "embabel-" + "appliance-"
    places = [*pathlib.Path("embabel_setup").glob("*.py"), *pathlib.Path("scripts").glob("*"),
              *pathlib.Path("skills").rglob("*.md"), *pathlib.Path("docs").rglob("*.md"),
              pathlib.Path("worlds.py"), pathlib.Path("me.py"), pathlib.Path("setup.py"),
              pathlib.Path("doctor.sh"), pathlib.Path("README.md"), pathlib.Path("CLI.md")]
    offenders = []
    for path in places:
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pinned in line:
                offenders.append(f"{path}:{number}")
    assert not offenders, f"the default appliance's names written out: {offenders}"


VERBS = ("up|down|status|ps|doctor|logs|open|realms|version|backup|restore|sample|contract|run-view|"
         "diagram|scenario|embeddings|sandbox|agents|upgrade|uninstall|prune|trust|bugreport|instances")
BARE_VERB = re.compile(rf"(?<![\w/.$-])embabel (?:{VERBS})\b")


def docstring_lines(tree):
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                lines.update(range(first.lineno, first.end_lineno + 1))
    return lines


def check_no_bare_embabel_hints():
    """Printed `embabel <verb>` hints come from embabel_command, which adds
    --instance for any instance but the default. The help examples in cliparser
    describe the verbs themselves and are left as they are; so are comments,
    docstrings and the comment lines written into .env."""
    offenders = []
    for path in [*pathlib.Path("embabel_setup").glob("*.py"), pathlib.Path("setup.py"),
                 pathlib.Path("worlds.py"), pathlib.Path("me.py")]:
        if not path.exists() or path.name == "cliparser.py":
            continue
        source = path.read_text(encoding="utf-8")
        docs = docstring_lines(ast.parse(source))
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.start[0] in docs or token.type not in (tokenize.STRING, tokenize.FSTRING_MIDDLE):
                continue
            text = token.string.lstrip("fFrRbBuU").lstrip("\"'")
            if text.startswith("# "):
                continue
            if BARE_VERB.search(token.string):
                offenders.append(f"{path}:{token.start[0]}")
    assert not offenders, f"embabel hints that do not name the instance: {offenders}"


def check_rendered_hints_name_the_instance():
    """The words in copy/ and the closing Next block are rendered, not string
    literals, so the scan above cannot see them. Render them for a second
    instance and look at what a person would read."""
    from embabel_setup import surfaces, words
    settings.use_instance("fresh")
    try:
        rendered = {path.name: words.copy_text(path.stem) for path in pathlib.Path("copy").glob("*.txt")}
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            surfaces.print_next()
        rendered["Next block"] = buffer.getvalue()
    finally:
        settings.use_instance(settings.DEFAULT_INSTANCE)
    plain = {name: re.sub(r"\x1b\[[0-9;]*m", "", text) for name, text in rendered.items()}
    offenders = [name for name, text in plain.items() if BARE_VERB.search(text)]
    assert not offenders, f"rendered hints that do not name the instance: {offenders}"
    assert "embabel --instance fresh up" in plain["Next block"], plain["Next block"]
    assert "embabel --instance fresh embeddings use local" in plain["embeddings-later.txt"]


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
            check_hints("fresh")
            check_hints(settings.DEFAULT_INSTANCE)
    finally:
        settings.APPLIANCE_DIR = original_dir
        settings.use_instance(original_instance)

check_no_other_compose_commands()
check_no_literal_compose_hints()
check_no_default_container_names()
check_no_bare_embabel_hints()
check_rendered_hints_name_the_instance()

print("ok: every compose call, container name and printed hint belongs to its instance")
