#!/usr/bin/env python3
"""One command that installs a fresh appliance and climbs the release ladder on it.

    python3 scripts/release-check.py                         # stable, five realms, torn down after
    python3 scripts/release-check.py --release 0.4.0         # one build, and prove it is the one running
    python3 scripts/release-check.py --release canary        # every merge to main, as users on canary get it
    python3 scripts/release-check.py --secrets ~/realm-keys.env --battery ~/realms/realm-account-health
    python3 scripts/release-check.py --keep                  # leave the instance up to look at afterwards
    python3 scripts/release-check.py --only-uninstall        # just L8 and L8.1: the ways out

See docs/RELEASE_CHECK.md for what each rung proves and for the short list of things this
cannot judge. That list is the point of the whole script: everything a machine CAN check about a
build is checked here, so a person's time on a release goes only to what needs a person.

WHAT IT DRIVES IS WHAT A USER DRIVES. The install is `curl | sh`'s own install.sh, run under a
PTY by scripts/drive-install.py — reused, not reimplemented, so this inherits every regression
that harness already asserts on. Onboarding is the console's own setup API with the token the
container holds. Realms come from git through the MCP `install_realm` tool, the way an agent adds
one. Nothing reaches into the server by a side door it does not offer a user.

IT ISOLATES ITSELF. The appliance goes into a temporary EMBABEL_HOME as its own instance, with
its own port block, beside whatever else this machine runs. The installer also writes outside its
directory — an `embabel` launcher on PATH and a PATH line in the shell profile — and an uninstall
takes those back out, which on a developer's machine would mean taking out THEIR launcher. So
every installer and CLI process here runs with HOME pointed into the temporary directory and
EMBABEL_BIN_DIR beside it (DOCKER_CONFIG keeps docker finding its own contexts and plugins). A
release check that edited the machine it ran on would be one nobody could run casually, which is
the only way it gets run on every build.

Python 3 standard library only, like drive-install.py: it has to run on a bare checkout.
Exit code is 0 when nothing FAILED. A KNOWN issue (see KNOWN_ISSUES) is shown, never hidden, and
does not fail the run unless --strict.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_REPO = "ghcr.io/embabel/assistant"

# The appliance's own reading of a release — what is valid, and which tag it pulls — rather than a
# second copy of the table here. Standard library all the same: the package is this checkout's.
sys.path.insert(0, HERE)
from embabel_setup.release import parse_release, release_tag  # noqa: E402

DEFAULT_REALMS = (
    "embabel-worlds/realm-business-vocabulary",
    "embabel-worlds/realm-chatwoot",
    "embabel-worlds/realm-lago",
    "embabel-worlds/realm-odoo",
    "embabel-worlds/realm-account-health",
)

# Failures this build is KNOWN to have, by view name, with the error code they fail with. Shown as
# KNOWN rather than FAIL so a release with an accepted defect still reads green on everything else
# — and shown at all, so nobody mistakes "accepted" for "fixed". When a known issue stops
# reproducing the run says so: delete its line then, or it will quietly excuse the next regression
# that happens to fail the same way.
KNOWN_ISSUES = {
    "OdooMeetingBriefing": ("VIEW_QUERY_INVALID", "known issue in 0.4.0: the engine refuses this view's query"),
    "OdooDealTriage": ("VIEW_QUERY_INVALID", "known issue in 0.4.0: the engine refuses this view's query"),
}

# The planner rung's fixture. ISOLATED HERE because every expectation in it depends on the
# account-health demo data — northwind-traders.example is that seed book's "silent escalation"
# account, with an open case about the failing export and nothing about SSO. Another data set
# means another fixture: pass one with --plan-fixture (a JSON file of this same shape).
PLAN_FIXTURE = {
    "email": (
        "From: Priya Raman <priya.raman@northwind-traders.example>\n"
        "Subject: Month end is blocked again\n\n"
        "Hi - the invoice export is still failing and it is blocking our month-end close, third month\n"
        "running. Separately, Tom in IT says SSO keeps looping on Safari. Can someone call me today?\n"
        "Priya Raman, Head of Finance"
    ),
    "goal": (
        "MATCH (who:Attendee)-[:RAISED]->(i:Issue) "
        "OPTIONAL MATCH (i)-[:TRACKED_BY]->(c:SupportCase) "
        "RETURN who.name AS who, i.summary AS issue, collect(c.subject) AS cases"
    ),
    "question": "From this email, which problems did the sender raise, and which open support cases already track each one?",
    # What the rows must show. Issue wording is the model's, so it is matched loosely; the case
    # subject is the demo data's own and is matched as a substring.
    "minIssues": 2,
    "trackedCaseContains": "export",
    "minUntracked": 1,
    "unreachableGoal": "MATCH (a:CustomerAccount) RETURN a.npsScore",
}

WRITE_GOAL = "MATCH (n:CustomerAccount) DETACH DELETE n"
EMPTY_WORLD_QUESTION = "Which customers owe us money?"

# Model calls and first-touch realm fetches are slow, and a client timeout below the server's
# would invent failures the appliance did not have.
HTTP_TIMEOUT = 300
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# ── reporting ───────────────────────────────────────────────────────────────

class Report:
    """Every check, in the order it ran, printed as it lands — an install takes minutes and a
    view sweep more, and a run that is silent until the end looks exactly like a hung one."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.facts: dict = {}
        self.current = ""

    def rung(self, title: str) -> None:
        self.current = title
        print(f"\n== {title}", flush=True)

    def add(self, check: str, status: str, reason: str = "", **detail) -> str:
        self.rows.append({"rung": self.current, "check": check, "status": status,
                          "reason": reason, **({"detail": detail} if detail else {})})
        print(f"   {status:<5} {check}" + (f" — {reason}" if reason else ""), flush=True)
        return status

    def progress(self, message: str) -> None:
        print(f"         … {message}", flush=True)

    def counts(self) -> dict:
        out: dict = {}
        for row in self.rows:
            out[row["status"]] = out.get(row["status"], 0) + 1
        return out

    def table(self) -> str:
        lines, last = [], None
        width = max([len(r["check"]) for r in self.rows] + [10])
        for row in self.rows:
            if row["rung"] != last:
                lines.append(f"\n{row['rung']}")
                last = row["rung"]
            reason = row["reason"].replace("\n", " ")
            if len(reason) > 150:
                reason = reason[:147] + "..."
            lines.append(f"  {row['status']:<5} {row['check']:<{width}}  {reason}")
        return "\n".join(lines)


# ── HTTP and MCP ────────────────────────────────────────────────────────────

