"""
The model-router interface — Phase 2.

docs/02-SYSTEM_DESIGN.md specifies one thin interface,
`complete(messages, tools) -> reply | tool_call`, with one implementation per
backend. This module is that interface plus the neutral data shapes it speaks:

- `Message`  — a conversation turn, in *Jarvis's* format, not any vendor's.
- `ToolCall` — the model asking for a tool, normalized across vendors.
- `Completion` — what came back: reply text, tool calls, or both.

The point of the neutral shapes is that `jarvis.agent`'s loop never sees an
Anthropic content block or an OpenAI `tool_calls` array. Each backend does its
own translation in both directions, so "swap the backend" stays a config change
instead of a rewrite (doc 03's whole argument for hand-rolling this).

`system` is passed as its own argument rather than as a message, because the
Anthropic API models it as a top-level field. Doc 02 writes the signature as
`complete(messages, tools)`; this is that, with the system prompt hoisted out
so no backend has to go fishing for it in the message list.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class BackendError(RuntimeError):
    """A backend couldn't answer — missing key, no server, API error, bad response.

    Raised (rather than returned) because it means the brain is unavailable,
    which is a different thing from the model deciding to say nothing. The
    agent loop turns it into something speakable at the top level.
    """


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def __str__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.arguments.items())
        return f"{self.name}({args})"


@dataclass(frozen=True)
class Message:
    """One conversation turn.

    role="user"       — content is what the user said.
    role="assistant"  — content is the reply text, tool_calls what it wants run.
    role="tool"       — content is a tool's result; tool_call_id/name say which.
    """

    role: str
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    name: str = ""


@dataclass(frozen=True)
class Completion:
    """What a backend returned for one `complete()` call."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    # Free-form, per-backend detail (model id, token usage, ...) — for logging
    # and honest backend comparison, never for control flow.
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class Backend(Protocol):
    """What every LLM backend must implement. That's the whole contract."""

    name: str
    model: str

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str = "",
    ) -> Completion: ...


def parse_arguments(raw: Any, *, tool_name: str) -> dict[str, Any]:
    """Coerce a backend's tool arguments into a plain dict.

    OpenAI/Ollama send them as a JSON *string*, Anthropic as an object, and
    models occasionally emit something malformed. Always parse rather than
    string-match (see the Claude API tool-call JSON escaping caveat), and treat
    unparseable arguments as empty — jarvis.tools.execute then reports the
    missing-argument error back to the model, which can retry.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
