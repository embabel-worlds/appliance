"""Host folders shared with the appliance, read-only: document folders and source trees.

A mount is the ONE thing the appliance cannot do for itself. The server is in a
container and the console is another container, so neither can bind a host
directory — only a process on the host can, which is why this lives here beside
the realms link rather than behind an endpoint.

TWO KINDS, because the container path means different things to each:

  folder  a curated document collection, mounted at /local/<name>. The name IS
          the identity, and the indexer commits documents keyed by their
          file:///local/... URL — so re-pathing an existing folder orphans
          everything already ingested. These are what the Me app's "Local files"
          panel writes, and this module reads and writes the same file.

  tree    a source tree, mounted at its OWN path (identity mount: host path ==
          container path). Not tidiness — a git worktree's .git is a FILE holding
          an absolute `gitdir:` pointer into the main repo, and a symlink between
          two checkouts is an absolute path too. Mount a tree at /local/dev and
          every worktree under it is unreadable, because the path it names does
          not exist in the container. Identity mounting also makes File.url
          openable on the host exactly as stored, with no translation table.

Both live in docker-compose.override.yml — the one filename compose merges into
plain `docker compose up` by convention — under whichever service the mode being
started defines. That last part is why `retarget` exists: the file used to be
written for `assistant` only, and merging an `assistant` block into the worlds
compose fabricates an image-less service, so the worlds mode simply never got
mounts. The mount SET is mode-independent; only the key it hangs under is not.

Nothing here writes anything a person wrote. The override file carries a marker
first line, and a file without it is somebody's hand work: refused, never eaten.
"""
import base64
import os
import re
import subprocess
import sys

from .colour import MIDDOT, TICK, bold, dim, warn
from .core import APPLIANCE_DIR, MODE_SERVICE, OVERRIDE_FILE, SetupError, prompt
from .realms import DOCKER_SHARED_PREFIXES
from .settings import set_env_var
from .words import say

# ── the file ────────────────────────────────────────────────────────────────

# First line of every override this program writes. Read as a PREFIX, so the
# `# Written by Embabel Me` files existing installs already have are still ours
# — the Me app writes that one, and the two writers must recognise each other's
# work or each would refuse to touch the other's file.
MARKER = "# Written by Embabel"

# Where curated document folders land. Source trees do not use it (see module doc).
MOUNT_ROOT = "/local"

# Per-world directory holding one symlink per source tree, beside the single
# `data/local -> /local` symlink that serves every document folder. Two roots
# rather than one because identity-mounted trees have no common parent to link.
TREE_LINK_DIR = "trees"

KIND_FOLDER = "folder"
KIND_TREE = "tree"

# A volume line, with the trailing annotation that says which kind it is. The
# annotation is OURS — compose ignores YAML comments — and `# tree` is what keeps
# an identity mount from being read back as a document folder that happens to
# live at a strange path.
MOUNT_LINE = re.compile(r'^\s*-\s*"(.+):([^:"]+):ro"\s*(#\s*(index|tree))?\s*$')
ENV_LINE = re.compile(r"^\s*-\s*([A-Z0-9_]+)=(.*)$")

# Written into .env once the first-run question has been put, so a second run
# does not ask again. Absence of mounts cannot carry this: a declined offer and
# a never-asked machine both have no mounts.
ASKED_VAR = "EMBABEL_MOUNTS_ASKED"

# Directories a developer's source actually lives in, most conventional first.
# Probed rather than asked for: at first run these already exist, which is the
# whole difference from the realms directory (see ensure_mounts).
TREE_CANDIDATES = (
    "~/dev", "~/src", "~/code", "~/Code", "~/Projects", "~/projects",
    "~/workspace", "~/repos", "~/git", "~/work",
)

# How deep to look for repositories when counting. Two levels covers both
# ~/dev/<repo> and ~/dev/<org>/<repo>; deeper is a walk of somebody's whole disk
# to answer a question that only needs a number.
PROBE_DEPTH = 2


class Mount:
    """One shared directory. `target` is where it appears inside the container."""

    def __init__(self, host: str, target: str, kind: str = KIND_FOLDER, index: bool = False):
        self.host = host
        self.target = target
        self.kind = kind
        self.index = index

    @property
    def name(self) -> str:
        return os.path.basename(self.target.rstrip("/")) or self.target

    def __repr__(self) -> str:
        return f"Mount({self.host} -> {self.target}, {self.kind})"


