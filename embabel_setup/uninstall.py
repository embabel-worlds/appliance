"""Uninstalling: everything the installer put on this machine goes, and only that.

An uninstall is judged by what is left afterwards. The old one kept the install folder, the
Docker images and the embedding model — sensible for somebody developing the appliance, and a
puzzle for everybody else, who removed Embabel and then found gigabytes of it still on disk with
no command to take them away. So the default is now NOTHING LEFT, and keeping things is the
opt-in: `--keep-images` for the downloads, `--keep-dir` for the folder.

What is kept by default is what the PERSON made, and each such thing is named with its reason:
a git repository they cloned into the realms folder, backups, and the install folder itself
when it is a git working tree — a developer's clone, which the installer did not create and has
no business deleting.

THOSE GIT REPOSITORIES ARE THE PERSON'S TO GIVE UP, AND ONLY THEIRS. After the uninstall is
confirmed it names them and asks once more, defaulting to no, whether they go too — so a clean
uninstall does not end with somebody hunting down realm clones by hand. Only a repository with
nothing to lose is deleted: no uncommitted, stashed or ignored files, an upstream, every commit on
the remote as it is now, no other worktree or submodule history depending on it. Anything else is named and kept whatever the answer.
`--yes` never deletes one, because nobody was asked; and a realms folder outside the install
folder is never walked, let alone offered.

THE PLAN IS COMPUTED FIRST AND PRINTED BEFORE THE QUESTION, and then it is what runs. A list of
what will go that differs from what goes is worse than no list, so the two are one data
structure rather than a paragraph of prose beside a sequence of calls.

It works from any state the appliance can be left in. Nothing here talks to the server: an
install whose setup never finished, whose container is stopped, unhealthy or missing, or whose
image pull failed halfway has no server to talk to, and it still has to be removable.
"""

from __future__ import annotations
import os
import shutil
import stat
import subprocess
import sys
from urllib.parse import urlparse
from urllib.request import url2pathname

from .agents import (MCP_SERVER_NAME, PATH_BLOCK_BEGIN, codex_token_profiles, shell_profiles,
                     unwire_coding_agents)
from .backup import DEFAULT_BACKUP_DIR
from .colour import bold, dim, heading, warn
from .core import APPLIANCE_DIR, EMBEDDING_MODEL, OVERRIDE_FILE, SetupError, prompt
from .dockerlib import (_docker, compose_command, compose_env, embedding_model_present, prune_sandboxes,
                        stray_sandbox_containers, take_everything_down)
from .launcher import cli_shim_paths, windows_shim_paths
from .lifecycle import another_appliances_launcher, is_our_shim, remove_cli_shim
from .settings import (DEFAULT_INSTANCE, compose_project, env_file_value, env_path, installed_instances,
                       instance)

# WHICH IMAGES ARE "OURS" is what compose would pull for this appliance, and nothing wider.
# Every local tag of ghcr.io/embabel/assistant looked like the right net, and on a developer's
# machine it caught images built there and tagged under that name — which no registry can give
# back. An image the compose files name is one this install downloaded; anything else is a guess.


def tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


