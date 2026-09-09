"""
Pending confirmations — Phase 4.

doc 04's point 4: Jarvis fills a login form and then *stops*, says "filled in,
want me to submit?", and waits. This module is that wait — one armed action, a
description of it, and a yes/no answer that runs or drops it.

It's a sibling of `jarvis.followup` (Phase 3's "where is that project?") and
shares its two safety properties for the same reasons: it **expires**, so a
question you walked away from can't attach itself to an unrelated sentence ten
minutes later and press a login button; and it is **consumed exactly once**, so
one "yes" submits one form.

Two design points that matter beyond this phase:

**The answer is resolved locally, with no model call.** "Yes" is not a hard
parsing problem, the model shouldn't be asked to re-derive what it's confirming,
and — most of the point — the thing being confirmed is the one action in Jarvis
that types a password into a page. Deciding that on this machine, from a fixed
word list, is strictly better than sending it out for adjudication.

**The armed action is a Python callable, not a tool name.** That's what makes
Phase 5 nearly free: a thumbs-up gesture calls `resolve(True)` and never has to
know what it confirmed, exactly as doc 02 promises ("gesture-mapped
meta-actions (confirm, cancel) ... reuse the same tool contract voice already
established"). The loop stays free of an if-this-was-voice branch.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger("jarvis.confirm")

# How long an armed action waits for an answer. Shorter than followup's 180s:
# this one *acts* when answered (it submits a login), where that one only
# resolves a folder name, so the window in which a stray "yes" can do something
# should be as small as is still comfortable.
TTL_SECONDS = 120.0

_YES = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed",
    "submit", "go", "do it", "go ahead", "please do", "send it", "log in",
    "login", "sign in", "that's right", "correct", "affirmative",
}
_NO = {
    "no", "nope", "nah", "cancel", "stop", "don't", "do not", "wait", "hold on",
    "abort", "never mind", "nevermind", "forget it", "not yet", "negative",
}


@dataclass(frozen=True)
class Pending:
    """An action waiting on a yes or a no."""

    description: str
    action: Callable[[], str]
    asked_at: float

    @property
    def age(self) -> float:
        return time.monotonic() - self.asked_at


_lock = threading.Lock()
_pending: Pending | None = None


def arm(description: str, action: Callable[[], str]) -> None:
    """Hold `action` until the user says yes. Replaces any earlier one.

    Replacing rather than queueing is deliberate: if Jarvis filled a second form
    before you answered about the first, the first question is stale — answering
    "yes" should submit what you were just asked about, not something you've
    forgotten.
    """
    global _pending
    with _lock:
        if _pending is not None:
            logger.info("dropping an unanswered confirmation: %s", _pending.description)
        _pending = Pending(description=description, action=action, asked_at=time.monotonic())
    logger.info("waiting for confirmation: %s", description)


def peek() -> Pending | None:
    """Look without consuming — for tests, logging, and the menu bar."""
    with _lock:
        pending = _pending
    if pending is not None and pending.age > TTL_SECONDS:
        return None
    return pending


def clear() -> None:
    global _pending
    with _lock:
        _pending = None


# An answer is a few words at most. Beyond this, an utterance that happens to
# start with "yes" is a sentence with a request in it ("yes, but open the other
# portal first"), and sending it to the model as a normal command is right.
MAX_ANSWER_WORDS = 4


def classify(transcript: str) -> bool | None:
    """Is this utterance a yes, a no, or neither?

    Matched against a fixed vocabulary rather than "does it contain the word
    yes", and only over a short utterance — the two failure modes worth avoiding
    are opposite, and both bad: treating a real command as a confirmation
    submits a form you didn't ask about, and failing to recognise a plain "yeah"
    leaves you saying it again at a laptop that isn't listening.

    Punctuation is dropped first because Whisper punctuates freely: "no, cancel
    that" and "no cancel that" are the same answer.
    """
    words = re.findall(r"[a-z']+", (transcript or "").lower())
    if not words or len(words) > MAX_ANSWER_WORDS:
        return None
    text = " ".join(words)
    for phrase in _YES:
        if text == phrase or text.startswith(phrase + " "):
            return True
    for phrase in _NO:
        if text == phrase or text.startswith(phrase + " "):
            return False
    return None


def resolve(answer: bool) -> str | None:
    """Run (or drop) the armed action. Returns what to say, or None if nothing was armed.

    This is the door Phase 5's gestures come through: a thumbs-up is
    `resolve(True)` and an open palm is `resolve(False)`, with no other wiring.
    """
    global _pending
    with _lock:
        pending, _pending = _pending, None

    if pending is None:
        return None
    if pending.age > TTL_SECONDS:
        logger.info("dropping a stale confirmation (%.0fs old): %s", pending.age, pending.description)
        return None

    if not answer:
        logger.info("cancelled: %s", pending.description)
        return "Cancelled."

    logger.info("confirmed: %s", pending.description)
    try:
        return pending.action()
    except Exception as exc:  # noqa: BLE001 - a confirmed action must not crash the turn
        logger.error("the confirmed action failed: %s", exc)
        return f"That didn't work: {exc}"
