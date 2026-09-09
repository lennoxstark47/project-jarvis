#!/usr/bin/env python3
"""
Offline self-test for Phase 7's sub-agents and routing.

No network, no model, no `claude` CLI, no microphone. Every check here runs
against fakes, and the real memory store is switched off before anything is
imported — for the reason Phase 6 wrote down after three self-tests quietly
appended to the user's own `memory/jarvis.db`: a check that passes because of
something you said last week has told you nothing.

What it pins:

- **the lane registry** — every tool belongs to exactly one sub-agent, or is
  deliberately the main loop's own. This is the boundary the whole phase is
  built on, and it is the kind of thing a later phase widens by accident.
- **classification** — the keyword router puts ordinary sentences in the right
  lane, names *both* lanes of a compound request, and never raises. It is a
  hint, so the checks are about it being sane, not about it being clever.
- **the handoff ledger** — findings travel from one lane to the next inside one
  turn, only findings do, a lane never gets its own output back, and the ledger
  does not outlive the utterance.
- **the join, end to end** — an agent turn where the model calls `research` and
  then `run_claude_code` *without* passing the findings along still hands them
  to the coding lane. That's the Definition of done's "without manual
  sequencing", checked at the layer that guarantees it rather than at the model
  that's asked nicely.
- **what the research lane may touch** — the argv it launches is read-only,
  MCP-free, web-tools-only, and the allowed-tools flag is one argv entry (Phase
  3 lost a live run to that flag's variadic form).
- **backend routing** — a lane can be given its own brain, and an explicitly
  constructed backend still beats everything.

    .venv/bin/python3 scripts/selftest_agents.py

What this can't tell you is whether a *small model* actually sequences two
lanes when you say one sentence out loud. That needs a real backend, and it's
scripts/try_agents.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Before jarvis is imported: an agent turn writes to the memory store, and this
# file runs several. See Phase 6's note in the tracker.
os.environ["JARVIS_MEMORY_ENABLED"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis import agents, claude_code, config, followup, orchestrator  # noqa: E402
from jarvis import tools as tool_layer  # noqa: E402
from jarvis.agent import MAX_STEPS, Agent  # noqa: E402
from jarvis.agents import base, coding, research  # noqa: E402
from jarvis.router.base import Completion, Message, ToolCall  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(f"{label}{f' — {detail}' if detail else ''}")


class ScriptedBackend:
    """Replies with whatever it was handed, and keeps what it was asked."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, completions) -> None:
        self._completions = list(completions)
        self.seen: list[list[Message]] = []

    def complete(self, messages, tools_, system=""):
        self.seen.append(list(messages))
        return self._completions.pop(0) if self._completions else Completion(text="done")


class patched:
    """Swap attributes on a module for the duration of a `with` block."""

    def __init__(self, target, **attributes) -> None:
        self.target = target
        self.attributes = attributes
        self.saved: dict = {}

    def __enter__(self):
        for name, value in self.attributes.items():
            self.saved[name] = getattr(self.target, name)
            setattr(self.target, name, value)
        return self.target

    def __exit__(self, *_exc) -> None:
        for name, value in self.saved.items():
            setattr(self.target, name, value)


# -- the lane registry --------------------------------------------------------

print("lanes: who owns what")

check("there are three lanes", sorted(agents.LANE_NAMES) == ["browser", "coding", "research"],
      str(agents.LANE_NAMES))

owned = [name for lane in agents.LANES.values() for name in lane.tools]
check("no tool is owned by two lanes", len(owned) == len(set(owned)), str(owned))
check(
    "every owned tool actually exists",
    set(owned) <= set(tool_layer.TOOL_NAMES),
    str(set(owned) - set(tool_layer.TOOL_NAMES)),
)
unowned = [
    name
    for name in tool_layer.TOOL_NAMES
    if agents.lane_for_tool(name) is None and name not in agents.UNOWNED_TOOLS
]
check(
    "every tool is owned by a lane or is deliberately the main loop's own",
    unowned == [],
    f"orphans: {unowned}",
)
check(
    "the main loop's own tools are the instant ones, and none of them is a sub-agent",
    all(agents.lane_for_tool(name) is None for name in agents.UNOWNED_TOOLS),
)
check(
    "run_claude_code belongs to the coding lane",
    agents.lane_for_tool("run_claude_code").name == "coding",
)
check("research belongs to the research lane", agents.lane_for_tool("research").name == "research")
check(
    "both browser tools belong to the browser lane",
    agents.lane_for_tool("open_portal").name == "browser"
    and agents.lane_for_tool("fill_login_form").name == "browser",
)
check("an unknown tool has no lane", agents.lane_for_tool("nonesuch") is None)
check(
    "every lane name is a key the routing config offers a backend for",
    set(agents.LANE_NAMES) | {orchestrator.CHAT}
    <= set(config.DEFAULTS["agents"]["routing"]["backends"]),
    str(config.DEFAULTS["agents"]["routing"]["backends"]),
)


