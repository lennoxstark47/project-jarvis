#!/usr/bin/env python3
"""
Development entry point for Jarvis.

    source .venv/bin/activate
    python3 run.py

Adds src/ to the path so `jarvis` is importable without installing the
package. scripts/build_app.sh wraps this same entry point in a real .app
bundle (so macOS grants it its own mic/camera permission identity) for the
"real" packaged background-app path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jarvis.main import main  # noqa: E402 - path insert must happen first

if __name__ == "__main__":
    main()
