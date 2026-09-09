"""
Credential vault — Phase 4.

This is doc 02's "Credential Vault" box and doc 04's point 3, and it is
deliberately tiny: a few dozen lines over the **macOS Keychain**, with no
crypto of Jarvis's own. The reasoning is worth keeping in view, because writing
a "vault" is a tempting weekend project:

- The Keychain is already encrypted at rest, already gated by the OS, and
  already audited. A file Jarvis invents is none of those things.
- A bug anywhere in Jarvis's own code can leak a plaintext secrets file. It
  cannot leak a Keychain item it never read.

Everything is keyed by *site as you say it out loud* ("the billing portal"),
normalised by `site_key`, so saying the same thing tomorrow finds the same
entry. Values are one JSON object per site holding the username and password
together, because they're always dictated and used together.

Storage goes through the `keyring` package, which wraps the same Security
framework the `security` CLI does. First read from a launchd-started process
may raise a Keychain access prompt — that's the OS access control doing its
job, not a bug; allow it once.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from jarvis import redact

logger = logging.getLogger("jarvis.vault")

# Shows up in Keychain Access as the item's "where", so make it recognisable —
# you will want to be able to find and delete these by hand.
SERVICE = "Jarvis (site logins)"

# Spoken site names carry filler that means nothing to a lookup: "open *the*
# billing portal", "the billing portal *website*".
_LEADING_ARTICLES = re.compile(r"^(the|my|our|a)\s+", re.IGNORECASE)
_TRAILING_NOUNS = re.compile(r"\s+(website|site|page|portal|login|log\s?in)$", re.IGNORECASE)


class VaultError(RuntimeError):
    """The Keychain couldn't be read or written."""


@dataclass(frozen=True)
class Credential:
    username: str
    password: str

    def __repr__(self) -> str:  # pragma: no cover - trivial, but load-bearing
        # A dataclass repr would print the password into any log line or
        # traceback that touches this object. The redaction filter would catch
        # it, but not printing it in the first place is the better layer.
        return f"Credential(username={self.username!r}, password=<hidden>)"


def site_key(site: str) -> str:
    """Normalise a spoken site name into a stable Keychain key.

    "The Billing Portal" and "billing portal" are the same login, and a URL is
    reduced to its host so "open https://portal.example.com" and "the example
    portal" don't fight over which spelling is canonical.
    """
    site = (site or "").strip()
    if not site:
        return ""
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/]+)", site)
    if match:
        return match.group(1).lower().removeprefix("www.")
    site = " ".join(site.split()).lower().removeprefix("www.")
    site = _LEADING_ARTICLES.sub("", site)
    # Strip trailing nouns until nothing changes, so every way you might name
    # one site lands on one key: "the billing portal", "billing portal login"
    # and "the billing portal website" all become "billing". Stopping after a
    # single pass would leave the first two on *different* keys, which shows up
    # as Jarvis mysteriously not remembering a login you know you saved.
    while True:
        trimmed = _TRAILING_NOUNS.sub("", site).strip()
        # Never normalise down to nothing: "the portal" stays "portal" rather
        # than becoming an empty key that quietly matches nothing.
        if not trimmed or trimmed == site:
            break
        site = trimmed
    return site.strip()


def _keyring():
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise VaultError(
            "the `keyring` package isn't installed — run "
            "`.venv/bin/python3 -m pip install keyring`"
        ) from exc
    return keyring


def get(site: str) -> Credential | None:
    """Look up the saved credential for `site`. None if there isn't one."""
    key = site_key(site)
    if not key:
        return None
    try:
        raw = _keyring().get_password(SERVICE, key)
    except VaultError:
        raise
    except Exception as exc:  # noqa: BLE001 - keyring raises backend-specific errors
        raise VaultError(f"couldn't read the Keychain: {exc}") from exc

    if not raw:
        return None
    try:
        data = json.loads(raw)
        credential = Credential(username=data["username"], password=data["password"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.error("the Keychain entry for %r is malformed (%s) — ignoring it", key, exc)
        return None

    # Anything now in memory is a live secret, so the log filter must know about
    # it before it has any chance of being logged.
    redact.register(credential.password)
    logger.info("found a saved login for %r", key)
    return credential


def set(site: str, credential: Credential) -> bool:  # noqa: A001 - doc 04 names it set()
    """Save `credential` for `site`, replacing any existing entry."""
    key = site_key(site)
    if not key:
        return False
    redact.register(credential.password)
    payload = json.dumps({"username": credential.username, "password": credential.password})
    try:
        _keyring().set_password(SERVICE, key, payload)
    except VaultError:
        raise
    except Exception as exc:  # noqa: BLE001 - keyring raises backend-specific errors
        raise VaultError(f"couldn't write to the Keychain: {exc}") from exc
    logger.info("saved the login for %r to the Keychain", key)
    return True


def forget(site: str) -> bool:
    """Delete `site`'s entry. True if there was one to delete."""
    key = site_key(site)
    if not key:
        return False
    try:
        _keyring().delete_password(SERVICE, key)
    except VaultError:
        raise
    except Exception as exc:  # noqa: BLE001 - keyring raises "not found" as an exception
        logger.info("nothing saved for %r to forget (%s)", key, exc)
        return False
    logger.info("forgot the saved login for %r", key)
    return True