def http(method: str, url: str, body=None, headers: dict | None = None,
         timeout: float = HTTP_TIMEOUT) -> tuple[int, str]:
    """One request; a transport failure is status 0 with the error as the body, never a raise —
    an unreachable surface is a finding to report, not a reason to stop reporting."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/json", **({"Content-Type": "application/json"} if data else {}),
        **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def as_json(text: str):
    try:
        return json.loads(text, strict=False)
    except (ValueError, TypeError):
        return None


class Mcp:
    """A streamable-HTTP MCP client, as small as the protocol allows.

    A response is EITHER a JSON body or an SSE stream, at the server's choice per call — the same
    door answered `tools/list` as JSON and the next `tools/call` as SSE. So both are read, and in a
    stream the message carrying our request id is preferred over notifications sent before it.
    """

    def __init__(self, url: str, auth: str):
        self.url, self.auth, self.session, self.next_id = url, auth, None, 1
        self.server_info: dict = {}

    def _post(self, payload: dict, timeout: float) -> dict | None:
        headers = {"Authorization": self.auth, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            self.session = response.headers.get("Mcp-Session-Id") or self.session
            raw = response.read().decode("utf-8", "replace")
        if "id" not in payload:
            return None
        if raw.lstrip().startswith("{"):
            return as_json(raw)
        messages = [as_json(line[5:].strip()) for line in raw.splitlines() if line.startswith("data:")]
        messages = [m for m in messages if isinstance(m, dict)]
        with_id = [m for m in messages if m.get("id") == payload["id"]]
        return (with_id or [m for m in messages if "id" in m] or messages or [None])[-1]

    def request(self, method: str, params: dict | None = None, timeout: float = HTTP_TIMEOUT) -> dict:
        self.next_id += 1
        message = self._post({"jsonrpc": "2.0", "id": self.next_id, "method": method,
                              **({"params": params} if params is not None else {})}, timeout)
        if not message:
            raise RuntimeError(f"{method}: no JSON-RPC answer")
        if "error" in message:
            raise RuntimeError(f"{method}: {message['error']}")
        return message.get("result") or {}

    def initialize(self) -> dict:
        result = self.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                             "clientInfo": {"name": "release-check", "version": "1"}},
                              timeout=60)
        self.server_info = result.get("serverInfo") or {}
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, 60)
        return result

    def tools(self) -> list[str]:
        return sorted(t["name"] for t in self.request("tools/list", {}, 60).get("tools", []))

    def call(self, name: str, arguments: dict, timeout: float = HTTP_TIMEOUT):
        """The tool's answer, parsed when it is JSON. An isError result is returned as
        {"isError": True, "text": ...} so a caller can report it rather than crash on it."""
        result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            return {"isError": True, "text": text}
        parsed = as_json(text)
        return parsed if parsed is not None else {"text": text}


# ── the instance ────────────────────────────────────────────────────────────

class Instance:
    def __init__(self, args, scratch: str):
        self.args = args
        self.name = args.instance
        self.scratch = scratch
        self.home = args.home or os.path.join(scratch, "worlds")
        self.container = f"embabel-{self.name}-worlds-1"
        self.auth_header = "Basic " + base64.b64encode(
            f"{args.username}:{args.password}".encode()).decode()
        fake_home = os.path.join(scratch, "home")
        os.makedirs(fake_home, exist_ok=True)
        # See the module docstring: the installer's writes outside its directory land here.
        self.env = {**os.environ, "EMBABEL_INSTANCE": self.name, "EMBABEL_HOME": self.home,
                    "EMBABEL_MODE": "worlds", "EMBABEL_BIN_DIR": os.path.join(scratch, "bin"),
                    "HOME": fake_home, "SHELL": "/bin/sh", "EMBABEL_NO_BROWSER": "1",
                    "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG")
                    or os.path.join(os.path.expanduser("~"), ".docker")}
        if args.ref:
            self.env["EMBABEL_REF"] = args.ref
        # The release goes in the way a user's does — EMBABEL_RELEASE to the installer, which
        # writes it into the instance's .env — so the run proves that path, not a side door.
        # A legacy EMBABEL_VERSION exported in this shell would outrank it for compose and check
        # some other build than the one asked for, so it does not reach the installer.
        self.env["EMBABEL_RELEASE"] = args.release
        self.env.pop("EMBABEL_VERSION", None)

    @property
    def env_file(self) -> str:
        return os.path.join(self.home, ".env" if self.name == "appliance" else f".env.{self.name}")

    def env_value(self, key: str) -> str | None:
        try:
            with open(self.env_file, encoding="utf8") as f:
                for line in f:
                    m = re.match(rf"^\s*{re.escape(key)}\s*=\s*(.*?)\s*$", line)
                    if m:
                        return m.group(1)
        except OSError:
            return None
        return None

    @property
    def port_base(self) -> int:
        return int(self.env_value("EMBABEL_PORT_BASE") or 0)

    @property
    def api(self) -> str:
        return f"http://127.0.0.1:{self.port_base + 1}"

    def get(self, path: str, timeout: float = HTTP_TIMEOUT):
        return http("GET", self.api + path, headers={"Authorization": self.auth_header}, timeout=timeout)

    def post(self, path: str, body, timeout: float = HTTP_TIMEOUT):
        return http("POST", self.api + path, body, {"Authorization": self.auth_header}, timeout)

    def delete(self, path: str):
        return http("DELETE", self.api + path, headers={"Authorization": self.auth_header})

    def mcp(self, door: str) -> Mcp:
        client = Mcp(self.api + door, self.auth_header)
        client.initialize()
        return client

    def cli(self, *argv: str, stdin: str | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
        """The installed appliance's own `embabel` command, run by path from its directory.

        stdin is a string or nothing, never this terminal: a verb that ASKS something then fails
        on EOF, which is exactly how "re-running up asks nothing" is proved.
        """
        return subprocess.run([sys.executable, os.path.join(self.home, "embabel"), "--instance", self.name, *argv],
                              cwd=self.home, env=self.env, input=stdin if stdin is not None else "",
                              capture_output=True, text=True, timeout=timeout)

    def health(self) -> str:
        run = subprocess.run(["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}"
                              "{{else}}{{.State.Status}}{{end}}", self.container],
                             capture_output=True, text=True)
        return run.stdout.strip() if run.returncode == 0 else "absent"

    def wait_healthy(self, timeout: int = 900) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.health() == "healthy":
                status, _ = self.get("/api/v1/models/in-use", timeout=10)
                if status in (200, 401):
                    return True
            time.sleep(5)
        return False


# ── the rungs ───────────────────────────────────────────────────────────────

def rung_install(inst: Instance, report: Report) -> bool:
    report.rung("L0 install — curl | sh, under a PTY")
    transcript = os.path.join(inst.scratch, "install.log")
    report.facts["transcript"] = transcript
    if inst.args.no_install:
        report.add("install", "SKIP", f"--no-install: reusing the instance in {inst.home}")
        return bool(inst.port_base)
    report.progress(f"installing instance '{inst.name}' into {inst.home} (transcript: {transcript})")
    command = [sys.executable, os.path.join(HERE, "scripts", "drive-install.py"), "--installer",
               "--mode", "worlds", "--username", inst.args.username, "--password", inst.args.password,
               "--display", inst.args.display, "--apikey", "", "--timeout", str(inst.args.install_timeout),
               "--transcript", transcript]
    run = subprocess.run(command, env=inst.env, capture_output=True, text=True)
    checks = [line.strip() for line in run.stdout.splitlines() if line.strip().startswith(("✓", "✗"))]
    if run.returncode != 0:
        report.add("drive-install.py --installer", "FAIL", "; ".join(checks) or run.stdout[-300:])
        return bool(inst.port_base)
    report.add("drive-install.py --installer", "PASS", "; ".join(checks))
    if not inst.port_base:
        report.add("instance settings", "FAIL", f"no EMBABEL_PORT_BASE in {inst.env_file}")
        return False
    # The release has to OUTLIVE the install: every later `up` and `upgrade` reads it from here.
    recorded = inst.env_value("EMBABEL_RELEASE")
    report.add("release recorded", "PASS" if recorded == inst.args.release else "FAIL",
               f"EMBABEL_RELEASE={recorded} in {os.path.basename(inst.env_file)}"
               + ("" if recorded == inst.args.release else f", asked for {inst.args.release}"))
    report.add("instance", "PASS", f"API {inst.api}, console http://127.0.0.1:{inst.port_base + 2}")
    healthy = inst.wait_healthy()
    report.add("container healthy", "PASS" if healthy else "FAIL", inst.health())
    return healthy


def remote_digest(ref: str) -> str | None:
    run = subprocess.run(["docker", "buildx", "imagetools", "inspect", ref, "--format", "{{json .Manifest}}"],
                         capture_output=True, text=True, timeout=120)
    doc = as_json(run.stdout) if run.returncode == 0 else None
    return doc.get("digest") if isinstance(doc, dict) else None


def rung_image(inst: Instance, report: Report) -> None:
    report.rung("L0.1 image — what is actually running")
    run = subprocess.run(["docker", "inspect", "--format", "{{.Config.Image}} {{.Image}}", inst.container],
                         capture_output=True, text=True)
    if run.returncode != 0:
        report.add("docker inspect", "FAIL", run.stderr.strip()[:200])
        return
    ref, image_id = run.stdout.split()
    inspected = subprocess.run(["docker", "image", "inspect", image_id, "--format", "{{json .}}"],
                               capture_output=True, text=True)
    image = as_json(inspected.stdout) or {}
    repo_digests = image.get("RepoDigests") or []
    labels = (image.get("Config") or {}).get("Labels") or {}
    local = sorted({d.split("@", 1)[1] for d in repo_digests if d.startswith(IMAGE_REPO + "@")})
    report.facts["image"] = {"ref": ref, "imageId": image_id, "repoDigests": local,
                             "version": labels.get("org.opencontainers.image.version"),
                             "revision": labels.get("org.opencontainers.image.revision"),
                             "created": labels.get("org.opencontainers.image.created")}
    report.add("running image", "PASS", f"{ref} {', '.join(local) or image_id[:19]}")
    tag = ref.rsplit(":", 1)[1] if ":" in ref.rsplit("/", 1)[-1] else "latest"
    # The release, as the table's first word on what was checked: which stream, which tag, and
    # what the build calls itself — a stream's tag alone does not say which build it was today.
    built = report.facts["image"]
    expected = release_tag(inst.args.release)
    report.add("release", "PASS" if tag == expected else "FAIL",
               f"{inst.args.release} → :{expected}"
               + (f", running :{tag}" if tag != expected else "")
               + (f", version {built['version']}" if built.get("version") else "")
               + (f", revision {built['revision'][:12]}" if built.get("revision") else "")
               + (f", built {built['created'][:16]}" if built.get("created") else ""))
    # And as the appliance itself reports it, which is what a user sees.
    shown = inst.cli("release", "--json", timeout=120)
    said = as_json(shown.stdout) if shown.returncode == 0 else None
    if isinstance(said, dict):
        running = (said.get("running") or {}).get("id")
        ok = said.get("release") == inst.args.release and running == image_id
        report.add("embabel release", "PASS" if ok else "FAIL",
                   f"follows {said.get('release')}, running {(said.get('running') or {}).get('image')}")
    else:
        report.add("embabel release", "FAIL", (shown.stderr or shown.stdout).strip()[-200:])
    want = f"{IMAGE_REPO}:{tag}"
    published = remote_digest(want)
    if not published:
        report.add(f"matches {want}", "SKIP", "could not read the registry's digest (offline, or no such tag)")
        return
    report.facts["image"]["registryDigest"] = published
    if published in local:
        report.add(f"matches {want}", "PASS", published)
    else:
        # For a floating tag this means the install started on a stale local pull — the run would
        # be checking some other build than the one users are downloading.
        report.add(f"matches {want}", "FAIL", f"registry has {published}, running {', '.join(local) or image_id}")


def rung_setup_link(inst: Instance, report: Report) -> None:
    report.rung("L0.5 setup link — the one link a new user clicks")
    try:
        with open(report.facts["transcript"], encoding="utf8") as f:
            text = ANSI.sub("", f.read())
    except OSError:
        report.add("setup link", "SKIP", "no install transcript (--no-install)")
        return
    links = re.findall(r"https?://\S+#setup=\S+", text)
    if not links:
        # An install that is "already set up" prints no link; on a fresh one it must.
        report.add("setup link printed", "FAIL", "no `…#setup=` link in the install output")
        return
    link = links[-1]
    page = link.split("#", 1)[0]
    status, _ = http("GET", page, headers={"Accept": "text/html"}, timeout=30)
    shown = re.sub(r"#setup=.*", "#setup=<token>", link)
    report.add("setup link opens", "PASS" if status == 200 else "FAIL", f"HTTP {status} for {shown}")


def rung_onboarding(inst: Instance, report: Report) -> bool:
    report.rung("L1 onboarding — the console's setup API, headless")
    run = subprocess.run(["docker", "exec", inst.container, "cat", "/data/embabel/assistant/admin/.setup-token"],
                         capture_output=True, text=True)
    token = run.stdout.strip()
    if not token:
        status, _ = inst.get("/api/v1/models/in-use", timeout=30)
        if status == 200:
            report.add("setup", "SKIP", "already set up (no setup token): the credentials in use already work")
            return True
        report.add("setup token", "FAIL", (run.stderr or "empty token file").strip()[:200])
        return False
    headers = {"X-Embabel-Setup-Token": token}

    def step(path: str, body=None):
        return http("POST" if body is not None else "GET", inst.api + "/api/v1/setup" + path, body, headers, 120)

    status, text = step("")
    if status == 410:
        # The token file outlives /complete; the API saying it is closed is the authority.
        report.add("setup", "SKIP", "already complete (the setup API answers 410)")
        status, _ = inst.get("/api/v1/models/in-use", timeout=30)
        return status == 200
    facts = as_json(text) or {}
    report.add("GET /setup", "PASS" if status == 200 else "FAIL", f"HTTP {status}"
               + (f", terms {facts.get('termsVersion')} accepted={facts.get('termsAccepted')}" if status == 200 else f": {text[:150]}"))
    status, text = step("/account", {"username": inst.args.username, "password": inst.args.password,
                                     "displayName": inst.args.display})
    report.add("POST /setup/account", "PASS" if status == 200 else "FAIL",
               f"HTTP {status}: {(as_json(text) or {}).get('detail', text)[:150]}")
    keys = [(provider, os.environ.get(env)) for provider, env in
            (("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY"))]
    keys = [(p, k) for p, k in keys if k]
    if not keys:
        report.add("POST /setup/provider", "FAIL", "neither ANTHROPIC_API_KEY nor OPENAI_API_KEY is set")
    for provider, key in keys:
        status, text = step("/provider", {"provider": provider, "apiKey": key})
        detail = (as_json(text) or {}).get("detail", text).replace(key, "<key>")
        report.add(f"POST /setup/provider {provider}", "PASS" if status == 200 else "FAIL",
                   f"HTTP {status}: {detail[:150]}")
    if facts.get("termsVersion") and not facts.get("termsAccepted"):
        status, text = step("/terms", {"version": facts["termsVersion"]})
        report.add("POST /setup/terms", "PASS" if status == 200 else "FAIL",
                   f"HTTP {status}: {(as_json(text) or {}).get('detail', text)[:150]}")
    else:
        report.add("POST /setup/terms", "SKIP", "already accepted in the terminal run" if facts.get("termsAccepted")
                   else "no terms in force")
    status, text = step("/complete", {})
    report.add("POST /setup/complete", "PASS" if status == 200 else "FAIL",
               f"HTTP {status}: {(as_json(text) or {}).get('detail', text)[:150]}")
    status, text = inst.get("/api/v1/models/in-use", timeout=60)
    models = (as_json(text) or {}).get("models") or []
    named = [f"{m.get('model')} ({m.get('provider')})" for m in models]
    real = status == 200 and models and "setup-required" not in text
    report.add("models in use", "PASS" if real else "FAIL",
               ", ".join(sorted(set(named))) if named else f"HTTP {status}: {text[:150]}")
    return bool(real)


def rung_mcp(inst: Instance, report: Report) -> None:
    report.rung("L1.5 MCP — three doors, and they differ")
    lists: dict[str, list[str]] = {}
    clients: dict[str, Mcp] = {}
    for door in ("/mcp/code", "/mcp/chat", "/mcp"):
        try:
            clients[door] = inst.mcp(door)
            lists[door] = clients[door].tools()
            report.add(f"handshake {door}", "PASS",
                       f"{clients[door].server_info.get('name', '?')}, {len(lists[door])} tools")
        except Exception as e:  # noqa: BLE001
            report.add(f"handshake {door}", "FAIL", f"{type(e).__name__}: {e}"[:200])
    code, chat, bare = lists.get("/mcp/code"), lists.get("/mcp/chat"), lists.get("/mcp")
    if chat is not None and bare is not None:
        report.add("/mcp is the chat door", "PASS" if chat == bare else "FAIL",
                   "same tool list" if chat == bare else f"differs: {sorted(set(chat) ^ set(bare))[:6]}")
    if code is not None:
        learn = [t for t in code if t.startswith("learn_")]
        report.add("code door has learn_*", "PASS" if learn else "FAIL", ", ".join(learn[:4]) or "none")
    if chat is not None:
        memory = [t for t in chat if t.startswith("memory_")]
        report.add("chat door has memory_*", "PASS" if memory else "FAIL", ", ".join(memory[:4]) or "none")
    for door in ("/mcp/code", "/mcp/chat"):
        tools = lists.get(door)
        if tools is None:
            continue
        if "plan" not in tools:
            report.add(f"plan on {door}", "FAIL", "not listed")
            continue
        # CALLED, not just listed: the list and the dispatch have been wrong independently.
        try:
            answer = clients[door].call("plan", {"goal": WRITE_GOAL})
            outcome = answer.get("outcome") if isinstance(answer, dict) else None
            report.add(f"plan refuses a write on {door}", "PASS" if outcome == "refused" else "FAIL",
                       f"outcome={outcome}" + (f": {answer.get('text', '')[:120]}" if outcome is None else ""))
        except Exception as e:  # noqa: BLE001
            report.add(f"plan refuses a write on {door}", "FAIL", str(e)[:200])


def rung_empty_world(inst: Instance, report: Report) -> None:
    report.rung("L2 the empty world is honest")
    status, text = inst.post("/api/v1/admin/kg/ask", {"question": EMPTY_WORLD_QUESTION})
    answer = as_json(text) or {}
    rows = answer.get("rows") or []
    reason = answer.get("reason")
    if status != 200:
        report.add("kg/ask on an empty world", "FAIL", f"HTTP {status}: {text[:150]}")
    elif rows:
        report.add("kg/ask on an empty world", "FAIL", f"{len(rows)} invented row(s): {json.dumps(rows[0])[:120]}")
    else:
        report.add("kg/ask on an empty world", "PASS", f"0 rows, reason {reason}")


def realm_name(answer, repo: str) -> str:
    if isinstance(answer, dict):
        for key in ("realm", "name", "installed"):
            value = answer.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict) and value.get("name"):
                return value["name"]
    base = repo.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    return base[len("realm-"):] if base.startswith("realm-") else base


def realm_problems(status: dict) -> tuple[list[str], list[str]]:
    """(blocking, notes) from a realm_status answer. A NOTE is the source answering null for a
    property it was asked for — a fact about the data, not a fault in the realm."""
    blocking, notes = [], []
    for problem in status.get("problems") or []:
        text = problem if isinstance(problem, str) else str(problem.get("message") or json.dumps(problem))
        severity = (problem.get("severity") or problem.get("level") or "") if isinstance(problem, dict) else ""
        (notes if str(severity).upper() in ("NOTE", "INFO") or text.startswith("NOTE") else blocking).append(text)
    return blocking, notes


def check_realms_active(inst: Instance, report: Report, names: list[str], label: str) -> None:
    try:
        client = inst.mcp("/mcp/code")
    except Exception as e:  # noqa: BLE001
        report.add(f"realm_status {label}", "FAIL", f"MCP: {e}"[:200])
        return
    for name in names:
        try:
            status = client.call("realm_status", {"realm": name}, timeout=120)
        except Exception as e:  # noqa: BLE001
            report.add(f"{name} {label}", "FAIL", str(e)[:200])
            continue
        state = (status.get("status") or status.get("state") or "?") if isinstance(status, dict) else "?"
        blocking, notes = realm_problems(status if isinstance(status, dict) else {})
        if state == "active" and not blocking:
            report.add(f"{name} {label}", "PASS", "active" + (f"; note: {notes[0][:100]}" if notes else ""))
        elif state == "degraded" and not blocking:
            report.add(f"{name} {label}", "PASS", f"degraded with notes only: {notes[0][:110] if notes else '-'}")
        else:
            report.add(f"{name} {label}", "FAIL", f"{state}: {(blocking or notes or [json.dumps(status)[:150]])[0][:150]}")


def rung_realms(inst: Instance, report: Report) -> list[str]:
    report.rung("L3 realms from git — install, status, and every view they ship")
    if inst.args.secrets:
        target = os.path.join(inst.home, "secrets.env")
        shutil.copyfile(os.path.expanduser(inst.args.secrets), target)
        os.chmod(target, 0o600)
        report.progress("secrets copied; `embabel up` so the container takes them")
        run = inst.cli("up")
        healthy = inst.wait_healthy()
        report.add("secrets → embabel up", "PASS" if run.returncode == 0 and healthy else "FAIL",
                   f"exit {run.returncode}, {inst.health()}" + ("" if run.returncode == 0 else f": {run.stdout[-200:]}"))
    else:
        report.add("realm keys", "SKIP", "no --secrets: realms that need a key will report their sources unavailable")
    names: list[str] = []
    try:
        client = inst.mcp("/mcp/code")
    except Exception as e:  # noqa: BLE001
        report.add("MCP code door", "FAIL", str(e)[:200])
        return names
    for repo in inst.args.realms:
        report.progress(f"install_realm {repo}")
        try:
            answer = client.call("install_realm", {"repo": repo}, timeout=600)
        except Exception as e:  # noqa: BLE001
            report.add(f"install_realm {repo}", "FAIL", str(e)[:200])
            continue
        text = json.dumps(answer)
        failed = isinstance(answer, dict) and (answer.get("isError") or answer.get("status") in ("error", "failed")
                                               or answer.get("ok") is False)
        name = realm_name(answer, repo)
        if failed:
            report.add(f"install_realm {repo}", "FAIL", text[:200])
            continue
        names.append(name)
        report.add(f"install_realm {repo}", "PASS", f"realm '{name}'")
    check_realms_active(inst, report, names, "active")
    report.facts["realms"] = names
    sweep_views(inst, report, names)
    return names


def sweep_views(inst: Instance, report: Report, realms: list[str]) -> None:
    """Every view the installed realms ship, on its defaults. A view that loads is not a view
    that answers — realm_status checks the first, only running it checks the second."""
    status, text = inst.get("/api/v1/admin/kg/views", timeout=120)
    views = as_json(text) if status == 200 else None
    if not isinstance(views, list):
        report.add("list views", "FAIL", f"HTTP {status}: {text[:150]}")
        return
    shipped = [v for v in views if v.get("source") in realms]
    report.progress(f"{len(shipped)} views across {', '.join(realms) or 'no realms'}")
    tally = {"SUCCEEDED": 0}
    for view in sorted(shipped, key=lambda v: (v.get("source") or "", v.get("name"))):
        name = view["name"]
        check = f"view {view['source']}/{name}"
        needs = [p for p, spec in (view.get("params") or {}).items()
                 if isinstance(spec, dict) and spec.get("default") is None]
        if needs:
            report.add(check, "SKIP", f"skipped (needs params: {', '.join(needs)})")
            continue
        started = time.time()
        code, body = inst.post(f"/api/v1/views/{name}/invoke", {"args": {}}, timeout=600)
        took = time.time() - started
        answer = as_json(body) or {}
        state = answer.get("status")
        error = answer.get("error") or {}
        known = KNOWN_ISSUES.get(name)
        if not (code == 200 and state == "SUCCEEDED") and not (known and known[0] == error.get("code")):
            # ONCE MORE, to tell a broken view from one that fails only on first touch — a view
            # reading records another realm's fetch has not built yet. Both are failures a user
            # meets; they are different bugs, and the report should say which.
            again_code, again_body = inst.post(f"/api/v1/views/{name}/invoke", {"args": {}}, timeout=600)
            again = as_json(again_body) or {}
            if again_code == 200 and again.get("status") == "SUCCEEDED":
                why = "/".join(x for x in (error.get("code"), error.get("reason")) if x) or f"HTTP {code}"
                report.add(check, "FAIL", f"first call FAILED {why}, the second SUCCEEDED "
                           f"({len(again.get('data') or [])} rows): fails on first touch")
                continue
        if code == 200 and state == "SUCCEEDED":
            tally["SUCCEEDED"] += 1
            rows = len(answer.get("data") or [])
            if known:
                report.add(check, "PASS", f"{rows} rows in {took:.1f}s — the KNOWN issue no longer reproduces: "
                           "remove it from KNOWN_ISSUES")
            else:
                report.add(check, "PASS", f"SUCCEEDED, {rows} rows in {took:.1f}s")
            continue
        code_name = error.get("code") or f"HTTP {code}"
        shown = f"{code_name}/{error['reason']}" if error.get("reason") else code_name
        message = (error.get("message") or body)[:140]
        if "SOURCE_UNAVAILABLE" in json.dumps(answer):
            report.add(check, "SKIP", f"SOURCE_UNAVAILABLE: {message}")
        elif known and known[0] == code_name:
            report.add(check, "KNOWN", f"{code_name} — {known[1]}")
        else:
            report.add(check, "FAIL", f"{state or 'no status'} {shown}: {message}")
    report.facts["viewsSucceeded"] = tally["SUCCEEDED"]


def subjects(cases) -> list[str]:
    """Case subjects whichever way a row carries them: a goal written as `collect(c.subject)`
    answers strings, one generated from a question may answer whole nodes."""
    out = []
    for case in cases or []:
        if isinstance(case, dict):
            case = case.get("subject") or case.get("title") or json.dumps(case)
        out.append(str(case))
    return sorted(out)


def issue_rows(rows: list[dict]) -> list[list[str]]:
    """Each row's case subjects, by finding the column that holds a list — the generated goal
    names its columns as it likes."""
    found = []
    for row in rows:
        lists = [v for v in row.values() if isinstance(v, list)]
        found.append(subjects(lists[0]) if lists else [])
    return found


def rung_planner(inst: Instance, report: Report, realms: list[str]) -> None:
    report.rung("L4 planner end to end — an email in, a quoted plan, rows out")
    if "account-health" not in realms:
        report.add("planner", "SKIP", "realm-account-health is not installed")
        return
    fixture = dict(PLAN_FIXTURE)
    if inst.args.plan_fixture:
        with open(inst.args.plan_fixture, encoding="utf8") as f:
            fixture.update(json.load(f))
    inputs = {"email": {"type": "text", "value": fixture["email"]}}
    try:
        client = inst.mcp("/mcp/code")
        dry = client.call("plan", {"goal": fixture["goal"], "inputs": inputs, "run": False})
        quote = dry.get("quote") if isinstance(dry, dict) else None
        ok = isinstance(dry, dict) and dry.get("outcome") == "planned" and bool(quote)
        report.add("dry run is planned, with a quote", "PASS" if ok else "FAIL",
                   f"{len(dry.get('steps') or [])} steps, ~{(quote or {}).get('modelCalls')} model calls"
                   if ok else json.dumps(dry)[:180])
        report.progress("running the plan (model calls)")
        ran = client.call("plan", {"goal": fixture["goal"], "inputs": inputs, "run": True}, timeout=600)
        rows = ran.get("rows") or [] if isinstance(ran, dict) else []
        tracked = issue_rows(rows)
        hits = [t for t in tracked if any(fixture["trackedCaseContains"].lower() in s.lower() for s in t)]
        untracked = [t for t in tracked if not t]
        good = (len(rows) >= fixture["minIssues"] and hits and len(untracked) >= fixture["minUntracked"])
        report.add("run returns the rows", "PASS" if good else "FAIL",
                   f"{len(rows)} issues, {len(hits)} tracked by a case containing "
                   f"'{fixture['trackedCaseContains']}', {len(untracked)} untracked"
                   + ("" if good else f": {json.dumps(ran)[:150]}"))
        asked = client.call("plan", {"question": fixture["question"], "inputs": inputs, "run": True}, timeout=600)
        qrows = asked.get("rows") or [] if isinstance(asked, dict) else []
        same = sorted(map(tuple, issue_rows(qrows))) == sorted(map(tuple, tracked)) and len(qrows) == len(rows)
        report.add("question mode gives the same answer", "PASS" if same else "FAIL",
                   f"{len(qrows)} issues; goal it wrote: {(asked.get('goal') or '')[:110]}"
                   if same else f"goal-mode {tracked} vs question-mode {issue_rows(qrows)}")
        bare = client.call("plan", {"goal": fixture["goal"]})
        outcome = bare.get("outcome") if isinstance(bare, dict) else None
        report.add("no email → needsInput", "PASS" if outcome == "needsInput" else "FAIL", f"outcome={outcome}")
        nope = client.call("plan", {"goal": fixture["unreachableGoal"]})
        outcome = nope.get("outcome") if isinstance(nope, dict) else None
        report.add("a property nobody holds → unreachable", "PASS" if outcome == "unreachable" else "FAIL",
                   f"outcome={outcome}, missing {nope.get('missing') if isinstance(nope, dict) else '?'}")
    except Exception as e:  # noqa: BLE001
        report.add("planner", "FAIL", f"{type(e).__name__}: {e}"[:200])


# Over :Person because it is a core label every world has, realms or not. An unlabelled
# `MATCH (n)` is refused by design — it cannot be scoped to the asking user — and a check that
# wrote one would be testing its own mistake.
VIEW_NAME = "ReleaseCheckPeople"
ACTION_NAME = "Release check people count"
ACTION_SOURCE = """\
const result = await gateway.kg.query({ cypher: 'MATCH (p:Person) RETURN count(p) AS n' })
console.log('people: ' + String(result.rows[0].n))
"""


def run_saved_view(inst: Instance) -> tuple[bool, str]:
    code, body = inst.post(f"/api/v1/views/{VIEW_NAME}/invoke", {"args": {}})
    answer = as_json(body) or {}
    if code == 200 and answer.get("status") == "SUCCEEDED":
        return True, f"SUCCEEDED, {json.dumps(answer.get('data'))[:80]}"
    return False, f"HTTP {code} {answer.get('status')}: {json.dumps(answer.get('error') or body)[:140]}"


def run_saved_action(inst: Instance) -> tuple[bool, str]:
    answer = inst.mcp("/mcp/code").call("test_action", {"name": ACTION_NAME}, timeout=300)
    ok = isinstance(answer, dict) and answer.get("ok") is True
    shown = (answer.get("stdout") or answer.get("error") or json.dumps(answer)) if isinstance(answer, dict) else answer
    return ok, str(shown).strip()[:140]


def rung_authoring(inst: Instance, report: Report) -> bool:
    report.rung("L5 authoring — a view and an action, saved and run")
    code, body = inst.post("/api/v1/admin/kg/views", {
        "name": VIEW_NAME, "cypher": "MATCH (p:Person) RETURN count(p) AS people",
        "description": "Release check: how many people the world holds."})
    saved = code == 200 and (as_json(body) or {}).get("ok")
    report.add("save a view", "PASS" if saved else "FAIL", f"HTTP {code}" + ("" if saved else f": {body[:150]}"))
    if saved:
        ok, why = run_saved_view(inst)
        report.add("invoke it", "PASS" if ok else "FAIL", why)
    action_ok = False
    try:
        client = inst.mcp("/mcp/code")
        answer = client.call("create_action", {"name": ACTION_NAME, "source": ACTION_SOURCE,
                                               "description": "Release check: count the world's people."})
        created = isinstance(answer, dict) and not answer.get("isError") and answer.get("status") not in ("error",)
        report.add("create_action (observe-only)", "PASS" if created else "FAIL", json.dumps(answer)[:150])
        if created:
            action_ok, why = run_saved_action(inst)
            report.add("test_action", "PASS" if action_ok else "FAIL", why)
    except Exception as e:  # noqa: BLE001
        report.add("create_action", "FAIL", str(e)[:200])
    return bool(saved) or action_ok


def rung_durability(inst: Instance, report: Report, realms: list[str]) -> None:
    report.rung("L6 durability — restart, and re-run setup")
    report.progress(f"docker restart {inst.container}")
    subprocess.run(["docker", "restart", inst.container], capture_output=True, text=True, timeout=300)
    healthy = inst.wait_healthy()
    report.add("healthy after restart", "PASS" if healthy else "FAIL", inst.health())
    if not healthy:
        return
    check_realms_active(inst, report, realms, "active after restart")
    ok, why = run_saved_view(inst)
    report.add("saved view still runs", "PASS" if ok else "FAIL", why)
    try:
        ok, why = run_saved_action(inst)
        report.add("saved action still runs", "PASS" if ok else "FAIL", why)
    except Exception as e:  # noqa: BLE001
        report.add("saved action still runs", "FAIL", str(e)[:200])
    report.progress("embabel up, with nothing to answer on stdin")
    run = inst.cli("up")
    out = ANSI.sub("", run.stdout + run.stderr)
    asked = re.search(r"(\[y/N\]|\[Y/n\]|Username|Password|API key:|EOFError)", out)
    quiet = run.returncode == 0 and not asked
    report.add("re-run `embabel up` asks nothing", "PASS" if quiet else "FAIL",
               ("already set up" if "already set up" in out else f"exit {run.returncode}")
               + ("" if quiet else f": {out.strip().splitlines()[-1][:140] if out.strip() else ''}"))
    inst.wait_healthy()


def cleanup_authored(inst: Instance, report: Report) -> None:
    code, _ = inst.delete(f"/api/v1/admin/kg/views/{VIEW_NAME}")
    report.add("delete the view", "PASS" if code == 200 else "FAIL", f"HTTP {code}")
    try:
        answer = inst.mcp("/mcp/code").call("delete_action", {"name": ACTION_NAME})
        ok = isinstance(answer, dict) and answer.get("status") == "deleted"
        report.add("delete the action", "PASS" if ok else "FAIL", json.dumps(answer)[:150])
    except Exception as e:  # noqa: BLE001
        report.add("delete the action", "FAIL", str(e)[:200])


def rung_battery(inst: Instance, report: Report) -> None:
    report.rung("L7 realm battery")
    if not inst.args.battery:
        report.add("battery", "SKIP", "no --battery <realm checkout>")
        return
    script = os.path.join(os.path.expanduser(inst.args.battery), "tests", "verify.sh")
    if not os.path.exists(script):
        report.add("battery", "FAIL", f"no {script}")
        return
    env = {**os.environ, "APPLIANCE": inst.api, "AUTH": f"{inst.args.username}:{inst.args.password}",
           "FROM_TEXT": "1"}
    if inst.args.secrets:
        with open(os.path.expanduser(inst.args.secrets), encoding="utf8") as f:
            for line in f:
                m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
                if m and m.group(1) not in env:
                    env[m.group(1)] = m.group(2)
    if inst.args.battery_python:
        # A battery is the realm's own script, and some need a module the stdlib lacks (PyYAML).
        # Its `python3` is whatever PATH finds first, so put the chosen one there.
        shim = os.path.join(inst.scratch, "battery-bin")
        os.makedirs(shim, exist_ok=True)
        link = os.path.join(shim, "python3")
        if not os.path.lexists(link):
            os.symlink(os.path.abspath(os.path.expanduser(inst.args.battery_python)), link)
        env["PATH"] = shim + os.pathsep + env.get("PATH", "")
    report.progress(f"sh {script}")
    run = subprocess.run(["sh", script], env=env, capture_output=True, text=True, timeout=3600,
                         cwd=os.path.dirname(os.path.dirname(script)))
    out = run.stdout + run.stderr
    log = os.path.join(inst.args.report_dir, f"battery-{inst.name}.log")
    with open(log, "w", encoding="utf8") as f:
        f.write(out)
    summary = [line.strip() for line in out.splitlines()
               if re.search(r"FAIL|SKIPPED|ALL CHECKS PASS|SOME CHECKS FAILED|^==", line)]
    missing = re.search(r"ModuleNotFoundError: No module named '([^']+)'", out)
    if missing:
        report.add("battery", "FAIL", f"the battery's python3 has no '{missing.group(1)}' module — install it, "
                   f"or pass --battery-python with an interpreter that has it (log {log})")
        return
    for line in summary:
        if line.startswith("FAIL"):
            report.add("battery check", "FAIL", line[:150])
    report.add(f"verify.sh ({os.path.basename(os.path.normpath(inst.args.battery))})",
               "PASS" if run.returncode == 0 else "FAIL",
               (summary[-1] if summary else f"exit {run.returncode}") + f" — full log {log}")


# ── the way out ─────────────────────────────────────────────────────────────
#
# A user had an older, unrelated program called `embabel` on PATH. The installer rightly left it
# alone, setup stalled in the console, and `embabel uninstall` ran the other program: there was no
# way out that the screen admitted to. These two rungs are that machine, on purpose — each its own
# instance, installed and then NEVER onboarded, removed through the one-liner a stranger would
# paste, and checked for anything left behind.

FOREIGN_BODY = "#!/bin/sh\necho 'an older, unrelated embabel'\n"


def side_instance(args, suffix: str) -> Instance:
    """A second, throwaway instance with its own scratch, HOME, launcher directory and name."""
    side = argparse.Namespace(**vars(args))
    side.instance = f"{args.instance}-{suffix}"
    side.home = None
    return Instance(side, tempfile.mkdtemp(prefix=f"embabel-release-check-{side.instance}-"))


def drive(inst: Instance, *flags: str, transcript: str, env: dict | None = None) -> tuple[bool, str]:
    """scripts/drive-install.py with these flags, for this instance; (passed, its ✓/✗ lines)."""
    command = [sys.executable, os.path.join(HERE, "scripts", "drive-install.py"), *flags,
               "--timeout", str(inst.args.install_timeout), "--transcript", transcript]
    run = subprocess.run(command, env=env or inst.env, capture_output=True, text=True)
    checks = [line.strip() for line in run.stdout.splitlines() if line.strip().startswith(("✓", "✗"))]
    return run.returncode == 0, "; ".join(checks) or run.stdout[-300:]


def install_without_onboarding(inst: Instance, report: Report, *extra: str, env: dict | None = None) -> str | None:
    transcript = os.path.join(inst.scratch, "install.log")
    report.progress(f"installing instance '{inst.name}' into {inst.home} — and not onboarding it")
    ok, checks = drive(inst, "--installer", "--mode", "worlds", "--apikey", "", *extra,
                       transcript=transcript, env=env)
    report.add("install", "PASS" if ok else "FAIL", checks)
    if not inst.port_base:
        report.add("instance settings", "FAIL", f"no EMBABEL_PORT_BASE in {inst.env_file}")
        return None
    # Setup STALLED is the point: the console has the link, nobody followed it. The setup API
    # answers 410 once setup is complete, and anything else from a serving appliance means open.
    status, body = http("GET", inst.api + "/api/v1/setup", timeout=30)
    open_setup = status not in (0, 410)
    report.add("setup left unfinished", "PASS" if open_setup else "FAIL",
               f"GET /api/v1/setup → {status}" + ("" if open_setup else f": {body[:120]}"))
    return transcript


def uninstall_one_liner(inst: Instance, report: Report, env: dict | None = None) -> str:
    """`install.sh --uninstall`, under a PTY so its question is asked and answered on the terminal."""
    transcript = os.path.join(inst.scratch, "uninstall.log")
    ok, checks = drive(inst, "--uninstall", transcript=transcript, env=env)
    report.add("install.sh --uninstall", "PASS" if ok else "FAIL", checks + f" — transcript {transcript}")
    try:
        with open(transcript, encoding="utf8") as f:
            return ANSI.sub("", f.read())
    except OSError:
        return ""


def assert_nothing_left(inst: Instance, report: Report, text: str, kept: list[str] = ()) -> None:
    """Everything the installer put on the machine is gone, except [kept] — which must be named."""
    project = f"embabel-{inst.name}"
    label = f"label=com.docker.compose.project={project}"
    left = subprocess.run(["docker", "ps", "-aq", "--filter", label], capture_output=True, text=True).stdout.split()
    volumes = subprocess.run(["docker", "volume", "ls", "-q", "--filter", label],
                             capture_output=True, text=True).stdout.split()
    report.add("no containers or volumes", "PASS" if not (left or volumes) else "FAIL",
               f"{len(left)} container(s), {len(volumes)} volume(s) labelled {project}")
    # Images: each one the uninstall said it removed is gone; anything it kept, it said why.
    listed = re.findall(r"(?m)^\s{8}(\S+:\S+)\s*$", text)
    present = [ref for ref in listed
               if subprocess.run(["docker", "image", "inspect", ref], capture_output=True).returncode == 0]
    if listed:
        report.add("images removed", "PASS" if not present else "FAIL",
                   f"{len(listed) - len(present)} of {len(listed)} gone" + (f"; still here: {present}" if present else ""))
    elif "other Embabel appliances on this machine use them" in text:
        report.add("images removed", "PASS", "kept, and said why: other appliances on this machine use them")
    else:
        report.add("images removed", "FAIL", "the uninstall neither removed images nor said why it kept them")
    launcher = os.path.join(inst.env["EMBABEL_BIN_DIR"], "embabel")
    report.add("launcher removed", "PASS" if not os.path.lexists(launcher) else "FAIL", launcher)
    profiles = [os.path.join(inst.env["HOME"], name) for name in (".zshrc", ".bashrc", ".config/fish/config.fish")]
    blocks = [p for p in profiles if os.path.exists(p) and "embabel appliance PATH" in open(p, encoding="utf8").read()]
    report.add("no PATH block", "PASS" if not blocks else "FAIL", ", ".join(blocks) or "none in the test HOME")
    remaining = []
    for current, dirs, files in os.walk(inst.home):
        if any(current == k or current.startswith(k + os.sep) for k in kept):
            dirs[:] = []
            continue
        remaining += [os.path.join(current, f) for f in files]
    report.add("install folder removed", "PASS" if not remaining else "FAIL",
               ("gone" if not os.path.exists(inst.home) else f"only {', '.join(kept)} kept")
               if not remaining else f"{len(remaining)} file(s) left, e.g. {remaining[0]}")
    for path in kept:
        named = os.path.basename(path) in text
        report.add(f"kept {os.path.basename(path)}", "PASS" if os.path.exists(path) and named else "FAIL",
                   "still there, and named in the uninstall" if os.path.exists(path) and named
                   else f"exists={os.path.exists(path)}, named={named}")
    if not re.search(r"(Embabel|This appliance|The '\S+' appliance) is uninstalled\.", text):
        report.add("closing line", "FAIL", "no 'is uninstalled' at the end")


def clean_side(inst: Instance) -> None:
    """Whatever a failed rung left: by label, never by name, and only this instance's."""
    project = f"embabel-{inst.name}"
    label = f"label=com.docker.compose.project={project}"
    left = subprocess.run(["docker", "ps", "-aq", "--filter", label], capture_output=True, text=True).stdout.split()
    if left:
        subprocess.run(["docker", "rm", "-f", *left], capture_output=True)
    volumes = subprocess.run(["docker", "volume", "ls", "-q", "--filter", label],
                             capture_output=True, text=True).stdout.split()
    if volumes:
        subprocess.run(["docker", "volume", "rm", "-f", *volumes], capture_output=True)
    shutil.rmtree(inst.scratch, ignore_errors=True)


