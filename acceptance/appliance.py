"""What the acceptance tests talk to: an appliance that is ALREADY INSTALLED.

THIS SUITE DOES NOT OWN THE LIFECYCLE, and that is the design rather than an
omission. Bringing the stack up differs per lane — a hosted runner scales the
heavy services away, a nightly box runs them all, a cold VM installs over the
network — and a suite that starts its own containers can only ever test the one
lane it knows how to start. Given an address and an account it tests all three.

    APPLIANCE_BASE      the API                      http://localhost:11043
    CONSOLE_BASE        the console                  derived: API port + 1
    APPLIANCE_USER      the operator account         required
    APPLIANCE_PASSWORD  its password                 required
    APPLIANCE_SQL_PORT  the SQL door's host port     optional
    COMPOSE_PROJECT     for the docker-level checks  optional

Standard library only, like the installer beside it: a test that needs its own
toolchain is a test somebody has to install before they can run it.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request


def base_url() -> str:
    return (os.environ.get("APPLIANCE_BASE") or "http://localhost:11043").rstrip("/")


def console_url() -> str:
    """The console, which is the API's port plus one unless told otherwise.

    Derived rather than required because the port block is contiguous by design
    (`EMBABEL_PORT_BASE` + offset), so one address is enough to find the other —
    and a lane that moves the block only has to pass one variable.
    """
    explicit = os.environ.get("CONSOLE_BASE")
    if explicit:
        return explicit.rstrip("/")
    api = base_url()
    host, _, port = api.rpartition(":")
    return f"{host}:{int(port) + 1}" if port.isdigit() else api


def account() -> tuple[str, str]:
    user = os.environ.get("APPLIANCE_USER") or ""
    secret = os.environ.get("APPLIANCE_PASSWORD") or ""
    if not user or not secret:
        raise RuntimeError(
            "APPLIANCE_USER and APPLIANCE_PASSWORD name the account these tests sign in "
            "with — the same one the headless install created."
        )
    return user, secret


def request(path: str, user: str | None = None, password: str | None = None,
            token: str | None = None, timeout: int = 30) -> tuple[int, bytes]:
    """GET a path and return (status, body), with a FAILURE STATUS AS A RESULT.

    urllib raises on 4xx/5xx, and every interesting assertion here is about a 401
    or a 410 — so a test written against the exception would read backwards and
    would conflate "refused me" with "could not be reached". Unreachable still
    raises; a verdict comes back as a number.
    """
    url = path if path.startswith("http") else f"{base_url()}{path}"
    req = urllib.request.Request(url)
    if user is not None:
        credential = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
        req.add_header("Authorization", f"Basic {credential}")
    if token is not None:
        req.add_header("X-Embabel-Setup-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def json_body(body: bytes):
    try:
        return json.loads(body or b"{}")
    except ValueError:
        return None


def wait_for_api(seconds: int = 120) -> None:
    """Block until the API answers at all, or give up loudly.

    A lane that starts the appliance and runs the suite in the next step is racing
    a JVM. Without this the first test failed with a connection error and the
    report blamed whatever assertion happened to run first.
    """
    deadline = time.monotonic() + seconds
    last = ""
    while time.monotonic() < deadline:
        try:
            request("/api/v1/setup", token="probe", timeout=5)
            return
        except Exception as e:  # noqa: BLE001 — any transport failure means "not yet"
            last = f"{e.__class__.__name__}: {e}"
            time.sleep(2)
    raise AssertionError(f"The appliance never answered on {base_url()} ({last}).")


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> bool:
    """Is something accepting connections there?

    Deliberately only a TCP connect. Speaking the Postgres wire protocol to prove
    the SQL door works belongs in a test that has a client; "the door is open on
    the port the surfaces block advertises" is the claim this one makes, and it is
    the claim that broke when the door moved into the server.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def published_ports(project: str) -> dict[str, str]:
    """Container name -> docker's port string, for the compose project.

    Shelled out rather than read from the API because the question is about the
    HOST's bindings, which the appliance cannot see and should not be trusted to
    report. Returns {} when docker is not here, so the test that uses it can skip
    rather than fail on a machine that is not running the stack.
    """
    try:
        done = subprocess.run(
            ["docker", "ps", "--filter", f"label=com.docker.compose.project={project}",
             "--format", "{{.Names}}\t{{.Ports}}"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    if done.returncode != 0:
        return {}
    out = {}
    for line in done.stdout.splitlines():
        name, _, ports = line.partition("\t")
        if name:
            out[name] = ports
    return out
