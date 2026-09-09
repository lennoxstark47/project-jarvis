#!/usr/bin/env python3
"""
Phase 4 live harness — does Jarvis actually talk, and actually log in?

scripts/selftest_login.py proves the wiring offline, with a fake browser and a
fake Keychain. This one is the real thing: it makes noise, opens a browser
window, types a password into a page, and writes to your macOS Keychain.

Four things you'd want to check, one flag each:

    # 1. can it talk, and which voice do you want?
    .venv/bin/python3 scripts/try_login.py --speak "Filled in, want me to submit?"
    .venv/bin/python3 scripts/try_login.py --speak "Hello" --voice say --list-voices

    # 2. did the local parser hear the password correctly? (no network at all)
    .venv/bin/python3 scripts/try_login.py --dictate \\
        "username is jsmith, password is Tango-Romeo-Alpha-7-Charlie-9"

    # 3. what does the *model* get sent? (this is doc 04's point 2, visibly)
    .venv/bin/python3 scripts/try_login.py

    # 4. the whole thing, out loud, on a real site — Phase 4's Definition of done
    .venv/bin/python3 scripts/try_login.py --for-real

The default site is https://the-internet.herokuapp.com/login, a public practice
login page that publishes its own test credentials on the page — so a real login
can be proven end to end without putting one of your accounts through an
early-days automation. Point --portal somewhere else once you trust it.

What to watch for in --for-real: the browser fills both fields and *stops*.
Jarvis says "filled in, want me to submit?" out loud, and nothing is submitted
until you answer here. That pause is doc 04's point 4, and it is the whole
safety story for this phase.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis import confirm, credentials, redact, speech, tools, vault  # noqa: E402
from jarvis.agent import Agent  # noqa: E402
from jarvis.router import BACKEND_NAMES  # noqa: E402

DEFAULT_PORTAL = "https://the-internet.herokuapp.com/login"

# Written the way Whisper would hand it over — no punctuation to speak of, the
# password half-spelled. "bang" is the decoder turning a spoken symbol name into
# "!", which is the part of jarvis.credentials worth seeing work.
DEFAULT_COMMAND = (
    "log into the practice portal, username is tomsmith, "
    "password is SuperSecretPassword bang"
)


def status(message: str) -> None:
    print(f"      · {message}", flush=True)


def show_voices() -> None:
    import subprocess

    print("\n  macOS voices available to the 'say' backend:\n")
    result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=False)
    for line in result.stdout.splitlines():
        print(f"    {line}")
    print("\n  Set one in config/jarvis.json under speech.backends.say.voice\n")


def run_speak(args: argparse.Namespace) -> int:
    started = time.monotonic()
    ok = speech.speak(args.speak, voice=args.voice)
    print(f"\n  {'spoke' if ok else 'SAID NOTHING'} in {time.monotonic() - started:.1f}s")
    if not ok:
        print("  (check logs above — is speech.enabled true, and is that backend set up?)")
    return 0 if ok else 1


def run_dictate(args: argparse.Namespace) -> int:
    """Show what the local parser heard. Nothing leaves this machine."""
    capture = credentials.capture(args.dictate)
    if capture is None:
        print("\n  No credential recognised in that sentence.")
        print("  Say it as: '... username is <name>, password is <spelled out>'\n")
        return 1

    print(f"\n  username:  {capture.credential.username!r}")
    if args.show:
        print(f"  password:  {capture.credential.password!r}")
    else:
        password = capture.credential.password
        print(f"  password:  {password[0]}{'*' * (len(password) - 1)} ({len(password)} chars)")
        print("             (--show to print it, if you're somewhere private)")
    print(f"\n  what the model would be sent instead:\n    {capture.redacted}\n")
    credentials.release(capture.ref)
    return 0


def run_keychain(args: argparse.Namespace) -> int:
    site = args.keychain
    if args.forget:
        print(f"\n  {'forgot' if vault.forget(site) else 'nothing saved for'} {site!r}\n")
        return 0
    saved = vault.get(site)
    if saved is None:
        print(f"\n  Nothing saved for {site!r} (key: {vault.site_key(site)!r})\n")
        return 1
    print(f"\n  {site!r} -> key {vault.site_key(site)!r}, username {saved.username!r}, "
          f"password {len(saved.password)} chars (not printed)\n")
    return 0


def run_login(args: argparse.Namespace) -> int:
    dry_run = not args.for_real
    agent = Agent(
        backend=args.backend, model=args.model, dry_run=dry_run, on_status=status
    )

    if args.for_real:
        print(f"\n  opening {args.portal} in Jarvis's browser")
        print(f"  {tools.execute('open_portal', {'url': args.portal}, on_status=status)}")

    command = args.command or DEFAULT_COMMAND
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

    # The credential must not be in any of that — this is the check, not the
    # decoration. Anything the tool call or the reply contains has been through
    # the model, or is about to be shown on screen.
    printed = f"{result.reply} " + " ".join(f"{call} {output}" for call, output in result.actions)
    if redact.redact_secrets(printed) != printed:
        print("\n  *** A CREDENTIAL APPEARED IN THE MODEL-FACING TEXT — that's a bug. ***")
        return 1
    print("    (nothing above contains the credential — as it should be)")

    pending = confirm.peek()
    if pending is None:
        print()
        return 0

    if result.reply:
        speech.speak(result.reply)

    print(f"\n  Jarvis is waiting on: {pending.description}")
    try:
        answer = input("  Answer it the way you would out loud (e.g. 'yes' / 'no'): ").strip()
    except (EOFError, KeyboardInterrupt):
        confirm.clear()
        print("\n  cancelled\n")
        return 0

    # Deliberately back through the agent, not straight to confirm.resolve: the
    # thing being tested is that a spoken answer gets there on its own.
    followup_result = agent.handle(answer)
    print(f"    says: {followup_result.reply}")
    speech.speak(followup_result.reply)

    if args.for_real:
        print("\n  The browser stays open until this process exits — press Enter to close it.")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("command", nargs="?", help="the spoken command to run through the agent")
    parser.add_argument("--speak", metavar="TEXT", help="say TEXT out loud and exit")
    parser.add_argument("--voice", choices=speech.VOICE_NAMES, help="override the configured voice")
    parser.add_argument("--list-voices", action="store_true", help="list macOS 'say' voices")
    parser.add_argument("--dictate", metavar="SENTENCE", help="show what the local parser hears")
    parser.add_argument("--show", action="store_true", help="print the decoded password in full")
    parser.add_argument("--keychain", metavar="SITE", help="show what's saved for SITE")
    parser.add_argument("--forget", action="store_true", help="with --keychain: delete it")
    parser.add_argument("--portal", default=DEFAULT_PORTAL, help="the login page to open")
    parser.add_argument("--backend", choices=BACKEND_NAMES, help="override the configured backend")
    parser.add_argument("--model", help="override that backend's model id")
    parser.add_argument(
        "--for-real",
        action="store_true",
        help="actually open the browser and type the credential in",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show Jarvis's own logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Even here. A harness that leaks the password into your terminal scrollback
    # is the same failure as one that leaks it into a log file.
    redact.install()
    if not args.verbose:
        logging.getLogger("jarvis").setLevel(logging.ERROR)

    if args.list_voices:
        show_voices()
        if not args.speak:
            return 0
    if args.speak:
        return run_speak(args)
    if args.dictate:
        return run_dictate(args)
    if args.keychain:
        return run_keychain(args)

    if not args.for_real:
        print(
            "\n(dry run — no browser, nothing typed, nothing saved to the Keychain; "
            "add --for-real when you mean it)"
        )
    return run_login(args)


if __name__ == "__main__":
    raise SystemExit(main())
