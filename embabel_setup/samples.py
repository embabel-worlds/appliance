"""Sample data from the command line: load it, list it, take it away, hand it on.

Fictional records, marked `:_Sample` by the appliance so they can be removed in one
move without touching anything real. Three uses, one mechanism — showing a realm off
before anybody has connected an account, driving a demo through scenarios, and sending
a support engineer the records behind a wrong answer.

Everything here is a thin client over `/api/v1/samples`. The rules live in the
appliance: what a set may contain, what gets marked, what redaction means. A CLI that
reimplemented any of that would be a second opinion nobody asked for.
"""

from __future__ import annotations
import base64
import getpass
import json
import os
import urllib.error
import urllib.request

from .colour import MIDDOT, TICK, bold, dim, warn
from .core import SetupError, prompt
from .dockerlib import _docker
from .settings import api_address

# Where a bare name resolves. The same rule realms use: a short name in a mailed
# instruction or a tweet must not be squattable, so it only ever means the org.
SAMPLE_ORG = "embabel-worlds"

# Read from the appliance rather than asked for, so `sample add` needs the password
# and nothing else. The file is the appliance's own record of who set it up.
SETUP_STATE = "/data/embabel/assistant/admin/.setup.json"


def sample_credential(container: str) -> str:
    """An Authorization header for the samples API.

    Basic, because bearer tokens authenticate the MCP chain and not this one — measured
    against a live appliance: Bearer 401, Basic 200.

    NOTHING IS STORED. The password is read for the length of one command and lives in
    a local variable; an appliance that kept it would be keeping a credential to its own
    front door, in a file, for the convenience of not typing. EMBABEL_PASSWORD exists for
    scripts, where the alternative is a script that cannot run unattended at all.
    """
    username = os.environ.get("EMBABEL_USER") or admin_username(container)
    if not username:
        raise SetupError("Could not tell who owns this appliance — set EMBABEL_USER.")
    password = os.environ.get("EMBABEL_PASSWORD")
    if not password:
        password = getpass.getpass(f"  Password for {username}: ")
    if not password:
        raise SetupError("No password given.")
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def admin_username(container: str) -> str | None:
    """Who set this appliance up, from its own state file."""
    run = _docker("exec", container, "sh", "-c", f"cat {SETUP_STATE} 2>/dev/null")
    if not run or run.returncode != 0:
        return None
    try:
        return json.loads(run.stdout or "{}").get("adminUsername")
    except ValueError:
        return None


def samples_api(base: str, auth: str, path: str = "", payload=None, method: str | None = None):
    """One call to /api/v1/samples, with the operator's own words on failure."""
    request = urllib.request.Request(
        f"{base}/api/v1/samples{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method or ("POST" if payload is not None else "GET"),
    )
    request.add_header("Authorization", auth)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 401:
            raise SetupError("The appliance did not accept that password.")
        # The appliance refuses a bad set with its reasons listed; passing them through
        # beats "400 Bad Request", which tells an author nothing about their file.
        try:
            problems = json.loads(body).get("problems")
        except ValueError:
            problems = None
        if problems:
            raise SetupError("This set was refused:\n    " + "\n    ".join(problems))
        raise SetupError(f"The appliance answered {e.code}: {body[:200]}")
    except urllib.error.URLError as e:
        raise SetupError(
            f"Could not reach the appliance ({e.reason}).\n"
            f"Its API did not answer on {api_address(base)}."
        )


def read_set(source: str) -> dict:
    """A sample set, from a file on disk or a name that resolves to one.

    JSON is the format because this client is stdlib-only and runs on whatever python3
    the operator has — a YAML dependency would be a package to install before the first
    command works. A `.yml` set is read when PyYAML happens to be present and says so
    plainly when it is not, rather than failing on a parse error nobody can act on.
    """
    if os.path.exists(source):
        return _parse_set(source, open(source, encoding="utf-8").read())

    url = _resolve(source)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return _parse_set(url, response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SetupError(f"No sample set '{source}' — looked at {url}")
        raise SetupError(f"Could not fetch {url}: {e.code}")
    except urllib.error.URLError as e:
        raise SetupError(f"Could not fetch {url} ({e.reason})")


def _resolve(source: str) -> str:
    """A name, `owner/repo`, or a URL, to somewhere a set can be read from."""
    if source.startswith(("http://", "https://")):
        return source
    if source.startswith("gh:"):
        source = source[3:]
    if "/" in source:
        owner, _, repo = source.partition("/")
    else:
        # A bare name is the org's, and only the org's.
        owner, repo = SAMPLE_ORG, f"sample-{source}"
    return f"https://raw.githubusercontent.com/{owner}/{repo}/main/sample.json"


def _parse_set(where: str, text: str) -> dict:
    if where.endswith((".yml", ".yaml")):
        try:
            import yaml
        except ImportError:
            raise SetupError(
                f"{where} is YAML and PyYAML is not installed.\n"
                "  Use a .json set, or: pip install pyyaml"
            )
        return yaml.safe_load(text)
    try:
        return json.loads(text)
    except ValueError as e:
        raise SetupError(f"{where} is not valid JSON: {e}")


def describe_sets(status: dict) -> None:
    """What is loaded, and whether any of it is sitting next to real records."""
    sets = status.get("sets") or []
    if not sets:
        print(f"  {MIDDOT} " + dim("No sample data loaded."))
    else:
        width = max(len(s.get("name") or "") for s in sets)
        for entry in sets:
            print(f"  {TICK} {bold((entry.get('name') or '').ljust(width))}  "
                  + dim(f"{entry.get('nodes', 0)} node(s)"))

    mixed = status.get("mixedLabels") or []
    if mixed:
        # The one state worth interrupting somebody about: a view over this label
        # returns fiction and fact in the same list.
        print()
        print("  " + warn("Sample and real records share a label:"))
        for row in mixed:
            print(f"    {row.get('label')}  "
                  + dim(f"{row.get('sample', 0)} sample, {row.get('real', 0)} real"))
        print("  " + dim("A screenshot of that list would not show which is which."))
