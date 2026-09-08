"""
Jarvis's tool contract — Phases 2-3.

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
- `fill_login_form` is Phase 4's, and lives in doc 04 until then.

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
            "Open a web page in the user's default browser. Use this whenever the "
            "user asks to open, go to, visit, pull up, or show a website, web page, "
            "or URL."
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
            "the thing being opened is a website."
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
        "name": "open_portal",
        "description": (
            "Open a web page in Jarvis's own automated browser, which Jarvis can see "
            "and act on afterwards. Use this when the user wants Jarvis to *do* "
            "something on the page — log in to a portal, fill something in — rather "
            "than just look at it. For a page the user only wants to read, use "
            "open_url instead."
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
    url = normalize_url(url)
    logger.info("tool open_url(%r)", url)
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


HANDLERS: dict[str, Callable[..., str]] = {
    "open_url": open_url,
    "open_app": open_app,
    "run_claude_code": run_claude_code,
    "open_portal": open_portal,
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