def _override_path() -> str:
    return os.path.join(APPLIANCE_DIR, OVERRIDE_FILE)


def read() -> tuple[list[Mount], dict]:
    """Everything this program owns in the override file.

    BOTH halves come back together because write() rewrites the whole file:
    reading only the mounts and writing back would silently drop the environment
    the Models panel keeps there, and vice versa.
    """
    path = _override_path()
    if not os.path.exists(path):
        return [], {}
    with open(path) as f:
        text = f.read()
    if not text.startswith(MARKER):
        raise SetupError(
            f"{OVERRIDE_FILE} exists but was not written by the appliance — "
            "edit or remove it yourself."
        )
    mounts: list[Mount] = []
    env: dict = {}
    for line in text.split("\n"):
        found = MOUNT_LINE.match(line)
        if found:
            host, target, _, note = found.group(1), found.group(2), found.group(3), found.group(4)
            kind = KIND_TREE if note == "tree" else KIND_FOLDER
            mounts.append(Mount(host, target, kind, index=(note == "index")))
            continue
        variable = ENV_LINE.match(line)
        if variable:
            env[variable.group(1)] = variable.group(2)
    return mounts, env


def write(mounts: list[Mount], env: dict, service: str) -> None:
    """Rewrite the override for one compose service.

    Nothing to say is NO FILE rather than an empty override: `services: <x>: {}`
    would still merge, and plain `up` should see exactly what is set.
    """
    path = _override_path()
    entries = [(k, v) for k, v in env.items() if v not in ("", None)]
    if not mounts and not entries:
        if os.path.exists(path):
            os.remove(path)
        return
    lines = [
        f"{MARKER} — the CLI and the Me app rewrite this file; don't edit it by hand.",
        "# Directories below are visible READ-ONLY inside the container. A `# tree`",
        "# mount is identity-mapped (same path both sides) because source trees hold",
        "# absolute paths — git worktree pointers, symlinks — that only resolve that",
        "# way. Any environment below is appliance settings. Gitignored: this is this",
        "# machine's business.",
        "services:",
        f"  {service}:",
    ]
    if mounts:
        lines.append("    volumes:")
        for m in mounts:
            note = " # tree" if m.kind == KIND_TREE else (" # index" if m.index else "")
            lines.append(f'      - "{m.host}:{m.target}:ro"{note}')
    if entries:
        lines.append("    environment:")
        lines += [f"      - {k}={v}" for k, v in entries]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def retarget(mode: str) -> None:
    """Point the override at the service the mode about to start actually defines.

    Called from the compose wrapper rather than from each caller, because every
    path that starts a container — up, restart, upgrade — needs it and a caller
    that forgets produces the silent failure this whole module exists to end:
    everything starts, nothing is mounted, nothing says so.
    """
    service = MODE_SERVICE.get(mode)
    if not service or not os.path.exists(_override_path()):
        return
    try:
        mounts, env = read()
    except SetupError:
        return  # somebody's own file: not ours to retarget
    with open(_override_path()) as f:
        if f"\n  {service}:\n" in f.read():
            return  # already right
    write(mounts, env, service)


# ── looking at a candidate ──────────────────────────────────────────────────

def _is_repo(path: str) -> bool:
    """A git checkout OR a worktree — the latter has .git as a FILE, not a dir."""
    return os.path.exists(os.path.join(path, ".git"))


def count_repos(path: str, depth: int = PROBE_DEPTH) -> int:
    """Repositories at or below `path`, to `depth` levels. A count, not an index."""
    if _is_repo(path):
        return 1
    if depth <= 0:
        return 0
    total = 0
    try:
        entries = sorted(os.listdir(path))
    except OSError:
        return 0
    for entry in entries:
        if entry.startswith("."):
            continue
        child = os.path.join(path, entry)
        if os.path.isdir(child) and not os.path.islink(child):
            total += count_repos(child, depth - 1)
    return total


