#!/usr/bin/env python3
"""
Offline self-test for Phase 6's memory.

No network, no model, no microphone — everything here runs against a temporary
database in a temporary directory, so it can be run as often as you like and
can never read or write the real `memory/jarvis.db`. That isolation is a rule
rather than a nicety: a check that passes because of something you said to
Jarvis last week has told you nothing.

What it pins, in the order the module's own docstring argues them:

- **the store** — aliases and preferences survive a round trip, a name is found
  however Whisper capitalised it, and re-teaching a name replaces it.
- **selective retrieval** — only the aliases actually named in a sentence come
  back, capped, most-used first. This is doc 02's "not dumped wholesale", and
  it is the one property that keeps latency flat as the store grows.
- **the conversation window** — a turn count, a TTL, oldest-first ordering, and
  a "forget this" that works.
- **what must never be stored** — a dictated credential is masked out of a turn
  before the turn reaches the disk, whatever sentence it arrived in. Doc 04's
  rule, checked at the layer that enforces it.
- **the preference boundary** — a preference can change the voice; it cannot
  change whether the coding sub-agent may edit files, and it never beats an
  environment variable.
- **degradation** — memory turned off, or a database that can't be written,
  costs nothing but memory. Jarvis still answers.
- **the seams** — jarvis.projects reads both alias layers with the config file
  winning; the agent loop puts history in front of the model, and stores a turn
  only when there was really a turn.

    .venv/bin/python3 scripts/selftest_memory.py

What this can't tell you is whether a *small model* uses the context well —
whether "open my project" actually becomes an open_project call. That needs a
real backend, and it's scripts/try_memory.py.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis import config, credentials, memory, projects, redact  # noqa: E402
from jarvis import tools as tool_layer  # noqa: E402
from jarvis.agent import Agent  # noqa: E402
from jarvis.router.base import Completion, Message  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(f"{label}{f' — {detail}' if detail else ''}")


class TempMemory:
    """A memory store in a temporary directory, plus whatever config it needs.

    Patches `config.load_config` wholesale rather than writing a config file:
    every accessor in jarvis.config reads through it, so one patch covers the
    memory block, the actions block and the preference layer at once.
    """

    def __init__(self, directory: Path, **overrides) -> None:
        self.settings = {
            "enabled": True,
            "path": str(directory / "jarvis.db"),
            "history_turns": 6,
            "history_ttl_seconds": 900,
            "max_facts": 6,
        }
        self.settings.update(overrides)
        self.extra: dict = {}
        self._original = None

    def __enter__(self) -> "TempMemory":
        self._original = config.load_config
        config.load_config = lambda: {"memory": self.settings, **self.extra}
        memory.reset_for_tests()
        return self

    def __exit__(self, *_exc) -> None:
        config.load_config = self._original
        memory.reset_for_tests()


class ScriptedBackend:
    """Answers with whatever it was given, and keeps what it was asked."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, completions) -> None:
        self._completions = list(completions)
        self.seen: list[list[Message]] = []

    def complete(self, messages, tools_, system=""):
        self.seen.append(list(messages))
        return self._completions.pop(0) if self._completions else Completion(text="done")


# -- the store ----------------------------------------------------------------

print("aliases: what Jarvis is told, it keeps")

with tempfile.TemporaryDirectory() as tmp:
    with TempMemory(Path(tmp)):
        check("a fresh store is empty", memory.aliases() == [])
        check("remembering succeeds", memory.remember_alias("my project", "/tmp/x"))
        check(
            "...and comes back",
            memory.alias_map() == {"my project": "/tmp/x"},
            str(memory.alias_map()),
        )
        found = memory.lookup("My Project")
        check("a name is found however it was capitalised", found is not None)
        check("...with the right value", found and found.value == "/tmp/x")
        check("an unknown name is None, not an error", memory.lookup("nothing") is None)

        memory.remember_alias("my project", "/tmp/y")
        check(
            "re-teaching a name replaces it — saying it again is how you correct it",
            memory.alias_map() == {"my project": "/tmp/y"},
            str(memory.alias_map()),
        )
        check("an empty name is refused", not memory.remember_alias("", "/tmp/z"))
        check("an empty value is refused", not memory.remember_alias("x", ""))

        memory.remember_alias("the billing portal", "https://billing.example.com", memory.PORTAL)
        check(
            "kinds are kept apart, because they're opened differently",
            memory.alias_map(memory.PROJECT) == {"my project": "/tmp/y"}
            and list(memory.alias_map(memory.PORTAL)) == ["the billing portal"],
            str(memory.alias_map(memory.PORTAL)),
        )
        check(
            "a project alias reads as a folder when it reaches the model",
            "a project folder at /tmp/y" in memory.lookup("my project").as_fact(),
            memory.lookup("my project").as_fact(),
        )
        check(
            "a portal alias reads as an address",
            "the web address" in memory.lookup("the billing portal").as_fact(),
        )
        check("forgetting one works", memory.forget_alias("the billing portal"))
        check("...and only that one", list(memory.alias_map()) == ["my project"])
        check("forgetting what isn't there is False, not an error",
              not memory.forget_alias("the billing portal"))


