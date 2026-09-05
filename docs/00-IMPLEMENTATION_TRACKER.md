# Implementation Tracker

**Rule:** read this doc before starting any work on Jarvis, to see what's already
done and what's left. After finishing any piece of work, update this doc — check
off what's done, and add a dated note if anything deviated from the original plan
in `01-PHASE_PLAN.md` (scope changed, a task got split, something turned out
harder/easier than expected). Tasks map 1:1 to the bullets and "Definition of
done" in `01-PHASE_PLAN.md` — if you add a task here that isn't in that doc, add
it there too so the two stay in sync.

**Current focus:** Phase 0 is ✅ Done. Ready to start Phase 1 — voice in → text out.

**Status legend:** ☐ not started · 🔶 in progress · ✅ done · 🚫 blocked

---

## Phase 0 — Scaffolding & environment
Status: ✅ Done

- ✅ Project skeleton (Python, macOS) — `src/jarvis/`, `run.py`, `requirements.txt`, `.venv`
- ✅ Menu-bar shell (`rumps`) — `src/jarvis/main.py`
- ✅ Background mic permission proven working — `check_microphone()` in `src/jarvis/permissions.py`
- ✅ Background camera permission proven working — `check_camera()`, same file
- ✅ Process survives reboot / runs at login (`launchd` agent) — installed, verified via
  `launchctl kickstart -k` (equivalent to a login relaunch): process starts, menu bar icon
  appears, mic/camera both ready, with no manual launch step
- ✅ On-disk layout: `config/`, `memory/`, `secrets/`, `logs/` — created, `.gitignore`'d appropriately
- **Definition of done:** background process, menu bar icon, prints "mic: ready" / "camera: ready"
  — confirmed **end-to-end and visually** (by you, in your actual menu bar), both launched via
  `open` and launched by `launchd` directly.

Notes (2026-09-05):
- **Bug found & fixed:** `rumps.notification()` throws `RuntimeError` when run as a bare
  `python3` script (no `CFBundleIdentifier`). This crashed the app *before* the menu bar icon
  ever appeared, which would have failed Phase 0's core deliverable. Fixed in `main.py` with a
  try/except that falls back to logging — the menu bar title already carries the same info, so
  this is a dev-mode-only degradation, not a functional gap.
- **Flakiness found & fixed:** `check_camera()` occasionally reported "NOT ready" on the very
  first frame read right after opening the device (camera warm-up), even though the camera was
  genuinely accessible — confirmed by immediately retrying and getting a good frame. Fixed by
  retrying up to 5 reads before concluding failure (`warmup_attempts` param).
