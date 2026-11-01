#!/usr/bin/env python3
"""An appliance can always be removed, and removing it leaves nothing the installer put there.

A user had an older, unrelated program called `embabel` on PATH. The installer rightly left it
alone; then every command the install printed ran THAT program, setup stalled, and `embabel
uninstall` could not remove anything. This check holds the two halves of the fix:

  - with a foreign `embabel` first on PATH, every command setup prints is this appliance's
    launcher by its full path, and the closing block says why;
  - `setup.py --uninstall` works from the states a real machine gets left in — setup never
    finished, Docker stopped, Docker gone — and afterwards no container, volume, image, launcher,
    PATH line or install folder of ours remains, while what the person made (a realm repository
    they cloned, a foreign `embabel`) is untouched and named.

The uninstall cases run the REAL setup.py as a child process, from a copy of the installer in a
temporary folder, against a stub `docker` that records what it was asked to do. HOME, PATH and
EMBABEL_BIN_DIR all point into that folder, so nothing on this machine is read or changed.

    python3 scripts/check-uninstall.py
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

failures = []

# A stub docker: `info` answers by STUB_DOCKER; one container OUTSIDE the appliance uses the
# neo4j image, so that image must be kept and named; our own repository has two tags, both of
# which must go. Every call is logged so a case can assert on exactly what was removed.
DOCKER_STUB = r'''#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$1" in
  info)
    [ "$STUB_DOCKER" = ok ] || { echo "Cannot connect" >&2; exit 1; }
    case "$*" in *json*) echo '{"MemTotal": 8589934592, "NCPU": 4}' ;; *) echo 29.0 ;; esac
    exit 0 ;;
  compose)
    case "$*" in *"config --images"*) printf 'ghcr.io/embabel/assistant:latest\nneo4j:2026.06-community\n' ;; esac
    exit 0 ;;
  images) printf 'ghcr.io/embabel/assistant:latest\tsha256:aaa\nneo4j:2026.06-community\tsha256:bbb\nghcr.io/embabel/assistant:my-local-build\tsha256:ccc\nsomeone/else:1\tsha256:ddd\n' ;;
  ps)
    case "$*" in *--filter*) exit 0 ;; esac
    [ "$2" = "-aq" ] && echo c1
    [ -n "$STUB_OTHER" ] && [ "$2" = "-a" ] && echo "embabel-other"
    exit 0 ;;
  inspect) printf 'sha256:bbb\ttheir-project\t/their-neo4j\n' ;;
  model) [ "$2" = list ] && echo "ai/qwen3-embedding   0.6B-F16   1.1GB" ; exit 0 ;;
esac
exit 0
'''

FOREIGN_BODY = '#!/bin/sh\necho "an older, unrelated embabel"\n'


def make_install(root: str, git: bool = False, mine: bool = True) -> str:
    """The folder install.sh would have unpacked, with a stalled setup in it: settings and a port
    block, no account, nothing the server ever confirmed."""
    install = os.path.join(root, "home", "embabel", "worlds")
    os.makedirs(install)
    for name in ("setup.py", "embabel"):
        shutil.copy2(os.path.join(HERE, name), install)
    for name in ("embabel_setup", "copy", "realms"):
        shutil.copytree(os.path.join(HERE, name), os.path.join(install, name),
                        ignore=shutil.ignore_patterns("__pycache__"))
    for name in os.listdir(HERE):
        if name.endswith(".yml"):
            shutil.copy2(os.path.join(HERE, name), install)
    with open(os.path.join(install, ".env"), "w") as f:
        f.write("EMBABEL_PORT_BASE=11990\nEMBABEL_MODE=worlds\n")
    # A realm somebody cloned into the realms folder: theirs, and it must survive.
    if mine:
        realm = os.path.join(install, "realms", "realm-mine")
        os.makedirs(os.path.join(realm, ".git"))
        with open(os.path.join(realm, "realm.yml"), "w") as f:
            f.write("name: mine\n")
    # A link out of the folder. Deleting through it would delete somebody else's file.
    outside = os.path.join(root, "outside")
    os.makedirs(outside)
    with open(os.path.join(outside, "precious.txt"), "w") as f:
        f.write("not ours\n")
    os.symlink(outside, os.path.join(install, "link-out"))
    if git:
        os.makedirs(os.path.join(install, ".git"))
    return install


def machine(root: str, install: str, docker: str | None) -> dict:
    """HOME, PATH and the launcher, all inside [root]. A foreign `embabel` sits FIRST on PATH."""
    home = os.path.join(root, "home")
    stub = os.path.join(root, "stub")
    bindir = os.path.join(root, "bin")
    foreign_dir = os.path.join(root, "foreign")
    for d in (stub, bindir, foreign_dir):
        os.makedirs(d, exist_ok=True)
    os.symlink(sys.executable, os.path.join(stub, "python3"))
    if docker:
        with open(os.path.join(stub, "docker"), "w") as f:
            f.write(DOCKER_STUB)
        os.chmod(os.path.join(stub, "docker"), 0o755)
    with open(os.path.join(foreign_dir, "embabel"), "w") as f:
        f.write(FOREIGN_BODY)
    os.chmod(os.path.join(foreign_dir, "embabel"), 0o755)
    shim = os.path.join(bindir, "embabel")
    with open(shim, "w") as f:
        f.write(f"#!/bin/sh\n# Forwards to the Embabel appliance in {os.path.realpath(install)}. "
                "Written by install.sh.\nexit 0\n")
    os.chmod(shim, 0o755)
    with open(os.path.join(home, ".bashrc"), "w") as f:
        f.write("export THEIRS=1\n# BEGIN embabel appliance PATH\n"
                f'export PATH="{bindir}:$PATH"\n# END embabel appliance PATH\n')
    return {"HOME": home, "PATH": os.pathsep.join([foreign_dir, stub, bindir, "/usr/bin", "/bin"]),
            "EMBABEL_BIN_DIR": bindir, "SHELL": "/bin/bash", "STUB_DOCKER": docker or "",
            "STUB_LOG": os.path.join(root, "docker.log"), "NO_COLOR": "1", "LANG": "C.UTF-8"}


def uninstall(install: str, env: dict, *flags: str, stdin: str = "") -> subprocess.CompletedProcess:
    run = subprocess.run([sys.executable, os.path.join(install, "setup.py"), "--uninstall", *flags],
                         env=env, input=stdin, capture_output=True, text=True, timeout=120, cwd=env["HOME"])
    if os.environ.get("SHOW"):  # SHOW=1 prints what a person would have read
        print(run.stdout + run.stderr)
    return run


def docker_calls(env: dict) -> str:
    try:
        with open(env["STUB_LOG"]) as f:
            return f.read()
    except OSError:
        return ""


def case(name):
    def register(fn):
        CASES.append((name, fn))
        return fn
    return register


CASES = []


@case("a stalled setup uninstalls to nothing, keeping only what the person made")
def stalled_setup_leaves_nothing():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root)
        env = machine(root, install, "ok")
        run = uninstall(install, env, "--yes")
        out = run.stdout + run.stderr
        assert run.returncode == 0, out
        calls = docker_calls(env)
        assert "down --volumes" in calls, calls
        assert "image rm ghcr.io/embabel/assistant:latest" in calls, calls
        assert "my-local-build" not in calls, "removed an image compose never pulled — a local build"
        assert "image rm neo4j" not in calls, "removed an image another container uses"
        assert "rm -f c1" not in calls, "removed a container that is not the appliance's"
        assert "model rm" in calls, "the local embedding model was left"
        assert "neo4j:2026.06-community — in use by the container their-neo4j" in out, out
        realm = os.path.join(install, "realms", "realm-mine")
        assert os.path.exists(os.path.join(realm, "realm.yml")), "deleted a realm repository the person cloned"
        assert "realm-mine — a git repository you put there" in out, out
        left = sorted(os.listdir(install))
        assert left == ["realms"], f"the install folder still holds {left}"
        assert os.listdir(os.path.join(install, "realms")) == ["realm-mine"], os.listdir(os.path.join(install, "realms"))
        assert os.path.exists(os.path.join(root, "outside", "precious.txt")), "followed a symlink out of the install"
        assert not os.path.exists(os.path.join(env["EMBABEL_BIN_DIR"], "embabel")), "the launcher was left"
        with open(os.path.join(root, "foreign", "embabel")) as f:
            assert f.read() == FOREIGN_BODY, "the foreign embabel was changed"
        with open(os.path.join(env["HOME"], ".bashrc")) as f:
            assert f.read() == "export THEIRS=1\n", "the PATH block was left, or their line went with it"
        assert "Embabel is uninstalled." in out, out
        assert "Another program called 'embabel' is still on your PATH" in out, out
        for jargon in ("checkout", "fresh clone"):
            assert jargon not in out, f"developer wording in user output: {jargon!r}"


@case("the list shown before the question is what happens")
def plan_matches_what_happens():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root)
        env = machine(root, install, "ok")
        run = uninstall(install, env, stdin="no\n")
        out = run.stdout + run.stderr
        assert run.returncode == 1 and "nothing was touched" in out, out
        assert os.path.exists(os.path.join(install, ".env")), "a refusal removed the settings"
        assert os.path.exists(os.path.join(env["EMBABEL_BIN_DIR"], "embabel")), "a refusal removed the launcher"
        assert "image rm" not in docker_calls(env) and "down" not in docker_calls(env)
        for promised in ("ghcr.io/embabel/assistant:latest",
                         "the local embedding model", "everything in ~/embabel/worlds except what is kept",
                         "the 'embabel' command", "the PATH line setup added"):
            assert promised in out, f"the plan never mentioned {promised!r}:\n{out}"


@case("Docker stopped: refuse before touching anything")
def docker_stopped_touches_nothing():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root)
        env = machine(root, install, "stopped")
        run = uninstall(install, env, "--yes")
        out = run.stdout + run.stderr
        assert run.returncode == 1 and "Docker is not running" in out and "Nothing was touched" in out, out
        assert os.path.exists(os.path.join(install, ".env")), "removed the settings with the data unreachable"
        assert os.path.exists(os.path.join(env["EMBABEL_BIN_DIR"], "embabel"))


@case("Docker gone entirely: the files still go")
def docker_absent_still_uninstalls():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root, mine=False)
        env = machine(root, install, None)
        run = uninstall(install, env, "--yes")
        out = run.stdout + run.stderr
        assert run.returncode == 0 and "Embabel is uninstalled." in out, out
        assert "Still on this machine" not in out, out
        # Nothing of the person's inside, so the folder goes, and ~/embabel that held only it.
        assert not os.path.exists(os.path.join(env["HOME"], "embabel")), "the install folder was left"
        assert not os.path.exists(os.path.join(env["EMBABEL_BIN_DIR"], "embabel"))


@case("a git working tree is kept and named; --keep-images keeps the downloads")
def developer_clone_is_kept():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root, git=True)
        env = machine(root, install, "ok")
        run = uninstall(install, env, "--yes", "--keep-images")
        out = run.stdout + run.stderr
        assert run.returncode == 0, out
        assert os.path.exists(os.path.join(install, "setup.py")), "deleted a developer's git clone"
        assert "it is a git repository, not something the installer created" in out, out
        assert not os.path.exists(os.path.join(install, ".env")), "the settings were left"
        calls = docker_calls(env)
        assert "image rm" not in calls and "model rm" not in calls, "--keep-images removed downloads"
        assert "down --volumes" in calls, calls


@case("another appliance on the machine keeps the images and the model, and says so")
def other_appliance_keeps_shared_downloads():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root)
        env = dict(machine(root, install, "ok"), STUB_OTHER="1")
        run = uninstall(install, env, "--yes")
        out = run.stdout + run.stderr
        assert run.returncode == 0, out
        calls = docker_calls(env)
        assert "image rm" not in calls and "model rm" not in calls, "removed downloads another appliance runs on"
        assert "other Embabel appliances on this machine use them: other" in out, out
        assert "This appliance is uninstalled." in out and "Embabel is uninstalled." not in out, out


@case("with a foreign embabel first on PATH, every printed command is ours by full path")
def foreign_embabel_gets_full_paths():
    from embabel_setup import launcher, settings, surfaces, words
    with tempfile.TemporaryDirectory() as root:
        foreign_dir = os.path.join(root, "foreign")
        os.makedirs(foreign_dir)
        with open(os.path.join(foreign_dir, "embabel"), "w") as f:
            f.write(FOREIGN_BODY)
        os.chmod(os.path.join(foreign_dir, "embabel"), 0o755)
        ours_dir = os.path.join(root, "ours")
        os.makedirs(ours_dir)
        with open(os.path.join(ours_dir, "embabel"), "w") as f:
            f.write(f"#!/bin/sh\n# Forwards to the Embabel appliance in {os.path.realpath(settings.APPLIANCE_DIR)}. "
                    "Written by install.sh.\n")
        os.chmod(os.path.join(ours_dir, "embabel"), 0o755)
        full = os.path.join(settings.APPLIANCE_DIR, "embabel")
        saved = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = os.pathsep.join([foreign_dir, ours_dir])
            assert launcher.foreign_embabel(settings.APPLIANCE_DIR) == os.path.join(foreign_dir, "embabel")
            assert settings.embabel_command("up") == f"{full} up", settings.embabel_command("up")
            assert f"{full} embeddings use local" in words.copy_text("embeddings-later")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                surfaces.print_next()
            shown = buffer.getvalue()
            assert 'Another program called "embabel" is on your PATH' in shown, shown
            assert os.path.join(foreign_dir, "embabel") in shown, shown
            assert f"{full} uninstall" in shown, shown
            assert f"{full} doctor" in shown, shown

            # Ours first: the bare name is right again, and nothing is said about a conflict.
            os.environ["PATH"] = os.pathsep.join([ours_dir, foreign_dir])
            assert settings.embabel_command("up") == "embabel up", settings.embabel_command("up")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                surfaces.print_next()
            assert "Another program" not in buffer.getvalue(), buffer.getvalue()
        finally:
            os.environ["PATH"] = saved


for name, fn in CASES:
    try:
        fn()
        print(f"  ✓ {name}")
    except Exception as e:  # noqa: BLE001 — every case reports, none stops the others
        print(f"  ✗ {name}: {type(e).__name__}: {e}")
        failures.append(name)

sys.exit(1 if failures else 0)