# -- selective retrieval ------------------------------------------------------

print("\nretrieval: only what this sentence is about")

with tempfile.TemporaryDirectory() as tmp:
    with TempMemory(Path(tmp)) as store:
        memory.remember_alias("my project", "/tmp/jarvis")
        memory.remember_alias("my thesis", "/tmp/thesis")
        memory.remember_alias("the billing portal", "https://billing.example.com", memory.PORTAL)

        check(
            "a sentence that names one alias recalls exactly that one",
            [alias.name for alias in memory.relevant_aliases("open my project")] == ["my project"],
            str(memory.recall("open my project")),
        )
        check(
            "a sentence that names none recalls nothing at all",
            memory.recall("what time is it") == [],
            str(memory.recall("what time is it")),
        )
        check(
            "capitalisation and punctuation don't hide a match",
            memory.recall("Open My Project, please.") != [],
        )
        check(
            "a sentence naming two gets both",
            len(memory.recall("put my thesis in my project")) == 2,
            str(memory.recall("put my thesis in my project")),
        )
        check(
            "a portal is recalled the same way a project is",
            "billing.example.com" in " ".join(memory.recall("log in to the billing portal")),
            str(memory.recall("log in to the billing portal")),
        )

        # The cap is the backstop that stops a store which has grown for a year
        # from lengthening every prompt. Checked with a store that would
        # otherwise overflow it.
        store.settings["max_facts"] = 2
        for index in range(5):
            memory.remember_alias(f"thing{index}", f"/tmp/{index}")
        said = " ".join(f"thing{index}" for index in range(5))
        check(
            "however many match, only max_facts are offered",
            len(memory.relevant_aliases(said)) == 2,
            str(memory.recall(said)),
        )

        store.settings["max_facts"] = 6
        for _ in range(3):
            memory.lookup("thing4")
        check(
            "the ones you actually use come first",
            memory.relevant_aliases(said)[0].name == "thing4",
            str([alias.name for alias in memory.relevant_aliases(said)]),
        )


# -- the conversation window --------------------------------------------------

print("\nhistory: a few minutes of conversation, and no more")

with tempfile.TemporaryDirectory() as tmp:
    with TempMemory(Path(tmp), history_turns=4) as store:
        check("a fresh window is empty", memory.history() == [])
        memory.remember_turn("user", "open github")
        memory.remember_turn("assistant", "Opened it.")
        window = memory.history()
        check("both halves of an exchange are kept", len(window) == 2, str(window))
        check(
            "oldest first — the newest thing said is the last message the model reads",
            [turn.content for turn in window] == ["open github", "Opened it."],
            str([turn.content for turn in window]),
        )
        check("roles survive", [turn.role for turn in window] == ["user", "assistant"])

        for index in range(10):
            memory.remember_turn("user", f"turn {index}")
        check(
            "the window never grows past history_turns",
            len(memory.history()) == 4,
            str(len(memory.history())),
        )
        check(
            "...and it's the most recent ones that are kept",
            memory.history()[-1].content == "turn 9",
            memory.history()[-1].content,
        )

        check("a turn with no words isn't a turn", not memory.remember_turn("user", "   "))
        check(
            "only the two conversational roles are stored",
            not memory.remember_turn("tool", "some tool output"),
        )

        store.settings["history_turns"] = 0
        check("history_turns: 0 turns the window off entirely", memory.history() == [])
        store.settings["history_turns"] = 4

        # The TTL is the property jarvis.followup and jarvis.confirm each spend
        # a docstring on: something said an hour ago must not become context for
        # something said now.
        store.settings["history_ttl_seconds"] = 0.01
        time.sleep(0.05)
        check(
            "a stale conversation drops out rather than ambushing a later one",
            memory.history() == [],
            str(memory.history()),
        )
        store.settings["history_ttl_seconds"] = 900
        check("...and it was the TTL, not deletion — they're still there",
              memory.history() != [])

        memory.clear_history()
        check("forgetting the conversation works", memory.history() == [])
        check("...and leaves what Jarvis has learned alone",
              memory.remember_alias("kept", "/tmp/k") and memory.alias_map() == {"kept": "/tmp/k"})


