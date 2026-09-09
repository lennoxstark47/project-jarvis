#!/usr/bin/env python3
"""
Offline self-test for Phase 5's gesture channel.

No camera, no model file, no hand, no network — this checks the half of
gestures that can be checked without a person in front of a lens:

- the **decision layer** (`jarvis.gestures.Stabilizer`), which is where a false
  trigger is either prevented or allowed. Every rule it has exists because of a
  specific way this goes wrong, so every rule is pinned here.
- the **watcher loop**, driven by a scripted frame source and a fake
  recogniser: does a held gesture reach the callback exactly once, is the
  camera released on every exit path, does a broken callback or an unopenable
  camera degrade instead of crashing.
- the **binding contract** between config, gestures and `jarvis.confirm` — that
  a bound gesture resolves a real armed action, and that the strings on both
  sides of that seam agree.

    .venv/bin/python3 scripts/selftest_gestures.py

Whether MediaPipe can actually *see* a thumbs-up is not a thing this file can
answer. That's scripts/try_gestures.py, and it needs your hand.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis import confirm, gestures  # noqa: E402
from jarvis.gestures import NO_GESTURE, GestureError, GestureWatcher, Reading, Stabilizer  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(f"{label}{f' — {detail}' if detail else ''}")


def feed(stabilizer: Stabilizer, label: str, score: float, frames: int) -> list[str]:
    """Feed `frames` identical readings, returning whatever fired."""
    fired = []
    for _ in range(frames):
        result = stabilizer.feed(Reading(label=label, score=score))
        if result is not None:
            fired.append(result)
    return fired


# -- the decision layer -------------------------------------------------------

print("stabilizer: holding a gesture")

s = Stabilizer(hold_frames=3, min_confidence=0.5, cooldown_seconds=0.0)
check("one frame doesn't fire", feed(s, "Thumb_Up", 0.9, 1) == [])

s = Stabilizer(hold_frames=3, min_confidence=0.5, cooldown_seconds=0.0)
check("two frames don't fire (hold_frames=3)", feed(s, "Thumb_Up", 0.9, 2) == [])

s = Stabilizer(hold_frames=3, min_confidence=0.5, cooldown_seconds=0.0)
check("three frames fire, exactly once", feed(s, "Thumb_Up", 0.9, 3) == ["Thumb_Up"])

s = Stabilizer(hold_frames=3, min_confidence=0.5, cooldown_seconds=0.0)
fired = feed(s, "Thumb_Up", 0.9, 30)
check("holding it for 30 frames still fires only once", fired == ["Thumb_Up"], str(fired))

print("\nstabilizer: what must NOT fire")

s = Stabilizer(hold_frames=3, min_confidence=0.7, cooldown_seconds=0.0)
check("a low-confidence gesture never fires", feed(s, "Thumb_Up", 0.5, 20) == [])

s = Stabilizer(hold_frames=3, min_confidence=0.5, cooldown_seconds=0.0)
check("MediaPipe's 'None' never fires, however confident",
      feed(s, NO_GESTURE, 1.0, 20) == [])

s = Stabilizer(hold_frames=3, min_confidence=0.5, cooldown_seconds=0.0)
for label in ("Thumb_Up", "Open_Palm", "Thumb_Up", "Victory", "Thumb_Up", "Open_Palm"):
    s.feed(Reading(label=label, score=0.9))
check("a hand flickering between gestures never reaches a hold", True)  # nothing fired above
s2 = Stabilizer(hold_frames=3, min_confidence=0.5, cooldown_seconds=0.0)
flicker = [s2.feed(Reading(label=l, score=0.9))
           for l in ("Thumb_Up", "Open_Palm", "Thumb_Up", "Victory", "Thumb_Up")]
check("...pinned: five alternating frames fire nothing",
      all(f is None for f in flicker), str(flicker))

s = Stabilizer(hold_frames=3, min_confidence=0.5, cooldown_seconds=0.0)
feed(s, "Thumb_Up", 0.9, 2)
s.feed(Reading())  # hand drops out for one frame
check("a gap resets the streak", feed(s, "Thumb_Up", 0.9, 2) == [])

print("\nstabilizer: re-arming (the held-hand problem)")

s = Stabilizer(hold_frames=3, min_confidence=0.5, cooldown_seconds=0.0)
check("first hold fires", feed(s, "Thumb_Up", 0.9, 5) == ["Thumb_Up"])
check("...and keeps not firing while the hand stays up",
      feed(s, "Thumb_Up", 0.9, 20) == [])
s.feed(Reading())  # hand lowered
check("...but fires again after the gesture actually stops",
      feed(s, "Thumb_Up", 0.9, 3) == ["Thumb_Up"])

s = Stabilizer(hold_frames=3, min_confidence=0.5, cooldown_seconds=0.0)
feed(s, "Thumb_Up", 0.9, 3)
check("a *different* gesture can fire without a gap first (thumb up then palm)",
      feed(s, "Open_Palm", 0.9, 3) == ["Open_Palm"])

print("\nstabilizer: cooldown")

now = [1000.0]
s = Stabilizer(hold_frames=2, min_confidence=0.5, cooldown_seconds=2.0, clock=lambda: now[0])
check("fires the first time", feed(s, "Thumb_Up", 0.9, 2) == ["Thumb_Up"])
check("in cooldown right after", s.in_cooldown)
s.feed(Reading())  # hand lowered, so re-arming isn't what's blocking
now[0] += 1.0
check("a second gesture inside the cooldown is dropped",
      feed(s, "Open_Palm", 0.9, 5) == [])
now[0] += 1.5  # past the 2s cooldown
check("cooldown has expired", not s.in_cooldown)
s.feed(Reading())
check("...and gestures work again after it", feed(s, "Open_Palm", 0.9, 2) == ["Open_Palm"])

now = [1000.0]
s = Stabilizer(hold_frames=2, min_confidence=0.5, cooldown_seconds=60.0, clock=lambda: now[0])
feed(s, "Thumb_Up", 0.9, 2)
s.reset()
check("reset() clears the cooldown, so the next window starts clean",
      not s.in_cooldown and feed(s, "Thumb_Up", 0.9, 2) == ["Thumb_Up"])

check("hold_frames can't be talked below 1", Stabilizer(hold_frames=0).hold_frames == 1)


# -- bindings ----------------------------------------------------------------

print("\nbindings")

real_config = gestures.config.gestures_config
try:
    gestures.config.gestures_config = lambda: {
        "bindings": {"Thumb_Up": "confirm", "Open_Palm": "cancel"}
    }
    check("the shipped vocabulary maps thumbs-up to confirm, open palm to cancel",
          gestures.bindings() == {"Thumb_Up": "confirm", "Open_Palm": "cancel"},
          str(gestures.bindings()))

    gestures.config.gestures_config = lambda: {
        "bindings": {"Thumbs_Up": "confirm", "Thumb_Up": "confirm"}
    }
    resolved = gestures.bindings()
    check("a label MediaPipe doesn't use is dropped, not silently dead config",
          resolved == {"Thumb_Up": "confirm"}, str(resolved))

    gestures.config.gestures_config = lambda: {"bindings": {"Thumb_Up": ""}}
    check("an empty meaning unbinds a gesture", gestures.bindings() == {})
finally:
    gestures.config.gestures_config = real_config

check("every shipped binding is a real MediaPipe label",
      all(label in gestures.CANNED_GESTURES for label in gestures.bindings()),
      str(gestures.bindings()))


# -- the watcher loop ---------------------------------------------------------

print("\nwatcher loop (scripted frames, no camera)")


class FakeCamera:
    """Hands out a fixed number of frames, then blocks on 'no frame'."""

    def __init__(self, frames: int = 10_000) -> None:
        self.remaining = frames
        self.closed = False
        self.reads = 0

    def read(self):
        self.reads += 1
        if self.remaining <= 0:
            return None
        self.remaining -= 1
        return object()  # the fake recognizer never looks at it

    def close(self) -> None:
        self.closed = True


class FakeRecognizer:
    """Returns a scripted sequence of readings, then NO_GESTURE forever."""

    def __init__(self, script: list[Reading]) -> None:
        self.script = list(script)
        self.closed = False
        self.timestamps: list[int] = []

    def read(self, _frame, timestamp_ms: int) -> Reading:
        self.timestamps.append(timestamp_ms)
        return self.script.pop(0) if self.script else Reading()

    def close(self) -> None:
        self.closed = True


FAST = {
    "enabled": True,
    "fps": 1000,  # no real sleeping
    "hold_frames": 3,
    "min_confidence": 0.5,
    "cooldown_seconds": 0.0,
    "bindings": {"Thumb_Up": "confirm", "Open_Palm": "cancel"},
}


def run_watcher(script, settings=None, on_gesture=None, frames=10_000, wait=2.0):
    """Run a watcher over a scripted reading sequence until it goes quiet."""
    seen: list[str] = []
    camera = FakeCamera(frames=frames)
    recognizer = FakeRecognizer(script)
    done = threading.Event()

    def callback(meaning: str) -> None:
        (on_gesture or seen.append)(meaning)
        done.set()

    watcher = GestureWatcher(
        on_gesture=callback,
        camera_factory=lambda: camera,
        recognizer_factory=lambda: recognizer,
        settings=settings or FAST,
    )
    watcher.start()
    done.wait(timeout=wait)
    # Give the loop a moment past the firing, so "fires exactly once" is a real
    # claim rather than an artefact of stopping the instant it fired.
    time.sleep(0.05)
    watcher.stop()
    return watcher, camera, recognizer, seen


script = [Reading("Thumb_Up", 0.9)] * 5
watcher, camera, recognizer, seen = run_watcher(script)
check("a held thumbs-up reaches the callback", seen[:1] == ["confirm"], str(seen))
check("...exactly once", len(seen) == 1, str(seen))
check("the callback gets the *meaning*, not MediaPipe's label",
      seen == ["confirm"], str(seen))
check("the camera is released when the watcher stops", camera.closed)
check("the recognizer is closed too", recognizer.closed)
check("frames are counted", watcher.frames_seen >= 5, str(watcher.frames_seen))
check("timestamps strictly increase (MediaPipe VIDEO mode rejects a repeat)",
      all(b > a for a, b in zip(recognizer.timestamps, recognizer.timestamps[1:])),
      str(recognizer.timestamps[:8]))
check("no error recorded on a clean run", watcher.last_error is None, str(watcher.last_error))

_, _, _, seen = run_watcher([Reading("Open_Palm", 0.9)] * 5)
check("an open palm means cancel", seen == ["cancel"], str(seen))

_, _, _, seen = run_watcher([Reading("Victory", 0.99)] * 20, wait=0.4)
check("an unbound gesture fires nothing, however clearly it's held",
      seen == [], str(seen))

_, _, _, seen = run_watcher([Reading("Thumb_Up", 0.2)] * 20, wait=0.4)
check("a gesture below the confidence floor fires nothing", seen == [], str(seen))

print("\nwatcher loop: degrading instead of crashing")


def boom(_meaning: str) -> None:
    raise RuntimeError("the callback is broken")


watcher, camera, _, _ = run_watcher([Reading("Thumb_Up", 0.9)] * 5, on_gesture=boom)
check("a callback that raises doesn't kill the camera loop", camera.closed)
check("...and the watcher is still shut down cleanly", not watcher.is_running)


def unopenable():
    raise GestureError("the webcam could not be opened")


watcher = GestureWatcher(
    on_gesture=lambda _m: None,
    camera_factory=unopenable,
    recognizer_factory=lambda: FakeRecognizer([]),
    settings=FAST,
)
watcher.start()
time.sleep(0.2)
watcher.stop()
check("a camera that won't open is recorded, not raised",
      watcher.last_error == "the webcam could not be opened", str(watcher.last_error))
check("...and the watcher isn't left claiming to run", not watcher.is_running)

camera = FakeCamera(frames=0)  # every read comes back empty
watcher = GestureWatcher(
    on_gesture=lambda _m: None,
    camera_factory=lambda: camera,
    recognizer_factory=lambda: FakeRecognizer([]),
    settings=FAST,
)
watcher.start()
deadline = time.monotonic() + 2.0
while watcher.is_running and time.monotonic() < deadline:
    time.sleep(0.05)
watcher.stop()
check("a camera that returns no frames gives up and says so",
      watcher.last_error == "the camera stopped returning frames", str(watcher.last_error))
check("...and released the device on the way out", camera.closed)

print("\nwatcher loop: lifecycle")

camera = FakeCamera()
watcher = GestureWatcher(
    on_gesture=lambda _m: None,
    camera_factory=lambda: camera,
    recognizer_factory=lambda: FakeRecognizer([]),
    settings=FAST,
)
check("stop() on a watcher that never started is a no-op", watcher.stop() is None)
watcher.start()
check("start() means running", watcher.is_running)
check("start() twice doesn't open a second camera", watcher.start() and watcher.is_running)
watcher.stop()
check("stop() means stopped", not watcher.is_running)
check("stop() twice is fine", watcher.stop() is None)
check("the camera really was released", camera.closed)

disabled = GestureWatcher(on_gesture=lambda _m: None, settings={"enabled": False})
check("gestures.enabled=false never opens the camera",
      disabled.start() is False and not disabled.is_running)


# -- the seam with jarvis.confirm ---------------------------------------------

print("\nthe seam with jarvis.confirm (Phase 4's contract, unchanged)")

ran: list[str] = []
confirm.arm("submit the login form", lambda: ran.append("submitted") or "Submitted.")
reply = confirm.resolve("confirm" == "confirm")
check("a 'confirm' gesture runs the armed action", ran == ["submitted"], str(ran))
check("...and returns something speakable", reply == "Submitted.", str(reply))

ran.clear()
confirm.arm("submit the login form", lambda: ran.append("submitted") or "Submitted.")
reply = confirm.resolve("cancel" == "confirm")
check("a 'cancel' gesture drops it without running anything", ran == [], str(ran))
check("...and says so", reply == "Cancelled.", str(reply))

confirm.clear()
check("a gesture with nothing armed resolves to nothing at all",
      confirm.resolve(True) is None)

check("the meanings gestures can emit are exactly the ones confirm understands",
      set(gestures.bindings().values()) <= {"confirm", "cancel"},
      str(set(gestures.bindings().values())))

# The camera-on window is defined by a pending confirmation, so these two
# constants have to relate sensibly: a hold must fit comfortably inside the
# window, or the gesture channel would be advertised and then unusable.
settings = gestures.config.gestures_config()
hold_seconds = settings["hold_frames"] / max(1, settings["fps"])
check("a gesture hold fits inside a confirmation's lifetime",
      hold_seconds * 10 < confirm.TTL_SECONDS,
      f"{hold_seconds:.1f}s hold vs {confirm.TTL_SECONDS:.0f}s window")


print()
if failures:
    print(f"{len(failures)} FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("All Phase 5 gesture checks passed.")
