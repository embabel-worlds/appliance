"""Talking to Docker, and finding this instance's part of it.

Named dockerlib rather than docker so it cannot shadow the docker SDK for
anybody who later installs one — a stdlib-only program has no such dependency
today, and a name that quietly breaks the day it gains one is a poor trade for
two characters.

EVERY LOOKUP IS SCOPED BY COMPOSE PROJECT. A container is only ours if its
project label says so: matching on a name prefix caught a developer's own stack
from the assistant repo, and matching on a service label alone found the other
instance's server.
"""

from __future__ import annotations
import os
import subprocess

from .colour import BULLET, MIDDOT, dim, good, warn
from .core import (
    APPLIANCE_DIR, EMBEDDING_MODEL, MODE_COMPOSE, MODE_CORE, MODE_SERVICE, MODE_SERVICES, OVERRIDE_FILE,
    SetupError, prompt,
)
from .settings import (
    compose_project, configured_mode, env_file_value, env_path, instance, phone_home_on,
    port_base, ports_for, resume_command, PHONE_HOME_ENDPOINT,
)

# Everything else, started AFTER the mode is up and reachable. None of it is a
# boot dependency — the mode services depend only on neo4j — and between them
# they are most of a first run's download, docling especially. Pulling them
# before handing over the terminal meant staring at a progress bar for several
# gigabytes to reach a login page that needed one.
DEFERRED_SERVICES = ("sandbox-image", "prometheus", "grafana", "docling")
# What each is for, in the one line the operator sees while it arrives.
DEFERRED_WHY = {
    "docling": "structured PDF and Office conversion",
    "sandbox-image": "the code sandbox",
    "grafana": "dashboards",
    "prometheus": "metrics",
}
# Every code-sandbox container carries this label (JvmInstance.JVM_LABEL_KEY on the
# server). They are created by the app THROUGH the docker socket as siblings of the
# appliance, not as compose services — so `docker compose down` does not see them.
SANDBOX_LABEL = "embabel-jvm"
# How long to keep watching a booting container for its token before asking.
BOOT_WAIT_SECONDS = 120
# The GitHub token names the assistant itself checks, in its order
# (WorldBootstrap.resolveGitHubToken). A private realm or world template is cloned over HTTPS with
# this as the username, so without one the clone 404s and the realm is quietly absent.
GITHUB_TOKEN_VARS = ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN")
def _docker(*argv: str, timeout: int = 30) -> subprocess.CompletedProcess | None:
    try:
        # encoding + errors: on Windows, text=True without encoding uses the
        # system ANSI codepage (cp1252 in en-US), and docker log output routinely
        # contains UTF-8 bytes -- container banners, log lines, escape sequences.
        # A single non-decodable byte took down setup on the first boot when
        # token_from_logs concatenated a stdout that had come back None from a
        # dead reader thread. errors="replace" is safe here: every caller either
        # regex-matches or string-searches the output, and non-decodable bytes
        # would never have matched anyway.
        return subprocess.run(
            ["docker", *argv], capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
def running_modes() -> dict[str, str]:
    """Running mode containers of THIS instance, keyed by compose service name.

    Both labels, always. The service label alone stopped being an identity the
    moment a second instance could exist — every instance has a container
    labelled service=worlds, and matching on that would have let a backup stop
    somebody else's appliance and copy the wrong graph.
    """
    found = {}
    for service in MODE_SERVICES:
        run = _docker("ps",
                      "--filter", f"label=com.docker.compose.project={compose_project()}",
                      "--filter", f"label=com.docker.compose.service={service}",
                      "--format", "{{.Names}}")
        if run is not None and run.returncode == 0:
            for name in run.stdout.split():
                found[service] = name
    return found
def find_mode_container(prefer: str | None = None) -> str | None:
    """The running mode's container. When a mode was explicitly asked for, PREFER it:
    announcing "setting up embabel-assistant" and then refusing because the caller
    asked for worlds is a small masterpiece of unhelpfulness."""
    modes = running_modes()
    if prefer and MODE_SERVICE.get(prefer) in modes:
        return modes[MODE_SERVICE[prefer]]
    if len(modes) > 1:
        print(f"  Both modes are running ({', '.join(modes.values())}) — run one at a time.")
    return next(iter(modes.values()), None)
def mode_service(container: str) -> str | None:
    """Which mode this container is — the compose service name from its label."""
    run = _docker("inspect", "-f", '{{index .Config.Labels "com.docker.compose.service"}}', container)
    return run.stdout.strip() if run is not None and run.returncode == 0 else None
def container_started_at(container: str) -> str:
    run = _docker("inspect", "-f", "{{.State.StartedAt}}", container, timeout=15)
    return run.stdout.strip() if run and run.returncode == 0 else ""
def container_status(container: str) -> str:
    """Docker's own word for what this container is doing — running, exited, created,
    restarting — or "" if it cannot be asked. Used where "the container exists" and "the
    container is up" have been confused, which is how somebody ends up being asked for a
    token printed by a process that never ran."""
    run = _docker("inspect", "-f", "{{.State.Status}}", container, timeout=15)
    return run.stdout.strip() if run and run.returncode == 0 else ""
def container_base_url(container: str) -> str | None:
    """The mode's URL from its own SERVER_PORT. The compose files keep the host and
    container ports equal by design, so the container's port IS the published one."""
    run = _docker("inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", container)
    if run is not None and run.returncode == 0:
        for line in run.stdout.splitlines():
            if line.startswith("SERVER_PORT="):
                return f"http://localhost:{line.split('=', 1)[1].strip()}"
    return None
def github_token() -> tuple[str, str] | None:
    """A GitHub token for cloning PRIVATE realms and world templates, and where it came from.

    Same courtesy the provider keys get in `from_environment`: a developer who already has one
    should not be asked for it. The difference is where it lives. An OpenAI key is an env var or
    nothing, but a GitHub token on a working machine is usually in the `gh` CLI's own store and NOT
    exported — so the env vars are checked first (any of the three the assistant reads) and `gh` is
    the fallback that makes it automatic for most people.

    Returns None when there is nothing to find, which is the ordinary case for a user who only ever
    installs public realms. Nothing is written to disk: see `compose_env`.
    """
    for var in GITHUB_TOKEN_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value, f"${var}"
    try:
        run = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10,
                             encoding="utf-8", errors="replace")
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None  # no gh, or it misbehaved — a public-realm install is unaffected
    token = run.stdout.strip() if run.returncode == 0 else ""
    return (token, "the gh CLI") if token else None
