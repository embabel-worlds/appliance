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
    they cloned, a foreign `embabel`) is untouched and named;
  - the person's git repositories inside the install folder go only when they answer yes to the
    second question, and only when nothing would be lost: no uncommitted or unpushed work, never
    under --yes, never a realms folder outside the install.

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
from unittest.mock import patch

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
        assert "other Embabel installations on this machine use them: other" in out, out
        assert "This appliance is uninstalled." in out and "Embabel is uninstalled." not in out, out


def git(cwd: str, *args: str) -> None:
    """git for building fixtures, deaf to this machine's config — a global commit.gpgsign would
    otherwise ask for a key in the middle of a check."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
               GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    subprocess.run(["git", "-c", "init.defaultBranch=main", *args], cwd=cwd, env=env, check=True,
                   capture_output=True)


def published(root: str, path: str, state: str = "pushed") -> str:
    """Make [path] a git repository whose every commit is on its remote, then spoil it as asked:
    "dirty" leaves an uncommitted file, "unpushed" a commit the remote never saw, "ignored" a
    file git ignores, "hidden" a change git is told to overlook, "stale" deletes the branch on the
    remote while the local remote-tracking ref still claims it is there, "self" makes it its own
    remote, and "submodule" leaves a deinitialised submodule's history in its .git."""
    os.makedirs(path, exist_ok=True)
    if not os.listdir(path):
        with open(os.path.join(path, "realm.yml"), "w") as f:
            f.write("name: x\n")
    if state == "ignored":
        with open(os.path.join(path, ".gitignore"), "w") as f:
            f.write("secret.env\n")
    remote = os.path.join(root, "remotes", os.path.basename(path) + ".git")
    git(root, "init", "-q", "--bare", remote)
    git(path, "init", "-q")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "first")
    git(path, "remote", "add", "origin", remote)
    git(path, "push", "-qu", "origin", "HEAD")
    if state in ("dirty", "unpushed"):
        with open(os.path.join(path, "notes.md"), "w") as f:
            f.write("mine\n")
    if state == "unpushed":
        git(path, "add", "notes.md")
        git(path, "commit", "-qm", "not pushed")
    if state == "ignored":
        with open(os.path.join(path, "secret.env"), "w") as f:
            f.write("KEY=mine\n")
    if state == "hidden":
        with open(os.path.join(path, "realm.yml"), "a") as f:
            f.write("mine: true\n")
        git(path, "update-index", "--assume-unchanged", "realm.yml")
    if state == "stale":
        git(remote, "update-ref", "-d", "refs/heads/main")
    if state == "self":  # its only remote is itself, so "pushed" proves nothing
        git(path, "remote", "set-url", "origin", f"file://localhost{path}")
        git(path, "fetch", "-q", "origin")
    if state == "submodule":  # deinitialised: its history sits in .git/modules, no folder to check
        lib = published(root, os.path.join(root, "lib"))
        git(path, "-c", "protocol.file.allow=always", "submodule", "add", "-q", lib, "lib")
        git(path, "commit", "-qm", "lib")
        git(path, "push", "-q")
        git(path, "submodule", "--quiet", "deinit", "-f", "lib")
    return path


@case("answering no to the repository question keeps every repository")
def repos_kept_on_no():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root, mine=False)
        realm = published(root, os.path.join(install, "realms", "realm-clean"))
        env = machine(root, install, "ok")
        run = uninstall(install, env, stdin="yes\nn\n")
        out = run.stdout + run.stderr
        assert run.returncode == 0, out
        assert "Delete these too? [y/N]" in out, out
        assert os.path.exists(os.path.join(realm, "realm.yml")), "deleted a repository after the person said no"
        assert "realm-clean — a git repository you put there" in out, out


