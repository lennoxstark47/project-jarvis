"""
The agent loop — Phase 2.

This is "the brain": transcript in, decision out. Strip the hype and an agent
is a loop (docs/03-AGENTS_AND_MODELS.md) — send the user's words plus the tool
list to a model, get back either a spoken reply or a tool call, run the tool,
feed the result back, repeat until the model stops asking for tools. That's
this file, and there is nothing more to it than that.

What it deliberately does *not* do:

- Know which backend answered. It talks to `jarvis.router`'s Backend protocol,
  so Claude/OpenAI/Ollama are interchangeable (doc 02's whole reason for the
  router).
- Know which *input modality* produced the transcript. Phase 5's gestures will
  hand it text through the same door, with no if-this-was-voice branch.
- Remember anything between utterances. Each transcript is a fresh
  conversation; conversation history and aliases ("my project") are Phase 6.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from jarvis import tools as tool_layer
from jarvis.router import Backend, BackendError, Message, ToolCall, get_backend

logger = logging.getLogger("jarvis.agent")

# How many model round-trips one utterance may take. Each iteration is one
# model call plus the tools it asked for, so this caps both cost and the worst
# case where a model gets stuck re-calling the same tool forever. With two
# tools, a normal command finishes in two (call the tool, then summarize).
MAX_STEPS = 4

SYSTEM_PROMPT = """You are Jarvis, a voice-controlled assistant running on the \
user's Mac.

Everything you receive was spoken out loud and transcribed automatically, so \
expect small transcription errors, missing punctuation, and app or site names \
that are spelled phonetically. Interpret what the user obviously meant rather \
than what the transcript says literally.

When the user asks you to do something you have a tool for, call the tool — \
don't describe what you would do, and don't ask for confirmation on something \
as harmless as opening a page or an app. If a request needs a tool you don't \
have, say so briefly.

Your replies are read out loud, so keep them to one short sentence. No lists, \
no markdown, no URLs read out character by character."""


@dataclass
class AgentResult:
    """What one utterance produced: what was said back, and what was actually done."""

    reply: str
    actions: list[tuple[ToolCall, str]] = field(default_factory=list)
    backend: str = ""
    model: str = ""
    steps: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def summary(self) -> str:
        """One-line, log-friendly description of what happened."""
        actions = "; ".join(f"{call} -> {result}" for call, result in self.actions)
        return f"[{self.backend}/{self.model}] {actions or '(no tools)'} | reply: {self.reply}"


class Agent:
    def __init__(
        self,
        backend: Backend | str | None = None,
        *,
        model: str | None = None,
        dry_run: bool = False,
        max_steps: int = MAX_STEPS,
    ) -> None:
        """`backend` may be a Backend, a name, or None (use the configured default).

        `model` overrides that backend's configured model, for comparing two
        model ids on one provider. `dry_run=True` makes tools report what they
        *would* do without doing it — which is what lets the same command be
        replayed across every backend without opening a browser window each time.
        """
        self._backend = backend if not isinstance(backend, (str, type(None))) else None
        self._backend_name = backend if isinstance(backend, str) else None
        self._model = model
        self.dry_run = dry_run
        self.max_steps = max_steps

    @property
    def backend(self) -> Backend:
        """Built on first use so a missing API key can't break app startup."""
        if self._backend is None:
            self._backend = get_backend(self._backend_name, self._model)
        return self._backend

    def handle(self, transcript: str) -> AgentResult:
        """Run one utterance through the loop. Never raises."""
        transcript = (transcript or "").strip()
        if not transcript:
            return AgentResult(reply="", error="empty transcript")

        try:
            backend = self.backend
        except BackendError as exc:
            logger.error("no usable backend: %s", exc)
            return AgentResult(reply="My brain isn't reachable right now.", error=str(exc))

        result = AgentResult(reply="", backend=backend.name, model=backend.model)
        messages: list[Message] = [Message(role="user", content=transcript)]

        for step in range(1, self.max_steps + 1):
            result.steps = step
            try:
                completion = backend.complete(
                    messages, tool_layer.TOOL_SPECS, system=SYSTEM_PROMPT
                )
            except BackendError as exc:
                logger.error("%s backend failed on step %d: %s", backend.name, step, exc)
                result.error = str(exc)
                result.reply = result.reply or "I couldn't reach my brain just now."
                return result

            logger.info(
                "step %d: %s said %r, tools=%s (%s)",
                step,
                backend.name,
                completion.text,
                [str(call) for call in completion.tool_calls],
                completion.meta,
            )

            if not completion.wants_tools:
                result.reply = completion.text
                return result

            messages.append(
                Message(
                    role="assistant",
                    content=completion.text,
                    tool_calls=completion.tool_calls,
                )
            )
            for call in completion.tool_calls:
                output = tool_layer.execute(call.name, call.arguments, dry_run=self.dry_run)
                result.actions.append((call, output))
                messages.append(
                    Message(
                        role="tool",
                        content=output,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
            # Keep whatever it said alongside the tool call, so a model that
            # narrates ("opening that now") and then hits the step limit still
            # has something to say out loud.
            if completion.text:
                result.reply = completion.text

        # Ran out of steps with the model still calling tools. The actions that
        # did run already happened, so report honestly rather than pretending.
        logger.warning("hit the %d-step limit; stopping.", self.max_steps)
        result.error = f"stopped after {self.max_steps} steps"
        result.reply = result.reply or "I did part of that, but got stuck partway."
        return result
