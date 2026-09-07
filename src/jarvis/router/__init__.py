"""
Model Router — Phase 2.

`get_backend("claude" | "openai" | "nvidia" | "ollama")` is the whole public surface:
everything above it (the agent loop) talks to the `Backend` protocol in
`jarvis.router.base` and never imports a vendor SDK directly. Backends are
constructed lazily and import their SDK lazily, so a missing `openai` install
or a missing Ollama server only ever affects the backend you actually asked for.
"""
from __future__ import annotations

from typing import Callable

from jarvis import config
from jarvis.router.base import Backend, BackendError, Completion, Message, ToolCall

BACKENDS: dict[str, Callable[..., Backend]] = {}


def _claude(model: str | None = None) -> Backend:
    from jarvis.router.claude import ClaudeBackend

    return ClaudeBackend(model=model)


def _openai(model: str | None = None) -> Backend:
    from jarvis.router.openai_backend import OpenAIBackend

    return OpenAIBackend(model=model)


def _nvidia(model: str | None = None) -> Backend:
    # NVIDIA's NIM API speaks the OpenAI dialect, so it's the OpenAI backend
    # pointed at a different base URL - see jarvis/router/openai_backend.py.
    from jarvis.router.openai_backend import OpenAIBackend

    return OpenAIBackend(model=model, provider="nvidia")


def _ollama(model: str | None = None) -> Backend:
    from jarvis.router.ollama import OllamaBackend

    return OllamaBackend(model=model)


BACKENDS.update(
    {"claude": _claude, "openai": _openai, "nvidia": _nvidia, "ollama": _ollama}
)

BACKEND_NAMES = tuple(BACKENDS)


def get_backend(name: str | None = None, model: str | None = None) -> Backend:
    """Build the named backend (default: whichever `config/jarvis.json` names).

    `model` overrides that backend's configured model for this instance only —
    it's how scripts/try_brain.py A/Bs two model ids on one provider without
    editing config.
    """
    name = (name or config.default_backend()).lower()
    factory = BACKENDS.get(name)
    if factory is None:
        raise BackendError(
            f"Unknown backend {name!r}. Available: {', '.join(BACKEND_NAMES)}."
        )
    return factory(model)


__all__ = [
    "BACKEND_NAMES",
    "Backend",
    "BackendError",
    "Completion",
    "Message",
    "ToolCall",
    "get_backend",
]
