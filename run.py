"""Entry point for the packaged app (PyInstaller starts here)."""
import multiprocessing
import sys

from clipclapcloup.__main__ import main

if __name__ == "__main__":
    # Without this, a frozen Windows build can relaunch itself when any
    # library touches multiprocessing.
    multiprocessing.freeze_support()
    sys.exit(main())