# -- what must never be stored ------------------------------------------------

print("\ncredentials: the one thing that never reaches the disk")

with tempfile.TemporaryDirectory() as tmp:
    with TempMemory(Path(tmp)):
        memory.remember_turn("user", "my password is hunter2")
        stored = " ".join(turn.content for turn in memory.history())
        check("a spoken 'my password is ...' is stored masked", stored != "", stored)
        check("...with the characters gone", "hunter2" not in stored, stored)
        check("...and said to be gone, so the model isn't left guessing",
              redact.PLACEHOLDER in stored, stored)
        memory.clear_history()

        # The other half of doc 04: a value the credential layer has registered
        # is redacted wherever it appears, in any phrasing — not only after the
        # word "password".
        redact.register("swordfish99")
        try:
            memory.remember_turn("user", "type swordfish99 into the box")
            stored = " ".join(turn.content for turn in memory.history())
            check("a registered secret is masked in any phrasing",
                  "swordfish99" not in stored and stored != "", stored)
        finally:
            redact.discard("swordfish99")
        memory.clear_history()

        check(
            "an ordinary sentence that merely mentions the word is kept whole",
            memory.remember_turn("user", "there's no password field on that page")
            and memory.history()[0].content == "there's no password field on that page",
            str(memory.history()),
        )
        memory.clear_history()

        # The end-to-end version, and the reason this is masking rather than
        # refusing: a spoken login is four turns long, and the window is worth
        # nothing if the turns that explain it are the ones missing.
        captured = credentials.capture("log in as alice, my password is correcthorse")
        if captured is None:
            check("a dictated credential is captured before anything is stored", False,
                  "credentials.capture returned None")
        else:
            try:
                check(
                    "the transcript the agent passes on is storable",
                    memory.remember_turn("user", captured.redacted),
                )
                memory.remember_turn("user", "log in as alice, my password is correcthorse")
                stored = " ".join(turn.content for turn in memory.history())
                check(
                    "...and however the sentence reaches it, the password does not",
                    "correcthorse" not in stored,
                    stored,
                )
                check(
                    "what survives is enough to know a login was in progress",
                    "alice" in stored,
                    stored,
                )
            finally:
                credentials.clear()


# -- preferences --------------------------------------------------------------

print("\npreferences: what you set out loud, and what you don't get to")

with tempfile.TemporaryDirectory() as tmp:
    with TempMemory(Path(tmp)) as store:
        check("setting one works", memory.set_preference("speech.backends.say.voice", "Daniel"))
        check(
            "...and reading it back gives the same thing",
            memory.preference("speech.backends.say.voice") == "Daniel",
        )
        check("an unset preference is the default", memory.preference("nope", "fallback") == "fallback")

        store.extra = {"speech": dict(config.DEFAULTS["speech"])}
        check(
            "a stored voice reaches the speech layer",
            config.speech_config()["backends"]["say"]["voice"] == "Daniel",
            str(config.speech_config()["backends"]["say"]),
        )

        # The boundary. A preference is set from a transcript, so it must not be
        # able to reach the settings that decide what Jarvis may do to files.
        memory.set_preference("actions.claude_code.allow_edits", "true")
        store.extra["actions"] = {"claude_code": {"allow_edits": False}}
        check(
            "a spoken preference cannot switch on file edits",
            config.actions_config("claude_code")["allow_edits"] is False,
            str(config.actions_config("claude_code")),
        )
        check(
            "...because that path simply isn't in the allowed list",
            "actions.claude_code.allow_edits" not in config.PREFERENCE_KEYS,
        )
        check(
            "nor is the containment boundary itself",
            "actions.projects.roots" not in config.PREFERENCE_KEYS,
        )

        # An environment variable is something you configured on purpose; a
        # sentence said months ago is not.
        memory.set_preference("backend", "claude")
        store.extra["backend"] = "ollama"
        os.environ["JARVIS_BACKEND"] = "ollama"
        try:
            check(
                "an environment variable beats a spoken preference",
                config.default_backend() == "ollama",
                config.default_backend(),
            )
        finally:
            del os.environ["JARVIS_BACKEND"]
        check(
            "...but with nothing in the environment, the preference wins",
            config.default_backend() == "claude",
            config.default_backend(),
        )

        check("a preference can be cleared", memory.clear_preference("backend"))
        check("...and then the file is back in charge", config.default_backend() == "ollama")

        # doc 01's "preferred model backend per task type". Stored and honoured;
        # nothing classifies a task type yet, which is Phase 7's job.
        memory.set_backend_for("coding", "claude")
        check("a per-task-type backend is stored", memory.backend_for("coding") == "claude")
        check("...and honoured when a type is passed in",
              config.default_backend("coding") == "claude", config.default_backend("coding"))
        check("an unset task type falls through to the normal answer",
              config.default_backend("research") == "ollama",
              config.default_backend("research"))