@case("answering yes deletes the clean repositories and the install folder, never an outside realms folder")
def repos_deleted_on_yes():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root, mine=False)
        realm = published(root, os.path.join(install, "realms", "realm-clean"))
        outside = published(root, os.path.join(root, "home", "my-realms", "realm-outside"))
        with open(os.path.join(install, ".env"), "a") as f:
            f.write(f"EMBABEL_REALMS_DIR={os.path.dirname(outside)}\n")
        env = machine(root, install, "ok")
        run = uninstall(install, env, stdin="yes\ny\n")
        out = run.stdout + run.stderr
        assert run.returncode == 0, out
        assert not os.path.exists(realm), "a repository the person agreed to delete was left"
        assert "Removed ~/embabel/worlds/realms/realm-clean." in out, out
        assert not os.path.exists(os.path.join(env["HOME"], "embabel")), "the install folder was left"
        assert os.path.exists(os.path.join(outside, "realm.yml")), "touched a realms folder outside the install"
        assert "realm-outside" not in out, f"offered a repository outside the install:\n{out}"
        assert os.path.exists(os.path.join(root, "outside", "precious.txt")), "followed a symlink out of the install"


@case("a repository with uncommitted, ignored, hidden or unpushed work is named and kept, whatever the answer")
def unsaved_repos_kept():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root)  # realm-mine: a .git git cannot read, so nothing vouches for it
        realms = os.path.join(install, "realms")
        clean = published(root, os.path.join(realms, "realm-clean"))
        dirty = published(root, os.path.join(realms, "realm-dirty"), "dirty")
        unpushed = published(root, os.path.join(realms, "realm-unpushed"), "unpushed")
        others = {state: published(root, os.path.join(realms, f"realm-{state}"), state)
                  for state in ("ignored", "hidden", "stale", "self", "submodule")}
        env = machine(root, install, "ok")
        run = uninstall(install, env, stdin="yes\ny\n")
        out = run.stdout + run.stderr
        assert run.returncode == 0, out
        assert not os.path.exists(clean), out
        assert os.path.exists(os.path.join(dirty, "notes.md")), "deleted a repository with uncommitted changes"
        assert os.path.exists(os.path.join(unpushed, "notes.md")), "deleted a repository with unpushed commits"
        assert os.path.exists(os.path.join(realms, "realm-mine", "realm.yml")), "deleted a repository git could not check"
        assert "realm-dirty — not deleted: it has uncommitted changes" in out, out
        assert "realm-unpushed — not deleted: it has commits that are not pushed" in out, out
        assert "realm-mine — not deleted: git could not check it" in out, out
        for state, path in others.items():
            assert os.path.exists(path), f"deleted a repository whose work is {state}:\n{out}"
        assert "realm-ignored — not deleted: it holds files git ignores, such as secret.env" in out, out
        assert "realm-hidden — not deleted: git is told to overlook changes" in out, out
        assert "realm-stale — not deleted: it has commits that are not pushed" in out, out
        assert "realm-self — not deleted: its remote 'origin' is inside the folder being uninstalled" in out, out
        assert "realm-submodule — not deleted: it holds submodule history" in out, out
        assert sorted(os.listdir(realms)) == ["realm-dirty", "realm-hidden", "realm-ignored", "realm-mine", "realm-self",
                                              "realm-stale", "realm-submodule", "realm-unpushed"], os.listdir(realms)


@case("work saved while remote checks run keeps the repository")
def work_saved_during_remote_check_kept():
    import embabel_setup.uninstall as uninstall_module

    with tempfile.TemporaryDirectory() as root:
        repo = published(root, os.path.join(root, "realm-clean"))
        saved = os.path.join(repo, "saved-during-check.md")
        real_run = subprocess.run
        remote_check_started = False

        def save_during_remote_check(command, *args, **kwargs):
            nonlocal remote_check_started
            if not remote_check_started and command[0] == "git" and "ls-remote" in command:
                with open(saved, "w", encoding="utf-8") as f:
                    f.write("mine\n")
                remote_check_started = True
            return real_run(command, *args, **kwargs)

        with patch.object(uninstall_module.subprocess, "run", side_effect=save_during_remote_check):
            why = uninstall_module.unsaved_work(repo)
        assert remote_check_started, "the remote check did not run"
        assert why == "it has uncommitted changes", why


