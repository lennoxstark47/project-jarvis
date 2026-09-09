#!/usr/bin/env python3
"""
Phase 5 live harness — can Jarvis actually see a thumbs-up?

scripts/selftest_gestures.py proves the decision layer offline, with a scripted
frame source and no camera. This one opens your real webcam (the light comes
on) and runs MediaPipe against it, which is the only way to check perception.

Four things worth checking, one flag each:

    # 1. what does it see, frame by frame? (nothing is confirmed — just watch)
    .venv/bin/python3 scripts/try_gestures.py --watch

    # 2. the Definition of done: does a thumbs-up confirm a pending action?
    .venv/bin/python3 scripts/try_gestures.py --confirm

    # 3. the other half of the DoD: no false triggers while you work normally
    .venv/bin/python3 scripts/try_gestures.py --soak 600

    # 4. is the camera/model side even working? (one frame, then exit)
    .venv/bin/python3 scripts/try_gestures.py --check

    # 5. does the app open the camera only while something is pending?
    .venv/bin/python3 scripts/try_gestures.py --wiring

`--watch` prints a line per classified frame, so a gesture that never fires can
be told apart from one that fires at the wrong confidence — the two need
opposite fixes (`hold_frames` vs `min_confidence` in config/jarvis.json's
`gestures` block).

`--soak` is the honest version of "no false triggers": go about your business in
front of the camera for ten minutes and see whether anything fires. It arms a
harmless action that only prints, so a false trigger is visible and costs
nothing.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis import confirm, gestures, redact, speech  # noqa: E402


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    redact.install()


def check() -> int:
    """One frame, one classification, then out. Proves the plumbing only."""
    print("Opening the camera and loading the gesture model...")
    try:
        model = gestures.ensure_model()
    except gestures.GestureError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"  model: {model} ({model.stat().st_size / 1e6:.1f} MB)")

    try:
        camera = gestures.OpenCVCamera(index=int(gestures.config.gestures_config().get("camera_index", 0)))
    except gestures.GestureError as exc:
        print(f"FAIL: {exc}")
        return 1
    try:
        recognizer = gestures.MediaPipeRecognizer()
        frame = None
        for _attempt in range(10):  # the first reads can be empty while it warms up
            frame = camera.read()
            if frame is not None:
                break
        if frame is None:
            print("FAIL: the camera opened but returned no frames.")
            return 1
        print(f"  frame: {frame.shape[1]}x{frame.shape[0]}")
        reading = recognizer.read(frame, 1)
        print(f"  classified as: {reading.label} ({reading.score:.2f})")
        recognizer.close()
    finally:
        camera.close()
    print("\nok — camera and gesture model both work.")
    print(f"bindings: {gestures.bindings()}")
    return 0


def watch(seconds: float) -> int:
    """Print every classified frame. Nothing is confirmed."""
    print(f"Watching for {seconds:.0f}s — hold a gesture in front of the camera.")
    print("(Ctrl-C to stop early. A line appears for every frame with a hand in it.)\n")

    fired: list[str] = []
    watcher = gestures.GestureWatcher(on_gesture=lambda meaning: fired.append(meaning))

    # Wrap the recogniser so every reading is printed, not just the ones that
    # fire — a gesture that never reaches hold_frames is invisible otherwise.
    real_factory = watcher._build_recognizer

    class Chatty:
        def __init__(self) -> None:
            self._inner = real_factory()
            self._last = ""

        def read(self, frame, timestamp_ms):
            reading = self._inner.read(frame, timestamp_ms)
            if reading.label != gestures.NO_GESTURE:
                line = f"  {reading.label:<12} {reading.score:.2f}"
                if line != self._last:
                    print(line, flush=True)
                    self._last = line
            return reading

        def close(self) -> None:
            self._inner.close()

    watcher._recognizer_factory = Chatty
    if not watcher.start():
        print("Gestures are disabled in config (gestures.enabled = false).")
        return 1
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and watcher.is_running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        watcher.stop()

    print(f"\n{watcher.frames_seen} frames seen; {len(fired)} gesture(s) fired: {fired}")
    if watcher.last_error:
        print(f"error: {watcher.last_error}")
        return 1
    return 0


def confirm_flow(speak: bool) -> int:
    """The Definition of done, minus the login: arm an action, confirm it by hand.

    This is exactly what happens after `fill_login_form` — Phase 4 arms a
    callable and waits. A thumbs-up here goes through the same
    `confirm.resolve(True)` a spoken "yes" does, which is the whole point of
    the phase: no separate path per input modality.
    """
    done: list[str] = []

    def pretend_to_submit() -> str:
        done.append("submitted")
        return "Submitted. (Not really — this is the gesture harness.)"

    confirm.arm("submit the (pretend) login form", pretend_to_submit)
    print("Armed a pending action: 'submit the (pretend) login form'.")
    print("Now show the camera a gesture:")
    for label, meaning in gestures.bindings().items():
        print(f"  {label:<12} -> {meaning}")
    print("\n(Ctrl-C to give up. 120s before the confirmation expires.)\n")

    answered: list[str] = []

    def on_gesture(meaning: str) -> None:
        print(f"\n>>> gesture said: {meaning}")
        reply = confirm.resolve(meaning == "confirm")
        if reply is None:
            print("    ...but nothing was armed any more (expired, or already answered).")
            return
        print(f"    Jarvis: {reply}")
        if speak:
            speech.speak(reply)
        answered.append(meaning)

    watcher = gestures.GestureWatcher(on_gesture=on_gesture)
    if not watcher.start():
        print("Gestures are disabled in config (gestures.enabled = false).")
        return 1
    try:
        deadline = time.monotonic() + confirm.TTL_SECONDS
        while not answered and time.monotonic() < deadline and watcher.is_running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        watcher.stop()
        confirm.clear()

    if watcher.last_error:
        print(f"\nerror: {watcher.last_error}")
        return 1
    if not answered:
        print("\nNothing fired. If you were holding a gesture, run --watch to see "
              "what it was being classified as.")
        return 1
    print(f"\nok — {answered[0]} by hand, and the armed action "
          f"{'ran' if done else 'was dropped'}.")
    return 0


def soak(seconds: float) -> int:
    """Watch for a long time with something armed, and count false triggers.

    The phase's Definition of done says "without false triggers during a
    10-minute session", so: `--soak 600`, and don't gesture.
    """
    triggers: list[tuple[float, str]] = []
    started = time.monotonic()

    def on_gesture(meaning: str) -> None:
        elapsed = time.monotonic() - started
        triggers.append((elapsed, meaning))
        print(f"  [{elapsed:6.1f}s] FIRED: {meaning}", flush=True)
        # Re-arm so the rest of the soak is still measuring something.
        confirm.arm("soak-test action", lambda: "nothing")

    confirm.arm("soak-test action", lambda: "nothing")
    watcher = gestures.GestureWatcher(on_gesture=on_gesture)
    if not watcher.start():
        print("Gestures are disabled in config (gestures.enabled = false).")
        return 1

    print(f"Soaking for {seconds / 60:.1f} minutes. Work normally in front of the "
          "camera — do NOT gesture deliberately.")
    print("Anything that fires is a false trigger and gets printed.\n")
    try:
        deadline = started + seconds
        next_report = started + 60.0
        while time.monotonic() < deadline and watcher.is_running:
            time.sleep(0.5)
            now = time.monotonic()
            # A "% 60 == 0" test on the elapsed seconds only prints when a
            # 0.5s sleep happens to land on an exact second, which is almost
            # never — hence an explicit next-report time.
            if now >= next_report:
                print(f"  [{now - started:6.1f}s] {watcher.frames_seen} frames, "
                      f"{len(triggers)} false trigger(s)", flush=True)
                next_report = now + 60.0
    except KeyboardInterrupt:
        print("\nstopped early.")
    finally:
        watcher.stop()
        confirm.clear()

    elapsed = time.monotonic() - started
    print(f"\n{elapsed:.0f}s, {watcher.frames_seen} frames, "
          f"{len(triggers)} false trigger(s).")
    if watcher.last_error:
        print(f"error: {watcher.last_error}")
        return 1
    return 0 if not triggers else 1


def wiring() -> int:
    """Does the menu bar app turn the camera on and off at the right moments?

    The property being checked is doc 02's: the camera is open *only* while
    something is waiting to be confirmed. It needs the real webcam (so the
    light really does come on and go off), but not the run loop — JarvisApp's
    gesture plumbing is driven here directly, so this doesn't put a second
    Jarvis in your menu bar.
    """
    from jarvis.main import GESTURES_OFF, GESTURES_WATCHING, JarvisApp

    app = JarvisApp()
    watcher = app.gesture_watcher
    problems: list[str] = []

    def state() -> str:
        app._sync_gesture_watcher()
        return app._latest_gesture_state

    print("1. nothing armed — the camera must be off")
    print(f"   running={watcher.is_running}  state={state()!r}")
    if watcher.is_running or app._latest_gesture_state != GESTURES_OFF:
        problems.append("the camera was on with nothing pending")

    ran: list[str] = []
    confirm.arm("submit the (pretend) form", lambda: ran.append("go") or "Submitted.")
    print("2. a confirmation is armed — the camera must come on")
    state()
    deadline = time.monotonic() + 15
    while watcher.frames_seen == 0 and time.monotonic() < deadline and watcher.is_running:
        time.sleep(0.25)
    print(f"   running={watcher.is_running}  state={app._latest_gesture_state!r}  "
          f"frames={watcher.frames_seen}  error={watcher.last_error}")
    if not watcher.is_running or app._latest_gesture_state != GESTURES_WATCHING:
        problems.append(f"the camera did not come on: {watcher.last_error}")
    elif watcher.frames_seen == 0:
        problems.append("the camera opened but produced no frames")

    print("3. a gesture fires — the armed action must run")
    app.handle_gesture("confirm")
    print(f"   action ran={ran}  reply={app._latest_reply!r}  "
          f"transcript={app._latest_transcript!r}")
    if ran != ["go"]:
        problems.append("a confirm gesture did not run the armed action")

    print("4. nothing pending any more — the camera must go off")
    print(f"   running={watcher.is_running}  state={state()!r}")
    if watcher.is_running or app._latest_gesture_state != GESTURES_OFF:
        problems.append("the camera stayed on after the confirmation was answered")

    print("5. a gesture with nothing armed must say nothing")
    before = app._latest_reply
    app.handle_gesture("confirm")
    if app._latest_reply != before:
        problems.append("a gesture with nothing armed changed the reply")
    print(f"   reply unchanged={app._latest_reply == before}")

    watcher.stop()
    confirm.clear()
    if problems:
        print("\nFAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nok — the camera is on exactly while something is pending.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="one frame, prove camera+model work")
    mode.add_argument("--watch", action="store_true", help="print what it sees, confirm nothing")
    mode.add_argument("--confirm", action="store_true", help="arm an action and confirm it by gesture")
    mode.add_argument("--soak", type=float, metavar="SECONDS",
                      help="watch for this long and count false triggers (DoD: 600)")
    mode.add_argument("--wiring", action="store_true",
                      help="camera on only while something is pending (needs no run loop)")
    parser.add_argument("--seconds", type=float, default=60.0, help="how long --watch runs (default 60)")
    parser.add_argument("--speak", action="store_true", help="also say the reply out loud in --confirm")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.check:
        return check()
    if args.watch:
        return watch(args.seconds)
    if args.confirm:
        return confirm_flow(args.speak)
    if args.soak is not None:
        return soak(args.soak)
    if args.wiring:
        return wiring()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
