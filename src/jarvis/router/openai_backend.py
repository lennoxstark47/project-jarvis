"""
OpenAI backend — Phase 2.

Uses the official `openai` SDK's Chat Completions API, which is the shape
almost every other tool-calling API (including Ollama's) copied — so this
backend's translation is the closest thing to a "neutral" one:

- Tools are `{"type": "function", "function": {name, description, parameters}}`.
- An assistant turn is text *plus* a `tool_calls` array (unlike Anthropic's
  interleaved content blocks).
- Arguments arrive as a JSON **string**, not an object — hence parse_arguments.
- Tool results are their own `role="tool"` messages, one per call, keyed by
  `tool_call_id` (unlike Anthropic, where they're merged into one user turn).

Because that shape is the de-facto standard, this class is generic over the
endpoint: point `base_url` at any OpenAI-compatible provider and it works
unchanged. That's how the `nvidia` backend is implemented — NVIDIA's NIM API
(free tier, https://build.nvidia.com) is this same class aimed at
`https://integrate.api.nvidia.com/v1` with its own key and model id.

Named `openai_backend.py` rather than `openai.py` so it can never shadow the
`openai` package it imports.
"""
from __future__ import annotations

import logging
from typing import Any

from jarvis import config
from jarvis.router.base import BackendError, Completion, Message, ToolCall, parse_arguments

logger = logging.getLogger("jarvis.router.openai")

MAX_TOKENS = 4096


class OpenAIBackend:
    name = "openai"

    def __init__(
        self,
        model: str | None = None,
        *,
        provider: str = "openai",
        base_url: str | None = None,
    ) -> None:
        """`provider` selects which config/key entry to use, not which API dialect.

        Every provider handled here speaks the OpenAI Chat Completions dialect;
        they differ only in base URL, key, and model id.
        """
        cfg = config.backend_config(provider)
        self.name = provider
        self.model = model or cfg.get("model", "gpt-4o")
        self.base_url = base_url or cfg.get("base_url")
        self.timeout = cfg.get("timeout", 60)
        self._client = None
        self._openai = None

    def _get_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as exc:  # pragma: no cover - dependency is pinned
                raise BackendError(f"openai SDK not installed: {exc}") from exc

            key = config.api_key(self.name)
            if not key:
                env_name = config.ENV_KEYS.get(self.name, "OPENAI_API_KEY")
                raise BackendError(
                    f"No API key for the {self.name!r} backend. Set {env_name} or add "
                    f'{{"{self.name}": "..."}} to secrets/api_keys.json.'
                )
            self._openai = openai
            self._client = openai.OpenAI(
                api_key=key, base_url=self.base_url, timeout=self.timeout
            )
        return self._client

    # -- translation ---------------------------------------------------------

    @staticmethod
    def _tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            }
            for spec in tools
        ]

    @staticmethod
    def _messages(messages: list[Message], system: str) -> list[dict[str, Any]]:
        import json

        wire: list[dict[str, Any]] = []
        if system:
            wire.append({"role": "system", "content": system})
        for message in messages:
            if message.role == "user":
                wire.append({"role": "user", "content": message.content})
            elif message.role == "assistant":
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content or None,
                }
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in message.tool_calls
                    ]
                wire.append(entry)
            elif message.role == "tool":
                wire.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.content,
                    }
                )
        return wire

    # -- the interface --------------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str = "",
    ) -> Completion:
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                tools=self._tools(tools),
                messages=self._messages(messages, system),
            )
        except self._openai.OpenAIError as exc:
            raise BackendError(f"{self.name} API error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - network/SDK errors must not crash Jarvis
            raise BackendError(f"{self.name} call failed: {exc}") from exc

        if not response.choices:
            raise BackendError(f"{self.name} returned no choices.")

        choice = response.choices[0].message
        calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=parse_arguments(call.function.arguments, tool_name=call.function.name),
            )
            for call in (choice.tool_calls or [])
            # Only function calls are ours to run; anything else (custom tools,
            # future block types) isn't in Jarvis's contract.
            if getattr(call, "type", "function") == "function"
        ]

        usage = response.usage
        return Completion(
            text=(choice.content or "").strip(),
            tool_calls=tuple(calls),
            meta={
                "backend": self.name,
                "model": response.model,
                "stop_reason": response.choices[0].finish_reason,
                "input_tokens": getattr(usage, "prompt_tokens", None),
                "output_tokens": getattr(usage, "completion_tokens", None),
            },
        )
