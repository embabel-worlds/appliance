"""The graph tables a failed `embabel import` or `embabel graph migrate` wrote into a Postgres or Oracle server, dropped
on rollback.

WHY. Both write the arriving graph through the server's own import, into a table prefix: `embabel import` into one of
its own (`embabel_graph_<stamp>`), `embabel graph migrate` into the one .env names. When the attempt fails, the
appliance goes back to the store it had, and what the attempt wrote used to stay in the server — a whole graph nobody
reads, which an operator had to find and drop by hand.

ONLY WHAT THE ATTEMPT CREATED. Before the attempt the prefix is asked for its tables (`bundle-tool graph tables`);
only when it held none is it dropped on rollback (`bundle-tool graph drop`), so a prefix that held a graph before —
the server's own, or one another appliance keeps there — is never touched. A prefix that could not be asked about is
not dropped either. When the drop is not made or fails, the message still names the prefix, as before.
"""

from __future__ import annotations
import json

from . import graphstores as stores
from .settings import env_file_value

DEFAULT_PREFIX = "embabel_graph"


def prefix_of(store: str) -> str:
    """The table prefix .env gives [store]'s graph."""
    return env_file_value(f"GRAPH_EMBABEL_{store.upper()}_PREFIX") or DEFAULT_PREFIX


def tables_under(image: str, store: str, prefix: str) -> list[str] | None:
    """The graph tables under [prefix] in [store]'s server, or None when the server could not be asked."""
    return _run(image, "tables", store, prefix)


def drop(image: str, store: str, prefix: str) -> list[str] | None:
    """Drops the graph under [prefix] in [store]'s server; what was dropped, or None when the drop did not run."""
    return _run(image, "drop", store, prefix)


def _run(image: str | None, command: str, store: str, prefix: str) -> list[str] | None:
    # bundle imports graphmigrate, which uses this module: imported here, so the three load in any order
    from .bundle import BundleError, _bundle_tool
    if not image or store not in stores.SERVER_SETTINGS:
        return None
    url_var, user_var, password_var = stores.SERVER_SETTINGS[store]
    args = ["graph", command, "--store", store, "--url", env_file_value(url_var) or "",
            "--user", env_file_value(user_var) or stores.DEFAULT_USER, "--prefix", prefix]
    try:
        run = _bundle_tool(image, *args, env={"EMBABEL_SOURCE_PASSWORD": stores.secret(password_var) or ""})
    except BundleError:
        # an image whose bundle-tool predates `tables` and `drop`: nothing is dropped, and the message says where it is
        return None
    if run.returncode != 0:
        return None
    try:
        return list(json.loads(run.stdout.decode(errors="replace").strip().splitlines()[-1])["tables"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def after_rollback(image: str | None, store: str, prefix: str, before: list[str] | None) -> str:
    """The rollback's line about the attempt's tables. [before] is what [prefix] held before the attempt: nothing, and
    the graph the attempt wrote there is dropped; a graph, and it is left alone (it was there first); unknown, and it
    is left where the message says."""
    if before:
        return ""
    if before is not None and drop(image, store, prefix) is not None:
        return f"\n  The tables the attempt wrote under the prefix {prefix} in the {store} server were dropped."
    return (f"\n  The tables the attempt wrote are under the prefix {prefix} in the {store} server; nothing reads them, "
            "and they can be dropped.")
