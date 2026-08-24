"""Where to go once the appliance is up, and how to get there.

The two doors present differently — Worlds hands you a console in a browser, Me
hands you a desktop app — but the shape is the same: say the address first,
because it is the whole answer on a headless box, and only then try to open it.

Launching the Me app also has to SEED it, or a freshly built app opens on an
empty settings file and asks the operator for a URL they were just shown.
"""
import json
import os
import platform
import shutil
import subprocess
import sys

from .colour import ARROW, MIDDOT, TICK, accent, bold, dim, url
from .core import APPLIANCE_DIR, ME_APP_DIR, prompt
from .settings import console_url, surface_urls
from .colour import heading

def print_worlds_surfaces(base: str) -> None:
    """Worlds onboarding ends at the way in, not at "done": every
    surface a worlds operator reaches next, in one block. The API/MCP lines use the
    mode's real detected port; the rest are the compose defaults (.env moves them)."""
    print("  " + heading("Your Worlds surfaces", 58))
    print(f"  {bold('Console')}        {url(surface_urls()['console'])}   "
          + accent(f"{ARROW} START HERE"))
    print("                 The Worlds console: realms, documents, keys, views, chat.")
    print("                 Opens with the commissioning sequence.")
    print()
    print(f"  API            {url(base)}   " + dim("(the server the console talks to)"))
    print(f"  Dashboards     {url(surface_urls()['dashboards'])}   {MIDDOT}   "
          f"Metrics  {url(surface_urls()['metrics'])}")
    print()
    # LAST, because they are the takeaway: the two MCP servers are how anything
    # agentic reaches this world, and the last lines of a wizard are the ones
    # that survive in the terminal when everything above has scrolled away.
    # (The graph link is gone on purpose \u2014 the browser and bolt ports are an
    # implementation detail; the console and MCP are the product's doors.)
    print(f"  MCP servers    {url(base + '/mcp')}       {dim('embabel-me \u2014 chat clients, the assistant surface')}")
    print(f"                 {url(base + '/mcp/dev')}   {dim('embabel-worlds \u2014 coding agents: realms, authoring')}")
    print("                 Authorization: Bearer \u2014 the token this setup just minted,")
    print("                 stored at /data/embabel/assistant/admin/providers.env")
    print()


def print_me_surfaces(base: str) -> None:
    """Where a Me operator goes next.

    The worlds mode has always ended on a block like this; Me ended on the app
    offer alone, which left the assistant's own web UI, its MCP endpoint and the
    graph undiscoverable from the terminal that had just configured all three.
    """
    print("  \u2500\u2500 Your Me surfaces " + "\u2500" * 41)
    print(f"  Assistant      {base}   \u2190 START HERE")
    print("                 Chat, documents and memories, in the browser.")
    print()
    # The two MCP servers close the block \u2014 see print_worlds_surfaces for why
    # they come last and the graph link is gone.
    print(f"  MCP servers    {base}/mcp      " + dim("embabel-me \u2014 chat clients, the assistant surface"))
    print(f"                 {base}/mcp/dev  " + dim("embabel-worlds \u2014 coding agents: realms, authoring"))
    print("                 Authorization: Bearer \u2014 the token this setup just minted,")
    print("                 stored at /data/embabel/assistant/admin/providers.env")
    print()


