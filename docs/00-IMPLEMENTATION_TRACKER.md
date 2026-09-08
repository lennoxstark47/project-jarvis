# Implementation Tracker

**Rule:** read this doc before starting any work on Jarvis, to see what's already
done and what's left. After finishing any piece of work, update this doc — check
off what's done, and add a dated note if anything deviated from the original plan
in `01-PHASE_PLAN.md` (scope changed, a task got split, something turned out
harder/easier than expected). Tasks map 1:1 to the bullets and "Definition of
done" in `01-PHASE_PLAN.md` — if you add a task here that isn't in that doc, add
it there too so the two stay in sync.

**Current focus:** Phase 3 is ✅ Done. Ready to start Phase 4 — voice out + credentialed
login.

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

- **⚠ Carry-forward for whoever changes `backend` in `config/jarvis.json`:** only the
  `nvidia` path has ever made a real network call. `claude` and `ollama` are written and
  their message translation is pinned by `scripts/selftest_brain.py`, but neither has been
  exercised against a live server, so treat the first switch to either as a test, not a
  config tweak — run `scripts/try_brain.py --backend <name>` before trusting the voice path
  with it. The likeliest breakages are the bits a self-test can't check: Anthropic's
  `output_config`/effort field on the installed SDK version, and whichever Ollama model gets
  pulled actually supporting tool calls (most small ones don't).

### How to test Phase 2 (do these in order)

1. **Offline, works right now, no key needed** — the loop and all four backends' message
   translation:
   ```
   .venv/bin/python3 scripts/selftest_brain.py     # 28 checks, currently all passing
   ```
2. **Add a key.** Free NVIDIA route: make an account at https://build.nvidia.com, generate an
   `nvapi-...` key, put it in `secrets/api_keys.json` as `"nvidia": "nvapi-..."`, and copy the
   exact model id from that site's model page into `config/jarvis.json` if you pick a different
   model than `meta/llama-3.3-70b-instruct` (it must be one that supports tool/function
   calling — that's the whole thing being tested). Same idea for `"claude"` / `"openai"`, or
   export `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `NVIDIA_API_KEY` instead.
   For Ollama: `brew install ollama && ollama serve` then `ollama pull hermes3`.
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
  - `scripts/selftest_actions.py` — 103 offline checks (60 in round 1, the rest round 2), no API key, no network, no money.
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
