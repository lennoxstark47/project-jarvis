# Implementation Tracker

**Rule:** read this doc before starting any work on Jarvis, to see what's already
done and what's left. After finishing any piece of work, update this doc — check
off what's done, and add a dated note if anything deviated from the original plan
in `01-PHASE_PLAN.md` (scope changed, a task got split, something turned out
harder/easier than expected). Tasks map 1:1 to the bullets and "Definition of
done" in `01-PHASE_PLAN.md` — if you add a task here that isn't in that doc, add
it there too so the two stay in sync.

**Current focus: Phase 7 — multi-agent orchestration.** Phase 6 finished
2026-09-09; the next command is **"start phase 7"**.

Two things are open and neither blocks Phase 7:

- **Phase 5 (gestures) is paused at 🔶 In progress**, deliberately, by your call
  on 2026-09-09: *"we will work on the hand gestures later on, right now lot of
  things are left for voice command itself."* Everything is built; what remains
  is two verification runs that need a person in front of a camera. It is picked
  up by running the numbered list in its own section, not by re-reading this one.
- **Phase 4's carried-forward item** — the four-turn spoken login has never
  completed *from the microphone*. Phase 6 is what the tracker said would unblock
  it, and it now has: an utterance is no longer a fresh conversation, so the
  window carries "a login is in progress" from one turn to the next, and a portal
  URL can be remembered as an alias instead of surviving Whisper twice.
  **That is unblocked, not verified** — nobody has run the login by voice since
  memory landed. See "Where Phase 4 actually stands".

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
Status: ✅ Done

- ✅ Push-to-talk hotkey trigger — confirmed live, both `python3 run.py` and packaged `dist/Jarvis.app`
- ✅ Local STT wired up (`faster-whisper`) — confirmed live, multiple correct transcripts in both modes
- ✅ Transcript logged / shown, no action taken yet — confirmed via `logs/jarvis.log` and the menu bar UI (screenshot)
- **Definition of done:** hotkey → speak → correct transcript in ~1-2s — met in steady state
  (post model-load); see notes for the one-time model-load exception and the
  Whisper repetition-glitch fix that was needed to get there.

Notes (2026-09-05):
- **Implemented, awaiting your live test** (same pattern as Phase 0 — nothing gets
  checked off until you've actually confirmed it end-to-end):
  - `src/jarvis/voice.py` — `PushToTalk`: hold **F9** to record (via a `pynput`
    global key listener), release to stop and transcribe. Recording and
    transcription both run off the listener thread so holding the key never
    blocks release-detection, and a slow transcription never blocks the next
    press.
  - `src/jarvis/stt.py` — wraps `faster-whisper` (`base.en` model, CPU,
    int8) for fully local/offline transcription — no audio ever leaves the
    machine, consistent with doc 02's rationale (voice commands routinely
    contain credentials/paths).
  - `src/jarvis/main.py` — menu bar now shows a "Hold F9 to talk" hint and a
    "Last transcript: ..." item that updates after each utterance. Updated via
    a `rumps.Timer` polling a lock-protected string, not directly from the
    background thread — Cocoa/AppKit calls aren't safe off the main thread.
  - `requirements.txt` — added `faster-whisper>=1.0.0`, `pynput>=1.7.6`;
    installed into `.venv` and import-checked already this session.
- **New permission this phase, expect a prompt:** global hotkey listening
  needs macOS **Input Monitoring** access (System Settings → Privacy &
  Security → Input Monitoring), separate from Phase 0's mic/camera TCC grant,
  and tied to whatever binary is actually running — same identity story as
  Phase 0, so it'll need granting again for `dist/Jarvis.app` even though mic/
  camera are already approved for it.
- **First run will be slower than the ~1-2s target:** `faster-whisper` downloads
  the `base.en` model from Hugging Face Hub the first time it's used (one-time,
  needs internet). Steady-state latency after that is the real test of the
  Definition of done.
- **Not yet done:** confirm hold-F9 → speak → correct transcript appears in the
  menu bar and `logs/jarvis.log` within ~1-2s, both in dev mode (`python3
  run.py`) and packaged (`dist/Jarvis.app`, after granting Input Monitoring).

