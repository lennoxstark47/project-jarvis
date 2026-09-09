#!/usr/bin/env python3
"""
Offline self-test for the Phase 4 voice-out + login layer.

Same shape as scripts/selftest_brain.py and scripts/selftest_actions.py: check
everything that can be checked without a browser, a real Keychain write, or a
sound coming out of the speakers. What matters most here isn't "does it fill a
form" — that's scripts/try_login.py's job, live — it's the set of properties
docs/04-CREDENTIALS_AND_SECURITY.md is built on, every one of which fails
*silently* when it breaks:

- a dictated password never reaches the model (doc 04, point 2)
- a dictated password never reaches a log line (point 5)
- nothing submits a login form without a human answer (point 4)
- a 2FA prompt stops the flow rather than being worked around (point 6)

Those are exactly the failures you would not notice by using Jarvis normally —
the login still works while the password is quietly in `logs/jarvis.log` — so
they get tested rather than watched for.

    .venv/bin/python3 scripts/selftest_login.py

Nothing here touches the real Keychain: `jarvis.vault`'s keyring call is swapped
for an in-memory stand-in. scripts/try_login.py exercises the real one.
"""
from __future__ import annotations

import io
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Before any jarvis import: Phase 6's memory reads the environment layer at load
# time, and a self-test must never write into the real memory/jarvis.db — see
# FakeConfig below, and scripts/selftest_memory.py for the checks that do want a
# store (they build their own, in a temporary directory).
os.environ["JARVIS_MEMORY_ENABLED"] = "false"


from jarvis import config, confirm, credentials, login, redact, speech, tools, vault  # noqa: E402
from jarvis.agent import Agent  # noqa: E402
from jarvis.router.base import Completion, ToolCall  # noqa: E402
from jarvis.vault import Credential  # noqa: E402

failures: list[str] = []

# The sentence doc 04 opens with, in the shape Whisper would hand it over.
DICTATION = (
    "open the billing portal, username is jsmith, password is - spelling it out - "
    "Tango-Romeo-Alpha-7-Charlie-9"
)
PASSWORD = "TRA7C9"


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(f"{label}{f' — {detail}' if detail else ''}")


class FakeKeyring:
    """An in-memory stand-in for the macOS Keychain."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, key):
        return self.store.get((service, key))

    def set_password(self, service, key, value):
        self.store[(service, key)] = value

    def delete_password(self, service, key):
        if (service, key) not in self.store:
            raise RuntimeError("no such password")
        del self.store[(service, key)]


class FakeBrowser:
    """A browser session that records what it was asked to type."""

    url = "https://portal.example.com/login"

    def __init__(self, *, submit_result=None, fail=""):
        self.filled: tuple[str, str] | None = None
        self.submitted = False
        self.submit_result = submit_result or {
            "url": "https://portal.example.com/home",
            "title": "Dashboard",
            "navigated": True,
            "still_has_password": False,
            "wants_code": False,
        }
        self.fail = fail

    def page_summary(self):
        return {"url": self.url, "title": "Sign in", "login_form": True}

    def fill_login(self, username, password):
        if self.fail:
            from jarvis.browser import BrowserError

            raise BrowserError(self.fail)
        self.filled = (username, password)
        return {"url": self.url, "title": "Sign in",
                "filled_username": bool(username), "username_field": True}

    def submit_login(self, *, wait_seconds=8.0):
        self.submitted = True
        return self.submit_result


class ScriptedBackend:
    """Answers with canned completions, and keeps every message it was sent."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, completions):
        self._completions = list(completions)
        self.seen: list[str] = []
        self.calls = 0

    def complete(self, messages, tools_, system=""):
        self.calls += 1
        self.seen.extend(message.content for message in messages)
        return self._completions.pop(0) if self._completions else Completion(text="done")


def use_browser(fake: FakeBrowser):
    """Point jarvis.login at a fake browser for the duration of a `with` block."""

    class _Swap:
        def __enter__(self):
            self._original = login.browser.session
            login.browser.session = lambda: fake
            return fake

        def __exit__(self, *_exc):
            login.browser.session = self._original

    return _Swap()


