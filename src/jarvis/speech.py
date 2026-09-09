"""
Text-to-speech — Phase 4. Doc 02's output layer, Jarvis's half of the
conversation.

Same shape as `jarvis.router`, for the same reason: one thin interface, one
implementation per provider, and *which* one is a `config/jarvis.json` setting
rather than a code change. Doc 01 asks for exactly this — "start local ... with
a cloud option ... behind the same model-agnostic pattern as the brain, so you
can compare quality later" — and the pattern is cheap enough here that not doing
it would only save a dozen lines.

- **say** (default) — macOS's built-in synthesiser, which is `AVSpeechSynthesizer`
  under a CLI. Free, offline, installed, and instant to start. Pick a nicer voice
  in System Settings → Accessibility → Spoken Content, then name it in config.
- **piper** — a local neural TTS, much more natural than `say`, at the cost of
  installing it and downloading a voice model. Still fully offline.
- **openai** — cloud, best quality, costs money per utterance and sends the text
  of Jarvis's replies to a third party. Never used by default, and worth knowing
  that a reply can quote a page Jarvis is looking at.

Everything here is spoken on the caller's thread and serialised behind one lock,
with a new utterance **interrupting** the one in progress: when you talk over
Jarvis, the answer to what you just said matters more than the end of the last
sentence. The alternative — queueing — makes it monologue at you.

Failures are logged and swallowed. A voice assistant that can't speak is
degraded; one that crashes because a voice model is missing is broken, and the
menu bar still shows every reply either way.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from jarvis import config

logger = logging.getLogger("jarvis.speech")

# Beyond this, a "reply" is something that went wrong (a model narrating, a tool
# result pasted verbatim) and listening to it read out is worse than not.
MAX_SPOKEN_CHARS = 600

# macOS's audio file player, used by every backend that produces a file rather
# than playing the audio itself.
PLAYER = "afplay"


class SpeechError(RuntimeError):
    """A voice couldn't be built or couldn't speak."""


class Voice(Protocol):
    """What every TTS backend must implement. That's the whole contract."""

    name: str

    def speak(self, text: str, process_sink: Any) -> None: ...


def _settings(backend: str) -> dict[str, Any]:
    return config.speech_config().get("backends", {}).get(backend, {})


def _run(argv: list[str], process_sink: Any, *, timeout: float = 120.0) -> None:
    """Run a synth/playback command, publishing it so `stop()` can kill it."""
    process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    process_sink(process)
    _, stderr = process.communicate(timeout=timeout)
    # Return code 15/-15 is this process being deliberately interrupted by a
    # newer utterance, which is normal operation, not a failure.
    if process.returncode not in (0, -15, 15, None):
        detail = (stderr or b"").decode(errors="replace").strip()
        raise SpeechError(detail or f"{argv[0]} exited with {process.returncode}")


class SayVoice:
    """macOS `say`. Always available, no setup, no network, no cost."""

    name = "say"

    def __init__(self) -> None:
        settings = _settings("say")
        self.voice = settings.get("voice")
        self.rate = settings.get("rate")

    def speak(self, text: str, process_sink: Any) -> None:
        argv = ["say"]
        if self.rate:
            argv += ["-r", str(self.rate)]
        # "--" so a reply that starts with a dash is spoken, not parsed as a flag.
        if self.voice:
            try:
                _run([*argv, "-v", str(self.voice), "--", text], process_sink)
                return
            except SpeechError as exc:
                # A voice that isn't installed on this Mac (or was removed in a
                # macOS update) must degrade to *some* voice rather than to
                # silence — a mute assistant looks like a hung one.
                logger.warning("the %r voice didn't work (%s) — using the system voice",
                               self.voice, exc)
        _run([*argv, "--", text], process_sink)


class PiperVoice:
    """Local neural TTS. Offline like `say`, but a voice you'd want to listen to.

    Install: `pip install piper-tts`, then download a voice (.onnx plus its
    .onnx.json) from the piper voices release and point `model` at it.
    """

    name = "piper"

    def __init__(self) -> None:
        settings = _settings("piper")
        self.binary = settings.get("binary") or "piper"
        self.model = settings.get("model")
        self.length_scale = settings.get("length_scale")
        if shutil.which(self.binary) is None:
            raise SpeechError(
                f"the piper binary {self.binary!r} isn't on PATH — install it "
                "(`pip install piper-tts`) or set speech.backends.piper.binary "
                "to its full path"
            )
        if not self.model:
            raise SpeechError(
                "no piper voice model configured — download a .onnx voice and set "
                "speech.backends.piper.model to its path"
            )
        if not Path(self.model).expanduser().exists():
            raise SpeechError(f"the piper voice model {self.model} doesn't exist")

    def speak(self, text: str, process_sink: Any) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as wav:
            argv = [self.binary, "--model", str(Path(self.model).expanduser()),
                    "--output_file", wav.name]
            if self.length_scale:
                argv += ["--length_scale", str(self.length_scale)]
            process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
            process_sink(process)
            _, stderr = process.communicate(input=text.encode(), timeout=120)
            if process.returncode not in (0, -15, 15):
                detail = (stderr or b"").decode(errors="replace").strip()
                raise SpeechError(detail or f"piper exited with {process.returncode}")
            _run([PLAYER, wav.name], process_sink)