# -- classification -----------------------------------------------------------

print("\nclassification: which lane does this sound like")

cases = [
    ("fix the bug in project jarvis", "coding"),
    ("ask claude code why the login test fails", "coding"),
    ("log in to the billing portal", "browser"),
    ("my username is alice", "browser"),
    ("look that up for me", "research"),
    ("what's the latest version of playwright", "research"),
    ("open github", orchestrator.CHAT),
    ("what time is it", orchestrator.CHAT),
    ("", orchestrator.CHAT),
]
for sentence, expected in cases:
    got = orchestrator.classify(sentence)
    check(f"{sentence or '(silence)'!r} -> {expected}", got == expected, f"got {got}")

compound = "look up how the requests API changed, then fix the bug in my project"
check(
    "a two-lane request names both lanes",
    orchestrator.lanes_named(compound) == ("coding", "research"),
    str(orchestrator.lanes_named(compound)),
)
check(
    "...and is routed as the heavier one, because that's the brain it needs",
    orchestrator.classify(compound) == "coding",
)
check(
    "classification never returns something that isn't a real routing key",
    all(
        orchestrator.classify(text) in set(agents.LANE_NAMES) | {orchestrator.CHAT}
        for text in ("", "   ", "?!", "log in and fix the bug and look it up", "ünïcödé")
    ),
)


# -- the handoff ledger -------------------------------------------------------

print("\nhandoff: what one lane found, the next one is told")

ledger = orchestrator.Handoff()
check("an empty ledger is falsy", not ledger)
check("...and carries nothing", ledger.context_for("coding") == "")

ledger.record("research", "requests 2.34 renamed `json=` to `body=`")
check("a ledger with something in it is truthy", bool(ledger))
check(
    "findings reach the next lane",
    "renamed" in ledger.context_for("coding"),
    ledger.context_for("coding"),
)
check(
    "a lane is never handed its own output back",
    ledger.context_for("research") == "",
)

ledger.record("browser", "Opened https://example.com")
check(
    "status from another lane is not passed on as if it were a finding",
    "Opened https://example.com" not in ledger.context_for("coding"),
    ledger.context_for("coding"),
)
ledger.record("coding", "Claude Code finished")
check(
    "lanes_used is in order, without repeats",
    ledger.lanes_used() == ("research", "browser", "coding"),
    str(ledger.lanes_used()),
)

big = orchestrator.Handoff()
big.record("research", "x" * (orchestrator.MAX_CONTEXT_CHARS * 3))
check(
    "a runaway result is capped before it reaches the next lane",
    len(big.context_for("coding")) <= orchestrator.MAX_CONTEXT_CHARS + 20,
    str(len(big.context_for("coding"))),
)
check(
    "...and says it was cut, rather than ending mid-sentence in silence",
    big.context_for("coding").endswith("(truncated)"),
)
check(
    "empty records are ignored, so a silent tool can't blank the context",
    (lambda l: (l.record("research", ""), l.record("", "x"), not l)[-1])(
        orchestrator.Handoff()
    ),
)

check(
    "findings are labelled as another agent's work, not stated as fact",
    "research agent" in base.with_context("fix it", "the API changed"),
    base.with_context("fix it", "the API changed"),
)
check(
    "...and a brief with no context is left exactly as it was",
    base.with_context("fix it", "") == "fix it",
)


# -- the join, end to end -----------------------------------------------------

print("\nthe join: two lanes in one utterance, nobody sequencing them")

calls: list[tuple[str, dict]] = []


def fake_research(question: str = "", *, dry_run: bool = False, on_status=None) -> str:
    calls.append(("research", {"question": question}))
    return "Findings: version 2.34 renamed `json=` to `body=`."