class FakeConfig:
    """Swap in an actions/speech config for the duration of a `with` block.

    Memory is off inside it, and that is not optional. Replacing `load_config`
    wholesale hides the environment layer too, so the `JARVIS_MEMORY_ENABLED`
    set at the top of this file stops applying here — and the confirmation
    checks below run whole agent turns, which would otherwise write "yes" and
    "Logged in" into the user's real memory/jarvis.db. Found exactly that way
    on 2026-09-09.
    """

    def __init__(self, **blocks) -> None:
        self._blocks = {"memory": {"enabled": False}, **blocks}

    def __enter__(self):
        self._original = config.load_config
        config.load_config = lambda: self._blocks
        return self

    def __exit__(self, *_exc) -> None:
        config.load_config = self._original


# --------------------------------------------------------------------------
print("\njarvis.redact — the credential never reaches a log file")

redact.register("hunter2-longer")
check(
    "a registered secret is replaced wherever it appears",
    redact.redact_secrets("typed hunter2-longer into the form") == f"typed {redact.PLACEHOLDER} into the form",
    redact.redact_secrets("typed hunter2-longer into the form"),
)
redact.discard("hunter2-longer")
check(
    "and stops being replaced once discarded",
    "hunter2-longer" in redact.redact_secrets("typed hunter2-longer"),
)
before = redact.registered_count()
redact.register("ab")
check("a too-short value is ignored, so the log stays readable", redact.registered_count() == before)
check(
    "a spoken 'password is ...' is redacted even before anything is registered",
    redact.redact_secrets("username is jsmith, password is Tango Romeo Alpha")
    == f"username is jsmith, password {redact.PLACEHOLDER}",
    redact.redact_secrets("username is jsmith, password is Tango Romeo Alpha"),
)

check(
    "a log line that only mentions a password keeps its meaning",
    redact.redact_secrets("there's no password field on that page")
    == "there's no password field on that page",
    redact.redact_secrets("there's no password field on that page"),
)

# The filter is the point: call-site discipline is what doc 04 says not to rely
# on, so check a real log record written through a real handler.
stream = io.StringIO()
handler = logging.StreamHandler(stream)
handler.setFormatter(logging.Formatter("%(message)s"))
probe = logging.getLogger("jarvis.selftest.redaction")
probe.setLevel(logging.INFO)
probe.propagate = False
probe.addHandler(handler)
redact.install(probe)
redact.register(PASSWORD)
probe.info("filling in %s for %s", PASSWORD, "jsmith")
probe.info("password is %s", "Tango Romeo Alpha")
logged = stream.getvalue()
check("a log line written through the filter carries no secret", PASSWORD not in logged, logged)
check("...and no spoken form of one either", "Tango Romeo" not in logged, logged)
check("...while the rest of the line survives", "for jsmith" in logged, logged)
redact.install(probe)
check("installing twice doesn't stack filters", len(handler.filters) == 1, str(handler.filters))

# --------------------------------------------------------------------------
print("\njarvis.credentials — captured locally, before any model call")

credentials.clear()
capture = credentials.capture(DICTATION)
check("a dictated credential is recognised", capture is not None)
check("the username is decoded", capture.credential.username == "jsmith", capture.credential.username)
check(
    "the NATO spelling is decoded",
    capture.credential.password == PASSWORD,
    capture.credential.password,
)
check(
    "what the model sees carries no part of the password",
    PASSWORD not in capture.redacted and "Tango" not in capture.redacted,
    capture.redacted,
)
check(
    "what the model sees still says what to do",
    "billing portal" in capture.redacted and capture.ref in capture.redacted,
    capture.redacted,
)
check("the ref resolves locally", credentials.resolve(capture.ref).password == PASSWORD)
check("an unknown ref resolves to nothing", credentials.resolve("pending_dictation_999") is None)
credentials.release(capture.ref)
check("release drops the plaintext", credentials.resolve(capture.ref) is None)

check(
    "an ordinary command isn't mistaken for a credential",
    credentials.capture("open github and check the pull requests") is None,
)
check(
    "'password' with nothing after it isn't a capture",
    credentials.capture("I forgot my password") is None,
)
check(
    "talking *about* a password field isn't a dictation",
    credentials.capture("the password field isn't showing up") is None,
)

