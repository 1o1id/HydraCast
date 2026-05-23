#!/usr/bin/env python3
"""
hydracast_bg.py  —  Background-mode entry point for HydraCast.

This is a thin wrapper that forces --background so the EXE built from
this file always starts HydraCast with no console window / TUI.
Drop hydracast_bg.exe next to hydracast.exe; it shares the same
bin/, config/, media/, and logs/ folders.
"""
import sys
# Inject --background before argparse sees argv so the user never has
# to pass it manually.  Guard against double-injection if somehow called
# with the flag already present.
if "--background" not in sys.argv and "-b" not in sys.argv:
    sys.argv.insert(1, "--background")

# Re-use the exact same main() — no duplication.
from hydracast import main

if __name__ == "__main__":
    main()
