#!/usr/bin/env python3
"""
Development entry point for Jarvis.

    source .venv/bin/activate
    python3 run.py

Adds src/ to the path so `jarvis` is importable without installing the
package — good enough for Phase 0-era iteration. setup.py (py2app) takes over
for the "real" packaged background-app path once that's worth the effort.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jarvis.main import main  # noqa: E402 - path insert must happen first

if __name__ == "__main__":
    main()