def rung_uninstall_stalled(args, report: Report) -> None:
    report.rung("L8 uninstall from a stalled setup — install, never onboard, remove with the one-liner")
    inst = side_instance(args, "stall")
    try:
        if not install_without_onboarding(inst, report):
            return
        # A realm somebody cloned into the realms folder: theirs, so it must survive and be named.
        mine = os.path.join(inst.home, "realms", "realm-mine")
        os.makedirs(mine, exist_ok=True)
        with open(os.path.join(mine, "realm.yml"), "w", encoding="utf8") as f:
            f.write("name: mine\n")
        subprocess.run(["git", "init", "-q", mine], capture_output=True)
        text = uninstall_one_liner(inst, report)
        assert_nothing_left(inst, report, text, kept=[mine])
    finally:
        clean_side(inst)


def rung_uninstall_foreign(args, report: Report) -> None:
    report.rung("L8.1 foreign embabel on PATH — named at install, full paths printed, untouched by uninstall")
    inst = side_instance(args, "foreign")
    foreign_dir = os.path.join(inst.scratch, "foreign-bin")
    os.makedirs(foreign_dir)
    foreign = os.path.join(foreign_dir, "embabel")
    with open(foreign, "w", encoding="utf8") as f:
        f.write(FOREIGN_BODY)
    os.chmod(foreign, 0o755)
    # EARLIER on PATH than anything of ours, which is the machine the user had.
    env = {**inst.env, "PATH": foreign_dir + os.pathsep + inst.env.get("PATH", "")}
    try:
        if not install_without_onboarding(inst, report, "--foreign-embabel", foreign, env=env):
            return
        text = uninstall_one_liner(inst, report, env=env)
        assert_nothing_left(inst, report, text)
        with open(foreign, encoding="utf8") as f:
            untouched = f.read() == FOREIGN_BODY
        report.add("foreign embabel untouched", "PASS" if untouched else "FAIL", foreign)
    finally:
        clean_side(inst)


