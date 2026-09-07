# Implementation Tracker

**Rule:** read this doc before starting any work on Jarvis, to see what's already
done and what's left. After finishing any piece of work, update this doc — check
off what's done, and add a dated note if anything deviated from the original plan
in `01-PHASE_PLAN.md` (scope changed, a task got split, something turned out
harder/easier than expected). Tasks map 1:1 to the bullets and "Definition of
done" in `01-PHASE_PLAN.md` — if you add a task here that isn't in that doc, add
it there too so the two stay in sync.

**Current focus:** Phase 2 is ✅ Done. Ready to start Phase 3 — real actions & the
Claude Code sub-agent.

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
