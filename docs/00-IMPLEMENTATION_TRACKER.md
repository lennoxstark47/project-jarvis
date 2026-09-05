# Implementation Tracker

**Rule:** read this doc before starting any work on Jarvis, to see what's already
done and what's left. After finishing any piece of work, update this doc — check
off what's done, and add a dated note if anything deviated from the original plan
in `01-PHASE_PLAN.md` (scope changed, a task got split, something turned out
harder/easier than expected). Tasks map 1:1 to the bullets and "Definition of
done" in `01-PHASE_PLAN.md` — if you add a task here that isn't in that doc, add
it there too so the two stay in sync.

**Current focus:** Phase 0 — code done and smoke-tested; waiting on you to build/install
the real .app + launchd agent (see Phase 0 notes below) before this flips to ✅ and we
move to Phase 1.

**Status legend:** ☐ not started · 🔶 in progress · ✅ done · 🚫 blocked

---

## Phase 0 — Scaffolding & environment
Status: 🔶 In progress (5/6 tasks done — one needs your hands-on step, see below)

- ✅ Project skeleton (Python, macOS) — `src/jarvis/`, `run.py`, `requirements.txt`, `.venv`
- ✅ Menu-bar shell (`rumps`) — `src/jarvis/main.py`, ran for 4+s without crashing, logged correctly
- ✅ Background mic permission proven working — `check_microphone()` in `src/jarvis/permissions.py`
- ✅ Background camera permission proven working — `check_camera()`, same file
- ☐ Process survives reboot / runs at login (`launchd` agent) — **artifacts ready, not installed yet**
- ✅ On-disk layout: `config/`, `memory/`, `secrets/`, `logs/` — created, `.gitignore`'d appropriately
- **Definition of done:** background process, menu bar icon, prints "mic: ready" / "camera: ready"
  — met for the dev-mode process (`python3 run.py`); the packaged/login-item version is the one
  remaining task below.

Notes (2026-09-05):
- **Bug found & fixed:** `rumps.notification()` throws `RuntimeError` when run as a bare
  `python3` script (no `CFBundleIdentifier` — that only exists once packaged as a real `.app`
  via `setup.py py2app`). This crashed the app *before* the menu bar icon ever appeared, which
  would have failed Phase 0's core deliverable. Fixed in `main.py` with a try/except that falls
  back to logging — the menu bar title already carries the same info, so this is a dev-mode-only
  degradation, not a functional gap. Packaging via py2app removes the need for the fallback.
- **Flakiness found & fixed:** `check_camera()` occasionally reported "NOT ready" on the very
  first frame read right after opening the device (camera warm-up), even though the camera was
  genuinely accessible — confirmed by immediately retrying and getting a good frame. Fixed by
  retrying up to 5 reads before concluding failure (`warmup_attempts` param).
- **Verified so far (this session, headless shell, not yet a real login session):** installed
  `rumps`/`sounddevice`/`opencv-python`/`numpy` into `.venv`; confirmed real hardware exists
  (`FaceTime HD Camera`, `MacBook Air Microphone` via `system_profiler`/`sounddevice.query_devices()`);
  ran `python3 run.py` and got `mic: ready` / `camera: ready` in `logs/jarvis.log`, process stayed
  alive in the rumps event loop until manually killed.
- **What's still open, and why I didn't do it automatically:** the launchd agent
  (`scripts/com.jarvis.agent.plist.template` + `scripts/install_launch_agent.sh`) registers a
  persistent login item on your actual Mac, and building the real `.app` (`python3 setup.py py2app`)
  plus granting its mic/camera permission prompts both require you to click through System Settings
  dialogs yourself — neither is something I can do or verify from here. **Your turn:**
  1. `source .venv/bin/activate && pip install -r requirements-build.txt && python3 setup.py py2app`
  2. Open `dist/Jarvis.app` once by hand and click "Allow" on the mic/camera prompts (should now
     say "Jarvis" wants to access..., not "Terminal" or "Python" — that's the thing this step proves).
  3. Confirm you see a "Jarvis [🎤📷]" item in the actual menu bar.
  4. `scripts/install_launch_agent.sh` to make it survive login/reboot (`scripts/uninstall_launch_agent.sh` reverses it).
  5. Tell me it's confirmed (or what broke) and I'll check this off and close out Phase 0.

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
