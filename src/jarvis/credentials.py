"""
Credential capture — Phase 4, and the one piece of doc 04 worth building
carefully (its point 2).

Everything else in that document is a one-line change or free. This module is
the part that keeps the characters of your password off the network: it runs
**locally, before the model call**, recognises "username is X, password is Y" in
a transcript, lifts the values out, and hands the brain a transcript that says a
credential exists without saying what it is:

    heard:  "open the billing portal, username is jsmith, password is
             Tango-Romeo-Alpha-7-Charlie-9"
    model:  "open the billing portal, [credential omitted]
             (Credentials for this were captured locally. Call fill_login_form
             with credential_ref="pending_dictation_1" ...)"

The model — which may be Claude or GPT over the network — therefore never sees
the value, only a reference; `resolve()` turns that reference back into the real
credential inside the tool call, on this machine. That is the entire trick, and
it is why this parser is a plain regex rather than a model call: asking a model
to extract a credential means sending the model the credential.

**It is a mode, not a single sentence.** doc 04's example says the username and
the password in one breath. Live, on 2026-09-08, that is not what happens: the
username came in one utterance ("login, username is lennoxstark47"), the
password in another twenty seconds later, and the username was simply lost —
the second utterance captured a password with nobody to go with it, and Jarvis
asked for a credential it had already been given half of. So capture holds a
half-filled credential and says which half is missing, and `expect()` arms the
parser to read the *next* utterance as the missing value even when it arrives
bare ("it's L-E-N-N-O-X-S-T-A-R-K-47") with no "username is" to key on.

That armed state is deliberately narrow, for the same reasons `jarvis.followup`
is: it expires, it is consumed once, and an utterance that looks like a command
rather than a value is left alone and goes to the model as normal.

**Spelling-out mode.** Doc 04's flow has you spelling a password with the NATO
alphabet, because Whisper will not otherwise get "Tr4-Ch9" right. `decode_spoken`
turns "Tango-Romeo-Alpha-7-Charlie-9" into "TRA7C9", and handles digits spoken as
words, symbol names ("dash", "at", "dot"), and explicit case markers ("capital
delta", "lowercase echo"). Spelled letters default to **upper** case, which is
how people read a password out; say "lowercase" to override.

It will still get things wrong sometimes — "hunter two" is genuinely ambiguous
between "hunter2" and "huntertwo". That is exactly what the confirm-before-submit
step in doc 04's point 4 exists to catch, and why nothing here ever presses the
login button on its own.

**On lifetime.** Captured values live in this module's `_pending` map with a TTL,
and are registered with `jarvis.redact` so they can never reach a log file. They
stay registered for the life of the process even after `release()` drops the
plaintext, because a log line written *later* — a Playwright error quoting what
it typed, say — is exactly the leak the filter is there to stop. Redaction needs
the value in memory to do that; the durable copy lives in the Keychain
(jarvis.vault), never on Jarvis's own disk.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

from jarvis import redact
from jarvis.vault import Credential

logger = logging.getLogger("jarvis.credentials")

# How long a captured credential stays resolvable. Long enough for the model to
# open the portal and then call fill_login_form (two round-trips, one of which
# waits on a page load); short enough that a dictation you abandoned isn't still
# sitting there an hour later.
TTL_SECONDS = 300.0

PLACEHOLDER = redact.PLACEHOLDER

# What the model is told in place of the characters. It has to be explicit about
# the ref, because a small local model given only "[credential omitted]" tends to
# either invent a password argument or ask the user to repeat themselves.
_HANDOFF = (
    '(Credentials for this were captured locally and are held under '
    'credential_ref="{ref}". Call fill_login_form with that exact '
    "credential_ref — you cannot see the characters and must not ask the user "
    "to repeat them.)"
)

# Said when only half a credential has arrived. It has to be blunt about calling
# no tool: a small model handed "a credential was captured" will otherwise try
# to log in with the half it hasn't got.
_HALF_HANDOFF = (
    "(The user's {have} was captured locally and is held here. Their {needs} is "
    "still missing. Ask them out loud to say their {needs}, in one short "
    "sentence, and call no tool at all this turn.)"
)

_USERNAME_RE = re.compile(
    r"\b(?:user\s?name|user\s?id|login\s?name|account\s?name|user|login)\b"
    r"\s*(?:is|was|equals|:)\s*",
    re.IGNORECASE,
)
# Same reasoning as jarvis.redact's pattern: the connective is required, so
# "I can't find the password field" isn't read as a dictation whose password is
# the word "field".
_PASSWORD_RE = re.compile(
    r"\b(?:pass\s?word|pass\s?code|pass\s?phrase|pin)\b\s*(?:is|was|equals|:)\s*",
    re.IGNORECASE,
)

# Where a username clause stops when a password clause doesn't follow it.
_CLAUSE_END_RE = re.compile(r"[,;.]|\band\b|\bwith\b", re.IGNORECASE)

# Spoken filler that sits *inside* the credential clause and isn't part of the
# value: "password is — spelling it out — Tango Romeo".
_FILLER_RE = re.compile(
    r"\b(?:spelling it out|spelled out|spelling that out|that's spelled|"
    r"i'll spell it|let me spell it|in caps|all caps)\b[\s,:—-]*",
    re.IGNORECASE,
)

_NATO = {
    "alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D",
    "echo": "E", "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I",
    "juliet": "J", "juliett": "J", "kilo": "K", "lima": "L", "mike": "M",
    "november": "N", "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R",
    "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
    "whiskey": "W", "whisky": "W", "xray": "X", "x-ray": "X", "yankee": "Y",
    "zulu": "Z",
}

_DIGITS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

# Deliberately conservative. "and" is not mapped to "&" and "comma" is not
# mapped to ",": both appear constantly as ordinary connectors in a spoken
# sentence, and a wrong symbol is a failed login you have to debug by ear.
_SYMBOLS = {
    "dash": "-", "hyphen": "-", "minus": "-",
    "underscore": "_", "under-score": "_",
    "dot": ".", "period": ".", "point": ".", "fullstop": ".",
    "at": "@", "hash": "#", "pound": "#",
    "star": "*", "asterisk": "*",
    "bang": "!", "exclamation": "!",
    "dollar": "$", "percent": "%", "slash": "/", "plus": "+",
    "tilde": "~", "caret": "^", "question": "?",
}

_UPPER_MARKERS = {"capital", "cap", "uppercase", "upper", "big"}
_LOWER_MARKERS = {"lowercase", "lower", "small", "little"}

# How long the parser stays armed for the missing half of a credential. Short:
# while this is live, a bare utterance is read as a password rather than as
# something to do, and that is not a state to leave lying around.
EXPECT_TTL_SECONDS = 120.0

# A bare utterance is only read as a credential value if it plausibly is one.
# These start a command, never a password.
_COMMAND_WORDS = {
    "open", "run", "close", "go", "show", "tell", "use", "what", "why", "how",
    "who", "where", "when", "stop", "cancel", "never", "forget", "search",
    "find", "play", "send", "make", "create", "check", "read", "write", "quit",
}

# Longer than this and it's a sentence, not a value.
_MAX_VALUE_WORDS = 8

_lock = threading.Lock()
_pending: dict[str, tuple[Credential, float]] = {}
_counter = 0
# The half-filled credential, and what's still missing. Plaintext, in memory,
# for at most EXPECT_TTL_SECONDS — same lifetime argument as _pending.
_partial: dict[str, str] = {"username": "", "password": ""}
_expecting: tuple[str, float] | None = None


@dataclass(frozen=True)
class Capture:
    """What one utterance yielded, before any model saw it.

    `complete` captures carry a resolvable `ref`. An incomplete one carries
    `needs` ("username" or "password") — the half Jarvis still has to ask for —
    and no ref, because there is nothing to fill a form with yet.
    """

    redacted: str
    ref: str = ""
    credential: Credential | None = None
    needs: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.ref)

    def __repr__(self) -> str:  # pragma: no cover - keeps secrets out of tracebacks
        return f"Capture(ref={self.ref!r}, needs={self.needs!r}, redacted={self.redacted!r})"


def _strip_value(text: str) -> str:
    """Trim the punctuation and filler around a dictated value."""
    text = _FILLER_RE.sub(" ", text)
    return text.strip().strip(".,;:!?—–-\"' ")


def decode_spoken(text: str, *, default_upper: bool = True) -> str:
    """Turn a spoken/spelled-out value into the characters it stands for.

    "Tango-Romeo-Alpha-7-Charlie-9" -> "TRA7C9"; "john dot smith at example dot
    com" -> "john.smith@example.com". Tokens that aren't letters, digits or
    symbol names are kept as they were spoken (a password dictated as a whole
    word stays that word), which is what makes this safe to run over every
    captured value rather than only obviously-spelled ones.

    `default_upper=False` lowercases those plain word tokens — used for
    usernames, which are conventionally lower case and which Whisper capitalises
    anyway whenever the clause starts a sentence.
    """
    if not text:
        return ""
    out: list[str] = []
    force: str | None = None
    # Hyphens are separators, not characters: Whisper writes a spelled-out
    # password as "Tango-Romeo-Alpha". Say "dash" when you mean the character.
    for raw in re.split(r"[\s\-–—]+", text):
        token = raw.strip().strip(".,;:!?\"'")
        if not token:
            continue
        key = token.lower()

        if key in _UPPER_MARKERS:
            force = "upper"
            continue
        if key in _LOWER_MARKERS:
            force = "lower"
            continue

        if key in _NATO:
            letter = _NATO[key]
            out.append(letter.lower() if force == "lower" else letter)
        elif key in _DIGITS:
            out.append(_DIGITS[key])
        elif key in _SYMBOLS:
            out.append(_SYMBOLS[key])
        elif len(token) == 1:
            # A single spoken character: "b", "7", "@". Case markers win;
            # otherwise it follows the same convention as a whole word, which
            # matters for a username spelled out letter by letter — Whisper
            # writes that as "L-E-N-N-O-X", and a login field wants lower case.
            if force == "upper":
                out.append(token.upper())
            elif force == "lower" or not default_upper:
                out.append(token.lower())
            else:
                out.append(token.upper() if token.isalpha() else token)
        else:
            if force == "upper":
                out.append(token.upper())
            elif force == "lower" or not default_upper:
                out.append(token.lower())
            else:
                out.append(token)
        force = None
    return "".join(out)


def _extract(transcript: str) -> tuple[str, str, int] | None:
    """Find the username/password clauses. Returns (username, password, start).

    Either half may come back empty — "username is jsmith" on its own is a real
    utterance that people say, and losing it because no password followed is the
    bug this returns a half-answer to avoid.
    """
    password_match = _PASSWORD_RE.search(transcript)
    password = ""
    start = len(transcript)
    if password_match is not None:
        password = _strip_value(transcript[password_match.end() :])
        if password:
            start = password_match.start()
        else:
            password_match = None

    # The username clause has to come before the password one — "password is X"
    # runs to the end of the sentence, so anything after it is part of the value.
    head = transcript[: password_match.start()] if password_match else transcript
    username_match = _USERNAME_RE.search(head)
    username = ""
    if username_match is not None:
        tail = head[username_match.end() :]
        end = _CLAUSE_END_RE.search(tail)
        username = _strip_value(tail[: end.start()] if end else tail)
        if username:
            start = min(start, username_match.start())

    if not username and not password:
        return None
    return username, password, start


def expect(what: str) -> None:
    """Arm the parser to read the next bare utterance as `what`.

    Called by jarvis.login when it needs a credential it hasn't got. "both"
    means nothing has been said yet, so whichever half arrives first is taken.
    """
    global _expecting
    with _lock:
        _expecting = (what, time.monotonic())
    logger.info("listening for the user's %s", what)


def expecting() -> str:
    """What the parser is currently waiting for, or "" if it isn't waiting."""
    with _lock:
        if _expecting is None:
            return ""
        what, armed_at = _expecting
        if time.monotonic() - armed_at > EXPECT_TTL_SECONDS:
            return ""
        return what


