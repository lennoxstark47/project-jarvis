"""
Hand gestures — Phase 5.

The webcam as a second input channel, alongside the microphone. doc 02 draws it
as `CAM -> GESTURE -> LOOP`, and the important half of that picture is what it
*doesn't* show: no separate path per modality. A gesture doesn't get its own
tool, its own prompt or its own branch in the agent loop — it resolves the same
pending action a spoken "yes" resolves, through `jarvis.confirm`, which Phase 4
built and proved for exactly this reason. So this module's whole job is
perception: camera frames in, one stable gesture out. What that gesture *means*
is `main.py`'s two-line binding, and what it *does* is Phase 4's code.

Three decisions worth knowing:

**MediaPipe's canned gesture classifier, not hand-rolled geometry.** This uses
the Tasks API's `GestureRecognizer`, whose bundled model already recognises
exactly the vocabulary the phase plan asked for (`Thumb_Up`, `Open_Palm`,
`Closed_Fist`, `Pointing_Up`, `Thumb_Down`, `Victory`, `ILoveYou`). Classifying
a thumbs-up from 21 landmarks by hand is a rotation-and-camera-angle problem
that Google has already solved with a trained model and, unlike geometry, it
reports a confidence — which is what the stability layer below needs to work
with.

**Pin mediapipe to the 0.10 line on macOS.** 1.0.1 cannot run *any* hand graph
in Python on macOS/arm64: both `GestureRecognizer` and `HandLandmarker` abort
the whole process inside `TensorsToDetectionsCalculator::Open()` with
`graph_service.h:139 Check failed: service_ Service is unavailable` (a Metal
helper the CPU graph never registers — measured 2026-09-09, and asking for
`BaseOptions.Delegate.CPU` explicitly does not avoid it). That's an abort, not
an exception, so it takes Jarvis with it and no try/except helps. 0.10.35 runs
the same code correctly. requirements.txt carries the pin and the reason.

**A gesture firing is decided here, locally, frame by frame — never by a model
call.** Same rule the rest of Jarvis runs on: what the user did themselves is
resolved on this machine. It also means a thumbs-up still works when the brain
is unreachable, exactly as the spoken "no, cancel" does (see jarvis.confirm).

**The camera is open only while something is waiting to be confirmed.** doc 02:
"opened only while gesture mode is active — don't leave the camera hot all the
time, both for battery and for the obvious trust reasons." `main.py` starts this
watcher when a confirmation is armed and stops it when one is answered, so the
camera light is on for a bounded window that always corresponds to a question
Jarvis just asked out loud. That is also, conveniently, most of the phase's
"no false triggers" requirement: for the majority of a session there is nothing
armed, so there is nothing a gesture could trigger even in principle.

The model file (~8 MB) is downloaded on first use into `memory/models/`, the
same way Phase 1's Whisper weights are fetched on the first utterance rather
than being vendored or made a manual install step.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jarvis import config

# Expect a handful of stderr lines from MediaPipe's C++ half every time a
# recogniser is built ("Feedback manager requires a model with a single
# signature inference", "Using NORM_RECT without IMAGE_DIMENSIONS", a GL
# version banner). They are harmless, and as far as this version goes,
# unsuppressable from Python: they come from absl's C++ logger, which ignores
# both `GLOG_minloglevel` (tried at 0/2/3 — identical output) and
# `absl.logging.set_verbosity` (that only governs absl's *Python* side).
# Noted so nobody spends an afternoon on it twice, and so the lines aren't read
# as a fault: none of them reaches logs/jarvis.log.

logger = logging.getLogger("jarvis.gestures")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = PROJECT_ROOT / "memory" / "models"
MODEL_PATH = MODEL_DIR / "gesture_recognizer.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/1/gesture_recognizer.task"
)

# What MediaPipe's canned classifier can return. Listed so a binding typo in
# config/jarvis.json is caught and named at startup rather than silently
# meaning "this gesture never fires".
CANNED_GESTURES = (
    "None",
    "Closed_Fist",
    "Open_Palm",
    "Pointing_Up",
    "Thumb_Down",
    "Thumb_Up",
    "Victory",
    "ILoveYou",
)

# MediaPipe's label for "a hand is visible but it isn't doing any of the above".
NO_GESTURE = "None"


class GestureError(RuntimeError):
    """The camera or the recogniser couldn't be had.

    Raised inside the watcher thread and logged there — a webcam that won't
    open is a degraded Jarvis (voice still works), never a crashed one, which
    is the same posture jarvis.speech takes when a voice is missing.
    """


@dataclass(frozen=True)
class Reading:
    """One frame's verdict: which gesture, and how sure.

    `label` is `NO_GESTURE` both when no hand is in frame and when a hand is
    doing nothing recognised — the stability layer treats those identically
    (either way, the gesture it was counting has stopped).
    """

    label: str = NO_GESTURE
    score: float = 0.0


# -- the decision layer -------------------------------------------------------
#
# Split out from the camera loop deliberately: this is where a false trigger is
# prevented or allowed, and it's pure logic over a stream of readings, so it can
# be tested exhaustively offline with no camera, no model file and no hand
# (scripts/selftest_gestures.py). The perception half below can then be checked
# by eye, which is the only way to check perception anyway.


class Stabilizer:
    """Turns a noisy per-frame gesture stream into at most one firing.

    Four rules, each earning its place against a specific way this goes wrong:

    1. **A confidence floor.** The classifier reports something for almost any
       hand shape; a half-formed gesture mid-movement scores low.
    2. **The same gesture on N consecutive frames.** A single frame is noise —
       a hand passing through a thumbs-up shape on its way to scratching your
       nose hits one or two frames, not a deliberate hold.
    3. **Re-arming: after firing, the gesture must actually stop** before the
       same one can fire again. Without this, holding your thumb up fires once
       per `hold_frames` for as long as you hold it, which for a gesture bound
       to "submit this login form" is the worst available behaviour.
    4. **A cooldown.** Covers the seconds right after a firing, when your hand
       is on its way down through other shapes and something else is armed.

    `clock` is injectable so the cooldown can be tested without sleeping.
    """

    def __init__(
        self,
        *,
        hold_frames: int = 6,
        min_confidence: float = 0.5,
        cooldown_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hold_frames = max(1, int(hold_frames))
        self.min_confidence = float(min_confidence)
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._clock = clock
        self._streak_label = NO_GESTURE
        self._streak = 0
        self._fired_label: str | None = None
        self._fired_at = 0.0

    @property
    def in_cooldown(self) -> bool:
        if self._fired_at == 0.0:
            return False
        return (self._clock() - self._fired_at) < self.cooldown_seconds

    def reset(self) -> None:
        """Forget everything — including the cooldown.

        Called when the watcher stops, so a firing at the end of one
        confirmation window can't suppress a deliberate gesture at the start of
        the next one, minutes later.
        """
        self._streak_label = NO_GESTURE
        self._streak = 0
        self._fired_label = None
        self._fired_at = 0.0

    def feed(self, reading: Reading) -> str | None:
        """Feed one frame. Returns the gesture label if this frame fires it."""
        confident = reading.label != NO_GESTURE and reading.score >= self.min_confidence

        if not confident:
            # The hold is broken, and whatever fired last is now re-armed:
            # the hand has left that shape, so the *next* one is deliberate.
            self._streak_label = NO_GESTURE
            self._streak = 0
            self._fired_label = None
            return None

        if reading.label == self._streak_label:
            self._streak += 1
        else:
            self._streak_label = reading.label
            self._streak = 1

        if self._streak < self.hold_frames:
            return None
        if self._fired_label == reading.label:
            return None  # still the same held gesture that already fired
        if self.in_cooldown:
            return None

        self._fired_label = reading.label
        self._fired_at = self._clock()
        return reading.label


def bindings() -> dict[str, str]:
    """Gesture label -> what it means ("confirm" / "cancel"), from config.

    Unknown labels are dropped with a warning rather than kept: a binding for
    `Thumbs_Up` (which isn't what MediaPipe calls it) would otherwise sit in
    the config file looking correct and never fire.
    """
    raw = config.gestures_config().get("bindings", {}) or {}
    resolved: dict[str, str] = {}
    for label, meaning in raw.items():
        if label not in CANNED_GESTURES:
            logger.warning(
                "ignoring gesture binding %r: not one of MediaPipe's labels (%s)",
                label,
                ", ".join(CANNED_GESTURES),
            )
            continue
        if not meaning:
            continue
        resolved[label] = str(meaning)
    return resolved


# -- the perception layer -----------------------------------------------------


def ensure_model(path: Path = MODEL_PATH, url: str = MODEL_URL) -> Path:
    """Return the gesture model's path, downloading it once if it's missing.

    ~8 MB from Google's model storage, cached under `memory/models/` (which is
    gitignored). Downloaded on first use rather than made a manual install
    step, matching Phase 1's Whisper weights — the alternative is a Jarvis that
    starts fine and then can't see, which is a worse failure than a slow first
    confirmation.
    """
    if path.exists() and path.stat().st_size > 0:
        return path

    import httpx

    logger.info("Downloading the gesture model (~8 MB) to %s ...", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        # Written to a .part file and renamed, so an interrupted download can
        # never leave a truncated model that "exists" and then fails to load.
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001 - network/disk, reported not raised as-is
        tmp.unlink(missing_ok=True)
        raise GestureError(f"could not download the gesture model: {exc}") from exc
    logger.info("Gesture model ready (%.1f MB).", path.stat().st_size / 1e6)
    return path


class MediaPipeRecognizer:
    """MediaPipe's `GestureRecognizer` behind a two-method interface.

    Kept this thin on purpose: `read(frame) -> Reading` and `close()` is the
    whole surface the watcher uses, so the watcher can be driven by a fake in
    the offline self-test, and swapping the classifier later (a custom-trained
    gesture, say) touches this class only.
    """

    def __init__(
        self,
        *,
        num_hands: int = 1,
        min_detection_confidence: float = 0.5,
        model_path: Path | None = None,
    ) -> None:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        path = model_path or ensure_model()
        options = vision.GestureRecognizerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(path)),
            # VIDEO rather than LIVE_STREAM: the watcher pulls frames on its own
            # thread at its own pace and wants the answer for the frame it just
            # read. LIVE_STREAM's callback would hand results back out of band,
            # buying asynchrony this loop has no use for.
            running_mode=vision.RunningMode.VIDEO,
            # One hand. Two hands in frame doing different things is an
            # ambiguity with no right answer, and the vocabulary is deliberately
            # small (see the phase plan: "resist the urge to support many
            # gestures before the few you have are reliable").
            num_hands=num_hands,
            min_hand_detection_confidence=min_detection_confidence,
        )
        self._recognizer = vision.GestureRecognizer.create_from_options(options)

    def read(self, frame, timestamp_ms: int) -> Reading:
        """Classify one BGR frame (as OpenCV hands them over)."""
        import cv2

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._recognizer.recognize_for_video(image, timestamp_ms)
        gestures = getattr(result, "gestures", None) or []
        if not gestures or not gestures[0]:
            return Reading()
        top = gestures[0][0]
        return Reading(label=top.category_name, score=float(top.score))

    def close(self) -> None:
        try:
            self._recognizer.close()
        except Exception as exc:  # noqa: BLE001 - closing must not raise
            logger.debug("closing the gesture recognizer: %s", exc)


class OpenCVCamera:
    """The webcam behind the same minimal interface, for the same reason."""

    def __init__(self, index: int = 0, width: int | None = 640, height: int | None = 480) -> None:
        import cv2

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            self._cap.release()
            raise GestureError(
                f"the webcam (index {index}) could not be opened — it may be in "
                "use by another app, or camera access may not be granted"
            )
        # Requested, not guaranteed: a camera picks the nearest mode it has.
        # Worth asking for anyway — this Mac's webcam opens at 1920x1080, and
        # moving 2 megapixels per frame is pure waste for a model that scales
        # the image down itself.
        if width:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        if height:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))

    def read(self):
        """Return a frame, or None if this read failed."""
        ok, frame = self._cap.read()
        return frame if ok else None

    def close(self) -> None:
        try:
            self._cap.release()
        except Exception as exc:  # noqa: BLE001 - releasing must not raise
            logger.debug("releasing the camera: %s", exc)


class GestureWatcher:
    """Watches the webcam and calls `on_gesture(meaning)` for a held gesture.

    `meaning` is what the binding says ("confirm" / "cancel"), not the
    MediaPipe label — the caller shouldn't have to know that a thumbs-up is
    spelled `Thumb_Up`.

    Start and stop are cheap and idempotent, because `main.py` calls them from
    a UI timer as confirmations come and go. The camera is opened on the
    watcher's own thread (opening it takes about a second) and released on
    every exit path, including a failure: doc 02's "don't leave the camera hot"
    is a property of this class, not of the code that uses it.

    `camera_factory` / `recognizer_factory` exist so the offline self-test can
    drive the whole loop with a scripted frame source, no webcam involved.
    """

    def __init__(
        self,
        on_gesture: Callable[[str], None],
        *,
        camera_factory: Callable[[], object] | None = None,
        recognizer_factory: Callable[[], object] | None = None,
        settings: dict | None = None,
    ) -> None:
        self.on_gesture = on_gesture
        self._settings = settings
        self._camera_factory = camera_factory
        self._recognizer_factory = recognizer_factory
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stabilizer: Stabilizer | None = None
        # Last thing that happened, for the menu bar and the log. A gesture
        # channel that silently isn't working looks identical to one nobody is
        # gesturing at, which is the Phase 3 status-line lesson again.
        self.last_error: str | None = None
        self.frames_seen = 0

    # -- lifecycle ----------------------------------------------------------

    @property
    def settings(self) -> dict:
        # Read fresh unless one was injected, so editing config/jarvis.json
        # takes effect on the next confirmation rather than the next restart
        # (same as config.actions_config).
        return self._settings if self._settings is not None else config.gestures_config()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Open the camera and start watching. Returns whether it's now running."""
        settings = self.settings
        if not settings.get("enabled", True):
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop.clear()
            self.last_error = None
            self.frames_seen = 0
            self._thread = threading.Thread(
                target=self._run, name="jarvis-gestures", daemon=True
            )
            self._thread.start()
        logger.info("gesture watching started (camera on)")
        return True

    def stop(self, timeout: float = 3.0) -> None:
        """Stop watching and release the camera. Safe to call when not running."""
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning("the gesture thread didn't stop within %.0fs", timeout)
        else:
            logger.info("gesture watching stopped (camera off) after %d frames", self.frames_seen)

    def __enter__(self) -> GestureWatcher:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def warm_up(self) -> bool:
        """Build a recogniser once and throw it away, to pay its cost early.

        Measured on this Mac (2026-09-09): building the first recogniser in a
        process takes **0.97s**, every one after it 0.02s — TFLite caches the
        loaded model process-wide, so one throwaway is enough. Opening the
        camera is the other 2.3s and is *not* pre-payable, but it doesn't need
        to be: the watcher starts the moment a confirmation is armed, which is
        before Jarvis has finished speaking the question out loud (1.9-4.8s,
        Phase 4's numbers), and nobody answers a question they haven't heard
        yet. So the camera warms up inside the sentence asking about it.

        Called from a startup timer in main.py, on a background thread, and
        deliberately quiet about failure: a webcam or model problem should
        surface when gestures are actually used, in the menu bar, not as an
        error at launch about a feature nobody has invoked.
        """
        if not self.settings.get("enabled", True):
            return False
        try:
            recognizer = self._build_recognizer()
        except Exception as exc:  # noqa: BLE001 - a warm-up failure is not an outage
            logger.info("gesture warm-up skipped: %s", exc)
            return False
        close = getattr(recognizer, "close", None)
        if close is not None:
            close()
        logger.info("gesture model warmed up")
        return True

    # -- the loop -----------------------------------------------------------

    def _build_camera(self):
        if self._camera_factory is not None:
            return self._camera_factory()
        settings = self.settings
        return OpenCVCamera(
            index=int(settings.get("camera_index", 0)),
            width=settings.get("frame_width", 640),
            height=settings.get("frame_height", 480),
        )

    def _build_recognizer(self):
        if self._recognizer_factory is not None:
            return self._recognizer_factory()
        settings = self.settings
        return MediaPipeRecognizer(
            num_hands=int(settings.get("num_hands", 1)),
            min_detection_confidence=float(settings.get("min_detection_confidence", 0.5)),
        )

    def _run(self) -> None:
        settings = self.settings
        fps = float(settings.get("fps", 10)) or 10.0
        interval = 1.0 / max(1.0, fps)
        active = set(bindings())

        self._stabilizer = Stabilizer(
            hold_frames=settings.get("hold_frames", 6),
            min_confidence=settings.get("min_confidence", 0.7),
            cooldown_seconds=settings.get("cooldown_seconds", 2.0),
        )

        camera = recognizer = None
        try:
            camera = self._build_camera()
            recognizer = self._build_recognizer()
        except GestureError as exc:
            self.last_error = str(exc)
            logger.error("gestures unavailable: %s", exc)
            self._teardown(camera, recognizer)
            return
        except Exception as exc:  # noqa: BLE001 - a missing dep or model, same posture
            self.last_error = str(exc)
            logger.error("gestures unavailable: %s", exc)
            self._teardown(camera, recognizer)
            return

        # MediaPipe's VIDEO mode requires strictly increasing timestamps and
        # rejects a repeat, so this counts frames rather than reading a clock —
        # two frames grabbed inside the same millisecond would otherwise throw.
        timestamp_ms = 0
        step_ms = max(1, int(interval * 1000))
        consecutive_bad_reads = 0

        try:
            while not self._stop.is_set():
                started = time.monotonic()
                frame = camera.read()
                if frame is None:
                    consecutive_bad_reads += 1
                    # A handful of empty reads is the camera warming up (see
                    # permissions.check_camera, which found the same thing);
                    # a long run of them means it's gone.
                    if consecutive_bad_reads >= 30:
                        self.last_error = "the camera stopped returning frames"
                        logger.error("gestures stopping: %s", self.last_error)
                        break
                    self._sleep_remaining(started, interval)
                    continue
                consecutive_bad_reads = 0
                self.frames_seen += 1

                timestamp_ms += step_ms
                try:
                    reading = recognizer.read(frame, timestamp_ms)
                except Exception as exc:  # noqa: BLE001 - one bad frame isn't fatal
                    logger.debug("gesture recognition failed on a frame: %s", exc)
                    self._sleep_remaining(started, interval)
                    continue

                fired = self._stabilizer.feed(reading)
                if fired is not None:
                    meaning = bindings().get(fired)
                    logger.info(
                        "gesture: %s (%.2f) -> %s",
                        fired,
                        reading.score,
                        meaning or "no binding, ignored",
                    )
                    if meaning and fired in active:
                        self._fire(meaning)

                self._sleep_remaining(started, interval)
        finally:
            self._teardown(camera, recognizer)
            if self._stabilizer is not None:
                self._stabilizer.reset()

    @staticmethod
    def _sleep_remaining(started: float, interval: float) -> None:
        remaining = interval - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)

    def _fire(self, meaning: str) -> None:
        try:
            self.on_gesture(meaning)
        except Exception as exc:  # noqa: BLE001 - a bad callback can't kill the camera loop
            logger.error("the gesture callback failed: %s", exc)

    def _teardown(self, camera, recognizer) -> None:
        for resource in (recognizer, camera):
            if resource is None:
                continue
            close = getattr(resource, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("closing %s: %s", type(resource).__name__, exc)
