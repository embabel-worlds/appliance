#!/usr/bin/env python3
"""Embabel Worlds in one command.

    ./worlds.py             # start the worlds mode (pulling images if needed) and set it up
    ./worlds.py --fresh     # wipe ALL appliance state first (asks), then the above

Nothing here but a hand-off: setup.py owns the whole flow — mode launch, the
first-boot log streamed live, the wizard, and the closing block of Worlds URLs.
This file exists so the new-user instruction is one typeable word long.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.execv(sys.executable, [sys.executable, os.path.join(HERE, "setup.py"), "worlds", *sys.argv[1:]])
