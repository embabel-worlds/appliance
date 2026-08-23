"""Which appliance this process is talking to, and everything that follows from
that: its compose project, its settings file, its port block, its URLs.

One appliance is the normal case and none of this should be visible to somebody
who has one. It is a module because a second install turns every one of these
from a constant into a question, and the answers all come from the same place.
"""
import os
import re

from .core import APPLIANCE_DIR, MODE_COMPOSE, SetupError

def default_base() -> str:
    """Where the Me door answers for the instance in play. A function now: the
    port moves with the instance, so a constant could only ever be right once."""
    return f"http://localhost:{ports_for(port_base())['ASSISTANT_PORT']}"
def console_url() -> str:
    """WHERE A PERSON GOES in worlds mode — not the same as where the API is. The
    worlds server also serves the old Vaadin UI, and sending a new operator there
    instead of to the console makes their first impression a surface on its way out."""
    return f"http://localhost:{ports_for(port_base())['WORLDS_CONSOLE_PORT']}"
def surface_urls() -> dict:
    """Every address this instance publishes, by the name a person uses for it."""
    p = ports_for(port_base())
    return {"me": f"http://localhost:{p['ASSISTANT_PORT']}",
            "worlds": f"http://localhost:{p['WORLDS_PORT']}",
            "console": console_url(),
            "graph": f"http://localhost:{p['NEO4J_BROWSER_PORT']}",
            "bolt": f"bolt://localhost:{p['NEO4J_BOLT_PORT']}",
            "dashboards": f"http://localhost:{p['GRAFANA_PORT']}",
            "metrics": f"http://localhost:{p['PROMETHEUS_PORT']}"}
# Machine-local configuration: keys, timezone, world template, realms directory.
# Gitignored, and removed by --uninstall — which is the difference between that
# and --fresh, since a .env that survives means the next run asks nothing.
ENV_FILE = ".env"
# The compose project both mode files declare. Anything belonging to the appliance
# carries it as a label — which is the only reliable way to tell the appliance's
# containers from a developer's own stack, whose names start the same way.
# The DEFAULT project name. Everything reads compose_project() instead, which
# resolves the instance in play — this remains only as the name that instance
# `appliance` produces, and as the answer for anything asking before an
# instance has been chosen.
COMPOSE_PROJECT = "embabel-appliance"
# The collector is fixed in the appliance image. This is repeated here because setup
# must disclose the destination before it asks the operator to finish installation;
# PHONE_HOME.md and the live endpoints remain the authoritative payload views.
# The collector, and the switch that decides whether anything reaches it. BOTH
# live in this repo rather than in the image: the address so it can be read
# before it is used, and the switch so turning reporting on is a thing an
# operator does here, deliberately, rather than a default they inherit.
#
# OFF unless .env says otherwise. The compose files name the endpoint with an
# empty default, so a hand-run `docker compose up` is off too — a switch that
# only works when you go through setup.py is not a switch.
PHONE_HOME_ENDPOINT = "https://telemetry.embabel.com/v1/appliance"
PHONE_HOME_VAR = "EMBABEL_PHONE_HOME"
PHONE_HOME_DOC_URL = "https://github.com/embabel-worlds/appliance/blob/main/PHONE_HOME.md"
def phone_home_on() -> bool:
    """Whether this appliance reports usage. False unless .env says true."""
    value = (os.environ.get(PHONE_HOME_VAR) or env_file_value(PHONE_HOME_VAR) or "").strip().lower()
    return value in ("1", "true", "yes", "on")
def set_env_var(key: str, value: str, why: tuple[str, ...] = ()) -> None:
    """Write `key=value` into .env, preserving everything else there.

    .env rather than a compose edit, always: the compose files stay pull-only, and
    a machine-local answer — a template, a checkout directory, which door you came
    through — is exactly what .env is for. An existing line is replaced where it
    sits, so the file keeps whatever shape its owner gave it; [why] is the comment
    written above a line that is new.
    """
    lines: list[str] = []
    if os.path.exists(env_file()):
        with open(env_file()) as f:
            lines = f.read().splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            break
    else:
        lines += ["", *why, f"{key}={value}"]
    with open(env_file(), "w") as f:
        f.write("\n".join(lines) + "\n")
