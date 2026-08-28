"""Pointing coding agents at this appliance, and unpointing them.

Claude Code and Codex are the two clients the appliance knows how to wire. Both
speak MCP, and neither is wired the same way: Claude takes the bearer token
inline, Codex reads it from an environment variable at session start and records
only the variable's NAME. That asymmetry is most of what is here.

The token itself is minted by the server exactly once, during setup, so nothing
in this module can re-issue one — it can only register the one it is handed.
"""

from __future__ import annotations
import os
import re
import shlex
import shutil
import subprocess

from .colour import TICK, bold, dim, heading, warn
from .core import APPLIANCE_DIR, prompt
from .settings import env_file_value, surface_urls
from .words import say

# The name setup registers with MCP clients, and therefore the name --uninstall
# has to remove. One constant so the two cannot disagree.
MCP_SERVER_NAME = "embabel"


# The env var Codex reads the MCP bearer token from: `codex mcp add` stores the
# variable's NAME in its config, never the token, so the operator exports this.
CODEX_TOKEN_ENV = "EMBABEL_MCP_TOKEN"


CODEX_AGENTS_FILE = os.path.expanduser("~/.codex/AGENTS.md")


AGENTS_BLOCK_BEGIN = "<!-- BEGIN embabel appliance -->"


AGENTS_BLOCK_END = "<!-- END embabel appliance -->"
TOKEN_BLOCK_BEGIN = "# BEGIN embabel appliance MCP token"
TOKEN_BLOCK_END = "# END embabel appliance MCP token"
SHELL_PROFILES = {
    "zsh": "~/.zshrc",
    "bash": "~/.bashrc",
    "fish": "~/.config/fish/config.fish",
}


def shell_profile() -> str | None:
    """The startup file for the current interactive shell."""
    profile = SHELL_PROFILES.get(os.path.basename(os.environ.get("SHELL", "")))
    return os.path.expanduser(profile) if profile else None


def shell_profiles() -> list[str]:
    """Every shell profile setup may have added the appliance token to."""
    return [os.path.expanduser(profile) for profile in SHELL_PROFILES.values()]


def codex_token_export(token: str, target: str | None = None) -> str:
    """Shell command that exports the Codex token, including fish syntax when needed."""
    return (f"set -gx {CODEX_TOKEN_ENV} {shlex.quote(token)}"
            if target and target.endswith("config.fish")
            else f"export {CODEX_TOKEN_ENV}={shlex.quote(token)}")


def install_codex_token(token: str, target: str) -> None:
    """Put the Codex token in one replaceable shell-profile block."""
    block = f"{TOKEN_BLOCK_BEGIN}\n{codex_token_export(token, target)}\n{TOKEN_BLOCK_END}\n"
    existed = os.path.exists(target)
    existing = ""
    if existed:
        with open(target) as f:
            existing = f.read()
    pattern = re.compile(
        rf"{re.escape(TOKEN_BLOCK_BEGIN)}.*?{re.escape(TOKEN_BLOCK_END)}\n?",
        re.DOTALL,
    )
    updated = pattern.sub(block, existing) if pattern.search(existing) else (
        existing + ("" if not existing or existing.endswith("\n") else "\n") + block
    )
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as f:
        f.write(updated)
    if not existed:
        os.chmod(target, 0o600)


def remove_codex_token(target: str) -> bool:
    """Remove only the appliance-owned token block from a shell profile."""
    if not os.path.exists(target):
        return False
    with open(target) as f:
        existing = f.read()
    updated = re.sub(
        rf"{re.escape(TOKEN_BLOCK_BEGIN)}.*?{re.escape(TOKEN_BLOCK_END)}\n?",
        "",
        existing,
        flags=re.DOTALL,
    )
    if updated == existing:
        return False
    with open(target, "w") as f:
        f.write(updated)
    return True