def inspect_tree(raw: str) -> tuple[str | None, int, list[str]]:
    """Look at a candidate source tree and say what is there.

    Returns (resolved absolute path or None, repository count, notes). None means
    it cannot be used and the first note says why — nothing is raised, because the
    interactive path wants to ask again rather than exit.
    """
    path = os.path.realpath(os.path.expanduser(raw.strip()))
    notes: list[str] = []

    if not os.path.exists(path):
        return None, 0, [f"{path} does not exist."]
    if not os.path.isdir(path):
        return None, 0, [f"{path} is not a directory."]
    if not os.access(path, os.R_OK | os.X_OK):
        return None, 0, [f"{path} is not readable."]
    if any(ch in path for ch in '":\\'):
        return None, 0, [f"{path} contains a quote, colon or backslash — compose cannot express it."]

    # The likeliest mistake by a distance, and the same one the realms link
    # catches: pointing at ONE repository instead of the directory repositories
    # live in. Mounting the repo works and then every sibling is invisible.
    if _is_repo(path):
        notes.append(
            f"{path} is itself a repository — mounting the directory it lives in "
            f"({os.path.dirname(path)}) means adding a repo is a clone, not a mount and a restart."
        )

    if sys.platform == "darwin" and not path.startswith(DOCKER_SHARED_PREFIXES):
        notes.append("Docker Desktop does not share this path by default — the mount would be EMPTY.")
        notes.append("Add it under Settings -> Resources -> File sharing, or choose a path under /Users.")

    return path, count_repos(path), notes


def probe_trees() -> list[tuple[str, int]]:
    """Conventional source directories that exist on THIS machine, richest first."""
    found: list[tuple[str, int]] = []
    seen: set[str] = set()
    for candidate in TREE_CANDIDATES:
        path = os.path.realpath(os.path.expanduser(candidate))
        if path in seen or not os.path.isdir(path):
            continue
        seen.add(path)
        repos = count_repos(path)
        if repos:
            found.append((path, repos))
    return sorted(found, key=lambda pair: -pair[1])


# ── changing the set ────────────────────────────────────────────────────────

def _target_for(host: str, kind: str, taken: set[str]) -> str:
    if kind == KIND_TREE:
        return host  # identity: the whole point
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(host.rstrip("/"))) or "folder"
    name = base
    n = 2
    while f"{MOUNT_ROOT}/{name}" in taken:
        name = f"{base}-{n}"
        n += 1
    return f"{MOUNT_ROOT}/{name}"


def add(hosts: list[str], kind: str, index: bool, mode: str) -> list[Mount]:
    """Add directories to the shared set. Returns the new full set."""
    mounts, env = read()
    taken = {m.target for m in mounts}
    for raw in hosts:
        path, _, notes = inspect_tree(raw) if kind == KIND_TREE else _inspect_folder(raw)
        if not path:
            raise SetupError(notes[0] if notes else f"{raw} cannot be shared.")
        for note in notes:
            print(f"  {warn('!')} {note}")
        if any(m.host == path for m in mounts):
            continue
        target = _target_for(path, kind, taken)
        taken.add(target)
        mounts.append(Mount(path, target, kind, index))
    write(mounts, env, MODE_SERVICE.get(mode, "assistant"))
    return mounts


def _inspect_folder(raw: str) -> tuple[str | None, int, list[str]]:
    """A document folder: the same checks as a tree, minus the repository advice."""
    path, repos, notes = inspect_tree(raw)
    return path, repos, [n for n in notes if "is itself a repository" not in n]


def remove(host: str, mode: str) -> list[Mount]:
    mounts, env = read()
    target = os.path.realpath(os.path.expanduser(host))
    kept = [m for m in mounts if m.host not in (host, target) and m.name != host]
    write(kept, env, MODE_SERVICE.get(mode, "assistant"))
    return kept


# ── first run ───────────────────────────────────────────────────────────────

def _already_asked() -> bool:
    env_file = os.path.join(APPLIANCE_DIR, ".env")
    if not os.path.exists(env_file):
        return False
    with open(env_file) as f:
        return any(line.strip().startswith(f"{ASKED_VAR}=") for line in f)


def announce(mounts: list[Mount]) -> None:
    if not mounts:
        return
    print()
    for m in mounts:
        if m.kind == KIND_TREE:
            repos = count_repos(m.host)
            detail = f"{repos} repositor{'ies' if repos != 1 else 'y'}"
        else:
            detail = "documents, indexed" if m.index else "documents"
        print(f"  {TICK} {bold(m.host)}  {dim(f'({detail})')}")
    print()
    say("mounts-linked")
    print()