Notes (2026-09-05, later same session — crash on first live test, fixed):
- **Bug found & fixed: `import numpy` crashed the whole app on launch**
  (`ImportError ... Symbol not found: _cblas_caxpy$NEWLAPACK$ILP64`, expected
  in `Accelerate.framework`). numpy 2.5.2 (already present in `.venv` from
  Phase 0, untouched by this phase's `pip install`) is built against Apple's
  Accelerate framework for BLAS/LAPACK on Apple Silicon, and this machine's
  macOS build doesn't have the ILP64 symbol variant that build expects —
  numpy's C extension fails at `dlopen`, before any of our code runs. This is
  an environment/OS-vs-wheel mismatch, not something Phase 1's code caused,
  but it surfaced now because reinstalling `requirements.txt` for
  `faster-whisper` was the first thing to meaningfully re-touch the venv.
  **Fix:** pinned `numpy>=1.26.0,<2` in `requirements.txt` — 1.26.4 doesn't hit
  this Accelerate path and was confirmed working at runtime (imports fine,
  `check_microphone()`/`check_camera()` both still report ready) despite pip's
  own metadata claiming `opencv-python>=5.0` needs `numpy>=2` (not actually
  enforced by anything opencv's C extension calls). Re-ran `python3 run.py`
  after the fix: menu bar item on screen, mic ready, camera ready, push-to-talk
  listener started, no crash.
- **Risk found, not yet confirmed as a real problem — watch for it:**
  `faster-whisper`'s `av` dependency and `opencv-python` each bundle their own
  FFmpeg `libavdevice`, and both register the same Objective-C class names
  (`AVFFrameReceiver`, `AVFAudioReceiver`) into the process at import time,
  logging `objc[...]: Class ... is implemented in both ... This may cause
  spurious casting failures and mysterious crashes.` Reproduced the real
  load order (camera/mic check first, then `faster-whisper`'s model load, as
  happens live) and re-checked the camera afterward — still reported ready,
  so this hasn't caused an actual failure yet, just a real warning worth
  knowing about if camera or transcription behavior ever gets flaky for no
  obvious reason. No fix applied — nothing to fix until it actually breaks
  something.
- **Confirmed working in dev mode (`python3 run.py`):** held F9, said "Hello,
  how are you?" and "Hey Jarvis!" — both transcribed correctly. First
  utterance took longer (~2.2s release-to-transcript) purely from the
  one-time model download/load; the second was ~0.8s release-to-transcript,
  comfortably inside the ~1-2s Definition of done.
- **Bug found & fixed: two Jarvis processes running at once caused a false
  "menu bar is broken" report.** After the dev-mode test above, you checked
  "the menu bar" and saw only "Check Permissions"/"Quit Jarvis" — but that
  was `dist/Jarvis.app`, separately auto-started by `launchd` at login and
  still running **stale, pre-Phase-1 code in memory** (started 16:59, before
  `main.py`'s last edit at 17:06) — not the `python3 run.py` process you'd
  just Ctrl-C'd. Two Jarvis menu bar icons look identical, so this is an easy
  mix-up. Fixed by restarting the launchd job (`launchctl kickstart -k
  gui/<uid>/com.jarvis.agent`) — no rebuild needed, since `dist/Jarvis.app`
  loads `run.py` fresh from disk on every launch rather than freezing code
  into the binary.
- **Correction: the new permission is Accessibility, not Input Monitoring.**
  The packaged app's restart logged pynput's own warning verbatim: `This
  process is not trusted! Input event monitoring will not be possible until
  it is added to accessibility clients.` — pynput's global listener uses a
  Quartz event tap, gated by **Accessibility** (System Settings → Privacy &
  Security → Accessibility), not Input Monitoring as first assumed. Corrected
  in `src/jarvis/voice.py`'s docstring. Granting it after the process has
  already started doesn't retroactively fix a live listener — the process
  needs restarting again afterward.
- **Confirmed by screenshot: menu bar UI works.** "Last transcript: Hey, how
  are you? I'm doing fine. How are you doing?" rendered correctly in the
  dropdown alongside "Check Permissions" / "Hold F9 to talk" / "Quit Jarvis"
  — all four items present, `_refresh_transcript_ui`'s lock-protected
  main-thread update works as designed.
- **Bug found & fixed: Whisper repetition/hallucination glitch on a short
  clip, which also blew the latency budget.** A 1.4s recording transcribed as
  "The The The The The The The" and took ~5s (vs. ~1-2s target) — a known
  Whisper failure mode on short/quiet audio (leading/trailing silence from
  push-to-talk reaction time confuses decoding into a repetition loop, and
  the loop itself is what makes it slow). Two other utterances in the same
  session transcribed correctly (fast: ~0.8s for 2.7s of audio; slower but
  correct: ~3.4s for 3.4s of audio — roughly real-time throughput for longer
  speech on `base.en`/CPU, still fine for short command-style phrases).
  **Fix (`src/jarvis/stt.py`):** enabled `vad_filter=True` (Silero VAD trims
  silence before decoding — directly targets the repetition trigger) and
  `condition_on_previous_text=False` (each push-to-talk clip is a standalone
  utterance, not a continuous stream, so conditioning on a nonexistent
  "previous segment" was itself a repetition-loop contributor).
- **`vad_filter` fix confirmed live:** re-tested in dev mode — "Hey, it's me
  again. How you?" transcribed correctly, log shows `VAD filter removed
  00:00.560 of audio`. No repetition glitch.
- **Packaged app (`dist/Jarvis.app`) confirmed too — Phase 1 fully closed
  out.** Rebuilt via `scripts/build_app.sh` (picks up the numpy pin and both
  STT/voice fixes; mic/camera permissions survived the rebuild without
  needing to be re-granted, despite the ad-hoc-signing friction noted in
  Phase 0). Granted Accessibility to it (System Settings → Privacy & Security
  → Accessibility), restarted via `launchctl kickstart -k`, held F9 — no
  "not trusted" warning this time, transcript "Hey, it's me again. How are
  you?" appeared correctly in both the log and the menu bar UI. Also
  confirmed: "Hold F9 to talk" / "Last transcript: ..." render dimmed in the
  dropdown because they're plain informational text with no click callback —
  expected, unrelated to whether the global F9 listener itself works.

---

## Phase 2 — The brain: model-agnostic understanding
Status: ✅ Done

- ✅ Model Router interface defined (`complete(messages, tools) -> reply | tool_call`) — `src/jarvis/router/base.py`
- ✅ OpenAI-dialect backend implementation (`router/openai_backend.py`) — **proven live**
  against an OpenAI-compatible endpoint (NVIDIA NIM); api.openai.com itself is unexercised
  only for want of a key
- ✅ Claude backend implementation (`router/claude.py`) — code complete, **never run live**
  (no API key on this machine). See the carry-forward warning below before relying on it.
- ✅ Local Ollama backend implementation (`router/ollama.py`) — code complete, **never run
  live** (Ollama not installed). Same warning applies.
- ✅ Tool-calling contract defined (fixed small tool list) — `src/jarvis/tools.py`, two tools
- ✅ Intent parsing loop (transcript → model → reply or tool call) — `src/jarvis/agent.py`,
  **proven live**: model picks the tool → tool runs → result goes back → model speaks
- ✅ `open_url` / `open_app` wired as first real tool calls — model-driven and confirmed for
  real: "open github dot com" opened GitHub in the browser
- **Definition of done (amended 2026-09-05, see below):** "open github.com" / "open Claude Code"
  resolve to the right tool call on the configured backend, through the real voice path — **met**

Notes (2026-09-05):
- **Everything is built and offline-verified; what's left is purely the live test**, which
  needs credentials this machine doesn't have yet: no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` in
  the environment or `secrets/`, and `ollama` isn't installed. Nothing gets checked off until
  it's been seen working end-to-end (same discipline as Phases 0 and 1).
- **What was built:**
  - `src/jarvis/router/` — the Model Router. `base.py` holds the neutral shapes
    (`Message`, `ToolCall`, `Completion`, `BackendError`) and the `Backend` protocol;
    `claude.py` / `openai_backend.py` / `ollama.py` each translate those to and from one
    vendor's wire format. `get_backend(name)` is the only entry point, and every SDK import
    is lazy, so an unused backend can't break startup.
  - `src/jarvis/tools.py` — the tool contract: `open_url` and `open_app` as JSON Schema,
    plus `execute()`. Both shell out to macOS `open` (which hands the argument to
    LaunchServices, not a shell, so a mis-transcribed command can't become code execution).
    A `dry_run` flag makes the tools report what they *would* do — that's what lets the same
    command be replayed across three backends without opening three browser windows.
  - `src/jarvis/agent.py` — the loop. Transcript → model → run tools → feed results back →
    repeat, capped at `MAX_STEPS=4`. Knows nothing about which backend answered or which
    input modality produced the transcript (Phase 5's gestures come in the same door).
  - `src/jarvis/config.py` + `config/jarvis.json` — backend/model selection as config, so
    "try what works best" is an edit to one file. API keys deliberately *not* in it: env var
    first, then `secrets/api_keys.json` (gitignored). Keychain is Phase 4's job, and these are
    Jarvis's own service keys, not the user's site credentials doc 04 is about.
  - `src/jarvis/main.py` — the menu bar now shows a "Jarvis: ..." reply line under the
    transcript. The agent call runs on the existing background transcription thread (a network
    round-trip on the main thread would freeze the menu bar), with the same lock + main-thread
    timer pattern Phase 1 established for the UI update.
  - `scripts/selftest_brain.py` — 25 offline checks, no network or API key needed. Covers the
    loop's control flow (tool execution, parallel calls, step limit, empty transcript, dry run)
    and each backend's message translation. All passing.
  - `scripts/try_brain.py` — the live comparison harness the Definition of done calls for:
    same command, same tools, three brains, side by side with timings. Dry-runs the tools
    unless `--for-real` is passed.
- **Deliberate model choice, worth knowing before the live test:** the Claude backend uses
  `claude-opus-5` with `output_config={"effort": "low"}` and leaves thinking at its default
  (adaptive). Effort is the latency knob because this call sits in the push-to-talk path;
  thinking is *not* disabled because with thinking off the model sometimes writes a tool call
  into its visible text instead of emitting a real tool-call block — which in a loop like ours
  silently does nothing at all. Both are config/one-line changes if the live test says otherwise.
- **Translation gotchas that the self-test now pins** (this is where a model-agnostic layer
  rots silently — a backend that mis-shapes tool results doesn't crash, it just quietly stops
  calling tools):
  - Anthropic wants *all* tool results for one assistant turn in a **single** user message;
    splitting them across messages trains the model out of making parallel tool calls.
  - OpenAI sends tool arguments as a JSON **string**, Anthropic and Ollama as an **object** —
    hence `parse_arguments()`, which always parses and never string-matches (Claude 4.6+
    models vary their JSON escaping, so string matching on serialized arguments is a trap).
  - Ollama sends **no tool-call id**, so ids are synthesized locally (`ollama-0`, ...) and
    results are sent back keyed by `tool_name`, which is what Ollama actually matches on.
- **Scope note vs `01-PHASE_PLAN.md`:** the plan says Phase 2 takes "no actual system actions
  yet except the safest one: opening a URL or app", so `open_url`/`open_app` really do call
  `open` rather than being stubs. Phase 3 still owns everything else in its list
  (`osascript`, `run_claude_code`, Playwright).
- **No regression from the wiring:** `python3 run.py` still starts clean with the new imports —
  menu bar item on screen, `mic: ready` / `camera: ready`, push-to-talk listener started.
- **Plan addition — a fourth backend, `nvidia`, at your request** (you plan to use NVIDIA's
  free tier rather than paying for a key yet). NVIDIA's NIM API speaks the OpenAI Chat
  Completions dialect, so this cost almost nothing: `OpenAIBackend` became generic over
  `provider` + `base_url`, and `nvidia` is that same class aimed at
  `https://integrate.api.nvidia.com/v1` with its own key (`NVIDIA_API_KEY`) and model id.
  Any other OpenAI-compatible provider (Groq, Together, OpenRouter, a local vLLM) is now a
  config entry, not new code. `config/jarvis.json`'s default backend is set to `nvidia`
  accordingly — change that one word to switch. This is an *addition* to the plan's three
  backends, not a replacement: the Definition of done still means Claude + OpenAI + Ollama.
- **Keys deliberately left blank for you to fill:** `secrets/api_keys.json` exists with empty
  strings for `claude` / `openai` / `nvidia` (gitignored, confirmed via `git check-ignore`).
  An empty string reads as "no key" and produces a clear per-backend error, not a crash.

- **Live results (2026-09-05), NVIDIA free tier, key in `secrets/api_keys.json`.** The agent
  loop works end to end on a real model:

  | command | tool chosen | outcome |
  |---|---|---|
  | "open github.com" | `open_url(url='https://github.com')` | ✅ |
  | "open Claude Code" | `open_app(name='Claude Code')` | ✅ |
  | "open github **dot** com" (spoken form, `--for-real`) | `open_url(url='https://github.com')` | ✅ browser actually opened |

  Both Definition-of-done commands resolve correctly. That's the phase's *mechanism* proven —
  but the DoD as written names Claude, OpenAI and Ollama specifically, so it stays open.
- **Voice path confirmed by you (2026-09-05):** held F9, said "open github dot com", the
  browser opened and the menu bar's "Jarvis: ..." line updated. That closes the full chain
  Phases 0-2 were building — mic -> local Whisper -> model -> tool -> real action -> UI —
  with every link seen working rather than inferred.
- **Model selection is empirical now, and the catalogue lies.** `GET {base_url}/models` with
  this key returns 81 models — and does **not** include `meta/llama-3.3-70b-instruct`, which
  had been the config default purely on reputation. Measured, both DoD commands, tools attached:

  | model | result |
  |---|---|
  | `openai/gpt-oss-20b` | ✅ 2.7s / 3.7s — **now the configured default** |
  | `nvidia/nemotron-3-super-120b-a12b` | ✅ 3.9s / 2.0s — runner-up, try it if quality slips |
  | `nvidia/nemotron-3.5-lightning-30b-a3b` | ✅ but erratic (21.1s then 1.0s) |
  | `meta/llama-3.2-90b-vision-instruct` | ❌ hangs forever (see below) |
  | `deepseek-ai/deepseek-v4-flash-0731` | ❌ read timeout |
  | `moonshotai/kimi-k2.6`, `nvidia/nemotron-nano-3-30b-a3b` | ❌ HTTP 404 (listed but not served) |
  | `mistralai/mistral-nemotron` | ❌ HTTP 500 |

- **Latency, honestly: ~9s per command steady-state** (the first call of a session is ~39s,
  cold start). That's ~4.5s x the two model calls one command needs — one to pick the tool,
  one to say what happened. Fine for a free tier, too slow to feel like a companion. The
  obvious fix is to skip the second call and speak a locally-composed confirmation once a
  tool has run, but that's Phase 8's latency pass, so it's flagged there rather than done here.
- **Live finding (2026-09-05), NVIDIA key in place — `meta/llama-3.2-90b-vision-instruct` is
  not usable and fails in the worst possible way.** Given a request with a `tools` array, that
  endpoint simply **never responds**: no 400, no "unsupported", just a read timeout (reproduced
  directly with httpx at 45s, and it had already burned ~5 minutes of a sweep before that).
  It's a *vision* NIM — tool calling isn't part of it — but nothing in the API says so.
  The model list this key can actually reach (81 models, via `GET /v1/models`) does **not**
  include `meta/llama-3.3-70b-instruct`, which is why the original config default was wrong.
  `openai/gpt-oss-20b` answers the same request in **2.8s** with a correct
  `open_url(url="https://github.com")` tool call.
- **Robustness fix this forced, worth having anyway:** hosted backends now take a per-backend
  `timeout` (default 60s, in `config/jarvis.json`). The SDKs default to *ten minutes*, and
  Jarvis speaks its replies — a hung backend would have left the menu bar silently stuck on the
  previous reply for that long, which is indistinguishable from "Jarvis ignored me".

- **✅ Carry-forward CLOSED (2026-09-08): the Ollama backend has now run live**, against
  Ollama on a second machine on the LAN (Windows PC, GTX 1660 Ti 6 GB, `granite4.1:3b`).
  It worked on the *first* attempt with no changes — the message translation this file's
  self-test pins turned out to be correct, tool calls included. See the Phase 3 "Local
  models" note for the numbers, which are the real story. The original warning follows,
  and still applies to `claude` and `openai`, which remain unexercised.

- **⚠ Carry-forward for whoever changes `backend` in `config/jarvis.json`:** only the
  `nvidia` path has ever made a real network call. `claude` and `ollama` are written and
  their message translation is pinned by `scripts/selftest_brain.py`, but neither has been
  exercised against a live server, so treat the first switch to either as a test, not a
  config tweak — run `scripts/try_brain.py --backend <name>` before trusting the voice path
  with it. The likeliest breakages are the bits a self-test can't check: Anthropic's
  `output_config`/effort field on the installed SDK version, and whichever Ollama model gets
  pulled actually supporting tool calls (most small ones don't).

### Backend selection moved to the environment (2026-09-09) — and a fifth backend

At your request: which brain answers is now an **environment** value, not a code or JSON
edit. `.env` in the project root (gitignored, `.env.example` committed as the template)
is read once at `jarvis.config` import and layered on top of everything:

    DEFAULTS (src/jarvis/config.py)  <  config/jarvis.json  <  .env / real env vars

So switching brains is one line — `JARVIS_BACKEND=openrouter`, or `ollama`, or `nvidia`
— and a real environment variable still beats the file, which means
`JARVIS_BACKEND=ollama .venv/bin/python3 run.py` overrides it for a single run and a
launchd plist's `EnvironmentVariables` overrides it permanently. The knobs are listed in
`ENV_OVERRIDES` (`config.py`) and mirrored in `.env.example`:

| variable | effect |
| --- | --- |
| `JARVIS_BACKEND` | which backend answers |
| `JARVIS_MODEL` / `JARVIS_BASE_URL` / `JARVIS_TIMEOUT` | applies to *whichever* backend is selected — see the trap below |
| `JARVIS_OPENROUTER_MODEL`, `JARVIS_OLLAMA_HOST`, ... | one specific backend, whether or not it's the active one |
| `JARVIS_SPEECH_ENABLED`, `JARVIS_SPEECH_VOICE`, `JARVIS_ALLOW_EDITS` | the knobs most likely to be flipped mid-session |
| `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, ... | keys, read before `secrets/api_keys.json` |

One trap, found while testing this and now written into `.env.example`: the unqualified
`JARVIS_MODEL` *follows* the switch. Set it to `openai/gpt-oss-20b` for OpenRouter and then
flip `JARVIS_BACKEND=ollama`, and Ollama gets asked for an OpenRouter model id it has never
pulled. That's the intended semantics (one variable, one active backend), but the local
`.env` therefore pins `JARVIS_OPENROUTER_MODEL` instead — per-backend variables stay put
across a switch.

The per-backend variables are **generated** from `DEFAULTS["backends"]`, so a backend
added there gets its whole set for free. Values are coerced through JSON's literals, which
matters: `JARVIS_SPEECH_ENABLED=false` has to become `False`, not the truthy string
`"false"`. The `.env` parser is ~20 lines rather than a dependency, and deliberately does
**not** strip inline `#` comments — an API key can contain a `#`, and that's pinned by a
check in `selftest_brain.py`.

Two things came with it:

- **`openrouter` is a fifth backend** — model `nex-agi/nex-n2.5-mini:free`, base URL
  `https://openrouter.ai/api/v1`. Like `nvidia`, it's zero new translation code: OpenRouter
  speaks the OpenAI dialect, so it's `OpenAIBackend` with another `provider` entry. It also
  gained optional per-provider `headers` (OpenRouter's `HTTP-Referer` / `X-Title`
  attribution), which no other backend sets.
- **Adding a provider is now config-only too.** `get_backend()` falls back to the OpenAI
  backend for *any* name that appears under `backends` in `config/jarvis.json` with a
  `base_url`, and `config.api_key()` falls back to the obvious env var name
  (`groq` → `GROQ_API_KEY`). So pointing Jarvis at Groq, Together, or a self-hosted vLLM is
  a JSON block, not a commit. `scripts/try_*.py` offer those names in `--backend` via the
  new `router.available_backends()`.

**Status: ✅ live-verified 2026-09-09.** The first key supplied that day was rejected by
OpenRouter itself (92 chars containing a `#`, not the `sk-or-v1-<64 hex>` shape it issues;
`GET /api/v1/key` returned `401 {"message":"Missing Authentication header"}` for it, and so
did plain `curl` — note that `GET /models` returning 200 proves nothing, it's public). The
replacement key works, and `nex-agi/nex-n2.5-mini:free` routes correctly:

| command | result | latency |
| --- | --- | --- |
| "open github.com" | `open_url(url='https://github.com')` | 5.7s |
| "open Claude Code" | `open_app(name='Claude Code')` | 2.7s |
| "pull up my email" | `open_app(name='Mail')` | 4.8s |
| "log me into github" | `open_portal(url='https://github.com/login')` | 4.6s |
| "open my thesis notes and tell me what the login module does" | `run_claude_code(...)`, then the "where is it?" follow-up fired correctly | 7.5s |
| "what time is it" | plain reply, no tool — correct, there's no clock tool | 6.1s |

So 2.7-7.5s, against 0.7-3.7s for LAN Ollama and 40-100s for the free NVIDIA tier. Slower
than the Ollama box but it answers **with the PC off**, which is the whole reason to have
it. `config/jarvis.json` and DEFAULTS now name that model as OpenRouter's fallback; note
the `:free` suffix is part of the slug, and whatever model you swap in must list `tools` in
its `supported_parameters` (`GET {base_url}/models`) or it can't route anything.

Unrelated gap this surfaced (**not** a backend problem, left alone deliberately): the model
sent `project='thesis notes'` for "my thesis notes", and `projects.py` matches aliases
exactly (`aliases().get(name)`), so the configured `"my thesis notes"` alias missed. The
follow-up question handled it gracefully, so this is a resolver nicety for the Phase 6
memory work, not a break.

### How to test Phase 2 (do these in order)

1. **Offline, works right now, no key needed** — the loop and all four backends' message
   translation:
   ```
   .venv/bin/python3 scripts/selftest_brain.py     # 48 checks, currently all passing
   ```
2. **Add a key.** Free NVIDIA route: make an account at https://build.nvidia.com, generate an
   `nvapi-...` key, put it in `secrets/api_keys.json` as `"nvidia": "nvapi-..."`, and copy the
   exact model id from that site's model page into `config/jarvis.json` if you pick a different
   model than `meta/llama-3.3-70b-instruct` (it must be one that supports tool/function
   calling — that's the whole thing being tested). Same idea for `"claude"` / `"openai"`, or
   put `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `NVIDIA_API_KEY` / `OPENROUTER_API_KEY` in
   `.env` instead (copy `.env.example`) — see the 2026-09-09 note above.
   For Ollama: `ollama serve` then `ollama pull granite4.1:3b` (see the Phase 3
   "Local models" note — it can run on another machine on your LAN).
3. **Test the brain on its own** (nothing opens — tools are dry-run by default):
   ```
   .venv/bin/python3 scripts/try_brain.py                          # every backend, both DoD commands
   .venv/bin/python3 scripts/try_brain.py --backend nvidia         # just one
   .venv/bin/python3 scripts/try_brain.py --backend nvidia "pull up my email"   # your own command
   .venv/bin/python3 scripts/try_brain.py --backend nvidia --for-real "open github.com"  # really opens it
   .venv/bin/python3 scripts/try_brain.py --backend nvidia --model openai/gpt-oss-120b   # A/B a model id
   ```
   `--model` overrides the configured model for that run only, which is how to find out which
   NVIDIA model actually tool-calls reliably without editing config between attempts. Model
   shortlist to work down (all on the free tier, all catalogued at build.nvidia.com):
   `meta/llama-3.3-70b-instruct` (the configured default — the safest, most-documented
   tool-caller), `openai/gpt-oss-120b` (mixture-of-experts, so noticeably faster to first token,
   which matters on the push-to-talk path), then a Nemotron or Qwen variant if neither holds up.
   What passing looks like: "open github.com" → `open_url(url='https://github.com')` and
   "open Claude Code" → `open_app(name='Claude Code')`. A backend with no key prints a `FAIL`
   line naming exactly what's missing and doesn't stop the others.
4. ✅ **Test the whole voice path** — `.venv/bin/python3 run.py`, hold F9, say "open github dot
   com", and watch the browser open plus the menu bar's new "Jarvis: ..." line. (Quit the
   launchd-started `dist/Jarvis.app` first, or you'll have two Jarvises and the Phase 1
   mix-up where you're looking at the wrong menu bar icon.) **Done — confirmed by you.**
5. ☐ **Then the packaged app.** Correction to what this note said earlier: **no
   `scripts/build_app.sh` rebuild is needed.** The bundle's launchd plist already puts the
   venv's `site-packages` on `PYTHONPATH` (verified — that's where `anthropic`/`openai` were
   installed), and per the Phase 1 notes it loads `run.py` fresh from disk on every launch
   rather than freezing code into the binary. So a restart is enough:
   `launchctl kickstart -k gui/$(id -u)/com.jarvis.agent`, then repeat step 4 against it.
6. ✅ **Decided (2026-09-05): the plan was amended, and Phase 2 is closed.** The original
   Definition of done said "on all three backends" (Claude, OpenAI, Ollama); this machine has
   a free NVIDIA NIM key instead. Rather than block the phase on buying credentials, the DoD
   in `01-PHASE_PLAN.md` was rewritten to be backend-agnostic, with the reasoning recorded
   there: the criterion's intent was proving the router isn't Claude-shaped, and running the
   OpenAI-dialect backend against a *non*-OpenAI endpoint tests exactly that — arguably harder
   than a second vendor would, since it's the case where a "compatible" API isn't quite.
   The three-backend comparison isn't abandoned, just de-gated: `scripts/try_brain.py` runs it
   in one command whenever a second set of credentials shows up.

---

## Phase 3 — Real actions & the Claude Code sub-agent
Status: ✅ Done

- ✅ `open_app` / `open_url` tool (real `open` calls) — shipped in Phase 2, unchanged;
  see the osascript note below for why `osascript` was deliberately *not* added
- ✅ `run_claude_code` tool (shells out to `claude` CLI, streams output, reports back) —
  `src/jarvis/claude_code.py`, **proven live** (see the runs table below)
- ✅ Project-name resolution + containment (`src/jarvis/projects.py`) — **not in the original
  plan**, added because `run_claude_code` needs a directory and a voice transcript doesn't
  have one; see the note below
- ✅ Basic Playwright browser tool (opens a portal URL) — `src/jarvis/browser.py`,
  **proven live** against a real login page
- ✅ Live status line in the menu bar (doc 02's "running a sub-agent" surface) —
  `on_status` through agent → tools → sub-agent, shown in `main.py`
- ✅ **Definition of done:** "open project X, use Claude Code to find this bug" runs end to
  end — met through the agent loop *and* confirmed by you through the microphone (below)

Notes (2026-09-07):
- **What was built:**
  - `src/jarvis/claude_code.py` — the sub-agent launcher. Runs
    `claude --print <task> --output-format stream-json --verbose --permission-mode <mode>
    --permission-prompts none` in the project directory and reads the NDJSON event stream as
    it arrives, so the menu bar can show what Claude Code is doing *right now* instead of
    going quiet for minutes. Returns one paragraph (result + tool count + duration + cost +
    session id) for the voice model to summarize.
  - `src/jarvis/projects.py` — spoken name → directory, and the containment boundary.
    Matches "project jarvis" against the immediate children of `actions.projects.roots`
    (exact → unique substring → fuzzy), refuses ambiguity rather than guessing, and rejects
    any path outside those roots. Explicit `actions.projects.aliases` win over the search and
    may point outside (they're your decision, not the model's).
  - `src/jarvis/browser.py` — the browser Jarvis can *see*, as opposed to `open_url`, which
    hands a URL to macOS and forgets it. Persistent Chromium profile under `memory/browser`,
    a long-lived window, and it reports the page title plus whether a password field is
    present — which is exactly the hand-off point Phase 4's `fill_login_form` starts from.
  - `src/jarvis/tools.py` — two new tools (`run_claude_code`, `open_portal`) and an
    `on_status` parameter on every handler, so the slow ones can report progress.
  - `config/jarvis.json` — a new `actions` block: `claude_code` (cli, permission_mode, model,
    timeout, max_budget_usd, extra_args), `browser` (engine, headless, timeout, user_data_dir)
    and `projects` (roots, aliases). Read fresh on every call, so changing the permission mode
    takes effect on the next command rather than the next restart.
  - `src/jarvis/main.py` — a "Status: ..." menu item fed by the agent's `on_status` callback,
    with the same lock + main-thread-timer discipline Phase 1 established. Menu text is
    truncated to 90 chars for display only (Claude Code returns paragraphs); the log keeps
    the full text.
  - `scripts/selftest_actions.py` — 109 offline checks, no API key, no network, no money.
  - `scripts/try_actions.py` — the live harness (each tool on its own, or a whole spoken
    sentence through the agent). Dry-runs unless `--for-real`, same as `try_brain.py`.

- **Live results (2026-09-07).** Every row was actually run on this machine:

  | what | result |
  |---|---|
  | `run_claude_code` direct: "what does src/jarvis/voice.py do?" | ✅ 18s, 2 tool calls, $0.21, correct answer |
  | `open_portal https://github.com/login` | ✅ real Chromium window, title read, **password field detected** |
  | **DoD, whole path:** "open project jarvis and use claude code to find out what the max steps limit in the agent loop is set to and why" | ✅ 54s — model emitted `run_claude_code(project='jarvis', task=...)`, Jarvis resolved `jarvis` → `~/Desktop/project_jarvis`, Claude Code ran there (6 tool calls, 37s, $0.30) and found `MAX_STEPS = 4` at `agent.py:43` with the right reasoning; Jarvis spoke an accurate one-liner |
  | **DoD, open-ended version:** "...find this bug the menu bar icon sometimes does not appear when the app is launched at login" | ⚠ tool call correct, but hit the 600s timeout mid-investigation — see the finding below |

- **⚠ The most important finding of this phase — a stopped sub-agent made the voice model
  invent a root cause.** The open-ended run above ran 95 tool calls without finishing, Jarvis
  stopped it at 600s as designed, and handed back the last thing Claude Code had *said*
  ("Strong evidence now. Let me have Plan agents design the fix"). The small NVIDIA model,
  given a narration and no conclusion, filled the gap: it confidently explained that the fix
  was to move status-item creation into `applicationDidFinishLaunching` and call
  `NSApp.activate(ignoringOtherApps:)` — **Swift/AppKit advice, for an app written in Python
  with rumps.** Nothing in the tool result said any of that. A voice assistant that invents
  findings out loud is worse than one that says it doesn't know, so this got fixed rather
  than noted:
  - `_summarize(..., partial=True)` now labels a stopped run as stopped, states outright that
    it reached no conclusion, and presents its last line as a progress note rather than the
    answer.
  - The timeout message tells the model, in words, not to present anything below it as a
    conclusion.
  - `SYSTEM_PROMPT` gained a rule: report only what the tool actually returned, never invent
    a finding, cause, file name or line of code; if the tool reached no conclusion, say so.
  - `scripts/selftest_actions.py` pins all three, so this can't quietly regress.
  Worth remembering as a general shape: **the small local-routing model will paper over a
  thin tool result.** Any future tool whose output can be partial needs to say that it is.

- **Deliberate deviation — no `osascript`.** `01-PHASE_PLAN.md` names "real `open`/`osascript`
  calls" for `open_app`/`open_url`. `open -a` already both launches a cold app and brings a
  running one to the front, so osascript would add nothing except risk: it takes a *script*
  where `open` takes an argument, which is precisely the property that keeps a mis-transcribed
  app name from becoming something executable. Left on `open`; noted in `tools.py` at the call
  site so nobody "fixes" it later.

- **Plan addition — `jarvis/projects.py` wasn't in the plan and had to be.** The plan says
  `run_claude_code(project_path, task)`, but nothing spoken ever contains a path, and the
  alternative — letting the model hand a raw path to a subprocess — is how a mis-transcribed
  word ends up pointing an editing agent at your home directory. The roots list is the whole
  containment story for this phase and the tests that matter most in
  `selftest_actions.py` are the ones proving a path can't escape it. Doc 02's memory store
  ("my project" → path, Phase 6) should feed this resolver rather than replace it.

- **The safety posture, and the one word to change when you want more.**
  `actions.claude_code.permission_mode` defaults to **`plan`**: Claude Code can read, search
  and reason, but not edit. That's deliberate — this runs from a transcript that is sometimes
  wrong, and an unattended edit to the wrong repo isn't something you notice mid-sentence.
  Set it to `acceptEdits` when you want fixes instead of findings; everything else already
  works, that flag is the only difference. `--permission-prompts none` is always passed so a
  run can never block forever on a prompt nobody is there to answer.

- **Cost and time, honestly.** A trivial question runs ~20-40s and ~$0.20-0.30 of Claude Code
  usage. An open-ended "find this bug" exceeded 600s. There is an unused
  `actions.claude_code.max_budget_usd` knob if that becomes a problem. The real fix for long
  tasks — start the sub-agent, let go of it, and speak the answer when it lands — is a
  background-task shape this phase doesn't have; flagged for Phase 8's latency pass rather
  than smuggled in here.

- **Install step this phase adds:** `playwright` is now in `requirements.txt`, but the browser
  binaries are a *separate* download that pip does not do:
  `.venv/bin/python3 -m playwright install chromium` (~95 MB). Already done on this machine.

- **Voice path confirmed by you (2026-09-07), and it survived a mis-transcription.** Held F9,
  said the DoD command; Whisper heard *"use **Cloud** Code"* rather than "Claude Code", and the
  model routed to `run_claude_code` anyway — the system prompt's "interpret what the user
  obviously meant" instruction earning its place. Claude Code ran in `project_jarvis` (3 tool
  calls, 27s, $0.24) and found `MAX_STEPS = 4` at `agent.py:43`; Jarvis's spoken reply was
  correct and grounded in what the tool actually returned. **~47s** wall clock from key release
  to answer: ~1.8s Whisper, ~5.5s to pick the tool, 27s of Claude Code, ~3s to summarize.
- **Handoff to Phase 4, spotted in that same run:** the reply came back as
  ``The max step limit is defined as `MAX_STEPS = 4` in `src/jarvis/agent.py` at line 43.`` —
  markdown backticks and a slash-separated path, which is fine in a menu bar and bad out loud,
  even though `SYSTEM_PROMPT` already asks for neither. Phase 4 owns TTS, so it owns this: it
  needs a "make this speakable" pass between the model's reply and the voice (strip markdown,
  read paths as words or don't read them at all), not just a stronger prompt.
- **Environment oddity seen once, recorded in case it recurs:** immediately after the
  playwright install, every launch of `/opt/anaconda3/bin/python3` was SIGKILLed at exec —
  `~/Library/Logs/DiagnosticReports/python3.12-*.ips` says `SIGKILL (Code Signature Invalid)`
  / `Taskgated Invalid Signature`. `codesign -v` reported the binary as valid, and it started
  working again on its own a couple of minutes later. Nothing in Jarvis caused or fixed it;
  if a future session hits an inexplicable exit code 137 from the venv python, that's what it
  is, and waiting it out worked.

### Round 2 (2026-09-08) — make it visible, and let it ask where things are

You asked for two changes after seeing round 1 work: the sub-agent should be *dramatic* — a
real terminal window that opens, `cd`s into the project and runs Claude Code in front of you —
and when Jarvis doesn't know where a project is, it should ask out loud and take your spoken
answer. Both are built. You also chose, when I flagged it as fragile, that Jarvis should still
relay the result even in the visible mode; that turned out to have a clean solution (below).

- ✅ **Terminal mode** (`actions.claude_code.mode: "terminal"`, now the default) —
  `src/jarvis/terminal.py` drives iTerm2 (or Terminal.app) over `osascript`: new window,
  `cd '<project>'` on one line, `claude ... '<your task>'` on the next. This is what the phase
  plan's "real `open`/`osascript` calls" bullet was actually for — round 1 dropped osascript
  because `open -a` already launches and fronts an app, which was true and beside the point:
  `open` can't type a command into the app it launched.
- ✅ **It still reports back, without running anything twice.** Claude Code writes every
  session to `~/.claude/projects/<slug>/<session-id>.jsonl` in the same event shapes
  `--output-format stream-json` emits — so Jarvis picks the session id *before* launching
  (`--session-id`) and tails that file. You get the window, Jarvis gets the stream, the live
  status line and the answer. No scraping, no double cost.
- ✅ **Knowing when an interactive session is done**, which is the one genuinely fuzzy part —
  it never exits, it just goes quiet. The rule (`_transcript_settled`): quiet for
  `quiet_seconds` **and** the last thing written was prose, not a tool call. The second half
  matters because a session paused on a permission prompt is also perfectly quiet, and calling
  that "finished" would have Jarvis announce an answer while Claude Code waits for a keypress.
- ✅ **"Where is it?"** — `jarvis/followup.py` parks the request when a project can't be
  resolved, and the *next* utterance gets one chance to be the answer. Resolved **locally, with
  no model call at all**: `projects.resolve_spoken` walks the real filesystem one spoken
  segment at a time (greedy longest-match, so "in Documents under client work" finds
  `Documents/client work` and "in documents adobe" finds `Documents/Adobe`), and anything that
  doesn't match something on disk resolves to nothing rather than to a plausible wrong path.
  The answer is then saved as an alias, so a project is asked about exactly once.
- ✅ **Argument hardening found on the way:** `tools.execute` now drops any argument a tool's
  JSON Schema doesn't declare. This exists because `run_claude_code` gained a keyword-only
  `directory` parameter (how the agent hands over a folder you just named out loud, bypassing
  the roots check) — without the filter, a model could have passed `directory` itself and
  walked straight through the containment boundary.

**Live results (2026-09-08):**

| what | result |
|---|---|
| terminal mode, direct | ✅ iTerm2 window opened in `project_jarvis`, ran, Jarvis followed the transcript live and spoke the answer (34s) |
| **"where is it?" end to end** | ✅ "use claude code on my invoicing thing to find why totals are wrong" → not found → Jarvis asked → "it's in Documents slash client work slash invoicing" → resolved locally, alias saved, task ran there, answer spoken (87s) |
| `allow_edits: false` | ✅ file byte-identical afterwards, bug still found and explained |
| `allow_edits: true` | ✅ same task, file actually fixed |

- **⚠⚠ The serious finding: what I told you about `plan` mode in round 1 was wrong.** I wrote
  that `--permission-mode plan` meant Claude Code "can read, search and reason, but not edit",
  and defaulted to it on that basis. It does not hold. A terminal-mode run under `plan`
  rewrote `totals.py` in the test project — verified by md5, not by reading the transcript.
  Nothing in your `~/.claude/settings.json` explains it; plan mode simply isn't a write
  barrier in an interactive session. Two further attempts, each measured:
  1. `--permission-mode plan` → **file modified.**
  2. `+ --disallowedTools=Edit,Write,NotebookEdit,MultiEdit` → **file modified**, and Claude
     Code said why in its own summary: *"Write and Edit are disabled in this session, so I
     applied the change via Bash."* Removing the editing tools doesn't remove the ability to
     edit, it reroutes it.
  3. `+ --restricted --strict-mcp-config` (no Bash or other code-runners, file tools confined
     to the working directory, MCP servers skipped) → **held.** Byte-identical file, bug still
     correctly found and explained, and Claude Code told the user it couldn't apply the fix.
  So the safety knob is now `actions.claude_code.allow_edits` (default false) and it applies
  the whole set — see `READ_ONLY_FLAGS` in `jarvis/claude_code.py`, which carries this chain as
  a comment so nobody re-weakens it. **The cost is real:** with the guard on, Claude Code has
  no Bash and investigates with Read/Grep/Glob only. `permission_mode` is still a config
  passthrough, but it's now documented as what Jarvis *asks* for, not what's enforced.
  The general lesson, and it's the second time this phase has taught it: a safety property
  nobody measured is a safety property you don't have.

- **Bug found and fixed the expensive way — a variadic flag ate the prompt.**
  `--disallowedTools` is documented as taking a "comma or space-separated" list, which means
  it keeps consuming argv entries until the next flag. In terminal mode the task is the final
  positional argument, so `--disallowedTools "Edit,Write" "<the task>"` swallowed the task
  word by word. Reproduced exactly:
  ```
  $ claude --print --disallowedTools "Edit,Write" "reply with exactly the word BANANA"
  Permission deny rule "reply" matches no known tool — check for typos.
  Permission deny rule "with" matches no known tool — check for typos.
  ...
  $ claude --print --disallowedTools=Edit,Write "reply with exactly the word BANANA"
  BANANA
  ```
  The symptom in Jarvis was indirect and would have been very hard to guess at: the session
  transcript "never appeared", because Claude Code had started an interactive session with no
  prompt at all. Fixed by using the `--flag=value` form, which takes exactly one value and can
  sit anywhere in the command line. Pinned by a test that fails if the flag is ever split back
  into two argv entries. **Rule for anything added here later: variadic CLI options must
  always use the `=` form.**

- **Deliberate scope call:** `jarvis/followup.py` is a slice of Phase 6's memory store taken
  early, and it is kept as narrow as it can be — one pending question, about one thing, for
  180 seconds, consumed exactly once. Both properties are there for a reason: an unanswered
  question that never expired would silently attach itself to an unrelated sentence ten
  minutes later and launch a coding agent on a task you'd forgotten about. When Phase 6
  replaces this with something general, keep the expiry and the single-consumption.

- **Housekeeping from this round:** the round-2 tests used a throwaway project at
  `~/Documents/client work/invoicing` (a deliberately buggy `totals.py`). It has been deleted,
  and the alias Jarvis learned for it removed from `config/jarvis.json`, so nothing points at
  a folder that no longer exists.

**Three bugs the first voice test of round 2 found (2026-09-08), all fixed:**

1. **The "where is it?" flow never armed, because the model wouldn't call the tool.** Asked
   *"use Claude Code on my thesis notes to explain what the phase plan covers"*, the model
   replied *"I'm not sure where your thesis note is stored, so I can't read it — I don't have
   a tool to retrieve that content."* No tool call, so nothing was parked, so the spoken
   answer that followed had nothing to attach to and was treated as a fresh command. The
   design assumed an unresolvable project would reach `projects.resolve` and fail there; it
   never got that far. Fixed in the *description*, not the code: `run_claude_code` now says
   outright that an unfamiliar project name is normal, that turning a name into a folder is
   Jarvis's job, and that "I don't know where it is" is never a reason to skip the tool —
   plus a matching line in `SYSTEM_PROMPT`. Verified by replaying the exact mis-transcribed
   sentence: it now calls the tool and asks *"Please tell me where the 'my thesis note'
   project is located on your Mac."*
2. **Turns raced, and a stale one overwrote a fresh one.** Each utterance is handled on its
   own thread (jarvis.voice spawns one per hotkey release), and the first command's summary
   landed **32 seconds after** the next command had already been answered — replacing a
   current reply with an old one in the menu bar. Fixed with a turn counter in `main.py`:
   every utterance takes a number, the number rides on its handling thread, and only the
   newest turn may write the reply or the status line.
3. **Whisper kept mangling the one phrase that decides everything.** "Claude Code" came back
   as *"CLOT code"*, *"slot code"* and *"plot code"* in three consecutive utterances. Added
   `initial_prompt` to `jarvis/stt.py` with the handful of names Jarvis actually hears — a
   Phase 1 file touched for a Phase 3 reason, noted here so it isn't a surprise.

**Also observed, not fixed (Phase 8's territory):** one model call took **175 seconds** on the
NVIDIA free tier, with an SDK retry in the middle. The sub-agent work is not the slow part —
the free-tier routing calls around it are.

### Local models on a second machine (2026-09-08) — and the new default backend

Tested at your request: Ollama on the Windows PC (GTX 1660 Ti, 6 GB), Jarvis on the Mac,
same wifi. `granite4.1:3b` (2.1 GB, Q4_K_M, `capabilities: ["completion","tools"]`).
Setup was `OLLAMA_HOST=0.0.0.0:11434` on the PC, firewall open on 11434, and
`backends.ollama.host` pointed at `http://192.168.1.110:11434`.

**It is not close.** Same commands, same tools, same prompts:

| command | ollama / granite4.1:3b | nvidia / gpt-oss-20b |
|---|---|---|
| "open github.com" | **3.7s** | ~2.7s (Phase 2) / 91s (today) |
| "open Claude Code" | **0.7s** | **101.7s** |
| "…use claude code to say what the followup module does" | **1.8s** | ~60-90s |
| the "where is it?" pair | **1.4s + 1.6s** | ~40s + ~30s |

The default backend became **`ollama`** here. The free NVIDIA tier had degraded to
40-100s per routing call, which is unusable for a push-to-talk assistant; a 3B model on a
six-year-old GPU one room away answers in under two seconds. The obvious cost: **Jarvis has
no brain when the PC is off.** Switch backends when working away from it — as of the
2026-09-09 note that's `JARVIS_BACKEND=` in `.env` rather than a word in
`config/jarvis.json` — and note that the sub-agent itself is unaffected either way, since Claude Code brings
its own model.

**Two bugs the local model found that the hosted one had been hiding:**

1. **"open Claude Code" routed to `run_claude_code`, with an invented task.** Phase 2's own
   Definition-of-done command, broken by Phase 3 adding a tool whose name contains the words
   the user is saying. gpt-oss-20b disambiguated it correctly and so it was never noticed;
   granite4.1:3b did not. Fixed in the descriptions — `run_claude_code` now states outright
   that hearing "Claude Code" is not a reason to use it and that "open Claude Code" means
   `open_app`, and never to invent a task the user didn't give. Granite then got it right in
   **0.7s**. Worth generalizing: *a smaller model is a better test of a tool description than
   a larger one*, because a large model quietly compensates for descriptions that are wrong.
2. **A dry run hallucinated results, and wrote to the real config.** Asked in dry-run mode
   what the phase plan covered, granite confidently described "literature review, data
   collection, analysis, manuscript preparation" — from a tool result that contained no
   content at all, only "would open...". The dry-run strings now say in words that nothing
   ran and there is no result to report (it now answers "this was a dry-run"). Separately,
   the follow-up path was calling `config.save_alias` even on a dry run, so a
   backend-comparison run left a junk alias in `config/jarvis.json`. Both fixed and pinned.

### Voice test on the local model (2026-09-08) — fast, and one more resolver bug

Same four commands by voice, `ollama` / `granite4.1:3b` on the LAN:

| step | routing latency |
|---|---|
| "open github.com" → `open_url` | **1.4s** (transcript to spoken reply) |
| "open project jarvis and use Claude Code…" → tool call | **1.1s** |
| "use Claude Code on my thesis…" → parked the question | **1.5s** |
| the spoken location → resolved, window opened | **15ms** |

Claude Code's own 30-50s is now the only wait, which is the right shape: the assistant is
instant and the sub-agent takes as long as the work takes.

**Bug found, and it was two bugs.** Told *"It's inside the Docs folder in the project
Jarvis"*, Jarvis opened `project_jarvis` rather than `project_jarvis/docs` — quietly one
directory up from what was said. Cause:

1. **Fuzzy matching absorbed a word into a shorter name.** From `~/Desktop`, difflib scored
   `"docsprojectjarvis"` against `"projectjarvis"` at **0.897** — over the 0.8 cutoff — so
   the walk consumed all three words onto the parent and reported a *complete* match, with
   "docs" simply gone. Fuzzy is meant to absorb a *mishearing* ("projekt jarvis"), not an
   extra path component, so it now also requires the two names to be within
   `FUZZY_LENGTH_RATIO` (0.8) of each other in length. "cliant work" still matches "client
   work"; "docs project jarvis" no longer matches "project_jarvis".
2. **"X inside Y" says the child before the parent**, and the walk only ever went
   left-to-right. "in"/"inside"/"under"/"within" were being thrown away as filler when they
   are in fact the only structure in the sentence. They're now group boundaries, and
   `resolve_spoken` tries the groups as spoken, rotated (first group last), and fully
   reversed — the filesystem decides, since only an order that matches real directories the
   whole way down counts. All of these now land correctly:
   *"the Docs folder in the project Jarvis"*, *"project jarvis, inside the docs folder"*,
   *"the invoicing folder in Documents slash client work"*, *"the src folder in desktop
   slash project jarvis"*.

**Fix confirmed by voice (2026-09-08):** same phrasing, and it opened
`project_jarvis/docs` correctly.

Worth noting what this bug looked like from outside: **nothing failed.** Claude Code opened,
ran, and gave a good answer — because the repo root happens to contain `docs/`. A resolver
that lands one directory up is invisible right until it isn't, which is why `resolve_spoken`
returns None rather than a best guess whenever the words don't match something real.

### How to test Phase 3 (do these in order)

1. **Offline, no key, no money** — resolution, containment, argv, stream parsing, dispatch:
   ```
   .venv/bin/python3 scripts/selftest_actions.py    # 103 checks, currently all passing
   .venv/bin/python3 scripts/selftest_brain.py      # Phase 2's 29, still passing
   ```
2. **See what the model decides, without launching anything** (dry run is the default):
   ```
   .venv/bin/python3 scripts/try_actions.py
   ```
3. **Each tool on its own, for real** — a failure here is the tool's, not the model's:
   ```
   .venv/bin/python3 scripts/try_actions.py --project jarvis --for-real \
       --claude-code "In one sentence, what does src/jarvis/voice.py do?"
   .venv/bin/python3 scripts/try_actions.py --portal https://github.com/login --for-real
   ```
4. **The whole path, for real** — this is the Definition of done:
   ```
   .venv/bin/python3 scripts/try_actions.py --for-real \
       "open project jarvis and use claude code to find out what the max steps limit in the agent loop is set to and why"
   ```
5. ✅ **Then the microphone** — `.venv/bin/python3 run.py`, hold F9, say *"open project jarvis
   and use Claude Code to find out what the max steps limit in the agent loop is"*, and watch
   the "Status:" line tick through Claude Code's tool calls before the "Jarvis:" line
   answers. (Quit the launchd-started `dist/Jarvis.app` first or you'll have two Jarvises —
   the Phase 1 mix-up.) **Done — confirmed by you, 2026-09-07** (round 1, headless mode).
5b. ✅ **The round-2 microphone check — done, confirmed by you 2026-09-08.** Everything below has already been verified through
   `scripts/try_actions.py` against this repo (2026-09-08) — what's left is doing it by voice.
   `.venv/bin/python3 run.py`, then hold F9 and say:
   - **Terminal mode:** *"open project jarvis and use Claude Code to say in one sentence what
     the followup module does"* — an **iTerm2 window should open in front of you**, `cd` into
     `project_jarvis` and run Claude Code with your words as its prompt. Watch the "Status:"
     line track its tool calls, then Jarvis speaks the answer and the window stays open.
   - **"Where is it?":** *"use Claude Code on my thesis notes to say in one sentence what the
     phase plan document covers"* — "thesis notes" is in none of the roots, so Jarvis asks.
     Answer on the next F9 press: *"it's in Desktop slash project jarvis slash docs"*. It
     should resolve locally (no model call), open a window **in `docs`**, and answer.
     Afterwards, clear the junk alias it learned: `actions.projects.aliases` in
     `config/jarvis.json`.

   **Pick the project name carefully when testing this second one.** The obvious phrasings
   don't trigger it: asked about "the jarvis source", the model shortened it to `jarvis`,
   which resolves to the repo root, and no question was ever asked. It has to be a name that
   can't be normalized into something sitting in a configured root.

   **Known cosmetic wart, seen live:** the question Jarvis actually asked was *"I couldn't
   find a project named thesis notes — please tell me the correct project name"*, not "where
   is it?". The small model paraphrases the instruction in the tool result. It doesn't break
   anything (the next utterance is parsed locally as a location regardless of how the question
   came out), but it's misleading — a prompt fix for whenever the wording is next touched.

   **Result (2026-09-08), all three by voice:**
   - *"Open project Jarvis and use Claude Code to say in one sentence what the follow-up
     module does"* — Whisper got "Claude Code" right this time (the `initial_prompt` fix),
     iTerm2 opened in `project_jarvis`, Claude Code read `followup.py`, answer spoken. ✅
   - *"Use Claude Code on my thesis note to say what the phase plan covers"* — the model
     called the tool on an unknown project (the fix), and Jarvis parked the question:
     `waiting to be told where 'thesis note' is`. ✅
   - *"It's inside the project jarvis folder inside the Docs folder"* — **no "slash" spoken at
     all**, and it resolved anyway, in **29 milliseconds with no model call**
     (12:04:47,957 transcript → 12:04:47,986 resolved). Alias learned, iTerm2 opened in
     `docs`, Claude Code read `01-PHASE_PLAN.md` and summarized it. ✅
   - The turn-ordering fix fired twice and correctly: `dropping a stale reply — you've spoken
     since`, once for an overtaken summary and once for a question that arrived after its own
     answer was already running.

   **The one number worth remembering from this run:** local resolution of the spoken location
   took **29 ms**; the model calls around it took **91 seconds** each at their worst (NVIDIA
   free tier, with SDK retries). Everything Jarvis does itself is instant. Everything it asks
   the free tier to do is not. That is the whole of Phase 8's latency problem in one log.
6. ☐ **Then the packaged app.** As in Phase 2, no `build_app.sh` rebuild is needed — the
   bundle loads `run.py` fresh from disk and gets the venv's site-packages via `PYTHONPATH`.
   One thing to actually check rather than assume: `launchd` gives the process a much shorter
   `PATH` than your shell, so if `claude` can't be found there, put its full path in
   `actions.claude_code.cli` (`which claude` → `/Users/<you>/.local/bin/claude`).

---

## Phase 4 — Voice out + credentialed login
Status: ✅ Done — except the spoken end-to-end run, carried forward (see the last note)

- ✅ TTS wired up (local: Piper / `say` / `AVSpeechSynthesizer`) — `src/jarvis/speech.py`,
  three backends behind one interface (`say` default, `piper`, `openai`), **proven live**
  for `say`; every reply from the menu bar app is now spoken
- ✅ `credential_capture` mode in intent parser (local-only handling, per doc 04) —
  `src/jarvis/credentials.py`, runs in `agent.handle()` *before* the model call
- ✅ `redact_secrets()` log filter applied globally — `src/jarvis/redact.py`, installed on
  the log handlers in `main.py` (and in the harness scripts)
- ✅ Keychain-backed vault module (`get(site)`, `set(site, cred)`) — `src/jarvis/vault.py`,
  **proven live** against the real macOS Keychain
- ✅ `fill_login_form` tool (fills, waits for spoken confirmation before submit) —
  `src/jarvis/login.py` + `src/jarvis/browser.py`, **proven live**: filled and submitted a
  real login page, with the confirmation step in between
- ☐ **Definition of done:** spoken login on a real test site works, credential never hits a
  log file — the login half is proven live end to end (below); the *spoken* half is what's
  left, and needs you at the microphone

Notes (2026-09-08):
- **What was built:**
  - `src/jarvis/speech.py` — the output layer, same swappable-backend shape as
    `jarvis.router`: `say` (macOS built-in, the default — free, offline, no setup), `piper`
    (local neural voice, needs `pip install piper-tts` plus a downloaded `.onnx` model) and
    `openai` (cloud, per-utterance cost). A new reply *interrupts* whatever is mid-sentence
    rather than queueing behind it, so talking over Jarvis works the way it does with a
    person. Failures are logged and swallowed — a voice assistant that can't speak is
    degraded, one that crashes because a voice model is missing is broken.
  - `src/jarvis/credentials.py` — doc 04's point 2, and the one piece of that document
    worth building carefully. A local regex lifts "username is X, password is Y" out of the
    transcript *before* the model call and replaces it with a reference, so the brain (which
    may be Claude or GPT, over the network) routes the request without ever learning the
    characters. Includes the spelling-out decoder: NATO alphabet, digits as words, symbol
    names ("dash", "at", "bang"), and case markers ("capital delta", "lowercase echo").
  - `src/jarvis/vault.py` — the Credential Vault box from doc 02, ~140 lines over the macOS
    Keychain via `keyring`, no crypto of its own. Keyed by site *as you say it*, normalised
    so "the billing portal", "billing portal login" and "the billing portal website" all hit
    one entry.
  - `src/jarvis/redact.py` — two overlapping defences: exact redaction of registered secret
    values (anything captured, from any module), plus a pattern for "password is ..." that
    covers the window before the parser has run. Applied as a `logging.Filter` on the
    *handlers*, which is what makes it global rather than remembered per call site.
  - `src/jarvis/confirm.py` — one armed action, a description, and a yes/no that runs or
    drops it, with a TTL and single-use semantics (same two safety properties as
    `jarvis.followup`). Not in the original plan as a separate module; see the deviation
    note below.
  - `src/jarvis/login.py` — the policy half: resolve the ref locally, fall back to the
    Keychain, fill, save, arm the confirmation, and (only from a confirmed action) submit.
  - `scripts/selftest_login.py` (offline, fake browser + fake Keychain) and
    `scripts/try_login.py` (live: `--speak`, `--dictate`, `--keychain`, `--for-real`).
- **What's proven live, and how:** against `https://the-internet.herokuapp.com/login` (a
  public practice login page that publishes its own test credentials), the full chain ran:
  `open_portal` → local capture → `fill_login_form` → *stop and ask* → confirm → submit →
  landed on `/secure`, with the credential saved to and then deleted from the real Keychain.
  The model half is proven separately: with `--backend nvidia`, `gpt-oss-20b` answered the
  dictated sentence with `fill_login_form(site='practice portal',
  credential_ref='pending_dictation_1')` — the right tool, the ref, and no credential
  anywhere in what crossed the network.
- **Nothing submits without a person.** There is deliberately no `submit_login` tool. The
  model can fill a form; only `confirm.resolve(True)` presses the button, and that is
  reached from a yes/no matched locally against a fixed word list, with no model call at
  all. Side effect worth keeping: "no, cancel that" works even when the brain is
  unreachable — the stop button doesn't depend on the network.
- **`say` latency is worth one config line.** Synthesising the reply "Filled in, want me to
  submit?" (2.0s of audio) measured: system default 4.8s, Samantha 2.3s, Alex 2.0s, Daniel
  1.9s, Karen 1.8s, Fred 1.4s. That cost lands squarely in the pause after you stop talking,
  so naming a voice in `speech.backends.say.voice` roughly halves how long Jarvis takes to
  answer. Left as `null` (system voice) by default rather than guessing at your taste; a
  name that isn't installed falls back to the system voice rather than to silence.
- **Bug found & fixed: the redaction pattern was eating whole log lines.** With the
  connective optional, `password\s*(?:is|was)?\s*.*` matched *any* mention of the word —
  a Playwright error reading "there's no password field on <url>" came back as "there's no
  password [credential omitted]", i.e. a log line destroyed to protect nothing. The
  connective is now required; registered values are what catch a real secret in an odd
  phrasing. Same fix in the capture regex, so "the password field isn't showing up" is no
  longer read as a dictation whose password is the word "field".
- **Bug found & fixed: a model that drops the ref.** `gpt-oss-20b` passed the
  `credential_ref` correctly, but a scripted test of the case where a model calls
  `fill_login_form(site=...)` with no ref showed Jarvis asking you to dictate a password you
  had dictated four seconds earlier. `credentials.latest()` now supplies the most recent
  live capture when no usable ref arrives. Nothing about the security story changes — the
  value is on this machine either way.
- **The username is deliberately *not* redacted.** It was, briefly, and it made the logs
  worse for nothing: it isn't the secret, it's the most useful thing in a line about a
  login, and a username like "admin" or "mail" occurs all over a log and half the file
  paths. It's still stripped from the transcript (doc 04 redacts the whole dictation
  clause) and still never reaches a model.
- **2FA stops the flow, per doc 04's point 6.** After submitting, a one-time-code field on
  the page makes Jarvis say a code is needed and hand it back to you, rather than claiming
  it logged in.
- **Deviation from `01-PHASE_PLAN.md`:** the plan describes the confirmation as a property
  of `fill_login_form` ("then pauses for you to confirm/submit"). It's built as its own
  module (`jarvis/confirm.py`) holding a *callable*, because that's both the smallest way to
  do it and the seam Phase 5 needs: a thumbs-up is `confirm.resolve(True)` and needs to know
  nothing about logins. Doc 02 already promised gesture-mapped `confirm`/`cancel` reusing
  the voice contract; this is that contract, arriving one phase early because Phase 4 needed
  it anyway. `01-PHASE_PLAN.md` Phase 5 updated to say so.
- **Environment notes from this session:** the Ollama host in `config/jarvis.json`
  (`192.168.1.110`) was unreachable, so the configured default backend timed out — the
  model-path checks were run with `--backend nvidia` instead (83-130s per call on the free
  tier that day, versus the 2.7-3.7s measured in Phase 2; the tool calls were correct both
  times). Nothing in Phase 4 depends on which backend answers.
### Second round, same day — what the first live voice session broke

You ran it through the microphone and almost nothing worked end to end. The log was worth
more than the feature: five separate defects, four of them mine, and every one of them
invisible to the offline self-test because it took a real model, a real browser window and
a real person talking to produce them.

1. **The model opened the page in the wrong browser.** "Open the practice portal at ..."
   went to `open_url` — macOS's default browser, which Jarvis cannot see — so
   `fill_login_form` was typing into a Chromium that was still on `about:blank`, and said
   so in a way ("is this the login page?") that sent the model hunting for a URL instead of
   for the right *tool*. Fixed in three places: `open_url` now says outright that a page
   opened with it is one Jarvis can't type into and that logins start with `open_portal`;
   `open_portal` says logins start there; and the fill failure now names the fix ("call
   open_portal with the login page's full URL first, then call fill_login_form again").
2. **A closed browser window wedged Jarvis until restart.** Chromium opened on
   `about:blank`, you closed it — entirely reasonably — and every browser command for the
   rest of the session failed with "Target page, context or browser has been closed".
   `ensure_started` only checked that the worker *thread* was alive, which it was. Added
   `BrowserSession._ensure_page`, run before every queued command: a closed page gets a new
   one, a closed window gets a relaunch. Verified live for both.
3. **Saying the username and the password in two breaths lost the username.** doc 04's
   example says both in one sentence; nobody does. "Login, username is lennoxstark47" was
   parsed, found no password, and was discarded whole — then the password arrived with
   nobody to go with it. `jarvis.credentials` is now the *mode* the task list always called
   it: it holds half a credential, says which half is missing, and `expect()` arms it to
   read the next utterance as that half even when it arrives bare ("it's
   L-E-N-N-O-X-S-T-A-R-K-47") with no "username is" to key on. Guarded by a TTL, single use,
   and a command-word check so "open github and log in" is still a command. A password with
   no username is no longer typed in at all — Jarvis asks for the username instead, because
   half a filled form is just a page that doesn't submit.
4. **The model called a page it had just opened "the billing portal"** — copied out of the
   tool description's own example — so the credential would have been filed in the Keychain
   under a name you never said and could never look up. Saved logins are now keyed on the
   **host of the page Jarvis actually typed into**, which is a fact rather than a guess;
   lookup tries that first and the spoken name second. Proven live: a second visit logs in
   from the Keychain with no dictation, while the model is still calling it the billing
   portal. The model's name for a site is now used only in what Jarvis says back.
5. **Four steps wasn't enough to recover.** "Open X and log in" spends a step opening, one
   discovering the wrong browser, one re-opening, one filling — leaving nothing for the
   sentence saying what happened, so a login that worked was reported as "I got stuck
   partway". `MAX_STEPS` 4 → 6.

Also fixed from that session: a `credential_ref` passed in the `site` argument (and, once,
the literal redaction placeholder) is now recognised rather than saved as a site name; a
spelled-out *username* decodes to lower case, which is what a login field wants; and
`speech.speak` logs what it said and how long it took, because a silent failure and a
working reply were indistinguishable in the log.

**Now proven live, four turns through the real local model** (`granite4.1:3b` on the LAN
Ollama box), replaying the exact shape of your failed session:

    "Open the portal at the-internet.herokuapp.com/login."   -> open_portal, login form found
    "Log in, username is tomsmith."                          -> "Please say your password."
    "The password is SuperSecretPassword bang."              -> filled, "want me to submit?"
    "Yes."                                                   -> submitted, landed on /secure

...and again on a second visit with no dictation at all, from the Keychain.

**One thing that is not fixed, because it isn't Phase 4's to fix:** spoken URLs. "the
internet dot herokuapp dot com slash login" came back from the model as
`https://login.herokuapp.com` — it mangles a domain it has to assemble from dictation, and
Whisper had already turned "herokuapp" into "herocap" once. Saying a URL out loud is a bad
interface; the fix is Phase 6's aliases ("the practice portal" -> a URL Jarvis already
knows), and `open_portal` now at least says out loud when it lands somewhere with no login
form on it, so a wrong page is noticed immediately instead of three steps later.

### Third round — "it should be easier" (the GitHub attempt)

Your second live session was one sentence — *"open GitHub.com and go to login in the Firefox
browser"* — and it produced two browser windows, neither of them Firefox, and then silence.
What you actually wanted was stated plainly enough to build to: open the page **in Firefox**,
land on the login form, and then **ask me for my username and password**. Four changes:

- **Jarvis now leads the login conversation.** `open_portal` landing on a page with a
  password field no longer just reports it. If there's a saved login for that host it says
  so and tells the model to fill it straight away; if there isn't, it tells the model to ask
  you out loud — *and arms the local parser* (`credentials.expect("both")`), so your answer
  is understood whether you say "username is tomsmith" or just "tomsmith". Live, turn one now
  ends with Jarvis saying "please tell me your username", which is the flow you described.
- **Firefox.** `playwright install firefox` done, `actions.browser.engine` switched to
  `firefox`, verified against `github.com/login` and the practice site. **What this is not:**
  your own Firefox, with your sessions and extensions. Playwright drives its own Firefox
  build with its own profile, and it cannot attach to a normal running Firefox — no
  automation tool can, Firefox has no equivalent of Chrome's remote-debugging attach. So
  this is a Firefox-shaped window that is not the one in your Dock. Set the engine back to
  `chromium` if that trade isn't worth it.
- **Profiles are now per-engine** (`memory/browser/<engine>`). A Chromium profile directory
  is not a Firefox one, and pointing one at the other either fails or corrupts it.
- **Both `fill_login_form` arguments are optional now.** A required `site` cost a whole step
  live — the model had the ref, had no name for the site, and burned a turn on "missing 1
  required positional argument" before inventing `example.com` to get past it. Jarvis keys
  on the page it types into, so it needs neither argument.

**Bug found in your Keychain, not in a test.** Checking whether a GitHub login was doable
turned up a real saved entry for `github.com` with the username **`lenoxstark47`** — one
letter short of yours, straight from a Whisper mis-hearing. It was saved at *fill* time,
before anyone knew whether the login worked, so a mis-transcription became a permanent entry
that would be silently reused on every later visit. Fixed: a dictated credential is now
saved only after the site has **accepted** it (navigated away, or asked for a 2FA code —
which means the password got through). A rejected login saves nothing and re-arms the parser
so you can just say it again. Delete the bad entry with:

    .venv/bin/python3 scripts/try_login.py --keychain github.com --forget

**On model strength.** The local `granite4.1:3b` is not the bottleneck it looked like. Every
failure in both live sessions had a cause in Jarvis's own code — a wrong tool description, a
required argument, a wedged browser, a parser that threw away half a credential — and each
one is fixed in the model-agnostic layer, where it helps every backend. Worth knowing before
switching: `meta/muse-glimmer-30b` *is* reachable with your NVIDIA key (81 models are; list
them with `GET {base_url}/models`), but the free tier measured **84-130s per call** on
2026-09-08, against 0.5-1s for the local model. That's the difference between a voice
assistant and a form you submit. A/B any of them without touching config:

    .venv/bin/python3 scripts/try_login.py --backend nvidia --model meta/muse-glimmer-30b

### Fourth round — your Firefox, not a browser Jarvis brought with it

You were clear twice, and the second time the reason landed: *"I have Firefox installed in
my system. I want the AI to use that Firefox."* Round three had switched to Playwright's
Firefox, which is a different browser that happens to share a name — none of your logins,
none of your extensions, not the window in your Dock.

**The blocking fact, verified rather than assumed:** Playwright *cannot* drive your Firefox.
Its Firefox is a patched build speaking the Juggler protocol; stock Firefox doesn't implement
it. Pointing `executable_path` at `/Applications/Firefox Developer Edition.app` fails to
launch, and `channel="firefox"` silently uses Playwright's own bundled copy instead. So "my
browser" and "Playwright" were mutually exclusive, and the fix was a second engine, not a
setting.

- `jarvis/browser.py` is now a base class holding all the thread/queue/recovery plumbing,
  with two engines under it exposing an identical four-action contract, so nothing above it
  changed: **`SystemFirefoxSession`** (your Firefox, via geckodriver — Mozilla's own driver
  and the supported way to automate a stock build) and **`PlaywrightSession`** (the previous
  behaviour, still there).
- `actions.browser.engine` is now `system-firefox` by default, with `binary` (null =
  find Firefox in /Applications, Developer Edition included) and `profile` (null = whichever
  profile Firefox itself opens — read out of `profiles.ini`, honouring the `[InstallXXXX]`
  section, which is what Developer Edition actually uses).
- **Selenium fetches geckodriver itself** (Selenium Manager), so there is nothing to
  `brew install`. `selenium>=4.20` added to requirements.
- **Profile locking is checked precisely**, by trying to take Firefox's own `fcntl` lock on
  `.parentlock` — not by asking "is any Firefox running". The difference matters: a second
  Firefox on a *different* profile is fine, and the coarse check would have stopped Jarvis
  working whenever your browser was open at all. When the profile really is in use, Jarvis
  says so in a sentence it can speak: *"your Firefox is already open with that profile ...
  quit Firefox and ask me again."*
- Doc 02's "Playwright, chosen over raw Selenium" is a locked decision, so it's been revised
  there rather than quietly contradicted here.

**Proven live:** geckodriver launched Firefox Developer Edition, navigated to
`github.com/login`, and found and filled both fields (dummy values, nothing submitted). Then
the whole four-turn login ran through the real Firefox on the practice site, with the local
3B model, ending logged in with the credential saved.

### The URL problem, finally fixed — locally, not by a bigger model

Three separate live runs, three mangled URLs from the model: `the-internet.herokuapp.com/login`
came back as `login.herokuapp.com`, then `the-intent-internet.herokuapp.com`. Whisper had
transcribed it *correctly* every time — the model corrupted a string it could see.

So `tools.prefer_spoken_url` applies the rule the rest of Jarvis already runs on: **anything
the user said themselves is resolved locally rather than round-tripped through a model.** A
URL spoken out loud (including "github dot com slash login", folded back into punctuation)
overrules the model's version when they disagree; when the user only named a site ("open
GitHub"), the model still supplies the URL, and when the two agree the model keeps its path.
Two spoken URLs in one sentence is ambiguous, so it defers rather than guessing. Live, this
immediately rescued a run: the model asked for `the-intent-internet.herokuapp.com` and Jarvis
opened `the-internet.herokuapp.com`.

The limit worth knowing: a hyphen that was never spoken can't be recovered — "the internet
dot herokuapp dot com" is genuinely ambiguous between "theinternet." and "the-internet.".
Say "dash", or let Phase 6's aliases remember the whole URL.

**On "we should use perfect models":** the timing run finished — `meta/muse-glimmer-30b` took
**262.7s** for one command and invented the URL `https://practiceportal.com`. That's the
larger model doing *worse* on the exact thing that kept failing, 400x slower. Every defect in
all four rounds had its cause in Jarvis's own code, and fixing them in the model-agnostic
layer is what made a 3B model on a LAN box run the whole flow correctly. Decision this round:
stay local.

### Fifth round — the first real voice attempt, and what it taught

Firefox quit, four turns spoken into the microphone. It did not log in, and the log is worth
keeping because three of the four failures were mine and one is a lesson about the interface
itself.

**What worked:** your Firefox launched with your own profile from a spoken command; the
URL-correction fired and logged what it did; Whisper, the agent, the browser and the voice
all ran the loop without a crash.

**Fixed, from that log:**

1. **The word "dash" survived into the domain.** Told to say "the dash internet", Whisper
   wrote `the dash-internet.herokuapp.com` — the word stayed *and* became punctuation, so
   the extractor read the host as `dash-internet.herokuapp.com`. The spoken-punctuation rule
   now swallows an adjacent hyphen, and that same transcript yields
   `the-internet.herokuapp.com`.
2. **The URL correction made one case *worse*, which is the failure that matters most.**
   Whisper split "herokuapp" into "heroku app"; the extractor read `the-internet.heroku` as a
   domain and overruled the model with it. A correction that can't tell a domain from a
   fragment has no business overruling anything, so a host must now end in a real TLD — any
   two-letter country code, or a list of the gTLDs people say out loud. `.heroku` isn't one,
   so Jarvis defers to the model instead of confidently breaking it.
3. **A DNS failure was 900 characters of Selenium stack trace** — passed to the model, come
   back paraphrased, and then *read out loud* for seventeen seconds. Driver errors are now
   translated where they happen: "there's no site at portal.example.com — that address
   doesn't exist". Everything Jarvis says has to survive being spoken.

**The lesson, which no amount of code fixes:** `the-internet.herokuapp.com` is not a
speakable domain. Whisper split it three different ways across four attempts, and every
repair pass is guessing at a hyphen that was never in the audio. That is a bad test target,
and it was my choice, not a fault in the phase. GitHub's domain is one word Whisper already
knows (it's in `stt.INITIAL_PROMPT`), which is why the next attempt uses it — and it's the
site you wanted to log into in the first place.

Also seen, not yet fixed: turn 2 ("the correct URL is ...") went to `open_url` rather than
`open_portal`, because each utterance is a fresh conversation and nothing tells the model a
login is in progress. Worth watching; conversation memory is Phase 6, and forcing it earlier
would mean building Phase 6's memory store inside Phase 4.

### Sixth round — the GitHub attempt, and Jarvis deadlocking itself

Spoken into the microphone against GitHub. Most of the phase worked, on the first try:

- *"Open github dot com slash login"* — transcribed correctly, page opened.
- *"My username is LenoxStar47"* — captured locally, and Jarvis asked out loud for the
  password. The two-utterance credential flow, which round two built, did its job.
- The password turn appears in `logs/jarvis.log` as **`transcript: The password [credential
  omitted]`**. That is doc 04's whole point, working, on a real password.
- `fill_login_form(credential_ref='pending_dictation_1')` — right tool, right ref, no
  credential anywhere in what crossed the network.

Then it failed, and the cause was mine: **turn one's `open_url` handed the URL to macOS,
which launched the user's Firefox, which took the profile lock — so two turns later
geckodriver could not drive the browser the page was sitting in.** Jarvis deadlocked itself.

The flaw was introduced by making `system-firefox` the engine and not following it through.
`open_url` ("a page to read") and `open_portal` ("a page to act on") were two different
browsers under Playwright, and that distinction is what the model has to get right. With one
real browser there is only one browser, so `open_url` now goes through the driven session
too, and the model's choice between the two tools stops being able to break anything. If the
driven browser can't be had — usually because Firefox is already open — it falls back to
macOS `open`, which lands the page in that same window, the best available outcome for
something you only want to read.

Also fixed: the profile-lock message was 272 characters and took **19 seconds** to speak. The
spoken half is now one short sentence; the part about `actions.browser.profile` went to the
log, where length costs nothing.

### Where Phase 4 actually stands

Every task in the list is built and proven. The Definition of done has two halves, and they
are in different states:

- **"credential never hits a log file"** — proven, on a real password, through the
  microphone. This was the half worth building carefully and it is done.
- **"spoken login on a real test site works"** — the full chain has run end to end
  repeatedly (open → capture → fill → confirm → submit → logged in, credential saved to the
  Keychain only after the site accepted it), in the real Firefox, driven by the local 3B
  model. What has never completed is that same run *starting* from the microphone, and every
  failure has been in one place: the model choosing a tool, or Whisper hearing a domain.

Neither of those is a Phase 4 problem any more:

- Tool choice going wrong across turns is a **conversation memory** gap — each utterance is a
  fresh conversation, so nothing tells the model a login is already in progress. That is
  Phase 6, and forcing it earlier means building Phase 6's memory store inside Phase 4.
- Saying a URL out loud is a bad interface, and the fix is the same **alias** work in Phase 6
  ("the practice portal" → a URL Jarvis already knows).

**Decision (2026-09-08):** carry the spoken-login verification forward rather than hold the
phase open for it. Phase 5's gestures land on `confirm.resolve()`, which is proven and does
not depend on it. Re-run the four turns whenever Phase 6 gives the loop memory, and tick the
box then. `.venv/bin/python3 run.py`, hold F9 for each:
  1. *"Open the portal at the-internet.herokuapp.com/login"* — say the URL slowly, or add it
     to `actions.projects.aliases`-style config later; if Whisper mangles it, open the page
     with `scripts/try_login.py --portal ... --for-real` and start from turn 2.
  2. *"Log in, username is tomsmith"* → Jarvis should ask for the password out loud.
  3. *"The password is SuperSecretPassword bang"* → it fills both fields and asks to submit.
  4. *"Yes"* → it submits and says where it landed.
  Then check `grep -i "SuperSecret\|password is" logs/jarvis.log` finds nothing. When that
  passes, tick the second half of this phase's Definition of done.

**Update 2026-09-09 — Phase 6 landed, so both gaps above are now closed in code:**

- *Conversation memory*: the loop carries a rolling window (`jarvis.memory.history`,
  6 turns / 15 minutes by default), so turn 3 knows a login started at turn 2. Turn 2's
  transcript is stored masked, on purpose — see Phase 6's note on why refusing it outright
  was the wrong call and would have left this exact flow with holes in it.
- *Saying a URL out loud*: teach it once instead. Say **"remember that the practice portal
  is the-internet.herokuapp.com/login"**, or run
  `scripts/try_memory.py --for-real --remember "the practice portal" the-internet.herokuapp.com/login`,
  and then turn 1 becomes *"Open the practice portal"* — no domain spelled out, nothing for
  Whisper to mangle. That alias is recalled into context whenever the phrase is said.

**Still not verified**, because it needs the microphone and a person: run the four turns
above with turn 1 replaced by the alias, and tick the box. Nothing else is outstanding.

---

## Phase 5 — Hand gestures
Status: 🔶 In progress — **paused 2026-09-09, deliberately**, with the code built and
working. Your call, and a reasonable one: the voice path is the product, and it still
has open items (see "Current focus"), whereas gestures are a second input channel for a
confirmation step that already works by voice. Nothing here is half-finished or left in
a state that rots — what remains is two *verification* runs, both of which need a person
in front of a camera and are written out step by step at the end of this section.

**To resume, cold, from this line alone:** run steps 4 and 5 of "How to test Phase 5"
below. If both pass, flip this to ✅ Done. If the soak fires anything, raise
`gestures.hold_frames` in `config/jarvis.json` before touching `min_confidence`, and see
the false-trigger note for why that order.

- ✅ MediaPipe wired to the webcam feed — `src/jarvis/gestures.py`, **proven live**
  (camera opens, frames classify at ~86 ms each, real hands recognised)
- ✅ Gesture vocabulary defined (thumbs-up = confirm, open palm = cancel) —
  `gestures.bindings` in `config/jarvis.json`
- ✅ Gestures mapped into the same contract as voice — a gesture calls
  `confirm.resolve()`, the identical call a spoken "yes" makes; no new tool, no branch
  in the agent loop
- ✅ Camera on only while something is pending — `main.py._sync_gesture_watcher`,
  **proven live** (`try_gestures.py --wiring`)
- ☐ **Definition of done, half 1:** a thumbs-up reliably confirms a pending action —
  needs a hand in front of the camera, so it needs **you** (`--confirm`, below).
  *Deferred 2026-09-09, not blocked.*
- 🔶 **Definition of done, half 2:** no false triggers over 10 minutes — **one false
  trigger measured** at the original settings, the fix is in, the re-run is outstanding.
  *Deferred 2026-09-09, not blocked.*

Notes (2026-09-09):

**What was built:**
- `src/jarvis/gestures.py` — the perception layer, in two halves that are deliberately
  separable. `Stabilizer` is pure logic over a stream of `(label, score)` readings and
  is where a false trigger is prevented or allowed; `GestureWatcher` is the camera loop
  that feeds it. The split is what makes 53 offline checks possible with no camera, no
  model file and no hand.
- `config/jarvis.json` — a `gestures` block (enabled, only_when_pending, camera_index,
  frame size, fps, hold_frames, min_confidence, cooldown, bindings), plus
  `JARVIS_GESTURES_*` env overrides on the same layering the 2026-09-09 backend note
  describes.
- `src/jarvis/main.py` — a `Gestures:` menu bar line and the start/stop rule. The line
  is not decoration: "watching (camera on)" versus "off" is the one thing a person is
  entitled to see stated plainly, and it's also how a broken camera is told apart from
  a session where nobody gestured.
- `scripts/selftest_gestures.py` (53 offline checks) and `scripts/try_gestures.py`
  (`--check`, `--watch`, `--confirm`, `--soak`, `--wiring`).

**Phase 4 paid for most of this, exactly as it predicted.** `jarvis.confirm` holds a
*callable*, so `handle_gesture` is nine lines and knows nothing about logins: it calls
`confirm.resolve(meaning == "confirm")` and speaks whatever comes back. Doc 02's claim
that gestures would plug in "without touching Phases 1-4" held — no file from those
phases changed to make this work, only `main.py`, which is the wiring.

**"Only while gesture mode is active" turned out to have an exact meaning already in
the design: while a confirmation is pending.** Jarvis has just asked you something out
loud, so a thumbs-up in the next two minutes is unambiguously an answer to it; outside
that window there is nothing a gesture could resolve even if one were seen. So the
camera light is on only in a window that always corresponds to a spoken question — and
that is also most of the "no false triggers" requirement for free, since for the
majority of a session nothing is armed. Recorded in doc 02 rather than only here.

**⚠ The mediapipe version pin is load-bearing, and the failure is a process abort.**
`mediapipe 1.0.1` (the current release) **cannot run any hand graph in Python on
macOS/arm64**. `GestureRecognizer` and `HandLandmarker` both abort inside
`TensorsToDetectionsCalculator::Open()` with `graph_service.h:139 Check failed:
service_ Service is unavailable` — a Metal helper the CPU graph never registers, and
asking for `BaseOptions.Delegate.CPU` explicitly does not avoid it. Being an abort
rather than an exception, no `try`/`except` can contain it: Jarvis would simply die,
mid-sentence, the first time you gestured. `0.10.35` runs the identical code correctly
and is pinned in `requirements.txt` with this reason next to it.

**A dependency near-miss worth recording, because the fix was to *not* take the
upgrade.** `pip install mediapipe` pulled `opencv-contrib-python 5.x`, which requires
`numpy>=2` — straight through Phase 1's documented `numpy<2` pin, the one guarding
against the Accelerate `_cblas_caxpy$NEWLAPACK$ILP64` crash. Rather than lift a pin
that a previous phase put there for a measured reason, `opencv-contrib-python` is
pinned to the 4.x line, which is happy on numpy 1.26.4. Verified afterwards: mic,
camera, faster-whisper's decode path and all three older self-tests still pass.
(Incidentally, numpy 2.5.3 *does* import fine on this machine today, where 2.5.2
didn't in Phase 1 — but "it doesn't crash today" is not a reason to undo a pin, and
nothing needed it.)

**Also worth knowing: two OpenCV distributions in one venv is a trap.** `opencv-python`
and `opencv-contrib-python` both install a `cv2` package over each other, so which one
you get depends on which pip touched last. `requirements.txt` now names only the
contrib build (mediapipe's own dependency, and a superset).

**Measured, on this Mac:**

| | |
|---|---|
| camera open | **2.30s** — the dominant cost, and not pre-payable |
| gesture model load | **0.97s** first time in a process, **0.02s** after (TFLite caches it) |
| classification | **86 ms/frame**, so ~11 fps is the ceiling; `fps: 10` sits just under it |
| `Thumb_Up` confidence | **0.73-0.74** on MediaPipe's own reference photos |
| `Open_Palm` confidence | **0.60**, same source |

Those last two changed a default before it ever ran: `min_confidence` started at 0.7,
which would have made **cancel unfirable** while looking entirely reasonable in the
config file. It is 0.5, and stability comes from the hold, not from demanding a high
per-frame score. The 2.3s camera open needs no fix either: the watcher starts when the
confirmation is armed, which is *before* Jarvis finishes speaking the question (1.9-4.8s
by Phase 4's measurements), and nobody answers a question they haven't heard. The 0.97s
model load is now paid at startup on a background thread (`GestureWatcher.warm_up`).

**⚠ One false trigger, measured — and the fix is not yet validated.** A soak run
(`--soak`, nothing being gestured deliberately) fired a false **cancel** 46 seconds in.
That is the Definition of done's second half failing, and it is worth being precise
about why: an open palm is the most common *incidental* hand shape in front of a
laptop, and `hold_frames: 6` at 10 fps meant holding one still for **0.6s** was enough.
Raised to **12** (~1.2s). A deliberate gesture is easy to hold that long; a hand resting
in frame much less so.

What that fix has **not** had is a clean 10-minute run to prove it, because the soak was
stopped partway (it turns the webcam on for ten minutes, which is not something to leave
running unannounced). **This is the one thing standing between Phase 5 and done**, along
with the thumbs-up half that needs a hand. Note the asymmetry while judging it: a false
*cancel* drops a pending action and Jarvis asks again, whereas a false *confirm* submits
a login form — nothing has ever falsely confirmed, but the run that would establish that
hasn't happened either.

### How to test Phase 5 (do these in order)

1. **Offline, no camera, no model, no hand:**
   ```
   .venv/bin/python3 scripts/selftest_gestures.py    # 53 checks, currently all passing
   ```
2. **Is the camera side alive?** (opens the camera for a second, classifies one frame)
   ```
   .venv/bin/python3 scripts/try_gestures.py --check
   ```
3. **Does the app open the camera at the right times?** (no run loop, no second menu
   bar icon — it drives `JarvisApp`'s gesture plumbing directly)
   ```
   .venv/bin/python3 scripts/try_gestures.py --wiring
   ```
4. ☐ **Definition of done, half 1 — your hand.** Arms a pretend action and waits:
   ```
   .venv/bin/python3 scripts/try_gestures.py --confirm
   ```
   Hold a thumbs-up steady for about a second and a half, facing the camera. It should
   print `gesture said: confirm` and run the armed action. If nothing fires, run
   `--watch` and hold the same gesture: that prints every frame's label and score, which
   separates "it never saw a thumbs-up" (a lighting/framing problem) from "it saw one at
   0.4" (lower `min_confidence`) from "it saw one but you moved" (lower `hold_frames`).
5. ☐ **Definition of done, half 2 — ten minutes, and don't gesture.**
   ```
   .venv/bin/python3 scripts/try_gestures.py --soak 600
   ```
   **The webcam is on for the whole ten minutes.** Work normally in front of it. Anything
   that fires is a false trigger and gets printed with a timestamp. Zero is the bar.
6. ☐ **Then by voice, the whole thing.** `.venv/bin/python3 run.py` (quit the
   launchd-started `dist/Jarvis.app` first, or you'll have two Jarvises — the Phase 1
   mix-up). Run a login until Jarvis asks *"want me to submit?"*, then **don't answer out
   loud** — give it a thumbs-up. The menu bar's `Gestures:` line should read
   "watching (camera on)" for exactly that window, and the form should submit.

---

## Phase 6 — Memory & personalization
Status: ✅ Done — 2026-09-09

- ✅ SQLite memory store set up — `src/jarvis/memory.py`, `memory/jarvis.db`
  (0600, gitignored), three tables: `aliases`, `preferences`, `turns`
- ✅ Aliases (e.g. "my project" → path) storable/retrievable — stored by the
  `remember` tool and by the "where is that project?" answer, read back through
  `jarvis.projects.aliases`
- ✅ Preferences (voice, default backend per task type) storable/retrievable —
  `memory.set_preference` / `memory.set_backend_for`, applied by
  `config.speech_config` and `config.default_backend`
- ✅ Selective retrieval into prompt context (not full dump) —
  `memory.relevant_aliases` matches alias names against the sentence and caps at
  `memory.max_facts`; plus a rolling conversation window with a turn count *and*
  a TTL
- ✅ **Definition of done:** "open my project" works without repeating the path
  — **proven live 2026-09-09** (see "What was actually run" below). Proven across
  a *cold process*, which is the mechanism the "days later" part rests on; the
  only thing elapsed days add is time, which SQLite is indifferent to.

Notes (2026-09-09):

**What was built:**
- `src/jarvis/memory.py` — the store. Doc 02's two kinds of memory, both behind
  functions that never raise: every read and write swallows `sqlite3.Error` *and*
  `OSError`, so a locked database or an unwritable disk degrades Jarvis to its
  Phase 5 statelessness rather than costing the user the sentence they just said.
- `src/jarvis/tools.py` — `remember` and `open_project`. Two tools this phase
  did not originally plan for; `01-PHASE_PLAN.md` now records why (the
  Definition of done is a sentence with two halves and neither existed).
- `src/jarvis/agent.py` — the loop is finally conversational. `_context` puts
  the recent turns and the relevant facts in front of the model; `_remember`
  writes the exchange back afterwards.
- `config/jarvis.json` — a `memory` block, plus `JARVIS_MEMORY_*` env overrides
  on the same layering as Phase 5's backend switch.
- `src/jarvis/main.py` — a "Forget This Conversation" menu item.
- `scripts/selftest_memory.py` (93 offline checks) and `scripts/try_memory.py`
  (`--remember`, `--forget`, `--set`, `--ask`, `--history`, ...).

**The retrieval question turned out not to be a question.** Doc 02 asks for
"retrieved selectively, not dumped wholesale", which reads like it wants
embeddings and a similarity search. It doesn't: the thing being retrieved is a
name *the user chose and then said out loud*, so matching the stored name
against the sentence — on the same normalized form `jarvis/projects.py` already
uses for project names — is not an approximation of the right answer, it is the
right answer. Measured cost of a recall on a store with several aliases: below a
millisecond, against 2.8-5.2s for the model call it feeds. Nothing here needs to
get cleverer until memory holds something that isn't a name.

**Where a learned alias lives changed, deliberately.** Phase 3 wrote them into
`config/jarvis.json` (`config.save_alias`, now removed); they go to the memory
store instead. Two alias layers now feed one resolver, and the config file wins
on a name collision — a line you typed is a more deliberate statement than a
sentence Whisper reconstructed. The reason for the move is narrower than
"Phase 6 owns aliases": a voice assistant that edits a file you also edit by
hand is a merge conflict waiting to be discovered at the worst possible moment.

**⚠ A rule that only became necessary in this phase: a preference set by voice
is set from an untrusted string.** It arrives down the same path as every other
spoken instruction — Whisper's reconstruction, interpreted by a small model. So
`config.PREFERENCE_KEYS` is a closed list of the config paths a preference may
reach, and it deliberately excludes `actions.claude_code.allow_edits` and
`actions.projects.roots`: the two settings that decide whether the coding
sub-agent may write to your files and which folders it may be launched in. Those
stay decisions made in a file, with your hands. Pinned in the self-test, because
it is precisely the kind of boundary a later phase widens by accident.

**⚠ The credential rule was written twice, and the first version was wrong.**
The first draft *refused* to store any turn the redaction layer touched. That
looks like the cautious choice and is actually the bad one: a spoken login is
four turns long ("open the portal" / "my username is..." / "my password is..." /
"yes"), so refusing those turns leaves the window holding exactly the half of
the exchange that doesn't explain what's going on — which defeats the reason
Phase 6 was prioritised over gestures at all. What ships stores the *masked*
sentence ("log in as alice, my [credential omitted]"), which is the identical
string Phase 4 already writes to `logs/jarvis.log` and already sends to the
cloud model as the live transcript. Redaction happens on the way **in**, so
there is no moment at which the plaintext exists inside `memory/jarvis.db`.

**⚠ Every self-test that runs an agent turn now has to say so.** Adding memory
made `Agent.handle` write to disk, and three older self-tests started quietly
appending to the *user's real* `memory/jarvis.db` — `selftest_login` got as far
as leaving "yes" and "Logged in — the page is now 'Dashboard'" in it, and
`selftest_brain` then failed, because the window it had polluted was being fed
back into the next prompt and the check on what the model was sent no longer
matched. Two fixes, and both are worth knowing about when Phase 7 adds more
state: `JARVIS_MEMORY_ENABLED=false` at the top of the three older files (the
real environment layer, not a private hook), *and* a `memory` block inside each
file's `FakeConfig` — because those patch `config.load_config` wholesale, which
hides the environment layer along with everything else. `selftest_memory.py`
builds its own store in a temporary directory instead.

**A near-miss the self-test caught, not a review:** the "never raises" promise
was only ever true for `sqlite3.Error`. The failures that actually happen — an
unwritable directory, a full disk — surface as `OSError` from *opening* the
file, before any query runs, and would have propagated all the way out of a
turn. The degradation check (a database in a 0500 directory) is what found it.

**Measured, on this Mac, live against OpenRouter/nex-n2.5-mini:free:**

| | |
|---|---|
| recall for one utterance | **< 1 ms** — string matching, not a search |
| "open my project", cold process, alias taught in an earlier one | **4.6-5.2s**, correct `open_project` call both times |
| a follow-up in a *third* process ("what did I just ask you to open") | **2.8s**, answered correctly from the window alone |

**What was actually run (the Definition of done, in three separate processes):**

```
.venv/bin/python3 scripts/try_memory.py --for-real --remember "my project" "project jarvis"
    -> Remembered: my project means project_jarvis.
# process exits

.venv/bin/python3 scripts/try_memory.py --for-real --ask "open my project"
    recalled for this sentence: ['"my project" means a project folder at .../project_jarvis']
    tool: open_project(name='my project')  ->  Opened project_jarvis
# process exits

.venv/bin/python3 scripts/try_memory.py --ask "what did I just ask you to open"
    history carried in: 2 turn(s)
    says: You asked me to open the project folder `project_jarvis` in Finder.
```

**Deliberately not built, and each is somebody else's phase:**
- **No tool sets a preference by voice.** Preferences are storable, retrievable
  and *applied* (a stored voice reaches `jarvis/speech.py`), and settable from
  `scripts/try_memory.py --set`. What's missing is a sentence like "use the
  Daniel voice from now on" reaching them, which needs a third tool. The phase
  asked for storable/retrievable; adding tools past what the Definition of done
  needs is what `01-PHASE_PLAN.md`'s ground rule exists to prevent.
- **Nothing classifies a task type.** `backend_for("coding")` stores and
  `config.default_backend("coding")` honours it, but no caller passes a type,
  because deciding what *kind* of request something is is Phase 7's router.
- **No summarisation of old turns.** The window drops turns on a count and a
  TTL. A summary of what fell off is a real feature and also a second place for
  a small model to invent something; not worth it until the window is the thing
  that's limiting.

### How to test Phase 6

1. **Offline, no model, no network** (never touches the real database):
   ```
   .venv/bin/python3 scripts/selftest_memory.py    # 93 checks
   ```
2. **What Jarvis remembers about you, right now:**
   ```
   .venv/bin/python3 scripts/try_memory.py
   ```
3. **The Definition of done — and it must be more than one process**, or all
   you've proven is that a dict still holds what you put in it:
   ```
   .venv/bin/python3 scripts/try_memory.py --for-real --remember "my project" "project jarvis"
   .venv/bin/python3 scripts/try_memory.py --for-real --ask "open my project"
   ```
4. **By voice, the real thing.** `.venv/bin/python3 run.py` (quit the
   launchd-started `dist/Jarvis.app` first). Say *"remember that my project is
   project jarvis"*, then — in a later session, after quitting and restarting —
   say *"open my project"*.

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
- Flagged from Phase 2 (2026-09-05): a tool-only command costs **two** model calls — one to
  pick the tool, one to narrate the result — which was measured at ~9s total on NVIDIA's free
  tier, about half of it spent on a sentence the user could be given locally. Speaking a
  composed confirmation as soon as a tool returns (and only round-tripping to the model when
  the tool *failed*, or when the user actually asked a question) should roughly halve
  perceived latency. Left undone deliberately: it's a latency optimization, not Phase 2 scope.

---

## Phase 9 (stretch) — Proactive behavior
Status: ☐ Not started — deliberately deferred, do not start early

- ☐ _(left open on purpose — revisit only once Phases 0-8 are boring and reliable)_

Notes:
- _(none yet)_

Notes (2026-09-05, later session — Phase 0 regression found on a real login):
- **Bug: no menu bar icon after an actual reboot/login** (reported as "can't open the app
  Jarvis, although when I start the laptop it asked for mic and camera permission"). The
  process was genuinely fine the whole time — `launchd` had it running as PID 778 since
  16:38:06, `lsappinfo` showed it registered as `type="UIElement"` under `com.jarvis.agent`,
  `sample` showed it parked in `-[NSApplication run]`, and `logs/jarvis.log` had the normal
  `mic: ready` / `camera: ready` pair. A screenshot of the full menu bar showed no `Jarvis
  [🎤📷]` item anywhere. `launchctl kickstart -k` on the *identical* build made it appear
  instantly, and it then stayed put.
- **Root cause:** rumps creates the `NSStatusItem` synchronously inside `App.run()`, *before*
  `AppHelper.runEventLoop()` starts. At login that call can land while the GUI session is
  still coming up, and macOS silently never places the item — no exception, no error in the
  unified log (checked: zero scene-activation errors, unlike the earlier `Info.plist=not bound`
  bug), so the app looks completely healthy while having no UI at all.
- **Correction to the previous session's verification claim:** `launchctl kickstart -k` is
  *not* equivalent to a login relaunch, which is what the note above assumed. Kickstart
  restarts the job on an already-established desktop and always works; only a real login
  reproduces the race. Phase 0's "runs at login" box was ticked on that false equivalence.
- **Fix (`src/jarvis/main.py`):**
  - Added a status-item watchdog: for the first 60s after launch it polls every 3s and, if the
    item has no placed backing window (`button().window()` nil or zero-width — `isVisible()`
    only reports requested visibility, so it can't be used here), tears it down and asks the
    status bar for a fresh one. Recreating via rumps' own `initializeStatusBar()` would append
    a second "Quit Jarvis" entry each call, so the repair rebuilds the item directly and
    re-attaches the existing menu.
  - Moved the startup permission check out of `main()` and onto a timer that fires once the run
    loop is up. It used to run *before* `app.run()`, so opening the camera delayed the status
    item's creation by ~8s (26s after launch at login, once cold Python import time is counted)
    — which is both why the app looked dead on startup and extra time spent inside the fragile
    window.
- **Verified:** watchdog reports `Menu bar item is on screen.` on a normal start with zero
  repairs (no false positives); repair path exercised by forcing one unhealthy check — item is
  back on screen afterwards, title preserved, menu intact with exactly one "Quit Jarvis".
  Menu bar icon confirmed by screenshot on the fixed build.
- **Still unverified:** the actual login path. The race only reproduces on a real reboot/login,
  so the watchdog's recovery has not yet been seen firing in the situation it was written for.
  Next reboot, check `logs/jarvis.log` for `Menu bar item was not on screen — recreated it`
  (watchdog did its job) or `Menu bar item never appeared after 60s` (fix insufficient — the
  next thing to try is launching via LaunchServices, i.e. `/usr/bin/open -a` in the launchd
  plist, so the app enters a fully-established GUI session).