def configured_mode() -> str | None:
    """Which door this appliance was last set up as, from .env.

    The installer's default is Me and the CLI's is Worlds, so without this a
    `embabel down` followed by `embabel up` quietly started the OTHER product on
    the same graph — a person who installed an assistant got a world runtime back,
    with nothing anywhere saying why.
    """
    value = os.environ.get("EMBABEL_MODE", "").strip()
    if value in MODE_COMPOSE:
        return value
    if os.path.exists(env_file()):
        with open(env_file()) as f:
            for line in f:
                if line.strip().startswith("EMBABEL_MODE="):
                    value = line.split("=", 1)[1].strip()
                    return value if value in MODE_COMPOSE else None
    return None
def remember_mode(mode: str) -> None:
    """Record the door, so the CLI can come back to it rather than to its default."""
    if configured_mode() == mode:
        return
    set_env_var("EMBABEL_MODE", mode, (
        "# The door this appliance was last started as. `embabel up` returns to it",
        "# instead of its own default; starting the other mode rewrites this line.",
    ))
# ── instances ───────────────────────────────────────────────────────────────
#
# ONE APPLIANCE IS THE NORMAL CASE, and nothing about this section should be
# visible to somebody who has one. There is no --instance to learn, no name to
# invent, no flag in `embabel --help`: the default instance is called
# `appliance`, its compose project is `embabel-appliance`, its settings are
# `.env`, and that is the whole story until somebody installs a second one.
#
# A SECOND ONE is what turns the machinery on. Instances differ in exactly three
# things, and everything else follows from them:
#
#   the PROJECT     embabel-<instance> — compose prefixes every volume, network
#                   and container with it, so two instances share nothing by
#                   accident. This is also why no service may declare
#                   container_name: a fixed name is global to the docker daemon
#                   and would collide on the second install.
#   the ENV FILE    .env for the default, .env.<instance> beside it. One
#                   checkout, several settings files — rather than several
#                   checkouts, each with its own copy of this script free to
#                   drift from the others.
#   the PORT BASE   sixteen consecutive host ports, EMBABEL_PORT_BASE + offset.
#                   Allocated when the instance is created, never guessed later.
#
# WHY 11042 (see PORTS below). Babel is Genesis 11. 42 is the Answer, and the
# Babel fish is out of the same book — so the number says the product's name
# rather than punning on one syllable of it. It is unassigned by IANA, absent
# from /etc/services, and sits below the Linux ephemeral floor of 32768, which
# 0xBABE (47806) does not — that one would have failed intermittently forever.

DEFAULT_INSTANCE = "appliance"
# 4242 was inherited and contended: OpenTSDB, Quassel and CrashPlan all default
# there, and CrashPlan takes 4242 AND 4243 — the assistant and the Neo4j browser.
DEFAULT_PORT_BASE = 11042
PORT_BLOCK = 16
# Offset within the block -> the variable the compose files read. Adding a
# service means taking the next free offset here; the spare tail is why there
# are sixteen and not eight.
PORT_OFFSETS = {
    "ASSISTANT_PORT": 0,        # the Me front door
    "WORLDS_PORT": 1,           # the worlds API
    "WORLDS_CONSOLE_PORT": 2,   # the Worlds front door
    "NEO4J_BROWSER_PORT": 3,
    "NEO4J_BOLT_PORT": 4,
    "GRAFANA_PORT": 5,
    "PROMETHEUS_PORT": 6,
    "OPEN_WEBUI_PORT": 7,
}
# Instance 24 would land on 11434, which is Ollama's — and the compose files
# talk to Ollama there. A ceiling nobody will reach, stated so it cannot be hit
# by surprise.
MAX_INSTANCES = 23
# Which instance THIS process is talking to. Set once, by the CLI, before any
# verb runs. A module-level value rather than a parameter on forty functions
# because it models something true: one process, one appliance.
_instance = DEFAULT_INSTANCE
# What compose accepts as a project name: lowercase, starting alphanumeric, then
# letters, digits, underscore, hyphen. The name also becomes a FILENAME
# (.env.<name>), so the same rule keeps `--instance ../../etc/passwd` from being
# a path and `--instance "my world"` from being two shell words.
INSTANCE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
def use_instance(name: str) -> None:
    """Choose the instance for this process, refusing a name that cannot be one.

    Validated HERE rather than at the flag, because setup.py is also entered
    directly and through the environment — three doors, one gate.
    """
    global _instance
    name = (name or DEFAULT_INSTANCE).strip()
    if not INSTANCE_NAME.match(name):
        raise SetupError(
            f"'{name}' cannot be an instance name. Use lowercase letters, digits, "
            "'-' and '_', starting with a letter or digit — it becomes a docker "
            "project name and a settings filename."
        )
    if len(name) > 40:
        raise SetupError(f"'{name}' is too long for an instance name (40 characters).")
    _instance = name
