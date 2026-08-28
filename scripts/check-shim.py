#!/usr/bin/env python3
"""The command uninstall removes must come back when the kept checkout sets up again.

install.sh used to be the only code that wrote the `embabel` forwarder. Uninstall
correctly removed it, but the documented `./worlds.py` route back through setup had
no way to restore it. This check keeps the writer and remover on temporary paths and
proves they agree about ownership — including old install.sh shims and the more
important opposite case, where a different tool already owns the name and must be
left byte-for-byte alone.

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

original_appliance_dir = lifecycle.APPLIANCE_DIR
original_env = {name: os.environ.get(name) for name in ("EMBABEL_BIN_DIR", "PATH", "SHELL")}
failures = []


def reset_state():
    lifecycle.APPLIANCE_DIR = original_appliance_dir
    os.environ.update(EMBABEL_BIN_DIR=bin_dir, PATH=other_dir, SHELL="/bin/bash")
    if os.path.lexists(shim):
        os.remove(shim)


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
    reset_state()
    path, output = write_quietly()
    assert path == shim, path
    assert lifecycle.is_our_shim(path), "the writer produced a shim uninstall does not recognise"
    assert os.stat(path).st_mode & stat.S_IXUSR, "the command is not executable"
    assert "another \"embabel\" already comes first" in output, output
    assert "~/.bashrc" in output, output


def writing_twice_is_idempotent():
    reset_state()
    path, _ = write_quietly()
    assert path == shim
    fixed = 1_700_000_000_000_000_000
    os.utime(shim, ns=(fixed, fixed))
    before = snapshot(shim)
    path, output = write_quietly()
    after = snapshot(shim)
    assert path == shim
    assert after == before, "an unchanged shim was rewritten"
    assert not output, output


def a_foreign_command_is_never_touched():
    reset_state()
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
    assert f"{os.path.realpath(original_appliance_dir)}/embabel" in output, output
    assert "EMBABEL_BIN_DIR" in output, output


def remove_then_write_restores_the_command():
    reset_state()
    path, _ = write_quietly()
    assert lifecycle.is_our_shim(path)
    with contextlib.redirect_stdout(io.StringIO()):
        lifecycle.remove_cli_shim()
    assert not os.path.exists(shim), "remove_cli_shim left its command behind"
    restored, _ = write_quietly()
    assert restored == shim and lifecycle.is_our_shim(restored)


def legacy_install_sh_shim_is_still_owned():
    reset_state()
    os.makedirs(bin_dir, exist_ok=True)
    checkout = os.path.realpath(original_appliance_dir)
    # Literal old heredoc body: this must not follow the current writer's marker,
    # because its job is to preserve ownership of shims already on machines.
    legacy = f'''#!/bin/sh
# Forwards to the Embabel appliance in {checkout}. Written by install.sh.
# Finds a Python 3.9+ each run, preferring newer. The CLI repeats this check
# as a sentence.
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    exec "$cand" "{checkout}/embabel" "$@"
  fi
done
echo "embabel: Python 3.9+ not found (macOS: brew install python@3.12)" >&2
exit 1
'''
    with open(shim, "w") as f:
        f.write(legacy)
    os.chmod(shim, 0o755)
    assert lifecycle.is_our_shim(shim), "an install.sh-era shim is no longer recognised"
    with contextlib.redirect_stdout(io.StringIO()):
        lifecycle.remove_cli_shim()
    assert not os.path.exists(shim), "remove_cli_shim left an install.sh-era shim behind"


def symlinked_checkout_and_bin_are_the_same_installation():
    reset_state()
    checkout_link = os.path.join(root, "checkout-link")
    real_bin = os.path.join(root, "real-bin")
    bin_link = os.path.join(root, "bin-link")
    os.symlink(original_appliance_dir, checkout_link)
    os.makedirs(real_bin)
    os.symlink(real_bin, bin_link)
    lifecycle.APPLIANCE_DIR = checkout_link
    os.environ.update(EMBABEL_BIN_DIR=bin_link, PATH=real_bin, SHELL="/bin/bash")
    path, output = write_quietly()
    assert lifecycle.is_our_shim(path), "a shim written through a checkout symlink is not ours"
    with open(path) as f:
        body = f.read()
    assert os.path.realpath(original_appliance_dir) in body, "the shim kept an unresolved checkout path"
    assert "another \"embabel\"" not in output, output
    assert "NOT on your PATH" not in output, output


CASES = (
    ("a written shim is executable and recognised by uninstall", write_and_detect_round_trip),
    ("writing the same shim twice changes nothing", writing_twice_is_idempotent),
    ("a foreign embabel command is left byte-for-byte alone", a_foreign_command_is_never_touched),
    ("remove then write restores the command", remove_then_write_restores_the_command),
    ("an install.sh-era shim is recognised and removed", legacy_install_sh_shim_is_still_owned),
    ("symlinked checkout and bin paths resolve to this installation", symlinked_checkout_and_bin_are_the_same_installation),
)

try:
    for name, case in CASES:
        try:
            case()
            print(f"  ✓ {name}")
        except Exception as e:
            print(f"  ✗ {name}: {type(e).__name__}: {e}")
            failures.append(name)
finally:
    lifecycle.APPLIANCE_DIR = original_appliance_dir
    for name, value in original_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    shutil.rmtree(root)

sys.exit(1 if failures else 0)
