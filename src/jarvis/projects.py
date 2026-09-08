"""
Spoken project name -> directory on disk — Phase 3.

`run_claude_code` needs a working directory, but what arrives from the mic is
"project jarvis", not `/Users/you/Desktop/project_jarvis`. This module is the
bridge, and it is deliberately the *only* place a path is chosen: the model
never gets to hand a raw filesystem path straight to a subprocess.

Two rules shape it:

- **Search, don't trust.** A spoken name is matched against the directories
  actually present under the configured roots (`actions.projects.roots`), so a
  mis-transcribed name fails to resolve rather than resolving to something
  unexpected. Explicit aliases (`actions.projects.aliases`) win over the search.
- **Stay inside the roots.** Even a fully-qualified path is only accepted if it
  sits under one of them. That's the containment boundary for Phase 3's one
  genuinely powerful tool — Claude Code can edit whatever is in the directory
  it's launched in, so which directory that can be is not the model's decision.

Aliases here are the manual, config-file version of what doc 02's memory store
("my project" -> path) does automatically in Phase 6. When that lands, it
should feed this resolver rather than replace it — the containment check has to
survive the upgrade.

**When it doesn't know** (round 2, 2026-09-08): rather than giving up, Jarvis
asks out loud where the project is and listens for the answer —
`resolve_spoken` is the other half of that, turning "it's in Documents slash
client work" into a real directory, and the answer is written back as an alias
so the question is asked exactly once per project. Parsing a filesystem path out
of a transcript is the weak link in that flow, so it never trusts the words: it
*walks* the filesystem segment by segment, matching each spoken segment against
directories that actually exist.
"""
from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path

from jarvis import config

logger = logging.getLogger("jarvis.projects")


class ProjectError(ValueError):
    """No single directory matched. The message is written to be *spoken back*."""


# Directory names that are never a "project", so they don't pollute the match
# candidates (~/Desktop in particular is full of these).
_SKIP_NAMES = {"library", "applications", "public", "movies", "music", "pictures"}


def _normalize(name: str) -> str:
    """Fold a spoken name and a directory name into the same shape.

    "Project Jarvis", "project_jarvis" and "project-jarvis" all become
    "projectjarvis". Whisper picks its own word separators, so matching on
    anything finer than this loses to punctuation it invented.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def roots() -> list[Path]:
    """The directories that may contain a project, from config."""
    configured = config.actions_config("projects").get("roots", [])
    found: list[Path] = []
    for entry in configured:
        path = Path(entry).expanduser()
        if path.is_dir():
            found.append(path.resolve())
        else:
            logger.debug("project root %s does not exist — skipping", path)
    return found


def aliases() -> dict[str, str]:
    return config.actions_config("projects").get("aliases", {}) or {}


def _is_contained(path: Path, allowed: list[Path]) -> bool:
    """True if `path` is one of `allowed`'s children (or one of them itself)."""
    return any(path == root or root in path.parents for root in allowed)


def candidates() -> dict[str, Path]:
    """Every directory that could be named out loud: {normalized name: path}.

    Immediate children of each root only. Recursing would make "jarvis" match a
    `node_modules/jarvis` five levels down, which is exactly the kind of quiet
    wrong answer this module exists to prevent.
    """
    found: dict[str, Path] = {}
    for root in roots():
        try:
            entries = sorted(root.iterdir())
        except OSError as exc:
            logger.warning("could not list project root %s: %s", root, exc)
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name.lower() in _SKIP_NAMES:
                continue
            found.setdefault(_normalize(entry.name), entry)
    return found