class Plan:
    """What an uninstall of this instance will do, worked out before anything is touched."""

    def __init__(self, keep_images: bool, keep_dir: bool):
        self.removed: list[str] = []         # printed under REMOVED, in order
        self.kept: list[tuple[str, str]] = []  # (what, why), printed under KEPT
        self.notes: list[str] = []
        self.docker = docker_state()
        self.here = os.path.realpath(APPLIANCE_DIR)
        # What a person recognises: the path they installed to, not the one symlinks resolve to.
        self.shown = tilde(os.path.abspath(APPLIANCE_DIR))
        self.others_here = others_installed_here()
        self.others_anywhere = sorted(n for n in installed_instances() if n != instance())
        self.sandboxes: list[str] = []
        self.images: list[str] = []
        self.model = False
        self.launchers = [p for p in cli_shim_paths() + windows_shim_paths()
                          if os.path.lexists(p) and is_our_shim(p)]
        self.directory: str | None = None
        self.kept_repos: list[str] = []
        # The git repositories the person may choose to delete after confirming -> how each is shown.
        self.repos: dict[str, str] = {}

        if self.docker == "stopped":
            # BEFORE ANYTHING IS TOUCHED. Removing the settings while the data is still in
            # Docker would leave volumes nothing can name any more — the worst kind of residue,
            # because it is invisible and it is large.
            raise SetupError(
                "Docker is not running, so the appliance and its data cannot be removed.\n"
                "Start Docker, then run this again. Nothing was touched.")

        if self.docker == "ok":
            self.removed.append("the appliance and all of its data — account, worlds, graph, documents, dashboards")
            self.sandboxes = stray_sandbox_containers()
            if self.sandboxes:
                self.removed.append(f"{len(self.sandboxes)} code-sandbox container(s) it started")
        else:
            self.notes.append("Docker is not installed here, so there are no containers or images to remove.")

        self.removed.append("its settings — your provider key, timezone, and the folders shared with it")
        self.removed.append(f"the '{MCP_SERVER_NAME}' entry in Claude Code and Codex — only where it points at this appliance")
        token_lines = codex_token_profiles()
        if token_lines and not shutil.which("codex"):
            self.kept.append(("the Codex token line in " + ", ".join(tilde(p) for p in token_lines),
                              "Codex is not on your PATH, so whether that line is this appliance's cannot be checked"))

        if self.others_here:
            # One folder, one command, one set of images for every appliance installed in it:
            # taking any of them away would break the one still standing.
            names = ", ".join(f"'{n}'" for n in self.others_here)
            self.kept.append((f"the 'embabel' command, the install folder {self.shown} and the Docker images",
                              f"{names} is still installed there and uses them"))
            return

        for path in self.launchers:
            self.removed.append(f"the 'embabel' command ({tilde(path)})")
        profiles = [p for p in shell_profiles() if holds_path_block(p)]
        if profiles and another_appliances_launcher():
            self.kept.append(("the PATH line in " + ", ".join(tilde(p) for p in profiles),
                              "another Embabel install's command still needs it"))
        elif profiles:
            self.removed.append("the PATH line setup added to " + ", ".join(tilde(p) for p in profiles))

        if self.docker == "ok":
            if keep_images:
                self.kept.append(("the Docker images and the embedding model", "you asked to keep them (--keep-images)"))
            elif self.others_anywhere:
                # Every appliance runs the same images and the same model, and one that is
                # stopped or mid-upgrade has no container to say so. While another is installed
                # anywhere on this machine, they are its as much as this one's.
                self.kept.append(("the Docker images and the embedding model",
                                  "other Embabel installations on this machine use them: "
                                  + ", ".join(self.others_anywhere)))
            else:
                self.images, in_use = removable_images()
                if self.images:
                    self.removed.append(f"{len(self.images)} Docker image(s) it downloaded:")
                    self.removed.extend(f"    {ref}" for ref in self.images)
                for ref, user in in_use:
                    self.kept.append((ref, f"in use by {user}, which is not this appliance"))
                if embedding_model_present():
                    self.model = True
                    self.removed.append(f"the local embedding model ({EMBEDDING_MODEL})")

        realms = env_file_value("EMBABEL_REALMS_DIR")
        if realms and os.path.isdir(realms) and not inside(realms, self.here):
            self.kept.append((tilde(realms), "your realms folder — never the installer's to delete"))

        if keep_dir:
            self.kept.append((self.shown, "you asked to keep it (--keep-dir)"))
        elif os.path.exists(os.path.join(self.here, ".git")):
            self.kept.append((self.shown, "it is a git repository, not something the installer created"))
            # Only a folder that is recognisably an appliance is offered: never home, never a root.
            if looks_like_an_install(self.here):
                self.repos[self.here] = self.shown
                for repo in git_repositories_inside(self.here):
                    self.repos[repo] = self.shown + repo[len(self.here):]
        elif not looks_like_an_install(self.here):
            self.kept.append((self.shown, "it does not look like a folder the installer created"))
        else:
            self.directory = self.here
            self.kept_repos = git_repositories_inside(self.here)
            parent = os.path.dirname(os.path.abspath(APPLIANCE_DIR))
            if self.kept_repos:
                self.removed.append(f"everything in {self.shown} except what is kept below")
            else:
                self.removed.append(f"{self.shown} — the folder Embabel was installed into")
                if is_installers_parent(parent):
                    self.removed.append(f"{tilde(parent)} — the folder that held it, if nothing else is in it")
            for repo in self.kept_repos:
                # Named under the path the person installed to, not the one symlinks resolve to.
                shown = self.shown + repo[len(self.here):]
                self.repos[repo] = shown
                self.kept.append((shown, "a git repository you put there; yours, not the installer's"))

        backups = os.path.abspath(DEFAULT_BACKUP_DIR)
        if os.path.isdir(backups) and os.listdir(backups):
            self.kept.append((tilde(backups), "backups you made"))

    def show(self) -> None:
        print("  Uninstalling removes everything Embabel put on this machine"
              + ("." if instance() == DEFAULT_INSTANCE else f" for the '{instance()}' appliance."))
        for note in self.notes:
            print("  " + dim(note))
        print("\n  " + bold("REMOVED"))
        for line in self.removed:
            print(f"    {line}")
        if self.kept:
            print("\n  " + bold("KEPT"))
            for what, why in self.kept:
                print(f"    {what} — {why}")
        print()


