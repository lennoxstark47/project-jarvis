"""
Local Ollama backend — Phase 2.

The free/offline option from doc 03: a local tool-calling model (granite4.1,
qwen3, llama3.1, ...) served by Ollama, on this machine or another one on the
LAN. Talks to `/api/chat` over plain HTTP with `httpx` rather than adding an
`ollama` package — the API is two fields wide and already a dependency of the
anthropic SDK.

Ollama copied OpenAI's tool shape, with two differences that matter here:

- Tool-call arguments come back as a JSON **object**, not a string.
- Tool calls carry **no id**, so the loop's call/result pairing has to be
  synthesized locally (`ollama-0`, `ollama-1`, ...) and sent back as
  `tool_name` on the result message, which is what Ollama actually matches on.

Doc 03 assumed local models would be the slow, weak option. Measured on
2026-09-08, that is half wrong: `granite4.1:3b` on a six-year-old GPU one room
away routes a command in **0.7-3.7s**, where the hosted free tier took 40-100s
that day. It *is* worse at tool use — it needed sharper tool descriptions than
the 20B model to stop confusing "open Claude Code" with running Claude Code —
but on latency, local wins outright, and latency is what a push-to-talk
assistant is judged on.

The timeout below is still generous (a cold model has to load first) and the
temperature is pinned to 0 — this call is a routing decision, not a creative one.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from jarvis import config
from jarvis.router.base import BackendError, Completion, Message, ToolCall, parse_arguments

logger = logging.getLogger("jarvis.router.ollama")

# Generous on purpose: a cold local model has to be loaded into memory on the
# first request, which can take tens of seconds on a laptop.
TIMEOUT = 120.0


class OllamaBackend:
    name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None) -> None:
        cfg = config.backend_config("ollama")
        self.model = model or cfg.get("model", "granite4.1:3b")
        self.host = (host or cfg.get("host", "http://localhost:11434")).rstrip("/")

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
        wire: list[dict[str, Any]] = []
        if system:
            wire.append({"role": "system", "content": system})
        for message in messages:
            if message.role == "user":
                wire.append({"role": "user", "content": message.content})
            elif message.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {"function": {"name": call.name, "arguments": call.arguments}}
                        for call in message.tool_calls
                    ]
                wire.append(entry)
            elif message.role == "tool":
                wire.append(
                    {
                        "role": "tool",
                        "content": message.content,
                        "tool_name": message.name,
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
        payload = {
            "model": self.model,
            "messages": self._messages(messages, system),
            "tools": self._tools(tools),
            "stream": False,
            "options": {"temperature": 0},
        }

        try:
            response = httpx.post(f"{self.host}/api/chat", json=payload, timeout=TIMEOUT)
        except httpx.ConnectError as exc:
            raise BackendError(
                f"Can't reach Ollama at {self.host} — is it running? "
                "Start it with `ollama serve` (install: https://ollama.com)."
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendError(f"Ollama request failed: {exc}") from exc

        if response.status_code == 404:
            raise BackendError(
                f"Ollama has no model {self.model!r} — pull it first: "
                f"`ollama pull {self.model}`."
            )
        if response.status_code != 200:
            raise BackendError(f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")

        try:
            body = response.json()
        except ValueError as exc:
            raise BackendError(f"Ollama returned non-JSON: {response.text[:200]}") from exc

        message = body.get("message") or {}
        raw_calls = message.get("tool_calls") or []
        calls = []
        for index, raw in enumerate(raw_calls):
            function = raw.get("function") or {}
            name = function.get("name")
            if not name:
                logger.warning("Ollama returned a tool call with no name: %r", raw)
                continue
            calls.append(
                ToolCall(
                    # Ollama sends no call id — synthesize a stable one so the
                    # agent loop can pair results with calls like it does for
                    # the hosted backends.
                    id=raw.get("id") or f"ollama-{index}",
                    name=name,
                    arguments=parse_arguments(function.get("arguments"), tool_name=name),
                )
            )

        return Completion(
            text=(message.get("content") or "").strip(),
            tool_calls=tuple(calls),
            meta={
                "backend": self.name,
                "model": body.get("model", self.model),
                "stop_reason": body.get("done_reason"),
                "input_tokens": body.get("prompt_eval_count"),
                "output_tokens": body.get("eval_count"),
            },
        )