def fake_coding(project: str, task: str, context: str = "", **kwargs) -> str:
    calls.append(("run_claude_code", {"project": project, "task": task, "context": context}))
    return "Claude Code finished: fixed it."


handlers = dict(tool_layer.HANDLERS)
handlers["research"] = fake_research
handlers["run_claude_code"] = fake_coding

two_lane = [
    Completion(
        tool_calls=(ToolCall(id="1", name="research", arguments={"question": "did it change?"}),)
    ),
    # Deliberately forgetful: the model asks for the coding lane and does NOT
    # pass the findings along. This is the failure the ledger exists to cover.
    Completion(
        tool_calls=(
            ToolCall(
                id="2",
                name="run_claude_code",
                arguments={"project": "jarvis", "task": "fix the call"},
            ),
        )
    ),
    Completion(text="Looked it up and fixed it."),
]

with patched(tool_layer, HANDLERS=handlers):
    backend = ScriptedBackend(two_lane)
    result = Agent(backend=backend, on_status=lambda _m: None).handle(compound)

check("the turn succeeded", result.ok, result.error)
check("both lanes ran, in order", result.lanes == ("research", "coding"), str(result.lanes))
check("the summary line says which lanes ran", "research+coding" in result.summary())
coded = next(args for name, args in calls if name == "run_claude_code")
check(
    "the coding lane was handed the lookup the model forgot to pass on",
    "renamed" in coded["context"],
    coded["context"],
)
check(
    "...and the task itself was left alone",
    coded["task"] == "fix the call",
    coded["task"],
)

# The opposite case: the model does pass context. Its version must win — it saw
# the conversation, and second-guessing it here would make the argument useless.
calls.clear()
with patched(tool_layer, HANDLERS=handlers):
    backend = ScriptedBackend(
        [
            Completion(
                tool_calls=(ToolCall(id="1", name="research", arguments={"question": "q"}),)
            ),
            Completion(
                tool_calls=(
                    ToolCall(
                        id="2",
                        name="run_claude_code",
                        arguments={
                            "project": "jarvis",
                            "task": "fix it",
                            "context": "only what the model chose",
                        },
                    ),
                )
            ),
            Completion(text="Done."),
        ]
    )
    Agent(backend=backend, on_status=lambda _m: None).handle(compound)
coded = next(args for name, args in calls if name == "run_claude_code")
check(
    "context the model did supply is not overwritten",
    coded["context"] == "only what the model chose",
    coded["context"],
)

# And a one-lane turn must be unchanged from Phase 6 — no phantom context.
calls.clear()
with patched(tool_layer, HANDLERS=handlers):
    backend = ScriptedBackend(
        [
            Completion(
                tool_calls=(
                    ToolCall(
                        id="1",
                        name="run_claude_code",
                        arguments={"project": "jarvis", "task": "fix it"},
                    ),
                )
            ),
            Completion(text="Done."),
        ]
    )
    single = Agent(backend=backend, on_status=lambda _m: None).handle("fix the bug in my project")
coded = next(args for name, args in calls if name == "run_claude_code")
check("a one-lane turn passes no context at all", coded["context"] == "", coded["context"])
check("...and reports one lane", single.lanes == ("coding",), str(single.lanes))

check(
    "the step budget fits a lookup, a coding run and a sentence about both",
    MAX_STEPS >= 3,
    str(MAX_STEPS),
)


# -- the coding lane ----------------------------------------------------------

print("\nthe coding lane: what Claude Code is actually given")

given: dict = {}


def fake_claude_run(directory, task, *, dry_run=False, on_status=None):
    given["directory"] = directory
    given["task"] = task
    return "Claude Code finished."


# The launcher itself is faked, not the lane: what this section is about is what
# the lane *hands* Claude Code, and nothing here should start a subprocess.
with patched(claude_code, run=fake_claude_run):
    out = coding.run(
        "fix the retry loop",
        context="requests 2.34 renamed `json=` to `body=`",
        directory=Path("/tmp/whatever"),
    )
    check("the lane reports what came back", "finished" in out, out)
    check("the brief survives", "fix the retry loop" in given["task"], given["task"])
    check(
        "the findings are in the task Claude Code sees — this is the whole join",
        "renamed" in given["task"],
        given["task"],
    )
    check(
        "...labelled as a lead to verify, not as fact",
        "verify against the actual" in given["task"],
    )

    check(
        "a brief with no project and no directory is an error, not a guess",
        coding.run("fix it").startswith("Error:"),
    )

    # An unresolvable project becomes a question — and the parked task keeps the
    # context, so a lookup done before the question isn't lost while you answer.
    followup.clear()
    parked = coding.run(
        "fix the retry loop",
        context="requests 2.34 renamed `json=` to `body=`",
        project="a project that does not exist anywhere",
    )
    pending = followup.peek()
    check("an unfindable project parks a question", pending is not None, parked)
    check(
        "...and the parked task still carries the findings",
        pending is not None and "renamed" in pending.task,
        pending.task if pending else "",
    )
    followup.clear()


