"""
Browser automation — Phase 3.

`open_url` hands a URL to macOS and forgets about it, which is right for "open
github.com" and useless for "open the billing portal and log me in": nothing
downstream can see the page. This module is the other kind of opening — a
browser Jarvis *drives*, which Phase 4's `fill_login_form` will type into.

Phase 3 stops at landing on the page and saying what's there (title, and
whether it looks like a login form). Credentials are doc 04's subject and
Phase 4's job.

Two design points that aren't obvious:

**It runs on its own thread, for the life of the process.** Playwright's sync
API is bound to the thread that started it, and Jarvis handles every utterance
on a *fresh* thread (see jarvis.voice — each release of the hotkey spawns one).
So the browser lives on one dedicated worker thread and callers post work to it
through a queue. That also fixes the more obvious problem: a browser opened
inside a tool call would close the instant the call returned, which is not what
"open this portal" means.

**The profile is persistent** (`actions.browser.user_data_dir`, under
`memory/`). Cookies and sessions survive a restart, so Jarvis doesn't re-login
to everything every morning — and so Phase 4's login only has to happen once
per site. That directory holds real session cookies: it is `memory/`-adjacent
on purpose, and gitignored like the rest of it.
"""
from __future__ import annotations

import atexit
import logging
import queue
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

# Selectors that mean "this page wants a password". Deliberately narrow: this
# is a hint Jarvis says out loud, and later the anchor Phase 4 fills in — a
# false positive there types a password into the wrong field.
_PASSWORD_SELECTOR = "input[type=password]"
_USERNAME_SELECTORS = (
    "input[type=email]",
    "input[name*=user i]",
    "input[name*=email i]",
    "input[id*=user i]",
    "input[id*=email i]",
)


class BrowserError(RuntimeError):
    """The browser couldn't be started or driven."""


def _settings() -> dict[str, Any]:
    return config.actions_config("browser")


def user_data_dir() -> Path:
    configured = _settings().get("user_data_dir", "memory/browser")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


class BrowserSession:
    """A long-lived Playwright browser, driven from one dedicated thread.

    Every public method is called from *some other* thread and posts a callable
    onto `_work`; `_serve` is the only thing that ever touches Playwright.
    """

    def __init__(self) -> None:
        self._work: queue.Queue[tuple[Callable[[], Any], queue.Queue]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._start_error: str = ""
        self._lock = threading.Lock()
        self._playwright = None
        self._context = None
        self._page = None

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
        # Launching Chromium with a cold profile is slow; give it room.
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
                reply.put(("ok", job()))
            except Exception as exc:  # noqa: BLE001 - crossed back to the caller intact
                logger.warning("browser command failed: %s", exc)
                reply.put(("error", exc))
        self._shutdown()

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
            raise BrowserError(str(payload)) from payload
        return payload

    # -- actions -------------------------------------------------------------

    def open_portal(self, url: str) -> dict[str, Any]:
        """Navigate to `url` and report what landed. Runs on the worker thread."""

        def job() -> dict[str, Any]:
            page = self._page
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:  # noqa: BLE001 - a chatty page never goes idle; not an error
                logger.debug("%s never reached networkidle — continuing", url)
            self._context.pages[0].bring_to_front()
            has_password = page.locator(_PASSWORD_SELECTOR).count() > 0
            has_username = any(
                page.locator(selector).count() > 0 for selector in _USERNAME_SELECTORS
            )
            return {
                "url": page.url,
                "title": (page.title() or "").strip(),
                "login_form": has_password,
                "username_field": has_username,
            }

        return self._submit(job)


# One browser per Jarvis process. Built lazily — importing this module must not
# start Chromium, or every `python3 run.py` would.
_SESSION = BrowserSession()
atexit.register(_SESSION.close)


def session() -> BrowserSession:
    return _SESSION


def open_portal(url: str, *, dry_run: bool = False, on_status: StatusFn | None = None) -> str:
    """Open `url` in Jarvis's own browser and describe the page (a model-facing string)."""
    from jarvis.tools import normalize_url  # local import: tools imports this module

    url = normalize_url(url)
    if dry_run:
        logger.info("[dry run] would open %s in the controlled browser", url)
        return f"(dry run — nothing was opened) Would open {url} in Jarvis's browser."

    if on_status:
        on_status(f"Opening {url}...")
    logger.info("open_portal(%r)", url)
    info = _SESSION.open_portal(url)

    description = f"Opened {info['url']} in Jarvis's browser — the page is titled {info['title']!r}."
    if info["login_form"]:
        description += " It has a login form with a password field."
    return description
