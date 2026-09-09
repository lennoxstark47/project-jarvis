"""
The login flow — Phase 4, and the policy half of doc 04.

`jarvis.browser` knows how to type into a form. This module decides *what* gets
typed, where it came from, whether it's worth remembering, and — the part that
matters most — that nothing presses the login button without you saying so.

The order of operations is the security design, so it's worth stating plainly:

1. The credential was lifted out of the transcript **locally**, before the model
   call (`jarvis.credentials`), so the model asking for this tool has only ever
   seen a `credential_ref`.
2. `resolve()` turns that ref back into characters **here**, on this machine, in
   the tool call — never in a prompt, never in a response, never over a network.
3. If there's no ref (you said "log into the billing portal" and nothing else),
   the macOS Keychain is asked instead (`jarvis.vault`) — same flow, one less
   repetition, per doc 04's point 3.
4. The form is filled and Jarvis **stops**, arming a confirmation
   (`jarvis.confirm`) and asking out loud. Doc 04's point 4: a mis-transcribed
   character should cost you one spoken "no", not a locked account.
5. Only a human answer runs the submit. The model's tool list contains no way to
   press that button — deliberately, because the tool list is the part an
   untrusted sentence can reach.

Step 5 is also the seam Phase 5 plugs into: a thumbs-up is `confirm.resolve(True)`
and needs to know nothing about logins at all.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from jarvis import browser, config, confirm, credentials, vault
from jarvis.vault import Credential, VaultError

logger = logging.getLogger("jarvis.login")

StatusFn = Callable[[str], None]


def _settings() -> dict[str, Any]:
    return config.actions_config("login")


def _current_host() -> str:
    """The host of the page Jarvis's browser is on, or "" if it can't be asked.

    This, not the `site` argument, is what a saved login is filed under. The
    model's idea of what the site is called is unreliable — live on 2026-09-08 it
    called a page it had just opened at the-internet.herokuapp.com "the billing
    portal", copying the example out of the tool description — and a credential
    filed under a name the user never said is one they can never look up again.
    The page Jarvis actually typed into is a fact, so it's the key.
    """
    try:
        return vault.site_key(browser.session().page_summary().get("url", ""))
    except browser.BrowserError as exc:
        logger.debug("couldn't ask the browser where it is: %s", exc)
        return ""


def _resolve_credential(site: str, credential_ref: str) -> tuple[Credential | None, str, str]:
    """Find the credential to type. Returns (credential, source, ref-it-came-from).

    Order matters: a credential you just spoke beats a saved one, because the
    reason you'd say it out loud again is that the saved one is wrong.
    """
    if credential_ref:
        credential = credentials.resolve(credential_ref)
        if credential is not None:
            return credential, "dictated", credential_ref
        # A ref that doesn't resolve is an expired dictation, or a model that
        # invented one. Neither is a reason to give up — fall through.
        logger.info("credential_ref %r didn't resolve", credential_ref)

    # No usable ref, but something was dictated recently: the model dropped the
    # ref on its way through, which small models do. See credentials.latest.
    recent = credentials.latest()
    if recent is not None:
        ref, credential = recent
        logger.info("no usable ref given — using the credential dictated as %s", ref)
        return credential, "dictated", ref

    # Nothing dictated — the Keychain, then. Under the page Jarvis is looking at
    # first (see _current_host), and under whatever the user called it second,
    # which covers entries saved before this was keyed on the host.
    for key in (_current_host(), site):
        if not key:
            continue
        try:
            saved = vault.get(key)
        except VaultError as exc:
            logger.error("Keychain lookup failed: %s", exc)
            return None, "", ""
        if saved is not None:
            return saved, "keychain", ""
    return None, "", ""


def _remember(site: str, credential: Credential) -> None:
    if not _settings().get("remember", True):
        logger.info("actions.login.remember is off — not saving this credential")
        return
    try:
        vault.set(site, credential)
    except VaultError as exc:
        # Not being able to save is a convenience failure, not a login failure:
        # the credential is already typed into the page.
        logger.error("couldn't save the login for %r: %s", site, exc)


def _describe_submit(result: dict[str, Any]) -> str:
    """Turn the post-submit page state into one spoken sentence."""
    if result.get("wants_code"):
        # doc 04, point 6: 2FA is a human step, on purpose. Say so and stop.
        return (
            "That went through, but it's asking for a one-time code — "
            "you'll need to type that one in yourself."
        )
    if result.get("still_has_password") and not result.get("navigated"):
        return (
            "It didn't take — the login form is still there. "
            "The password may have come through wrong."
        )
    title = result.get("title") or ""
    return f"Logged in — the page is now {title!r}." if title else "Logged in."


def portal_hint(url: str) -> str:
    """What to add when open_portal lands on a page with a login form.

    Doing this here, rather than leaving the model to notice, is what makes the
    flow feel like the one you asked for: you say "open GitHub and go to login",
    and Jarvis comes back with "what's your username and password?" instead of
    "the login page is open" followed by silence. It also arms the local parser
    (jarvis.credentials), so your answer is understood even said bare — the
    model is told to ask, and Jarvis handles the answer itself either way.
    """
    try:
        saved = vault.get(url)
    except VaultError as exc:
        logger.error("Keychain lookup failed: %s", exc)
        saved = None

    if saved is not None:
        return (
            " Jarvis already has a saved login for this site. If the user wants to "
            "log in, call fill_login_form now — you do not need to ask them for "
            "anything."
        )

    credentials.expect("both")
    return (
        " Jarvis has no saved login for it. Ask the user out loud for their username "
        "and password, in one short sentence, and call no other tool this turn — "
        "Jarvis captures their answer itself and you will never see it."
    )


def fill_login_form(
    site: str = "",
    credential_ref: str = "",
    *,
    dry_run: bool = False,
    on_status: StatusFn | None = None,
) -> str:
    """Fill the open login form for `site`, then ask before submitting.

    Returns a model-facing string. Never raises — a failure here is something
    for Jarvis to say out loud, not a crash (same contract as every other tool).
    """
    site = (site or "").strip()
    credential_ref = (credential_ref or "").strip()

    # Models put the ref in the site field. Seen live 2026-09-08:
    # fill_login_form(site='pending_dictation_1'). Recognise it rather than
    # saving a credential under a site called "pending_dictation_1".
    if site.startswith("pending_dictation"):
        site, credential_ref = "", credential_ref or site
    if credentials.PLACEHOLDER in site:
        # The model reaching for the redaction marker in the transcript and
        # calling it a site name. Also seen live — it costs nothing to ignore,
        # and it would otherwise be read back out loud as the site's name.
        site = ""

    credential, source, credential_ref = _resolve_credential(site, credential_ref)
    if credential is None:
        # Arm the parser: whatever is said next is the answer to this, even said
        # bare ("it's jsmith") with nothing to key on. See jarvis.credentials.
        needed = credentials.expecting() or "both"
        credentials.expect(needed)
        asked = "username and password" if needed == "both" else needed
        return (
            f"I don't have a login for {site or 'that site'} yet. Ask the user out loud "
            f"to say their {asked}, in one short sentence, and call no tool at all this "
            "turn — Jarvis handles their answer itself."
        )

    if source == "dictated" and not credential.username:
        # Half a credential is not something to type into a login form: filling
        # the password alone leaves the form incomplete and the user watching a
        # page that didn't submit.
        credentials.expect("username")
        return (
            "I have the password but not the username. Ask the user out loud for their "
            "username, in one short sentence, and call no tool at all this turn."
        )

    if dry_run:
        # Deliberately says *which* fields and where the credential came from,
        # without any part of the value — that's the whole thing worth checking
        # in a dry run.
        return (
            f"(dry run — nothing was typed) Would fill the login form on {site!r} "
            f"with the {source} credential for {credential.username or '(no username)'}, "
            "then ask before submitting."
        )

    if on_status:
        # Not "...for {site}": the model's name for the page is unreliable
        # enough that putting it in the menu bar mostly misinforms.
        on_status("Filling in the login...")

    try:
        filled = browser.session().fill_login(credential.username, credential.password)
    except browser.BrowserError as exc:
        return f"I couldn't fill that in: {exc}"

    # Filed under the page it was actually typed into, not the name the model
    # gave it — but only once the login has actually worked. See _submit.
    key = vault.site_key(filled.get("url", "")) or site
    remember = credential if source == "dictated" else None

    if not _settings().get("confirm_before_submit", True):
        logger.warning("confirm_before_submit is off — submitting without asking")
        return _submit(site, key, remember, credential_ref)

    # The page's own title first, `site` (the model's words) only as a fallback:
    # this string is what gets read back when asking whether to submit, and it
    # should name the page Jarvis is actually looking at.
    confirm.arm(
        f"submit the login form on {filled.get('title') or site or 'this page'}",
        lambda: _submit(site, key, remember, credential_ref),
    )
    who = f" as {credential.username}" if credential.username else ""
    return (
        f"Filled in the login form{who}. Say the words to the user: "
        '"Filled in, want me to submit?" — then stop and wait for their answer. '
        "Do not call any other tool."
    )


def _worked(result: dict[str, Any]) -> bool:
    """Did that login actually go through?

    A one-time-code prompt counts as yes: the site would not be asking for a
    second factor if it had rejected the first one.
    """
    if result.get("wants_code"):
        return True
    return bool(result.get("navigated")) and not result.get("still_has_password")


def _submit(
    site: str, key: str = "", remember: Credential | None = None, credential_ref: str = ""
) -> str:
    """Press the login button. Reached only through a confirmed action.

    This is also where a dictated credential is *saved*, and that placement is
    the point. Saving at fill time — which is what this did until 2026-09-08 —
    caches whatever Whisper heard before anyone knows whether it was right, so a
    mis-transcription becomes a permanent Keychain entry that gets silently
    reused on every later visit. Found in the wild: a real GitHub username
    stored as "lenoxstark47", one letter short, from a single mis-heard
    dictation. A credential is only worth keeping once a site has accepted it.
    """
    wait = float(_settings().get("submit_wait", 8))
    try:
        result = browser.session().submit_login(wait_seconds=wait)
    except browser.BrowserError as exc:
        return f"I couldn't submit that: {exc}"
    logger.info("submitted the login for %r -> %s", site, result.get("url"))

    if _worked(result):
        if remember is not None:
            _remember(key or site, remember)
            credentials.release(credential_ref)
    else:
        # It didn't take, so the credential is suspect. Arm the parser: the
        # natural next thing to say is the password again, and it should be
        # understood as one rather than treated as a command.
        credentials.expect("both")

    return _describe_submit(result)
