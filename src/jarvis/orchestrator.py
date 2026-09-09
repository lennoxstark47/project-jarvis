"""
Routing across the sub-agents — Phase 7.

Phase 7's goal, in doc 01's words, is that "the main Jarvis loop routes to these
rather than trying to do everything in one prompt". Two things are needed for
that and this module is both of them.

**1. Which lane is this?** `classify` answers it, from the words alone, with no
model call. That looks like a shortcut and is a deliberate choice: what the
answer is *used for* is picking which brain runs the turn
(`config.default_backend(lane)` — doc 01's "preferred model backend per task
type", which has had storage since Phase 6 and no caller until now). Spending a
model call to decide which model to call is a latency cost paid on every single
utterance, at the exact point in the turn where the user is standing there
waiting; and being wrong costs nothing worse than using the brain that would
have answered anyway. So it is keywords, the classification is a *hint*, and
nothing downstream is allowed to depend on it being right.

Note what deliberately does **not** live here: which tool runs. That is still
the model's job, made by choosing a tool from `jarvis.tools.TOOL_SPECS` — a
model reading a whole sentence is far better at "did they mean open the app or
run a task in it" than any keyword list, and Phase 3 has the scar tissue to
prove it. This module routes *models and attribution*; the loop routes work.

**2. What did the last lane find?** `Handoff` is the ledger. When one utterance
touches two lanes — "look up how this library's API changed, then fix the bug in
my project" — the second lane needs the first one's findings, and the whole
point of the Definition of done is that the *user* doesn't relay them. The model
is asked to pass them along (`run_claude_code`'s `context` argument), but it is
not *trusted* to: a small model that forgets one argument would silently turn a
two-agent request back into a one-agent one, and the failure would look like a
worse answer rather than a bug. So the ledger records what each lane returned,
and `jarvis.agent` fills the argument in from it when the model leaves it out.
The rule that has run through every phase since Phase 3 — anything load-bearing
is resolved locally, the model decides *whether*, not *what* — applies here too.

Only the research lane's output is carried forward. The others return status
("Opened the page"), not findings, and pasting that into a coding brief would be
noise a small model then has to explain away.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from jarvis.agents import LANES, SubAgent, lane_for_tool

logger = logging.getLogger("jarvis.orchestrator")

# The lane an utterance is put in when nothing matches: an ordinary command or a
# question. Not a sub-agent — nobody owns it — but a real key for
# `agents.routing.backends`, because "use the cheap model for chatter and the
# good one for code" is exactly the preference doc 01 asked for.
CHAT = "chat"

# When a sentence matches more than one lane it is a compound request, and the
# brain that runs the turn should be the one that can handle the *hardest* part
# of it. Coding first: it is the lane whose mistakes edit files.
PRIORITY = ("coding", "browser", "research", CHAT)

# Only findings travel between lanes. See the module docstring.
CARRIES_CONTEXT = ("research",)

# How much of an earlier lane's output is handed to the next one. Long enough
# for the six lines the research lane is told to produce; short enough that a
# runaway result can't push the actual task out of a small model's window.
MAX_CONTEXT_CHARS = 1500

# Word-boundary patterns, one lane each. Kept as phrases people actually say to
# a voice assistant rather than a vocabulary — a keyword list that tries to be
# complete is a keyword list that matches everything.
_PATTERNS: dict[str, tuple[str, ...]] = {
    "coding": (
        r"\bclaude code\b",
        r"\bfix\b",
        r"\bdebug\b",
        r"\brefactor\b",
        r"\bthe bug\b",
        r"\ba bug\b",
        r"\bstack trace\b",
        r"\bunit test(s)?\b",
        r"\bin (my|the) (project|repo|codebase)\b",
        r"\b(function|method|class|module|file) (called|named)\b",
        r"\bwhy (is|does|isn'?t|doesn'?t) .*(code|test|build|app|script)\b",
    ),
    "browser": (
        r"\blog ?in\b",
        r"\blogged in\b",
        r"\bsign ?in\b",
        r"\bportal\b",
        r"\bmy (username|password)\b",
        r"\bfill (in|out)\b",
        r"\bthe form\b",
    ),
    "research": (
        r"\blook (that |it |this )?up\b",
        r"\bsearch (the web|online|for)\b",
        r"\bgoogle\b",
        r"\bfind out\b",
        r"\bwhat'?s the latest\b",
        r"\blatest version\b",
        r"\bchangelog\b",
        r"\brelease notes\b",
        r"\bhow (has|have|did) .*\bchange(d)?\b",
        r"\bdeprecat(ed|ion)\b",
    ),
}

_COMPILED = {
    name: tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
    for name, patterns in _PATTERNS.items()
}


def lanes_named(transcript: str) -> tuple[str, ...]:
    """Every lane this sentence looks like it touches, in PRIORITY order.

    Plural on purpose: a compound request naming two lanes is the thing Phase 7
    exists for, and seeing that in the log is how a mis-sequenced turn gets
    diagnosed later. Nothing acts on more than the first one.
    """
    text = transcript or ""
    found = [
        name
        for name in PRIORITY
        if name in _COMPILED and any(rx.search(text) for rx in _COMPILED[name])
    ]
    return tuple(found)


def classify(transcript: str) -> str:
    """Which lane this utterance most likely belongs to. A hint, never a decision.

    Returns a lane name or CHAT. Never raises and never returns something that
    isn't a real key — a caller may pass the result straight to
    `config.default_backend`.
    """
    found = lanes_named(transcript)
    lane = found[0] if found else CHAT
    if len(found) > 1:
        logger.info("%r looks like %s — routing as %s", transcript[:60], found, lane)
    return lane


@dataclass
class Handoff:
    """What each lane produced during one utterance, for the next lane to use.

    One per turn, held by `jarvis.agent`. Deliberately not global and not
    persisted: findings are context for *this* request, and a lookup done twenty
    minutes ago silently becoming background for an unrelated coding task is the
    same failure mode `jarvis.followup` and `jarvis.confirm` each spend a
    docstring avoiding.
    """

    entries: list[tuple[str, str]] = field(default_factory=list)

    def record(self, lane_name: str, output: str) -> None:
        if not lane_name or not output:
            return
        self.entries.append((lane_name, output.strip()))

    def context_for(self, lane_name: str) -> str:
        """Findings from earlier lanes that `lane_name` should be told about.

        A lane is never handed its own output back — a second lookup in one turn
        is a refinement of the first, not a continuation of it.
        """
        parts = [
            text
            for name, text in self.entries
            if name in CARRIES_CONTEXT and name != lane_name
        ]
        if not parts:
            return ""
        joined = "\n\n".join(parts)
        if len(joined) > MAX_CONTEXT_CHARS:
            joined = joined[:MAX_CONTEXT_CHARS].rstrip() + "... (truncated)"
        return joined

    def lanes_used(self) -> tuple[str, ...]:
        """Which lanes ran this turn, in order, without repeats."""
        seen: list[str] = []
        for name, _ in self.entries:
            if name not in seen:
                seen.append(name)
        return tuple(seen)

    def __bool__(self) -> bool:
        return bool(self.entries)


def lane_of(tool_name: str) -> SubAgent | None:
    """Which sub-agent owns this tool. Re-exported so callers need one import."""
    return lane_for_tool(tool_name)


def describe() -> str:
    """One line per lane — for `scripts/try_agents.py` and the log at startup."""
    return "\n".join(
        f"{name:<9} {lane.description}  (tools: {', '.join(lane.tools)})"
        for name, lane in LANES.items()
    )
