# Phase-Wise Plan

Ground rule: each phase produces something you can actually run and talk to.
No phase depends on unfinished plumbing from two phases ahead. If a phase starts
feeling like "and then also build X", that's a sign to split it, not push through.

---

## Phase 0 — Scaffolding & environment

**Goal:** an empty daemon that proves the OS will let you do the invasive stuff
before you invest in the AI parts.

- macOS project skeleton (Python is the pragmatic choice: best library support for
  audio/vision/ML glue; a menu-bar shell around it via `rumps` or similar).
- Get mic permission and webcam permission granted and working from a background
  process (not just a foreground app) — this is the part most likely to have
  annoying macOS privacy-prompt gotchas, so validate it first.
- Hello-world loop: process starts at login, sits in the menu bar, logs to a file.
- Decide the on-disk layout: `config/`, `memory/` (local state), `secrets/`
  (see doc 04), `logs/`.

**Definition of done:** a background process that survives a reboot, shows a menu
bar icon, and can print "mic: ready" / "camera: ready".

---

## Phase 1 — Voice in → text out (no intelligence yet)

**Goal:** prove the ears work before wiring up the brain.

- Wake-word or push-to-talk trigger (start simple: push-to-talk via a global
  hotkey; wake-word is a Phase 8 nicety, not a blocker).
- Speech-to-text: local Whisper (`whisper.cpp` or `faster-whisper`) so this works
  offline and costs nothing per utterance.
- Transcript shows up in a log / simple UI. No action is taken on it yet.

**Definition of done:** you press the hotkey, say a sentence, and see the correct
transcription appear within ~1-2 seconds.

---

## Phase 2 — The brain: model-agnostic understanding

**Goal:** turn a transcript into a *decision* — what does the user want, and which
tool (if any) should handle it — using whichever LLM backend you point it at.

- Build the **model router** (see System Design doc): one interface, three
  interchangeable backends — Claude API, OpenAI API, local Ollama model.
  (Added 2026-09-05: a fourth, `nvidia`, pointing the OpenAI-dialect backend at
  NVIDIA's free NIM endpoint. Any OpenAI-compatible provider is now a config
  entry rather than new code — but the Definition of done below still means the
  three backends originally named.)
- Define Jarvis's tool-calling contract: a small, fixed set of tools the model can
  invoke (open_url, open_app, run_claude_code, fill_login_form, ...) — start with
  2-3 real tools, not the full list.
- Intent parsing loop: transcript → model call with tool definitions → either a
  direct spoken reply or a tool call.
- No actual system actions yet except the safest one: opening a URL or app.

**Definition of done:** "open github.com" and "open Claude Code" reliably resolve
to the right tool call, on the configured backend, through the real voice path.