decoded = credentials.decode_spoken("john dot smith at example dot com", default_upper=False)
check("spoken punctuation decodes", decoded == "john.smith@example.com", decoded)
decoded = credentials.decode_spoken("capital delta echo lowercase foxtrot 4")
check("case markers are honoured", decoded == "DEf4", decoded)
check(
    "a password said as a word stays that word",
    credentials.decode_spoken("swordfish") == "swordfish",
)

credentials.clear()
half = credentials.capture("login, username is jsmith")
check("half a credential is not thrown away", half is not None and not half.complete)
check("...and Jarvis knows which half is missing", half.needs == "password", half.needs)
check("...and says so without a ref to fill with", "credential_ref" not in half.redacted, half.redacted)
whole = credentials.capture("the password is Tango Romeo Alpha 7 Charlie 9")
check("the second utterance completes it", whole is not None and whole.complete)
merged = credentials.resolve(whole.ref)
check(
    "...with both halves, said 20 seconds apart",
    merged.username == "jsmith" and merged.password == PASSWORD,
    f"{merged.username} / {len(merged.password)} chars",
)

credentials.clear()
credentials.expect("both")
bare = credentials.capture("It's L-E-N-N-O-X-S-T-A-R-K-47")
check("a bare answer is understood when Jarvis just asked", bare is not None)
check(
    "...and a spelled-out username comes out lower case, like a login field wants",
    credentials._partial["username"] == "lennoxstark47",
    credentials._partial["username"],
)
check(
    "a command said while waiting is still a command",
    credentials.capture("open github and log in") is None,
)
check(
    "a whole sentence is never read as a password",
    credentials.capture("no that was the username for github not a password") is None,
)
credentials.clear()
check("nothing is armed once cleared", credentials.expecting() == "")

original_ttl = credentials.TTL_SECONDS
credentials.TTL_SECONDS = -1.0
stale = credentials.capture("username is x, password is Bravo Bravo Bravo")
check("an expired credential can't be resolved later", credentials.resolve(stale.ref) is None)
credentials.TTL_SECONDS = original_ttl
credentials.clear()

# --------------------------------------------------------------------------
print("\njarvis.confirm — nothing submits without a human answer")

ran: list[str] = []
confirm.arm("submit the login form", lambda: ran.append("submitted") or "Logged in.")
check("a yes is recognised", confirm.classify("yes") is True)
check("so is a natural yes", confirm.classify("yeah go ahead") is True)
check("a no is recognised", confirm.classify("no, cancel that") is False)
check(
    "a sentence with more in it than an answer is not an answer",
    confirm.classify("yes but open the other portal first") is None,
)
check("an unrelated command is not an answer", confirm.classify("open github") is None)
check("the action hasn't run yet", ran == [])
check("a yes runs it", confirm.resolve(True) == "Logged in." and ran == ["submitted"])
check("and it is consumed exactly once", confirm.resolve(True) is None)

ran.clear()
confirm.arm("submit the login form", lambda: ran.append("submitted") or "Logged in.")
check("a no drops it without running", confirm.resolve(False) == "Cancelled." and ran == [])

ran.clear()
confirm.arm("submit the login form", lambda: ran.append("submitted") or "Logged in.")
confirm._pending = confirm.Pending(
    description="submit the login form",
    action=lambda: ran.append("submitted") or "Logged in.",
    asked_at=time.monotonic() - confirm.TTL_SECONDS - 1,
)
check("a stale question is not answerable", confirm.peek() is None)
check("...and answering it anyway does nothing", confirm.resolve(True) is None and ran == [])

ran.clear()
confirm.arm("first", lambda: ran.append("first") or "first")
confirm.arm("second", lambda: ran.append("second") or "second")
confirm.resolve(True)
check("a yes answers the question you were just asked", ran == ["second"], str(ran))
confirm.clear()

# --------------------------------------------------------------------------
print("\njarvis.vault — the Keychain, not a file Jarvis owns")

fake_keyring = FakeKeyring()
vault._keyring = lambda: fake_keyring

