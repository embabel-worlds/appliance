#!/usr/bin/env python3
"""The command uninstall removes must come back when the kept checkout sets up again.

install.sh used to be the only code that wrote the `embabel` forwarder. Uninstall
correctly removed it, but the documented `./worlds.py` route back through setup had
no way to restore it. This check keeps the writer and remover on temporary paths and
proves they agree about ownership — including the more important opposite case,
where a different tool already owns the name and must be left byte-for-byte alone.

    python3 scripts/check-shim.py
"""
import contextlib
import io
import os
import shutil
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embabel_setup import lifecycle  # noqa: E402

root = tempfile.mkdtemp(prefix="embabel-shim-check-")
bin_dir = os.path.join(root, "bin")
other_dir = os.path.join(root, "other")
shim = os.path.join(bin_dir, "embabel")
other = os.path.join(other_dir, "embabel")
os.makedirs(other_dir)
with open(other, "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
os.chmod(other, 0o755)

original_paths = lifecycle.cli_shim_paths
original_env = {name: os.environ.get(name) for name in ("EMBABEL_BIN_DIR", "PATH", "SHELL")}
lifecycle.cli_shim_paths = lambda: [shim]
os.environ.update(EMBABEL_BIN_DIR=bin_dir, PATH=other_dir, SHELL="/bin/bash")
failures = []


def write_quietly():
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        path = lifecycle.write_cli_shim()
    return path, output.getvalue()


def snapshot(path):
    with open(path, "rb") as f:
        body = f.read()
    info = os.stat(path)
    return body, info.st_mode, info.st_mtime_ns


def write_and_detect_round_trip():
    path, output = write_quietly()
    assert path == shim, path
    assert lifecycle.is_our_shim(path), "the writer produced a shim uninstall does not recognise"
    assert os.stat(path).st_mode & stat.S_IXUSR, "the command is not executable"
    assert "another \"embabel\" already comes first" in output, output
    assert "~/.bashrc" in output, output


def writing_twice_is_idempotent():
    fixed = 1_700_000_000_000_000_000
    os.utime(shim, ns=(fixed, fixed))
    before = snapshot(shim)
    path, output = write_quietly()
    after = snapshot(shim)
    assert path == shim
    assert after == before, "an unchanged shim was rewritten"
    assert not output, output


def a_foreign_command_is_never_touched():
    os.remove(shim)
    os.makedirs(bin_dir, exist_ok=True)
    foreign = b"#!/bin/sh\necho somebody-else\n"
    with open(shim, "wb") as f:
        f.write(foreign)
    os.chmod(shim, 0o700)
    before = snapshot(shim)
    path, output = write_quietly()
    after = snapshot(shim)
    assert path is None
    assert after == before, "the foreign command changed"
    assert "Left" in output and "alone" in output, output


def remove_then_write_restores_the_command():
    os.remove(shim)
    path, _ = write_quietly()
    assert lifecycle.is_our_shim(path)
    with contextlib.redirect_stdout(io.StringIO()):
        lifecycle.remove_cli_shim()
    assert not os.path.exists(shim), "remove_cli_shim left its command behind"
    restored, _ = write_quietly()
    assert restored == shim and lifecycle.is_our_shim(restored)


CASES = (
    ("a written shim is executable and recognised by uninstall", write_and_detect_round_trip),
    ("writing the same shim twice changes nothing", writing_twice_is_idempotent),
    ("a foreign embabel command is left byte-for-byte alone", a_foreign_command_is_never_touched),
    ("remove then write restores the command", remove_then_write_restores_the_command),
)

try:
    for name, case in CASES:
        try:
            case()
            print(f"  ✓ {name}")
        except AssertionError as e:
            print(f"  ✗ {name}: {e}")
            failures.append(name)
finally:
    lifecycle.cli_shim_paths = original_paths
    for name, value in original_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    shutil.rmtree(root)

sys.exit(1 if failures else 0)