def compose_env() -> dict:
    """The environment `docker compose` runs with.

    A discovered token is passed to the compose PROCESS rather than written to `.env`, and that is
    deliberate. `.env` is gitignored, but it is still a plaintext credential sitting in a checkout,
    and the timezone precedent does not carry: a zone is not a secret. The cost is that a later
    hand-run `docker compose up` gets no token unless the operator exports one — which is why the
    line printed below names the variable rather than just saying it worked.
    """
    env = dict(os.environ)
    found = github_token()
    if found:
        env["GITHUB_TOKEN"] = found[0]
    # The port block, computed rather than trusted. The settings file carries it
    # too — this is what makes a hand-edited or half-written file produce a
    # working appliance on the right ports instead of a confusing half-collision.
    for var, value in ports_for(port_base()).items():
        env[var] = str(value)
    env["EMBABEL_INSTANCE"] = instance()
    # The switch resolved to an address. Empty is the off state the compose files
    # already default to; this only ever turns it ON.
    env["ASSISTANT_PHONE_HOME_ENDPOINT"] = PHONE_HOME_ENDPOINT if phone_home_on() else ""
    return env
def announce_github_token() -> None:
    """Say once, before the containers start, whether private realms will resolve. Never print the
    token itself — this runs in terminals people screen-share."""
    found = github_token()
    if not found:
        return
    print(f"  Found a GitHub token ({found[1]}) — private realms and world templates will clone.")
    print("  It is passed to the containers for this run only, never written to .env.\n")
# The overlay that adds the local embedding model, and the .env answer that turns it on.
def embeddings_local_file(mode: str) -> str:
    """The overlay for this mode. One per mode — see the note in the files themselves."""
    return f"embeddings-local-{mode}.yml"
LOCAL_EMBEDDING_MODEL = "docker.io/ai/qwen3-embedding:0.6B-F16"


def core_services(mode: str) -> tuple[str, ...]:
    """MODE_CORE, adjusted for the graph engine. `compose up <name>` force-activates
    a service even when an overlay parks it behind a profile, so the bring-up list
    itself must know the engine: FalkorDB swaps the graph service, MEMORY brings
    none at all — the engine lives inside the app."""
    engine = graph_engine()
    base = MODE_CORE[mode]
    if engine == "NEO4J":
        return base
    rest = tuple(s for s in base if s != "neo4j")
    return ("falkordb",) + rest if engine == "FALKORDB" else rest


