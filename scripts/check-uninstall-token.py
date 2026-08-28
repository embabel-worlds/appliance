#!/usr/bin/env python3
"""Regression checks for appliance-owned Codex token cleanup during uninstall."""
import contextlib
import io
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
    from embabel_setup import agents  # noqa: E402

    assert agents.CODEX_AGENTS_FILE.startswith(home), "check imported a path from the real home directory"
    profiles = tuple(os.path.expanduser(path) for path in agents.SHELL_PROFILES.values())
    expected = b"before\nafter\xff\n"

    def install_blocks():
        for profile in profiles:
            os.makedirs(os.path.dirname(profile), exist_ok=True)
            with open(profile, "wb") as f:
                f.write(b"before\n")
            agents.install_codex_token("not-a-real-token", profile)
            with open(profile, "ab") as f:
                f.write(b"after\xff\n")

    install_blocks()
    for profile in profiles:
        assert agents.remove_codex_token(profile)
        with open(profile, "rb") as f:
            assert f.read() == expected, f"token cleanup changed unrelated bytes in {profile}"
        assert not agents.remove_codex_token(profile), "token cleanup is not idempotent"

    install_blocks()
    with patch.object(agents.shutil, "which", side_effect=lambda name: "/fake/codex" if name == "codex" else None), \
         patch.object(agents, "registered_mcp_url", return_value="http://localhost:9999/mcp/code"):
        agents.unwire_coding_agents()
    for profile in profiles:
        with open(profile, "rb") as f:
            assert agents.TOKEN_BLOCK_BEGIN.encode() in f.read(), "foreign Codex registration lost its shared token"

    output = io.StringIO()
    with patch.object(agents.shutil, "which", return_value=None), contextlib.redirect_stdout(output):
        agents.unwire_coding_agents()
    assert "ownership could not be verified" in output.getvalue(), "missing Codex ownership was not reported"
    for profile in profiles:
        with open(profile, "rb") as f:
            assert agents.TOKEN_BLOCK_BEGIN.encode() in f.read(), "unverifiable Codex registration lost its token"

    output = io.StringIO()
    with patch.object(agents.shutil, "which", side_effect=lambda name: "/fake/codex" if name == "codex" else None), \
         patch.object(agents, "registered_mcp_url", return_value=None), \
         contextlib.redirect_stdout(output):
        agents.unwire_coding_agents()
    assert "could not be verified" in output.getvalue(), "unknown Codex ownership was not reported"
    for profile in profiles:
        with open(profile, "rb") as f:
            assert agents.TOKEN_BLOCK_BEGIN.encode() in f.read(), "unknown Codex registration lost its token"

    owned_url = next(iter(agents.this_appliance_urls()))
    with patch.object(agents.shutil, "which", side_effect=lambda name: "/fake/codex" if name == "codex" else None), \
         patch.object(agents, "registered_mcp_url", return_value=owned_url), \
         patch.object(agents.subprocess, "run", return_value=agents.subprocess.CompletedProcess([], 0)):
        agents.unwire_coding_agents()
    for profile in profiles:
        with open(profile, "rb") as f:
            assert f.read() == expected, "owned Codex registration kept its stale token"

    profile = profiles[0]
    no_trailing_newline = b"export USER_SETTING=kept"
    with open(profile, "wb") as f:
        f.write(no_trailing_newline)
    agents.install_codex_token("not-a-real-token", profile)
    agents.install_codex_token("replacement-not-a-real-token", profile)
    assert agents.remove_codex_token(profile)
    with open(profile, "rb") as f:
        assert f.read() == no_trailing_newline, "install then removal left an appliance-owned separator"

    crlf = (b"before\r\n" + agents.TOKEN_BLOCK_BEGIN.encode() + b"\r\n"
            + f"export {agents.CODEX_TOKEN_ENV}=not-a-real-token".encode() + b"\r\n"
            + agents.TOKEN_BLOCK_END.encode() + b"\r\nafter\r\n")
    with open(profile, "wb") as f:
        f.write(crlf)
    agents.install_codex_token("not-a-real-token", profile)
    assert agents.remove_codex_token(profile)
    with open(profile, "rb") as f:
        assert f.read() == b"before\r\nafter\r\n", "CRLF content outside the token block changed"

    quoted = f"echo '{agents.TOKEN_BLOCK_BEGIN}'\nunrelated\n{agents.TOKEN_BLOCK_END}\n".encode()
    with open(profile, "wb") as f:
        f.write(quoted)
    agents.install_codex_token("not-a-real-token", profile)
    assert agents.remove_codex_token(profile)
    with open(profile, "rb") as f:
        assert f.read() == quoted, "inline marker text caused unrelated content removal"

    malformed = f"{agents.TOKEN_BLOCK_BEGIN}\nexport {agents.CODEX_TOKEN_ENV}=not-a-real-token\n".encode()
    with open(profile, "wb") as f:
        f.write(malformed)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert not agents.remove_codex_token(profile)
    with open(profile, "rb") as f:
        assert f.read() == malformed, "malformed token block was modified"
    assert "incomplete" in output.getvalue().lower(), "malformed token block was not reported"

    output = io.StringIO()
    owned_url = next(iter(agents.this_appliance_urls()))
    with patch.object(agents.shutil, "which", side_effect=lambda name: "/fake/codex" if name == "codex" else None), \
         patch.object(agents, "registered_mcp_url", return_value=owned_url), \
         patch.object(agents.subprocess, "run", return_value=agents.subprocess.CompletedProcess([], 0)), \
         patch.object(agents, "shell_profiles", return_value=[profile]), \
         patch.object(agents, "remove_codex_token", side_effect=OSError("read failed")), \
         contextlib.redirect_stdout(output):
        agents.unwire_coding_agents()
    assert "Could not update" in output.getvalue(), "profile cleanup failure was hidden"

print("ok: uninstall safely removes only owned Codex token blocks")
