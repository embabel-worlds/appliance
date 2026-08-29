#!/usr/bin/env python3
"""Embabel Me in one command.

    ./me.py                   # start the Me mode (pulling images if needed) and set it up
    ./me.py --fresh           # wipe ALL appliance state first (asks), then the above
    ./me.py --reset-password  # forgot the password: new account, all data kept

Nothing here but a hand-off: setup.py owns the whole flow — mode launch, the
first-boot log streamed live, the wizard, and at the end an offer to start the
Me app (the menu-bar sensor in me-app/), so one command takes a new machine all
the way to a sensing appliance. Re-running it on a set-up appliance skips
straight to that offer. This file exists so the new-user instruction is one
typeable word long. See worlds.py for the other mode.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARGV = [sys.executable, os.path.join(HERE, "setup.py"), "me", *sys.argv[1:]]

# Windows: see worlds.py for why os.execv is not safe on Windows for an
# interactive hand-off (console ownership flips to the parent shell as
# the exec-parent exits, and the child inherits a console the shell has
# already started reading from). subprocess.call keeps the parent alive
# throughout and keeps the console single-owner.
if os.name == "nt":
    sys.exit(subprocess.call(ARGV))
else:
    os.execv(sys.executable, ARGV)
