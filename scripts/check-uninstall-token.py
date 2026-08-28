#!/usr/bin/env python3
"""Regression check for removing appliance-owned Codex tokens during uninstall."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embabel_setup import agents  # noqa: E402

with tempfile.TemporaryDirectory() as home:
    profiles = (
        os.path.join(home, ".zshrc"),
        os.path.join(home, ".bashrc"),
        os.path.join(home, ".config", "fish", "config.fish"),
    )
    expected = "before\nafter\n"
    with patch.dict(os.environ, {"HOME": home}):
        for profile in profiles:
            os.makedirs(os.path.dirname(profile), exist_ok=True)
            with open(profile, "w") as f:
                f.write("before\n")
            agents.install_codex_token("not-a-real-token", profile)
            with open(profile, "a") as f:
                f.write("after\n")

        with patch.object(agents.shutil, "which", return_value=None), \
             patch.object(agents, "CODEX_AGENTS_FILE", os.path.join(home, ".codex", "AGENTS.md")):
            agents.unwire_coding_agents()

        for profile in profiles:
            with open(profile) as f:
                actual = f.read()
            assert actual == expected, f"appliance token block remains in {profile}: {actual!r}"

print("ok: uninstall removes only appliance-owned token blocks from shell profiles")
