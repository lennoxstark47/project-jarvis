"""
The agent loop — Phases 2-3.

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

Phase 3 added two things to it. One is an `on_status` callback. Its tools stopped being
instant — `run_claude_code` can run for minutes — and doc 02 asks for a status
surface so a long sub-agent call doesn't look like Jarvis hung. The loop passes
the callback straight down to jarvis.tools and otherwise ignores it; it is the
UI's business, not the loop's.

The other is the *one* exception to "remember nothing between utterances", and
it's narrow on purpose: if the last turn ended with Jarvis asking where a
project is (jarvis.followup), this turn gets one chance to be the answer. That
is resolved **locally** — no model call at all — because a filesystem path is
not something to round-trip through a small model, and because the answer is
worth acting on the instant it's understood. See `_seed_from_followup`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from jarvis import config, followup, projects
from jarvis import tools as tool_layer
from jarvis.router import Backend, BackendError, Message, ToolCall, get_backend

StatusFn = Callable[[str], None]

logger = logging.getLogger("jarvis.agent")

# How many model round-trips one utterance may take. Each iteration is one
# model call plus the tools it asked for, so this caps both cost and the worst
# case where a model gets stuck re-calling the same tool forever. A normal
# command finishes in two (call the tool, then say what happened).
MAX_STEPS = 4

# The "say only what the tool said" paragraph is not boilerplate. Phase 3's
# first live sub-agent run timed out mid-investigation, and the small model,
# handed a partial result with no conclusion in it, confidently narrated a
# root cause in Swift/AppKit — for an app written in Python. A voice assistant
# that invents findings is worse than one that says it doesn't know.
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

Never refuse because something is unfamiliar to you. If the user names a \
project, a folder or a file you know nothing about, still call the tool — \
finding it is Jarvis's job, and it will ask the user if it has to.

When you report what a tool returned, say only what it actually said. Never \
invent a finding, a cause, a file name, or a line of code that isn't in the \
tool's result — if the tool didn't reach a conclusion, say that it didn't.

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
        on_status: StatusFn | None = None,
    ) -> None:
        """`backend` may be a Backend, a name, or None (use the configured default).

        `model` overrides that backend's configured model, for comparing two
        model ids on one provider. `dry_run=True` makes tools report what they
        *would* do without doing it — which is what lets the same command be
        replayed across every backend without opening a browser window each time.

        `on_status` is called with short progress lines ("Claude Code: Read
        agent.py") while a slow tool runs. Whatever it does must be cheap and
        must not raise — it's invoked from whichever thread the loop is on.
        """
        self._backend = backend if not isinstance(backend, (str, type(None))) else None
        self._backend_name = backend if isinstance(backend, str) else None
        self._model = model
        self.dry_run = dry_run
        self.max_steps = max_steps
        self.on_status = on_status

    @property
    def backend(self) -> Backend:
        """Built on first use so a missing API key can't break app startup."""
        if self._backend is None:
            self._backend = get_backend(self._backend_name, self._model)
        return self._backend

    def _status(self, message: str) -> None:
        if self.on_status is None:
            return
        try:
            self.on_status(message)
        except Exception as exc:  # noqa: BLE001 - a UI callback must never end a turn
            logger.warning("status callback raised: %s", exc)

    def _seed_from_followup(self, transcript: str, result: AgentResult) -> list[Message] | None:
        """If Jarvis asked where a project was, treat this utterance as the answer.

        Returns a seeded conversation (user request, the tool call, its result)
        when it *was* the answer, so the caller's loop only has to produce the
        spoken summary. Returns None otherwise, and leaves the question armed —
        a mishearing shouldn't cost the user the whole request, and
        jarvis.followup's TTL is what eventually drops it.
        """
        pending = followup.peek()
        if pending is None:
            return None
        if pending.age > followup.TTL_SECONDS:
            followup.clear()
            return None

        directory = projects.resolve_spoken(pending.project, transcript)
        if directory is None:
            logger.info("%r didn't resolve to a folder — still waiting", transcript)
            return None

        followup.take()
        # Learn it, so this is asked once per project and never again.
        config.save_alias(pending.project, directory)
        logger.info("%r is %s — running the parked task there", pending.project, directory)
        self._status(f"Got it — {directory.name}")

        arguments = {"project": pending.project, "task": pending.task}
        call = ToolCall(id="followup-0", name="run_claude_code", arguments=arguments)
        # Hand the resolved folder over directly rather than round-tripping
        # through the alias that was just saved: if that write failed (read-only
        # config, a malformed file), re-resolving would fail too and the request
        # would silently park itself again instead of running.
        try:
            output = tool_layer.run_claude_code(
                pending.project,
                pending.task,
                directory=directory,
                dry_run=self.dry_run,
                on_status=self.on_status,
            )
        except Exception as exc:  # noqa: BLE001 - a turn must never raise
            logger.error("the parked task failed: %s", exc)
            output = f"Error: couldn't run that in {directory.name}: {exc}"
        result.actions.append((call, output))
        return [
            Message(role="user", content=pending.task),
            Message(role="assistant", content="", tool_calls=(call,)),
            Message(role="tool", content=output, tool_call_id=call.id, name=call.name),
        ]

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

        # Was this utterance the answer to "where is that project?" If so the
        # tool has already run by the time this returns, and the conversation is
        # seeded as though the model had asked for it — so the loop below just
        # says what happened.
        messages = self._seed_from_followup(transcript, result)
        if messages is None:
            self._status("Thinking...")
            messages = [Message(role="user", content=transcript)]

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
                output = tool_layer.execute(
                    call.name,
                    call.arguments,
                    dry_run=self.dry_run,
                    on_status=self.on_status,
                )
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