def teardown(inst: Instance, report: Report) -> None:
    report.rung("teardown")
    if inst.args.keep:
        report.add("kept", "SKIP", f"--keep: instance '{inst.name}' in {inst.home}, API {inst.api}. "
                   f"Remove it with: {inst.home}/embabel --instance {inst.name} uninstall")
        return
    if os.path.exists(os.path.join(inst.home, "embabel")):
        run = inst.cli("uninstall", stdin="yes\n", timeout=600)
        report.add("embabel uninstall", "PASS" if run.returncode == 0 else "FAIL",
                   f"exit {run.returncode}" + ("" if run.returncode == 0 else f": {run.stdout[-200:]}"))
    project = f"embabel-{inst.name}"
    left = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                          capture_output=True, text=True).stdout.split()
    volumes = subprocess.run(["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
                             capture_output=True, text=True).stdout.split()
    if left or volumes:
        subprocess.run(["docker", "rm", "-f", *left], capture_output=True) if left else None
        subprocess.run(["docker", "volume", "rm", "-f", *volumes], capture_output=True) if volumes else None
        report.add("nothing left behind", "FAIL",
                   f"uninstall left {len(left)} container(s), {len(volumes)} volume(s) of {project} — removed by hand")
    else:
        report.add("nothing left behind", "PASS", f"no containers or volumes labelled {project}")
    if not inst.args.home:
        shutil.rmtree(inst.scratch, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instance", default="relcheck", help="instance name (default relcheck)")
    parser.add_argument("--release", "--version", dest="release", default="stable",
                        help="the release to install: stable (default), beta, nightly, canary, or one build — "
                             "X.Y.Z, X.Y.Z-rc.N, sha-xxxxxxx. --version is the old name")
    parser.add_argument("--ref", help="appliance branch or tag install.sh downloads (EMBABEL_REF; default main)")
    parser.add_argument("--realms", default=",".join(DEFAULT_REALMS),
                        help="comma-separated owner/repo list to install_realm (empty for none)")
    parser.add_argument("--secrets", help="a secrets.env of realm keys, copied to the instance")
    parser.add_argument("--battery", help="a realm checkout whose tests/verify.sh to run at the end")
    parser.add_argument("--battery-python", help="the python3 the battery runs under (it may need PyYAML)")
    parser.add_argument("--plan-fixture", help="JSON overriding the planner fixture (see PLAN_FIXTURE)")
    parser.add_argument("--username", default="demo")
    parser.add_argument("--password", default="terminat8r")
    parser.add_argument("--display", default="Release Check")
    parser.add_argument("--keep", action="store_true", help="leave the instance installed")
    parser.add_argument("--strict", action="store_true", help="a KNOWN issue fails the run too")
    parser.add_argument("--home", help="install directory (default: a fresh temporary one)")
    parser.add_argument("--no-install", action="store_true",
                        help="reuse an instance already installed in --home (developing this script)")
    parser.add_argument("--install-timeout", type=int, default=1800)
    parser.add_argument("--report", help="JSON report path (default: in the system temp directory)")
    ways_out = parser.add_mutually_exclusive_group()
    ways_out.add_argument("--only-uninstall", action="store_true",
                          help="run only L8 and L8.1, the uninstall rungs — each installs its own instance")
    ways_out.add_argument("--no-uninstall-rungs", action="store_true",
                          help="skip L8 and L8.1")
    args = parser.parse_args()
    args.realms = [r.strip() for r in args.realms.split(",") if r.strip()]
    if args.no_install and not args.home:
        parser.error("--no-install needs --home")
    try:
        # Kept in the form .env keeps it: `v0.4.1` is 0.4.1, `edge` is canary.
        args.release = parse_release(args.release)
    except Exception as e:
        parser.error(str(e))

    scratch = tempfile.mkdtemp(prefix=f"embabel-release-check-{args.instance}-")
    args.report_dir = tempfile.gettempdir()
    report_path = args.report or os.path.join(args.report_dir, f"embabel-release-check-{args.instance}.json")
    inst = Instance(args, scratch)
    report = Report()
    report.facts.update({"instance": args.instance, "home": inst.home, "release": args.release,
                         "imageTag": release_tag(args.release),
                         "realmsRequested": args.realms, "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
    print(f"Release check — instance '{args.instance}', release {args.release} (:{release_tag(args.release)}), "
          f"home {inst.home}")
    try:
        if args.only_uninstall:
            pass
        elif rung_install(inst, report):
            rung_image(inst, report)
            rung_setup_link(inst, report)
            if rung_onboarding(inst, report):
                rung_mcp(inst, report)
                rung_empty_world(inst, report)
                realms = rung_realms(inst, report)
                rung_planner(inst, report, realms)
                authored = rung_authoring(inst, report)
                rung_durability(inst, report, realms)
                if authored:
                    report.rung("L5 authoring — clean up")
                    cleanup_authored(inst, report)
                rung_battery(inst, report)
    except KeyboardInterrupt:
        report.add("interrupted", "FAIL", "stopped by Ctrl-C")
    finally:
        # Saved before teardown, so a teardown that hangs still leaves the transcript's path behind.
        if os.path.exists(os.path.join(scratch, "install.log")):
            kept = os.path.join(args.report_dir, f"embabel-release-check-{args.instance}-install.log")
            shutil.copyfile(os.path.join(scratch, "install.log"), kept)
            report.facts["transcript"] = kept
        if not args.only_uninstall:
            teardown(inst, report)
        else:
            shutil.rmtree(scratch, ignore_errors=True)

    # AFTER the main instance is gone, so two appliances are never booting on one machine's
    # memory at once. Each rung brings its own instance and leaves nothing of it behind.
    if not args.no_uninstall_rungs:
        try:
            rung_uninstall_stalled(args, report)
            rung_uninstall_foreign(args, report)
        except KeyboardInterrupt:
            report.add("interrupted", "FAIL", "stopped by Ctrl-C")

    counts = report.counts()
    report.facts["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(report_path, "w", encoding="utf8") as f:
        json.dump({"facts": report.facts, "counts": counts, "checks": report.rows}, f, indent=2)
    print("\n" + "=" * 100 + "\nRELEASE CHECK" + report.table())
    failed = counts.get("FAIL", 0) + (counts.get("KNOWN", 0) if args.strict else 0)
    print(f"\n{' · '.join(f'{k} {v}' for k, v in sorted(counts.items()))}   report: {report_path}")
    print("FAILED" if failed else "PASSED — now the checks only a person can make: docs/RELEASE_CHECK.md")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
