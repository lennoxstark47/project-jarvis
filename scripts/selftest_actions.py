#!/usr/bin/env python3
"""
Offline self-test for the Phase 3 action layer.

Same idea as scripts/selftest_brain.py, one layer down: check everything about
`run_claude_code` / `open_portal` that can be checked without spending money on
a sub-agent run or opening a browser. Concretely:

- **jarvis.projects** — the containment boundary. A spoken name resolving to
  the wrong directory, or a path escaping the configured roots, is the failure
  that matters most in this phase, and it's silent when it happens.
- **jarvis.claude_code** — the argv Claude Code is launched with (the
  permission flags in particular), and the stream-json parsing, driven from
  recorded event shapes rather than a live run.
- **jarvis.tools** — that the new tools dispatch, dry-run, and report bad
  arguments instead of raising.

    .venv/bin/python3 scripts/selftest_actions.py

Live behaviour (does Claude Code actually run? does the browser open?) is
scripts/try_actions.py.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json  # noqa: E402
import shlex  # noqa: E402
import time  # noqa: E402

from jarvis import claude_code, config, followup, projects, terminal, tools  # noqa: E402
from jarvis import agent as agent_module  # noqa: E402
from jarvis.agent import Agent  # noqa: E402
from jarvis.claude_code import _RunState, _handle_event, _summarize  # noqa: E402
from jarvis.router.base import Completion, ToolCall  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(f"{label}{f' — {detail}' if detail else ''}")


class FakeConfig:
    """Swap in a config dict for the duration of a `with` block.

    The action layer reads config on every call (so editing the permission mode
    takes effect immediately), which makes this the natural way to test it.
    """

    def __init__(self, actions: dict) -> None:
        self._actions = actions
        self._original = None

    def __enter__(self):
        self._original = config.load_config
        config.load_config = lambda: {"actions": self._actions}
        return self

    def __exit__(self, *_exc) -> None:
        config.load_config = self._original


# --------------------------------------------------------------------------
print("\njarvis.projects — resolving a spoken project name")

with tempfile.TemporaryDirectory() as tmp:
    # .resolve() because macOS puts temp dirs under /var, which is a symlink to
    # /private/var — and jarvis.projects deliberately returns real paths.
    root = Path(tmp).resolve() / "workspace"
    outside = Path(tmp).resolve() / "elsewhere"
    for name in ("project_jarvis", "billing-service", "billing-frontend", ".hidden"):
        (root / name).mkdir(parents=True)
    (root / "notes.txt").write_text("not a project")
    outside.mkdir()

    with FakeConfig({"projects": {"roots": [str(root)], "aliases": {"my notes": str(outside)}}}):
        def resolved(name: str) -> str:
            try:
                return str(projects.resolve(name))
            except projects.ProjectError as exc:
                return f"ProjectError: {exc}"

        check(
            "exact folder name resolves",
            resolved("project_jarvis") == str(root / "project_jarvis"),
            resolved("project_jarvis"),
        )
        check(
            "spoken form ('project jarvis') resolves through normalization",
            resolved("project jarvis") == str(root / "project_jarvis"),
            resolved("project jarvis"),
        )
        check(
            "unique substring ('jarvis') resolves",
            resolved("jarvis") == str(root / "project_jarvis"),
            resolved("jarvis"),
        )
        check(
            "typo ('projekt jarvis') resolves by fuzzy match",
            resolved("projekt jarvis") == str(root / "project_jarvis"),
            resolved("projekt jarvis"),
        )
        check(
            "ambiguous name is an error, not a guess",
            "more than one project" in resolved("billing"),
            resolved("billing"),
        )
        check(
            "unknown name lists what it does know",
            "couldn't find a project" in resolved("nothing like this"),
            resolved("nothing like this"),
        )
        check(
            "a file is not a project",
            "couldn't find a project" in resolved("notes.txt"),
            resolved("notes.txt"),
        )
        check(
            "dotfile directories are not candidates",
            "couldn't find" in resolved("hidden"),
            resolved("hidden"),
        )
        check(
            "an in-root absolute path is accepted",
            resolved(str(root / "project_jarvis")) == str(root / "project_jarvis"),
            resolved(str(root / "project_jarvis")),
        )
        check(
            "a path outside the roots is refused",
            "outside the folders" in resolved(str(outside)),
            resolved(str(outside)),
        )
        check(
            "'..' can't escape the roots",
            "outside the folders" in resolved(str(root / "project_jarvis" / ".." / "..")),
            resolved(str(root / "project_jarvis" / ".." / "..")),
        )
        check(
            "an explicit alias wins, and may point outside the roots",
            resolved("my notes") == str(outside),
            resolved("my notes"),
        )
        check("an empty name is an error", "which project" in resolved(""), resolved(""))

# --------------------------------------------------------------------------
print("\njarvis.claude_code — how the sub-agent gets launched")

DEFAULT_CC = {
    "cli": "claude",
    "allow_edits": False,
    "permission_mode": "plan",
    "model": None,
    "timeout": 600,
    "max_budget_usd": None,
    "extra_args": [],
}
command = claude_code.build_command("find the bug", settings=DEFAULT_CC)

check("the task is passed as the prompt", "find the bug" in command, str(command))
check("runs non-interactively (--print)", "--print" in command, str(command))
check(
    "asks for stream-json (+ the --verbose it requires)",
    command[command.index("--output-format") + 1] == "stream-json" and "--verbose" in command,
    str(command),
)
check(
    "defaults to the read-only permission mode",
    command[command.index("--permission-mode") + 1] == "plan",
    str(command),
)
check(
    "never waits on a permission prompt nobody can answer",
    command[command.index("--permission-prompts") + 1] == "none",
    str(command),
)
check(
    "the write guard is on by default",
    all(flag in command for flag in claude_code.READ_ONLY_FLAGS),
    str(command),
)
check(
    "the guard closes Bash too — taking away Edit/Write alone was measured "
    "insufficient, Claude Code just used Bash",
    "--restricted" in command,
    str(command),
)
check("no model pinned when config says null", "--model" not in command, str(command))
check("no budget flag when config says null", "--max-budget-usd" not in command, str(command))

loose = claude_code.build_command(
    "fix it",
    settings={**DEFAULT_CC, "permission_mode": "acceptEdits", "model": "sonnet",
              "max_budget_usd": 2.5, "extra_args": ["--add-dir", "/tmp/x"]},
)
check(
    "permission mode is configurable (acceptEdits)",
    loose[loose.index("--permission-mode") + 1] == "acceptEdits",
    str(loose),
)
check(
    "allow_edits: true lifts the whole guard",
    not any(
        flag in claude_code.build_command("fix it", settings={**DEFAULT_CC, "allow_edits": True})
        for flag in claude_code.READ_ONLY_FLAGS
    ),
)
check("model is passed when configured", "sonnet" in loose, str(loose))
check("budget cap is passed when configured", "2.5" in loose, str(loose))
check("extra_args are appended", loose[-2:] == ["--add-dir", "/tmp/x"], str(loose))

# --------------------------------------------------------------------------
print("\njarvis.claude_code — reading the stream-json event stream")

# Shapes taken from a real `claude -p --output-format stream-json` run.
EVENTS = [
    {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
    {"type": "system", "subtype": "init", "session_id": "abc-123", "tools": ["Read"]},
    {
        "type": "assistant",
        "message": {"content": [{"type": "thinking", "thinking": "", "signature": "x"}]},
    },
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Read",
                    "input": {"file_path": "/tmp/project/calc.py"},
                }
            ]
        },
    },
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "1\tdef add"}]}},
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "/p/calc.py"}}
            ]
        },
    },
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "add() subtracts."}]}},
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "`add` returns a - b, which is the bug.",
        "total_cost_usd": 0.1648945,
        "session_id": "abc-123",
    },
]

state = _RunState()
seen: list[str] = []
for event in EVENTS:
    _handle_event(event, state, seen.append)

check("session id is captured", state.session_id == "abc-123", state.session_id)
check("tool calls are counted", state.tools_used == ["Read", "Edit"], str(state.tools_used))
check("edited files are noticed", state.files_touched == {"calc.py"}, str(state.files_touched))
check("the final result is captured", state.result_text.startswith("`add` returns"), state.result_text)
check("cost is captured", state.cost_usd == 0.1648945, str(state.cost_usd))
check("no error flagged on a clean run", state.is_error is False)
check(
    "empty thinking blocks don't become status noise",
    all(line.strip() and "signature" not in line for line in seen),
    str(seen),
)
check(
    "status lines name the file being read",
    any("Read calc.py" in line for line in seen),
    str(seen),
)

summary = _summarize(state, 42.0)
check("summary reports the outcome", summary.startswith("Claude Code finished"), summary[:60])
for fact in ("2 tool calls", "42s", "changed calc.py", "$0.16", "session abc-123"):
    check(f"summary mentions {fact!r}", fact in summary, summary.splitlines()[0])
check("summary carries the answer itself", "which is the bug" in summary, summary)

long_state = _RunState()
long_state.result_text = "x" * 5000
check(
    "an enormous result is truncated before it reaches the voice model",
    len(_summarize(long_state, 1.0)) < claude_code.MAX_RESULT_CHARS + 300,
    str(len(_summarize(long_state, 1.0))),
)

failed = _RunState()
failed.is_error = True
failed.error_subtype = "error_max_turns"
check(
    "a failed run says so",
    _summarize(failed, 1.0).startswith("Claude Code failed (error_max_turns)"),
    _summarize(failed, 1.0),
)

# The failure this pins actually happened live: a timed-out run's last
# narration line ("Strong evidence now, let me design the fix") was handed to
# the voice model as if it were the answer, and the model invented a root cause
# to fill the gap. A stopped run has to *say* it was stopped.
stopped = _RunState()
stopped.last_text = "Strong evidence now. Let me have Plan agents design the fix."
stopped.tools_used = ["Read"] * 95
partial_summary = _summarize(stopped, 600.0, partial=True)
check(
    "a stopped run is never described as finished",
    partial_summary.startswith("Claude Code was stopped before it finished"),
    partial_summary.splitlines()[0],
)
check(
    "a stopped run states outright that it reached no conclusion",
    "reached no conclusion" in partial_summary,
    partial_summary,
)
check(
    "its last words are labelled as progress, not as the answer",
    "while working was" in partial_summary,
    partial_summary,
)
check(
    "a completed run is not labelled partial",
    _summarize(state, 1.0).startswith("Claude Code finished"),
    _summarize(state, 1.0).splitlines()[0],
)

check(
    "the system prompt forbids inventing what a tool didn't return",
    "Never" in agent_module.SYSTEM_PROMPT and "invent" in agent_module.SYSTEM_PROMPT,
    agent_module.SYSTEM_PROMPT[-200:],
)

# --------------------------------------------------------------------------
print("\njarvis.claude_code — terminal mode (the visible window)")

term_cmd = claude_code.build_terminal_command("find the bug", "SID-1", settings=DEFAULT_CC)
check("the session id is chosen by us, before launch", "SID-1" in term_cmd, str(term_cmd))
check(
    "the task rides in as the prompt argument, last",
    term_cmd[-1] == "find the bug",
    str(term_cmd),
)
check("interactive, so no --print", "--print" not in term_cmd, str(term_cmd))
check(
    "no --permission-prompts none — there IS somebody at the keyboard now",
    "--permission-prompts" not in term_cmd,
    str(term_cmd),
)
check(
    "the configured permission mode still applies",
    term_cmd[term_cmd.index("--permission-mode") + 1] == "plan",
    str(term_cmd),
)
check(
    "the write guard applies in terminal mode too",
    all(flag in term_cmd for flag in claude_code.READ_ONLY_FLAGS),
    str(term_cmd),
)
# --disallowedTools is variadic: as two argv entries it eats every following
# argument, and in terminal mode the next argument is the task. A live run was
# lost to exactly this. The "=" form takes one value and can sit anywhere.
check(
    "the write guard is a single --flag=value entry, so it can't eat the task",
    not any(arg == "--disallowedTools" for arg in term_cmd) and term_cmd[-1] == "find the bug",
    str(term_cmd),
)

# A spoken task can contain anything. It has to survive two layers — the
# AppleScript string literal and then the shell — as *text*.
NASTY = 'find the "bug"; rm -rf ~ && echo $(whoami) \\ done'
line = terminal.shell_line(claude_code.build_terminal_command(NASTY, "S", settings=DEFAULT_CC))
check(
    "a hostile task stays one shell argument",
    shlex.split(line)[-1] == NASTY,
    line,
)
script = terminal._iterm_script([line], "Jarvis")
check(
    "quotes are escaped for AppleScript, not left to terminate the string",
    '\\"' in script and script.count("write text") == 1,
    script[-200:],
)
check(
    "backslashes are escaped too",
    "\\\\" in script,
    script[-200:],
)
check(
    "cd and the command are separate typed lines (iTerm)",
    terminal._iterm_script(["cd /tmp", "claude x"], "").count("write text") == 2,
)
check(
    "Terminal.app joins them with && so a failed cd can't run it elsewhere",
    " && " in terminal._terminal_script(["cd /tmp", "claude x"], ""),
    terminal._terminal_script(["cd /tmp", "claude x"], ""),
)
check("iTerm2 is preferred when installed", terminal.choose_app("auto") in ("iTerm2", "Terminal"))
check("an explicit choice is honoured", terminal.choose_app("Terminal") == "Terminal")

# An interactive session never exits, so "done" is inferred. The rule that
# matters: a session paused on a permission prompt is quiet too.
settled = _RunState()
settled.last_block = "text"
waiting = _RunState()
waiting.last_block = "tool_use"
check(
    "quiet + ended on prose = finished",
    claude_code._transcript_settled(settled, 25.0, 20.0),
)
check(
    "quiet but ended on a tool call = still waiting (a permission prompt is quiet)",
    not claude_code._transcript_settled(waiting, 999.0, 20.0),
)
check(
    "prose but not yet quiet = still working",
    not claude_code._transcript_settled(settled, 5.0, 20.0),
)

# --------------------------------------------------------------------------
print("\njarvis.projects — answering 'where is it?' out loud")

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp).resolve()
    (home / "Documents" / "client work" / "invoicing").mkdir(parents=True)
    (home / "Desktop" / "project_jarvis").mkdir(parents=True)

    original_home = projects.Path.home
    projects.Path.home = staticmethod(lambda: home)
    try:
        with FakeConfig({"projects": {"roots": [str(home / "Desktop")], "aliases": {}}}):
            spoken = lambda project, answer: projects.resolve_spoken(project, answer)

            check(
                "'it's in Documents slash client work' walks two levels",
                spoken("client work", "it's in Documents slash client work")
                == home / "Documents" / "client work",
                str(spoken("client work", "it's in Documents slash client work")),
            )
            check(
                "a location said without 'slash' is still split correctly",
                spoken("invoicing", "in my documents folder under client work")
                == home / "Documents" / "client work" / "invoicing",
                str(spoken("invoicing", "in my documents folder under client work")),
            )
            check(
                "naming only the containing folder still finds the project inside it",
                spoken("invoicing", "it's in Documents")
                == home / "Documents" / "client work" / "invoicing",
                str(spoken("invoicing", "it's in Documents")),
            )
            check(
                "a multi-word folder name is matched whole",
                spoken("client work", "documents slash client work")
                == home / "Documents" / "client work",
                str(spoken("client work", "documents slash client work")),
            )
            check(
                "'the X folder in Y' names the child first, and still resolves",
                spoken("invoicing", "the invoicing folder in Documents slash client work")
                == home / "Documents" / "client work" / "invoicing",
                str(spoken("invoicing", "the invoicing folder in Documents slash client work")),
            )
            check(
                "a fuzzy match can't absorb a word into a shorter parent name",
                # "client work invoicing" scores 0.85 against "client work";
                # without a length guard the walk eats "invoicing" and stops at
                # the parent — the live failure this pins.
                spoken("invoicing", "documents client work invoicing")
                == home / "Documents" / "client work" / "invoicing",
                str(spoken("invoicing", "documents client work invoicing")),
            )
            check(
                "a deeper folder isn't swallowed by a fuzzy match on its parent",
                spoken("invoicing", "documents client work invoicing")
                == home / "Documents" / "client work" / "invoicing",
                str(spoken("invoicing", "documents client work invoicing")),
            )
            check(
                "but a genuinely misheard name still resolves by fuzzy match",
                spoken("client work", "it's in Documents slash cliant work")
                == home / "Documents" / "client work",
                str(spoken("client work", "it's in Documents slash cliant work")),
            )
            check(
                "words that match nothing on disk resolve to nothing, not a guess",
                spoken("invoicing", "it is in the flumph directory") is None,
                str(spoken("invoicing", "it is in the flumph directory")),
            )
            check("an empty answer resolves to nothing", spoken("x", "") is None)
            check(
                "the home directory itself is refused",
                spoken("x", "home") is None and spoken("x", "~") is None,
            )
    finally:
        projects.Path.home = original_home

check(
    "system directories are refused however clearly they were said",
    projects.resolve_spoken("x", "slash System") is None
    and projects.resolve_spoken("x", "slash usr") is None,
)

# --------------------------------------------------------------------------
print("\njarvis.followup — the one thing remembered between utterances")

followup.clear()
check("nothing pending by default", followup.peek() is None)
followup.ask_where("my invoicing thing", "find why totals are wrong")
check("a parked question is visible", followup.peek().project == "my invoicing thing")
check("it carries the task, not just the name", followup.take().task == "find why totals are wrong")
check("taking it consumes it — the trap isn't left armed", followup.peek() is None)
check("taking nothing is not an error", followup.take() is None)

followup.ask_where("stale", "x")
followup._pending = followup.PendingLocation(
    project="stale", task="x", asked_at=time.monotonic() - followup.TTL_SECONDS - 1
)
check("a question you never answered expires", followup.take() is None, "stale one survived")
followup.clear()

# --------------------------------------------------------------------------
print("\njarvis.tools — dispatch, dry runs and bad arguments")

check(
    "the Phase 2+3 tools are all still declared",
    # Not an equality check any more: Phase 4 added fill_login_form, and this
    # file's job is that Phase 3's tools survived, not that the list froze.
    set(tools.TOOL_NAMES)
    >= {"open_url", "open_app", "run_claude_code", "open_portal"},
    str(tools.TOOL_NAMES),
)
check(
    "every tool has a handler",
    all(name in tools.HANDLERS for name in tools.TOOL_NAMES),
    str(sorted(tools.HANDLERS)),
)
check(
    "every tool spec is complete",
    # A spec needs a description and parameters. It does *not* need a required
    # argument: Phase 4's fill_login_form deliberately has none, because Jarvis
    # can work out the site and the credential itself and a required argument
    # the model can't supply just costs a wasted round-trip. What is worth
    # checking is that nothing is listed as required without being declared —
    # that one is a typo, and it makes a backend reject the whole tool list.
    all(
        spec["description"]
        and spec["parameters"]["properties"]
        and set(spec["parameters"]["required"]) <= set(spec["parameters"]["properties"])
        for spec in tools.TOOL_SPECS
    ),
)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp).resolve()
    (root / "demo").mkdir()
    with FakeConfig({"projects": {"roots": [str(root)], "aliases": {}}, "claude_code": DEFAULT_CC}):
        dry = tools.execute(
            "run_claude_code", {"project": "demo", "task": "find the bug"}, dry_run=True
        )
        check("run_claude_code dry-runs without launching anything", "dry run" in dry, dry)
        check("dry run names the resolved directory", "demo" in dry, dry)
        check("dry run states the permission mode it would use", "plan" in dry, dry)

        missing = tools.execute(
            "run_claude_code", {"project": "no such thing", "task": "x"}, dry_run=True
        )
        check(
            "an unresolvable project comes back as something to say, not an exception",
            "couldn't find a project" in missing,
            missing,
        )

portal = tools.execute("open_portal", {"url": "example dot com"}, dry_run=True)
check("open_portal dry-runs", "dry run" in portal, portal)
check("open_portal normalizes a spoken URL", "https://example.com" in portal, portal)

check(
    "arguments the schema doesn't declare are dropped, not passed on",
    "dry run" in tools.execute(
        "open_portal", {"url": "example.com", "directory": "/", "headless": True}, dry_run=True
    ),
)
check(
    "an unknown tool is reported, not raised",
    "no such tool" in tools.execute("teleport", {}, dry_run=True),
)
check(
    "missing arguments are reported back to the model",
    "bad arguments" in tools.execute("run_claude_code", {"project": "x"}, dry_run=True),
)

# --------------------------------------------------------------------------
print("\njarvis.agent — status callbacks reach the UI")


class ScriptedBackend:
    name = "scripted"
    model = "scripted-1"

    def __init__(self, completions):
        self._completions = list(completions)

    def complete(self, messages, tools_, system=""):
        return self._completions.pop(0) if self._completions else Completion(text="done")


statuses: list[str] = []
backend = ScriptedBackend(
    [
        Completion(
            tool_calls=(ToolCall(id="1", name="open_url", arguments={"url": "example.com"}),)
        ),
        Completion(text="Opened it."),
    ]
)
result = Agent(backend=backend, dry_run=True, on_status=statuses.append).handle("open example.com")
check("the turn completes", result.reply == "Opened it.", result.reply)
check("a status line is emitted while thinking", "Thinking..." in statuses, str(statuses))


# The round-2 flow, end to end: Jarvis asks where a project is, and the next
# thing you say is the answer.
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp).resolve()
    (home / "Documents" / "client work" / "invoicing").mkdir(parents=True)
    (home / "Desktop").mkdir()
    config_file = home / "jarvis.json"
    config_file.write_text("{}")

    original_home, original_path = projects.Path.home, config.CONFIG_PATH
    projects.Path.home = staticmethod(lambda: home)
    config.CONFIG_PATH = config_file
    try:
        with FakeConfig(
            {
                "projects": {"roots": [str(home / "Desktop")], "aliases": {}},
                "claude_code": DEFAULT_CC,
            }
        ):
            followup.clear()
            spoken_backend = ScriptedBackend(
                [Completion(text="What time?"), Completion(text="Started it in invoicing.")]
            )
            agent = Agent(backend=spoken_backend, dry_run=True, on_status=lambda _m: None)

            check(
                "with nothing pending, a location-ish sentence is just a normal utterance",
                agent.handle("it's in Documents").actions == [],
            )

            followup.ask_where("my invoicing thing", "find why the totals are wrong")
            answered = agent.handle("it's in Documents slash client work slash invoicing")
            check(
                "the parked task runs as soon as the location is understood",
                [call.name for call, _ in answered.actions] == ["run_claude_code"],
                str(answered.actions),
            )
            check(
                "and it runs the task you originally asked for",
                answered.actions[0][0].arguments["task"] == "find why the totals are wrong",
                str(answered.actions),
            )
            check(
                "the model is only asked to describe it, not to re-decide it",
                answered.reply == "Started it in invoicing.",
                answered.reply,
            )
            check("the question is consumed", followup.peek() is None)
            check(
                "a dry run leaves the user's config alone",
                json.loads(config_file.read_text()) == {},
                config_file.read_text(),
            )
            # The learning itself, tested directly — a dry run deliberately
            # skips it, so the seeded turn above can't be what proves it works.
            config.save_alias("my invoicing thing", home / "Documents" / "client work")
            check(
                "a real answer is learned, so it's asked exactly once per project",
                json.loads(config_file.read_text())["actions"]["projects"]["aliases"]
                == {"my invoicing thing": str(home / "Documents" / "client work")},
                config_file.read_text(),
            )
            check(
                "saving an alias preserves whatever else is in the file",
                "actions" in json.loads(config_file.read_text()),
            )

            followup.ask_where("mystery", "do the thing")
            confused = agent.handle("actually never mind what time is it")
            check(
                "an answer that resolves to nothing doesn't run anything",
                confused.actions == [],
                str(confused.actions),
            )
            check(
                "and the question stays armed, so a mishearing costs one sentence not the request",
                followup.peek() is not None,
            )
            followup.clear()
    finally:
        projects.Path.home = original_home
        config.CONFIG_PATH = original_path


def explode(_message: str) -> None:
    raise RuntimeError("the menu bar fell over")


boom = ScriptedBackend([Completion(text="fine")])
result = Agent(backend=boom, dry_run=True, on_status=explode).handle("hello")
check("a broken status callback can't end a turn", result.reply == "fine", result.reply)

# --------------------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)
print("All Phase 3 action-layer checks passed.")
