#!/usr/bin/env python3
"""
Phase 6's memory, by hand — see it, edit it, and prove it survives a restart.

scripts/selftest_memory.py pins the mechanism against a temporary database.
This one talks to the **real** `memory/jarvis.db`, which is what makes it
useful for the two things a self-test can't answer:

- what Jarvis currently remembers about *you*, in one screen;
- whether a real model actually uses that memory — whether "open my project",
  said cold with no other context, becomes an `open_project` call.

    # what's in there right now
    .venv/bin/python3 scripts/try_memory.py

    # teach it something, the way the voice tool would
    .venv/bin/python3 scripts/try_memory.py --remember "my project" "project jarvis"
    .venv/bin/python3 scripts/try_memory.py --remember "the billing portal" billing.example.com

    # and take it back
    .venv/bin/python3 scripts/try_memory.py --forget "my project"

    # preferences: which voice, which brain
    .venv/bin/python3 scripts/try_memory.py --set speech.backends.say.voice Daniel
    .venv/bin/python3 scripts/try_memory.py --unset speech.backends.say.voice

    # the Definition of done, end to end, against a real backend.
    # Nothing is opened unless you add --for-real.
    .venv/bin/python3 scripts/try_memory.py --ask "open my project"

    # the conversation window, and clearing it
    .venv/bin/python3 scripts/try_memory.py --history
    .venv/bin/python3 scripts/try_memory.py --forget-conversation

**The Definition of done needs two runs, not one.** "Open my project works days
later without repeating the path" is a claim about a *cold process*, so proving
it means teaching the alias in one run, letting the process exit, and asking in
another — which is what `--remember` and `--ask` being separate invocations is
for. Doing both in one Python process would prove only that a dict still holds
what you put in it.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis import config, memory  # noqa: E402 - path insert must happen first
from jarvis import tools as tool_layer  # noqa: E402
from jarvis.agent import Agent  # noqa: E402


def show() -> None:
    """Everything Jarvis remembers, in the order it would be reached."""
    print(f"database: {memory.db_path()}")
    if not memory.enabled():
        print("memory is off (memory.enabled = false in config/jarvis.json)")
        return
    if not memory.db_path().exists():
        print("(no database yet — it's created the first time something is remembered)")

    stored = memory.aliases()
    print(f"\naliases ({len(stored)}):")
    if not stored:
        print("  (none — teach one with --remember)")
    for alias in stored:
        print(f"  {alias.kind:<7} {alias.name!r} -> {alias.value}   (used {alias.uses}x)")

    # The config file's own aliases are a separate, hand-edited layer that wins
    # on a name collision — worth showing side by side, because "why is my
    # alias not taking effect" is otherwise a genuinely confusing half-hour.
    typed = config.actions_config("projects").get("aliases", {}) or {}
    if typed:
        print(f"\naliases typed into config/jarvis.json ({len(typed)}) — these win:")
        for name, value in typed.items():
            print(f"  project {name!r} -> {value}")

    preferences = memory.preferences()
    print(f"\npreferences ({len(preferences)}):")
    if not preferences:
        print("  (none — the config file and the environment decide everything)")
    for key, value in sorted(preferences.items()):
        reachable = key in config.PREFERENCE_KEYS or key.startswith(memory.BACKEND_FOR_PREFIX)
        note = "" if reachable else "   (stored, but not a setting a preference may change)"
        print(f"  {key} = {value}{note}")

    show_history()


def show_history() -> None:
    window = memory.history()
    settings = memory.settings()
    print(
        f"\nconversation window ({len(window)} turn(s); keeps up to "
        f"{settings.get('history_turns')} within {settings.get('history_ttl_seconds')}s):"
    )
    if not window:
        print("  (empty)")
    for turn in window:
        when = datetime.fromtimestamp(turn.at).strftime("%H:%M:%S")
        print(f"  {when} {turn.role:<9} {turn.content}")


def remember(name: str, value: str, *, dry_run: bool) -> int:
    """Teach an alias through the *tool*, not the store.

    Going through `jarvis.tools.remember` is the point: it's the path a spoken
    sentence takes, so it's the path that resolves a project name to a real
    folder and refuses one that doesn't exist. Writing straight to the store
    would let this script save something the voice path never could.
    """
    print(tool_layer.execute("remember", {"name": name, "value": value}, dry_run=dry_run))
    return 0


def ask(command: str, *, backend: str | None, dry_run: bool) -> int:
    """Put a sentence through the whole loop, and show what memory contributed."""
    facts = memory.recall(command)
    print(f"recalled for this sentence: {facts or '(nothing)'}")
    window = memory.history()
    print(f"history carried in: {len(window)} turn(s)")

    agent = Agent(backend=backend, dry_run=dry_run)
    started = time.monotonic()
    result = agent.handle(command)
    elapsed = time.monotonic() - started

    print(f"\n[{'ok' if result.ok else 'FAIL'}] {result.backend}/{result.model} in {elapsed:.1f}s")
    for call, output in result.actions:
        print(f"  tool: {call}")
        print(f"  ->    {output}")
    if result.reply:
        print(f"  says: {result.reply}")
    if result.error:
        print(f"  error: {result.error}")
    return 0 if result.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remember", nargs=2, metavar=("NAME", "VALUE"),
                        help="teach an alias, exactly as the voice tool would")
    parser.add_argument("--forget", metavar="NAME", help="drop one alias by name")
    parser.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), dest="set_preference",
                        help="store a preference (a dotted config path, e.g. "
                             "speech.backends.say.voice)")
    parser.add_argument("--unset", metavar="KEY", help="clear a preference")
    parser.add_argument("--backend-for", nargs=2, metavar=("TASK_TYPE", "BACKEND"),
                        help="prefer a backend for a kind of task (stored now, routed in Phase 7)")
    parser.add_argument("--ask", metavar="COMMAND", help="run one sentence through the agent loop")
    parser.add_argument("--backend", help="which brain answers --ask (default: the configured one)")
    parser.add_argument("--for-real", action="store_true",
                        help="let --ask and --remember actually act, instead of dry-running")
    parser.add_argument("--history", action="store_true", help="show only the conversation window")
    parser.add_argument("--forget-conversation", action="store_true",
                        help="clear the window, keeping aliases and preferences")
    parser.add_argument("--verbose", action="store_true", help="show Jarvis's own log lines")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not memory.enabled() and (args.remember or args.set_preference or args.backend_for):
        print("memory is off — set memory.enabled to true in config/jarvis.json first.")
        return 1

    dry_run = not args.for_real

    if args.forget_conversation:
        memory.clear_history()
        print("Conversation forgotten. Aliases and preferences are untouched.")
        return 0
    if args.forget:
        print(f"Forgot {args.forget!r}." if memory.forget_alias(args.forget)
              else f"Nothing called {args.forget!r} was stored.")
        return 0
    if args.unset:
        print(f"Cleared {args.unset}." if memory.clear_preference(args.unset)
              else f"{args.unset} wasn't set.")
        return 0
    if args.set_preference:
        key, value = args.set_preference
        if key not in config.PREFERENCE_KEYS:
            # Stored anyway rather than refused: the store is general, and this
            # script is a debugging tool. But say so, because a preference on a
            # path that isn't in the list will sit there doing nothing.
            print(f"note: {key} isn't a setting a preference may change "
                  f"(config.PREFERENCE_KEYS) — storing it, but nothing will read it.")
        memory.set_preference(key, value)
        print(f"{key} = {value}")
        return 0
    if args.backend_for:
        task_type, backend = args.backend_for
        memory.set_backend_for(task_type, backend)
        print(f"{task_type} tasks will prefer {backend} — once something classifies a "
              f"task type, which is Phase 7.")
        return 0
    if args.remember:
        return remember(*args.remember, dry_run=dry_run)
    if args.ask:
        return ask(args.ask, backend=args.backend, dry_run=dry_run)
    if args.history:
        show_history()
        return 0

    show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
