"""
Browser automation — Phases 3-4.

`open_url` hands a URL to macOS and forgets about it, which is right for "open
github.com" and useless for "open the billing portal and log me in": nothing
downstream can see the page. This module is the other kind of opening — a
browser Jarvis *drives*.

Phase 3 stopped at landing on the page and saying what's there (title, and
whether it looks like a login form). Phase 4 adds the typing: `fill_login` puts
a username and password into the fields it finds, and `submit_login` presses the
button — as two separate calls, never one, because doc 04's point 4 puts a
spoken confirmation between them. This module is only the mechanism; which
credential goes in, whether it's remembered, and who is allowed to trigger the
submit are jarvis.login's business.

## Two engines, one contract

`actions.browser.engine` picks which browser is on the other end, and the rest
of Jarvis can't tell the difference — every session exposes the same four
actions (`open_portal`, `page_summary`, `fill_login`, `submit_login`) returning
the same dictionaries.

- **`system-firefox`** (default) — *your* Firefox, the one in /Applications, with
  *your* profile: your logins, your extensions, your bookmarks. Driven through
  geckodriver, which is Mozilla's own driver and the only supported way to
  automate a stock Firefox build.
- **`chromium` / `firefox` / `webkit`** — Playwright's own downloaded browsers,
  with a profile under `memory/`. Self-contained and needs nothing installed,
  but it is emphatically not the browser in your Dock.

Why both, rather than just Playwright: Playwright's Firefox is a *patched* build
speaking a protocol (Juggler) that stock Firefox doesn't implement. Pointing it
at /Applications/Firefox.app fails to launch — verified 2026-09-08, not assumed.
So "use my actual browser" and "use Playwright" are mutually exclusive, and the
choice belongs in config rather than in code.

The one thing neither engine can do is **attach to the Firefox window already
open on screen**. A driver has to start the browser itself; nothing short of a
browser extension can take over a running one. With `system-firefox` that means
Jarvis launches your Firefox, and your profile can only be open once — so if
Firefox is already running on that profile, Jarvis says so and asks you to quit
it rather than failing with a driver stack trace.

## Two design points that aren't obvious

**It runs on its own thread, for the life of the process.** Playwright's sync
API is bound to the thread that started it, and Jarvis handles every utterance
on a *fresh* thread (see jarvis.voice — each release of the hotkey spawns one).
So the browser lives on one dedicated worker thread and callers post work to it
through a queue. That also fixes the more obvious problem: a browser opened
inside a tool call would close the instant the call returned, which is not what
"open this portal" means. Selenium is less fussy about threads than Playwright,
but it runs on the same worker for the same second reason.

**The profile is persistent.** Cookies and sessions survive a restart, so Jarvis
doesn't re-login to everything every morning. For `system-firefox` that's your
real profile and the whole point; for Playwright it's a directory under
`memory/`, which holds real session cookies and is gitignored like the rest of
it.
"""
from __future__ import annotations

import atexit
import logging
import queue
import re
import threading
from pathlib import Path
from typing import Any, Callable

from jarvis import config

logger = logging.getLogger("jarvis.browser")

StatusFn = Callable[[str], None]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# How long a queued browser command may take before the calling thread gives up
# on the worker. Longer than the per-navigation timeout so a slow page reports
# its own error rather than surfacing as a mysterious worker timeout.
COMMAND_TIMEOUT = 120.0

# Engines that mean "the browser installed on this Mac", as opposed to one
# Playwright downloaded.
SYSTEM_ENGINES = ("system-firefox",)

# Where a real Firefox might be, most-preferred first. Developer Edition counts:
# it is the browser someone who installed it actually uses.
_FIREFOX_APPS = (
    "/Applications/Firefox.app",
    "/Applications/Firefox Developer Edition.app",
    "/Applications/Firefox Nightly.app",
    "~/Applications/Firefox.app",
    "~/Applications/Firefox Developer Edition.app",
)