@case("unpublished Git notes keep the repository")
def unpublished_notes_kept():
    import embabel_setup.uninstall as uninstall_module

    with tempfile.TemporaryDirectory() as root:
        repo = published(root, os.path.join(root, "realm-notes"))
        git(repo, "notes", "add", "-m", "unpublished note")
        why = uninstall_module.unsaved_work(repo)
        assert why == "it has commits that are not pushed", why

        published_notes = published(root, os.path.join(root, "realm-published-notes"))
        git(published_notes, "notes", "add", "-m", "published note")
        git(published_notes, "push", "origin", "refs/notes/commits")
        why = uninstall_module.unsaved_work(published_notes)
        assert why is None, why


@case("a commit kept only in the reflog keeps the repository")
def reflog_only_commit_kept():
    import embabel_setup.uninstall as uninstall_module

    with tempfile.TemporaryDirectory() as root:
        repo = published(root, os.path.join(root, "realm-reflog"))
        with open(os.path.join(repo, "unpublished.md"), "w", encoding="utf-8") as f:
            f.write("not pushed\n")
        git(repo, "add", "unpublished.md")
        git(repo, "commit", "-qm", "reflog-only commit")
        git(repo, "reset", "--hard", "@{u}")
        why = uninstall_module.unsaved_work(repo)
        assert why == "it has commits that are not pushed", why


@case("a stash keeps the repository")
def stashed_work_kept():
    import embabel_setup.uninstall as uninstall_module

    with tempfile.TemporaryDirectory() as root:
        repo = published(root, os.path.join(root, "realm-stash"))
        with open(os.path.join(repo, "realm.yml"), "a", encoding="utf-8") as f:
            f.write("mine\n")
        git(repo, "stash", "push", "-m", "unpublished work")
        why = uninstall_module.unsaved_work(repo)
        assert why == "it has stashed changes", why


@case("unreadable submodule metadata keeps the repository")
def unreadable_modules_kept():
    import embabel_setup.uninstall as uninstall_module

    with tempfile.TemporaryDirectory() as root:
        repo = published(root, os.path.join(root, "realm-modules"))
        modules = os.path.join(repo, ".git", "modules")
        os.makedirs(modules)
        real_listdir = os.listdir
        target = os.path.normcase(os.path.abspath(modules))

        def unreadable(path):
            if os.path.normcase(os.path.abspath(path)) == target:
                raise PermissionError("not readable")
            return real_listdir(path)

        with patch.object(uninstall_module.os, "listdir", side_effect=unreadable):
            why = uninstall_module.unsaved_work(repo)
        assert why == "it holds submodule history that cannot be checked", why


@case("--yes never deletes a repository, and never asks")
def yes_keeps_repos():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root, mine=False)
        realm = published(root, os.path.join(install, "realms", "realm-clean"))
        env = machine(root, install, "ok")
        run = uninstall(install, env, "--yes")
        out = run.stdout + run.stderr
        assert run.returncode == 0, out
        assert "Delete these too" not in out, out
        assert "--yes never deletes them" in out, out
        assert os.path.exists(os.path.join(realm, "realm.yml")), "--yes deleted a repository"


@case("a clean, pushed git clone of the appliance goes when the person says so")
def clean_clone_deleted_on_yes():
    with tempfile.TemporaryDirectory() as root:
        install = make_install(root, mine=False)
        shutil.copy2(os.path.join(HERE, ".gitignore"), install)
        published(root, install)
        realm = published(root, os.path.join(install, "realms", "realm-clean"))
        env = machine(root, install, "ok")
        run = uninstall(install, env, stdin="yes\ny\n")
        out = run.stdout + run.stderr
        assert run.returncode == 0, out
        assert "~/embabel/worlds — the install folder" in out, out
        assert not os.path.exists(realm) and not os.path.exists(install), f"the clone was left:\n{out}"
        assert not os.path.exists(os.path.join(env["HOME"], "embabel")), out
        assert os.path.exists(os.path.join(root, "outside", "precious.txt")), "followed a symlink out of the clone"


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
