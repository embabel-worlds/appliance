#!/usr/bin/env python3
"""`embabel agents` must restore coding agents to the code MCP door.

    python3 scripts/check-agents.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embabel_setup import clidata  # noqa: E402

clidata.current_mode = lambda: "worlds"
clidata.s.find_mode_container = lambda mode: "worlds-container"
clidata.s.container_base_url = lambda container: "http://localhost:11043"
clidata.s._docker = lambda *args: SimpleNamespace(
    returncode=0,
    stdout="EMBABEL_SETUP_MCP_TOKEN=secret\nEMBABEL_SETUP_MCP_TOKEN_USER=rod\n",
)
received = []
clidata.s.wire_coding_agents = received.append

assert clidata.cmd_agents(SimpleNamespace(show_token=False)) == 0
assert received == [{
    "token": "secret",
    "url": "http://localhost:11043/mcp/chat",
    "developerUrl": "http://localhost:11043/mcp/code",
    "user": "rod",
}], received
print("  ✓ embabel agents restores the chat and coding-agent MCP doors")