def graph_engine() -> str:
    """The engine this appliance runs — GRAPH_TYPE in .env; NEO4J when unset."""
    chosen = os.environ.get("GRAPH_TYPE") or env_file_value("GRAPH_TYPE")
    return (chosen or "NEO4J").strip().upper()


def graph_overlay_file(mode: str) -> str | None:
    """The engine overlay for this mode, or None for the default engine.
    One file per mode per engine — see the note in the files."""
    engine = graph_engine()
    if engine == "FALKORDB":
        return f"graph-falkordb-{mode}.yml"
    if engine == "MEMORY":
        return f"graph-memory-{mode}.yml"
    return None


def local_embeddings_wanted() -> bool:
    """Whether this appliance runs the local embedder.

    Keyed off the model NAME rather than a separate switch, so there is one answer to
    "which embedding model" and no way for a flag and a name to disagree — which would
    show up as a compose file pulling a model the app never asks for, or the reverse.
    """
    chosen = os.environ.get("ASSISTANT_EMBEDDING_MODEL") or env_file_value("ASSISTANT_EMBEDDING_MODEL")
    return bool(chosen) and chosen.startswith(("docker.io/ai/", "ai/"))


def _compose(mode: str, *argv: str, capture: bool = False):
    """docker compose against the mode's file, from the appliance directory.
    capture=False inherits stdout/stderr — pulls and boots narrate themselves."""
    # -p and --env-file are what make a second instance a second instance: the
    # project prefixes every container, volume and network, and the settings file
    # carries that instance's port block. For the default instance both resolve to
    # exactly what compose would have done on its own.
    # --env-file ONLY IF IT EXISTS. Compose treats a named-but-missing env file as
    # an error, and a fresh clone has no .env yet — passing it unconditionally
    # made every compose call fail on exactly the path a new user is on, and
    # again after `uninstall`. Omitting it is also correct: compose then reads
    # `.env` from the project directory by convention, which for the default
    # instance is the same file.
    cmd = ["docker", "compose", "-p", compose_project()]
    if os.path.exists(env_path()):
        cmd += ["--env-file", env_path()]
    cmd += ["-f", MODE_COMPOSE[mode]]
    if mode == "me" and os.path.exists(OVERRIDE_FILE):
        cmd += ["-f", OVERRIDE_FILE]
    # THE LOCAL EMBEDDER IS AN OVERLAY, not a default. It needs Docker Model Runner,
    # which is a Docker Desktop feature — requiring it made every install download
    # ~1.1GB before anybody had uploaded a document, and failed outright on plain
    # Docker Engine. Composed in only once somebody has asked for it, which is the
    # one line in .env that `embabel embeddings use local` writes.
    overlay = embeddings_local_file(mode)
    if local_embeddings_wanted() and os.path.exists(overlay):
        cmd += ["-f", overlay]
    # THE GRAPH ENGINE IS AN OVERLAY TOO, same posture as the embedder: the default
    # composition is Neo4j and stays byte-identical unless .env says otherwise. One
    # answer — GRAPH_TYPE — selects the engine for compose AND inside the app, so a
    # flag and the app's connection type can never disagree.
    graph_overlay = graph_overlay_file(mode)
    if graph_overlay and os.path.exists(graph_overlay):
        cmd += ["-f", graph_overlay]
    cmd += argv
    try:
        return subprocess.run(cmd, capture_output=capture, text=True, env=compose_env(),
                              encoding="utf-8", errors="replace")
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        raise SetupError(f"docker compose failed: {e}")
def take_everything_down() -> None:
    """Every service and volume in the project, whichever mode was last up. Both
    mode files merged so nothing is missed — shared by --fresh and --uninstall,
    which differ in what they take with them, not in how they stop.

    Compose's own output is CAPTURED rather than shown. It ends on lines like
    "! Network embabel-appliance_default  Resource is still in use", which is not
    a failure — it is compose declining to break a network that a container of
    yours, from some other project, is attached to. Shown raw as the last thing
    before "Done", it reads as the uninstall having failed. So: run it quietly,
    then report what is actually gone by looking.
    """
    # -p, or this tears down the DEFAULT instance whichever one you asked for —
    # the mode files carry `name: embabel-appliance`, so without it every
    # instance's uninstall would delete the same appliance.
    cmd = ["docker", "compose", "-p", compose_project()]
    if os.path.exists(env_path()):  # missing is normal mid-uninstall; see _compose
        cmd += ["--env-file", env_path()]
    cmd += ["-f", MODE_COMPOSE["me"], "-f", MODE_COMPOSE["worlds"],
            "down", "--volumes", "--remove-orphans"]
    run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    # BY PROJECT LABEL, not by name. Matching "embabel-" caught embabel-assistant-neo4j
    # and embabel-assistant-docling — a developer's own stack from the assistant repo,
    # a different compose project entirely — and reported somebody else's healthy
    # containers as an uninstall that had failed.
    left = _docker("ps", "-a", "--filter", f"label=com.docker.compose.project={compose_project()}",
                   "--format", "{{.Names}}")
    remaining = [n.strip() for n in (left.stdout.splitlines() if left and left.returncode == 0 else [])
                 if n.strip()]
    if remaining:
        print(f"  ! {len(remaining)} container(s) would not stop: {', '.join(remaining)}")
        print(f"    {(run.stderr or run.stdout).strip().splitlines()[-1] if (run.stderr or run.stdout).strip() else ''}")
    else:
        print("  Containers and volumes removed.")

    # The network is the one thing a foreign container can hold open. Name the
    # holder: "resource is still in use" is a fact about somebody's OTHER project,
    # and without the name it reads as this uninstall having left a mess.
    net = _docker("network", "inspect", f"{compose_project()}_default",
                  "--format", "{{range .Containers}}{{.Name}} {{end}}")
    if net and net.returncode == 0 and net.stdout.strip():
        holders = net.stdout.split()
        print(f"  Kept the network: {', '.join(holders)} is attached to it.")
        print("    Not ours to disconnect — it belongs to another compose project.")
