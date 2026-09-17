"""Finish setup in the browser: `embabel up --browser` (worlds mode).

The console can drive the same setup API this installer drives, one question at a
time. What it cannot do is start the appliance or read the token from its log, so
the terminal still does those, then opens the console with the token and waits.

THE TOKEN GOES AFTER THE `#`. A fragment is never sent to a server, so the token
stays out of the console's access log; the console takes it off the address bar the
moment it reads it.

A link this early used to be an invitation to a surface that was not ready (see the
note where setup.py prints nothing until the end). This one is different: the page it
opens IS the setup, and it says so while the appliance is still starting.

WHAT THE TERMINAL STILL OWNS, and why this flow does not do it:
- wiring Claude Code and Codex on this machine: the MCP token is shown once, in the
  browser, and never reaches this process. The console shows it, and Settings can
  mint another;
- indexing the guides: it signs in with the account's password, which only the
  browser saw (embabel-worlds/appliance#87 moves this into the appliance);
- the usage-reporting disclosure: shown HERE, before handing over, because the
  console does not show it yet.
"""
from __future__ import annotations
import time

from .colour import TICK, bold, dim, heading, url
from .core import AlreadySetUp, SetupError, Unreachable
from .dockerlib import container_started_at
from .settings import console_url
from .status import STATUS, wait_until_serving
from .steps import call, disclose_usage_reporting
from .surfaces import open_in_browser, print_worlds_surfaces

# Long enough to answer a handful of questions and paste a key, with room to spare;
# the appliance stays up either way, and `embabel up --browser` picks up again.
BROWSER_WAIT_SECONDS = 60 * 60


def setup_link(token: str) -> str:
    return f"{console_url()}/#setup={token}"


def hand_setup_to_browser(base: str, token: str, container: str | None) -> int:
    """Open the console on setup and wait for it to finish. Returns an exit code."""
    disclose_usage_reporting(base)

    link = setup_link(token)
    print("\n" + heading("Finish setup in your browser"))
    # Printed first and always: over ssh, or with no opener, the link is the whole answer.
    print(f"  {url(link)}")
    if open_in_browser(link):
        print("  " + dim("Opening it in your browser…"))
    print("  " + dim("This link carries the setup token. It works until setup is finished."))

    started_before = container_started_at(container) if container else ""
    STATUS.start("Waiting for setup to finish in the browser")
    deadline = time.monotonic() + BROWSER_WAIT_SECONDS
    # The last thing setup said about MCP before it closed: after /complete the API
    # answers 410 and says nothing, so this is the only way to know what to print.
    mcp_token = False
    try:
        while time.monotonic() < deadline:
            try:
                mcp_token = bool(call(base, "", token).get("mcpTokenExists")) or mcp_token
            except AlreadySetUp:
                break
            except Unreachable:
                # Finishing restarts the appliance, and a restart looks exactly like this.
                pass
            time.sleep(3)
        else:
            STATUS.stop()
            raise SetupError(
                "Setup was not finished in the browser within an hour. Nothing is lost; "
                "run `embabel up --browser` to pick it up again."
            )
        STATUS.set("Restarting to pick up what you connected")
        wait_until_serving(container, base, started_before)
    finally:
        STATUS.stop()

    print(f"\n  {TICK} Setup complete. {bold('Embabel Worlds')} is at {url(console_url())}")
    print_worlds_surfaces(base, mcp_token=mcp_token)
    return 0


__all__ = ["BROWSER_WAIT_SECONDS", "hand_setup_to_browser", "setup_link"]
