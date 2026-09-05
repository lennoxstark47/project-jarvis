"""
Push-to-talk voice capture — Phase 1.

Hold a global hotkey, speak, release it — the held-down window is recorded
from the mic and handed to `jarvis.stt.transcribe` the moment the key comes
back up. "Global" means it fires even while Jarvis (a menu-bar-only app with
no window ever in focus) isn't the frontmost app, which is the whole point of
a background voice assistant.

That global reach is also why this needs its own one-time macOS permission,
separate from the mic/camera TCC prompts Phase 0 already proved out: pynput's
system-wide key listener uses a Quartz event tap, which macOS gates behind the
**Accessibility** permission (System Settings -> Privacy & Security ->
Accessibility), not Input Monitoring - confirmed via pynput's own runtime
warning ("This process is not trusted! ... accessibility clients") the first
time this ran as the packaged .app. Granted to whatever the running binary
actually is, same identity story as Phase 0's mic/camera grants, so it needs
granting again for the built .app if it wasn't already, and the process needs
restarting after granting it (the event tap only succeeds at creation time -
granting the permission to an already-running process doesn't retroactively
fix its listener).

Recording and transcription both run off the hotkey-listener thread (via a
short-lived sounddevice stream + a dedicated transcription thread) so holding
the key never blocks the listener from noticing the release, and a slow
transcription never blocks the next press.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np

logger = logging.getLogger("jarvis.voice")

SAMPLE_RATE = 16000

# Held-down-to-talk key. F9 was picked because it's unused by both macOS and
# the apps this project cares about (browsers, terminals, IDEs) — revisit if
# that turns out not to hold on your setup.
HOTKEY_NAME = "f9"


class PushToTalk:
    def __init__(self, on_transcript: Callable[[str], None]) -> None:
        self._on_transcript = on_transcript
        self._hotkey = None  # resolved in start(), needs pynput imported first
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._stream = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start listening for the hotkey. Non-blocking - runs its own thread."""
        from pynput import keyboard

        self._hotkey = getattr(keyboard.Key, HOTKEY_NAME)
        listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        listener.daemon = True
        listener.start()
        logger.info("Push-to-talk listener started (hold %s to talk).", HOTKEY_NAME.upper())

    # -- key events -----------------------------------------------------------

    def _on_press(self, key) -> None:
        if key != self._hotkey:
            return
        with self._lock:
            if self._recording:
                return  # OS key-repeat while held down - not a new press
            self._recording = True
            self._frames = []
        self._start_stream()
        logger.info("Recording (F9 held)...")

    def _on_release(self, key) -> None:
        if key != self._hotkey:
            return
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        self._stop_stream()
        logger.info("Recording stopped - transcribing...")
        threading.Thread(target=self._transcribe_and_report, daemon=True).start()

    # -- audio capture ---------------------------------------------------------

    def _start_stream(self) -> None:
        import sounddevice as sd

        def callback(indata, _frames, _time_info, status) -> None:
            if status:
                logger.warning("audio input status: %s", status)
            with self._lock:
                if self._recording:
                    self._frames.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback
        )
        self._stream.start()

    def _stop_stream(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _transcribe_and_report(self) -> None:
        with self._lock:
            frames, self._frames = self._frames, []
        if not frames:
            logger.info("no audio captured - nothing to transcribe")
            return

        from jarvis.stt import transcribe

        audio = np.concatenate(frames, axis=0).reshape(-1)
        text = transcribe(audio, samplerate=SAMPLE_RATE)
        logger.info("transcript: %s", text or "(empty)")
        self._on_transcript(text)