# -- degradation --------------------------------------------------------------

print("\ndegradation: memory failing is not Jarvis failing")

with tempfile.TemporaryDirectory() as tmp:
    with TempMemory(Path(tmp), enabled=False):
        check("nothing is written when memory is off",
              not memory.remember_alias("my project", "/tmp/x"))
        check("nothing is read either", memory.aliases() == [] and memory.history() == [])
        check("no turn is stored", not memory.remember_turn("user", "hello"))
        check("no preference is stored", not memory.set_preference("backend", "claude"))
        check("recall is simply empty", memory.recall("open my project") == [])
        check("and it says so plainly", memory.describe() == "memory is off")

with tempfile.TemporaryDirectory() as tmp:
    unwritable = Path(tmp) / "locked"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    try:
        with TempMemory(Path(tmp), path=str(unwritable / "sub" / "jarvis.db")):
            raised = ""
            try:
                stored = memory.remember_alias("my project", "/tmp/x")
                read = memory.aliases()
            except Exception as exc:  # noqa: BLE001 - that's what's being checked
                raised, stored, read = repr(exc), None, None
            check("a database that can't be created never raises", raised == "", raised)
            check("...it just doesn't remember", stored is False and read == [], f"{stored} {read}")
    finally:
        unwritable.chmod(0o700)


# -- the seam with jarvis.projects --------------------------------------------

print("\njarvis.projects: two alias layers, and the containment check survives")

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp).resolve()
    (home / "roots" / "learned thing").mkdir(parents=True)
    (home / "roots" / "typed thing").mkdir(parents=True)
    (home / "outside").mkdir()

    with TempMemory(home) as store:
        store.extra = {
            "actions": {
                "projects": {
                    "roots": [str(home / "roots")],
                    "aliases": {"my project": str(home / "roots" / "typed thing")},
                }
            }
        }
        memory.remember_alias("my thesis", str(home / "roots" / "learned thing"))
        check(
            "a learned alias resolves",
            projects.resolve("my thesis") == home / "roots" / "learned thing",
            str(projects.resolve("my thesis")),
        )
        memory.remember_alias("my project", str(home / "roots" / "learned thing"))
        check(
            "on a collision the file you edited by hand wins",
            projects.resolve("my project") == home / "roots" / "typed thing",
            str(projects.resolve("my project")),
        )
        check(
            "projects.learn is the one way an alias is written",
            projects.learn("another", str(home / "roots" / "learned thing"))
            and "another" in memory.alias_map(memory.PROJECT),
        )
        # An alias is an explicit decision, so it may point outside the roots —
        # but a *path* said out loud still may not. Phase 3's boundary, re-checked
        # now that a second layer feeds the same resolver.
        raised = ""
        try:
            projects.resolve(str(home / "outside"))
        except projects.ProjectError as exc:
            raised = str(exc)
        check(
            "a path outside the roots is still refused",
            "outside the folders" in raised,
            raised,
        )


# -- the seam with the tools --------------------------------------------------