FIREFOX_SUPPORT_DIR = Path("~/Library/Application Support/Firefox").expanduser()

# Selectors that mean "this page wants a password". Deliberately narrow: this
# is both the hint Jarvis says out loud and the anchor `fill_login` types into —
# a false positive here puts a password in the wrong field.
_PASSWORD_SELECTOR = "input[type=password]"
_USERNAME_SELECTORS = (
    # The standards-based signal first: a site that fills this in has told every
    # password manager on earth which field it is, and it's right far more often
    # than guessing from a name attribute.
    "input[autocomplete=username]",
    "input[type=email]",
    "input[name*=user i]",
    "input[name*=email i]",
    "input[id*=user i]",
    "input[id*=email i]",
    # GitHub's field is name="login" id="login_field" — no "user" or "email"
    # anywhere in it, which is common enough to be worth its own pair.
    "input[name*=login i]",
    "input[id*=login i]",
    # Last resort, and last on purpose: on a page with several text inputs this
    # can pick the wrong one, so it only runs when nothing more specific matched.
    "input[type=text]",
)

# What to press once the fields are filled, as plain CSS both engines understand.
_SUBMIT_SELECTORS = ("button[type=submit]", "input[type=submit]")

# ...and the same button found by its words, for forms that don't mark it up.
# Each engine expresses this in its own dialect; if none matches, `submit_login`
# presses Enter in the password field, which is what a person would do.
_SUBMIT_TEXTS = ("log in", "sign in", "login", "continue")

# A one-time code being asked for. doc 04's point 6 makes 2FA a deliberate
# human step — Jarvis says a code is needed and stops, rather than trying to
# find, intercept or bypass it.
_OTP_SELECTORS = (
    "input[autocomplete='one-time-code']",
    "input[name*=otp i]",
    "input[id*=otp i]",
    "input[name*=totp i]",
    "input[name*='verification' i]",
    "input[id*='verification' i]",
    "input[name*='2fa' i]",
    "input[id*='2fa' i]",
)


class BrowserError(RuntimeError):
    """The browser couldn't be started or driven."""


# Selenium reports a failed navigation as a WebDriverException whose message is
# an internal `about:neterror` URL plus a chrome:// stack trace — nearly 900
# characters of it. That string went to the model, came back paraphrased, and
# then got read *out loud*: 17 seconds of a synthesised voice explaining a DNS
# lookup. Everything Jarvis says has to survive being spoken, so a failure has
# to arrive as a sentence, not as a diagnostic.
_NETERROR_RE = re.compile(r"about:neterror\?e=([A-Za-z]+)")

_NETERROR_SENTENCES = {
    "dnsNotFound": "there's no site at {host} — that address doesn't exist",
    "connectionFailure": "nothing answered at {host}",
    "netTimeout": "{host} took too long to answer",
    "proxyConnectFailure": "the proxy wouldn't connect to {host}",
    "nssFailure2": "{host} has a broken security certificate",
    "sslv3Disabled": "{host} has a broken security certificate",
}


def _short_error(text: str, url: str = "") -> str:
    """One speakable sentence out of whatever the driver threw."""
    host = url.split("://", 1)[-1].split("/", 1)[0] or "that address"
    match = _NETERROR_RE.search(text)
    if match:
        template = _NETERROR_SENTENCES.get(match.group(1), "{host} wouldn't load")
        return template.format(host=host)
    first = text.split("Stacktrace:")[0].strip().splitlines()
    return (first[0] if first else "the browser couldn't do that")[:200]


def _settings() -> dict[str, Any]:
    return config.actions_config("browser")


def engine() -> str:
    return str(_settings().get("engine", "system-firefox"))


