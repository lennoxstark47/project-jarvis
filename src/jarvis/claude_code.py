"""
Jarvis's first sub-agent: the Claude Code CLI — Phase 3.

Doc 02 is explicit about the posture here: Claude Code is "an opaque, already
excellent sub-agent — it does not try to reimplement any of what Claude Code
already does". So this module is a *launcher and a relay*, nothing more. It
starts `claude` in a project directory with a spoken task as the prompt, follows
its progress, and hands back one short paragraph the voice model can summarize.

Two modes, and why both exist
-----------------------------
`actions.claude_code.mode` picks one:

- **terminal** (the default). A real terminal window opens in front of you,
  `cd`s into the project and runs an ordinary interactive `claude` session with
  your spoken task as its first prompt. You watch it work, and the keyboard is
  yours the moment you want it — which is the whole reason this mode exists.
- **headless**. `claude --print` in a captured subprocess. Nothing appears on
  screen; Jarvis reports the answer and that's all you get.

Terminal mode looks like it can't report anything back — it's your window, not a
pipe Jarvis holds. It can, without running anything twice: Claude Code writes
every session to `~/.claude/projects/<slug>/<session-id>.jsonl`, in the same
event shapes `--output-format stream-json` emits. So Jarvis picks the session id
*before* launching and then tails that file. You get the window; Jarvis still
gets the stream, the live status line, and the answer to speak.

Knowing when an interactive session is *finished* is the one genuinely fuzzy
part, because it never exits — it just goes quiet waiting for your next message.
See `_transcript_settled` for the rule and why it's shaped the way it is.

Why it streams rather than just waiting
---------------------------------------
`claude -p --output-format stream-json` emits one JSON object per line as it
works. Two reasons Jarvis reads them instead of blocking on the final answer:

1. A real task takes minutes. Doc 02's output layer calls for a status surface
   so "running a sub-agent" doesn't look like Jarvis hung — the `on_status`
   callback is fed from this stream, and the menu bar shows the tool Claude Code
   is running right now.
2. A silent 10-minute subprocess is impossible to debug from a menu bar. The
   event log lands in `logs/jarvis.log` either way.

Why the permission posture is what it is
----------------------------------------
This is a voice-triggered agent with file-write access to a real repository,
started from a transcript that is sometimes wrong. Two guards, both deliberate:

- **`allow_edits` is off by default**, which runs Claude Code with
  `--restricted --strict-mcp-config --disallowedTools=Edit,Write,...`. So a
  misheard command investigates and reports instead of rewriting a file. Set it
  to true in `actions.claude_code` when you want fixes rather than findings.

  That specific set of flags is not a guess — it's what was left after two
  weaker guards were tested and failed. `--permission-mode plan` didn't stop an
  edit; removing the write tools didn't either (Claude Code simply used Bash,
  and said so). See `READ_ONLY_FLAGS` for the whole chain and what each attempt
  actually did.
- `--permission-mode` is still passed through and configurable, but treat it as
  what Jarvis *asks* for rather than what is enforced.
- `--permission-prompts none` is passed in headless mode only. Non-interactively
  there is nobody to answer a permission prompt, and the alternative to
  auto-denying is a subprocess that waits forever on stdin nobody is typing
  into. In terminal mode there *is* somebody: you.

Which directory it may run in is not decided here at all — see jarvis.projects.
"""
from __future__ import annotations

import json
import logging
import selectors
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from jarvis import config, terminal

logger = logging.getLogger("jarvis.claude_code")

StatusFn = Callable[[str], None]

# Claude Code's own output can be long (it summarizes multi-file work). What
# comes back here is fed to a small voice model that has to turn it into one
# spoken sentence, so trim it rather than blow out that model's context.
MAX_RESULT_CHARS = 1200

# How long to wait for the process to die after a timeout kill before giving up
# on it. Only ever hit if the CLI ignores SIGTERM.
KILL_GRACE = 5.0