print("\njarvis.tools: teaching a name, and using it")

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp).resolve()
    (home / "roots" / "project_jarvis").mkdir(parents=True)

    with TempMemory(home) as store:
        store.extra = {
            "actions": {"projects": {"roots": [str(home / "roots")], "aliases": {}}}
        }
        said = tool_layer.execute(
            "remember", {"name": "my project", "value": "project jarvis"}, dry_run=True
        )
        check("a dry run says what it would do", "Would remember" in said, said)
        check("...and remembers nothing", memory.alias_map() == {}, str(memory.alias_map()))

        said = tool_layer.execute("remember", {"name": "my project", "value": "project jarvis"})
        check("teaching a project name works", "Remembered" in said, said)
        check(
            "what's stored is the folder, not the words — so it can't drift",
            memory.alias_map(memory.PROJECT)
            == {"my project": str(home / "roots" / "project_jarvis")},
            str(memory.alias_map()),
        )
        said = tool_layer.execute("remember", {"name": "x", "value": "a project that isn't there"})
        check(
            "a name that resolves to nothing fails out loud instead of being kept",
            "couldn't save" in said and memory.lookup("x") is None,
            said,
        )
        said = tool_layer.execute(
            "remember", {"name": "the billing portal", "value": "billing.example.com"}
        )
        check(
            "a web address is recognised as one without the model being asked",
            memory.alias_map(memory.PORTAL)
            == {"the billing portal": "https://billing.example.com"},
            str(memory.alias_map(memory.PORTAL)),
        )
        check(
            "a project whose name looks a bit like a TLD isn't mistaken for a site",
            "couldn't save" in tool_layer.execute("remember", {"name": "ab", "value": "ab"}),
        )
        check(
            "both halves are required",
            "Error" in tool_layer.execute("remember", {"name": "", "value": "x"}),
        )

        said = tool_layer.execute("open_project", {"name": "my project"}, dry_run=True)
        check("the remembered name opens the folder", "project_jarvis" in said, said)
        check(
            "an unknown project is something to say, not an exception",
            "couldn't find" in tool_layer.execute("open_project", {"name": "nope"}, dry_run=True),
            tool_layer.execute("open_project", {"name": "nope"}, dry_run=True),
        )
        check(
            "both new tools are on the list the model is shown",
            {"remember", "open_project"} <= set(tool_layer.TOOL_NAMES),
            str(tool_layer.TOOL_NAMES),
        )
        check(
            "every tool the model can name has a handler",
            set(tool_layer.TOOL_NAMES) == set(tool_layer.HANDLERS),
        )


# -- the seam with the agent loop ---------------------------------------------

print("\njarvis.agent: the loop is finally conversational")

with tempfile.TemporaryDirectory() as tmp:
    with TempMemory(Path(tmp)):
        backend = ScriptedBackend([Completion(text="Opened it."), Completion(text="Done.")])
        agent = Agent(backend=backend, on_status=lambda _m: None)

        first = agent.handle("open github")
        check("a turn still works exactly as before", first.reply == "Opened it.")
        check(
            "the first utterance of a session carries no history",
            [message.content for message in backend.seen[0]] == ["open github"],
            str([message.content for message in backend.seen[0]]),
        )

        second = agent.handle("now open gitlab")
        check("the second one is given what was just said", len(backend.seen[1]) > 1)
        check(
            "...oldest first, with the new request last",
            [message.content for message in backend.seen[1]]
            == ["open github", "Opened it.", "now open gitlab"],
            str([message.content for message in backend.seen[1]]),
        )
        check("and it was stored", len(memory.history()) == 4, str(len(memory.history())))

with tempfile.TemporaryDirectory() as tmp:
    with TempMemory(Path(tmp)) as store:
        store.extra = {"actions": {"projects": {"roots": [tmp], "aliases": {}}}}
        memory.remember_alias("my project", "/tmp/jarvis")
        backend = ScriptedBackend([Completion(text="Opening it.")])
        Agent(backend=backend, on_status=lambda _m: None).handle("open my project")
        said = " ".join(message.content for message in backend.seen[0])
        check(
            "an alias named in the sentence is stated to the model",
            "/tmp/jarvis" in said,
            said,
        )
        check(
            "...as context, not as the request — the request is still last",
            backend.seen[0][-1].content == "open my project",
            backend.seen[0][-1].content,
        )

        backend = ScriptedBackend([Completion(text="No idea.")])
        Agent(backend=backend, on_status=lambda _m: None).handle("what time is it")
        check(
            "a sentence about nothing Jarvis knows gets no facts at all",
            "/tmp/jarvis" not in " ".join(m.content for m in backend.seen[0]),
        )

with tempfile.TemporaryDirectory() as tmp:
    with TempMemory(Path(tmp)):
        backend = ScriptedBackend([Completion(text="Opened it.")])
        Agent(backend=backend, dry_run=True, on_status=lambda _m: None).handle("open github")
        check("a dry run leaves no trace in the conversation", memory.history() == [])

        class BrokenBackend:
            name = "broken"
            model = "broken-1"

            def complete(self, messages, tools_, system=""):
                from jarvis.router import BackendError

                raise BackendError("no network")

        failed = Agent(backend=BrokenBackend(), on_status=lambda _m: None).handle("open github")
        check("a failed turn is reported", not failed.ok)
        check(
            "...and is not remembered — an unanswered question must not become context",
            memory.history() == [],
            str(memory.history()),
        )
        check("an empty transcript is still a no-op", Agent(
            backend=ScriptedBackend([]), on_status=lambda _m: None).handle("   ").error != "")


print()
if failures:
    print(f"{len(failures)} FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("All Phase 6 memory checks passed.")
