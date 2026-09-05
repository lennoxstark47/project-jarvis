"""
Jarvis menu bar shell.

Phase 0 proved out the two things every later phase depends on:
- the process can live in the menu bar (no normal window, no Dock icon) rather
  than as a foreground terminal app
- it can get real microphone + camera access from that same process

Phase 1 adds push-to-talk voice capture on top of that: hold F9, speak, and
the transcript shows up in the menu bar and the log (see jarvis.voice /
jarvis.stt) — no action is taken on it yet, that's Phase 2's job.

Run during development with:

    source .venv/bin/activate
    python3 run.py

See docs/00-IMPLEMENTATION_TRACKER.md for how to package this as a real .app
and install it as a launchd agent.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import rumps
from AppKit import NSStatusBar

from jarvis.permissions import check_camera, check_microphone
from jarvis.voice import HOTKEY_NAME, PushToTalk

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

# How long after launch to keep re-checking that the menu bar item is actually
# on screen, and how often. See JarvisApp._watch_status_item for why this exists.
STATUS_ITEM_WATCH_INTERVAL = 3.0
STATUS_ITEM_WATCH_DURATION = 60.0

# How often the main-thread timer checks for a new transcript to display.
# Kept separate from the status-item watchdog interval - this one is about
# UI latency (part of the ~1-2s Definition of done), that one about recovery.
TRANSCRIPT_UI_POLL_INTERVAL = 0.25


class JarvisApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Jarvis", icon=None, quit_button="Quit Jarvis")
        self.transcript_item = rumps.MenuItem("Last transcript: (none yet)")
        self.menu = ["Check Permissions", f"Hold {HOTKEY_NAME.upper()} to talk", self.transcript_item]
        self._status_item_elapsed = 0.0
        self._status_item_ever_on_screen = False
        self._status_item_repairs = 0

        # Set from PushToTalk's background thread (handle_transcript), read
        # from the main thread (_refresh_transcript_ui). Cocoa/AppKit calls
        # like updating a menu item's title aren't safe to make from an
        # arbitrary thread, so the background thread only ever writes this
        # plain string under a lock - a rumps.Timer on the main thread is what
        # actually touches the menu item.
        self._transcript_lock = threading.Lock()
        self._latest_transcript = "(none yet)"
        self._displayed_transcript = "(none yet)"

    # -- permission check ---------------------------------------------------

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

    # -- push-to-talk transcript ---------------------------------------------

    def handle_transcript(self, text: str) -> None:
        """Called from PushToTalk's background thread when a transcript is ready.

        Only writes the plain string under a lock - see the comment on
        ``_latest_transcript`` in __init__ for why the actual menu bar update
        happens elsewhere, on the main thread.
        """
        with self._transcript_lock:
            self._latest_transcript = text if text else "(heard nothing)"

    def _refresh_transcript_ui(self, _timer: rumps.Timer) -> None:
        with self._transcript_lock:
            text = self._latest_transcript
        if text == self._displayed_transcript:
            return
        self._displayed_transcript = text
        self.transcript_item.title = f"Last transcript: {text}"

    def _startup_check(self, timer: rumps.Timer) -> None:
        """Run the startup permission check once, from inside the run loop.

        Deliberately *not* called before ``app.run()``: opening the camera takes
        several seconds, and doing that first delays creation of the status item
        by exactly that much, so the app looks like it failed to launch. Running
        it on a timer means the menu bar item shows up immediately and the check
        happens right after.
        """
        timer.stop()
        self.run_permission_check()

    # -- menu bar item watchdog ---------------------------------------------

    def _status_item_on_screen(self) -> bool:
        """Whether our NSStatusItem actually has a placed window in the menu bar.

        ``NSStatusItem.isVisible()`` only reflects the requested visibility, not
        whether macOS really gave the item a spot, so check the backing window
        instead — an item the system never placed has no window (or a zero-width
        one).
        """
        nsapp = getattr(self, "_nsapp", None)
        item = getattr(nsapp, "nsstatusitem", None)
        if item is None:
            return False
        button = item.button()
        if button is None:
            return False
        window = button.window()
        if window is None:
            return False
        return window.frame().size.width > 0

    def _repair_status_item(self) -> None:
        """Drop the dead status item and ask the status bar for a fresh one.

        Not done via rumps' own ``initializeStatusBar()``: that re-adds the quit
        button to the menu every call, so calling it twice would leave two
        "Quit Jarvis" entries.
        """
        nsapp = self._nsapp
        status_bar = NSStatusBar.systemStatusBar()
        old_item = getattr(nsapp, "nsstatusitem", None)
        if old_item is not None:
            status_bar.removeStatusItem_(old_item)

        new_item = status_bar.statusItemWithLength_(-1)  # variable dimensions
        new_item.setHighlightMode_(True)
        nsapp.nsstatusitem = new_item
        nsapp.setStatusBarIcon()
        nsapp.setStatusBarTitle()
        new_item.setMenu_(self._menu._menu)

        self._status_item_repairs += 1
        logger.warning(
            "Menu bar item was not on screen — recreated it (repair #%d).",
            self._status_item_repairs,
        )

    def _watch_status_item(self, timer: rumps.Timer) -> None:
        """Keep the menu bar item alive through the first minute after launch.

        rumps creates the status item synchronously in ``App.run()``, *before*
        the event loop starts. At login that can happen while the GUI session is
        still coming up, and macOS then silently never places the item: the
        process runs fine (mic/camera work, the log looks healthy) but nothing
        appears in the menu bar, and the only way to get it back is to restart
        the app by hand. Observed on 2026-09-05 — launched by launchd at login it
        never showed, and `launchctl kickstart -k` on the very same build showed
        it instantly.

        Re-asking the status bar for an item once the run loop is up recovers
        from that, so poll for a while and rebuild whenever the item isn't
        actually on screen.
        """
        self._status_item_elapsed += timer.interval

        if self._status_item_on_screen():
            if not self._status_item_ever_on_screen:
                self._status_item_ever_on_screen = True
                logger.info("Menu bar item is on screen.")
        else:
            self._repair_status_item()

        if self._status_item_elapsed >= STATUS_ITEM_WATCH_DURATION:
            timer.stop()
            if not self._status_item_ever_on_screen:
                logger.error(
                    "Menu bar item never appeared after %.0fs and %d repair "
                    "attempts — the app is running but has no UI.",
                    STATUS_ITEM_WATCH_DURATION,
                    self._status_item_repairs,
                )


def main() -> None:
    logger.info("Jarvis starting up.")
    app = JarvisApp()

    # Both timers fire as soon as the run loop starts (NSTimer's first fire date
    # is "now"), so the menu bar item exists before either does any work.
    # Watchdog is scheduled first so it gets a clean first pass before the
    # permission check blocks the main thread on the camera for a few seconds.
    rumps.Timer(app._watch_status_item, STATUS_ITEM_WATCH_INTERVAL).start()
    rumps.Timer(app._startup_check, 1.0).start()
    rumps.Timer(app._refresh_transcript_ui, TRANSCRIPT_UI_POLL_INTERVAL).start()

    # Kept on the app instance (not a local var) so it isn't a candidate for
    # garbage collection for as long as JarvisApp itself is alive.
    app.push_to_talk = PushToTalk(on_transcript=app.handle_transcript)
    app.push_to_talk.start()

    app.run()


if __name__ == "__main__":
    main()