check("a spoken site name normalises", vault.site_key("The Billing Portal") == "billing")
check(
    "every way you might say one site lands on one key",
    vault.site_key("billing portal login") == vault.site_key("the billing portal website"),
    f"{vault.site_key('billing portal login')} vs {vault.site_key('the billing portal website')}",
)
check("a URL normalises to its host", vault.site_key("https://portal.example.com/login") == "portal.example.com")
check("...and never to nothing", vault.site_key("the portal") == "portal")

vault.set("the billing portal", Credential("jsmith", PASSWORD))
saved = vault.get("billing portal")
check("a saved login comes back", saved is not None and saved.password == PASSWORD)
check("...under any phrasing of the same site", vault.get("the Billing Portal website") is not None)
check("an unknown site has no login", vault.get("something else entirely") is None)
check("repr never prints the password", "TRA7C9" not in repr(saved), repr(saved))
check("forget removes it", vault.forget("billing portal") and vault.get("billing portal") is None)
check("forgetting nothing is not an error", vault.forget("never saved") is False)

# --------------------------------------------------------------------------
print("\njarvis.login — fill, then stop")

credentials.clear()
confirm.clear()
fake_keyring.store.clear()
capture = credentials.capture(DICTATION)

with FakeConfig(actions={"login": {"remember": True, "confirm_before_submit": True}}):
    fake = FakeBrowser()
    with use_browser(fake):
        message = login.fill_login_form("the billing portal", capture.ref)
        check("the real credential reaches the browser", fake.filled == ("jsmith", PASSWORD), str(fake.filled))
        check("nothing was submitted", fake.submitted is False)
        check("a confirmation is armed", confirm.peek() is not None)
        check(
            "what goes back to the model carries no password",
            PASSWORD not in message,
            message,
        )
        check(
            "nothing is saved yet — the login hasn't been accepted",
            vault.get("portal.example.com") is None,
            str(sorted(key for _service, key in fake_keyring.store)),
        )

        spoken = confirm.resolve(True)
        check("confirming submits it", fake.submitted is True)
        check("and says where it landed", "Dashboard" in spoken, spoken)
        check(
            "now it's saved, under the page it was typed into",
            vault.get("portal.example.com") is not None,
            str(sorted(key for _service, key in fake_keyring.store)),
        )
        check(
            "...not under whatever the model called the site",
            vault.get("the billing portal") is None,
        )
        check("and the dictated copy was dropped", credentials.resolve(capture.ref) is None)

    # Saying it again with no dictation at all: the Keychain answers.
    fake = FakeBrowser()
    with use_browser(fake):
        login.fill_login_form("billing portal")
        check(
            "a second login needs no re-dictation",
            fake.filled == ("jsmith", PASSWORD),
            str(fake.filled),
        )
        confirm.clear()

    # A model that drops the ref on its way through — small ones do.
    fake_keyring.store.clear()
    credentials.clear()
    credentials.capture(DICTATION)
    fake = FakeBrowser()
    with use_browser(fake):
        login.fill_login_form("billing portal")
    check(
        "a model that forgets the ref still gets the dictated credential",
        fake.filled == ("jsmith", PASSWORD),
        str(fake.filled),
    )
    confirm.clear()
    credentials.clear()

    # 2FA — doc 04, point 6.
    fake = FakeBrowser(
        submit_result={
            "url": "https://portal.example.com/2fa", "title": "Verify",
            "navigated": True, "still_has_password": False, "wants_code": True,
        }
    )
    fake_keyring.store.clear()
    credentials.clear()
    two_factor = credentials.capture(DICTATION)
    with use_browser(fake):
        login.fill_login_form("billing portal", two_factor.ref)
        spoken = confirm.resolve(True)
    check("a one-time code is handed back to the user", "one-time code" in spoken, spoken)
    check("...and Jarvis doesn't claim it logged in", "Logged in" not in spoken, spoken)
    check(
        "...but the password is kept, since the site accepted it",
        vault.get("portal.example.com") is not None,
        str(sorted(key for _service, key in fake_keyring.store)),
    )

    # A password that came through wrong.
    fake = FakeBrowser(
        submit_result={
            "url": "https://portal.example.com/login", "title": "Sign in",
            "navigated": False, "still_has_password": True, "wants_code": False,
        }
    )
    fake_keyring.store.clear()
    credentials.clear()
    rejected = credentials.capture(DICTATION)
    with use_browser(fake):
        login.fill_login_form("billing portal", rejected.ref)
        spoken = confirm.resolve(True)
    check("a failed login is reported as one", "didn't take" in spoken, spoken)
    check(
        "a credential the site rejected is not saved",
        vault.get("portal.example.com") is None,
        str(sorted(key for _service, key in fake_keyring.store)),
    )
    check(
        "...and Jarvis is listening for you to say it again",
        credentials.expecting() == "both",
        credentials.expecting(),
    )
    credentials.clear()

    # Nothing to fill.
    credentials.clear()
    nothing_to_fill = credentials.capture(DICTATION)
    fake = FakeBrowser(fail="there's no password field on about:blank — is this the login page?")
    with use_browser(fake):
        message = login.fill_login_form("billing portal", nothing_to_fill.ref)
    check("a page with no form is something to say, not a crash", "couldn't fill" in message, message)
    check("...and nothing is left armed", confirm.peek() is None)

    fake_keyring.store.clear()
    credentials.clear()
    with use_browser(FakeBrowser()):
        message = login.fill_login_form("a site never logged into")
    check("an unknown site asks the user to dictate", "say their username and password" in message, message)
    check(
        "...and arms the parser, so a bare answer is understood",
        credentials.expecting() == "both",
        credentials.expecting(),
    )

    # Half a credential must not be typed in — ask for the other half.
    credentials.clear()
    credentials.expect("password")
    credentials.capture("SuperSecretPassword bang")
    fake = FakeBrowser()
    with use_browser(fake):
        message = login.fill_login_form("billing portal")
    check("a password with no username isn't typed in", fake.filled is None)
    check("...it asks for the username instead", "username" in message, message)
    check("...and waits for it", credentials.expecting() == "username", credentials.expecting())

    # The model putting the ref in the site field (seen live).
    credentials.clear()
    capture_two = credentials.capture(DICTATION)
    fake_keyring.store.clear()
    fake = FakeBrowser()
    with use_browser(fake):
        login.fill_login_form(capture_two.ref)
        check(
            "a ref passed as the site is recognised as a ref",
            fake.filled == ("jsmith", PASSWORD),
            str(fake.filled),
        )
        confirm.resolve(True)
    check(
        "...and it's filed under the page it was typed into, not the ref",
        vault.get("portal.example.com") is not None,
        str(sorted(key for _service, key in fake_keyring.store)),
    )
    confirm.clear()
    credentials.clear()

