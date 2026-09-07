"""
Jarvis's tool contract — Phase 2.

This module is the *entire* set of things Jarvis is able to do. One schema per
tool, described in backend-neutral JSON Schema; each backend in
`jarvis.router` converts these into whatever shape its API wants. That's the
"fixed small tool list" from docs/01-PHASE_PLAN.md — it grows only when a phase
explicitly adds a tool, never opportunistically.

Phase 2 deliberately ships only the two safest actions (per the phase plan:
"No actual system actions yet except the safest one: opening a URL or app").
`run_claude_code`, Playwright browsing, and `fill_login_form` are Phases 3-4.

Both tools shell out to macOS's `open`, which is why they're safe: `open` hands
the argument to LaunchServices rather than a shell, so a mis-transcribed
command can't turn into command execution.
"""
from __future__ import annotations

import logging
import re
import subprocess
from typing import Any, Callable

logger = logging.getLogger("jarvis.tools")

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


def open_url(url: str, *, dry_run: bool = False) -> str:
    url = normalize_url(url)
    logger.info("tool open_url(%r)", url)
    ok, detail = _run_open([url], dry_run=dry_run)
    return f"{detail}Opened {url}" if ok else f"Could not open {url}: {detail}"


def open_app(name: str, *, dry_run: bool = False) -> str:
    name = name.strip()
    logger.info("tool open_app(%r)", name)
    ok, detail = _run_open(["-a", name], dry_run=dry_run)
    return f"{detail}Opened {name}" if ok else f"Could not open {name}: {detail}"


HANDLERS: dict[str, Callable[..., str]] = {
    "open_url": open_url,
    "open_app": open_app,
}


def execute(name: str, arguments: dict[str, Any], *, dry_run: bool = False) -> str:
    """Run tool `name` with `arguments`, returning a result string for the model.

    Never raises: a tool failure is information the model should get back and
    can talk about ("I couldn't find that app"), not a crash. Same reasoning as
    jarvis.stt.transcribe's broad except.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        logger.warning("model asked for unknown tool %r", name)
        return f"Error: no such tool {name!r}. Available tools: {', '.join(TOOL_NAMES)}."

    try:
        return handler(**arguments, dry_run=dry_run)
    except TypeError as exc:
        # Wrong/missing arguments from the model — tell it precisely that, so it
        # can retry with the right shape instead of the loop dying.
        logger.warning("bad arguments for %s: %s", name, exc)
        return f"Error: bad arguments for {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 - a tool must never crash the assistant
        logger.error("tool %s raised: %s", name, exc)
        return f"Error: {name} failed: {exc}"
