"""
The research / lookup lane — Phase 7.

The job: answer a question Jarvis cannot answer from what it already knows —
"how did this library's API change", "what's the current version of X" — from
the actual web, and hand back something short and factual enough that the coding
lane can act on it and the voice model can say one sentence about it.

**Why this is the `claude` CLI again, and not a search API.** Jarvis's brain is
whichever backend `config` names, and on this machine that's a free OpenRouter
model with no browsing of its own. Adding a lookup capability the obvious way
means a search API key, a scraper, and a summarizer — three new failure modes
and a new secret to keep — to reproduce something already installed,
authenticated and used by Phase 3: Claude Code's own `WebSearch`/`WebFetch`.
Doc 02's posture on that sub-agent applies unchanged here — "an opaque, already
excellent sub-agent", so this module is a launcher and a relay.

**What it may touch, and why that list is short.** This lane is reached by a
tool call the model chose from a transcript, so its blast radius is the thing to
bound rather than its capability. It runs with:

- `--restricted --strict-mcp-config`, the pair Phase 3 *measured* as the only
  combination that actually stops a file being written (see
  `jarvis.claude_code.READ_ONLY_FLAGS` for the two weaker attempts that didn't),
- `--allowedTools=WebSearch,WebFetch` — nothing else is on, so there is no Read,
  no Bash and no Edit to redirect,
- and a **fresh temporary directory** as its working directory. `--restricted`
  confines the file tools to the working directory; pointing that at an empty
  temp dir rather than one of the user's projects means the confinement has
  nothing to confine it *to*. A lookup has no business in a repository.

That combination is why this needed no new key, no new permission prompt and no
new entry in doc 04: it can read the public web and write to a directory that is
deleted when it returns.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Callable

from jarvis import claude_code, config
from jarvis.agents.base import SubAgent

logger = logging.getLogger("jarvis.agents.research")

StatusFn = Callable[[str], None]

# The tools this lane is allowed. Written as one `--allowedTools=...` argv entry
# for the reason `jarvis.claude_code._write_guard` documents at length: the flag
# is variadic, so as two entries it keeps swallowing arguments — including the
# question itself.
WEB_TOOLS = "WebSearch,WebFetch"

# What the lookup agent is *for*. Three instructions here are not style:
#
# - "say you couldn't find it" — a lookup that quietly guesses is worse than no
#   lookup at all, because the coding lane will be handed the guess as a lead
#   and will go looking for something that was never there.
# - the length cap — this text is read by a small model that has to turn it into
#   one spoken sentence, and (when a second lane follows) prepended to a coding
#   brief. Both get worse with an essay.
# - **the search cap**, which is the one that came from a measurement rather
#   than a principle. The first live run of this lane (2026-09-09) made
#   *thirteen* web searches over 84 seconds and cost $0.51 answering "what
#   version is requests on, and was a keyword argument renamed" — it kept
#   hunting for the second half, which had no answer, and the honest "I found no
#   evidence" it eventually gave was worth about two of those searches. Someone
#   is standing in front of a menu bar waiting to hear a sentence, so a lookup
#   that has not found it in three searches should say so and stop.
BRIEF_PROMPT = """You are a research assistant answering one question for a \
voice assistant. Look the answer up on the web — do not answer from memory \
alone, and do not guess.

Make at most three web searches. Someone is waiting to hear the answer out \
loud, so stop as soon as you have it. If three searches have not produced a \
clear answer, say what you did find and that the rest is unconfirmed — do not \
keep looking.

Answer in at most six short lines of plain prose. Lead with the answer itself. \
Include concrete specifics — version numbers, dates, the old and new name of \
anything that was renamed — because another agent may act on this. Name your \
sources briefly at the end.

If the web does not give you a clear answer, say exactly that and stop. Never \
fill a gap with something plausible.

The question: {question}"""


def _settings() -> dict[str, Any]:
    return config.agents_config("research")


def enabled() -> bool:
    return bool(_settings().get("enabled", True))


def build_command(question: str, *, settings: dict[str, Any] | None = None) -> list[str]:
    """The argv the lookup runs as. Separated out so it's testable offline."""
    cfg = settings if settings is not None else _settings()
    command = [
        cfg.get("cli", "claude"),
        "--print",
        BRIEF_PROMPT.format(question=question),
        "--output-format",
        "stream-json",
        # stream-json under --print requires --verbose (Phase 3's finding).
        "--verbose",
        # Nobody is at a keyboard: deny rather than block forever on stdin.
        "--permission-prompts",
        "none",
        "--restricted",
        "--strict-mcp-config",
        f"--allowedTools={WEB_TOOLS}",
    ]
    if cfg.get("model"):
        command += ["--model", str(cfg["model"])]
    if cfg.get("max_budget_usd"):
        command += ["--max-budget-usd", str(cfg["max_budget_usd"])]
    command += list(cfg.get("extra_args", []))
    return command