*Amended 2026-09-05, after the phase was built.* This originally read "on all
three backends, so you can compare them honestly". That was the right *intent* —
prove the router is genuinely model-agnostic rather than Claude-shaped — but it
tied the criterion to three specific vendors, and the machine ended up with a
free NVIDIA NIM key instead of Claude/OpenAI keys. Running Jarvis's
OpenAI-dialect backend against a **non-OpenAI** endpoint tests the same property
the original wording was reaching for (arguably harder: it's the case where a
"compatible" API isn't quite), so the criterion is now backend-agnostic. The
comparison itself isn't abandoned — it just stops being a gate on Phase 2, and
happens whenever a second backend gets credentials. `scripts/try_brain.py`
exists to run it in one command.

---

## Phase 3 — Real actions & the Claude Code sub-agent

**Goal:** the two example commands from your original ask both work end to end.

- `open_app` / `open_url` tool: real `open`/`osascript` calls.
- `run_claude_code` tool: shells out to the Claude Code CLI in a target project
  directory with a task prompt (e.g. "find and fix this bug"), streams its output
  back, and reports completion. This is Jarvis's first *sub-agent* — it doesn't
  need to know how Claude Code works internally, only how to launch it and relay
  the result.
- Basic browser automation tool (Playwright) for "open this portal" style commands,
  landing on the page — login itself is Phase 4.

**Definition of done:** "open project X, use Claude Code to find this bug" actually
launches Claude Code against project X with your spoken description as the prompt.

---

## Phase 4 — Voice out + credentialed login

**Goal:** Jarvis talks back, and can take a spoken username/password and use it.

- Text-to-speech: start local (Piper or macOS `say`/AVSpeechSynthesizer for a free,
  decent voice) with a cloud option (ElevenLabs/OpenAI TTS) behind the same
  model-agnostic pattern as the brain, so you can compare quality later.
- Credential capture flow, spelling-out mode, storage and redaction — this is
  fully specified in doc 04, build it exactly as described there, don't shortcut it.
- `fill_login_form` tool: Playwright locates the username/password fields on the
  already-opened portal and types the captured credential, then pauses for you to
  confirm/submit (don't auto-submit until you trust it).

**Definition of done:** "open this portal, username is jsmith, password is..." logs
you into a real test site, out loud, without you touching the keyboard, and with
the credential never appearing in a log file.

---

## Phase 5 — Hand gestures

**Goal:** the webcam becomes a second input channel alongside voice.

- MediaPipe Hands (runs locally, no cloud, real-time on a laptop webcam) for
  landmark detection.
- Define a small gesture vocabulary first (e.g. open palm = stop/cancel, thumbs up
  = confirm, swipe = dismiss) — resist the urge to support many gestures before the
  few you have are reliable.
- Gestures map to the *same* tool-calling contract as voice (a gesture is just
  another way to trigger `confirm`, `cancel`, etc.), so the brain doesn't need a
  separate code path per input modality.

**Definition of done:** thumbs-up reliably confirms a pending action, in normal
room lighting, without false triggers during a 10-minute session.

---

## Phase 6 — Memory & personalization

**Goal:** Jarvis remembers things across sessions instead of starting blank every time.

- Local memory store (simple: SQLite or flat files) for: known portals/projects
  and their aliases ("my project" → path), preferences (preferred voice, preferred
  model backend per task type), and a rolling conversation history window.
- Retrieval: pull relevant memory into the model context per request rather than
  dumping everything in (keeps latency and cost sane).

**Definition of done:** you can say "open my project" once you've told it what
"my project" means, days later, without repeating the full path.

---

## Phase 7 — Multi-agent orchestration

**Goal:** Jarvis becomes a coordinator, not a single model doing everything.

- Split responsibilities into sub-agents with narrow jobs: a coding agent (wraps
  Claude Code), a browser/automation agent (wraps Playwright tools), a
  research/lookup agent. The main Jarvis loop routes to these rather than trying
  to do everything in one prompt.
- This is where a framework decision (LangGraph / CrewAI / custom / Claude Agent
  SDK) actually starts to matter — deferred to Phase 7 on purpose, see doc 03 for
  why building this earlier would be premature.

**Definition of done:** a single spoken request that needs two sub-agents (e.g.
"look up how this library's API changed, then fix the bug in my project") completes
without you manually sequencing the steps.

---

## Phase 8 — Polish: always-on companion

**Goal:** stop feeling like a dev tool, start feeling like a companion.

- Wake-word detection (openWakeWord/Porcupine) to replace push-to-talk.
- Menu bar UI: status (listening/thinking/speaking), quick history, mute toggle.
- Notifications for long-running tool calls (e.g. Claude Code still working).
- Latency pass: caching, streaming responses, picking faster models for
  low-stakes intent parsing vs. slower/smarter ones for actual coding tasks.

---

## Phase 9 (stretch) — Proactive behavior

Ideas to revisit only once 0–8 are solid, not before: calendar/email awareness,
scheduled check-ins, learning your habits well enough to suggest actions instead
of only reacting to commands. Deliberately vague — this is where scope creep
lives, so it stays a "later" bucket until the fundamentals are boring and reliable.
