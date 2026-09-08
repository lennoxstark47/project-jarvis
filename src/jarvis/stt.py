"""
Speech-to-text — Phase 1.

Wraps `faster-whisper` (a CTranslate2 reimplementation of OpenAI Whisper) so
transcription runs fully offline and locally: voice commands routinely contain
credentials, project paths, and other things that don't need to leave this
machine just to get transcribed (see docs/02-SYSTEM_DESIGN.md, "Perception
layer").

The model is loaded lazily and cached at module level — loading it takes a
noticeable moment (and, the very first time only, needs internet access to
download the model weights from Hugging Face Hub), so we pay that cost once
per process rather than once per utterance.
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("jarvis.stt")

# "base.en" is the sweet spot for push-to-talk-length utterances on CPU: small
# enough to transcribe a short sentence in ~1-2s (this phase's Definition of
# done), accurate enough for command-style speech. Revisit if accuracy turns
# out to matter more than latency once real commands are being parsed.
MODEL_SIZE = "base.en"

# Whisper accepts a hint of what the audio is likely to contain, which biases
# decoding towards those words. Every command to Jarvis is drawn from a tiny
# vocabulary, and the words it kept getting wrong were the ones that matter
# most: "Claude Code" came back as "CLOT code", "slot code" and "plot code"
# across three consecutive utterances (2026-09-08), each of which changes what
# the request means. Names go here; ordinary English doesn't need the help.
INITIAL_PROMPT = (
    "Jarvis, Claude Code, project jarvis, GitHub, Playwright, Whisper, "
    "open the portal, run Claude Code on my project, it's in Documents."
)

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        logger.info("Loading faster-whisper model %r (first run may download it)...", MODEL_SIZE)
        _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
        logger.info("faster-whisper model loaded.")
    return _model


def transcribe(audio: np.ndarray, samplerate: int = 16000) -> str:
    """Transcribe a mono float32 waveform at `samplerate` Hz to text.

    Returns an empty string (rather than raising) if nothing usable came back
    — an empty push-to-talk recording or a transcription failure shouldn't
    crash the app, just produce nothing to show.
    """
    if audio.size == 0:
        return ""
    try:
        model = _get_model()
        segments, _info = model.transcribe(
            audio,
            language="en",
            # Live-tested 2026-09-05: a short, mostly-silent push-to-talk clip
            # (leading/trailing silence from reaction time around the actual
            # speech) came back as "The The The The The The The" and took
            # ~5s to decode - a known Whisper failure mode on quiet/short
            # audio. vad_filter trims the silence Silero's VAD detects before
            # decoding, which fixes both the repetition and the latency it
            # caused. condition_on_previous_text=False because each push-to-
            # talk recording is a standalone utterance, not a continuous
            # stream - conditioning on a (nonexistent) previous segment is
            # itself a repetition-loop contributor.
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=INITIAL_PROMPT,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text
    except Exception as exc:  # noqa: BLE001 - broad on purpose, this must never crash the app
        logger.error("transcription failed (%s)", exc)
        return ""