# -- the research lane --------------------------------------------------------

print("\nthe research lane: what it is allowed to touch")

argv = research.build_command("what changed in requests 2.34?")
check("it runs headlessly — no window, no keyboard", "--print" in argv)
check("...streaming, which needs --verbose", "--verbose" in argv and "stream-json" in argv)
check("...and denies rather than blocking on a prompt nobody can answer",
      "--permission-prompts" in argv and "none" in argv)
check(
    "it cannot write a file: --restricted, the flag Phase 3 measured as the one that held",
    "--restricted" in argv,
)
check("...and an MCP server can't hand it one either", "--strict-mcp-config" in argv)
check(
    "the only tools it has are the web ones",
    f"--allowedTools={research.WEB_TOOLS}" in argv,
    str(argv),
)
check(
    "the allowed-tools flag is ONE argv entry — the variadic form eats the question",
    not any(item == "--allowedTools" for item in argv),
)
check("the question is what it's asked", "requests 2.34" in " ".join(argv))
check(
    "a pinned model is passed through",
    research.build_command("q", settings={"cli": "claude", "model": "claude-haiku-4-5"})[-2:]
    == ["--model", "claude-haiku-4-5"],
)
check(
    "a budget cap is passed through",
    "--max-budget-usd"
    in research.build_command("q", settings={"cli": "claude", "max_budget_usd": 0.5}),
)

check(
    "a dry run looks nothing up",
    research.run("what changed?", dry_run=True).startswith("(dry run"),
)
check(
    "...and tells the model not to answer from memory instead",
    "do not answer the question yourself" in research.run("what changed?", dry_run=True),
)
check("an empty question is an error, not a search", research.run("").startswith("Error:"))

with patched(
    research,
    _settings=lambda: {"enabled": False, "cli": "claude", "timeout": 1},
):
    off = research.run("what changed?")
check("lookups can be switched off", "switched off" in off, off)
check("...and being off is said out loud, not answered around", "do not answer" in off)


# -- backend routing ----------------------------------------------------------

print("\nrouting: a lane can have its own brain")

base_config = config.load_config()


def config_with(routing: dict) -> dict:
    merged = dict(base_config)
    merged["agents"] = {"research": base_config["agents"]["research"], "routing": routing}
    return merged


with patched(
    config,
    load_config=lambda: config_with(
        {"enabled": True, "backends": {"coding": "claude", "chat": None}}
    ),
):
    check(
        "a lane with a backend configured gets it",
        config.default_backend("coding") == "claude",
        config.default_backend("coding"),
    )
    check(
        "a lane with nothing configured falls through to the global default",
        config.default_backend("chat") == base_config["backend"],
        config.default_backend("chat"),
    )
    check(
        "an unknown lane falls through too, so a wrong guess costs nothing",
        config.default_backend("nonsense") == base_config["backend"],
    )
    with patched(
        config,
        load_config=lambda: config_with(
            {"enabled": False, "backends": {"coding": "claude"}}
        ),
    ):
        check(
            "routing can be switched off entirely",
            config.default_backend("coding") == base_config["backend"],
        )

    # And the override that must always hold: a backend handed in explicitly is
    # the backend that runs, whatever any config says about lanes.
    fake = ScriptedBackend([Completion(text="hi")])
    agent = Agent(backend=fake, on_status=lambda _m: None)
    check(
        "an explicitly constructed backend beats every routing rule",
        agent._backend_for("coding") is fake,
    )
    named = Agent(backend="openrouter")
    check(
        "...as does a backend named on the command line",
        named._backend_for("coding").name == "openrouter",
    )


print()
if failures:
    print(f"{len(failures)} FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("All Phase 7 sub-agent checks passed.")
