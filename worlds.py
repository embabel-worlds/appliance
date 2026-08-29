#!/usr/bin/env python3
"""Embabel Worlds in one command.

    ./worlds.py             # start the worlds mode (pulling images if needed) and set it up
    ./worlds.py --fresh     # wipe ALL appliance state first (asks), then the above

Nothing here but a hand-off: setup.py owns the whole flow — mode launch, the
first-boot log streamed live, the wizard, and the closing block of Worlds URLs.
This file exists so the new-user instruction is one typeable word long.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARGV = [sys.executable, os.path.join(HERE, "setup.py"), "worlds", *sys.argv[1:]]

# Windows: os.execv() is NOT a real exec. Python's Windows implementation
# spawns a new process and exits the original, and during that transition
# the console owner changes from the exiting parent to the new child.
# cmd/PowerShell see the parent end, take back the console, and read the
# next typed line as their own command -- typed usernames were leaking to
# the parent shell as "'aheifetz' is not recognized as an internal or
# external command". subprocess.call keeps the parent alive throughout,
# which keeps console ownership single-owner on every platform.
if os.name == "nt":
    sys.exit(subprocess.call(ARGV))
else:
    os.execv(sys.executable, ARGV)
