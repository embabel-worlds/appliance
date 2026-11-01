"""Which store the Embabel engine keeps its graph in, as .env and secrets.env say — and the bundled
database servers the appliance runs for the two stores that are a server.

THE STORES. `sqlite` (the default: one file on the data volume), `snapshot` (the engine's older
journal files), `postgres` and `oracle`. A server store is a URL and a user in .env
(GRAPH_EMBABEL_POSTGRES_URL, …) and a password in secrets.env, never in .env: secrets.env is the
file the compose files load whole into the app, and it holds nothing but secrets.

BUNDLED OR NOT. A URL naming `graph-postgres` or `graph-oracle` is a server this appliance runs
itself, composed in by graph-store-<kind>-<mode>.yml with a volume of its own; any other URL is a
server somebody else runs, and the appliance composes nothing for it. The bundled server's password
is generated here the first time it is needed, and the server takes it from secrets.env too.

ORACLE'S LAYOUT. On Oracle 23ai — what the bundled server is — the engine keeps the graph in its
property-graph layout (JSON under a SQL property graph); GRAPH_EMBABEL_ORACLE_LAYOUT=rows keeps the
engine's own rows instead, for a server before 23ai. `auto`, the default, asks the server.
"""

from __future__ import annotations
import getpass
import os
import secrets
import time

from .core import APPLIANCE_DIR, SetupError
from .dockerlib import BUNDLED_STORES, _compose, _docker, graph_store_bundled, service_container
from .settings import env_file_value, set_env_var, volume_exists, volume_name

SECRETS_FILE = "secrets.env"
# Each server store's settings: (url variable, username variable, password variable in secrets.env).
SERVER_SETTINGS = {
    "postgres": ("GRAPH_EMBABEL_POSTGRES_URL", "GRAPH_EMBABEL_POSTGRES_USERNAME", "GRAPH_EMBABEL_POSTGRES_PASSWORD"),
    "oracle": ("GRAPH_EMBABEL_ORACLE_URL", "GRAPH_EMBABEL_ORACLE_USERNAME", "GRAPH_EMBABEL_ORACLE_PASSWORD"),
}
STORE_VOLUME = {"postgres": "embabel_graph_postgres_data", "oracle": "embabel_graph_oracle_data"}
DEFAULT_USER = "embabel"
# What a person types after --to, and the store it means.
TARGETS = {"embabel": "sqlite", "sqlite": "sqlite", "postgres": "postgres", "oracle": "oracle"}
HEALTH_TIMEOUT_SECONDS = 900


def current_store() -> str:
    """The store .env says the engine keeps its graph in."""
    return (env_file_value("VC_EMBABEL_STORE") or "sqlite").strip().lower()


def bundled_service(store: str) -> str | None:
    entry = BUNDLED_STORES.get(store)
    return entry[0] if entry else None


def secrets_path() -> str:
    return os.path.join(APPLIANCE_DIR, SECRETS_FILE)


def secret(key: str) -> str | None:
    if not os.path.exists(secrets_path()):
        return None
    with open(secrets_path()) as f:
        for line in f.read().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
    return None


def set_secret(key: str, value: str) -> None:
    """One line of secrets.env, replaced where it is or added: verbatim, as the file's rules say,
    and readable by its owner alone."""
    lines = []
    if os.path.exists(secrets_path()):
        with open(secrets_path()) as f:
            lines = f.read().splitlines()
    found = [i for i, line in enumerate(lines) if line.startswith(f"{key}=")]
    if found:
        lines[found[-1]] = f"{key}={value}"
    else:
        lines += [f"{key}={value}"]
    with open(secrets_path(), "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(secrets_path(), 0o600)


def configure(store: str, url: str | None, username: str | None) -> bool:
    """Write .env (and, for a server, secrets.env) so the engine opens [store]. Returns whether the
    server is a bundled one this run must start. The journal and SQLite paths are written whatever
    the store, because the reports and SQLite's file live beside them."""
    from .pins import EMBABEL_JOURNAL, EMBABEL_SQLITE
    set_env_var("VC_EMBABEL_STORE", store)
    set_env_var("VC_EMBABEL_JOURNAL", EMBABEL_JOURNAL)
    set_env_var("GRAPH_EMBABEL_SQLITE_PATH", EMBABEL_SQLITE)
    if store not in SERVER_SETTINGS:
        return False
    url_var, user_var, password_var = SERVER_SETTINGS[store]
    set_env_var(url_var, url or BUNDLED_STORES[store][2])
    set_env_var(user_var, username or env_file_value(user_var) or DEFAULT_USER)
    bundled = graph_store_bundled(store)
    if not secret(password_var):
        set_secret(password_var, secrets.token_urlsafe(24) if bundled else _ask_password(store))
    return bundled


def _ask_password(store: str) -> str:
    given = os.environ.get("EMBABEL_GRAPH_STORE_PASSWORD") or getpass.getpass(f"  Password for the {store} user: ")
    if not given:
        raise SetupError(f"A {store} server needs a password: set EMBABEL_GRAPH_STORE_PASSWORD, or type it when asked.")
    return given


def start_bundled(mode: str, store: str) -> None:
    """The bundled server up and answering, before the app that needs it starts."""
    service = bundled_service(store)
    print(f"  Starting the bundled {store} server.")
    if _compose(mode, "up", "-d", service).returncode != 0:
        raise SetupError(f"The bundled {store} server would not start — see the output above.")
    started = time.monotonic()
    while time.monotonic() - started < HEALTH_TIMEOUT_SECONDS:
        container = service_container(service)
        run = _docker("inspect", "-f", "{{.State.Health.Status}}", container, timeout=15) if container else None
        if run and run.returncode == 0 and run.stdout.strip() == "healthy":
            return
        time.sleep(5)
    raise SetupError(f"The bundled {store} server did not become healthy in {HEALTH_TIMEOUT_SECONDS // 60} minutes.")


def ensure_running(store: str) -> None:
    """A bundled source server running for the copy, whatever the composition now says."""
    container = service_container(bundled_service(store) or "", stopped=True)
    if container:
        _docker("start", container, timeout=180)


def stop_bundled(store: str) -> None:
    """Stopped cleanly, its volume kept: the graph in it is the way back."""
    container = service_container(bundled_service(store) or "", stopped=True)
    if container:
        _docker("stop", "--time", "120", container, timeout=240)


def volume_held(store: str) -> bool:
    return store in STORE_VOLUME and volume_exists(STORE_VOLUME[store])


def remove_bundled(store: str) -> None:
    """A bundled server this run created, and its volume, gone again: a failed copy into it leaves
    nothing behind to be mistaken for a graph."""
    container = service_container(bundled_service(store) or "", stopped=True)
    if container:
        _docker("rm", "-f", "-v", container, timeout=180)
    if store in STORE_VOLUME:
        _docker("volume", "rm", volume_name(STORE_VOLUME[store]), timeout=120)
