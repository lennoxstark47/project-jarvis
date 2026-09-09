"""
Model Router — Phase 2.

`get_backend("claude" | "openai" | "nvidia" | "openrouter" | "ollama")` is the
whole public surface: everything above it (the agent loop) talks to the
`Backend` protocol in
`jarvis.router.base` and never imports a vendor SDK directly. Backends are
constructed lazily and import their SDK lazily, so a missing `openai` install
or a missing Ollama server only ever affects the backend you actually asked for.

Which one runs is a config/environment decision, never a code one:
`JARVIS_BACKEND=openrouter` in `.env` (or `"backend"` in `config/jarvis.json`)
is the entire switch. And because most vendors now speak the OpenAI dialect,
*adding* a provider is also config-only — any name that appears in
`config/jarvis.json`'s `backends` with a `base_url` is served by the OpenAI
backend even if it has no entry in BACKENDS below (see get_backend).
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


def _openrouter(model: str | None = None) -> Backend:
    # OpenRouter is also the OpenAI dialect — one key in front of many vendors'
    # models, at https://openrouter.ai/api/v1.
    from jarvis.router.openai_backend import OpenAIBackend

    return OpenAIBackend(model=model, provider="openrouter")


def _ollama(model: str | None = None) -> Backend:
    from jarvis.router.ollama import OllamaBackend

    return OllamaBackend(model=model)


BACKENDS.update(
    {
        "claude": _claude,
        "openai": _openai,
        "nvidia": _nvidia,
        "openrouter": _openrouter,
        "ollama": _ollama,
    }
)

BACKEND_NAMES = tuple(BACKENDS)


def available_backends() -> tuple[str, ...]:
    """Every backend name `get_backend` will accept, registered or config-only.

    Used for `--backend` choices in scripts/, so a provider you added to
    config/jarvis.json by hand is offered there too.
    """
    configured = tuple(
        name
        for name, entry in config.load_config().get("backends", {}).items()
        if name not in BACKENDS and isinstance(entry, dict) and entry.get("base_url")
    )
    return BACKEND_NAMES + configured


def get_backend(name: str | None = None, model: str | None = None) -> Backend:
    """Build the named backend (default: whichever `config/jarvis.json` names).

    `model` overrides that backend's configured model for this instance only —
    it's how scripts/try_brain.py A/Bs two model ids on one provider without
    editing config.
    """
    name = (name or config.default_backend()).lower()
    factory = BACKENDS.get(name)
    if factory is not None:
        return factory(model)

    # Not registered — but if config/jarvis.json declares it with a base_url,
    # take it at its word and treat it as another OpenAI-dialect endpoint.
    # That's what makes "point Jarvis at a new provider" a config edit.
    entry = config.backend_config(name)
    if entry.get("base_url"):
        from jarvis.router.openai_backend import OpenAIBackend

        return OpenAIBackend(model=model, provider=name)

    raise BackendError(
        f"Unknown backend {name!r}. Available: {', '.join(available_backends())}. "
        "To add another OpenAI-compatible provider, give it a `base_url` under "
        "`backends` in config/jarvis.json."
    )


__all__ = [
    "BACKEND_NAMES",
    "available_backends",
    "Backend",
    "BackendError",
    "Completion",
    "Message",
    "ToolCall",
    "get_backend",
]
