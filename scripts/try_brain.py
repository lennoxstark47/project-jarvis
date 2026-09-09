#!/usr/bin/env python3
"""
Phase 2 backend comparison harness.

Phase 2's Definition of done is that a command like "open github.com" resolves
to the right tool call on *all three* backends, "so you can compare them
honestly" (docs/01-PHASE_PLAN.md). This is that comparison — same command, same
tool list, same system prompt, three brains, side by side with timings.

    # every backend, on the two Definition-of-done commands (nothing opens)
    .venv/bin/python3 scripts/try_brain.py

    # one backend, your own command
    .venv/bin/python3 scripts/try_brain.py --backend claude "open my email"

    # A/B two model ids on one provider (which NVIDIA model actually tool-calls?)
    .venv/bin/python3 scripts/try_brain.py --backend nvidia --model openai/gpt-oss-120b

    # let it actually open things
    .venv/bin/python3 scripts/try_brain.py --for-real --backend claude "open github.com"

Runs with tools in dry-run mode by default: three backends x two commands is
six browser windows otherwise, and what's being tested here is the *decision*,
not macOS's ability to open a URL.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.agent import Agent  # noqa: E402 - path insert must happen first
from jarvis.router import available_backends  # noqa: E402

# The two commands Phase 2's Definition of done names explicitly.
DEFAULT_COMMANDS = ["open github.com", "open Claude Code"]


def run(backend: str, command: str, *, dry_run: bool, model: str | None) -> None:
    agent = Agent(backend=backend, model=model, dry_run=dry_run)
    started = time.monotonic()
    result = agent.handle(command)
    elapsed = time.monotonic() - started

    status = "ok " if result.ok else "FAIL"
    print(f"  [{status}] {backend:<7} {elapsed:5.1f}s  {result.model}")
    for call, output in result.actions:
        print(f"           tool: {call}")
        print(f"           ->    {output}")
    if result.reply:
        print(f"           says: {result.reply}")
    if result.error:
        print(f"           error: {result.error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("command", nargs="*", help="command(s) to try (default: both DoD commands)")
    parser.add_argument(
        "--backend",
        action="append",
        choices=available_backends(),
        help="backend to try (repeatable; default: all three)",
    )
    parser.add_argument(
        "--model",
        help="override the configured model id for the chosen backend(s)",
    )
    parser.add_argument(
        "--for-real",
        action="store_true",
        help="actually open things instead of dry-running the tools",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show Jarvis's own logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        # Jarvis logs backend failures at ERROR, which would print above the
        # report and say the same thing twice - the per-backend lines below
        # already carry the error. -v puts the full log back.
        logging.getLogger("jarvis").setLevel(logging.CRITICAL)

    backends = args.backend or list(available_backends())
    commands = args.command or DEFAULT_COMMANDS

    for command in commands:
        print(f'\n"{command}"')
        for backend in backends:
            run(backend, command, dry_run=not args.for_real, model=args.model)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
