# PyInstaller runtime hook for windowed builds.
# Some .NET / pywebview imports write to stdout/stderr; both are None in a
# windowed build, which can abort the process before main() runs.
import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
