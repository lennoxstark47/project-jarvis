"""
Permission-check helpers — Phase 0.

macOS gates microphone and camera access behind a per-app TCC (Transparency,
Consent, and Control) prompt. These functions actually *use* the device
briefly rather than just checking an entitlement flag, because opening the
device is what triggers the OS permission prompt the first time, and it's the
only reliable way to confirm access genuinely works end-to-end (a device can
exist and still not be usable if permission was denied).

Both functions log and return False on any failure instead of raising — Phase 0
just needs an honest "mic: ready" / "camera: ready" or "NOT ready", not a
crashed menu bar app.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("jarvis.permissions")


def check_microphone(duration_seconds: float = 0.3, samplerate: int = 16000) -> bool:
    """Open a short mic input stream to confirm microphone access works."""
    try:
        import sounddevice as sd

        sd.rec(int(duration_seconds * samplerate), samplerate=samplerate, channels=1)
        sd.wait()
        logger.info("mic: ready")
        return True
    except Exception as exc:  # noqa: BLE001 - broad on purpose, this is a health check
        logger.error("mic: NOT ready (%s)", exc)
        return False


def check_camera(camera_index: int = 0, warmup_attempts: int = 5) -> bool:
    """Open the webcam briefly and grab a frame to confirm camera access works.

    The first read or two right after opening the device can come back empty
    while the camera warms up (observed in testing, not just theoretical), so
    this retries a few times before concluding access genuinely doesn't work
    — a single failed frame isn't reliable enough to report "NOT ready" on.
    """
    try:
        import cv2

        cap = cv2.VideoCapture(camera_index)
        try:
            if not cap.isOpened():
                raise RuntimeError("camera device could not be opened")
            ok = False
            for _attempt in range(warmup_attempts):
                ok, _frame = cap.read()
                if ok:
                    break
            if not ok:
                raise RuntimeError("camera opened but no frame could be read")
        finally:
            cap.release()
        logger.info("camera: ready")
        return True
    except Exception as exc:  # noqa: BLE001 - broad on purpose, this is a health check
        logger.error("camera: NOT ready (%s)", exc)
        return False