def _findings(state: Any, limit: int) -> str:
    """What the run actually concluded, or an honest statement that it didn't."""
    text = (state.result_text or "").strip()
    if not text:
        # Same rule as jarvis.claude_code._summarize: a narration mid-lookup is
        # not a finding, and handing one over as if it were is how "I was about
        # to check the changelog" becomes a reported answer.
        return ""
    if len(text) > limit:
        text = text[:limit].rstrip() + "... (truncated)"
    return text


def run(
    brief: str,
    *,
    context: str = "",
    dry_run: bool = False,
    on_status: StatusFn | None = None,
) -> str:
    """Look `brief` up on the web. Returns findings, or says it couldn't.

    Never raises — including when the `claude` CLI is missing, which is a
    perfectly ordinary thing to be true on a machine and is something to say out
    loud rather than a crash in the middle of a sentence.
    """
    question = (brief or "").strip()
    if not question:
        return "Error: I need a question to look up."
    if context:
        question = f"{question}\n\n(For background, this came up while: {context.strip()})"

    cfg = _settings()
    if not cfg.get("enabled", True):
        return (
            "Web lookups are switched off in Jarvis's config, so I could not look "
            "that up. Say so — do not answer the question from memory."
        )

    command = build_command(question, settings=cfg)
    limit = int(cfg.get("max_result_chars", 1200))
    timeout = float(cfg.get("timeout", 180))

    if dry_run:
        logger.info("[dry run] would look up: %s", question)
        return (
            f"(dry run — nothing was looked up and there is no answer. Say only that "
            f"this was a dry run; do not answer the question yourself.) Would search "
            f"the web for: {question}"
        )

    logger.info("research lane: %r", question)
    _status(on_status, "Looking that up...")

    # Empty, disposable, and outside every project root — see the module
    # docstring on what `--restricted` is confined to.
    with tempfile.TemporaryDirectory(prefix="jarvis-research-") as scratch:
        try:
            outcome = claude_code.stream_run(
                command, cwd=Path(scratch), timeout=timeout, on_status=on_status
            )
        except claude_code.ClaudeCodeError as exc:
            logger.error("research lane couldn't start: %s", exc)
            return f"I couldn't look that up: {exc}"

    if outcome.timed_out:
        _status(on_status, "The lookup timed out")
        return (
            f"The web lookup was still running after {timeout:.0f} seconds, so I stopped "
            f"it and there is NO answer. Tell the user the lookup timed out — do not "
            f"answer the question yourself."
        )
    if outcome.failed_to_start:
        logger.error("research lane exited %s: %s", outcome.returncode, outcome.stderr)
        return f"I couldn't look that up: {outcome.stderr}"

    findings = _findings(outcome.state, limit)
    if not findings:
        _status(on_status, "The lookup found nothing")
        return (
            "The web lookup finished without reaching an answer. Tell the user that — "
            "do not answer the question yourself."
        )

    searches = sum(1 for tool in outcome.state.tools_used if tool.startswith("Web"))
    cost = f", ${outcome.state.cost_usd:.2f}" if outcome.state.cost_usd is not None else ""
    _status(on_status, "Looked it up")
    logger.info("research lane finished in %.1fs: %s", outcome.elapsed, findings[:120])
    return (
        f"Web lookup finished ({searches} web calls, {outcome.elapsed:.0f}s{cost}). "
        f"These are the findings — report only what is in them:\n\n{findings}"
    )


def _status(on_status: StatusFn | None, message: str) -> None:
    if on_status is None:
        return
    try:
        on_status(message)
    except Exception as exc:  # noqa: BLE001 - a UI callback must never end a lookup
        logger.warning("status callback raised: %s", exc)


SUBAGENT = SubAgent(
    name="research",
    description="Looks something up on the public web and reports what it found.",
    tools=("research",),
    run=run,
)