def docker_state() -> str:
    """"absent" (no docker command at all), "stopped" (a command, no daemon) or "ok".

    The two failures are opposites for an uninstall. With Docker gone entirely there is nothing
    in it to remove — uninstalling Docker Desktop took the containers and volumes with it — so
    the files can still go. With Docker merely stopped the data is all still there, unreachable,
    and removing the settings would orphan it.
    """
    if not shutil.which("docker"):
        return "absent"
    run = _docker("info", "--format", "{{.ServerVersion}}", timeout=30)
    return "ok" if run and run.returncode == 0 else "stopped"


def others_installed_here() -> list[str]:
    """Other appliances installed from THIS folder: a settings file here, or containers compose
    started from here. Instances elsewhere have their own folder and their own launcher."""
    here = os.path.realpath(APPLIANCE_DIR)
    # A settings file with a port block, which every created instance has. A bare `.env` holding
    # only a timezone is what an older setup wrote for a NAMED instance into the default's file,
    # and counting it would keep the folder for an appliance that was never installed here.
    names = {n for n in installed_instances()
             if n != instance() and env_file_value("EMBABEL_PORT_BASE", n)}
    run = _docker("ps", "-a", "--format",
                  '{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.project.working_dir"}}',
                  timeout=20)
    if run and run.returncode == 0:
        for line in run.stdout.splitlines():
            project, _, workdir = line.partition("\t")
            if (project.startswith("embabel-") and project != compose_project()
                    and workdir and os.path.realpath(workdir) == here):
                names.add(project[len("embabel-"):])
    return sorted(names)


def removable_images() -> tuple[list[str], list[tuple[str, str]]]:
    """The images this appliance pulled that can go, and the ones another container still uses.

    What compose would run for this instance, and only that. An image a container OUTSIDE this
    appliance uses — something of the person's own — is kept and named; Docker would refuse to
    remove it anyway, and saying so up front keeps the list printed before the question true.
    """
    wanted: set[str] = set()
    # A listing that fails narrows the plan to our own repositories; it never stops the uninstall.
    try:
        config = subprocess.run(compose_command(("me", "worlds"), "config", "--images"), capture_output=True,
                                text=True, env=compose_env(), timeout=60, encoding="utf-8", errors="replace")
    except (subprocess.SubprocessError, OSError):
        config = None
    if config and config.returncode == 0:
        wanted |= {line.strip() for line in config.stdout.splitlines() if line.strip()}
    local = _docker("images", "--no-trunc", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}", timeout=30)
    if not local or local.returncode != 0:
        return [], []
    present: dict[str, str] = {}
    for line in local.stdout.splitlines():
        ref, _, image_id = line.partition("\t")
        if ref in wanted or f"docker.io/library/{ref}" in wanted or f"docker.io/{ref}" in wanted:
            present[ref] = image_id
    users = image_users()
    removable, in_use = [], []
    for ref, image_id in sorted(present.items()):
        if image_id in users:
            in_use.append((ref, users[image_id]))
        else:
            removable.append(ref)
    return removable, in_use