def ensure_mounts(mode: str, explicit: list[str] | None) -> None:
    """Share source trees with the appliance. Must run BEFORE the mode starts.

    Compose reads the override when it CREATES a container, so a mount written
    afterwards applies to the next start rather than this one — the same reason
    the realms directory and the world template are settled here. Written before
    the first `up`, a mount costs no restart at all; every later change costs one,
    which is the honest deal and the reason `embabel mount` exists separately.

    THE QUESTION IS PUT HERE, unlike the realms directory, and the difference is
    whether it can be answered. A first worlds run has no realm checkouts, so
    naming a directory of them is a question asked exactly when it cannot be
    answered. Source trees are the opposite: ~/dev exists on a developer's machine
    before they have heard of us. So it is not only answerable — it is DETECTABLE,
    and the repository count is what explains the feature in one line.

    Only trees are offered. Document folders are the Me app's panel on macOS and
    `embabel mount add --kind folder` everywhere; splitting the two writers by
    concern is what keeps them from fighting over one file.
    """
    if explicit:
        announce(add(explicit, KIND_TREE, index=False, mode=mode))
        set_env_var(ASKED_VAR, "yes", ("# Source trees were offered at first run (./setup.py --mount).",))
        return

    try:
        existing, _ = read()
    except SetupError as e:
        print(f"  {warn('!')} {e}\n")
        return
    if existing:
        return  # already answered, either way
    if _already_asked() or not sys.stdin.isatty():
        return

    candidates = probe_trees()
    if not candidates:
        set_env_var(ASKED_VAR, "yes", ("# Source trees were offered at first run.",))
        return

    print()
    print(bold("  Your source, in your world"))
    print()
    print("  Sharing a source directory read-only lets chat, apps and scheduled jobs")
    print("  ask about your files — where something is, what changed, what mentions")
    print("  a term. Nothing is copied and nothing is uploaded: it is read where it")
    print("  lies, every time you ask. (At a terminal, your coding agent is better at")
    print("  this; the mount is for the surfaces that have no shell.)")
    print()
    for i, (path, repos) in enumerate(candidates[:4], start=1):
        lead = f"  {i}. " if len(candidates) > 1 else "  "
        print(f"{lead}{bold(path)}  {dim(f'{repos} repositor' + ('ies' if repos != 1 else 'y'))}")
    print()

    if len(candidates) == 1:
        answer = prompt(f"  Share {candidates[0][0]} read-only? [Y/n] ").strip().lower()
        chosen = [candidates[0][0]] if answer in ("", "y", "yes") else []
    else:
        answer = prompt("  Share which? [1, or 1,2 — Enter for 1, n for none] ").strip().lower()
        if answer in ("n", "no"):
            chosen = []
        else:
            picks = [p.strip() for p in (answer or "1").split(",")]
            chosen = [
                candidates[int(p) - 1][0]
                for p in picks
                if p.isdigit() and 1 <= int(p) <= len(candidates[:4])
            ]

    set_env_var(ASKED_VAR, "yes", ("# Source trees were offered at first run.",))
    if not chosen:
        print(f"  {MIDDOT} Nothing shared. `embabel mount add <dir>` whenever you want to.\n")
        return
    announce(add(chosen, KIND_TREE, index=False, mode=mode))


# ── the world side ──────────────────────────────────────────────────────────
#
# A mount makes files visible to the CONTAINER. Making them visible to virtual
# Cypher is a second step, in the data VOLUME: the walk enters through a symlink
# under the world's data/ tree and follows it only because config/world.yml
# trusts the target (filesTrustedRoots — pinned human-only at the app's own write
# seam, so the assistant can never widen it; this program, acting for the person
# who just answered the question, is exactly the out-of-band actor that setting
# expects). Done with a throwaway alpine run so it works whether or not the
# appliance is up, and survives the recreate that follows.

VOLUME_ENV = "EMBABEL_APPLIANCE_VOLUME"
DEFAULT_VOLUME = "embabel-appliance_embabel_assistant_data"
HELPER_IMAGE = "alpine:3.22"
IN_VOLUME_MARKER = "Written by Embabel"
RESOURCE_DIR = os.path.join(APPLIANCE_DIR, "me-app", "resources")

# The world-side declarations are the ONE copy, shared with the Me app's panel
# rather than duplicated here. Two provisioners writing the same file from two
# copies of the same YAML is a drift bug waiting for the first edit that only
# lands on one side.
TYPES_RESOURCE = "local-files-types.yml"
PRODUCERS_RESOURCE = "local-files-producers.yml"

