"""
The coding lane — Phase 3's sub-agent, given a lane's shape in Phase 7.

Nothing about *how* Claude Code is run moved here: that is still
`jarvis.claude_code`, which opens the window, streams the transcript and decides
when an interactive session has settled. What moved here is everything that
happens between "the model asked for a coding task" and that launcher — turning
a spoken project name into a directory, parking the request as a question when
it can't (jarvis.followup), and, new in this phase, folding in what another lane
already found.

The last of those is the point of the lane existing at all. Phase 7's Definition
of done is one request that needs two sub-agents, and the join between them is
this: the research lane's findings arrive as `context` and are prepended to the
task text, labelled as somebody else's work (`jarvis.agents.base.with_context`).
Claude Code sees one instruction that already contains the lookup, so the user
never has to repeat the answer to the second agent — which is exactly what "no
manual sequencing" means in practice.

Why the containment check stays here rather than in the lane's caller: a lane is
reached from a tool call, and a tool call comes from a transcript. `directory`
skips resolution and is deliberately absent from the tool schema (see
`jarvis.tools.run_claude_code`), so the only way past `jarvis.projects` is the
one the *user* opens by saying where a project is.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from jarvis.agents.base import SubAgent, with_context

logger = logging.getLogger("jarvis.agents.coding")

StatusFn = Callable[[str], None]


def run(
    brief: str,
    *,
    context: str = "",
    dry_run: bool = False,
    on_status: StatusFn | None = None,
    project: str = "",
    directory: Path | None = None,
) -> str:
    """Give Claude Code `brief` inside `project`, and report what came back.

    `directory` bypasses project resolution for a caller that already worked the
    folder out through a trusted route — jarvis.agent, after the user said out
    loud where the project is. Never raises.
    """
    from jarvis import claude_code, followup, projects

    task = with_context(brief, context)
    if not task:
        return "Error: I need to know what to ask Claude Code to do."

    if directory is None:
        project = (project or "").strip()
        if not project:
            return "Error: I need to know which project to work in."
        try:
            directory = projects.resolve(project)
        except projects.ProjectError as exc:
            # A resolution failure is a *question for the user* ("where is
            # it?"), not a crash. Park the request — with the context already
            # folded in, so a lookup done before the question isn't lost while
            # the user answers it — and hand the model the wording to ask with.
            followup.ask_where(project, task)
            return (
                f"{exc} Ask the user where it is, in one short sentence, and say "
                f"nothing else — their next words will be the answer and I'll handle it."
            )

    logger.info("coding lane: %s in %s", brief[:80], directory)
    try:
        return claude_code.run(directory, task, dry_run=dry_run, on_status=on_status)
    except claude_code.ClaudeCodeError as exc:
        return str(exc)


SUBAGENT = SubAgent(
    name="coding",
    description="Hands a task to Claude Code inside one of the user's projects.",
    tools=("run_claude_code",),
    run=run,
)