# Where Claude Code keeps its session transcripts. The per-project subdirectory
# name is a slug of the project path, and the exact slug rule isn't ours to
# depend on — so terminal mode globs for the session id instead of computing it.
SESSIONS_ROOT = Path.home() / ".claude" / "projects"

# How long to wait for the transcript file to appear after launching the window.
# Covers the terminal opening, the shell starting and Claude Code booting.
TRANSCRIPT_APPEAR_TIMEOUT = 60.0

# How often to re-read the transcript while a terminal session is running.
TRANSCRIPT_POLL_INTERVAL = 0.5


class ClaudeCodeError(RuntimeError):
    """Couldn't launch or complete a Claude Code run."""


def _settings() -> dict[str, Any]:
    return config.actions_config("claude_code")


# The tools that can change a file directly.
WRITE_TOOLS = "Edit,Write,NotebookEdit,MultiEdit"

# What it takes to actually stop a file being changed. Every flag here was added
# because the previous attempt was measured and failed:
#
#   1. `--permission-mode plan` — DID NOT HOLD. A terminal-mode run under it
#      rewrote a source file anyway (2026-09-08).
#   2. + `--disallowedTools=Edit,Write,...` — DID NOT HOLD either, and said so
#      out loud: "Write and Edit are disabled in this session, so I applied the
#      change via Bash." Taking the editing tools away doesn't take away the
#      ability to edit; it just reroutes it.
#   3. + `--restricted` (removes Bash and the other code-runners, and confines
#      the file tools to the working directory) + `--strict-mcp-config` (so an
#      MCP server can't supply a write tool of its own) — HELD. Same task, same
#      project: file byte-identical afterwards, and the bug still correctly
#      found and explained.
#
# The cost is real: no Bash means investigation runs on Read/Grep/Glob alone.
# That's the price of the guarantee, and it's why `allow_edits: true` exists.
READ_ONLY_FLAGS = ["--restricted", "--strict-mcp-config", f"--disallowedTools={WRITE_TOOLS}"]


def _write_guard(settings: dict[str, Any]) -> list[str]:
    """The flags that stop Claude Code changing files. Empty if edits are allowed.

    Note the `=` in the disallowedTools flag — it is load-bearing, and cost a
    live run to find out. `--disallowedTools` is variadic ("comma or
    space-separated"), so written as two argv entries it keeps eating arguments
    until it hits the next flag — including the task. Measured, with
    `--disallowedTools "Edit,Write" "reply with exactly the word BANANA"`:

        Permission deny rule "reply" matches no known tool — check for typos.
        Permission deny rule "with" matches no known tool — check for typos.
        ...

    The prompt was consumed word by word and Claude Code started with nothing to
    do. The `=` form is a single argv entry, takes exactly one value, and can
    sit anywhere in the command line. Same applies to any other variadic option
    added here later.
    """
    if settings.get("allow_edits", False):
        return []
    return list(READ_ONLY_FLAGS)


def build_command(task: str, *, settings: dict[str, Any] | None = None) -> list[str]:
    """The argv Claude Code is launched with. Separated out so it's testable."""
    settings = settings if settings is not None else _settings()
    command = [
        settings.get("cli", "claude"),
        "--print",
        task,
        "--output-format",
        "stream-json",
        # stream-json under --print requires --verbose; without it the CLI
        # refuses to start rather than degrading to plain output.
        "--verbose",
        "--permission-mode",
        settings.get("permission_mode", "plan"),
        # Nobody is at a keyboard: deny anything that would prompt instead of
        # blocking on it. The permission mode above still decides the rest.
        "--permission-prompts",
        "none",
    ]
    command += _write_guard(settings)
    if settings.get("model"):
        command += ["--model", settings["model"]]
    if settings.get("max_budget_usd"):
        command += ["--max-budget-usd", str(settings["max_budget_usd"])]
    command += list(settings.get("extra_args", []))
    return command