def resolve(name: str) -> Path:
    """Turn a spoken project name (or a path) into a directory. Raises ProjectError.

    Resolution order: explicit alias, then a literal path, then a search of the
    roots — exact normalized match, then unique prefix/substring, then fuzzy.
    Ambiguity is an error rather than a guess: launching Claude Code against the
    wrong repository is not a mistake you notice from a spoken one-liner.
    """
    name = (name or "").strip()
    if not name:
        raise ProjectError("I need to know which project you mean.")

    allowed = roots()
    if not allowed:
        raise ProjectError(
            "None of my configured project folders exist — check "
            "actions.projects.roots in config/jarvis.json."
        )

    alias_target = aliases().get(name) or aliases().get(name.lower())
    if alias_target:
        path = Path(alias_target).expanduser().resolve()
        if not path.is_dir():
            raise ProjectError(f"The alias for {name} points at {path}, which isn't a folder.")
        return path  # an alias is an explicit human decision — it may sit outside the roots

    if name.startswith(("/", "~", "./")):
        path = Path(name).expanduser()
        try:
            path = path.resolve()
        except OSError as exc:
            raise ProjectError(f"I couldn't make sense of the path {name}: {exc}") from exc
        if not path.is_dir():
            raise ProjectError(f"{path} isn't a folder I can find.")
        if not _is_contained(path, allowed):
            raise ProjectError(
                f"{path} is outside the folders I'm allowed to work in "
                f"({', '.join(str(root) for root in allowed)})."
            )
        return path

    options = candidates()
    if not options:
        raise ProjectError("I couldn't find any projects in my configured folders.")

    key = _normalize(name)
    if key in options:
        return options[key]

    partial = [value for candidate, value in options.items() if key and key in candidate]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise ProjectError(_ambiguous(name, partial))

    close = difflib.get_close_matches(key, list(options), n=3, cutoff=0.7)
    if len(close) == 1:
        logger.info("resolved %r to %s by fuzzy match", name, options[close[0]])
        return options[close[0]]
    if len(close) > 1:
        raise ProjectError(_ambiguous(name, [options[match] for match in close]))

    raise ProjectError(
        f"I couldn't find a project called {name}. I know about: "
        f"{', '.join(sorted(path.name for path in options.values())[:12])}."
    )


def _ambiguous(name: str, matches: list[Path]) -> str:
    names = ", ".join(sorted(path.name for path in matches))
    return f"{name} matches more than one project ({names}) — which one?"


# -- answering "where is it?" out loud --------------------------------------

# Words people say around a location that aren't part of it.
_FILLER = {
    "it's", "its", "it", "is", "in", "at", "on", "the", "my", "under", "inside",
    "folder", "directory", "dir", "called", "named", "located", "you'll", "find",
    "look", "there", "a", "of", "path", "to", "please", "um", "uh",
}

# What Whisper writes when you say punctuation out loud.
_SPOKEN_PUNCTUATION = {
    "slash": "/", "forwardslash": "/", "backslash": "/",
    "dot": ".", "period": ".", "tilde": "~",
    "dash": "-", "hyphen": "-", "minus": "-", "underscore": "_",
}

def _forbidden() -> set[Path]:
    """Directories nothing is ever launched against, however clearly you said them.

    A coding agent pointed at your whole home directory (or /) because one word
    was misheard is the failure this module exists to prevent. Computed per call
    rather than at import so it can't go stale.
    """
    return {
        Path("/"), Path.home(), Path("/System"), Path("/Library"), Path("/usr"),
        Path("/etc"), Path("/private"), Path("/Applications"), Path("/Volumes"),
    }


def _segments(text: str) -> list[str]:
    """Spoken location -> path segments, e.g. "in Documents slash client work"
    -> ["documents", "client work"]."""
    words = re.split(r"[\s,]+", (text or "").strip().lower())
    parts: list[str] = []
    for word in words:
        word = word.strip(".!?;:")
        if not word:
            continue
        if word in _SPOKEN_PUNCTUATION:
            parts.append(_SPOKEN_PUNCTUATION[word])
        elif word in _FILLER:
            continue
        else:
            parts.append(word)

    joined = " ".join(parts)
    # Glue the punctuation back onto its neighbours: "documents / client work"
    # -> "documents/client work".
    joined = re.sub(r"\s*([/.~_-])\s*", r"\1", joined)
    return [segment for segment in joined.split("/") if segment]


def _child_matching(parent: Path, segment: str, *, fuzzy: bool = True) -> Path | None:
    """The one subdirectory of `parent` whose name matches a spoken `segment`."""
    key = _normalize(segment)
    if not key:
        return None
    try:
        entries = [entry for entry in parent.iterdir() if entry.is_dir()]
    except OSError:
        return None

    exact = [entry for entry in entries if _normalize(entry.name) == key]
    if len(exact) == 1:
        return exact[0]
    partial = [entry for entry in entries if key in _normalize(entry.name)]
    if len(partial) == 1:
        return partial[0]
    if not fuzzy:
        return None
    close = difflib.get_close_matches(key, [_normalize(e.name) for e in entries], n=2, cutoff=0.8)
    if len(close) == 1:
        return next(entry for entry in entries if _normalize(entry.name) == close[0])
    return None