def clear_expectation() -> None:
    """Stop waiting, and forget any half-credential collected so far."""
    global _expecting
    with _lock:
        _expecting = None
        _partial["username"] = _partial["password"] = ""


def _looks_like_value(text: str) -> bool:
    """Is this short enough, and unlike a command enough, to be a credential?

    The guard matters in both directions. Too strict and a spelled-out password
    goes to the model as a command (which is how a password ends up in a cloud
    prompt). Too loose and "open github and log in" gets typed into a password
    field while the model is told the user said nothing.
    """
    words = text.split()
    if not words or len(words) > _MAX_VALUE_WORDS:
        return False
    first = words[0].lower().strip(".,;:!?\"'")
    return first not in _COMMAND_WORDS


def _bare_value(transcript: str) -> str:
    """Strip the lead-in from an answer to "what's your username?"."""
    text = _FILLER_RE.sub(" ", transcript).strip()
    text = re.sub(r"^(?:it'?s|that'?s|its|just|my)\s+", "", text, flags=re.IGNORECASE)
    return _strip_value(text)


def capture(transcript: str) -> Capture | None:
    """Lift a dictated credential — or half of one — out of `transcript`.

    Returns None when the utterance isn't credential dictation at all, so the
    caller can treat it as an ordinary command. On any other result the value is
    already registered with `jarvis.redact`, and `Capture.redacted` is a
    transcript that is safe to send to a cloud model.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return None

    found = _extract(transcript)
    if found is not None:
        spoken_username, spoken_password, start = found
        head = transcript[:start].strip()
    else:
        # Nothing said "username is" or "password is" — but if Jarvis has just
        # asked for one of them out loud, the answer arrives bare.
        awaited = expecting()
        if not awaited or not _looks_like_value(transcript):
            return None
        value = _bare_value(transcript)
        if not value:
            return None
        # "both" armed and nothing collected yet: a username is what people give
        # first, and it's the half Jarvis asks for first.
        field = awaited if awaited in ("username", "password") else "username"
        spoken_username = value if field == "username" else ""
        spoken_password = value if field == "password" else ""
        head = ""
        logger.info("read a bare utterance as the %s Jarvis asked for", field)

    return _merge(spoken_username, spoken_password, head)


def _merge(spoken_username: str, spoken_password: str, head: str) -> Capture | None:
    """Fold what this utterance gave into the half-credential, and report where that leaves us."""
    global _counter

    username = decode_spoken(spoken_username, default_upper=False) if spoken_username else ""
    password = decode_spoken(spoken_password) if spoken_password else ""

    # Register before anything else can log: from here the value is live.
    if password:
        redact.register(password, spoken_password)

    with _lock:
        if username:
            _partial["username"] = username
        if password:
            _partial["password"] = password
        username, password = _partial["username"], _partial["password"]

        if not password:
            missing, have = "password", "username"
        elif not username:
            missing, have = "username", "password"
        else:
            missing = have = ""

        if missing:
            global _expecting
            _expecting = (missing, time.monotonic())
        else:
            _counter += 1
            ref = f"pending_dictation_{_counter}"
            _pending[ref] = (Credential(username=username, password=password), time.monotonic())
            _partial["username"] = _partial["password"] = ""
            _expecting = None
            _expire_locked()

    if missing:
        logger.info("captured the user's %s locally — still need the %s", have, missing)
        note = _HALF_HANDOFF.format(have=have, needs=missing)
        return Capture(redacted=f"{head} {PLACEHOLDER} {note}".strip(), needs=missing)

    logger.info(
        "captured a credential locally (%s, username %s) — the model sees only the ref",
        ref,
        "yes" if username else "none given",
    )
    return Capture(
        ref=ref,
        credential=Credential(username=username, password=password),
        redacted=f"{head} {PLACEHOLDER} {_HANDOFF.format(ref=ref)}".strip(),
    )


def _expire_locked() -> None:
    """Drop timed-out entries. Caller holds `_lock`."""
    now = time.monotonic()
    for ref in [r for r, (_, at) in _pending.items() if now - at > TTL_SECONDS]:
        logger.info("dropping expired credential %s", ref)
        del _pending[ref]


def resolve(ref: str) -> Credential | None:
    """Turn a credential_ref back into the real value. Local calls only."""
    with _lock:
        _expire_locked()
        entry = _pending.get((ref or "").strip())
    return entry[0] if entry else None


def latest() -> tuple[str, Credential] | None:
    """The most recent still-live capture, as (ref, credential). None if there is none.

    This exists because models forget to pass the ref. A small local model, told
    a credential is held under `pending_dictation_1`, will sometimes call
    fill_login_form with just the site — and asking you to dictate a password
    you dictated four seconds ago is a bad answer to a problem Jarvis can see
    the solution to. Nothing about the security story changes: the value is on
    this machine either way, and the model still never learns it.

    Most recent rather than "only if there's exactly one": if you dictated twice,
    the second one is the correction.
    """
    with _lock:
        _expire_locked()
        if not _pending:
            return None
        ref = max(_pending, key=lambda key: _pending[key][1])
    return ref, _pending[ref][0]


def release(ref: str) -> None:
    """Drop the plaintext for `ref` once it's been used or is no longer wanted."""
    with _lock:
        _pending.pop((ref or "").strip(), None)


def clear() -> None:
    """Drop every pending credential, half-credential and expectation."""
    global _expecting
    with _lock:
        _pending.clear()
        _partial["username"] = _partial["password"] = ""
        _expecting = None


def pending_refs() -> list[str]:
    """Which refs are currently resolvable — for tests and diagnostics."""
    with _lock:
        _expire_locked()
        return sorted(_pending)
