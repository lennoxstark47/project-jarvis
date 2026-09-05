"""
Jarvis Phase 0 menu bar shell.

Proves out the two things every later phase depends on:
- the process can live in the menu bar (no normal window, no Dock icon) rather
  than as a foreground terminal app
- it can get real microphone + camera access from that same process

Run during development with:

    source .venv/bin/activate
    python3 run.py

See docs/00-IMPLEMENTATION_TRACKER.md (Phase 0 notes) for how to package this
as a real .app and install it as a launchd agent.
"""
from __future__ import annotations

import logging
from pathlib import Path

import rumps

from jarvis.permissions import check_camera, check_microphone

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "jarvis.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("jarvis.main")


class JarvisApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Jarvis", icon=None, quit_button="Quit Jarvis")
        self.menu = ["Check Permissions"]

    def run_permission_check(self) -> None:
        logger.info("Running mic/camera permission check.")
        mic_ok = check_microphone()
        cam_ok = check_camera()
        self.title = f"Jarvis [{'🎤' if mic_ok else '🚫🎤'}{'📷' if cam_ok else '🚫📷'}]"
        try:
            rumps.notification(
                title="Jarvis",
                subtitle="Permission check complete",
                message=(
                    f"mic: {'ready' if mic_ok else 'NOT ready'} · "
                    f"camera: {'ready' if cam_ok else 'NOT ready'}"
                ),
            )
        except RuntimeError:
            # macOS notifications need a real app-bundle identity
            # (CFBundleIdentifier), which a bare `python3 run.py` process
            # doesn't have — only the scripts/build_app.sh-built .app does.
            # The menu bar title update above already carries the same info,
            # so this is a dev-mode-only degradation, not a functional gap.
            logger.info(
                "Skipping OS notification (no app bundle identity yet — "
                "expected until packaged via scripts/build_app.sh)."
            )

    @rumps.clicked("Check Permissions")
    def check_permissions(self, _sender: rumps.MenuItem) -> None:
        self.run_permission_check()


def main() -> None:
    logger.info("Jarvis Phase 0 starting up.")
    app = JarvisApp()
    # Check on startup too, not just on menu click — this is what makes Phase
    # 0's "prints mic: ready / camera: ready" definition of done true the
    # moment the process launches, without needing to click anything first.
    app.run_permission_check()
    app.run()


if __name__ == "__main__":
    main()