def _walk_words(start: Path, words: list[str], *, fuzzy: bool = True) -> tuple[Path, int]:
    """Greedily match `words` against real directories under `start`.

    Returns the deepest directory reached and how many words that took. The
    greed is the point: spoken locations don't say where one folder name ends
    and the next begins ("in Documents under client work" is
    `Documents/client work`, but "in documents adobe" is `Documents/Adobe`), so
    at each level it tries the longest run of words that names an existing
    directory and works down from there. Nothing is guessed — every step has to
    match something that's actually on disk.

    A partial match is returned rather than discarded, because "it's in
    Documents, the one with the silly name" should still get us to Documents;
    `resolve_spoken` then searches inside it for the project by name.
    """
    best = (start, 0)
    for length in range(len(words), 0, -1):
        segment = " ".join(words[:length])
        child = Path.home() if segment == "~" else _child_matching(start, segment, fuzzy=fuzzy)
        if child is None:
            continue
        deeper, consumed = _walk_words(child, words[length:], fuzzy=fuzzy)
        if length + consumed > best[1]:
            best = (deeper, length + consumed)
        if best[1] == len(words):
            break
    return best


def _walk(start: Path, parts: list[str], *, fuzzy: bool = True) -> tuple[Path | None, bool]:
    """Follow a spoken location, where `parts` were separated by a spoken "slash".

    Returns (deepest directory reached, whether every spoken word was matched).
    The second half is what lets `resolve_spoken` prefer a walk that explained
    the whole sentence over one that got partway and gave up.
    """
    current = start
    moved = False
    complete = True
    for part in parts:
        if part == "~":
            current = Path.home()
            moved = True
            continue
        words = part.split()
        reached, consumed = _walk_words(current, words, fuzzy=fuzzy)
        if consumed:
            current = reached
            moved = True
        if consumed < len(words):
            complete = False
            break
    if not moved or current == start:
        return None, False
    return current, complete


def _search_for(parent: Path, name: str, depth: int = 2) -> Path | None:
    """Look for a directory called `name` under `parent`, breadth-first."""
    key = _normalize(name)
    frontier = [parent]
    for _ in range(depth):
        next_frontier: list[Path] = []
        for directory in frontier:
            try:
                children = [entry for entry in directory.iterdir() if entry.is_dir()]
            except OSError:
                continue
            for child in children:
                if child.name.startswith("."):
                    continue
                if _normalize(child.name) == key or key in _normalize(child.name):
                    return child
                next_frontier.append(child)
        frontier = next_frontier
    return None


def resolve_spoken(project: str, answer: str) -> Path | None:
    """Work out a directory from a *spoken* answer to "where is that project?".

    Deliberately does not parse the transcript into a path and trust it. It
    walks the real filesystem one spoken segment at a time, so a segment that
    was misheard fails to match rather than producing a plausible-looking path
    to somewhere that isn't what you meant. Returns None if nothing lines up —
    which is a perfectly good outcome, and better than a confident wrong answer.
    """
    segments = _segments(answer)
    if not segments:
        return None

    starts = [Path.home(), *roots()]
    if answer.strip().startswith("/"):
        starts.insert(0, Path("/"))

    # Exact/substring matching first, fuzzy only if that explained nothing fully.
    # Fuzzy matching inside the greedy walk will happily swallow a word it
    # shouldn't: asked for "desktop project jarvis src", it scores "project
    # jarvis src" against `project_jarvis` at 0.93, consumes all three words and
    # lands on the repo root — quietly dropping the `src` you actually named.
    # But it can't simply be dropped either, or "projekt jarvis" stops resolving,
    # and mishearing a project name is the normal case here. Hence: prefer a walk
    # that accounts for *every* word, and only widen to fuzzy when no exact walk
    # does. A partial walk is still returned as a last resort, because landing on
    # the containing folder is useful — `resolve_spoken` searches inside it.
    complete_walks: list[Path] = []
    partial_walks: list[Path] = []
    for allow_fuzzy in (False, True):
        for start in starts:
            reached, complete = _walk(start, segments, fuzzy=allow_fuzzy)
            if reached is None:
                continue
            (complete_walks if complete else partial_walks).append(reached)
        if complete_walks:
            break

    found = next(iter(complete_walks + partial_walks), None)
    if found is None:
        return None

    # They may have named the *containing* folder ("it's in Documents") rather
    # than the project itself, so look inside for what was actually asked about.
    if _normalize(found.name) != _normalize(project):
        inside = _search_for(found, project)
        if inside is not None:
            found = inside

    found = found.resolve()
    if not found.is_dir() or found in _forbidden():
        logger.warning("refusing spoken location %s", found)
        return None
    return found
