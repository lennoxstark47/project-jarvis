"""
The one thing Jarvis remembers between two utterances — Phase 3 (round 2).

Everything else in the agent is stateless on purpose: each transcript is a fresh
conversation, and real conversation memory is Phase 6's job (doc 02's memory
store). This module is a deliberate, single-purpose exception, because one
specific exchange is useless without it:

    you:    "use Claude Code on my invoicing thing to find why totals are wrong"
    Jarvis: "I don't know where 'my invoicing thing' is — where is it?"
    you:    "it's in Documents, under client work"
                                    ^ meaningless to a stateless agent

So when a project can't be resolved, the request is parked here, and the *next*
utterance gets one chance to be the answer to it. That's the whole scope: one
pending question, about one thing, for a few minutes.

Two properties worth keeping when Phase 6 replaces this with something general:

- **It expires.** A question you never answered must not silently attach itself
  to whatever you say ten minutes later — that would launch a coding agent on a
  task you'd forgotten you started.
- **It is consumed exactly once.** `take()` clears it, so a failed answer
  doesn't leave the trap armed for the sentence after that.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger("jarvis.followup")

# How long an unanswered question stays live. Long enough to think about where
# something is, short enough that it can't ambush a later, unrelated command.
TTL_SECONDS = 180.0


@dataclass(frozen=True)
class PendingLocation:
    """A run_claude_code request waiting on "…but where is that project?"."""

    project: str
    task: str
    asked_at: float

    @property
    def age(self) -> float:
        return time.monotonic() - self.asked_at


_lock = threading.Lock()
_pending: PendingLocation | None = None


def ask_where(project: str, task: str) -> None:
    """Park a request until the user says where the project is."""
    global _pending
    with _lock:
        _pending = PendingLocation(project=project, task=task, asked_at=time.monotonic())
    logger.info("waiting to be told where %r is", project)


def take() -> PendingLocation | None:
    """Return the live pending question and clear it. None if there isn't one."""
    global _pending
    with _lock:
        pending, _pending = _pending, None
    if pending is None:
        return None
    if pending.age > TTL_SECONDS:
        logger.info("dropping a stale question about %r (%.0fs old)", pending.project, pending.age)
        return None
    return pending


def peek() -> PendingLocation | None:
    """Look without consuming — for tests and logging."""
    with _lock:
        return _pending


def clear() -> None:
    global _pending
    with _lock:
        _pending = None
