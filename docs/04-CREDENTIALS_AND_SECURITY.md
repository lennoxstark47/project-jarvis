# Credentials & Security

Your call: no vault-first ceremony — you just tell Jarvis the username/password
out loud in the moment (spelling it out if the word is one it might mishear), and
it types it into the login form. This doc is how to build *that*, specifically,
without it turning into the reason your accounts get compromised. Nothing here
adds friction to the flow you asked for — it's about what happens to the
credential in the background while that flow runs.

## The flow, as you described it

1. You: "Open the billing portal. Username is jsmith, password is — spelling it
   out — Tango-Romeo-Alpha-7-Charlie-9."
2. Jarvis transcribes it, recognizes it's in credential-dictation mode, types
   username + password into the detected login fields.
3. Jarvis confirms out loud before hitting submit (cheap insurance against a
   misheard character logging you into the wrong thing or locking the account).

## Where the risk actually is, and what to do about each part

**1. The mic transcript is the credential in plaintext, however briefly.**
Whisper (local, per doc 02) never leaves your machine, so this is contained —
but only if you also make sure nothing *downstream* writes that transcript
anywhere durable. Concretely: the credential-dictation turn must be excluded from
the conversation log / memory store (doc 02's Memory Store) — redact it to
`[credential omitted]` before it's ever written to disk, and never include it in
what gets sent back to Claude/GPT for the *next* turn's context. The local model
router only needs to know "a login tool call happened," not the characters
involved.

**2. Cloud LLMs should never see the raw characters.**
If a cloud backend (Claude/GPT) is doing the intent-parsing for this turn, design
the tool call so the credential value is captured by a **local-only** step (e.g.
a lightweight local regex/parser that detects "username is X, password is Y" and
extracts the values *before* the full transcript is sent to a cloud model) rather
than round-tripped through the cloud API as plain arguments. Concretely: send the
cloud model something like `tool_call: fill_login_form(site="billing portal", credential_ref="pending_dictation_1")`
and resolve `pending_dictation_1` to the actual value locally, never in the prompt/
response that hits the network. This is the one piece worth building carefully
even though it's invisible to you day-to-day — everything else in this list is
either free or a one-line change.

**3. Storage: keep it, but keep it in the OS vault, not a Jarvis-owned file.**
Since you'll often repeat the same portal, cache what you dictate in the
**macOS Keychain** (via the `security` CLI, or Python's `keyring` package which
wraps it) keyed by site name, rather than a plaintext file or a homemade "vault"
Jarvis invents. This gets you real OS-level encryption and access control for
free, and means a bug in Jarvis's own code can't leak a plaintext secrets file.
Next time you say "log into the billing portal" without redictating, Jarvis reads
from Keychain instead of asking again — same natural flow, one less repetition
over time.

**4. Typing it in: fill, don't auto-submit.**
Playwright fills the detected fields; Jarvis speaks a one-line confirmation
("filled in, want me to submit?") before hitting the login button. This is the
cheap safety net mentioned above — it catches STT mis-transcriptions before they
become a failed-login lockout or, worse, credentials typed into the wrong field
on the wrong page.

**5. Logs, screen recordings, crash reports — audit for leaks.**
Anywhere Jarvis writes debug output (the Phase 0 log file, any future crash
reporting), make sure the redaction from point 1 is enforced at the logging layer
itself (e.g. a `redact_secrets()` filter every log line passes through), not just
"remembered" at each call site — call-site discipline is exactly the kind of rule
that gets forgotten under deadline pressure and silently leaks a password into a
log file.

*Extended 2026-09-09 (Phase 6).* The log stopped being the only place Jarvis
writes what you said: `memory/jarvis.db` now holds a rolling window of the
conversation, and a spoken login is *inside* that conversation. The same rule
applies to it, with one addition that matters — redaction happens on the way
**in**, not on the way out, so there is no moment at which the plaintext exists
in that file and no future reader who can forget to ask. What is stored is the
masked sentence ("log in as alice, my [credential omitted]"), which is the same
string this point already sanctions for the log and the same one point 2 already
sends to the cloud model. Refusing to store it at all was tried first and is
worse: a spoken login is four turns long, and dropping the turns with
credentials in them leaves the memory window holding only the half that doesn't
explain what's happening. The file is created 0600 and lives under a gitignored
directory. Enforced in `jarvis/memory.py:remember_turn`, pinned in
`scripts/selftest_memory.py`.

*Also Phase 6:* a **preference** set by voice is set from an untrusted string —
Whisper's reconstruction, interpreted by a small model — so it can only reach
the config paths in `config.PREFERENCE_KEYS`. That list deliberately excludes
`actions.claude_code.allow_edits` and `actions.projects.roots`: the settings
that decide whether the coding sub-agent may write to your files and which
folders it may be launched in stay decisions you make in a file, by hand.

**6. 2FA/OTP: explicitly out of scope for automation.**
If a portal asks for a one-time code, that's a deliberate human-in-the-loop step —
Jarvis should stop, tell you a code is needed, and let you type it (or read it to
you if it can see it, e.g. from an email/SMS you've separately granted it access
to) rather than trying to intercept or bypass it.

## What this means for Phase 4, concretely

- A `credential_capture` mode the intent-parser can enter when it hears
  "username is / password is", which routes the raw values to a **local-only**
  handler — never through the cloud model call for that turn.
- A thin `keyring`-backed vault module: `get(site) -> Optional[cred]`,
  `set(site, cred)` — this is the entire "Credential Vault" box from doc 02, a
  few dozen lines, no custom crypto to write yourself.
- A `redact_secrets()` log filter applied globally, not per call site.
- `fill_login_form` stops before submit and waits for spoken confirmation.

None of this changes the experience you asked for. You still just say the
username and password out loud, in the moment, and Jarvis handles it. The only
difference from "no fully natural way" taken completely literally is that the
plaintext value's *lifetime* is deliberately kept as short as possible, and its
one durable copy lives in the same OS vault your browser's own password manager
already trusts — not a new attack surface you'd otherwise be creating from scratch.