with FakeConfig(actions={"login": {"remember": False, "confirm_before_submit": True}}):
    fake_keyring.store.clear()
    credentials.clear()
    capture = credentials.capture(DICTATION)
    with use_browser(FakeBrowser()):
        login.fill_login_form("billing portal", capture.ref)
    check(
        "remember=false keeps the credential out of the Keychain",
        vault.get("billing portal") is None,
    )
    confirm.clear()

# --------------------------------------------------------------------------
print("\njarvis.tools — the model can fill a form, but cannot submit one")

check("fill_login_form is declared", "fill_login_form" in tools.TOOL_NAMES)
check(
    "no tool submits a login",
    not any("submit" in name for name in tools.TOOL_NAMES),
    str(tools.TOOL_NAMES),
)
spec = next(item for item in tools.TOOL_SPECS if item["name"] == "fill_login_form")
check(
    "the schema offers no way to pass a password",
    set(spec["parameters"]["properties"]) == {"site", "credential_ref"},
    str(list(spec["parameters"]["properties"])),
)

credentials.clear()
capture = credentials.capture(DICTATION)
with use_browser(FakeBrowser()) as fake:
    # A model that ignores the schema and sends the password anyway: the
    # argument must be dropped before the handler ever sees it.
    output = tools.execute(
        "fill_login_form",
        {"site": "billing portal", "credential_ref": capture.ref, "password": "whatever"},
        dry_run=True,
    )
check("an invented password argument is dropped", "dry run" in output, output)
check("...and a dry run types nothing", fake.filled is None)
confirm.clear()
credentials.clear()

# --------------------------------------------------------------------------
print("\njarvis.tools — the URL the user said beats the one the model retyped")

