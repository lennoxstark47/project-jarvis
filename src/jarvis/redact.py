"""
Secret redaction at the logging layer — Phase 4.

docs/04-CREDENTIALS_AND_SECURITY.md, point 5: the rule "don't log the password"
must be enforced *once*, where log lines are formatted, rather than remembered
at every call site. Call-site discipline is exactly the kind of rule that gets
forgotten under deadline pressure and silently leaks a password into
`logs/jarvis.log`, which is the one place a credential would otherwise sit in
plaintext forever.

So there are two overlapping defences here, and they cover different failures:

- **Registered values.** `jarvis.credentials` registers each captured secret the
  moment it exists, so any log line that contains those characters — from any
  module, including a Playwright error that quotes what it was told to type —
  comes out as `[credential omitted]`. This is exact and catches everything.
- **Patterns.** A "password is ..." phrase is redacted whether or not anything
  was registered, which covers the window before the parser has run and the case
  where it didn't recognise the dictation at all. This is fuzzy and catches the
  transcript itself.

`register()` deliberately ignores very short values. A one- or two-character
secret would match half the words in the log and turn every line into
`[credential omitted]`, which destroys the log's usefulness to protect
essentially nothing — a two-character password is not what this is defending.
"""
from __future__ import annotations

import logging
import re
import threading

PLACEHOLDER = "[credential omitted]"

# Below this length, exact-substring redaction does more damage to the log than
# it does good. See the module docstring.
MIN_REDACTABLE_LENGTH = 3

# "password is hunter2", "passcode was ...", "my pin is ..." — everything from
# the keyword to the end of the line goes, because a spoken credential runs to
# the end of the sentence and we can't know where it stops.
#
# The connective ("is", "was", ":") is required, and that is not a detail. Made
# optional, this swallowed the rest of any line that merely *mentioned* a
# password: a Playwright error reading "there's no password field on <url>" came
# back as "there's no password [credential omitted]", which is a log line
# destroyed to protect nothing. Registered values (above) are what catch a real
# secret in an odd phrasing; this pattern only has to catch the ordinary one.
_SPOKEN_SECRET_RE = re.compile(
    r"\b(pass\s?word|pass\s?code|pass\s?phrase|pin)\b\s*(?:is|was|equals|:)\s*\S.*",
    re.IGNORECASE,
)

_lock = threading.Lock()
_registered: set[str] = set()


def register(*values: str) -> None:
    """Start redacting `values` from every log line, everywhere."""
    with _lock:
        for value in values:
            value = (value or "").strip()
            if len(value) >= MIN_REDACTABLE_LENGTH:
                _registered.add(value)


def discard(*values: str) -> None:
    """Stop redacting `values` — call when a secret's lifetime is over."""
    with _lock:
        for value in values:
            _registered.discard((value or "").strip())


def registered_count() -> int:
    """How many live secrets are being redacted (for tests and diagnostics)."""
    with _lock:
        return len(_registered)


def redact_secrets(text: str) -> str:
    """Replace every known secret, and anything that looks like one, in `text`."""
    if not text:
        return text
    with _lock:
        # Longest first: a password that contains the username as a substring
        # must not be half-redacted into something still guessable.
        values = sorted(_registered, key=len, reverse=True)
    for value in values:
        if value in text:
            text = text.replace(value, PLACEHOLDER)
    return _SPOKEN_SECRET_RE.sub(lambda match: f"{match.group(1)} {PLACEHOLDER}", text)


class RedactingFilter(logging.Filter):
    """Rewrites every record's message through `redact_secrets`.

    Attached to *handlers* rather than to a logger: a filter on a logger only
    sees records logged through that logger, so a filter on the root logger
    would miss everything from `jarvis.*` — which is all of it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken log call must not break logging
            return True
        clean = redact_secrets(message)
        if clean != message:
            # Args are folded into the redacted string, so they must not be
            # re-applied to it — a stray '%s' in a password would then raise.
            record.msg = clean
            record.args = ()
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True


def install(logger: logging.Logger | None = None) -> None:
    """Add the filter to every handler on `logger` (default: the root logger).

    Call once, after logging is configured. Safe to call again — a handler that
    already has one doesn't get a second.
    """
    target = logger or logging.getLogger()
    for handler in target.handlers:
        if not any(isinstance(existing, RedactingFilter) for existing in handler.filters):
            handler.addFilter(RedactingFilter())
