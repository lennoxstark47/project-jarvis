"""
Jarvis menu bar shell.

Phase 0 proved out the two things every later phase depends on:
- the process can live in the menu bar (no normal window, no Dock icon) rather
  than as a foreground terminal app
- it can get real microphone + camera access from that same process

Phase 1 added push-to-talk voice capture on top of that: hold F9, speak, and
the transcript shows up in the menu bar and the log (see jarvis.voice /
jarvis.stt).

Phase 2 hands that transcript to the brain (jarvis.agent) instead of only
displaying it: the configured LLM backend decides whether the utterance is a
request for one of Jarvis's tools (jarvis.tools) or just something to reply to,
and both the reply and any action taken show up in the menu bar. Which backend
answers is a config/jarvis.json setting, not a code change (jarvis.router).

Phase 3 gave those tools real weight - one of them launches Claude Code and can
run for minutes - so the menu bar grew a live status line fed by the agent's
on_status callback. That's doc 02's "listening / thinking / speaking / running a
sub-agent" surface, and the reason a long sub-agent call doesn't look like a
hang.

Phase 4 makes the conversation two-way: every reply is also spoken out loud
(jarvis.speech), which is the "speaking" state that status line always had a
name for. It also installs the redaction filter (jarvis.redact) on the log
handlers here, at the one place logging is configured, so doc 04's rule about
credentials never reaching a log file is enforced for the whole process rather
than remembered call site by call site - including the menu bar's own copy of
the transcript, which is on screen and therefore in every screenshot.

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

from jarvis import redact, speech
from jarvis.agent import Agent
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
# Every log line, from every module, goes through this. Installed immediately
# after basicConfig and before anything else can log (doc 04, point 5).
redact.install()

logger = logging.getLogger("jarvis.main")

# How long after launch to keep re-checking that the menu bar item is actually
# on screen, and how often. See JarvisApp._watch_status_item for why this exists.
STATUS_ITEM_WATCH_INTERVAL = 3.0
STATUS_ITEM_WATCH_DURATION = 60.0

# How often the main-thread timer checks for new text to display.
# Kept separate from the status-item watchdog interval - this one is about
# UI latency (part of the ~1-2s Definition of done), that one about recovery.
TRANSCRIPT_UI_POLL_INTERVAL = 0.25

# Menu bar placeholders, also what the labels revert to on a fresh launch.
NO_TRANSCRIPT = "(none yet)"
NO_REPLY = "(nothing yet)"
IDLE_STATUS = "idle"
SPEAKING_STATUS = "speaking"

# Menu items are one line in a dropdown, and Phase 3's tools return whole
# paragraphs (Claude Code summarizes what it found). Truncate for display only -
# logs/jarvis.log keeps the full text.
MENU_TEXT_LIMIT = 90


class JarvisApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Jarvis", icon=None, quit_button="Quit Jarvis")
        self.transcript_item = rumps.MenuItem(f"Last transcript: {NO_TRANSCRIPT}")
        self.reply_item = rumps.MenuItem(f"Jarvis: {NO_REPLY}")
        self.status_item = rumps.MenuItem(f"Status: {IDLE_STATUS}")
        self.menu = [
            "Check Permissions",
            f"Hold {HOTKEY_NAME.upper()} to talk",
            self.transcript_item,
            self.reply_item,
            self.status_item,
        ]
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
        self._latest_transcript = NO_TRANSCRIPT
        self._displayed_transcript = NO_TRANSCRIPT
        self._latest_reply = NO_REPLY
        self._displayed_reply = NO_REPLY
        self._latest_status = IDLE_STATUS
        self._displayed_status = IDLE_STATUS

        # Each utterance is handled on its own thread (jarvis.voice spawns one
        # per hotkey release), so two can be in flight at once and they do not
        # finish in order. Seen live 2026-09-08: an utterance whose sub-agent
        # ran long had its summary land 32 seconds *after* the next command had
        # already been answered, overwriting a fresh reply with a stale one.
        # Every turn takes a number; only the newest one may write to the UI.
        # The number travels on the handling thread, which is also the thread
        # the agent and its tools run their status callbacks from.
        self._newest_turn = 0
        self._turn = threading.local()

        # The brain. Constructed here but not connected to anything until the
        # first utterance - jarvis.agent builds the backend (and reads its API
        # key) lazily, so a missing key surfaces as a spoken-style error on the
        # first command rather than a crash at launch.
        self.agent = Agent(on_status=self.set_status)

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

        Runs the transcript through the agent loop and stores both halves for
        the UI. Only plain strings are written here, under a lock - see the
        comment on ``_latest_transcript`` in __init__ for why the actual menu
        bar update happens elsewhere, on the main thread.

        The agent call is deliberately made on this (background) thread: it's a
        network round-trip to an LLM, and doing it on the main thread would
        freeze the menu bar for its duration.
        """
        with self._transcript_lock:
            self._newest_turn += 1
            self._turn.id = self._newest_turn
            # Redacted for display: this string ends up in the menu bar, which
            # is on screen, in screenshots, and in screen recordings. The agent
            # gets the real text - it has its own, exact redaction (doc 04).
            self._latest_transcript = redact.redact_secrets(text) if text else "(heard nothing)"

        if not text:
            return

        try:
            result = self.agent.handle(text)
            logger.info("agent: %s", result.summary())
            with self._transcript_lock:
                if self._is_newest():
                    self._latest_reply = result.reply or self._describe_actions(result)
                    spoken = self._latest_reply
                else:
                    spoken = ""
                    logger.info("dropping a stale reply — you've spoken since.")
            # Outside the lock: speaking takes seconds, and holding the lock for
            # it would stall the UI timer and every other turn for that long.
            if spoken:
                self._speak(spoken)
        finally:
            # Whatever happened, stop claiming Jarvis is still working on it -
            # a status line stuck on "Claude Code: Read agent.py" is exactly the
            # hung-looking UI this was added to prevent. Routed through
            # set_status so an overtaken turn can't clear a newer turn's status
            # on its way out.
            self.set_status(IDLE_STATUS)

    def _is_newest(self) -> bool:
        """Whether the calling thread is handling the most recent utterance.

        Caller must hold ``_transcript_lock``. A turn that is no longer newest
        has been overtaken by something the user said more recently, and has no
        business writing to the menu bar any more.
        """
        return getattr(self._turn, "id", 0) == self._newest_turn

    def set_status(self, message: str) -> None:
        """Show what Jarvis is doing right now. Called from background threads.

        Same lock-and-poll discipline as the transcript: only a plain string is
        written here, and the main-thread timer is what touches the menu item.
        """
        with self._transcript_lock:
            if self._is_newest():
                self._latest_status = message or IDLE_STATUS

    def _speak(self, reply: str) -> None:
        """Read a reply out loud, if this turn is still the newest one.

        Blocks for as long as the sentence takes. That's fine here - this runs
        on the per-utterance background thread - and jarvis.speech interrupts
        whatever is mid-sentence when a newer reply arrives, so talking over
        Jarvis works the way it does with a person.
        """
        self.set_status(SPEAKING_STATUS)
        speech.speak(reply)

    @staticmethod
    def _shorten(text: str) -> str:
        text = " ".join((text or "").split())
        return text if len(text) <= MENU_TEXT_LIMIT else text[: MENU_TEXT_LIMIT - 3] + "..."

    @staticmethod
    def _describe_actions(result) -> str:
        """Fallback label for a turn where the model acted but said nothing."""
        if result.actions:
            return " · ".join(output for _call, output in result.actions)
        return result.error or NO_REPLY

    def _refresh_transcript_ui(self, _timer: rumps.Timer) -> None:
        with self._transcript_lock:
            text = self._latest_transcript
            reply = self._latest_reply
            status = self._latest_status
        if text != self._displayed_transcript:
            self._displayed_transcript = text
            self.transcript_item.title = f"Last transcript: {self._shorten(text)}"
        if reply != self._displayed_reply:
            self._displayed_reply = reply
            self.reply_item.title = f"Jarvis: {self._shorten(reply)}"
        if status != self._displayed_status:
            self._displayed_status = status
            self.status_item.title = f"Status: {self._shorten(status)}"

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