check(
    "a mangled domain is corrected back",
    tools.prefer_spoken_url(
        "https://the-intent-internet.herokuapp.com/login",
        "Open the portal at the-internet.herokuapp.com/login.",
    )
    == "https://the-internet.herokuapp.com/login",
)
check(
    "a domain said out loud in words is understood",
    tools.prefer_spoken_url("https://gh.com", "open github dot com slash login")
    == "https://github.com/login",
)
check(
    "the model keeps its URL when the user only named a site",
    tools.prefer_spoken_url("https://github.com", "Open GitHub and log in.")
    == "https://github.com",
)
check(
    "...and when it agrees with what was said, path and all",
    tools.prefer_spoken_url(
        "https://github.com/login", "open github.com and go to the login page"
    )
    == "https://github.com/login",
)
check(
    "two spoken URLs are ambiguous, so it defers to the model",
    tools.prefer_spoken_url(
        "https://a.com", "open example.com and then other.com"
    )
    == "https://a.com",
)
check(
    "an ordinary sentence produces no phantom domain",
    tools.spoken_urls("no, it's the username for GitHub not a password") == [],
    str(tools.spoken_urls("no, it's the username for GitHub not a password")),
)
check(
    "a domain Whisper split apart is not trusted over the model's",
    # "herokuapp" heard as "heroku app" made "the-internet.heroku" look like a
    # domain, and the correction turned a wrong URL into a worse one.
    tools.prefer_spoken_url(
        "https://the-internet.heroku.com/app/login",
        "Open portal at the-internet.heroku app slash login.",
    )
    == "https://the-internet.heroku.com/app/login",
)
check(
    "the word 'dash' doesn't survive into the domain",
    # Whisper writes "the dash internet" as "the dash-internet": the word stays
    # *and* becomes punctuation.
    tools.prefer_spoken_url(
        "https://dash-internet.herokuapp.com-logging",
        "Open the portal at the dash-internet.herokuapp.com-logging.",
    )
    == "https://the-internet.herokuapp.com",
)
check(
    "an abbreviation isn't a domain",
    tools.spoken_urls("open it, e.g. the portal") == [],
    str(tools.spoken_urls("open it, e.g. the portal")),
)

# With one real browser there is only one way to open a page in it — the flaw
# that deadlocked the first GitHub attempt (open_url took the profile lock that
# fill_login_form then needed).
opened: list[str] = []


class FakeOpenPortal:
    def __init__(self, fail=""):
        self.fail = fail

    def __enter__(self):
        from jarvis import browser as browser_module

        self._original = browser_module.open_portal
        self._engine = browser_module.engine

        def fake(url, *, dry_run=False, on_status=None):
            if self.fail:
                raise browser_module.BrowserError(self.fail)
            opened.append(url)
            return f"Opened {url} in Jarvis's browser."

        browser_module.open_portal = fake
        browser_module.engine = lambda: "system-firefox"
        return self

    def __exit__(self, *_exc):
        from jarvis import browser as browser_module

        browser_module.open_portal = self._original
        browser_module.engine = self._engine


with FakeOpenPortal():
    output = tools.execute("open_url", {"url": "github.com"})
check(
    "open_url goes through the browser Jarvis drives, not a second one",
    opened == ["https://github.com"],
    str(opened),
)
check("...and says it opened it", "Opened" in output, output)

with FakeOpenPortal(fail="your Firefox is already open"):
    output = tools.execute("open_url", {"url": "https://example.com"})
check(
    "...but falls back to macOS `open` when that browser can't be had",
    "Opened https://example.com" in output,
    output,
)

from jarvis.browser import _short_error  # noqa: E402

check(
    "a driver stack trace becomes something speakable",
    _short_error(
        "Message: Reached error page: about:neterror?e=dnsNotFound&u=https%3A//x.com/"
        "\nStacktrace:\nRemoteError@chrome://remote/content/shared/RemoteError.sys.mjs:8:8",
        "https://portal.example.com",
    )
    == "there's no site at portal.example.com — that address doesn't exist",
)
check(
    "...and an unrecognised one is at least trimmed to one line",
    _short_error("Message: Unable to find element\nStacktrace:\nfoo@bar")
    == "Message: Unable to find element",
)

# --------------------------------------------------------------------------
print("\njarvis.agent — what actually crosses the network")

