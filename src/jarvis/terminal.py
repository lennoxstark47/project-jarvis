"""
Opening a real terminal window and typing into it — Phase 3 (round 2).

`jarvis.claude_code` can run the sub-agent two ways. Headless, it captures the
output and nobody sees anything happen. In *terminal* mode — the default — a
terminal window actually opens in front of you, `cd`s into the project and runs
Claude Code, so you can watch it work and take the keyboard whenever you like.
This module is the second half of that: the AppleScript that opens the window
and types the commands.

This is what `01-PHASE_PLAN.md`'s "real `open`/`osascript` calls" bullet turned
out to be for. Round 1 of this phase concluded osascript wasn't needed, because
`open -a` already launches and fronts an app — true, but it can't type a command
into the app it launched, which is the entire point here.

**The escaping matters more than it looks.** Two layers sit between a spoken
sentence and something that runs: AppleScript string literals, and then the
shell inside the terminal. A task like `find the bug in "utils"; rm -rf ~` has
to survive both as *text*, so every command is `shlex.quote`d for the shell and
then escaped for AppleScript. Nothing is ever interpolated raw.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

logger = logging.getLogger("jarvis.terminal")

# How long to wait on osascript. Opening a window is instant; this only trips if
# the terminal app is wedged or a permission dialog is sitting unanswered.
OSASCRIPT_TIMEOUT = 30

ITERM_APP = Path("/Applications/iTerm.app")


class TerminalError(RuntimeError):
    """Couldn't open a terminal window, or couldn't talk to the terminal app."""


def choose_app(preference: str = "auto") -> str:
    """Which terminal app to drive: 'iTerm2' or 'Terminal'.

    'auto' prefers iTerm2 when it's installed — it has a far saner AppleScript
    dictionary (a real `create window` that hands back the session you just
    made), where Terminal.app's `do script` makes you guess which window you got.
    """
    preference = (preference or "auto").strip()
    if preference.lower() not in ("auto", ""):
        return preference
    return "iTerm2" if ITERM_APP.is_dir() else "Terminal"


def _applescript_string(value: str) -> str:
    """Quote `value` as an AppleScript string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def shell_line(parts: list[str]) -> str:
    """One shell command line, with every argument quoted."""
    return " ".join(shlex.quote(part) for part in parts)


def _iterm_script(lines: list[str], title: str) -> str:
    typed = "\n".join(f"        write text {_applescript_string(line)}" for line in lines)
    naming = f"        set name to {_applescript_string(title)}\n" if title else ""
    return f"""
tell application "iTerm2"
    activate
    set newWindow to (create window with default profile)
    tell current session of newWindow
{naming}{typed}
    end tell
end tell
"""


def _terminal_script(lines: list[str], title: str) -> str:
    # Terminal.app has no per-line `write text`, so the commands are joined into
    # one `do script`. `&&` rather than `;` so a failed `cd` doesn't go on to
    # run Claude Code in whatever directory the shell happened to start in —
    # which would be the wrong project, silently.
    joined = " && ".join(lines)
    naming = (
        f"    set custom title of front window to {_applescript_string(title)}\n" if title else ""
    )
    return f"""
tell application "Terminal"
    activate
    do script {_applescript_string(joined)}
{naming}end tell
"""


def open_window(lines: list[str], *, app: str = "auto", title: str = "") -> str:
    """Open a terminal window and type `lines` into it, one command per line.

    Returns the name of the app that was driven. Raises TerminalError.
    """
    if not lines:
        raise TerminalError("nothing to run in the terminal")

    app = choose_app(app)
    script = _iterm_script(lines, title) if app.lower().startswith("iterm") else _terminal_script(
        lines, title
    )
    logger.info("opening a %s window running: %s", app, lines[-1])

    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, script passed as data
            ["osascript", "-"],
            input=script,
            capture_output=True,
            text=True,
            timeout=OSASCRIPT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TerminalError(f"couldn't drive {app}: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        # -1743 is macOS's "not authorised to send Apple events" — the one
        # failure here that isn't a bug, and that no retry will fix.
        if "-1743" in detail or "Not authorized" in detail:
            raise TerminalError(
                f"macOS won't let Jarvis control {app}. Grant it in System Settings -> "
                f"Privacy & Security -> Automation, then try again."
            )
        raise TerminalError(f"{app} refused to open a window: {detail}")

    return app
