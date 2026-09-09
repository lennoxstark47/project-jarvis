"""
Jarvis's tool contract — Phases 2-6.

This module is the *entire* set of things Jarvis is able to do. One schema per
tool, described in backend-neutral JSON Schema; each backend in
`jarvis.router` converts these into whatever shape its API wants. That's the
"fixed small tool list" from docs/01-PHASE_PLAN.md — it grows only when a phase
explicitly adds a tool, never opportunistically.

- Phase 2 shipped `open_url` / `open_app`: the safest possible actions, both
  shelling out to macOS's `open`, which hands its argument to LaunchServices
  rather than a shell, so a mis-transcribed command can't become code execution.
- Phase 3 adds `run_claude_code` (the sub-agent — jarvis.claude_code) and
  `open_portal` (the driveable browser — jarvis.browser).
- Phase 4 adds `fill_login_form` (jarvis.login). Note what it does *not* add: a
  way to press the login button. Submitting is a confirmed action
  (jarvis.confirm) reached only by a human answer, because this tool list is
  precisely the surface a mis-transcribed sentence can reach, and doc 04's
  point 4 puts a person between "filled" and "submitted" on purpose.
- Phase 6 adds `remember` and `open_project` (jarvis.memory, jarvis.projects) —
  the two halves of doc 01's "open my project works days later": one to teach a
  name, one to use it. `remember` is the first tool that writes something
  lasting, so it stores a *resolved* value rather than the words it was given:
  a project name is put through jarvis.projects (and its containment check)
  before anything is saved, so a misheard sentence fails at the moment it's
  said rather than becoming a permanent wrong answer.

The bodies of the two Phase 3 tools are in their own modules; what lives here
is the contract and the dispatch, so the list of what Jarvis can do stays
readable in one screen.

**Status callbacks.** `run_claude_code` can take minutes. Every handler
therefore accepts an optional `on_status` callable and the slow ones call it as
they go, which is what lets the menu bar show progress instead of looking hung
(doc 02's output layer). Handlers that finish instantly accept it and ignore it,
so dispatch stays uniform.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("jarvis.tools")

StatusFn = Callable[[str], None]

# How long to wait on `open`. It returns as soon as LaunchServices accepts the
# request (it doesn't wait for the app to finish launching), so this only trips
# on something genuinely wedged.
OPEN_TIMEOUT = 10

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "open_url",
        "description": (
            "Open a web page in the user's default browser, for the user to read. "
            "Use this whenever the user asks to open, go to, visit, pull up, or show "
            "a website, web page, or URL.\n\n"
            "Do NOT use this when the user wants to log in to the page, or wants "
            "Jarvis to fill anything in on it — a page opened here is one Jarvis "
            "cannot see or type into afterwards. Use open_portal for those.\n\n"
            "Never call this and open_portal for the same page in one turn. That "
            "opens the same site twice in two different windows, which is not what "
            "anyone asked for: pick the one tool that matches what the user wants "
            "to do with the page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "The full URL to open, including the scheme, e.g. "
                        "'https://github.com'. If the user said a bare domain like "
                        "'github.com', prefix it with 'https://'."
                    ),
                }
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_app",
        "description": (
            "Launch a macOS application by name. Use this whenever the user asks to "
            "open, start, launch, or bring up an app on their Mac (e.g. 'Safari', "
            "'Terminal', 'Claude Code', 'Visual Studio Code'). Prefer open_url when "
            "the thing being opened is a website. Note that \"open Claude Code\" "
            "belongs here — it means launch that app, not run a coding task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "The application's name as it appears in /Applications, "
                        "e.g. 'Safari' or 'Visual Studio Code'. Spoken names are "
                        "often approximate — use the real app name you believe the "
                        "user means."
                    ),
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_claude_code",
        "description": (
            "Hand a coding task to Claude Code — the user's AI coding agent — inside "
            "one of their projects, and report back what it found or changed. Use "
            "this whenever the request is about code, files or documents inside one "
            "of the user's projects: finding a bug, investigating why something "
            "breaks, explaining or changing code, reading what a document says. It "
            "can take several minutes, which is expected. Do not use it to open an "
            "app or a web page.\n\n"
            "The user saying the words \"Claude Code\" is NOT by itself a reason "
            "to use this tool. \"Open Claude Code\" means launch the application "
            "— that is open_app. Use this tool only when there is an actual task "
            "to carry out inside a project, and never invent one: if the user "
            "named no task, they did not ask for this tool.\n\n"
            "Call this even when you have never heard of the project the user named "
            "and have no idea where it is. Turning a spoken project name into a "
            "folder is Jarvis's job, not yours: it searches the user's project "
            "folders, and if it still can't find it, it asks the user out loud and "
            "handles the answer. Never reply that you cannot do something because "
            "you don't know where a project is, or because you have no tool for it "
            "— call this tool and let it resolve."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": (
                        "Which project to work in, as the user named it out loud "
                        "(e.g. 'project jarvis', 'the billing service', 'my thesis "
                        "notes'). A project name, not a file path — Jarvis matches "
                        "it against the user's project folders itself. Pass whatever "
                        "the user called it even if it means nothing to you; an "
                        "unfamiliar name is normal and is not a reason to skip the "
                        "tool."
                    ),
                },
                "task": {
                    "type": "string",
                    "description": (
                        "The complete task to give Claude Code, written out as an "
                        "instruction. Claude Code cannot hear the user and sees "
                        "nothing except this text, so include every detail of the "
                        "problem the user described — symptoms, file or function "
                        "names, error messages — rather than a short paraphrase."
                    ),
                },
            },
            "required": ["project", "task"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fill_login_form",
        "description": (
            "Type the user's username and password into the login form on the page "
            "already open in Jarvis's browser. Use this when the user wants to log "
            "in to a site or portal — including when they have just said their "
            "username and password out loud. The page must already be open: call "
            "open_portal first if it isn't.\n\n"
            "You will never see the credential itself. If the user's message says a "
            "credential was captured under a credential_ref, pass that ref through "
            "exactly as written; Jarvis resolves it to the real value locally. If "
            "there is no ref, call this anyway with just the site — Jarvis looks the "
            "site up in the user's macOS Keychain, where a previous login may have "
            "been saved.\n\n"
            "This fills the form and stops. It never submits — Jarvis asks the user "
            "out loud first, and their spoken answer is what submits it. After "
            "calling this, say what the tool told you to say and stop; do not call "
            "another tool, and never ask the user to repeat their password."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "site": {
                    "type": "string",
                    "description": (
                        "Which site this login is for: its URL or domain if you know "
                        "it, otherwise the words the user used for it. Never copy an "
                        "example from these instructions, and never guess a site the "
                        "user hasn't mentioned — Jarvis files the credential under the "
                        "page it actually types into, so a wrong name here is only "
                        "wrong in what it says back to the user."
                    ),
                },
                "credential_ref": {
                    "type": "string",
                    "description": (
                        "The credential_ref from the user's message (e.g. "
                        "'pending_dictation_1'), copied exactly. Leave it out if the "
                        "message didn't contain one. Never invent a ref, and never "
                        "put an actual username or password here."
                    ),
                },
            },
            # Nothing is required. Jarvis files the credential under the page it
            # actually types into, so it needs neither argument to do its job —
            # and a required argument the model can't supply is just a wasted
            # step: live on 2026-09-08 a model that had the ref but no site name
            # burned a turn on "missing 1 required positional argument: 'site'"
            # and then invented a site to get past it.
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_portal",
        "description": (
            "Open a web page in Jarvis's own automated browser, which Jarvis can see "
            "and act on afterwards. Use this when the user wants Jarvis to *do* "
            "something on the page — log in to a portal, fill something in — rather "
            "than just look at it. For a page the user only wants to read, use "
            "open_url instead.\n\n"
            "Anything involving logging in starts here: fill_login_form can only type "
            "into a page that was opened with this tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "The full URL of the portal or page, including the scheme, "
                        "e.g. 'https://portal.example.com'."
                    ),
                }
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remember",
        "description": (
            "Permanently remember what a name the user uses means, so they never have "
            "to spell it out again. Use this whenever the user tells you what "
            "something of theirs *is* — 'my project is project jarvis', 'remember the "
            "billing portal is billing.example.com', 'call that one my thesis'.\n\n"
            "Only for lasting facts about names. Do not use it to take notes, to "
            "remember something for later in this conversation, or for anything the "
            "user did not ask you to keep."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "What the user calls it, in their own words — 'my project', "
                        "'the billing portal'. Not a tidied-up version."
                    ),
                },
                "value": {
                    "type": "string",
                    "description": (
                        "What it refers to: either a project the user has on their "
                        "Mac (name it as they said it, e.g. 'project jarvis') or a "
                        "web address (e.g. 'billing.example.com'). Jarvis works out "
                        "which and checks it before saving anything."
                    ),
                },
            },
            "required": ["name", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_project",
        "description": (
            "Open one of the user's project folders on their Mac, in Finder. Use this "
            "when they ask to open a project, a folder, or something they have a "
            "remembered name for ('open my project'). For a website use open_url; to "
            "have Claude Code *work* on a project use run_claude_code instead — this "
            "tool only shows the folder."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "The project as the user named it — a remembered name like "
                        "'my project', or the folder's own name like 'project jarvis'."
                    ),
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = [spec["name"] for spec in TOOL_SPECS]

# Voice transcripts arrive spoken, so URLs come through as "github dot com" or
# "github.com" rather than anything with a scheme. Normalising here (not in the
# prompt alone) means a backend that skips the instruction still produces a
# working URL.
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def normalize_url(url: str) -> str:
    url = url.strip()
    url = re.sub(r"\s+dot\s+", ".", url, flags=re.IGNORECASE)
    url = url.replace(" ", "")
    if not url:
        return url
    if not _SCHEME_RE.match(url):
        url = "https://" + url
    return url


# A domain as Whisper writes one down: "the-internet.herokuapp.com/login",
# "github.com". Requires a dot and a plausible TLD so ordinary sentences don't
# match, and stops at whitespace.
_SPOKEN_URL_RE = re.compile(
    r"\b(?:https?://)?(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,}(?:/[^\s,;]*)?",
    re.IGNORECASE,
)

# Words that end a sentence and look like a TLD. Without this, "open it. Now"
# and "e.g. github" produce phantom domains.
_NOT_DOMAINS = {"e.g", "i.e", "etc", "vs", "a.m", "p.m"}


# "github dot com slash login" is how a URL sounds out loud, and Whisper writes
# it down that way. Folded into punctuation before matching, or the domain isn't
# there to find at all.
#
# The dash rule swallows an adjacent hyphen because Whisper does both at once:
# "the dash internet" came back as "the dash-internet" — the word survived *and*
# became punctuation, which without this leaves the literal word "dash" sitting
# in the domain.
_SPOKEN_PUNCTUATION = (
    (re.compile(r"\s+dot\s+", re.IGNORECASE), "."),
    (re.compile(r"\s+slash\s+", re.IGNORECASE), "/"),
    (re.compile(r"\s*\b(?:dash|hyphen)\b\s*-?", re.IGNORECASE), "-"),
)

# A domain has to end in something that is actually a top-level domain. Every
# two-letter label is a country code, so those are taken on trust; the rest is a
# short list of what people say out loud.
#
# This is not pedantry — it's the guard that stops this whole mechanism doing
# harm. Live on 2026-09-08, Whisper split "herokuapp" into "heroku app", the
# extractor happily read "the-internet.heroku" as a domain, and Jarvis
# "corrected" the model's wrong URL into a worse one. A correction that can't
# tell a domain from a fragment has no business overruling anything.
_KNOWN_TLDS = {
    "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "app",
    "dev", "io", "ai", "co", "tv", "cc", "xyz", "online", "site", "shop",
    "store", "cloud", "tech", "blog", "news", "live", "life", "world", "email",
    "page", "link", "wiki", "design", "studio", "space", "group", "work",
    "today", "run", "sh", "gg", "fm", "ly", "one", "pro", "name", "mobi",
}


def _has_real_tld(host: str) -> bool:
    tld = host.rsplit(".", 1)[-1].lower()
    return len(tld) == 2 or tld in _KNOWN_TLDS


def spoken_urls(transcript: str) -> list[str]:
    """Every URL the user actually said, as they said it.

    Note the limit this cannot get past: a hyphen that was never spoken can't be
    recovered. "the internet dot herokuapp dot com" is genuinely ambiguous
    between "theinternet." and "the-internet." — say "dash" if the domain has
    one, or let Phase 6's aliases remember the whole URL for you.
    """
    text = transcript or ""
    for pattern, replacement in _SPOKEN_PUNCTUATION:
        text = pattern.sub(replacement, text)

    found = []
    for match in _SPOKEN_URL_RE.finditer(text):
        raw = match.group(0).rstrip(".,;:!?")
        host = raw.split("://", 1)[-1].split("/", 1)[0].lower()
        if host in _NOT_DOMAINS or not _has_real_tld(host):
            continue
        found.append(raw)
    return found


def prefer_spoken_url(model_url: str, transcript: str) -> str:
    """Use the URL the user said, not the one the model retyped from memory.

    Small models are unreliable at copying a domain out of a sentence, and the
    failure is silent — you land on a real page that isn't yours. Live on
    2026-09-08, "the-internet.herokuapp.com/login" came back from the model
    twice as `login.herokuapp.com` and once as `the-intent-internet.herokuapp.com`,
    each time opening a page with no login form on it.

    So the same rule the rest of Jarvis uses applies here too: anything the user
    said themselves is resolved locally rather than round-tripped through a
    model. The model still decides *whether* to open a page, and still supplies
    the URL when the user only named a site ("open GitHub") — this only
    overrules it when the user spelled a domain out loud and the model came back
    with a different one. Two spoken URLs in one sentence is ambiguous, so it
    defers rather than guessing.
    """
    spoken = spoken_urls(transcript)
    if len(spoken) != 1:
        return model_url

    wanted = normalize_url(spoken[0])
    if _host(wanted) and _host(wanted) == _host(normalize_url(model_url)):
        return model_url  # the model got it right; keep its path
    logger.info("using the URL you said (%s), not the model's (%s)", wanted, model_url)
    return wanted


def _host(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0].lower().removeprefix("www.")


def _run_open(args: list[str], *, dry_run: bool) -> tuple[bool, str]:
    """Run `open <args>`. Returns (succeeded, detail-for-the-model)."""
    if dry_run:
        logger.info("[dry run] would run: open %s", " ".join(args))
        return True, "(dry run — nothing was actually opened) "
    try:
        result = subprocess.run(
            ["open", *args],
            capture_output=True,
            text=True,
            timeout=OPEN_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("open %s failed: %s", args, exc)
        return False, f"could not run open: {exc}"

    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        logger.warning("open %s failed: %s", args, message)
        return False, message
    return True, ""


def open_url(url: str, *, dry_run: bool = False, on_status: StatusFn | None = None) -> str:
    """Open a page for the user to read.

    When Jarvis drives the browser you actually use (`actions.browser.engine` =
    system-firefox), this goes through that same driven browser rather than
    handing the URL to macOS. That looks like a detail and is not: `open` starts
    *your* Firefox, which takes the profile lock, and geckodriver then cannot
    drive the browser it needs for a login. Live on 2026-09-08 Jarvis deadlocked
    itself exactly that way — turn one opened GitHub with this tool, and two
    turns later fill_login_form couldn't get at the browser the page was sitting
    in. With one real browser there is only one right way to open a page in it.

    If the driven browser can't be had (usually: your Firefox is already open),
    it falls back to macOS `open`, which lands the page in that same window —
    the best available outcome for something the user only wants to read.
    """
    from jarvis import browser

    url = normalize_url(url)
    logger.info("tool open_url(%r)", url)

    if not dry_run and browser.engine() in browser.SYSTEM_ENGINES:
        try:
            return browser.open_portal(url, on_status=on_status)
        except browser.BrowserError as exc:
            logger.info("couldn't use the driven browser (%s) — opening normally", exc)

    ok, detail = _run_open([url], dry_run=dry_run)
    return f"{detail}Opened {url}" if ok else f"Could not open {url}: {detail}"


def open_app(name: str, *, dry_run: bool = False, on_status: StatusFn | None = None) -> str:
    name = name.strip()
    logger.info("tool open_app(%r)", name)
    # `open -a` both launches a cold app and brings a running one to the front,
    # so the osascript `activate` the phase plan mentions as an alternative buys
    # nothing here — and osascript takes a *script*, where `open` takes an
    # argument, so staying on `open` keeps a mis-transcribed app name from being
    # anything more dangerous than a name that doesn't exist.
    ok, detail = _run_open(["-a", name], dry_run=dry_run)
    return f"{detail}Opened {name}" if ok else f"Could not open {name}: {detail}"


def run_claude_code(
    project: str,
    task: str,
    *,
    directory: "Path | None" = None,
    dry_run: bool = False,
    on_status: StatusFn | None = None,
) -> str:
    """Resolve the project name, then hand the task to the Claude Code sub-agent.

    `directory` skips resolution for a caller that has already worked the folder
    out through a trusted route — specifically jarvis.agent, after you said out
    loud where the project is. It is keyword-only and deliberately **not** in
    this tool's JSON Schema, and `execute()` drops arguments that aren't in the
    schema, so a model cannot reach it and hand a subprocess a path of its own
    choosing. That's the containment boundary; this is the one door through it,
    and a person is what opens it.

    Imported lazily so that a broken/missing `claude` CLI can only ever affect
    the command that asked for it, the way the router treats vendor SDKs.
    """
    from jarvis import claude_code, followup, projects

    logger.info("tool run_claude_code(project=%r, task=%r)", project, task)
    if directory is not None:
        return _hand_over(directory, task, dry_run=dry_run, on_status=on_status)

    try:
        directory = projects.resolve(project)
    except projects.ProjectError as exc:
        # A resolution failure is a *question for the user* ("where is it?"),
        # not a crash. Park the request so the next thing they say can be the
        # answer (jarvis.followup), and hand the model the wording to ask with.
        followup.ask_where(project, task)
        return (
            f"{exc} Ask the user where it is, in one short sentence, and say nothing else — "
            f"their next words will be the answer and I'll handle it."
        )

    return _hand_over(directory, task, dry_run=dry_run, on_status=on_status)


def _hand_over(
    directory: "Path", task: str, *, dry_run: bool, on_status: StatusFn | None
) -> str:
    from jarvis import claude_code

    try:
        return claude_code.run(directory, task, dry_run=dry_run, on_status=on_status)
    except claude_code.ClaudeCodeError as exc:
        return str(exc)


def open_portal(url: str, *, dry_run: bool = False, on_status: StatusFn | None = None) -> str:
    from jarvis import browser

    try:
        return browser.open_portal(url, dry_run=dry_run, on_status=on_status)
    except browser.BrowserError as exc:
        return f"I couldn't open that in my own browser: {exc}"


def fill_login_form(
    site: str = "",
    credential_ref: str = "",
    *,
    dry_run: bool = False,
    on_status: StatusFn | None = None,
) -> str:
    """Fill the open login form. The credential is resolved locally — see jarvis.login."""
    from jarvis import login

    # Note what is *not* logged: no ref-to-value lookup, no username, nothing
    # that would put a credential a redaction filter has to catch back into the
    # log. The site name is the useful part anyway.
    logger.info("tool fill_login_form(site=%r, ref=%s)", site, credential_ref or "none")
    return login.fill_login_form(
        site, credential_ref, dry_run=dry_run, on_status=on_status
    )


def remember(
    name: str = "",
    value: str = "",
    *,
    dry_run: bool = False,
    on_status: StatusFn | None = None,
) -> str:
    """Learn that `name` means `value`, for good — Phase 6's half of the DoD.

    Two things happen before anything is written, and both are the point:

    - **The kind is decided here, not by the model.** A value that looks like a
      web address is stored as one; everything else is treated as a project.
      Asking the model to classify would put a schema field between the user and
      a permanent fact for no gain — the string itself says which it is.
    - **The value is resolved before it is stored.** A project name goes through
      `jarvis.projects.resolve`, so what lands in the database is a directory
      that exists, inside the roots Jarvis is allowed to work in. A misheard
      name therefore fails now, out loud, instead of being remembered wrong and
      quietly opening the wrong folder in a month's time.
    """
    name = (name or "").strip()
    value = (value or "").strip()
    logger.info("tool remember(%r, %r)", name, value)
    if not name or not value:
        return "Error: I need both what to call it and what it refers to."

    from jarvis import memory, projects

    host = _host(normalize_url(value))
    # A dot is required as well as a plausible TLD: `_has_real_tld` takes every
    # two-letter ending on trust (they're all country codes), which without this
    # would read a project called "ab" as a web address.
    if _SCHEME_RE.match(value) or ("." in host and _has_real_tld(host)):
        target, kind, spoken = normalize_url(value), memory.PORTAL, normalize_url(value)
    else:
        try:
            resolved = projects.resolve(value)
        except projects.ProjectError as exc:
            # Spoken back verbatim: ProjectError messages are written to be
            # heard ("that matches more than one project — which one?").
            return f"I couldn't save that: {exc}"
        target, kind, spoken = str(resolved), memory.PROJECT, resolved.name

    if dry_run:
        return f"(dry run — nothing was saved) Would remember that {name} means {spoken}."
    if not memory.remember_alias(name, target, kind=kind):
        return (
            f"I couldn't save that — my memory isn't writable right now. "
            f"Say 'remember {name}' again once that's fixed."
        )
    return f"Remembered: {name} means {spoken}."


def open_project(
    name: str = "", *, dry_run: bool = False, on_status: StatusFn | None = None
) -> str:
    """Open a project folder in Finder, by whatever the user calls it.

    Routed through `jarvis.projects.resolve` rather than taking a path, for the
    same reason `run_claude_code` is: the model never hands a filesystem path to
    a subprocess, and the containment check is what makes a misheard name a
    failure rather than a surprise.
    """
    name = (name or "").strip()
    logger.info("tool open_project(%r)", name)

    from jarvis import projects

    try:
        directory = projects.resolve(name)
    except projects.ProjectError as exc:
        return str(exc)

    ok, detail = _run_open([str(directory)], dry_run=dry_run)
    return f"{detail}Opened {directory.name}" if ok else f"Could not open {directory}: {detail}"


HANDLERS: dict[str, Callable[..., str]] = {
    "open_url": open_url,
    "open_app": open_app,
    "run_claude_code": run_claude_code,
    "open_portal": open_portal,
    "fill_login_form": fill_login_form,
    "remember": remember,
    "open_project": open_project,
}


def _only_declared(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep just the arguments this tool's schema declares.

    Models invent parameters, and a handler may have keyword arguments that are
    deliberately not offered to them (`run_claude_code`'s `directory`, which
    bypasses the project-containment check). Filtering here means the schema is
    the *whole* of what a model can reach, rather than a suggestion that happens
    to line up with the Python signature.
    """
    spec = next((item for item in TOOL_SPECS if item["name"] == name), None)
    if spec is None:
        return dict(arguments)
    allowed = set(spec["parameters"]["properties"])
    unexpected = set(arguments) - allowed
    if unexpected:
        logger.warning("dropping undeclared argument(s) %s for %s", sorted(unexpected), name)
    return {key: value for key, value in arguments.items() if key in allowed}