def image_users() -> dict[str, str]:
    """Image id -> a container outside this appliance that uses it, for every such container."""
    ids = _docker("ps", "-aq", timeout=20)
    if not ids or ids.returncode != 0 or not ids.stdout.split():
        return {}
    run = _docker("inspect", "--format",
                  '{{.Image}}\t{{index .Config.Labels "com.docker.compose.project"}}\t{{.Name}}',
                  *ids.stdout.split(), timeout=60)
    users: dict[str, str] = {}
    for line in (run.stdout.splitlines() if run and run.returncode == 0 else []):
        image_id, project, name = (line.split("\t") + ["", ""])[:3]
        if project == compose_project():
            continue
        users.setdefault(image_id, f"the container {name.lstrip('/')}")
    return users


def is_installers_parent(parent: str) -> bool:
    """~/embabel: install.sh made it to hold ~/embabel/worlds, so it is ours by name and place."""
    return (os.path.basename(parent) == "embabel"
            and os.path.realpath(os.path.dirname(parent)) == os.path.realpath(os.path.expanduser("~")))


def holds_path_block(profile: str) -> bool:
    try:
        with open(profile, encoding="utf-8", errors="replace") as f:
            return PATH_BLOCK_BEGIN in f.read()
    except OSError:
        return False


def inside(path: str, parent: str) -> bool:
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path == parent or path.startswith(parent + os.sep)


def looks_like_an_install(path: str) -> bool:
    """The folder install.sh unpacks, and nothing bigger.

    The last guard before deleting a directory: it must hold the installer's own files, and it
    must not be the home folder or a filesystem root, whatever a stray EMBABEL_HOME said.
    """
    real = os.path.realpath(path)
    if real in (os.path.realpath(os.path.expanduser("~")), os.path.realpath(os.sep)) or os.path.dirname(real) == real:
        return False
    return all(os.path.exists(os.path.join(real, name)) for name in ("setup.py", "embabel", "embabel_setup"))


def git_repositories_inside(root: str) -> list[str]:
    """Every git repository under [root], outermost only, never following a symlink.

    A realm somebody cloned into the realms folder is their work, with their commits on it and
    their remotes behind it. The installer never creates one of these, so any that exists was put
    there by a person, and it stays.
    """
    found = []
    for current, dirs, files in os.walk(root, followlinks=False):
        if current != root and (".git" in dirs or ".git" in files):
            found.append(current)
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(current, d))]
    return sorted(found)