- **Plan deviation — py2app dropped, replaced with a thin wrapper script:** tried packaging via
  `setup.py py2app` (as originally planned) and it failed outright on this machine's Anaconda-based
  Python: the built app crashed on launch with `Library not loaded: @rpath/libffi.8.dylib`, and
  separately, `cv2`/`sounddevice`/`numpy` weren't even being bundled (py2app's static analysis
  missed the imports since they're inside function bodies in `permissions.py`). Rather than fight
  Anaconda-Python/py2app compatibility — and needing to re-fight it every future phase that adds a
  heavy native dep (Whisper, MediaPipe, Playwright's bundled browsers) — replaced it with
  `scripts/build_app.sh`: a minimal hand-built `.app` whose executable is a shell one-liner that
  `exec`s the existing `.venv`'s python3 against `run.py`. Same Info.plist/CFBundleIdentifier/
  usage-description benefits, none of the freezing fragility. `setup.py` and
  `requirements-build.txt` deleted; `install_launch_agent.sh` and `main.py`/`run.py` comments
  updated to point at the new script instead.
- **Bug found & fixed — the actual reason the menu bar icon didn't appear:** the first working
  `build_app.sh` (shell script that `exec`'d `.venv/bin/python3`) got mic/camera TCC identity
  right (dialogs correctly said "Jarvis", confirmed by you), but the menu bar icon never showed.
  Root cause, found by streaming the unified system log (`log stream`) through a real `open
  dist/Jarvis.app` launch: macOS's Control Center status-item service rejected every attempt with
  `scene activation failed ... XPC error`, repeating once a second. `codesign -dv` on the running
  binary showed `Info.plist=not bound` — Control Center requires the *actual running binary* to
  have its Info.plist bound via code signature, which a shell script that `exec`s an external
  interpreter can never provide (exec always swaps out the running image's identity; only TCC's
  more lenient launch-context-based check tolerates that, not Control Center's scene service).
  Fix: `scripts/build_app.sh` now **copies** the real interpreter binary into
  `Contents/MacOS/Jarvis` (not a wrapper script) and ad-hoc re-signs the whole bundle
  (`codesign --force --deep -s -`) so Info.plist gets bound to it. Confirmed via the same
  `log stream` technique: zero scene-activation errors on the next launch, and you visually
  confirmed the menu bar icon.
- **Two more bugs found chasing that fix, each confirmed and fixed in turn:**
  - Copying the raw interpreter binary loses its venv context (a venv's `bin/python3` normally
    finds a `pyvenv.cfg` next to itself; a copy placed inside `Contents/MacOS/` has no access to
    that) — surfaced as `ModuleNotFoundError: No module named 'rumps'`. Fixed by adding the venv's
    real site-packages directory to `PYTHONPATH` in `build_app.sh` (queried via `.venv/bin/python3
    -c 'import site; print(site.getsitepackages()[0])'`, not hardcoded).
  - Since a bare double-click can't pass CLI args, `build_app.sh` auto-runs Jarvis via a
    `sitecustomize.py` (auto-imported by Python's `site` module on every startup) triggered by a
    `JARVIS_RUN_SCRIPT` env var set through Info.plist's `LSEnvironment`. That env var **only
    applies when launched via `open`/Finder** — `launchd`'s own `ProgramArguments` exec bypasses
    LaunchServices (and Info.plist) entirely, so the launchd-started process saw no
    `JARVIS_RUN_SCRIPT`, sitecustomize no-op'd, and the interpreter just exited cleanly with no
    error (`launchctl print` showed `last exit code = 0`, silently wrong). Fixed by also setting
    `JARVIS_RUN_SCRIPT`/`PYTHONPATH` directly via `EnvironmentVariables` in the launchd plist
    itself (`scripts/com.jarvis.agent.plist.template` + `install_launch_agent.sh`), independent of
    Info.plist.
- **Verified end-to-end, confirmed by you (not just by me):**
  - `mic`/`camera` TCC dialogs correctly attributed to "Jarvis", Allow granted.
  - Menu bar shows `Jarvis [🎤📷]` — both launched via `open` and launched directly by `launchd`
    (`launchctl kickstart -k gui/<uid>/com.jarvis.agent`, simulating a login relaunch) — the
    launchd agent is installed and `launchctl print` shows `state = running`.
  - `logs/jarvis.log` shows the correct `mic: ready` / `camera: ready` sequence in both cases.
- **Known dev-workflow friction, not a blocker, worth knowing for later phases:** ad-hoc code
  signing (`-s -`) gives the bundle a fresh identity hash on every rebuild, so macOS sometimes
  wants to reconfirm mic/camera permission after a `build_app.sh` rerun. A stable self-signed
  certificate (via Keychain Access) would avoid this if it becomes annoying during Phase 1+
  iteration — not worth setting up preemptively.

---

## Phase 1 — Voice in → text out
Status: ☐ Not started

- ☐ Push-to-talk hotkey trigger
- ☐ Local STT wired up (`faster-whisper` or `whisper.cpp`)
- ☐ Transcript logged / shown, no action taken yet
- **Definition of done:** hotkey → speak → correct transcript in ~1-2s

Notes:
- _(none yet)_

---

## Phase 2 — The brain: model-agnostic understanding
Status: ☐ Not started

- ☐ Model Router interface defined (`complete(messages, tools) -> reply | tool_call`)
- ☐ Claude backend implementation
- ☐ OpenAI backend implementation
- ☐ Local Ollama backend implementation
- ☐ Tool-calling contract defined (fixed small tool list)
- ☐ Intent parsing loop (transcript → model → reply or tool call)
- ☐ `open_url` / `open_app` wired as first real tool calls
- **Definition of done:** "open github.com" / "open Claude Code" resolve correctly on all three backends

Notes:
- _(none yet)_

---

## Phase 3 — Real actions & the Claude Code sub-agent
Status: ☐ Not started

- ☐ `open_app` / `open_url` tool (real `open`/`osascript` calls)
- ☐ `run_claude_code` tool (shells out to `claude` CLI, streams output, reports back)
- ☐ Basic Playwright browser tool (opens a portal URL)
- **Definition of done:** "open project X, use Claude Code to find this bug" runs end to end

Notes:
- _(none yet)_

---

## Phase 4 — Voice out + credentialed login
Status: ☐ Not started

- ☐ TTS wired up (local: Piper / `say` / `AVSpeechSynthesizer`)
- ☐ `credential_capture` mode in intent parser (local-only handling, per doc 04)
- ☐ `redact_secrets()` log filter applied globally
- ☐ Keychain-backed vault module (`get(site)`, `set(site, cred)`)
- ☐ `fill_login_form` tool (fills, waits for spoken confirmation before submit)
- **Definition of done:** spoken login on a real test site works, credential never hits a log file

Notes:
- _(none yet)_

---

## Phase 5 — Hand gestures
Status: ☐ Not started

- ☐ MediaPipe Hands wired to webcam feed
- ☐ Gesture vocabulary defined (start small: confirm / cancel)
- ☐ Gestures mapped into the same tool-calling contract as voice
- **Definition of done:** thumbs-up reliably confirms a pending action, no false triggers over 10 min

Notes:
- _(none yet)_

---

## Phase 6 — Memory & personalization
Status: ☐ Not started

- ☐ SQLite memory store set up
- ☐ Aliases (e.g. "my project" → path) storable/retrievable
- ☐ Preferences (voice, default backend per task type) storable/retrievable
- ☐ Selective retrieval into prompt context (not full dump)
- **Definition of done:** "open my project" works days later without repeating the path

Notes:
- _(none yet)_

---

## Phase 7 — Multi-agent orchestration
Status: ☐ Not started

- ☐ Framework decision revisited (LangGraph / CrewAI / custom / Claude Agent SDK)
- ☐ Coding sub-agent (wraps Claude Code)
- ☐ Browser/automation sub-agent (wraps Playwright tools)
- ☐ Research/lookup sub-agent
- ☐ Main loop routes across sub-agents instead of one-prompt-does-everything
- **Definition of done:** a request needing 2 sub-agents completes without manual sequencing

Notes:
- _(none yet)_

---

## Phase 8 — Polish: always-on companion
Status: ☐ Not started

- ☐ Wake-word detection (openWakeWord/Porcupine), replacing push-to-talk
- ☐ Menu bar status (listening/thinking/speaking)
- ☐ Quick history view / mute toggle
- ☐ Notifications for long-running tool calls
- ☐ Latency pass (caching, streaming, fast-vs-smart model split)

Notes:
- _(none yet)_

---

## Phase 9 (stretch) — Proactive behavior
Status: ☐ Not started — deliberately deferred, do not start early

- ☐ _(left open on purpose — revisit only once Phases 0-8 are boring and reliable)_

Notes:
- _(none yet)_
