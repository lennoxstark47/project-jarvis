#!/usr/bin/env python3
"""
Offline self-test for the Phase 2 brain.

Checks the parts that can be checked without an API key, a network, or a
running Ollama: the agent loop's control flow, and each backend's translation
of Jarvis's neutral message format into that vendor's wire format. Those
translations are exactly where a model-agnostic layer rots — a backend that
mis-shapes tool results doesn't crash, it just quietly stops calling tools —
so they're pinned here rather than discovered later against a live API.

    .venv/bin/python3 scripts/selftest_brain.py

Live behaviour (does a real model pick the right tool?) is scripts/try_brain.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.agent import Agent  # noqa: E402
from jarvis.router.base import Completion, Message, ToolCall, parse_arguments  # noqa: E402
from jarvis.router.claude import ClaudeBackend  # noqa: E402
from jarvis.router.ollama import OllamaBackend  # noqa: E402
from jarvis.router.openai_backend import OpenAIBackend  # noqa: E402
from jarvis.tools import TOOL_SPECS  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(f"{label}{f' — {detail}' if detail else ''}")


class ScriptedBackend:
    """A backend that replays a fixed list of Completions, recording what it saw."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, completions: list[Completion]) -> None:
        self._completions = list(completions)
        self.seen: list[list[Message]] = []

    def complete(self, messages, tools, system=""):
        self.seen.append(list(messages))
        return self._completions.pop(0) if self._completions else Completion(text="done")


# A conversation covering every message role, including two parallel tool calls.
CALLS = (
    ToolCall(id="c1", name="open_url", arguments={"url": "https://github.com"}),
    ToolCall(id="c2", name="open_app", arguments={"name": "Safari"}),
)
CONVERSATION = [
    Message(role="user", content="open github and safari"),
    Message(role="assistant", content="On it.", tool_calls=CALLS),
    Message(role="tool", content="Opened https://github.com", tool_call_id="c1", name="open_url"),
    Message(role="tool", content="Opened Safari", tool_call_id="c2", name="open_app"),
]


print("\nagent loop")

backend = ScriptedBackend(
    [
        Completion(text="Opening that.", tool_calls=(CALLS[0],)),
        Completion(text="GitHub is open."),
    ]
)
result = Agent(backend=backend, dry_run=True).handle("open github.com")
check("runs the tool the model asked for", [c.name for c, _ in result.actions] == ["open_url"])
check("returns the model's final reply", result.reply == "GitHub is open.", result.reply)
check("reports success", result.ok and result.steps == 2, result.summary())
check(
    "feeds the tool result back on the next turn",
    len(backend.seen) == 2
    and [m.role for m in backend.seen[1]] == ["user", "assistant", "tool"]
    and backend.seen[1][-1].content.endswith("Opened https://github.com"),
    str(backend.seen[-1]),
)

parallel = ScriptedBackend([Completion(tool_calls=CALLS), Completion(text="Both open.")])
result = Agent(backend=parallel, dry_run=True).handle("open github and safari")
check("handles parallel tool calls", len(result.actions) == 2, str(result.actions))

looping = ScriptedBackend([Completion(tool_calls=(CALLS[0],)) for _ in range(10)])
result = Agent(backend=looping, dry_run=True, max_steps=3).handle("open github.com")
check("stops at the step limit", result.steps == 3 and not result.ok, result.summary())

result = Agent(backend=ScriptedBackend([]), dry_run=True).handle("   ")
check("ignores an empty transcript", result.reply == "" and not result.ok)

dry = ScriptedBackend([Completion(tool_calls=(CALLS[0],)), Completion(text="ok")])
result = Agent(backend=dry, dry_run=True).handle("open github.com")
check("dry run doesn't really open anything", "dry run" in result.actions[0][1], str(result.actions))


print("\nclaude translation")

wire = ClaudeBackend()._messages(CONVERSATION)
check("assistant turn becomes text + tool_use blocks",
      [b["type"] for b in wire[1]["content"]] == ["text", "tool_use", "tool_use"], str(wire[1]))
check("tool_use carries the arguments as an object",
      wire[1]["content"][1]["input"] == {"url": "https://github.com"}, str(wire[1]))
