#!/usr/bin/env python3
"""Embabel Me in one command.

    ./me.py             # start the Me door (pulling images if needed) and set it up
    ./me.py --fresh     # wipe ALL appliance state first (asks), then the above

Nothing here but a hand-off: setup.py owns the whole flow — door launch, the
first-boot log streamed live, and the wizard. This file exists so the new-user
instruction is one typeable word long. See worlds.py for the other door.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.execv(sys.executable, [sys.executable, os.path.join(HERE, "setup.py"), "me", *sys.argv[1:]])