def codex_agents_block() -> str:
    """The global-guidance block for Codex, pointing back at this checkout.

    A ROUTER, not a copy: the canonical guidance is AGENTS.md at the repo root and
    the full runbooks are skills/*/SKILL.md, all readable by any agent from the
    absolute path below. Embedding the path is the point — global guidance applies
    in sessions on OTHER projects, where a relative path means nothing.
    """
    # APPLIANCE_DIR, not dirname(__file__): this used to live in setup.py at the
    # checkout root, where those were the same directory. They are not the same
    # from inside the package, and the value is written into someone's AGENTS.md
    # as an absolute path an agent will later try to read.
    checkout = os.path.abspath(APPLIANCE_DIR)
    return (
        f"{AGENTS_BLOCK_BEGIN}\n"
        f"## Embabel appliance\n"
        f"The MCP server named '{MCP_SERVER_NAME}' is an Embabel appliance — the user's world\n"
        f"runtime (data, realms, saved views, apps). Before working with it, read\n"
        f"`{checkout}/AGENT_GUIDE.md` — the first-calls list there saves failed guesses, and it\n"
        f"routes to full runbooks under `{checkout}/skills/` for realm prospecting,\n"
        f"realm diagnosis, calling the server from apps, building world-served apps, and\n"
        f"interrogating a world.\n"
        f"{AGENTS_BLOCK_END}\n"
    )


def install_codex_agents_block(target: str = CODEX_AGENTS_FILE) -> None:
    """Put (or refresh) the marked block in Codex's global AGENTS.md, idempotently.

    Only the region between our markers is ever touched; everything else in the
    file is somebody's own guidance and stays byte-for-byte.
    """
    block = codex_agents_block()
    existing = ""
    if os.path.exists(target):
        with open(target) as f:
            existing = f.read()
    if AGENTS_BLOCK_BEGIN in existing and AGENTS_BLOCK_END in existing:
        head, rest = existing.split(AGENTS_BLOCK_BEGIN, 1)
        _, tail = rest.split(AGENTS_BLOCK_END, 1)
        updated = head + block.rstrip("\n") + tail
    else:
        joiner = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        updated = existing + joiner + block
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as f:
        f.write(updated)


def remove_codex_agents_block(target: str = CODEX_AGENTS_FILE) -> bool:
    """Remove OUR marked block from Codex's global AGENTS.md; everything else stays.
    Returns True when a block was actually removed."""
    if not os.path.exists(target):
        return False
    with open(target) as f:
        existing = f.read()
    if AGENTS_BLOCK_BEGIN not in existing or AGENTS_BLOCK_END not in existing:
        return False
    head, rest = existing.split(AGENTS_BLOCK_BEGIN, 1)
    _, tail = rest.split(AGENTS_BLOCK_END, 1)
    updated = (head.rstrip("\n") + "\n" + tail.lstrip("\n")).strip("\n")
    with open(target, "w") as f:
        f.write(updated + "\n" if updated else "")
    return True


def this_appliance_urls() -> set[str]:
    """Every MCP URL that means THIS install, normalized for comparison.

    Both modes count: Me and Worlds are the same checkout, the same volume and the
    same account, so a registration against either port belongs to this appliance.
    A configured public base URL counts too — that is what a remote client was
    wired with.
    """
    bases = [
        surface_urls()["me"],
        surface_urls()["worlds"],
        surface_urls()["me"].replace("localhost", "127.0.0.1"),
        surface_urls()["worlds"].replace("localhost", "127.0.0.1"),
    ]
    for key in ("ASSISTANT_PUBLIC_BASE_URL", "WORLDS_PUBLIC_BASE_URL"):
        value = env_file_value(key)
        if value:
            bases.append(value)
    # EVERY door this appliance has ever answered on, including the ones it no longer
    # offers. Coding agents are wired against the code door, so an uninstall that only
    # recognized /mcp would leave that registration standing — a client pointed at an
    # appliance that no longer exists. /mcp/dev is the code door's old name and stays in
    # this list for exactly that reason: the registrations it made are still out there,
    # and uninstall is the one place that has to recognize history rather than intent.
    doors = ("/mcp", "/mcp/chat", "/mcp/code", "/mcp/dev")
    return {base.rstrip("/").lower() + door for base in bases for door in doors}


