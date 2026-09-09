#!/usr/bin/env python3
"""
Phase 7's sub-agents, by hand — see the lanes, run one, or run the whole join.

scripts/selftest_agents.py pins the mechanism against fakes. This one uses the
real thing, which is what makes it useful for the two questions a self-test
can't answer:

- does a real lookup actually come back with something true and short enough;
- does a *small model*, given one sentence, sequence two lanes on its own?

    # what lanes exist, and which brain each one would use
    .venv/bin/python3 scripts/try_agents.py

    # how a sentence is classified (no model, no network, instant)
    .venv/bin/python3 scripts/try_agents.py --classify "fix the bug in my project"

    # one lane, directly. Nothing runs unless you add --for-real.
    .venv/bin/python3 scripts/try_agents.py --lane research "what changed in requests 2.34" --for-real
    .venv/bin/python3 scripts/try_agents.py --lane browser "example.com" --for-real

    # the Definition of done: one sentence, two sub-agents, nobody sequencing.
    .venv/bin/python3 scripts/try_agents.py --ask \\
        "look up what changed in the requests library, then have claude code check \\
         whether project jarvis uses it" --for-real

**Dry run is the default everywhere**, as in every other try_ script: a lookup
costs real money and a coding run opens a terminal window, and neither should
happen because you pressed up-arrow. `--for-real` is the whole difference.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis import agents, config, orchestrator  # noqa: E402 - path insert first
from jarvis.agent import Agent  # noqa: E402
from jarvis.router import available_backends  # noqa: E402


def show() -> None:
    """The lane table: who owns what, and which brain would run it."""
    routing = config.agents_config("routing")
    print(f"routing: {'on' if routing.get('enabled', True) else 'OFF (every lane uses the default)'}")
    print(f"default brain: {config.default_backend()}\n")
    for name, lane in agents.LANES.items():
        print(f"  {name:<9} -> {config.default_backend(name):<12} {lane.description}")
        print(f"  {'':<9}    tools: {', '.join(lane.tools)}")
    print(f"  {orchestrator.CHAT:<9} -> {config.default_backend(orchestrator.CHAT):<12} "
          f"anything else — an ordinary command or question")
    print(f"\n  the main loop's own tools (no sub-agent): {', '.join(agents.UNOWNED_TOOLS)}")

    research = config.agents_config("research")
    print(f"\nweb lookups: {'on' if research.get('enabled', True) else 'OFF'}"
          f" — {research.get('cli', 'claude')}, model {research.get('model') or '(the CLI default)'}"
          f", timeout {research.get('timeout')}s")


def classify(sentence: str) -> None:
    named = orchestrator.lanes_named(sentence)
    chosen = orchestrator.classify(sentence)
    print(f"  said:    {sentence}")
    print(f"  lanes:   {', '.join(named) if named else '(none matched)'}")
    print(f"  routed:  {chosen}  ->  {config.default_backend(chosen)}")
    if len(named) > 1:
        print("  this is a compound request — the loop is expected to call two tools.")


def run_lane(name: str, brief: str, *, dry_run: bool) -> int:
    lane = agents.lane(name)
    if lane is None:
        print(f"no such lane: {name}. Try one of: {', '.join(agents.LANE_NAMES)}")
        return 2
    print(f"[{name}] {brief}{' (dry run)' if dry_run else ''}\n")
    started = time.monotonic()
    output = lane.run(brief, dry_run=dry_run, on_status=lambda line: print(f"  ... {line}"))
    print(f"\n{output}\n\n({time.monotonic() - started:.1f}s)")
    return 0


def ask(sentence: str, *, backend: str | None, dry_run: bool) -> int:
    """One utterance through the real loop — the same path the microphone uses."""
    agent = Agent(
        backend=backend, dry_run=dry_run, on_status=lambda line: print(f"  ... {line}")
    )
    print(f"you: {sentence}{' (dry run)' if dry_run else ''}\n")
    started = time.monotonic()
    result = agent.handle(sentence)
    elapsed = time.monotonic() - started

    print()
    for call, output in result.actions:
        lane = orchestrator.lane_of(call.name)
        print(f"  [{lane.name if lane else 'main'}] {call}")
        for line in output.splitlines()[:6]:
            print(f"      {line}")
        print()
    print(f"says: {result.reply}")
    print(
        f"\n[{result.backend}/{result.model}] lanes: "
        f"{'+'.join(result.lanes) if result.lanes else '(none)'} | "
        f"{result.steps} step(s) | {elapsed:.1f}s"
    )
    if result.error:
        print(f"error: {result.error}")
    if len(result.lanes) > 1:
        print("\nTwo sub-agents, one sentence, no manual sequencing — that's the "
              "Phase 7 Definition of done.")
    return 0 if result.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--classify", metavar="SENTENCE",
                        help="show which lane a sentence routes to (no model, no network)")
    parser.add_argument("--lane", nargs=2, metavar=("NAME", "BRIEF"),
                        help=f"run one lane directly ({', '.join(agents.LANE_NAMES)})")
    parser.add_argument("--ask", metavar="SENTENCE",
                        help="run one sentence through the whole agent loop")
    parser.add_argument("--backend", choices=available_backends(),
                        help="which brain answers --ask (default: whatever the lane routes to)")
    parser.add_argument("--for-real", action="store_true",
                        help="actually look things up / open things, instead of dry-running")
    parser.add_argument("--verbose", action="store_true", help="show Jarvis's own log lines")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    dry_run = not args.for_real

    if args.classify:
        classify(args.classify)
        return 0
    if args.lane:
        return run_lane(args.lane[0], args.lane[1], dry_run=dry_run)
    if args.ask:
        return ask(args.ask, backend=args.backend, dry_run=dry_run)

    show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