def instance() -> str:
    return _instance
def compose_project(name: str | None = None) -> str:
    """The compose project, which is the real identity of an instance."""
    return f"embabel-{name or _instance}"
def env_file(name: str | None = None) -> str:
    """Settings for an instance. The default's is plain `.env`, because the
    common case must not be made to look like a special case."""
    name = name or _instance
    return ENV_FILE if name == DEFAULT_INSTANCE else f"{ENV_FILE}.{name}"
def env_path(name: str | None = None) -> str:
    return os.path.join(APPLIANCE_DIR, env_file(name))
def port_base(name: str | None = None) -> int:
    value = env_file_value("EMBABEL_PORT_BASE", name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_PORT_BASE
def ports_for(base: int) -> dict:
    return {var: base + offset for var, offset in PORT_OFFSETS.items()}
def installed_instances() -> list[str]:
    """Every instance this machine knows about — from its settings file, and
    from docker, because an instance whose .env was deleted still has volumes
    and still answers, and hiding it would be the unhelpful kind of tidy."""
    found = set()
    for entry in os.listdir(APPLIANCE_DIR):
        if entry == ENV_FILE:
            found.add(DEFAULT_INSTANCE)
        elif entry.startswith(f"{ENV_FILE}.") and not entry.endswith((".example", ".before-restore")):
            found.add(entry[len(ENV_FILE) + 1:])
    # LATE IMPORT, and the one cycle in this package. settings tells dockerlib
    # which project to scope to, so dockerlib imports settings; but an instance
    # whose settings file was deleted still has containers, and finding those
    # needs Docker. Importing here rather than at module scope keeps the cycle
    # from existing at import time, and marks the one place it is real.
    from .dockerlib import _docker
    run = _docker("ps", "-a", "--format", "{{.Label \"com.docker.compose.project\"}}", timeout=20)
    if run and run.returncode == 0:
        for line in run.stdout.split():
            if line.startswith("embabel-"):
                found.add(line[len("embabel-"):])
    return sorted(found)
def ensure_port_block() -> int:
    """Give this instance a port block if it does not have one yet.

    Written at CREATION and never recomputed, because a base that is derived
    fresh each run is a base that moves when a sibling instance is removed —
    every bookmark, every registered MCP URL and every `.env` in a realm
    checkout would silently start pointing at a different appliance.
    """
    existing = env_file_value("EMBABEL_PORT_BASE")
    if existing:
        return int(existing)
    # The default instance is the ANCHOR and always owns the first block. Handing
    # it one off the free list let a sibling push the primary appliance off the
    # port every bookmark, MCP registration and realm checkout already names.
    base = DEFAULT_PORT_BASE if instance() == DEFAULT_INSTANCE else free_port_base()
    set_env_var("EMBABEL_PORT_BASE", str(base), (
        "# The sixteen host ports this appliance owns, allocated when it was",
        "# created. Everything else is this + a fixed offset. Do not edit it to",
        "# move one service — set that service's own PORT variable instead.",
    ))
    if instance() != DEFAULT_INSTANCE or base != DEFAULT_PORT_BASE:
        print(f"  Instance '{instance()}' uses ports {base}-{base + PORT_BLOCK - 1}.")
    return base
def free_port_base() -> int:
    """The next unused block. Bases in use are read from the instances that
    exist rather than counted, so removing the second of three instances frees
    its block instead of stranding it."""
    used = {port_base(name) for name in installed_instances() if name != instance()}
    used.add(DEFAULT_PORT_BASE)  # reserved for the default instance, always
    for n in range(MAX_INSTANCES + 1):
        candidate = DEFAULT_PORT_BASE + n * PORT_BLOCK
        if candidate not in used:
            return candidate
    raise SetupError(
        f"All {MAX_INSTANCES + 1} port blocks are in use. The next one would collide "
        f"with Ollama on 11434, which this appliance talks to."
    )
def volume_name(key: str) -> str:
    """The real Docker volume name — compose prefixes every volume with the project."""
    return f"{compose_project()}_{key}"
def volume_exists(key: str) -> bool:
    run = _docker("volume", "inspect", volume_name(key), timeout=15)
    return run is not None and run.returncode == 0
def env_file_value(key: str, name: str | None = None) -> str | None:
    """One value from an instance's settings, or None. Defaults to the current
    instance; `name` is for reading ANOTHER one, which is how the port allocator
    finds out which blocks are already spoken for. The file may already be gone
    during teardown."""
    path = env_path(name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith(f"{key}="):
                return stripped.split("=", 1)[1].strip() or None
    return None