check("both tool results merge into ONE user message",
      len(wire) == 3
      and wire[2]["role"] == "user"
      and [b["type"] for b in wire[2]["content"]] == ["tool_result", "tool_result"], str(wire))
check("tool results reference the call ids",
      [b["tool_use_id"] for b in wire[2]["content"]] == ["c1", "c2"], str(wire[2]))
claude_tools = ClaudeBackend()._tools(TOOL_SPECS)
check("tools use input_schema",
      all(set(t) == {"name", "description", "input_schema"} for t in claude_tools), str(claude_tools))


print("\nopenai translation")

wire = OpenAIBackend()._messages(CONVERSATION, "SYS")
check("system prompt goes in as a message", wire[0] == {"role": "system", "content": "SYS"})
check("assistant turn keeps text plus a tool_calls array",
      wire[2]["content"] == "On it." and len(wire[2]["tool_calls"]) == 2, str(wire[2]))
check("arguments are serialized to a JSON string",
      wire[2]["tool_calls"][0]["function"]["arguments"] == '{"url": "https://github.com"}',
      str(wire[2]["tool_calls"][0]))
check("one tool message per call, keyed by id",
      [m.get("tool_call_id") for m in wire[3:]] == ["c1", "c2"], str(wire[3:]))
openai_tools = OpenAIBackend()._tools(TOOL_SPECS)
check("tools are wrapped as type=function",
      all(t["type"] == "function" and "parameters" in t["function"] for t in openai_tools))


print("\nollama translation")

wire = OllamaBackend()._messages(CONVERSATION, "SYS")
check("system prompt goes in as a message", wire[0] == {"role": "system", "content": "SYS"})
check("arguments stay an object (not a JSON string)",
      wire[2]["tool_calls"][0]["function"]["arguments"] == {"url": "https://github.com"},
      str(wire[2]))
check("tool results are keyed by tool_name, since Ollama sends no ids",
      [m.get("tool_name") for m in wire[3:]] == ["open_url", "open_app"], str(wire[3:]))


print("\nbackend registry")

from jarvis.router import BACKEND_NAMES, get_backend  # noqa: E402

check("all five backends are registered",
      set(BACKEND_NAMES) == {"claude", "openai", "nvidia", "openrouter", "ollama"},
      str(BACKEND_NAMES))
nvidia = get_backend("nvidia")
check("nvidia is the OpenAI dialect pointed at NVIDIA's endpoint",
      isinstance(nvidia, OpenAIBackend)
      and nvidia.name == "nvidia"
      and nvidia.base_url == "https://integrate.api.nvidia.com/v1",
      f"{type(nvidia).__name__} {nvidia.name} {nvidia.base_url}")
check("nvidia keeps its own model id, not OpenAI's",
      nvidia.model != get_backend("openai").model, nvidia.model)
openrouter = get_backend("openrouter")
check("openrouter is the OpenAI dialect pointed at OpenRouter's endpoint",
      isinstance(openrouter, OpenAIBackend)
      and openrouter.name == "openrouter"
      and openrouter.base_url == "https://openrouter.ai/api/v1",
      f"{type(openrouter).__name__} {openrouter.name} {openrouter.base_url}")
check("openrouter sends its attribution headers",
      "X-Title" in openrouter.headers, str(openrouter.headers))
check("a backend with no headers block sends none", get_backend("nvidia").headers == {})

# The config-only path: a provider Jarvis has never heard of, declared in
# config/jarvis.json with a base_url, is served by the OpenAI backend. This is
# what makes "point Jarvis at another provider" a config edit and not a commit.
from jarvis import config as _config  # noqa: E402
from jarvis.router import BackendError  # noqa: E402

_real_backend_config = _config.backend_config
_config.backend_config = lambda name: (
    {"model": "llama-3.3-70b", "base_url": "https://api.groq.com/openai/v1"}
    if name == "groq" else _real_backend_config(name)
)
try:
    groq = get_backend("groq")
    check("an unregistered provider with a base_url still works",
          isinstance(groq, OpenAIBackend)
          and groq.name == "groq"
          and groq.model == "llama-3.3-70b",
          f"{type(groq).__name__} {groq.name} {groq.model}")
finally:
    _config.backend_config = _real_backend_config

