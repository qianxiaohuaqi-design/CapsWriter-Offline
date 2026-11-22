"""Keep windowed startup imports from writing to None stdout/stderr.

No user configuration is imported here; bootstrap belongs to the app entry point.
"""
import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