def fresh_wipe() -> None:
    """--fresh: delete the whole appliance state after saying exactly what dies."""
    print("  --fresh DELETES the appliance's entire state:")
    print("    account and password, world, knowledge graph, documents, dashboards.")
    print("  Images and the local embedding model survive; nothing else does.")
    answer = prompt("  Type 'yes' to wipe: ").strip().lower()
    if answer != "yes":
        raise SetupError("Not wiped — nothing was touched.")
    take_everything_down()
    print()
# The mode's image list, resolved once. `compose config` costs about a second,
# which is fine at the start of a wait and not fine every three seconds.
_IMAGES_FOR_MODE: dict[str, list[str]] = {}
# Image name fragment -> what a person is waiting FOR. A tag tells somebody
# nothing about why their install is slow; "structured PDF and Office
# conversion" tells them what they lose by not waiting.
IMAGE_PURPOSE = {
    "assistant": "the appliance itself",
    "worlds-console": "the console",
    "neo4j": "the graph",
    "docling": "structured PDF and Office conversion",
    "sandbox": "the code sandbox",
    "grafana": "dashboards",
    "prometheus": "metrics",
    "open-webui": "the alternative chat UI",
}

# Approximate pull sizes, stated so a wait explains itself: "pulling the appliance
# itself ~1.7 GB" and "pulling docling" are the same minute felt two different ways.
# Approximate on purpose — these drift a little per release, and "~7 GB" stays true.
IMAGE_SIZE = {
    "docling": "~7 GB",
    "sandbox": "~2.6 GB",
    "assistant": "~1.7 GB",
    "neo4j": "~600 MB",
    "grafana": "~400 MB",
    "prometheus": "~300 MB",
    "worlds-console": "~80 MB",
}

# The images the mode WAITS on. Everything else arrives via start_deferred while
# the wizard runs, so the progress line must not count it against the boot — a
# counter that says 3/7 during a 3-image boot reads as a stall (observed on a
# fresh install, 2026-08-25). "sandbox" is checked FIRST when classifying: the
# sandbox image name contains "assistant" too, and matching that fragment would
# promote a background pull into the boot count.
BOOT_IMAGE_FRAGMENTS = ("neo4j", "worlds-console", "assistant")

def _boots(image: str) -> bool:
    if "sandbox" in image:
        return False
    return any(fragment in image for fragment in BOOT_IMAGE_FRAGMENTS)

def _describe(image: str) -> str:
    for fragment, purpose in IMAGE_PURPOSE.items():
        if fragment in image:
            size = IMAGE_SIZE.get(fragment)
            return purpose + (f" {size}" if size else "")
    return "an image"