class OpenAIVoice:
    """OpenAI TTS. The cloud option — best quality, per-utterance cost."""

    name = "openai"

    def __init__(self) -> None:
        settings = _settings("openai")
        self.model = settings.get("model") or "gpt-4o-mini-tts"
        self.voice = settings.get("voice") or "alloy"
        self.timeout = float(settings.get("timeout", 30))
        self.api_key = config.api_key("openai")
        if not self.api_key:
            raise SpeechError(
                "no OpenAI API key — set OPENAI_API_KEY or add it to secrets/api_keys.json"
            )

    def speak(self, text: str, process_sink: Any) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise SpeechError("the `openai` package isn't installed") from exc

        client = OpenAI(api_key=self.api_key, timeout=self.timeout)
        try:
            response = client.audio.speech.create(
                model=self.model, voice=self.voice, input=text
            )
            audio = response.read()
        except Exception as exc:  # noqa: BLE001 - vendor SDK, any failure is the same to us
            raise SpeechError(f"OpenAI TTS failed: {exc}") from exc

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as mp3:
            mp3.write(audio)
            mp3.flush()
            _run([PLAYER, mp3.name], process_sink)


VOICES = {"say": SayVoice, "piper": PiperVoice, "openai": OpenAIVoice}
VOICE_NAMES = tuple(VOICES)

_lock = threading.Lock()
_current: subprocess.Popen | None = None
_cached: Voice | None = None
_cached_name: str = ""


def get_voice(name: str | None = None) -> Voice:
    """Build the named voice (default: whichever `config/jarvis.json` names).

    Cached by name, so a backend with real setup cost isn't rebuilt per reply,
    and re-resolved the moment you change the config — same live-config
    behaviour as the action layer.
    """
    global _cached, _cached_name

    name = (name or config.speech_config().get("backend") or "say").lower()
    if _cached is not None and _cached_name == name:
        return _cached

    factory = VOICES.get(name)
    if factory is None:
        raise SpeechError(f"unknown voice {name!r}. Available: {', '.join(VOICE_NAMES)}.")
    voice = factory()
    _cached, _cached_name = voice, name
    return voice


def enabled() -> bool:
    return bool(config.speech_config().get("enabled", True))


def stop() -> None:
    """Cut off whatever is being spoken right now. Safe to call at any time."""
    global _current
    with _lock:
        process, _current = _current, None
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except OSError as exc:  # pragma: no cover - already gone
            logger.debug("couldn't stop the voice: %s", exc)


def speak(text: str, *, voice: str | None = None) -> bool:
    """Say `text` out loud. Blocks until it's said. Never raises.

    Returns whether anything was actually spoken, which is what the self-test
    checks — the menu bar shows the reply regardless, so a silent failure here
    must not be silent in the log.
    """
    text = " ".join((text or "").split())
    if not text:
        return False
    if not enabled():
        logger.debug("speech is disabled in config — not speaking")
        return False
    if len(text) > MAX_SPOKEN_CHARS:
        logger.info("truncating a %d-character reply for speech", len(text))
        text = text[:MAX_SPOKEN_CHARS].rsplit(" ", 1)[0] + "..."

    stop()  # interrupt the previous utterance; the newest reply wins

    def sink(process: subprocess.Popen) -> None:
        global _current
        with _lock:
            _current = process

    try:
        started = time.monotonic()
        spoken_by = get_voice(voice)
        spoken_by.speak(text, sink)
        # Logged because "did Jarvis actually say that?" is otherwise invisible
        # in the log — a silent failure and a working reply look identical.
        logger.info("spoke %d chars via %s in %.1fs", len(text), spoken_by.name,
                    time.monotonic() - started)
        return True
    except SpeechError as exc:
        logger.error("couldn't speak: %s", exc)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("the voice failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 - speaking must never end a turn
        logger.error("unexpected speech failure: %s", exc)
    return False
