"""
The browser / automation lane — Phase 4's browser, given a lane's shape in Phase 7.

This lane owns the two tools that act on a page Jarvis can see: `open_portal`
and `fill_login_form`. Their bodies stay where they are (`jarvis.browser`,
`jarvis.login`) — what this module adds is the lane itself: the statement that
those two tools belong together, are attributed together, and can be routed to
as a unit.

**Why this lane has no inner model loop, when a multi-agent phase would suggest
one.** The obvious Phase 7 move is to give the browser its own small model and
let it drive itself: open the page, find the form, fill it, submit. Jarvis
deliberately does not, and the reason is Phase 4's design rather than
conservatism. A login here is *four utterances* — open the portal, hear the
username, hear the password, hear a spoken "yes" — and two of those steps are
resolved with no model call at all, on purpose: the credential is lifted out of
the transcript locally (`jarvis.credentials`, doc 04 point 2) and the submit is
gated on a human answer (`jarvis.confirm`, doc 04 point 4). A sub-agent loop
sitting between the user and those steps would either have to be handed the
password — destroying the one guarantee that lets a cloud brain be used for a
login at all — or would be a second small model re-deciding a "yes" the user
already gave.

So the sequencing this lane needs is *already* multi-step and *already*
automatic; what makes it correct is that a person is one of the steps. The lane
is therefore the tools plus that gate, and `run` below is only the entry point
for the one thing that can be done in a single shot: putting a page in front of
Jarvis. Revisit this if a task ever needs several page actions with nobody in
between — that is when a loop starts paying for itself, and not before.
"""
from __future__ import annotations

import logging
from typing import Callable

from jarvis.agents.base import SubAgent

logger = logging.getLogger("jarvis.agents.browser")

StatusFn = Callable[[str], None]


def run(
    brief: str,
    *,
    context: str = "",
    dry_run: bool = False,
    on_status: StatusFn | None = None,
) -> str:
    """Open the page named in `brief` in Jarvis's own browser.

    `brief` may be a bare URL or a sentence with one in it — a spoken "the
    internet dot herokuapp dot com slash login" included, which is why the
    extraction is `jarvis.tools.spoken_urls` rather than anything simpler.
    Never raises.
    """
    from jarvis import tools

    brief = (brief or "").strip()
    if not brief:
        return "Error: I need to know which page to open."

    spoken = tools.spoken_urls(brief)
    if len(spoken) > 1:
        return "Error: that names more than one page — which one?"
    url = spoken[0] if spoken else brief
    logger.info("browser lane: opening %s", url)
    return tools.open_portal(url, dry_run=dry_run, on_status=on_status)


SUBAGENT = SubAgent(
    name="browser",
    description=(
        "Drives Jarvis's own browser: opens a page it can see, and fills a login "
        "form on it (submitting stays with the user)."
    ),
    tools=("open_portal", "fill_login_form"),
    run=run,
)