def images_for(mode: str) -> list[str]:
    if mode not in _IMAGES_FOR_MODE:
        run = _compose(mode, "config", "--images", capture=True)
        _IMAGES_FOR_MODE[mode] = sorted({
            line.strip() for line in (run.stdout.splitlines() if run and run.returncode == 0 else [])
            if line.strip()
        })
    return _IMAGES_FOR_MODE[mode]
def image_progress(mode: str) -> str:
    """What Docker is doing, in the only terms it will honestly give us.

    THERE IS NO PERCENTAGE TO REPORT. Docker exposes pull progress to the client
    that started the pull and nowhere else; `docker system df` would give a
    growing byte total but takes four seconds on a real machine, which is longer
    than the poll interval. What IS cheap — 63ms — is asking whether each image
    the mode needs has arrived.

    So: a count, and the NAME of what is still coming, in terms of what it does.
    "Pulling docling" means nothing to somebody watching an install stall;
    "structured PDF and Office conversion" tells them what the wait buys.
    """
    needed = images_for(mode)
    if not needed:
        return ""
    def present(image: str) -> bool:
        run = _docker("image", "inspect", image, "--format", "ok", timeout=10)
        return run is not None and run.returncode == 0

    missing = [image for image in needed if not present(image)]
    if not missing:
        return ""
    # The BOOT count is the promise being kept; the background pulls are news, not
    # a debt. Counting them together made a three-image boot read as "3/7, stuck".
    boot = [image for image in needed if _boots(image)]
    boot_missing = [image for image in missing if _boots(image)]
    background_missing = [image for image in missing if not _boots(image)]
    if boot_missing:
        # One named with its size. This shares a line with three lamps and a clock,
        # and a status line that wraps leaves its first half behind at every redraw.
        shown = _describe(sorted(boot_missing)[0])
        if len(boot_missing) > 1:
            shown += f" +{len(boot_missing) - 1}"
        tail = f" · {len(background_missing)} more in background" if background_missing else ""
        return (dim(f"boot images {len(boot) - len(boot_missing)}/{len(boot)}")
                + dim(" · pulling ") + shown + dim(tail))
    # Boot is satisfied — whatever remains downloads behind the running appliance.
    shown = _describe(sorted(background_missing)[0])
    if len(background_missing) > 1:
        shown += f" +{len(background_missing) - 1}"
    return dim("background: ") + shown + dim(" still downloading")
def mode_of(container: str | None) -> str:
    """Which mode a container belongs to, for looking up its image list."""
    service = mode_service(container) if container else None
    return "me" if service == "assistant" else "worlds"
def find_graph_container() -> str | None:
    run = _docker("ps", "--filter", f"label=com.docker.compose.project={compose_project()}",
                  "--filter", "label=com.docker.compose.service=neo4j",
                  "--format", "{{.Names}}", timeout=15)
    names = run.stdout.split() if run and run.returncode == 0 else []
    return names[0] if names else None
def _answers(base: str) -> bool:
    """Does the door answer at all? Any HTTP status counts — 401 is an answer."""
    try:
        import urllib.request
        urllib.request.urlopen(base, timeout=2)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False
# ── what is on the host, and getting rid of what should not be ──────────────

def appliance_containers() -> list[dict]:
    """Every container belonging to this compose project, running or not.

    BY PROJECT LABEL, never by name prefix. A developer's own stack from the
    assistant repo is called embabel-assistant-something too, and reporting
    their containers as the appliance's is how `uninstall` once claimed to have
    failed — see take_everything_down for the same lesson learned the same way.
    """
    run = _docker("ps", "-a", "--filter", f"label=com.docker.compose.project={compose_project()}",
                  "--format", "{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Image}}", timeout=20)
    if not run or run.returncode != 0:
        return []
    found = []
    for line in run.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            found.append(dict(zip(("name", "state", "status", "image"), (p.strip() for p in parts))))
    return sorted(found, key=lambda c: c["name"])
def prune_sandboxes(names: list[str]) -> int:
    """Remove the named sandbox containers. The CALLER decides which and asks —
    see remove_stray_sandboxes for why this must never guess: a developer running
    an assistant from an IDE has sandboxes carrying the same label, and killing
    those mid-session is the exact bug the per-jvm scoping exists to prevent."""
    if not names:
        return 0
    run = _docker("rm", "-f", *names, timeout=120)
    return len(names) if run and run.returncode == 0 else 0
