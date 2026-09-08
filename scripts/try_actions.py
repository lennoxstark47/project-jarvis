#!/usr/bin/env python3
"""
Phase 3 live harness — does the action layer actually do the thing?

scripts/selftest_actions.py proves the wiring offline. This one spends real
time and real money: it launches Claude Code, and it opens a real browser
window. Two ways to use it, matching the two questions you'd want answered:

    # 1. the tools on their own — no LLM involved, so a failure is the tool's
    .venv/bin/python3 scripts/try_actions.py --project jarvis \\
        --claude-code "In one sentence, what does src/jarvis/voice.py do?" --for-real
    .venv/bin/python3 scripts/try_actions.py --portal https://example.com --for-real

    # 2. the whole path — spoken sentence in, tool call out (Phase 3's
    #    Definition of done). Dry-runs unless you pass --for-real.
    .venv/bin/python3 scripts/try_actions.py
    .venv/bin/python3 scripts/try_actions.py --for-real \\
        "open project jarvis and use claude code to find out why the menu bar icon sometimes doesn't appear"

Everything dry-runs by default, exactly like scripts/try_brain.py: the point of
a default run is to see *which tool the model picks with what arguments*, and
that's answerable without starting a sub-agent.

Status lines are printed as they arrive, which is also a check in itself — it's
the same stream the menu bar's "Status:" line reads, so if nothing shows up
here, the sub-agent looks hung there too.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis import claude_code, projects, tools  # noqa: E402
from jarvis.agent import Agent  # noqa: E402
from jarvis.config import actions_config  # noqa: E402
from jarvis.router import BACKEND_NAMES  # noqa: E402

# The command Phase 3's Definition of done names, in the shape Whisper would
# hand over: no punctuation, lowercase, project named the way you'd say it.
DEFAULT_COMMAND = (
    "open project jarvis and use claude code to find out why the menu bar icon "
    "sometimes doesn't appear at login"
)


def status(message: str) -> None:
    print(f"      · {message}", flush=True)


def run_tool_directly(args: argparse.Namespace) -> int:
    dry_run = not args.for_real

    if args.claude_code:
        settings = actions_config("claude_code")
        try:
            directory = projects.resolve(args.project)
        except projects.ProjectError as exc:
            print(f"  [FAIL] {exc}")
            return 1
        print(f"  project:    {directory}")
        print(f"  permission: {settings.get('permission_mode')} (config/jarvis.json)")
        print(f"  task:       {args.claude_code}\n")
        started = time.monotonic()
        output = tools.execute(
            "run_claude_code",
            {"project": args.project, "task": args.claude_code},
            dry_run=dry_run,
            on_status=status,
        )
        print(f"\n  ({time.monotonic() - started:.1f}s)\n{output}\n")
        return 0

    print(f"  opening {args.portal} in Jarvis's own browser\n")
    output = tools.execute(
        "open_portal", {"url": args.portal}, dry_run=dry_run, on_status=status
    )
    print(f"\n{output}\n")
    if args.for_real:
        print("  The browser stays open until this process exits — press Enter to close it.")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    return 0


def run_through_agent(args: argparse.Namespace) -> int:
    agent = Agent(
        backend=args.backend, model=args.model, dry_run=not args.for_real, on_status=status
    )
    for command in args.command or [DEFAULT_COMMAND]:
        print(f'\n"{command}"')
        started = time.monotonic()
        result = agent.handle(command)
        elapsed = time.monotonic() - started
        print(f"  [{'ok ' if result.ok else 'FAIL'}] {result.backend}/{result.model}  {elapsed:.1f}s")
        for call, output in result.actions:
            print(f"    tool: {call}")
            print(f"    ->    {output}")
        if result.reply:
            print(f"    says: {result.reply}")
        if result.error:
            print(f"    error: {result.error}")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("command", nargs="*", help="spoken command(s) to run through the agent")
    parser.add_argument("--project", default="jarvis", help="project for --claude-code")
    parser.add_argument("--claude-code", metavar="TASK", help="run the sub-agent tool directly")
    parser.add_argument("--portal", metavar="URL", help="run the browser tool directly")
    parser.add_argument("--backend", choices=BACKEND_NAMES, help="override the configured backend")
    parser.add_argument("--model", help="override that backend's model id")
    parser.add_argument(
        "--for-real",
        action="store_true",
        help="actually launch Claude Code / open the browser instead of dry-running",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show Jarvis's own logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        # The status lines below already say what's happening; the full log
        # would print each of them twice.
        logging.getLogger("jarvis").setLevel(logging.CRITICAL)

    if args.claude_code and args.portal:
        parser.error("--claude-code and --portal test different tools; run them separately")
    if not args.for_real:
        print("\n(dry run — nothing will really be launched; add --for-real when you mean it)")
    if args.claude_code or args.portal:
        return run_tool_directly(args)
    return run_through_agent(args)


if __name__ == "__main__":
    raise SystemExit(main())
