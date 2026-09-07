"""
Claude backend — Phase 2.

Uses the official `anthropic` SDK against the Messages API. Translation notes
for anyone comparing this with the OpenAI/Ollama backends:

- Anthropic models an assistant turn as a *list of content blocks* (text blocks
  and `tool_use` blocks side by side), not text-plus-a-tool_calls-array.
- Tool results go back as a **user** message containing `tool_result` blocks —
  and all results for one assistant turn must be in a single user message.
  Splitting them trains the model out of making parallel tool calls, so
  consecutive `role="tool"` messages are merged here.
- Thinking is deliberately left at its default (adaptive, on for Opus 5) rather
  than disabled: with thinking off, the model sometimes writes a tool call into
  its visible text instead of emitting a real `tool_use` block, which in a loop
  like ours silently does nothing. `effort` (config: backends.claude.effort) is
  the latency knob instead — intent routing over two tools runs fine at "low".
"""
from __future__ import annotations

import logging
from typing import Any

from jarvis import config
from jarvis.router.base import BackendError, Completion, Message, ToolCall, parse_arguments

logger = logging.getLogger("jarvis.router.claude")

# A cap, not a target — nothing here should come close. Big enough that a reply
# is never truncated mid-sentence, small enough to stay well inside the SDK's
# non-streaming HTTP timeout.
MAX_TOKENS = 4096


class ClaudeBackend:
    name = "claude"

    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        cfg = config.backend_config("claude")
        self.model = model or cfg.get("model", "claude-opus-5")
        self.effort = effort or cfg.get("effort", "low")
        self.timeout = cfg.get("timeout", 60)
        self._client = None
        self._anthropic = None  # the module itself, imported lazily with the client

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency is pinned
                raise BackendError(f"anthropic SDK not installed: {exc}") from exc

            key = config.api_key("claude")
            if not key:
                raise BackendError(
                    "No Anthropic API key. Set ANTHROPIC_API_KEY or add "
                    '{"claude": "sk-ant-..."} to secrets/api_keys.json.'
                )
            self._anthropic = anthropic
            self._client = anthropic.Anthropic(api_key=key, timeout=self.timeout)
        return self._client

    # -- translation ---------------------------------------------------------

    @staticmethod
    def _tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "name": spec["name"],
                "description": spec["description"],
                "input_schema": spec["parameters"],
            }
            for spec in tools
        ]

    @staticmethod
    def _messages(messages: list[Message]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "user":
                wire.append({"role": "user", "content": message.content})
            elif message.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                    for call in message.tool_calls
                )
                if blocks:
                    wire.append({"role": "assistant", "content": blocks})
            elif message.role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
                # Merge into the previous user message if that's also tool
                # results — see the module docstring on parallel tool use.
                if (
                    wire
                    and wire[-1]["role"] == "user"
                    and isinstance(wire[-1]["content"], list)
                    and wire[-1]["content"][0].get("type") == "tool_result"
                ):
                    wire[-1]["content"].append(block)
                else:
                    wire.append({"role": "user", "content": [block]})
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
            response = client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system or self._anthropic.NOT_GIVEN,
                tools=self._tools(tools),
                messages=self._messages(messages),
                output_config={"effort": self.effort},
            )
        except self._anthropic.APIError as exc:
            raise BackendError(f"Claude API error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - network/SDK errors must not crash Jarvis
            raise BackendError(f"Claude call failed: {exc}") from exc

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise BackendError(
                f"Claude declined this request ({getattr(details, 'category', 'unspecified')})."
            )

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=parse_arguments(block.input, tool_name=block.name),
                    )
                )

        return Completion(
            text=" ".join(part.strip() for part in text_parts if part.strip()),
            tool_calls=tuple(calls),
            meta={
                "backend": self.name,
                "model": response.model,
                "stop_reason": response.stop_reason,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