# The server stamps every sandbox with this (JvmInstance.INSTANCE_LABEL_KEY),
# naming the appliance it belongs to. The jvm label beside it cannot serve: its
# value is a per-process UUID, so a sandbox outlived by its JVM — the only kind
# worth pruning — matches nothing the host can name.
SANDBOX_INSTANCE_LABEL = "embabel-instance"
def stray_sandbox_containers(mine_only: bool = True) -> list[str]:
    """Sandbox containers still on the host, by name.

    THIS instance's, by the label the server stamps on them. Sandboxes carry no
    compose project — they are siblings created through the docker socket, not
    services — so without that label the only filter available was the bare
    `embabel-jvm` key, which matches every appliance's and every IDE run's alike.
    Pass mine_only=False to see all of them, which is what a bug report wants.

    The server sweeps these itself, but only two of the three cases: on shutdown it
    removes containers matching ITS OWN jvm id, and on startup it reaps EXITED ones
    from any jvm. A RUNNING sandbox whose jvm died without its shutdown hook — a
    kill -9, a crashed Docker VM, a `down` that timed out into SIGKILL — is caught
    by neither, and holds its memory until somebody notices.
    """
    argv = ["ps", "-a", "--filter", f"label={SANDBOX_LABEL}"]
    if mine_only:
        argv += ["--filter", f"label={SANDBOX_INSTANCE_LABEL}={instance()}"]
    run = _docker(*argv, "--format", "{{.Names}}")
    if not run or run.returncode != 0:
        return []
    return [line.strip() for line in run.stdout.splitlines() if line.strip()]
def other_running_appliances() -> list[str]:
    """Appliances running from a DIFFERENT checkout than this one.

    The `embabel` command is one per machine and forwards to whichever checkout
    installed it, so uninstalling one appliance can take the command away from
    another that is still serving. Compose records the directory it ran in, which
    is the only thing on the host that can tell two checkouts apart.
    """
    run = _docker("ps", "--filter", "label=com.docker.compose.project",
                  "--format", '{{index .Config.Labels "com.docker.compose.project.working_dir"}}',
                  timeout=20)
    if not run or run.returncode != 0:
        run = _docker("ps", "--format", "{{.Label \"com.docker.compose.project.working_dir\"}}", timeout=20)
    if not run or run.returncode != 0:
        return []
    here = os.path.realpath(APPLIANCE_DIR)
    found = set()
    for line in run.stdout.splitlines():
        path = line.strip()
        if path and os.path.realpath(path) != here and os.path.basename(path) != "":
            # Only appliances: another compose project's working directory is
            # none of our business and must not stop an uninstall.
            if os.path.exists(os.path.join(path, "setup.py")):
                found.add(path)
    return sorted(found)


def embedding_model_present() -> bool:
    """Is the pinned embedding model actually on this machine?

    By exact tag. `latest` on that repository is the 4B variant with different
    dimensions, so "a qwen3-embedding is present" is not the question.
    """
    run = _docker("model", "list", timeout=30)
    if not run or run.returncode != 0:
        return False
    name, _, tag = EMBEDDING_MODEL.partition(":")
    short = name.split("/")[-1]
    return any(short in line and tag in line for line in run.stdout.splitlines())