def registered_mcp_url(cli: str, name: str) -> str | None:
    """The URL a client has registered under [name], from its own `mcp get` output.

    Both CLIs print a `URL:`/`url:` line; anything else (entry absent, output
    reshaped) comes back None, which callers treat as "cannot tell".
    """
    try:
        run = subprocess.run([cli, "mcp", "get", name],
                             capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return None
    if run.returncode != 0:
        return None
    for line in run.stdout.splitlines():
        match = re.match(r"\s*url:\s*(\S+)", line, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def unwire_coding_agents() -> None:
    """Drop the MCP registration setup minted — and ONLY that one, verified by URL.

    The token is issued once and never returned again, so a registration that
    outlives its volume is not stale config — it is a client pointed at an
    appliance that cannot authenticate it, failing on every session start with
    nothing to say why. Removing the account without removing this is how you end
    up with `embabel: Failed to connect` in `claude mcp list` and no idea when it
    broke.

    But the NAME is not proof of ownership: '{MCP_SERVER_NAME}' in a client may
    point at a different appliance — a remote one, another checkout on other
    ports. Removing by name alone would take out a registration this uninstall
    never created. So each client is asked what URL it has, and only an entry
    pointing at this install is removed; anything else is left standing and said
    so. "Cannot tell" also leaves it standing — deleting on uncertainty is the
    wrong default for someone else's config.

    Must run while .env still exists: this install's ports live there.
    """
    ours = this_appliance_urls()
    for name, cli in (("Claude Code", shutil.which("claude")), ("Codex", shutil.which("codex"))):
        if not cli:
            continue
        url = registered_mcp_url(cli, MCP_SERVER_NAME)
        if url is None:
            continue
        if url.rstrip("/").lower() not in ours:
            print(f"  Left {name}'s '{MCP_SERVER_NAME}' registration alone — it points at {url}, not this appliance.")
            continue
        try:
            run = subprocess.run([cli, "mcp", "remove", MCP_SERVER_NAME],
                                 capture_output=True, text=True, timeout=30)
        except (subprocess.SubprocessError, OSError):
            continue
        if run.returncode == 0:
            print(f"  Removed the '{MCP_SERVER_NAME}' MCP server from {name}.")
    try:
        if remove_codex_agents_block():
            print(f"  Removed the appliance block from {CODEX_AGENTS_FILE}.")
    except OSError:
        pass
    for profile in shell_profiles():
        try:
            if remove_codex_token(profile):
                print(f"  Removed the appliance MCP token from {profile}.")
        except OSError:
            pass


def wire_coding_agents(result: dict) -> None:
    """Offer to point Claude Code and Codex at the appliance, using the token the mcp step just
    minted. The token exists in this process exactly once — the server never returns
    it again — so this is the moment to hand it to a client.

    `claude mcp add` only writes config; the token itself goes live when setup
    completes and the appliance restarts, and the closing message says so."""
    # Coding agents get the CODE door (/mcp/code) when the server has one — its
    # tools/list is the building surface (realm authoring, mining) with none of the
    # personal-assistant tools. The server states both URLs as facts and this installer
    # picks; `developerUrl` is absent entirely on a server without the door, so an old
    # image degrades to the chat door rather than to a 404. The wire key keeps the older
    # word because the server's own type for that surface is McpMode.DEVELOPER.
    token = result.get("token")
    url = result.get("developerUrl") or result.get("url")
    if not token or not url:
        return

    print("\n" + heading("Wire up coding agents"))
    say("coding-agents")
    print()
    wired = False
    claude = shutil.which("claude")
    if claude:
        answer = prompt("  Point Claude Code at this appliance now (user scope)? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            try:
                # REMOVE FIRST, exactly as the Codex path does. `claude mcp add` refuses a
                # name that already exists, and this branch treated that refusal as a
                # message to print rather than a thing to fix — so an operator who had
                # ever wired an earlier appliance kept the OLD entry, silently. A
                # developer hit precisely that: his Claude Code went on dialling
                # localhost:4242, the port this product used before the rebase, because
                # the registration from his previous install was never replaced.
                #
                # The token is also a reason on its own: it is minted fresh here, so even
                # a same-URL entry holds a bearer that no longer authenticates.
                existing = registered_mcp_url(claude, MCP_SERVER_NAME)
                if existing and existing.rstrip("/").lower() != url.rstrip("/").lower():
                    print(f"  (replacing Claude Code's '{MCP_SERVER_NAME}' entry, which pointed at {existing})")
                # No --scope: the CLI then removes the entry from WHICHEVER scope holds
                # it. Naming `user` would step over a stale entry sitting in local or
                # project scope, which would go on shadowing the one we are about to
                # write — the same silent survival this whole block exists to end.
                subprocess.run([claude, "mcp", "remove", MCP_SERVER_NAME],
                               capture_output=True, text=True, timeout=30)
                run = subprocess.run(
                    [claude, "mcp", "add", "--transport", "http", "--scope", "user",
                     MCP_SERVER_NAME, url, "--header", f"Authorization: Bearer {token}"],
                    capture_output=True, text=True, timeout=60,
                )
                if run.returncode == 0:
                    print(f"  Claude Code wired as '{MCP_SERVER_NAME}' — new sessions will see the appliance.")
                    wired = True
                else:
                    print(f"  claude mcp add failed: {(run.stderr or run.stdout).strip()[:200]}")
            except (subprocess.SubprocessError, OSError) as e:
                print(f"  Could not run claude: {e}")
    else:
        print("  Claude Code CLI not found on PATH.")

    # Codex reads the bearer token from an ENVIRONMENT VARIABLE at session start —
    # `codex mcp add` records only the variable's NAME, never the token itself. So
    # wiring Codex is two moves: register the server, then get the export into the
    # operator's shell profile. The second half cannot be done for them silently
    # (editing someone's shell profile uninvited is not this script's place), so it
    # is printed as the one line they must add — loudly, because a registration
    # whose variable is unset fails on every session with nothing to say why.
    codex = shutil.which("codex")
    if codex:
        answer = prompt("  Point Codex at this appliance too? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            try:
                existing = registered_mcp_url(codex, MCP_SERVER_NAME)
                # `url` is a whole endpoint, not a base — the second arm of this test used
                # to append "/mcp" to it and so could never match anything.
                if existing and existing.rstrip("/").lower() != url.rstrip("/").lower():
                    print(f"  (replacing Codex's '{MCP_SERVER_NAME}' entry, which pointed at {existing})")
                subprocess.run([codex, "mcp", "remove", MCP_SERVER_NAME],
                               capture_output=True, text=True, timeout=30)
                run = subprocess.run(
                    [codex, "mcp", "add", MCP_SERVER_NAME, "--url", url,
                     "--bearer-token-env-var", CODEX_TOKEN_ENV],
                    capture_output=True, text=True, timeout=60,
                )
                if run.returncode == 0:
                    print(f"  Codex wired as '{MCP_SERVER_NAME}'.")
                    profile = shell_profile()
                    if profile:
                        answer = prompt(f"  Add its token to {profile}? [Y/n]: ").strip().lower()
                        if answer in ("", "y", "yes"):
                            try:
                                install_codex_token(token, profile)
                                print(f"  Token added to {profile} — open a new terminal to use it.")
                            except OSError as e:
                                print(f"  Could not update {profile}: {e}")
                                print(f"  Add this to {profile} before using Codex:")
                                print(f"    {codex_token_export(token, profile)}")
                        else:
                            print(f"  Add this to {profile} before using Codex:")
                            print(f"    {codex_token_export(token, profile)}")
                    else:
                        print(f"  Set ${CODEX_TOKEN_ENV} in your shell before using Codex:")
                        print(f"    {codex_token_export(token)}")
                    try:
                        install_codex_agents_block()
                        print(f"  Added appliance guidance to {CODEX_AGENTS_FILE} (a marked block; the rest of the file is untouched).")
                    except OSError as e:
                        print(f"  Could not update {CODEX_AGENTS_FILE}: {e}")
                    wired = True
                else:
                    print(f"  codex mcp add failed: {(run.stderr or run.stdout).strip()[:200]}")
            except (subprocess.SubprocessError, OSError) as e:
                print(f"  Could not run codex: {e}")

    # THE CHAT DOOR, ALWAYS — wired or not.
    #
    # This function wires `claude` and `codex`, which are the two clients with a
    # CLI to wire. The door most people would reach through a tool they already
    # have open has none, so it was never mentioned: the step said "coding
    # agents", the closing block called /mcp "chat clients", and nothing in
    # between ever handed the operator what a chat client needs. It costs two
    # lines and it is the same token.
    chat_url = result.get("url") or url
    if chat_url != url:
        print(f"\n  For a chat client — Claude Desktop, Open WebUI, anything speaking MCP:")
        print(f"    URL:    {chat_url}")
        if wired:
            print("    Token:  `embabel agents --show-token`")
        else:
            print(f"    Header: Authorization: Bearer {token}")
        print("  " + dim("Same token, the assistant's tools rather than the builder's."))

    if wired:
        return

    # Manual fallback — also what Cursor and other MCP clients copy from. Printing
    # the token is deliberate: this is the operator's own machine and the only time
    # it is available.
    print("\n  Wire a coding agent manually:")
    print(f"    URL:    {url}")
    print(f"    Header: Authorization: Bearer {token}")
    print(f"  (Claude Code: claude mcp add --transport http --scope user {MCP_SERVER_NAME} "
          f"{url} --header \"Authorization: Bearer <token>\")")
    print(f"  (Codex:       codex mcp add {MCP_SERVER_NAME} --url {url} "
          f"--bearer-token-env-var {CODEX_TOKEN_ENV}, then export {CODEX_TOKEN_ENV})")