credentials.clear()
confirm.clear()
fake_keyring.store.clear()

with FakeConfig(actions={"login": {"remember": True, "confirm_before_submit": True}}):
    fake = FakeBrowser()
    with use_browser(fake):
        backend = ScriptedBackend(
            [
                Completion(
                    tool_calls=(
                        ToolCall(
                            id="1",
                            name="fill_login_form",
                            arguments={"site": "the billing portal"},
                        ),
                    )
                ),
                Completion(text="Filled in, want me to submit?"),
            ]
        )
        agent = Agent(backend=backend)
        result = agent.handle(DICTATION)

        sent = " ".join(backend.seen)
        check("the model never saw the password", PASSWORD not in sent, sent)
        check("the model never saw the spoken form either", "Tango" not in sent, sent)
        check("the model was told a credential exists", "credential_ref" in sent, sent)
        check("the credential still reached the browser", fake.filled[1] == PASSWORD)
        check("Jarvis asked before submitting", "submit" in result.reply.lower(), result.reply)
        check("nothing was submitted yet", fake.submitted is False)

        # ...and now the answer.
        calls_before = backend.calls
        answer = agent.handle("yes")
        check("a yes submits it", fake.submitted is True)
        check(
            "and needed no model call to decide that",
            backend.calls == calls_before,
            f"{backend.calls} vs {calls_before}",
        )
        check("the turn reports what it did", "Logged in" in answer.reply, answer.reply)

    # A "no" must work even when the brain is unreachable — it's the stop button.
    class DeadBackend:
        name, model = "dead", "dead-1"

        def complete(self, *_args, **_kwargs):
            raise AssertionError("the model must not be called to answer a confirmation")

    fake = FakeBrowser()
    with use_browser(fake):
        confirm.arm("submit the login form", lambda: "submitted")
        result = Agent(backend=DeadBackend()).handle("no, cancel that")
    check("a cancel works with no working brain", result.reply == "Cancelled.", result.reply)
    check("...and submitted nothing", fake.submitted is False)

    # An utterance that isn't an answer leaves the question alone.
    confirm.arm("submit the login form", lambda: "submitted")
    backend = ScriptedBackend([Completion(text="ok")])
    Agent(backend=backend).handle("what time is it")
    check("a non-answer goes to the model as usual", backend.calls == 1)
    check("...and leaves the question armed", confirm.peek() is not None)
    confirm.clear()

credentials.clear()

# --------------------------------------------------------------------------
print("\njarvis.speech — Jarvis talks back")

spoken_text: list[str] = []


class FakeVoice:
    name = "fake"

    def speak(self, text, process_sink):
        spoken_text.append(text)


speech._cached, speech._cached_name = FakeVoice(), "fake"
with FakeConfig(speech={"enabled": True, "backend": "fake"}):
    check("a reply is spoken", speech.speak("Filled in, want me to submit?") is True)
    check("...with the text it was given", spoken_text == ["Filled in, want me to submit?"])
    check("an empty reply says nothing", speech.speak("") is False)
    long_reply = "word " * 400
    speech.speak(long_reply)
    check(
        "an absurdly long reply is truncated rather than monologued",
        len(spoken_text[-1]) <= speech.MAX_SPOKEN_CHARS + 3,
        str(len(spoken_text[-1])),
    )

with FakeConfig(speech={"enabled": False, "backend": "fake"}):
    count = len(spoken_text)
    check("speech.enabled=false is silent", speech.speak("hello") is False and len(spoken_text) == count)


class BrokenVoice:
    name = "broken"

    def speak(self, text, process_sink):
        raise speech.SpeechError("no voice model installed")


speech._cached, speech._cached_name = BrokenVoice(), "broken"
with FakeConfig(speech={"enabled": True, "backend": "broken"}):
    check("a broken voice is reported, not raised", speech.speak("hello") is False)

speech._cached, speech._cached_name = None, ""
with FakeConfig(speech={"enabled": True, "backend": "nonsense"}):
    check("an unknown voice is a clear failure", speech.speak("hello") is False)

check("the default voice needs no setup", "say" in speech.VOICE_NAMES)

# --------------------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)
print("All Phase 4 login + voice-out checks passed.")
