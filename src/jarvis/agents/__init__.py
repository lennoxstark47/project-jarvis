"""
Jarvis's sub-agents — Phase 7.

Three lanes, each a narrow worker with its own job and its own tools:

- **coding**   (`jarvis.agents.coding`)   — Claude Code inside a project.
- **browser**  (`jarvis.agents.browser`)  — the page Jarvis can see and type into.
- **research** (`jarvis.agents.research`) — a web lookup, in a scratch directory.

`LANES` is the registry, and `jarvis.orchestrator` is what routes to it. The
sub-agent contract, and the argument for why these three don't want a framework
between them, is in `jarvis.agents.base`.

Note what is *not* a lane: `open_url`, `open_app`, `remember` and `open_project`
are the main loop's own hands. They finish in milliseconds, need no worker, and
belong to nobody — `UNOWNED_TOOLS` names them so "every tool belongs to exactly
one lane, or is deliberately the main agent's own" is a rule that can be
checked rather than a claim in a docstring.

Lane modules are imported here, at package import, and that is safe because none
of them import a vendor SDK or a browser at module level — `jarvis.agents.
coding` pulls in `jarvis.claude_code` only inside its `run`, and
`jarvis.agents.browser` likewise. The same laziness the router uses, for the
same reason: a broken Playwright install must only ever break the command that
needed a browser.
"""
from __future__ import annotations

from jarvis.agents.base import SubAgent, with_context
from jarvis.agents.browser import SUBAGENT as BROWSER
from jarvis.agents.coding import SUBAGENT as CODING
from jarvis.agents.research import SUBAGENT as RESEARCH

LANES: dict[str, SubAgent] = {
    lane.name: lane for lane in (CODING, BROWSER, RESEARCH)
}

LANE_NAMES = tuple(LANES)

# The main agent's own instant actions. See the module docstring.
UNOWNED_TOOLS = ("open_url", "open_app", "remember", "open_project")


def lane(name: str) -> SubAgent | None:
    return LANES.get(name)


def lane_for_tool(tool_name: str) -> SubAgent | None:
    """Which sub-agent owns `tool_name`, or None if it's the main loop's own."""
    for candidate in LANES.values():
        if candidate.owns(tool_name):
            return candidate
    return None


__all__ = [
    "BROWSER",
    "CODING",
    "LANES",
    "LANE_NAMES",
    "RESEARCH",
    "SubAgent",
    "UNOWNED_TOOLS",
    "lane",
    "lane_for_tool",
    "with_context",
]