def user_data_dir() -> Path:
    """Where a Playwright engine's persistent profile lives.

    Scoped by engine name: a Chromium profile directory is not a Firefox one,
    and pointing one at the other would either fail or corrupt it. So switching
    engines gets a clean profile of its own rather than a confusing failure, at
    the cost of logging in again in the new one. Not used by `system-firefox`,
    which has your real profile instead.
    """
    configured = _settings().get("user_data_dir", "memory/browser")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path / engine()


def firefox_binary() -> str:
    """The Firefox executable to drive. Config first, then the usual places."""
    configured = _settings().get("binary")
    if configured:
        return str(Path(configured).expanduser())
    for app in _FIREFOX_APPS:
        path = Path(app).expanduser()
        if path.exists():
            return str(path / "Contents" / "MacOS" / "firefox")
    raise BrowserError(
        "no Firefox found in /Applications — install it, or set "
        "actions.browser.binary to the executable inside the .app"
    )


def firefox_profile() -> Path | None:
    """Which Firefox profile to open. None means "let Firefox choose".

    `actions.browser.profile` takes a profile *name* as it appears in Firefox's
    profiles.ini ("default", "dev-edition-default"), a full path, or null for
    whichever profile Firefox itself would open — which is the one you actually
    use, and is why null is the default.
    """
    configured = _settings().get("profile")
    if not configured:
        return _default_firefox_profile()
    path = Path(str(configured)).expanduser()
    if path.is_dir():
        return path
    for candidate in (FIREFOX_SUPPORT_DIR / "Profiles").glob("*"):
        if candidate.is_dir() and candidate.name.split(".", 1)[-1] == str(configured):
            return candidate
    raise BrowserError(
        f"no Firefox profile called {configured!r} — check "
        f"{FIREFOX_SUPPORT_DIR / 'profiles.ini'}"
    )


def _default_firefox_profile() -> Path | None:
    """Read profiles.ini for the profile Firefox opens on its own.

    An `[InstallXXXX]` section wins when there is one: that's the per-install
    default, and it's what Developer Edition uses — reading only `Default=1`
    would hand back the *other* profile, the one without your logins in it.
    """
    ini = FIREFOX_SUPPORT_DIR / "profiles.ini"
    if not ini.exists():
        return None
    import configparser

    parser = configparser.ConfigParser()
    try:
        parser.read(ini)
    except (OSError, configparser.Error) as exc:
        logger.warning("couldn't read %s (%s)", ini, exc)
        return None

    relative = ""
    for section in parser.sections():
        if section.startswith("Install") and parser.get(section, "Default", fallback=""):
            relative = parser.get(section, "Default")
            break
    if not relative:
        for section in parser.sections():
            if parser.get(section, "Default", fallback="") == "1":
                relative = parser.get(section, "Path", fallback="")
                break
    if not relative:
        return None
    path = Path(relative)
    return path if path.is_absolute() else FIREFOX_SUPPORT_DIR / path