# Mechanical half: directories, symlinks, and the two owned declaration files.
# Emits one WORLD line per world and one YML line carrying that world's current
# world.yml, because the trusted-roots edit is a YAML splice and belongs in
# Python rather than in an awk program embedded in a string.
PROVISION_SCRIPT = r"""
BASE=/data/embabel/assistant/users
[ -d "$BASE" ] || { echo NOWORLDS; exit 0; }
write_owned() {
  if [ -f "$1" ] && ! head -1 "$1" | grep -q "MARKER_TEXT"; then
    echo "WARN $1 exists and was not written by the appliance - left alone"
    return
  fi
  echo "$2" | base64 -d > "$1"
}
for w in "$BASE"/*/*; do
  [ -d "$w/config" ] || continue
  mkdir -p "$w/data" "$w/data/TREE_DIR" "$w/config/types" "$w/config/producers"
  ln -sfn /local "$w/data/local"
  # Rebuilt from scratch every time: a tree that was removed must stop resolving,
  # and a dangling link reads as "no files" rather than as an error.
  find "$w/data/TREE_DIR" -maxdepth 1 -type l -exec rm -f {} +
  echo "$TREES" | while IFS='	' read -r name host; do
    [ -n "$name" ] && ln -sfn "$host" "$w/data/TREE_DIR/$name"
  done
  write_owned "$w/config/types/local-files.yml" "$TYPES_B64"
  write_owned "$w/config/producers/local-files.yml" "$PRODUCERS_B64"
  [ -f "$w/config/world.yml" ] || printf '%s\n' "# World configuration." > "$w/config/world.yml"
  echo "WORLD $w"
  echo "YML $w/config/world.yml $(base64 < "$w/config/world.yml" | tr -d '\n')"
done
"""

WRITE_BACK_SCRIPT = r"""
echo "$UPDATES" | while IFS='	' read -r path b64; do
  [ -n "$path" ] || continue
  if echo "$b64" | base64 -d > "$path"; then echo "WROTE $path"; else echo "FAILED $path"; fi
done
"""

TRUSTED_KEY = "filesTrustedRoots"
TRUSTED_HEADER = f"# {IN_VOLUME_MARKER} - roots the file walk may follow into. Human-only setting."