def ensure_embedding_model() -> None:
    """Pull the embedding model before anything needs it, and say so while it happens.

    NOT LEFT TO COMPOSE. infra.yml declares it as a `models:` element, which is
    the right declaration and not a guarantee: that element is a recent Compose
    feature, and where it is not honoured — or where the provisioning quietly
    fails — the model never arrives. The server then cannot build its embedding
    bean and dies with an UnsatisfiedDependencyException naming Spring, forty
    seconds into a boot, having said nothing about a download.

    So it is pulled here, explicitly, before a container starts. Doing it twice
    costs nothing: `docker model pull` on a model already present returns
    immediately.

    Embeddings are the appliance's one capability that needs no key and no
    account, which makes this the difference between an appliance that works out
    of the box and one that will not start at all.
    """
    if embedding_model_present():
        return
    runner = _docker("model", "status", timeout=20)
    if not runner or runner.returncode != 0:
        # THE SAME WORDS install.sh USES, from the same file: a person can meet
        # this either in the installer or here, and two hand-written variants of
        # one explanation are two things to keep true. Imported inside the
        # function because words imports core, which imports this module.
        from .words import copy_text
        # AND WHAT TO TYPE NEXT. This message ends the run, so leaving somebody
        # with a fix and no way back means the last thing they read is a dead
        # end — they enable Model Runner and then have to remember, or find,
        # the command that got them here. Not in the copy file: install.sh
        # shares that text and only WARNS, so a "run it again" line would be
        # wrong there, and only this side knows how they invoked it anyway.
        return_here = resume_command()
        raise SetupError(
            copy_text("docker-model-runner").strip()
            + f"\n\n  Then pick up where you left off:  {return_here}"
        )
    print(f"  Downloading the embedding model {dim('(' + EMBEDDING_MODEL + ', about 1.1GB)')}.")
    print("  " + dim("Required: it turns your documents into vectors, here, with no key and no"))
    print("  " + dim("account. Docker's own progress follows."))
    # Output inherited on purpose: this is the one download whose progress bar is
    # worth the terminal, because nothing can start until it finishes.
    try:
        subprocess.run(["docker", "model", "pull", EMBEDDING_MODEL], timeout=3600)
    except (subprocess.SubprocessError, OSError) as e:
        raise SetupError(f"Could not pull the embedding model: {e}")
    if not embedding_model_present():
        raise SetupError(
            f"The embedding model {EMBEDDING_MODEL} did not arrive, and the appliance\n"
            "  cannot start without it. Try it by hand to see why:\n"
            f"    docker model pull {EMBEDDING_MODEL}"
        )
    print(f"  {good('Embedding model ready.')}\n")


def boot_failure(container: str | None) -> str | None:
    """Why the server is not coming up, if it is not coming up.

    A WAIT MUST NOTICE A DEATH. The boot wait polled for a token until its
    two-minute deadline and reported nothing else, so a server whose Spring
    context failed on the first second produced two minutes of a cheerful
    spinner and then "could not find the setup token automatically" — which
    blames the token for an application that never started.

    Two signals, both cheap. A container that has exited or is restarting is
    dead by definition. And a context that failed says so in its own log, in a
    line that names the cause far better than anything this script could infer.
    """
    if not container:
        return None
    run = _docker("inspect", "-f", "{{.State.Status}}|{{.State.ExitCode}}", container, timeout=10)
    if not run or run.returncode != 0:
        return None
    status, _, code = run.stdout.strip().partition("|")
    if status in ("exited", "dead") or (status == "restarting" and code not in ("", "0")):
        return last_startup_error(container) or f"the server container {status} (exit {code})"
    # Still "running", but Spring may have given up inside it — the JVM lingers.
    log = _docker("logs", "--tail", "80", container, timeout=20)
    text = (log.stdout + log.stderr) if log else ""
    if "Application run failed" in text or "APPLICATION FAILED TO START" in text:
        return last_startup_error(container) or "the application failed to start"
    return None


def last_startup_error(container: str) -> str | None:
    """The most useful line from a failed boot: the Spring cause if there is one,
    else the last ERROR. Trimmed, because these run to several hundred characters
    of package names before they say anything."""
    log = _docker("logs", "--tail", "200", container, timeout=20)
    if not log:
        return None
    text = (log.stdout or "") + (log.stderr or "")
    for line in reversed(text.splitlines()):
        if "UnsatisfiedDependencyException" in line or "BeanCreationException" in line:
            return _first_cause(line)
    for line in reversed(text.splitlines()):
        if " ERROR " in line and "Application run failed" not in line:
            return _first_cause(line)
    return None


def _first_cause(line: str) -> str:
    """The clause that names the problem, not the chain that led to it."""
    for marker in ("Cannot resolve reference to bean", "nested exception is",
                   "Caused by:", "Error creating bean with name"):
        if marker in line:
            line = line.split(marker, 1)[1]
    line = line.strip(" :;")
    return (line[:220] + "…") if len(line) > 220 else line


def running_mode_names() -> list[str]:
    """The modes that are up, as mode names rather than compose service names."""
    by_service = {service: mode for mode, service in MODE_SERVICE.items()}
    return [by_service[service] for service in running_modes() if service in by_service]

def backup_mode() -> str:
    """Which mode's compose file to stop, start and create volumes with.

    Whatever is running, else whatever was set up here, else me — and me is the
    fallback rather than worlds because its file is the one that defines BOTH
    volumes and can therefore create them from nothing on a fresh machine.
    """
    running = running_mode_names()
    return running[0] if running else (configured_mode() or "me")