def profile_in_use(profile: Path) -> bool:
    """Is this exact profile open in a running Firefox?

    Firefox holds an advisory `fcntl` lock on `.parentlock` for as long as it has
    the profile open, so trying to take that lock answers the question exactly —
    where "is any Firefox running?" would not. That distinction matters: a second
    Firefox on a *different* profile is fine, and refusing to start one would
    mean Jarvis couldn't drive a browser whenever you happened to be using yours.

    The lock is taken non-blocking and released immediately; a running Firefox
    never notices.
    """
    lock = Path(profile) / ".parentlock"
    if not lock.exists():
        return False
    import fcntl

    try:
        with open(lock, "a") as handle:
            fcntl.lockf(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.lockf(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return True
    except Exception as exc:  # noqa: BLE001 - an unreadable lock file isn't proof of use
        logger.debug("couldn't test the profile lock (%s)", exc)
    return False


class BrowserSession:
    """A long-lived browser, driven from one dedicated thread.

    Every public method is called from *some other* thread and posts a callable
    onto `_work`; `_serve` is the only thing that ever touches the driver.
    Subclasses supply the four actions and the launch/teardown; everything about
    threading, queueing and recovery lives here so both engines behave the same.
    """

    name = "browser"

    def __init__(self) -> None:
        self._work: queue.Queue[tuple[Callable[[], Any], queue.Queue]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._start_error: str = ""
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------

    def ensure_started(self) -> None:
        """Start the worker thread and the browser, once. Raises BrowserError."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._started.clear()
                self._start_error = ""
                self._thread = threading.Thread(
                    target=self._serve, name="jarvis-browser", daemon=True
                )
                self._thread.start()
        # Launching with a cold profile is slow; give it room.
        if not self._started.wait(timeout=COMMAND_TIMEOUT):
            raise BrowserError("the browser took too long to start")
        if self._start_error:
            raise BrowserError(self._start_error)

    def _serve(self) -> None:
        """The worker thread: own the browser, then run queued commands forever."""
        try:
            self._launch()
        except Exception as exc:  # noqa: BLE001 - reported to the caller, not raised here
            logger.error("could not start the browser: %s", exc)
            self._start_error = str(exc)
            self._started.set()
            return

        self._started.set()
        while True:
            job, reply = self._work.get()
            if job is None:
                break
            try:
                self._ensure_page()
                reply.put(("ok", job()))
            except Exception as exc:  # noqa: BLE001 - crossed back to the caller intact
                logger.warning("browser command failed: %s", exc)
                reply.put(("error", exc))
        self._shutdown()

    def close(self) -> None:
        """Stop the worker and the browser. Safe to call when never started."""
        if self._thread is not None and self._thread.is_alive():
            self._work.put((None, queue.Queue()))
            self._thread.join(timeout=10.0)

    # -- command plumbing ----------------------------------------------------

    def _submit(self, job: Callable[[], Any]) -> Any:
        self.ensure_started()
        reply: queue.Queue = queue.Queue()
        self._work.put((job, reply))
        try:
            status, payload = reply.get(timeout=COMMAND_TIMEOUT)
        except queue.Empty as exc:
            raise BrowserError("the browser stopped responding") from exc
        if status == "error":
            # Trimmed here, at the one place every failure passes through, so no
            # individual action has to remember to do it.
            raise BrowserError(_short_error(str(payload))) from payload
        return payload

    # -- actions (the whole contract, identical for every engine) -------------

    def open_portal(self, url: str) -> dict[str, Any]:
        """Navigate to `url` and report what landed."""
        return self._submit(lambda: self._open_portal(url))

    def page_summary(self) -> dict[str, Any]:
        """What's on screen right now — used to check a portal is actually open."""
        return self._submit(self._page_summary)

    def fill_login(self, username: str, password: str) -> dict[str, Any]:
        """Type the credential into the fields on the open page. Never submits."""
        return self._submit(lambda: self._fill_login(username, password))

    def submit_login(self, *, wait_seconds: float = 8.0) -> dict[str, Any]:
        """Press the login button and report where that landed.

        Called only from a confirmed action (jarvis.confirm) — nothing in the
        model's tool list reaches it.
        """
        return self._submit(lambda: self._submit_login(wait_seconds))

    # -- what each engine must provide ---------------------------------------

    def _launch(self) -> None: ...
    def _shutdown(self) -> None: ...
    def _ensure_page(self) -> None: ...
    def _open_portal(self, url: str) -> dict[str, Any]: ...
    def _page_summary(self) -> dict[str, Any]: ...
    def _fill_login(self, username: str, password: str) -> dict[str, Any]: ...
    def _submit_login(self, wait_seconds: float) -> dict[str, Any]: ...


def _no_password_field(url: str) -> BrowserError:
    """The error a model reads when there's nothing to type into.

    It says what to do next rather than only what went wrong, because the
    failure it describes is near-universal on the first attempt: the model
    opened the page with open_url (a browser Jarvis cannot see), so Jarvis's own
    browser is still sitting on a blank page.
    """
    where = "a blank page" if url in ("about:blank", "") else url
    return BrowserError(
        f"Jarvis's own browser is on {where}, which has no password field. "
        "A page opened with open_url does not count — Jarvis cannot see or "
        "type into that one. Call open_portal with the login page's full URL "
        "first, then call fill_login_form again."
    )


class PlaywrightSession(BrowserSession):
    """Playwright's own downloaded browser. Self-contained, but not yours."""

    name = "playwright"

    def __init__(self) -> None:
        super().__init__()
        self._playwright = None
        self._context = None
        self._page = None

    def _launch(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise BrowserError(
                "Playwright isn't installed — run `.venv/bin/python3 -m pip install "
                "playwright && .venv/bin/python3 -m playwright install chromium`."
            ) from exc

        settings = _settings()
        profile = user_data_dir()
        profile.mkdir(parents=True, exist_ok=True)

        self._playwright = sync_playwright().start()
        browser_type = getattr(self._playwright, settings.get("engine", "chromium"))
        self._context = browser_type.launch_persistent_context(
            str(profile),
            headless=bool(settings.get("headless", False)),
            args=["--no-first-run", "--no-default-browser-check"],
        )
        self._context.set_default_timeout(float(settings.get("timeout", 30)) * 1000)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        logger.info("browser started (profile: %s)", profile)

    def _shutdown(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception as exc:  # noqa: BLE001 - shutting down anyway
            logger.debug("closing the browser context: %s", exc)
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception as exc:  # noqa: BLE001 - shutting down anyway
            logger.debug("stopping playwright: %s", exc)
        self._context = self._page = self._playwright = None

    def _ensure_page(self) -> None:
        """Re-open a page or a whole browser the user closed.

        Seen live 2026-09-08: Jarvis's browser opened on about:blank, the window
        was closed by hand (reasonably — it was blank), and every command after
        that failed with "Target page, context or browser has been closed" for
        the life of the process. The worker thread was still alive, so
        `ensure_started` saw nothing wrong: a live thread is not the same thing
        as a live browser.
        """
        try:
            if self._page is not None and not self._page.is_closed():
                return
        except Exception as exc:  # noqa: BLE001 - a dead handle raises rather than answering
            logger.debug("the browser page handle is dead (%s)", exc)

        try:
            if self._context is not None:
                self._page = self._context.new_page()
                logger.info("the browser page was closed — opened a new one")
                return
        except Exception as exc:  # noqa: BLE001 - context gone too, fall through to relaunch
            logger.info("the browser context is gone (%s) — relaunching", exc)

        self._shutdown()
        self._launch()

    # -- element finding -----------------------------------------------------

    def _first_visible(self, selectors: tuple[str, ...]) -> Any:
        """The first element matching any of `selectors` that a person could click.

        Visibility is the whole point: login pages routinely carry hidden
        honeypot inputs and inert duplicates of the real form, and filling one of
        those types the password somewhere it will never be submitted from — or
        worse, into a field designed to catch bots.
        """
        for selector in selectors:
            try:
                locator = self._page.locator(selector)
                count = locator.count()
            except Exception as exc:  # noqa: BLE001 - a bad selector must not stop the rest
                logger.debug("selector %s failed: %s", selector, exc)
                continue
            for index in range(count):
                element = locator.nth(index)
                try:
                    if element.is_visible():
                        return element
                except Exception:  # noqa: BLE001 - element detached mid-check
                    continue
        return None

    def _submit_button(self) -> Any:
        texts = tuple(f"button:has-text('{text}')" for text in _SUBMIT_TEXTS)
        return self._first_visible(_SUBMIT_SELECTORS + texts)

    # -- actions -------------------------------------------------------------

    def _open_portal(self, url: str) -> dict[str, Any]:
        page = self._page
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:  # noqa: BLE001 - a chatty page never goes idle; not an error
            logger.debug("%s never reached networkidle — continuing", url)
        self._context.pages[0].bring_to_front()
        # Same visibility rule `fill_login` uses, so "it has a login form" and
        # "I could actually fill it" can never disagree.
        return {
            "url": page.url,
            "title": (page.title() or "").strip(),
            "login_form": self._first_visible((_PASSWORD_SELECTOR,)) is not None,
            "username_field": self._first_visible(_USERNAME_SELECTORS) is not None,
        }

    def _page_summary(self) -> dict[str, Any]:
        return {
            "url": self._page.url,
            "title": (self._page.title() or "").strip(),
            "login_form": self._first_visible((_PASSWORD_SELECTOR,)) is not None,
        }

    def _fill_login(self, username: str, password: str) -> dict[str, Any]:
        page = self._page
        password_field = self._first_visible((_PASSWORD_SELECTOR,))
        if password_field is None:
            raise _no_password_field(page.url)

        filled_username = False
        if username:
            username_field = self._first_visible(_USERNAME_SELECTORS)
            if username_field is not None:
                username_field.fill(username)
                filled_username = True
                # Some login forms only render the password field once the
                # username has been entered; re-find it rather than typing into
                # a handle that may now be detached.
                password_field = self._first_visible((_PASSWORD_SELECTOR,)) or password_field

        password_field.fill(password)
        self._context.pages[0].bring_to_front()
        return {
            "url": page.url,
            "title": (page.title() or "").strip(),
            "filled_username": filled_username,
            "username_field": filled_username or username == "",
        }

    def _submit_login(self, wait_seconds: float) -> dict[str, Any]:
        page = self._page
        before = page.url
        button = self._submit_button()
        if button is not None:
            button.click()
        else:
            password_field = self._first_visible((_PASSWORD_SELECTOR,))
            if password_field is None:
                raise BrowserError("the login form is gone — nothing to submit")
            password_field.press("Enter")

        try:
            page.wait_for_load_state("networkidle", timeout=wait_seconds * 1000)
        except Exception:  # noqa: BLE001 - a chatty page never goes idle; not an error
            logger.debug("%s never reached networkidle after submit", page.url)

        return {
            "url": page.url,
            "title": (page.title() or "").strip(),
            "navigated": page.url != before,
            "still_has_password": self._first_visible((_PASSWORD_SELECTOR,)) is not None,
            "wants_code": self._first_visible(_OTP_SELECTORS) is not None,
        }


class SystemFirefoxSession(BrowserSession):
    """The Firefox installed on this Mac, with your own profile, via geckodriver.

    geckodriver is Mozilla's driver and the supported way to automate a stock
    Firefox — Playwright cannot, because its Firefox is a patched build. Selenium
    fetches the driver itself on first use (Selenium Manager), so there is
    nothing to `brew install`.
    """

    name = "system-firefox"

    def __init__(self) -> None:
        super().__init__()
        self._driver = None

    def _launch(self) -> None:
        try:
            from selenium import webdriver
            from selenium.webdriver.firefox.options import Options
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise BrowserError(
                "Selenium isn't installed — run `.venv/bin/python3 -m pip install selenium`"
            ) from exc

        settings = _settings()
        binary = firefox_binary()
        profile = firefox_profile()

        # A Firefox profile can only be open in one process. Checked up front so
        # this is a sentence Jarvis can say out loud, rather than a driver
        # timeout the user has to go and read a log to understand.
        if profile is not None and profile_in_use(profile):
            # Two sentences, both short: this one gets read out loud, and the
            # first version of it took nineteen seconds to speak. The rest of
            # what a person might need to know goes in the log, where it can be
            # as long as it likes.
            logger.info(
                "profile %s is in use. Quit Firefox, or set actions.browser.profile to "
                "a different profile name to run a second Firefox alongside yours.",
                profile.name,
            )
            raise BrowserError(
                "your Firefox is already open — quit it and ask me again, and I'll "
                "use your own profile"
            )

        options = Options()
        options.binary_location = binary
        if profile is not None:
            options.add_argument("-profile")
            options.add_argument(str(profile))
        if settings.get("headless"):
            options.add_argument("-headless")

        try:
            self._driver = webdriver.Firefox(options=options)
        except Exception as exc:  # noqa: BLE001 - selenium raises a zoo of driver errors
            raise BrowserError(f"couldn't start your Firefox: {exc}") from exc
        self._driver.set_page_load_timeout(float(settings.get("timeout", 30)))
        logger.info("your Firefox started (%s, profile: %s)", binary, profile or "default")

    def _shutdown(self) -> None:
        try:
            if self._driver is not None:
                self._driver.quit()
        except Exception as exc:  # noqa: BLE001 - shutting down anyway
            logger.debug("quitting the driver: %s", exc)
        self._driver = None

    def _ensure_page(self) -> None:
        """Relaunch if the window was closed. Same reasoning as Playwright's."""
        try:
            if self._driver is not None and self._driver.window_handles:
                return
        except Exception as exc:  # noqa: BLE001 - a dead session raises rather than answering
            logger.info("your Firefox is gone (%s) — relaunching", exc)
        self._shutdown()
        self._launch()

    # -- element finding -----------------------------------------------------

    def _first_visible(self, selectors: tuple[str, ...]) -> Any:
        from selenium.webdriver.common.by import By

        for selector in selectors:
            by = By.XPATH if selector.startswith(("//", "(")) else By.CSS_SELECTOR
            try:
                elements = self._driver.find_elements(by, selector)
            except Exception as exc:  # noqa: BLE001 - a bad selector must not stop the rest
                logger.debug("selector %s failed: %s", selector, exc)
                continue
            for element in elements:
                try:
                    if element.is_displayed():
                        return element
                except Exception:  # noqa: BLE001 - element detached mid-check
                    continue
        return None

    def _submit_button(self) -> Any:
        # Playwright's :has-text() has no CSS equivalent, so the by-its-words
        # fallback is XPath here. translate() is XPath 1.0's only way to
        # lowercase, which is why it looks like that.
        lower = (
            "translate(normalize-space(.),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
        )
        texts = tuple(
            f"//button[contains({lower}, '{text}')]" for text in _SUBMIT_TEXTS
        )
        return self._first_visible(_SUBMIT_SELECTORS + texts)

    def _fill(self, element: Any, value: str) -> None:
        element.clear()
        element.send_keys(value)

    # -- actions -------------------------------------------------------------

    def _open_portal(self, url: str) -> dict[str, Any]:
        try:
            self._driver.get(url)
        except Exception as exc:  # noqa: BLE001 - selenium raises a zoo of driver errors
            # Handled here rather than left to _short_error alone because this
            # is the one place the URL is known, and "there's no site at
            # example.com" is a far better sentence than "a page wouldn't load".
            raise BrowserError(_short_error(str(exc), url)) from exc
        return {
            "url": self._driver.current_url,
            "title": (self._driver.title or "").strip(),
            "login_form": self._first_visible((_PASSWORD_SELECTOR,)) is not None,
            "username_field": self._first_visible(_USERNAME_SELECTORS) is not None,
        }

    def _page_summary(self) -> dict[str, Any]:
        return {
            "url": self._driver.current_url,
            "title": (self._driver.title or "").strip(),
            "login_form": self._first_visible((_PASSWORD_SELECTOR,)) is not None,
        }

    def _fill_login(self, username: str, password: str) -> dict[str, Any]:
        password_field = self._first_visible((_PASSWORD_SELECTOR,))
        if password_field is None:
            raise _no_password_field(self._driver.current_url)

        filled_username = False
        if username:
            username_field = self._first_visible(_USERNAME_SELECTORS)
            if username_field is not None:
                self._fill(username_field, username)
                filled_username = True
                password_field = self._first_visible((_PASSWORD_SELECTOR,)) or password_field

        self._fill(password_field, password)
        return {
            "url": self._driver.current_url,
            "title": (self._driver.title or "").strip(),
            "filled_username": filled_username,
            "username_field": filled_username or username == "",
        }

    def _submit_login(self, wait_seconds: float) -> dict[str, Any]:
        from selenium.webdriver.common.keys import Keys

        before = self._driver.current_url
        button = self._submit_button()
        if button is not None:
            button.click()
        else:
            password_field = self._first_visible((_PASSWORD_SELECTOR,))
            if password_field is None:
                raise BrowserError("the login form is gone — nothing to submit")
            password_field.send_keys(Keys.ENTER)

        # Selenium has no networkidle, so wait for the thing that actually
        # matters: the page becoming something other than the login form.
        deadline = threading.Event()
        for _ in range(int(max(wait_seconds, 1) * 4)):
            if self._driver.current_url != before or self._first_visible(
                (_PASSWORD_SELECTOR,)
            ) is None:
                break
            deadline.wait(0.25)

        return {
            "url": self._driver.current_url,
            "title": (self._driver.title or "").strip(),
            "navigated": self._driver.current_url != before,
            "still_has_password": self._first_visible((_PASSWORD_SELECTOR,)) is not None,
            "wants_code": self._first_visible(_OTP_SELECTORS) is not None,
        }


# One browser per Jarvis process, built lazily — importing this module must not
# start a browser, or every `python3 run.py` would. Rebuilt if the configured
# engine changes, so switching engines takes effect on the next command rather
# than the next restart, like the rest of the action layer.
_SESSION: BrowserSession | None = None
_SESSION_ENGINE = ""
_SESSION_LOCK = threading.Lock()


def session() -> BrowserSession:
    global _SESSION, _SESSION_ENGINE

    wanted = engine()
    with _SESSION_LOCK:
        if _SESSION is not None and _SESSION_ENGINE == wanted:
            return _SESSION
        if _SESSION is not None:
            logger.info("engine changed to %r — closing the old browser", wanted)
            _SESSION.close()
        _SESSION = (
            SystemFirefoxSession() if wanted in SYSTEM_ENGINES else PlaywrightSession()
        )
        _SESSION_ENGINE = wanted
        return _SESSION


@atexit.register
def _close_session() -> None:
    if _SESSION is not None:
        _SESSION.close()


def open_portal(url: str, *, dry_run: bool = False, on_status: StatusFn | None = None) -> str:
    """Open `url` in Jarvis's browser and describe the page (a model-facing string)."""
    from jarvis.tools import normalize_url  # local import: tools imports this module

    url = normalize_url(url)
    if dry_run:
        logger.info("[dry run] would open %s in the controlled browser", url)
        return f"(dry run — nothing was opened) Would open {url} in Jarvis's browser."

    if on_status:
        on_status(f"Opening {url}...")
    logger.info("open_portal(%r)", url)
    info = session().open_portal(url)

    description = f"Opened {info['url']} in Jarvis's browser — the page is titled {info['title']!r}."
    if info["login_form"]:
        # Landing on a login page is the whole reason open_portal exists, so
        # Jarvis leads from here instead of waiting for the model to work out
        # that a credential is the obvious next thing. jarvis.login decides what
        # to say — whether there's a saved login for this site is its business,
        # not the browser's. Imported here rather than at module scope because
        # jarvis.login imports this module.
        from jarvis import login

        description += " It has a login form with a password field."
        description += login.portal_hint(info["url"])
    else:
        # Said out loud rather than left implied: a spoken URL often arrives
        # mangled, and landing on the wrong page is much cheaper to notice here
        # than three steps later when fill_login_form has nothing to type into.
        description += (
            " It has no login form on it. If the user asked to log in, this is "
            "probably not the page they meant — check the URL with them rather "
            "than trying to fill anything in."
        )
    return description