def unsaved_work(repo: str, spare: set[str] = frozenset()) -> str | None:
    """Why deleting [repo] would lose something, or None when git vouches that it would not.

    Every doubt keeps it: git missing, a command failing, a remote that cannot be reached, a
    detached head with no upstream. Untracked AND ignored files count as work — an ignored file
    is often exactly the local secret or data nobody committed — except Python's caches and what
    [spare] names: the nested repositories offered on their own, and the settings files this
    uninstall removes anyway. Pushed means on the remote NOW, asked with ls-remote: a local
    remote-tracking ref outlives the branch it tracks, and a squash-merged branch deleted on the
    server would read as pushed while its commits exist nowhere else.

    GIT_* variables are dropped so that a GIT_DIR in the environment cannot make git answer for
    some other repository, and git may not prompt: a question nobody sees would stall the uninstall.
    A repository nested inside this one goes with it, so it is checked too — unless [spare] names
    it and its history is its own: a submodule's lives in the parent's .git, so it is checked
    with the parent whatever happens to the submodule's folder.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"

    def git(*args: str, stdin: str = "") -> str | None:
        try:
            run = subprocess.run(["git", "-C", repo, *args], input=stdin, capture_output=True, text=True,
                                 env=env, timeout=60, encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return None
        return run.stdout if run.returncode == 0 else None

    # A broken .git makes git search upward and answer for the repository AROUND this one — the
    # appliance clone, whose ignore rules hide the realms. Only an answer about [repo] counts.
    top = git("rev-parse", "--show-toplevel")
    if top is None or os.path.realpath(top.strip()) != os.path.realpath(repo):
        return "git could not check it for unsaved work"
    if git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") is None:
        return "its branch has no upstream, so whether it is pushed cannot be checked"
    worktrees = git("worktree", "list", "--porcelain")
    if worktrees is None or worktrees.count("worktree ") > 1:
        return "other git worktrees share its history"
    # A deinitialised submodule leaves its history in .git/modules with no folder to check it by.
    modules = git("rev-parse", "--git-path", "modules")
    if modules is None:
        return "it holds submodule history that cannot be checked"
    modules_path = os.path.join(repo, modules.strip())
    try:
        module_entries = os.listdir(modules_path)
    except FileNotFoundError:
        module_entries = []
    except OSError:
        return "it holds submodule history that cannot be checked"
    if module_entries:
        return "it holds submodule history that cannot be checked"
    remotes = (git("remote") or "").split()
    published = []
    for remote in remotes:
        # A remote that is a folder inside the install is deleted with it: no backup at all.
        url = (git("remote", "get-url", remote) or "").strip()
        local = os.path.join(repo, url2pathname(urlparse(url).path) if url.startswith("file:") else url)
        if not url or (os.path.exists(local) and inside(local, APPLIANCE_DIR)):
            return f"its remote '{remote}' is inside the folder being uninstalled"
        listing = git("ls-remote", "--refs", remote)
        if listing is None:
            return f"its remote '{remote}' could not be reached to confirm everything is pushed"
        published += ["^" + line.split()[0] for line in listing.splitlines() if line.strip()]
    for nested in git_repositories_inside(repo):
        if nested in spare and os.path.isdir(os.path.join(nested, ".git")):
            continue
        why = unsaved_work(nested)
        if why:
            return f"{tilde(nested)} inside it: {why}"

    # Remote and nested-repository checks can take minutes. Re-read local history, the stash,
    # index and finally the worktree only after they finish, so newly saved work cannot pass as clean.
    unpushed = git("rev-list", "--ignore-missing", "--all", "--reflog", "--stdin",
                   stdin="".join(f"{rev}\n" for rev in published))
    if not remotes or unpushed is None or unpushed.strip():
        stash = git("stash", "list")
        if stash is None or stash.strip():
            return "it has stashed changes"
        return "it has commits that are not pushed"
    flags = git("ls-files", "-v")
    if flags is None:
        return "git could not check it for unsaved work"
    # Lower case is assume-unchanged, S is skip-worktree: changes git status was told not to show.
    if any(line[:1].islower() or line[:1] == "S" for line in flags.splitlines()):
        return "git is told to overlook changes to some of its files"
    stash = git("stash", "list")
    if stash is None or stash.strip():
        return "it has stashed changes"
    status = git("status", "--porcelain", "-z", "--ignored", "--untracked-files=normal", "--ignore-submodules=none")
    if status is None:
        return "git could not check it for unsaved work"
    entries = [e for e in status.split("\0") if e]
    if any(not e.startswith("!! ") for e in entries):
        return "it has uncommitted changes"
    for entry in entries:
        path = os.path.join(repo, entry[3:].rstrip("/"))
        name = os.path.basename(path)
        if not (os.path.realpath(path) in spare or name in ("__pycache__", ".DS_Store") or name.endswith(".pyc")):
            return f"it holds files git ignores, such as {entry[3:]}"
    return None


def accounted_for(plan: Plan) -> set[str]:
    """What unsaved_work may find inside a repository without that counting as work to lose."""
    return set(plan.repos) | {os.path.realpath(p) for p in (env_path(), os.path.join(APPLIANCE_DIR, OVERRIDE_FILE))}


def choose_repos(plan: Plan, assume_yes: bool) -> tuple[list[str], list[tuple[str, str]]]:
    """Ask, once, whether the git repositories the uninstall keeps go too. Default: no.

    Returns what the person agreed to delete and, only when they agreed, the rest with why
    each stays. A refusal, --yes, or no terminal to ask on all mean the same: nothing of
    theirs is deleted and the plan's KEPT list stands as printed.
    """
    if not plan.repos:
        return [], []
    if assume_yes:
        print("  " + dim("Your git repositories stay: --yes never deletes them. Run without it to be asked."))
        return [], []
    why = {repo: unsaved_work(repo, accounted_for(plan)) for repo in plan.repos}
    safe = [repo for repo in plan.repos if not why[repo]]
    unsafe = [(repo, why[repo]) for repo in plan.repos if why[repo]]
    if unsafe:
        print("  These git repositories stay whatever you answer — deleting them would lose work:")
        for repo, reason in unsafe:
            print(f"    {plan.repos[repo]} — {reason}")
    if not safe:
        print()
        return [], []
    print("  These git repositories would be kept, and everything in them is committed and pushed:")
    for repo in safe:
        print(f"    {plan.repos[repo]}" + (" — the install folder" if repo == plan.here else ""))
    try:
        answer = prompt("  Delete these too? [y/N] ").strip().lower()
    except SetupError:
        answer = ""
    print()
    if answer not in ("y", "yes"):
        return [], []
    return safe, unsafe


def _rmtree(path: str) -> None:
    """shutil.rmtree that also takes read-only files, which Windows refuses to delete and git
    makes every object it stores."""
    def writable_then_retry(func, failed, _):
        os.chmod(failed, stat.S_IWRITE)
        func(failed)
    shutil.rmtree(path, onerror=writable_then_retry)


def remove_tree(root: str, keep: list[str]) -> list[tuple[str, str]]:
    """Delete [root], except the paths in [keep] and the folders that lead to them.

    By hand rather than shutil.rmtree, which cannot leave a hole in the middle of a tree. A
    symlink is removed as a link and never followed: the folder may hold a link to somewhere
    else on the machine, and deleting through it would delete that somewhere else. Returns
    what could not be removed, with why.
    """
    failures: list[tuple[str, str]] = []
    keep_real = {os.path.realpath(k) for k in keep}

    def holds_kept(path: str) -> bool:
        real = os.path.realpath(path)
        return any(k == real or k.startswith(real + os.sep) for k in keep_real)

    def visit(path: str) -> None:
        try:
            entries = list(os.scandir(path))
        except OSError as e:
            failures.append((path, str(e)))
            return
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    os.remove(entry.path)
                elif os.path.realpath(entry.path) in keep_real:
                    continue
                elif holds_kept(entry.path):
                    visit(entry.path)
                else:
                    _rmtree(entry.path)
            except OSError as e:
                failures.append((entry.path, e.strerror or str(e)))
        if not holds_kept(path):
            try:
                os.rmdir(path)
            except OSError as e:
                failures.append((path, e.strerror or str(e)))

    visit(root)
    return failures


def sweep_project() -> None:
    """Whatever `compose down` did not reach, by this appliance's own label.

    `down --volumes` removes what the CURRENT compose files declare. A volume made by an older
    release, or by a graph store this .env no longer selects, is declared by nothing compose is
    shown — and it is a world's worth of data left on disk under the appliance's name.
    """
    label = f"label=com.docker.compose.project={compose_project()}"
    for kind, listing, remove in (("container", ("ps", "-aq"), ("rm", "-f")),
                                  ("volume", ("volume", "ls", "-q"), ("volume", "rm", "-f")),
                                  ("network", ("network", "ls", "-q"), ("network", "rm"))):
        found = _docker(*listing, "--filter", label, timeout=30)
        names = found.stdout.split() if found and found.returncode == 0 else []
        if not names:
            continue
        run = _docker(*remove, *names, timeout=120)
        if run and run.returncode == 0:
            print(f"  Removed {len(names)} more {kind}(s) left from an earlier setup.")
        else:
            print(f"  {warn('!')} Could not remove {len(names)} {kind}(s): "
                  + ((run.stderr or "").strip().splitlines() or ["docker refused"])[-1])


def uninstall(assume_yes: bool = False, keep_images: bool = False, keep_dir: bool = False) -> None:
    """--uninstall: take away everything the installer put on this machine, and say what stayed."""
    plan = Plan(keep_images, keep_dir)
    plan.show()
    if assume_yes:
        print("  " + dim("Confirmed on the command line (--yes)."))
    else:
        answer = prompt("  Type 'yes' to uninstall: ").strip().lower()
        if answer != "yes":
            raise SetupError("Not uninstalled — nothing was touched.")
    print()
    delete, stays = choose_repos(plan, assume_yes)

    remained: list[tuple[str, str]] = [] + plan.kept
    if plan.docker == "ok":
        take_everything_down()
        sweep_project()
        if plan.sandboxes:
            removed = prune_sandboxes(plan.sandboxes)
            if removed:
                print(f"  Removed {removed} code-sandbox container(s).")
            else:
                remained.append((f"{len(plan.sandboxes)} code-sandbox container(s)", "Docker would not remove them"))

    # Before the settings go: unwiring checks each registration against this appliance's
    # ports, and those ports live in the settings file.
    unwire_coding_agents()
    for path in (env_path(), os.path.join(APPLIANCE_DIR, OVERRIDE_FILE)):
        if os.path.exists(path):
            try:
                os.remove(path)
                print(f"  Removed {tilde(path)}.")
            except OSError as e:
                remained.append((tilde(path), e.strerror or str(e)))

    if not plan.others_here:
        remove_cli_shim()

    for ref in plan.images:
        run = _docker("image", "rm", ref, timeout=300)
        if run and run.returncode == 0:
            print(f"  Removed the image {ref}.")
        else:
            remained.append((ref, ((run.stderr if run else "") or "docker refused").strip().splitlines()[-1]))
    if plan.model:
        run = _docker("model", "rm", EMBEDDING_MODEL, timeout=300)
        if run and run.returncode == 0:
            print(f"  Removed the embedding model {EMBEDDING_MODEL}.")
        else:
            remained.append((EMBEDDING_MODEL, "Docker Model Runner would not remove it"))

    # The person's repositories, when they said so. One at a time, each checked again just before
    # it goes: the answer may be minutes old, a sibling's remote may take a minute to answer, and
    # a commit made meanwhile is exactly what must not be lost. The install folder itself goes
    # last, below, with the installer's own files; its check is the last thing before that.
    if delete:
        os.chdir(os.path.expanduser("~"))
        remained = [k for k in remained if k[0] not in plan.repos.values()]
        remained += [(plan.repos[repo], f"not deleted: {why}") for repo, why in stays]
        for repo in sorted(delete, key=lambda r: r == plan.here):
            why = unsaved_work(repo, accounted_for(plan))
            if why:
                remained.append((plan.repos[repo], f"not deleted: {why}"))
            elif repo == plan.here:
                plan.directory = plan.here
            else:
                try:
                    _rmtree(repo)
                    print(f"  Removed {plan.repos[repo]}.")
                except OSError as e:
                    remained.append((plan.repos[repo], f"could not be removed: {e.strerror or e}"))
        if plan.directory:
            plan.kept_repos = [r for r in git_repositories_inside(plan.here) if os.path.lexists(r)]

    # LAST, because it is the folder this program is running from. Everything above has been
    # imported and run; from here on only what is already loaded is used, and the working
    # directory moves out first — Windows refuses to delete a directory a process stands in.
    if plan.directory:
        os.chdir(os.path.expanduser("~"))
        failures = remove_tree(plan.directory, plan.kept_repos)
        # A link the person installed THROUGH goes too, once what it pointed at has gone.
        link = os.path.abspath(APPLIANCE_DIR)
        if not failures and not plan.kept_repos and os.path.islink(link):
            try:
                os.remove(link)
            except OSError as e:
                failures.append((link, e.strerror or str(e)))
        for path, why in failures:
            remained.append((tilde(path), f"could not be removed: {why}"))
        if not failures and plan.kept_repos:
            print(f"  Removed everything in {plan.shown} but the repositories kept below.")
        elif not failures:
            print(f"  Removed {plan.shown}.")
            parent = os.path.dirname(link)
            if is_installers_parent(parent):
                try:
                    os.rmdir(parent)
                    print(f"  Removed {tilde(parent)}.")
                except OSError:
                    # Something else of the person's is in there, and it is theirs.
                    remained.append((tilde(parent), "it holds other things of yours"))

    print()
    # "Embabel is uninstalled" only when it is true of the machine, not just of this appliance.
    if plan.others_here or plan.others_anywhere:
        which = "This appliance" if instance() == DEFAULT_INSTANCE else f"The '{instance()}' appliance"
        print("  " + bold(f"{which} is uninstalled.") + " "
              + dim(f"Other Embabel installations here: {', '.join(plan.others_anywhere or plan.others_here)}."))
    else:
        print("  " + bold("Embabel is uninstalled."))
    if remained:
        print("  Still on this machine:")
        for what, why in remained:
            print(f"    {what} — {why}")
    print()
    sys.stdout.flush()