class _RunState:
    """Everything worth knowing about one Claude Code run, filled in as it streams."""

    def __init__(self) -> None:
        self.session_id = ""
        self.result_text = ""
        self.last_text = ""
        self.tools_used: list[str] = []
        self.files_touched: set[str] = set()
        self.cost_usd: float | None = None
        self.is_error = False
        self.error_subtype = ""
        # "text" or "tool_use": what the assistant's most recent content block
        # was. Terminal mode's finished-yet? rule turns on this — see
        # _transcript_settled.
        self.last_block = ""


def _describe_tool(name: str, tool_input: dict[str, Any]) -> str:
    """A short, speakable description of one Claude Code tool call."""
    target = ""
    for key in ("file_path", "path", "pattern", "command", "url", "description"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            target = value.strip()
            break
    if target:
        target = Path(target).name if "/" in target and " " not in target else target
        if len(target) > 60:
            target = target[:57] + "..."
        return f"{name} {target}"
    return name


def _handle_event(event: dict[str, Any], state: _RunState, on_status: StatusFn | None) -> None:
    """Fold one stream-json line into `state`, emitting a status line if useful."""
    kind = event.get("type")

    if kind == "system" and event.get("subtype") == "init":
        state.session_id = event.get("session_id", "")
        _status(on_status, "Claude Code started")
        return

    if kind == "assistant":
        for block in event.get("message", {}).get("content", []) or []:
            if block.get("type") == "tool_use":
                name = block.get("name", "tool")
                tool_input = block.get("input") or {}
                state.tools_used.append(name)
                state.last_block = "tool_use"
                path = tool_input.get("file_path")
                if isinstance(path, str) and name in {"Edit", "Write", "NotebookEdit"}:
                    state.files_touched.add(Path(path).name)
                _status(on_status, f"Claude Code: {_describe_tool(name, tool_input)}")
            elif block.get("type") == "text":
                text = (block.get("text") or "").strip()
                if text:
                    state.last_text = text
                    state.last_block = "text"
                    _status(on_status, f"Claude Code: {text.splitlines()[0][:80]}")
        return

    if kind == "result":
        state.is_error = bool(event.get("is_error"))
        state.error_subtype = event.get("subtype", "") or ""
        result = event.get("result")
        if isinstance(result, str):
            state.result_text = result.strip()
        cost = event.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            state.cost_usd = float(cost)
        return


def _status(on_status: StatusFn | None, message: str) -> None:
    logger.info(message)
    if on_status is None:
        return
    try:
        on_status(message)
    except Exception as exc:  # noqa: BLE001 - a UI callback must never kill the run
        logger.warning("status callback raised: %s", exc)


def _summarize(state: _RunState, elapsed: float, *, partial: bool = False) -> str:
    """One paragraph for the voice model: what happened, and what it should say.

    `partial=True` means the run was cut short, and the distinction matters more
    than it looks. Claude Code narrates as it works ("Strong evidence now, let
    me design the fix"), and handing that line over as if it were the answer is
    how a stopped investigation gets reported as a finished one — which is
    exactly what happened on the first live run of this tool: the voice model,
    given a mid-investigation narration and nothing else, invented a plausible
    root cause out of thin air. So a partial run says so, in words, and its last
    line is labelled as a progress note rather than a conclusion.
    """
    if state.result_text:
        body = state.result_text
    elif partial and state.last_text:
        body = (
            "It reached no conclusion. The last thing it said while working was: "
            f"{state.last_text}"
        )
    elif state.last_text:
        body = state.last_text
    else:
        body = "Claude Code finished without reporting anything."
    if len(body) > MAX_RESULT_CHARS:
        body = body[:MAX_RESULT_CHARS].rstrip() + "... (truncated)"

    facts = [f"{len(state.tools_used)} tool calls", f"{elapsed:.0f}s"]
    if state.files_touched:
        facts.append("changed " + ", ".join(sorted(state.files_touched)))
    if state.cost_usd is not None:
        facts.append(f"${state.cost_usd:.2f}")
    if state.session_id:
        facts.append(f"session {state.session_id}")

    if partial:
        lead = "Claude Code was stopped before it finished"
    else:
        lead = "Claude Code failed" if state.is_error else "Claude Code finished"
    if state.is_error and state.error_subtype:
        lead += f" ({state.error_subtype})"
    return f"{lead} ({'; '.join(facts)}).\n\n{body}"


def build_terminal_command(
    task: str, session_id: str, *, settings: dict[str, Any] | None = None
) -> list[str]:
    """The argv for an *interactive* Claude Code session, as typed into a terminal.

    The task rides along as the CLI's positional prompt argument rather than
    being typed into the TUI after it boots. Same visible result — the session
    opens with your words already in it — but no race against a text UI that
    might not be listening yet, and nothing that can half-type a sentence.
    """
    settings = settings if settings is not None else _settings()
    command = [
        settings.get("cli", "claude"),
        # Chosen by us, before launch, so Jarvis knows which transcript to tail.
        "--session-id",
        session_id,
        "--permission-mode",
        settings.get("permission_mode", "plan"),
    ]
    command += _write_guard(settings)
    if settings.get("model"):
        command += ["--model", settings["model"]]
    command += list(settings.get("extra_args", []))
    command.append(task)
    return command


def _find_transcript(session_id: str, deadline: float) -> Path | None:
    """Wait for Claude Code to create this session's transcript, and return it."""
    pattern = f"*/{session_id}.jsonl"
    while time.monotonic() < deadline:
        matches = list(SESSIONS_ROOT.glob(pattern))
        if matches:
            return matches[0]
        time.sleep(TRANSCRIPT_POLL_INTERVAL)
    return None


def _transcript_settled(state: _RunState, quiet_for: float, quiet_seconds: float) -> bool:
    """Whether an interactive session looks done with the task we gave it.

    An interactive session never exits — it answers and then sits there waiting
    for your next message — so "finished" has to be inferred. Two conditions,
    and the second one is the one that took a bug to find:

    1. It has been quiet for `quiet_seconds`.
    2. The last thing it wrote was **text**, not a tool call. A session paused on
       a permission prompt is also perfectly quiet, and calling that "finished"
       would make Jarvis announce an answer while Claude Code is still waiting
       for you to press y. Ending on prose means it actually said something.
    """
    return quiet_for >= quiet_seconds and state.last_block == "text"


def _tail_transcript(
    path: Path,
    state: _RunState,
    on_status: StatusFn | None,
    *,
    deadline: float,
    quiet_seconds: float,
) -> bool:
    """Follow a session transcript until it settles or `deadline` passes.

    Returns True if it settled (i.e. Claude Code answered). Transcript lines are
    the same JSON objects `--output-format stream-json` emits, so they go
    through the same `_handle_event` the headless path uses.
    """
    offset = 0
    last_change = time.monotonic()
    while time.monotonic() < deadline:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                new_lines = handle.readlines()
                offset = handle.tell()
        except OSError as exc:
            logger.warning("could not read the session transcript: %s", exc)
            return False

        # A trailing partial line means Claude Code is mid-write; rewind to the
        # last newline and pick it up whole on the next pass.
        if new_lines and not new_lines[-1].endswith("\n"):
            offset -= len(new_lines.pop().encode("utf-8"))

        for line in new_lines:
            line = line.strip()
            if not line:
                continue
            last_change = time.monotonic()
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                _handle_event(event, state, on_status)

        if _transcript_settled(state, time.monotonic() - last_change, quiet_seconds):
            return True
        time.sleep(TRANSCRIPT_POLL_INTERVAL)
    return False


def run_in_terminal(
    directory: Path,
    task: str,
    *,
    on_status: StatusFn | None = None,
    timeout: float,
    settings: dict[str, Any],
) -> str:
    """Open a terminal window, run Claude Code in it, and follow along."""
    session_id = str(uuid.uuid4())
    argv = build_terminal_command(task, session_id, settings=settings)
    lines = [
        terminal.shell_line(["cd", str(directory)]),
        terminal.shell_line(argv),
    ]

    _status(on_status, f"Opening a terminal in {directory.name}...")
    started = time.monotonic()
    try:
        app = terminal.open_window(
            lines, app=settings.get("terminal_app", "auto"), title=f"Jarvis — {directory.name}"
        )
    except terminal.TerminalError as exc:
        raise ClaudeCodeError(str(exc)) from exc

    _status(on_status, f"Claude Code is running in {app}")
    state = _RunState()
    state.session_id = session_id
    quiet_seconds = float(settings.get("quiet_seconds", 20))

    transcript = _find_transcript(session_id, time.monotonic() + TRANSCRIPT_APPEAR_TIMEOUT)
    if transcript is None:
        # The window is open and Claude Code may well be working fine — we just
        # can't watch it. Say exactly that rather than implying failure.
        return (
            f"Claude Code is now running in a {app} window in {directory.name}, but I can't "
            f"follow along — its session transcript never appeared, so watch that window "
            f"yourself. Do not invent a result."
        )

    logger.info("following session transcript %s", transcript)
    settled = _tail_transcript(
        transcript,
        state,
        on_status,
        deadline=started + timeout,
        quiet_seconds=quiet_seconds,
    )
    elapsed = time.monotonic() - started

    if not settled:
        _status(on_status, "Claude Code is still working")
        return (
            f"Claude Code is still working in the {app} window after {elapsed:.0f} seconds and "
            f"has NOT finished. Tell the user it's still going and to watch that window — do "
            f"not present anything below as a conclusion.\n\n"
            f"{_summarize(state, elapsed, partial=True)}"
        )

    _status(on_status, "Claude Code answered")
    return (
        f"{_summarize(state, elapsed)}\n\n"
        f"The {app} window is still open in {directory.name} if the user wants to carry on "
        f"the conversation there."
    )


def run(
    project: str | Path,
    task: str,
    *,
    dry_run: bool = False,
    on_status: StatusFn | None = None,
    timeout: float | None = None,
) -> str:
    """Run Claude Code in `project` on `task`, returning a summary for the model.

    Dispatches on `actions.claude_code.mode`: "terminal" opens a window you can
    watch and take over, "headless" captures everything invisibly. See the
    module docstring for why both exist.

    Raises ClaudeCodeError only for "couldn't start it at all". Anything Claude
    Code itself reports — including failing — comes back as a string, because
    that's information the voice model should relay rather than a crash.
    """
    settings = _settings()
    directory = Path(project).expanduser()
    if not directory.is_dir():
        raise ClaudeCodeError(f"{directory} isn't a folder I can work in.")

    task = (task or "").strip()
    if not task:
        raise ClaudeCodeError("I need to know what to ask Claude Code to do.")

    mode = settings.get("mode", "terminal")
    timeout = float(timeout if timeout is not None else settings.get("timeout", 600))

    if mode == "terminal":
        if dry_run:
            argv = build_terminal_command(task, "<session-id>", settings=settings)
            logger.info("[dry run] would open a terminal in %s and run: %s", directory, argv)
            return (
                f"(dry run — no terminal was opened, Claude Code did NOT run, and there "
                f"is no result. Say only that this was a dry run; do not describe what "
                f"the project or file contains.) Would open a "
                f"{terminal.choose_app(settings.get('terminal_app', 'auto'))} window in "
                f"{directory}, cd there, and run: {shlex.join(argv)}"
            )
        return run_in_terminal(
            directory, task, on_status=on_status, timeout=timeout, settings=settings
        )

    command = build_command(task, settings=settings)

    if dry_run:
        logger.info("[dry run] would run in %s: %s", directory, command)
        return (
            f"(dry run — Claude Code was NOT started and there is no result. Say only "
            f"that this was a dry run; do not describe what the project or file "
            f"contains.) Would run in {directory} with permission mode "
            f"{settings.get('permission_mode', 'plan')}: {task}"
        )

    _status(on_status, f"Starting Claude Code in {directory.name}...")
    outcome = stream_run(command, cwd=directory, timeout=timeout, on_status=on_status)

    if outcome.timed_out:
        _status(on_status, "Claude Code timed out")
        return (
            f"Claude Code was still working after {timeout:.0f} seconds, so I stopped it "
            f"and it did NOT finish the task. Tell the user it ran out of time and did "
            f"not reach an answer — do not present anything below as a conclusion.\n\n"
            f"{_summarize(outcome.state, outcome.elapsed, partial=True)}"
        )

    if outcome.failed_to_start:
        logger.error("claude exited %s: %s", outcome.returncode, outcome.stderr)
        return f"Claude Code couldn't run: {outcome.stderr}"

    summary = _summarize(outcome.state, outcome.elapsed)
    logger.info(
        "claude code run finished in %.1fs: %s", outcome.elapsed, summary.splitlines()[0]
    )
    _status(on_status, "Claude Code finished")
    return summary


@dataclass
class StreamOutcome:
    """One headless `claude` run, as it ended. See `stream_run`."""

    state: "_RunState"
    elapsed: float
    timed_out: bool
    returncode: int
    stderr: str

    @property
    def failed_to_start(self) -> bool:
        """The process died without producing a result — a launch/auth failure.

        Distinct from a run that *finished* and reported a problem: that one has
        a result to relay, and relaying it is the whole job.
        """
        return self.returncode != 0 and not self.state.result_text


def stream_run(
    command: list[str],
    *,
    cwd: Path,
    timeout: float,
    on_status: StatusFn | None = None,
) -> StreamOutcome:
    """Run a `claude --print --output-format stream-json` argv and follow it.

    Factored out of `run` in Phase 7 so the research lane (jarvis/agents/
    research.py) gets the same streaming, the same status lines, the same
    deadline that fires while the CLI is *silent*, and the same stderr drain —
    rather than a second, subtly different subprocess wrapper. What the events
    mean is still `_handle_event`'s business; what they should be *called* out
    loud belongs to the caller, which is why this returns the state instead of a
    sentence.
    """
    started = time.monotonic()
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except (OSError, ValueError) as exc:
        raise ClaudeCodeError(
            f"I couldn't start Claude Code ({exc}). Is the `claude` CLI installed "
            f"and on PATH?"
        ) from exc

    state = _RunState()
    stderr_lines: list[str] = []
    # Drained on its own thread: a subprocess that fills the stderr pipe while
    # we're blocked reading stdout deadlocks, and Claude Code is chatty enough
    # on stderr for that to be a real risk on a long run.
    stderr_thread = threading.Thread(
        target=lambda: stderr_lines.extend(line for line in process.stderr),
        daemon=True,
    )
    stderr_thread.start()

    timed_out = _pump(process, state, on_status, deadline=started + timeout)
    elapsed = time.monotonic() - started

    if timed_out:
        _terminate(process)
        return StreamOutcome(state, elapsed, True, process.returncode or 0, "")

    process.wait()
    stderr_thread.join(timeout=1.0)
    stderr = "".join(stderr_lines).strip()[-500:] or f"exit code {process.returncode}"
    return StreamOutcome(state, elapsed, False, process.returncode, stderr)


def _pump(
    process: subprocess.Popen,
    state: _RunState,
    on_status: StatusFn | None,
    *,
    deadline: float,
) -> bool:
    """Read stream-json lines until the process ends or `deadline` passes.

    Uses a selector rather than a plain `for line in stdout` so the timeout
    still fires while Claude Code is *silent* — which is exactly when it's
    thinking, and exactly when a hung run looks identical to a working one.
    Returns True if it timed out.
    """
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            if not selector.select(timeout=min(remaining, 1.0)):
                if process.poll() is not None:
                    return False
                continue
            line = process.stdout.readline()
            if not line:
                return False
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Not every line is ours — a warning or a progress spinner can
                # land on stdout. Log it and keep reading rather than aborting.
                logger.debug("non-JSON line from claude: %s", line[:200])
                continue
            if isinstance(event, dict):
                _handle_event(event, state, on_status)
    finally:
        selector.close()


def _terminate(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        logger.warning("claude ignored SIGTERM — killing it")
        process.kill()
        process.wait(timeout=KILL_GRACE)
