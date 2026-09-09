"""
The memory store — Phase 6.

Everything before this phase was stateless on purpose. Each utterance was a
fresh conversation: `jarvis.followup` and `jarvis.confirm` each carried exactly
one fact across exactly one turn, and both said in their own docstrings that
Phase 6 was where the general version lived. This is it.

Doc 02 asks for SQLite and two kinds of memory, and that is what's here:

- **Long-term** — `aliases` ("my project" -> a path, "the billing portal" -> a
  URL) and `preferences` (which voice reads replies out, which backend to
  prefer). Written once, read for months.
- **Short-term** — `turns`, a rolling window of recent conversation, so the
  four-turn spoken login of Phase 4 stops starting from nothing every time the
  user opens their mouth.

Four things shape this file, and each of them is a rule rather than a
preference:

**Retrieval is selective, never a dump.** `recall()` returns only the aliases
whose names actually appear in what was just said, capped at
`memory.max_facts`. Doc 02 is explicit about this and the reason is latency:
every fact injected is tokens on the critical path between the user finishing a
sentence and hearing an answer, on a small model where that path is already the
whole user experience. A store that grows for a year must not make Jarvis a
second slower every month.

**Nothing secret is ever written here.** The conversation window is the one
place in Jarvis where the user's words are put on disk, and the user dictates
passwords out loud (doc 04). So every turn goes through `jarvis.redact` on the
way *in* — a masked sentence is stored, never the characters, and there is no
window in which the plaintext exists in this file. Doing it here rather than at
each call site is the same decision doc 04 point 5 made for logging, for the
same reason: a rule enforced once cannot be forgotten under deadline pressure.
The database sits under `memory/`, which is gitignored, and is created 0600.

**An alias is a fact, not an instruction.** What is stored is `name -> value`;
what happens when the user says the name is still decided by `jarvis.projects`,
whose containment check ("stay inside the roots") remains the thing standing
between a misheard word and a coding agent launched in the wrong directory.
projects.py asked for exactly this when it said memory "should feed this
resolver rather than replace it".

**Memory failing is not Jarvis failing.** A locked database, a full disk, a
corrupted file — none of that should cost the user the sentence they just
spoke. Every public function here swallows both `sqlite3.Error` and `OSError`
— the second matters as much as the first, because the failures that actually
happen (an unwritable directory, a full disk) surface as OSError from opening
the file rather than from any query — and degrades to the stateless behaviour
of Phase 5, loudly in the log and silently to the user. That's the same call
`jarvis.stt.transcribe` and `jarvis.tools.execute` make, for the same reason.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis import config, redact

logger = logging.getLogger("jarvis.memory")

# Alias kinds. Two, because two things get named out loud and they are opened
# in different ways: a folder on disk (jarvis.projects resolves it, and the
# containment check applies) and a web address (jarvis.tools.open_url).
PROJECT = "project"
PORTAL = "portal"
KINDS = (PROJECT, PORTAL)

SCHEMA = """
CREATE TABLE IF NOT EXISTS aliases (
    name       TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    created_at REAL NOT NULL,
    used_at    REAL,
    uses       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS preferences (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      REAL NOT NULL,
    role    TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_at ON turns (at);
"""


@dataclass(frozen=True)
class Alias:
    name: str
    value: str
    kind: str
    uses: int = 0

    def as_fact(self) -> str:
        """How this reads when it's put in front of the model."""
        what = "a project folder at" if self.kind == PROJECT else "the web address"
        return f'"{self.name}" means {what} {self.value}'


@dataclass(frozen=True)
class Turn:
    at: float
    role: str
    content: str

    @property
    def age(self) -> float:
        return time.time() - self.at


# -- settings -----------------------------------------------------------------


def settings() -> dict[str, Any]:
    """The `memory` block from config/jarvis.json.

    Read per call, like `config.actions_config` and for the same reason: turning
    memory off, or shortening the history window after it has confused a turn,
    should take effect on the next utterance rather than the next restart.
    """
    return config.memory_config()


def enabled() -> bool:
    return bool(settings().get("enabled", True))


def db_path() -> Path:
    """Where the database lives. Relative paths hang off the project root."""
    raw = str(settings().get("path") or "memory/jarvis.db")
    path = Path(raw).expanduser()
    return path if path.is_absolute() else config.PROJECT_ROOT / path


# -- the connection -----------------------------------------------------------

# One connection per thread. Jarvis calls into memory from the mic thread, the
# gesture thread and the menu bar timer, and a sqlite3 connection may not be
# shared across threads; a thread-local is simpler than a lock around a single
# connection and cannot deadlock with the ones jarvis.confirm already holds.
_local = threading.local()
_ready: set[str] = set()
_ready_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    path = db_path()
    existing = getattr(_local, "connection", None)
    if existing is not None and getattr(_local, "path", None) == str(path):
        return existing

    if existing is not None:  # the configured path changed under us (tests do this)
        existing.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    connection = sqlite3.connect(str(path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    # WAL so a slow read (recall, on the mic thread) can't block a write, and
    # so an interrupted process leaves a readable database rather than a lock.
    connection.execute("PRAGMA journal_mode=WAL")
    if fresh:
        # This file holds what the user said out loud. Other accounts on the
        # machine have no business reading it, and the default 0644 would let
        # them. Set before the schema is written, so there is no window.
        try:
            path.chmod(0o600)
        except OSError as exc:  # pragma: no cover - depends on the filesystem
            logger.warning("could not restrict permissions on %s: %s", path, exc)

    with _ready_lock:
        if str(path) not in _ready:
            connection.executescript(SCHEMA)
            connection.commit()
            _ready.add(str(path))

    _local.connection = connection
    _local.path = str(path)
    return connection


def close() -> None:
    """Drop this thread's connection. For tests and for a clean shutdown."""
    connection = getattr(_local, "connection", None)
    if connection is not None:
        connection.close()
    _local.connection = None
    _local.path = None


def reset_for_tests() -> None:
    """Forget which databases have been initialised. Only scripts/selftest_*."""
    close()
    with _ready_lock:
        _ready.clear()


# -- normalisation ------------------------------------------------------------


def normalize(name: str) -> str:
    """Fold a spoken name and a stored one into the same shape.

    Deliberately identical to `jarvis.projects._normalize`: "My Project", "my
    project" and "my_project" are one alias, because Whisper picks its own
    capitalisation and word separators and the user has no idea which it chose.
    """
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# -- aliases ------------------------------------------------------------------


def remember_alias(name: str, value: str | Path, kind: str = PROJECT) -> bool:
    """Store `name` -> `value`. Returns whether it stuck.

    Overwrites an existing alias of the same name rather than erroring: the
    user re-teaching a name is them correcting it, and being told "you already
    have one of those" by a voice assistant is useless — there is no way to say
    "yes, replace it" that is shorter than just saying it again.
    """
    name = (name or "").strip()
    value = str(value).strip()
    if not name or not value:
        return False
    if kind not in KINDS:
        logger.warning("unknown alias kind %r — storing as %s", kind, PROJECT)
        kind = PROJECT
    if not enabled():
        logger.info("memory is off — not remembering %r", name)
        return False

    try:
        connection = _connect()
        with connection:
            connection.execute(
                """
                INSERT INTO aliases (name, value, kind, created_at, uses)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(name) DO UPDATE SET value = excluded.value,
                                                kind = excluded.kind
                """,
                (name, value, kind, time.time()),
            )
    except (sqlite3.Error, OSError) as exc:
        logger.error("could not remember %r: %s", name, exc)
        return False
    logger.info("remembered that %r means %s", name, value)
    return True


def forget_alias(name: str) -> bool:
    """Drop an alias. Returns whether one was actually there to drop."""
    if not enabled():
        return False
    try:
        connection = _connect()
        with connection:
            cursor = connection.execute("DELETE FROM aliases WHERE name = ?", ((name or "").strip(),))
        return cursor.rowcount > 0
    except (sqlite3.Error, OSError) as exc:
        logger.error("could not forget %r: %s", name, exc)
        return False


def aliases(kind: str | None = None) -> list[Alias]:
    """Every stored alias, most-used first. The full dump — not what goes in a prompt."""
    if not enabled():
        return []
    try:
        connection = _connect()
        if kind is None:
            rows = connection.execute(
                "SELECT name, value, kind, uses FROM aliases ORDER BY uses DESC, name"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT name, value, kind, uses FROM aliases WHERE kind = ? "
                "ORDER BY uses DESC, name",
                (kind,),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.error("could not read aliases: %s", exc)
        return []
    return [Alias(row["name"], row["value"], row["kind"], row["uses"]) for row in rows]


def alias_map(kind: str | None = None) -> dict[str, str]:
    """{name: value}, for `jarvis.projects` to merge with its config aliases."""
    return {alias.name: alias.value for alias in aliases(kind)}


def lookup(name: str, kind: str | None = None) -> Alias | None:
    """Find one alias by spoken name, tolerant of how it was transcribed.

    Exact match first, then normalized, so "My Project" finds "my project"
    without a stored name ever being ambiguous with a *different* stored name.
    Bumps the use counter, which is what makes `recall`'s cap prefer the aliases
    the user actually leans on.
    """
    name = (name or "").strip()
    if not name or not enabled():
        return None

    key = normalize(name)
    for alias in aliases(kind):
        if alias.name == name or normalize(alias.name) == key:
            _touch(alias.name)
            return alias
    return None


def _touch(name: str) -> None:
    """Record that an alias was used. Best-effort — a failure here changes nothing."""
    try:
        connection = _connect()
        with connection:
            connection.execute(
                "UPDATE aliases SET uses = uses + 1, used_at = ? WHERE name = ?",
                (time.time(), name),
            )
    except (sqlite3.Error, OSError) as exc:
        logger.debug("could not bump use count for %r: %s", name, exc)


# -- preferences --------------------------------------------------------------


def set_preference(key: str, value: Any) -> bool:
    """Store one preference. Keys are dotted config paths where one exists.

    Using the config path as the key ("speech.backends.say.voice") rather than
    inventing a second vocabulary means a preference and the setting it
    overrides are obviously the same thing, and `config.load_config` can apply
    them as one more layer without a translation table to keep in sync.
    """
    key = (key or "").strip()
    if not key or not enabled():
        return False
    try:
        connection = _connect()
        with connection:
            connection.execute(
                "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, "" if value is None else str(value), time.time()),
            )
    except (sqlite3.Error, OSError) as exc:
        logger.error("could not save preference %r: %s", key, exc)
        return False
    logger.info("preference %s = %r", key, value)
    return True


def preference(key: str, default: Any = None) -> Any:
    values = preferences()
    return values.get(key, default)


def preferences() -> dict[str, str]:
    if not enabled():
        return {}
    try:
        connection = _connect()
        rows = connection.execute("SELECT key, value FROM preferences").fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.error("could not read preferences: %s", exc)
        return {}
    return {row["key"]: row["value"] for row in rows}


# Where a per-task-type backend choice is stored. Namespaced away from the
# dotted config paths above because it *isn't* one: there is no
# `backend_for.coding` field in jarvis.json, and pretending there is would put a
# key in PREFERENCE_KEYS that `_set_path` would happily invent.
BACKEND_FOR_PREFIX = "backend_for."


def set_backend_for(task_type: str, backend: str) -> bool:
    """Prefer `backend` for `task_type` — doc 01's "per task type" preference."""
    task_type = (task_type or "").strip().lower()
    if not task_type:
        return False
    return set_preference(f"{BACKEND_FOR_PREFIX}{task_type}", backend)


def backend_for(task_type: str) -> str | None:
    """Which backend the user prefers for this kind of task, if they said.

    Nothing in Jarvis classifies a task into a type yet — that is Phase 7's
    router, and doing it here would be Phase 7's work without Phase 7's design.
    So this is the store half of the requirement, honoured by
    `config.default_backend` the moment something starts passing a type in.
    """
    task_type = (task_type or "").strip().lower()
    if not task_type:
        return None
    return preference(f"{BACKEND_FOR_PREFIX}{task_type}") or None


def clear_preference(key: str) -> bool:
    if not enabled():
        return False
    try:
        connection = _connect()
        with connection:
            cursor = connection.execute("DELETE FROM preferences WHERE key = ?", (key,))
        return cursor.rowcount > 0
    except (sqlite3.Error, OSError) as exc:
        logger.error("could not clear preference %r: %s", key, exc)
        return False


# -- conversation history -----------------------------------------------------


def remember_turn(role: str, content: str) -> bool:
    """Append one turn to the rolling window.

    `content` is redacted on the way in, not on the way out. That ordering is
    the whole safety property: if the process dies between the write and a
    later read, what is on disk is already safe — there is no window in which
    a plaintext password exists in this file, and no future caller who can
    forget to ask.

    What is stored is the *masked* sentence ("log in as alice, my [credential
    omitted]"), not nothing at all. Two reasons, and the first is doc 04's own
    precedent: this is exactly the string Phase 4 already writes to
    logs/jarvis.log and already sends to the cloud model as the live transcript,
    so refusing it here would be a stricter rule than the one the credential
    design settled on, applied in the one place it costs something. And it costs
    a lot — a spoken login is four turns long ("open the portal", "my username
    is...", "my password is...", "yes"), and dropping the turns with credentials
    in them would leave the window holding exactly the half of the exchange that
    doesn't explain what is going on.
    """
    content = (content or "").strip()
    if not content or not enabled():
        return False
    if role not in ("user", "assistant"):
        logger.warning("not storing a %r turn — only user and assistant are kept", role)
        return False

    safe = redact.redact_secrets(content)
    if safe != content:
        logger.info("a credential was masked out of a turn before storing it")

    try:
        connection = _connect()
        with connection:
            connection.execute(
                "INSERT INTO turns (at, role, content) VALUES (?, ?, ?)",
                (time.time(), role, safe),
            )
            # Trim as we go rather than in a background sweep: the window is
            # tiny, the delete is cheap, and it means the file can't grow
            # unboundedly if Jarvis is never restarted.
            connection.execute(
                "DELETE FROM turns WHERE id NOT IN "
                "(SELECT id FROM turns ORDER BY id DESC LIMIT ?)",
                (max(1, int(settings().get("history_turns", 6))) * 4,),
            )
    except (sqlite3.Error, OSError) as exc:
        logger.error("could not store a turn: %s", exc)
        return False
    return True


def history() -> list[Turn]:
    """The recent conversation, oldest first — only what's still fresh.

    Two limits, both load-bearing:

    - **A turn count** (`memory.history_turns`), because these go into every
      prompt and a small model handed twenty turns of context answers the wrong
      one of them.
    - **A TTL** (`memory.history_ttl_seconds`), because this is the exact trap
      `jarvis.followup` and `jarvis.confirm` each spent a docstring warning
      about: something said an hour ago must not silently become context for
      something said now. A conversation you walked away from is over.
    """
    if not enabled():
        return []
    limit = max(0, int(settings().get("history_turns", 6)))
    if limit == 0:
        return []
    ttl = float(settings().get("history_ttl_seconds", 900))
    try:
        connection = _connect()
        rows = connection.execute(
            "SELECT at, role, content FROM turns WHERE at >= ? ORDER BY id DESC LIMIT ?",
            (time.time() - ttl, limit),
        ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.error("could not read history: %s", exc)
        return []
    return [Turn(row["at"], row["role"], row["content"]) for row in reversed(rows)]


def clear_history() -> None:
    """Forget the conversation. The menu bar's "Forget this conversation"."""
    if not enabled():
        return
    try:
        connection = _connect()
        with connection:
            connection.execute("DELETE FROM turns")
    except (sqlite3.Error, OSError) as exc:
        logger.error("could not clear history: %s", exc)


# -- selective retrieval ------------------------------------------------------


def relevant_aliases(transcript: str) -> list[Alias]:
    """The aliases worth putting in front of the model for *this* sentence.

    An alias is relevant when its name is in what was just said — matched on the
    normalized forms, so "open my project" finds "my project" through whatever
    spacing and capitalisation Whisper chose. Nothing else is offered, however
    many are stored; see the module docstring on why a dump is the wrong answer.

    Ties are broken by how often an alias has been used, then by length: if both
    "project jarvis" and "jarvis" match, the longer name is the more specific
    reading of the sentence and goes first.
    """
    spoken = normalize(transcript)
    if not spoken:
        return []
    matched = [alias for alias in aliases() if normalize(alias.name) and normalize(alias.name) in spoken]
    matched.sort(key=lambda alias: (-alias.uses, -len(alias.name)))
    cap = max(0, int(settings().get("max_facts", 6)))
    return matched[:cap]


def recall(transcript: str) -> list[str]:
    """Memory for one utterance, as plain sentences to hand the model.

    Returns [] when there is nothing relevant — which is the common case, and
    the reason this is cheap. The caller decides how to present them; this
    decides *what* is worth presenting.
    """
    return [alias.as_fact() for alias in relevant_aliases(transcript)]


def describe() -> str:
    """One line for a log or a menu bar: what Jarvis currently remembers."""
    if not enabled():
        return "memory is off"
    stored = aliases()
    projects_count = sum(1 for alias in stored if alias.kind == PROJECT)
    portals = len(stored) - projects_count
    return (
        f"{projects_count} project alias(es), {portals} portal alias(es), "
        f"{len(preferences())} preference(s), {len(history())} turn(s) in the window"
    )
