#!/usr/bin/env python3
"""No credential's VALUE is ever printed by the CLI — it is named, and withheld.

WHY THIS EXISTS. `embabel upgrade` on an install that predated the pins printed
`Pinned in .env: GRAPH_TYPE=NEO4J, NEO4J_VERSION=…, NEO4J_PASSWORD=<the password>, …`
to the terminal. Upgrade output is exactly what gets pasted into an issue, shown on a
call or kept in a CI log. So each place that reports a setting, a token or a client's
error is driven here with a sentinel value, and every byte it prints is searched for it.

Nothing here talks to Docker or a client CLI: volumes, containers and subprocesses are patches.

    python3 scripts/check-secret-echo.py
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from embabel_setup import agents, bugreport, dockerlib, pins, settings  # noqa: E402

SENTINEL = "s3ntinel-n0t-a-real-passw0rd"
TOKEN = "s3ntinel-mcp-bearer-t0ken"
failures = []


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


@contextlib.contextmanager
def captured():
    """Everything printed, to either stream."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        yield out


@contextlib.contextmanager
def checkout(env_text, environ=None):
    """A scratch checkout with this .env, no pins in the environment but `environ`, no Docker."""
    with tempfile.TemporaryDirectory() as root:
        if env_text is not None:
            with open(os.path.join(root, ".env"), "w") as f:
                f.write(env_text)
        clean = {k: v for k, v in os.environ.items() if k not in {p.key for p in pins.PINS}}
        with patch.object(settings, "APPLIANCE_DIR", root), \
                patch.dict(os.environ, {**clean, **(environ or {})}, clear=True), \
                patch.object(pins, "volume_exists", return_value=False), \
                patch.object(dockerlib, "_docker", return_value=None), \
                patch.object(dockerlib, "service_container", return_value=None), \
                patch.object(pins, "_store_the_server_has", return_value="sqlite"):
            settings.use_instance(settings.DEFAULT_INSTANCE)
            yield root


# ── which names are credentials ──────────────────────────────────────────────

for key in ("NEO4J_PASSWORD", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "EMBABEL_KEY_SECRET", "GITHUB_TOKEN",
            "NEO4J_AUTH", "EMBABEL_SETUP_MCP_TOKEN", "APPLIANCE_PASS", "EMBABEL_PASSWORD"):
    check(settings.is_secret_setting(key), f"{key} is not treated as a credential")
for key in ("GRAPH_TYPE", "NEO4J_VERSION", "ASSISTANT_EMBEDDING_MODEL", "ASSISTANT_DOC_CONVERTER",
            "OLLAMA_BASE_URL", "EMBABEL_MONITORING", "EMBABEL_PORT_BASE"):
    check(not settings.is_secret_setting(key), f"{key} is hidden, and it is not a credential")
# Every pinned row whose name says credential is one the pin line must withhold.
check(any(settings.is_secret_setting(p.key) for p in pins.PINS), "no pin row is a credential — has NEO4J_PASSWORD moved?")

# ── the pin line: a new install that states its Neo4j password ───────────────

with checkout(None, {"GRAPH_TYPE": "NEO4J", "NEO4J_PASSWORD": SENTINEL}) as root, captured() as out:
    written = pins.pin_install(creating=True)
    stored = settings.env_file_value("NEO4J_PASSWORD")
printed = out.getvalue()
check(written.get("NEO4J_PASSWORD") == SENTINEL and stored == SENTINEL,
      "the stated password was not the one pinned — this check no longer exercises the pin line")
check(SENTINEL not in printed, "pin_install printed the Neo4j password")
check("NEO4J_PASSWORD" in printed, "pin_install no longer names the credential it pinned")
check("NEO4J_VERSION=" in printed, "pin_install stopped showing the values that are not credentials")

# ── the pin line: an install that predates the pins (the `embabel upgrade` case) ──

with checkout("EMBABEL_PORT_BASE=11042\n") as root, captured() as out:
    written = pins.pin_install(creating=False)
legacy = written.get("NEO4J_PASSWORD")
check(legacy, f"an existing Neo4j install did not pin its password: {written}")
check(legacy and f"={legacy}" not in out.getvalue(), "upgrading an old install printed its Neo4j password")

# ── a bug report's settings file ─────────────────────────────────────────────

with checkout(f"GRAPH_TYPE=NEO4J\nNEO4J_PASSWORD={SENTINEL}\nOPENAI_API_KEY={SENTINEL}\n"):
    report = bugreport._redacted_env()
check(SENTINEL not in report, "the bug report's env-keys.txt carries a credential")
check("NEO4J_PASSWORD" in report, "the bug report no longer says which settings exist")

# ── wiring coding agents: the MCP bearer token ───────────────────────────────

result = {"token": TOKEN, "url": "http://localhost:1/mcp/chat", "developerUrl": "http://localhost:1/mcp/code"}

# No client on PATH: the manual fallback and the chat-client block.
with patch.object(agents.shutil, "which", return_value=None), captured() as out:
    agents.wire_coding_agents(result)
check(TOKEN not in out.getvalue(), "the manual-wiring instructions printed the MCP token")
check("--show-token" in out.getvalue(), "the manual-wiring instructions no longer say where the token is")

# Claude Code on PATH, and its `mcp add` times out — Python's message quotes the command line.
def claude_times_out(argv, **_kwargs):
    if "add" in argv:
        raise subprocess.TimeoutExpired(argv, 60)
    return subprocess.CompletedProcess(argv, 0, "", "")


def claude_fails_loudly(argv, **_kwargs):
    return subprocess.CompletedProcess(argv, 1, "", f"refused: {' '.join(argv)}")


for runner in (claude_times_out, claude_fails_loudly):
    with patch.object(agents.shutil, "which", side_effect=lambda n: "/fake/claude" if n == "claude" else None), \
            patch.object(agents, "registered_mcp_url", return_value=None), \
            patch.object(agents, "prompt", return_value="y"), \
            patch.object(agents.subprocess, "run", side_effect=runner), captured() as out:
        agents.wire_coding_agents(result)
    check(TOKEN not in out.getvalue(), f"a failed `claude mcp add` ({runner.__name__}) printed the MCP token")

# Codex on PATH, the operator declines the profile edit: they are told what to add, not the token.
with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}), \
        patch.object(agents.shutil, "which", side_effect=lambda n: "/fake/codex" if n == "codex" else None), \
        patch.object(agents, "registered_mcp_url", return_value=None), \
        patch.object(agents, "shell_profile", return_value=os.path.join(home, ".zshrc")), \
        patch.object(agents, "install_codex_agents_block", return_value=None), \
        patch.object(agents, "prompt", side_effect=lambda q: "n" if "token" in q.lower() else "y"), \
        patch.object(agents.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
        captured() as out:
    agents.wire_coding_agents(result)
check(TOKEN not in out.getvalue(), "the Codex instructions printed the MCP token")
check(f"{agents.CODEX_TOKEN_ENV}=<token>" in out.getvalue(), "the Codex instructions no longer say what to export")

if failures:
    print("FAIL:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("ok: no credential value is printed by the pin line, the bug report, or agent wiring")