def execute(
    name: str,
    arguments: dict[str, Any],
    *,
    dry_run: bool = False,
    on_status: StatusFn | None = None,
    transcript: str = "",
) -> str:
    """Run tool `name` with `arguments`, returning a result string for the model.

    Never raises: a tool failure is information the model should get back and
    can talk about ("I couldn't find that app"), not a crash. Same reasoning as
    jarvis.stt.transcribe's broad except.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        logger.warning("model asked for unknown tool %r", name)
        return f"Error: no such tool {name!r}. Available tools: {', '.join(TOOL_NAMES)}."

    arguments = _only_declared(name, arguments)
    if transcript and isinstance(arguments.get("url"), str):
        # Done here, once, rather than in each url-taking handler: the rule is
        # about the model's reliability, not about what any one tool does.
        arguments["url"] = prefer_spoken_url(arguments["url"], transcript)
    try:
        return handler(**arguments, dry_run=dry_run, on_status=on_status)
    except TypeError as exc:
        # Wrong/missing arguments from the model — tell it precisely that, so it
        # can retry with the right shape instead of the loop dying.
        logger.warning("bad arguments for %s: %s", name, exc)
        return f"Error: bad arguments for {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 - a tool must never crash the assistant
        logger.error("tool %s raised: %s", name, exc)
        return f"Error: {name} failed: {exc}"