def _docker(args: list[str], stdin: str | None = None, env: dict | None = None, timeout: int = 180):
    merged = dict(os.environ)
    merged.update(env or {})
    try:
        return subprocess.run(
            ["docker", *args], input=stdin, capture_output=True, text=True,
            timeout=timeout, env=merged,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        raise SetupError(f"docker failed: {e}")


def _volume() -> str:
    return os.environ.get(VOLUME_ENV) or DEFAULT_VOLUME


def _resource_b64(name: str) -> str:
    with open(os.path.join(RESOURCE_DIR, name), "rb") as f:
        return base64.b64encode(f.read()).decode()


def splice_trusted_roots(world_yml: str, roots: list[str]) -> tuple[str, str | None]:
    """Ensure world.yml trusts exactly `roots`, without eating a hand-written block.

    OUR block — the one under the marker header — is REPLACED rather than added to,
    because the set of trees changes and a root left behind is a symlink the walk
    follows to nothing. A block we did not write is left exactly as it is and named
    in a warning: widening somebody's own trust declaration is not ours to do.
    """
    lines = world_yml.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith(TRUSTED_KEY + ":"):
            out.append(lines[i])
            i += 1
            continue
        previous = next((l for l in reversed(out) if l.strip()), "")
        if IN_VOLUME_MARKER not in previous:
            missing = [r for r in roots if f"- {r}" not in world_yml]
            return world_yml, (
                f"{TRUSTED_KEY} is set by hand — add these roots yourself: {', '.join(missing)}"
                if missing else None
            )
        if out and IN_VOLUME_MARKER in out[-1]:
            out.pop()
        i += 1
        while i < len(lines) and lines[i].startswith("  - "):
            i += 1

    while out and not out[-1].strip():
        out.pop()
    out += ["", TRUSTED_HEADER, f"{TRUSTED_KEY}:"] + [f"  - {r}" for r in roots] + [""]
    return "\n".join(out), None


def provision(mounts: list[Mount]) -> dict:
    """Wire the current mounts into every world. Never raises for a fresh machine."""
    result = {"worlds": 0, "warnings": [], "error": None}
    if _docker(["volume", "inspect", _volume()]).returncode != 0:
        return result  # mode never started: nothing to provision yet

    trees = [m for m in mounts if m.kind == KIND_TREE]
    script = (
        PROVISION_SCRIPT
        .replace("MARKER_TEXT", IN_VOLUME_MARKER)
        .replace("TREE_DIR", TREE_LINK_DIR)
    )
    run = _docker(
        [
            "run", "-i", "--rm",
            "-e", "TYPES_B64", "-e", "PRODUCERS_B64", "-e", "TREES",
            "-v", f"{_volume()}:/data", HELPER_IMAGE, "sh", "-s",
        ],
        stdin=script,
        env={
            "TYPES_B64": _resource_b64(TYPES_RESOURCE),
            "PRODUCERS_B64": _resource_b64(PRODUCERS_RESOURCE),
            "TREES": "\n".join(f"{m.name}\t{m.host}" for m in trees),
        },
    )
    if run.returncode != 0:
        result["error"] = f"provisioning failed: {(run.stderr or run.stdout).strip()[-300:]}"
        return result

    lines = [l.strip() for l in run.stdout.split("\n")]
    result["warnings"] = [l[5:] for l in lines if l.startswith("WARN ")]
    worlds = [l[6:] for l in lines if l.startswith("WORLD ")]
    result["worlds"] = len(worlds)
    if not worlds:
        return result

    roots = [MOUNT_ROOT] + [m.host for m in trees]
    updates: list[str] = []
    for line in lines:
        if not line.startswith("YML "):
            continue
        _, path, b64 = line.split(" ", 2)
        current = base64.b64decode(b64).decode("utf-8", "replace")
        new, warning = splice_trusted_roots(current, roots)
        if warning:
            result["warnings"].append(f"{path}: {warning}")
        if new != current:
            updates.append(f"{path}\t{base64.b64encode(new.encode()).decode()}")

    if updates:
        back = _docker(
            ["run", "-i", "--rm", "-e", "UPDATES", "-v", f"{_volume()}:/data", HELPER_IMAGE, "sh", "-s"],
            stdin=WRITE_BACK_SCRIPT,
            env={"UPDATES": "\n".join(updates)},
        )
        wrote = [l for l in back.stdout.split("\n") if l.strip().startswith("WROTE ")]
        if back.returncode != 0 or len(wrote) != len(updates):
            result["error"] = (
                f"world config write failed ({len(wrote)}/{len(updates)} written): "
                f"{(back.stderr or back.stdout).strip()[-300:]}"
            )
    return result


# ── saying whether it actually worked ───────────────────────────────────────

def verify(container: str | None) -> list[tuple[Mount, str]]:
    """Resolve every mount INSIDE the running container.

    This is the step whose absence is the whole problem. Provisioning lands in the
    data volume and persists; the mount lives on the container and does not — so a
    mode switch leaves a fully configured producer walking a dangling symlink and
    returning EMPTY, which reads as "no files match" rather than "nothing is
    mounted". A wrong answer, not a smaller one.
    """
    try:
        mounts, _ = read()
    except SetupError:
        return []
    if not mounts or not container:
        return [(m, "unknown") for m in mounts]
    probe = "; ".join(
        f'[ -d "{m.target}" ] && ([ -n "$(ls -A "{m.target}" 2>/dev/null | head -1)" ] '
        f'&& echo "ok" || echo "empty") || echo "missing"'
        for m in mounts
    )
    run = _docker(["exec", container, "sh", "-c", probe], timeout=30)
    states = [l.strip() for l in run.stdout.split("\n") if l.strip()] if run.returncode == 0 else []
    return [(m, states[i] if i < len(states) else "unknown") for i, m in enumerate(mounts)]


TROUBLE = {
    "empty": "mounted but EMPTY — on macOS, share the path in Docker Desktop -> Resources -> File sharing",
    "missing": "NOT mounted — `embabel mount add` writes it, and the container recreates on next start",
    "unknown": "could not be checked (the appliance is not running)",
}


def report(container: str | None) -> None:
    """One line per mount, and nothing at all when there are none.

    A stopped appliance is NOT reported as three problems. Nothing is wrong with
    the mounts in that case — they simply cannot be checked, which is one fact
    about the appliance rather than one fact about each directory.
    """
    checked = verify(container)
    if not checked:
        return
    print()
    for mount, state in checked:
        if state in ("ok", "unknown"):
            print(f"  {TICK if state == 'ok' else MIDDOT} {mount.host} {dim(f'({mount.kind})')}")
        else:
            print(f"  {warn('!')} {mount.host} — {TROUBLE[state]}")
    if any(state == "unknown" for _, state in checked):
        print(f"  {dim('Not checked — the appliance is not running.')}")
    print()