def me_app_settings_file() -> str | None:
    """Where the Me app keeps its settings — Electron's per-app userData directory,
    named for the app itself (`app.setName('Embabel Me')`). Only the platforms the
    app runs on are mapped; anywhere else, seeding is silently skipped."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Embabel Me", "settings.json")
    if sys.platform.startswith("linux"):
        config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        return os.path.join(config, "Embabel Me", "settings.json")
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return os.path.join(appdata, "Embabel Me", "settings.json") if appdata else None
    return None


def seed_me_app_settings(base: str, username: str | None) -> None:
    """Hand the Me app what setup already established: which mode to talk to, and
    who the user is. Retyping a URL and username the wizard just set up is pure
    friction — and the URL is worth seeding even at the default, because a
    non-default ASSISTANT_PORT would otherwise leave the app pointed at nothing.

    NEVER the password. Setup knows it, but a wizard silently writing a credential
    to a file the user did not choose is a different act from that user typing it
    into an app they can see — one keystroke of theirs is a fair price for that
    line staying honest.

    Only fills what is MISSING: an existing setting is the user's own answer, and
    a later run must not overwrite it with an assumption."""
    path = me_app_settings_file()
    if path is None:
        return
    try:
        current = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                current = json.load(f)
        if not isinstance(current, dict):
            return  # a settings file we do not recognise is not ours to rewrite
        seeded = dict(current)
        if not seeded.get("baseUrl"):
            seeded["baseUrl"] = base
        if username and not seeded.get("username"):
            seeded["username"] = username
        if seeded == current:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(seeded, f, indent=2)
    except (OSError, ValueError):
        # Seeding is a courtesy; the app asks for these anyway. Never fail setup
        # over a settings file.
        pass


def packaged_me_app() -> str | None:
    """The built app, if this machine has one — installed, or sitting in the repo's
    own release directory after `npm run package`. Returns a path `open -a` takes."""
    if sys.platform != "darwin":
        return None
    candidates = [
        "/Applications/Embabel Me.app",
        os.path.expanduser("~/Applications/Embabel Me.app"),
    ]
    release = os.path.join(ME_APP_DIR, "release")
    if os.path.isdir(release):
        for entry in sorted(os.listdir(release)):
            candidates.append(os.path.join(release, entry, "Embabel Me.app"))
    return next((c for c in candidates if os.path.isdir(c)), None)


def open_in_browser(target: str) -> bool:
    """Open a URL, if this machine has anything to open it with.

    The one moment worth doing this is the end of setup — the appliance has just
    been waited for, so the page will render rather than refuse, which is the
    difference between a finish and a broken link. Before the restart wait
    existed, opening here would have shown a dead port.

    Refuses in the cases where a browser is the wrong answer: no opener on PATH
    (a server, a container), no terminal (a script, CI), or SSH_CONNECTION set —
    over ssh the opener runs on the WRONG MACHINE, which is worse than not
    running at all. EMBABEL_NO_BROWSER opts out for anyone who just dislikes it.
    """
    if os.environ.get("EMBABEL_NO_BROWSER") or os.environ.get("SSH_CONNECTION"):
        return False
    if not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return False
    opener = next((cmd for cmd in ("open", "xdg-open", "wslview") if shutil.which(cmd)), None)
    if not opener:
        return False
    try:
        subprocess.Popen([opener, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def launch_me_app(base: str, username: str | None = None) -> None:
    """Me onboarding ends in the Me app, the way worlds onboarding ends at the
    console: the appliance thinks, the app senses, and a new user should meet
    both. The app is plain JavaScript on Electron — no build step — so `npm
    install` (run here on first use, for Electron itself) is the whole cost."""
    if not os.path.isdir(ME_APP_DIR):
        return  # a checkout without the app (or a remote setup) — nothing to offer
    seed_me_app_settings(base, username)
    print("\n  " + heading("The Me app", 58))
    print("  Your appliance thinks; the Me app senses. It sits in your menu bar,")
    print(f"  reads local signals, and sends only what you approve to {base}.")
    # A packaged build wins when there is one: it carries the app's own identity,
    # so macOS names IT in permission prompts ("Embabel Me wants to control Google
    # Chrome") instead of naming Electron, and the user gets no terminal, no npm,
    # and no build step. `npm start` stays the developer path.
    packaged = packaged_me_app()
    if packaged:
        answer = prompt("  Start it now? [Y/n]: ").strip().lower()
        if answer not in ("", "y", "yes"):
            print(f'  Whenever you like:  open -a "{packaged}"')
            return
        subprocess.Popen(["open", "-a", packaged], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('  Starting — look for "Me" in your menu bar. The appliance URL and your username')
        print("  are filled in already; enter your password and it will offer its first scan.")
        return

    npm = shutil.which("npm")
    if npm is None:
        print("  It needs Node.js (https://nodejs.org). Once that is installed:")
        print("      cd me-app && npm start")
        return
    answer = prompt("  Start it now? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        print("  Whenever you like:  cd me-app && npm start")
        return
    if not os.path.isdir(os.path.join(ME_APP_DIR, "node_modules", "electron")):
        # The app's one dependency, dev-time only. Foreground, so a failure is
        # visible and a slow download narrates itself instead of hanging mute.
        print("  First run — fetching Electron (npm install)…")
        run = subprocess.run([npm, "install"], cwd=ME_APP_DIR)
        if run.returncode != 0:
            print("  npm install failed — fix the error above, then: cd me-app && npm start")
            return
    # Detached, output dropped: the app outlives this wizard, and Electron's
    # chatter has no business in the terminal being handed back.
    subprocess.Popen([npm, "start"], cwd=ME_APP_DIR, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('  Starting — look for "Me" in your menu bar. The appliance URL and your username')
    print("  are filled in already; enter your password and it will offer its first scan.")
