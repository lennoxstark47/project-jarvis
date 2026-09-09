"""
What a sub-agent *is* in Jarvis — Phase 7.

Doc 03 spends a section on frameworks and ends by deferring the choice to this
phase. The choice made here is **no framework** (the reasoning is written out in
docs/03-AGENTS_AND_MODELS.md, revisited 2026-09-09), and the reason it isn't
laziness is this file: a framework earns its keep when sub-agents are
homogeneous things a graph can schedule, and Jarvis's three are not remotely
homogeneous. One is a CLI subprocess that opens a terminal window you can take
over. One is a browser session with a *human confirmation* in the middle of it,
spread across several utterances. One is a different CLI invocation with the web
tools on and the filesystem off. What they have in common is not an execution
model — it's a contract about what they may touch and who they report to, which
is exactly what this dataclass is.

So a sub-agent here is four facts and a function:

- `name`      — the lane. Also the key doc 01 means by "backend per task type",
                so `config.default_backend("coding")` and
                `memory.backend_for("coding")` agree with this string.
- `tools`     — the tool names in `jarvis.tools` this lane owns. Ownership is
                the boundary that makes it a sub-agent rather than a function:
                every tool belongs to exactly one lane, and the lane is what a
                tool result is attributed to (jarvis.orchestrator).
- `description` — one line, for logs and for the router.
- `run`       — do the lane's work for a brief, and return a string the voice
                model can talk about. Never raises: a lane failing is something
                to say out loud, exactly as a tool failing is (jarvis.tools.
                execute's broad except, and for the same reason).

`run` takes `context` because Phase 7's Definition of done is a request that
needs two lanes: what the first one found has to reach the second without the
user reading it out. Every lane accepts it; a lane with nothing to do with it
ignores it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

StatusFn = Callable[[str], None]


class RunFn(Protocol):
    def __call__(
        self,
        brief: str,
        *,
        context: str = "",
        dry_run: bool = False,
        on_status: StatusFn | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class SubAgent:
    """One lane: a narrow job, the tools it owns, and how to run it."""

    name: str
    description: str
    tools: tuple[str, ...]
    run: RunFn

    def owns(self, tool_name: str) -> bool:
        return tool_name in self.tools


def with_context(brief: str, context: str) -> str:
    """Prepend what an earlier lane found to this lane's brief.

    Written once, here, because the wording is load-bearing and every lane needs
    the same of it: findings arrive labelled as *another agent's* work and as
    background rather than instruction. A coding sub-agent handed an unlabelled
    paragraph treats it as fact and stops checking; handed it this way, it knows
    what to verify. The same lesson `jarvis.claude_code._summarize` learned the
    hard way about partial results — say what a thing is, not just what it says.
    """
    context = (context or "").strip()
    brief = (brief or "").strip()
    if not context:
        return brief
    return (
        f"{brief}\n\n"
        f"--- Background, from Jarvis's research agent (it looked this up before "
        f"handing you the task). Treat it as a lead to verify against the actual "
        f"code, not as a fact you may rely on: ---\n{context}"
    )