try:
    get_backend("nonesuch")
    check("a truly unknown backend raises", False, "no error raised")
except BackendError as exc:
    check("a truly unknown backend raises BackendError", "nonesuch" in str(exc), str(exc))


print("\nthe environment layer")

import os  # noqa: E402

from jarvis import config  # noqa: E402


def with_env(**overrides):
    """Run load_config() with these env vars set, then put the environment back."""
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return config.load_config()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


cfg = with_env(JARVIS_BACKEND="nvidia")
check("JARVIS_BACKEND picks the backend", cfg["backend"] == "nvidia", cfg["backend"])

cfg = with_env(JARVIS_BACKEND="ollama", JARVIS_MODEL="llama3.1:8b")
check("JARVIS_MODEL follows whichever backend is selected",
      cfg["backends"]["ollama"]["model"] == "llama3.1:8b",
      str(cfg["backends"]["ollama"]))
check("...and leaves the other backends' models alone",
      cfg["backends"]["nvidia"]["model"] == "openai/gpt-oss-20b",
      str(cfg["backends"]["nvidia"]))

cfg = with_env(JARVIS_OLLAMA_HOST="http://10.0.0.9:11434")
check("per-backend vars work regardless of the active backend",
      cfg["backends"]["ollama"]["host"] == "http://10.0.0.9:11434",
      str(cfg["backends"]["ollama"]))

cfg = with_env(JARVIS_SPEECH_ENABLED="false", JARVIS_TIMEOUT="15")
check("'false' becomes a boolean, not a truthy string",
      cfg["speech"]["enabled"] is False, repr(cfg["speech"]["enabled"]))
active = cfg["backend"]
check("a numeric var becomes an int",
      cfg["backends"][active]["timeout"] == 15,
      repr(cfg["backends"][active].get("timeout")))

check("_coerce leaves a model id alone",
      config._coerce("openai/gpt-oss-20b") == "openai/gpt-oss-20b")
check("_coerce reads null as None", config._coerce("null") is None)

# .env parsing. An API key can contain a '#', so inline-comment stripping is
# deliberately *not* implemented — this pins that.
tmp = Path(__file__).resolve().parent / ".selftest.env"
tmp.write_text(
    "# a comment\n\n"
    "export JARVIS_SELFTEST_A=plain\n"
    'JARVIS_SELFTEST_B="quoted value"\n'
    "JARVIS_SELFTEST_C=key#with#hashes\n"
    "not-an-assignment\n"
)
try:
    for key in ("JARVIS_SELFTEST_A", "JARVIS_SELFTEST_B", "JARVIS_SELFTEST_C"):
        os.environ.pop(key, None)
    loaded = config.load_dotenv(tmp)
    check("export prefix is stripped", loaded.get("JARVIS_SELFTEST_A") == "plain", str(loaded))
    check("quotes are stripped", loaded.get("JARVIS_SELFTEST_B") == "quoted value", str(loaded))
    check("a '#' inside a value survives",
          loaded.get("JARVIS_SELFTEST_C") == "key#with#hashes", str(loaded))
    check("lines without '=' are skipped", len(loaded) == 3, str(loaded))
    check(".env reaches os.environ", os.environ.get("JARVIS_SELFTEST_A") == "plain")

    os.environ["JARVIS_SELFTEST_A"] = "already set"
    config.load_dotenv(tmp)
    check("a real env var wins over .env",
          os.environ["JARVIS_SELFTEST_A"] == "already set")
finally:
    tmp.unlink(missing_ok=True)
    for key in ("JARVIS_SELFTEST_A", "JARVIS_SELFTEST_B", "JARVIS_SELFTEST_C"):
        os.environ.pop(key, None)


print("\nargument parsing")

check("object passes through", parse_arguments({"url": "x"}, tool_name="t") == {"url": "x"})
check("JSON string is parsed", parse_arguments('{"url": "x"}', tool_name="t") == {"url": "x"})
check("malformed JSON degrades to empty", parse_arguments("{oops", tool_name="t") == {})
check("a JSON non-object degrades to empty", parse_arguments("[1,2]", tool_name="t") == {})
check("None degrades to empty", parse_arguments(None, tool_name="t") == {})


print()
if failures:
    print(f"{len(failures)} FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)
print("all checks passed")
