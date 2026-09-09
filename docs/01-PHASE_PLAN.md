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

- `open_app` / `open_url` tool: real `open` calls. (Amended 2026-09-07: this
  originally said "`open`/`osascript`". `open -a` already both launches a cold app
  and fronts a running one, and osascript takes a *script* where `open` takes an
  argument — so staying on `open` is what keeps a mis-transcribed app name from
  being executable. No osascript was added.)
- `run_claude_code` tool: shells out to the Claude Code CLI in a target project
  directory with a task prompt (e.g. "find and fix this bug"), streams its output
  back, and reports completion. This is Jarvis's first *sub-agent* — it doesn't
  need to know how Claude Code works internally, only how to launch it and relay
  the result.
- **Added 2026-09-07: spoken project name → directory, with a containment
  boundary.** The bullet above says `run_claude_code(project_path, task)`, but a
  voice transcript never contains a path, and letting the model hand a raw path to
  a subprocess is how one mis-transcribed word points an editing agent at your home
  directory. So a resolver (`jarvis/projects.py`) matches spoken names against the
  immediate children of configured project roots and refuses anything outside them.
  Doc 02's memory store (Phase 6) should feed this resolver, not replace it.
- Basic browser automation tool (Playwright) for "open this portal" style commands,
  landing on the page — login itself is Phase 4.
- **Added 2026-09-07: a live status line in the menu bar.** Doc 02's output layer
  asks for a "running a sub-agent" surface, and this is the first phase with a tool
  that runs for minutes — without it, a working sub-agent and a hung Jarvis look
  identical. Implemented as an `on_status` callback threaded agent → tools →
  sub-agent, so Phase 4's TTS can subscribe to the same stream.

**Definition of done:** "open project X, use Claude Code to find this bug" actually
launches Claude Code against project X with your spoken description as the prompt.

**Amended 2026-09-08 — the sub-agent is *visible* by default.** Round 1 ran Claude
Code headless and only spoke the result. On seeing that, the user asked for the
dramatic version instead: a real terminal window that opens, `cd`s into the
project and runs Claude Code in front of them, so they can watch and take the
keyboard. That's `actions.claude_code.mode: "terminal"`, and it's the default;
`"headless"` keeps the old behaviour. It still reports back — Jarvis picks the
session id before launching and tails Claude Code's own session transcript, so
the visible window costs nothing in relayed detail.

**Also added 2026-09-08: Jarvis asks where a project is, out loud.** When a name
doesn't resolve, the request is parked (`jarvis/followup.py`) and the next thing
said gets one chance to be the answer, resolved locally against the real
filesystem and then remembered as an alias. This is a deliberately narrow slice
of Phase 6's memory store, taken early because the alternative — "I couldn't find
that" and a dead end — made the sub-agent unusable for any project not sitting in
a configured root.

**Safety decision, corrected 2026-09-08 (this is the important one).** The plan
said, and this doc previously recorded, that the sub-agent defaults to Claude
Code's `plan` permission mode — "read and reason, don't edit". **That was
measured and it is false**: under `plan`, a terminal-mode run rewrote a source
file. Nor was removing the write tools enough — Claude Code used Bash instead and
said so. What actually holds is `--restricted --strict-mcp-config
--disallowedTools=Edit,Write,...`, verified by md5 on the same task. The knob is
now `actions.claude_code.allow_edits` (default false), and turning it true is how
you get an agent that fixes rather than one that finds. The cost of false is that
Claude Code loses Bash and investigates with Read/Grep/Glob only. Any future
phase that adds a "safe mode" to anything should take the lesson rather than the
flags: a safety property nobody measured is a safety property you don't have.

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
  - *Built 2026-09-08 as `jarvis/confirm.py`, a general armed-action-plus-answer
    module rather than a pause inside the login tool.* Same behaviour, and it is
    the seam Phase 5 plugs into — see that phase's note below.

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
  - *Phase 4 already built that contract:* `jarvis.confirm` holds one armed
    action and `resolve(True/False)` runs or drops it, which is what a spoken
    "yes" goes through today. A thumbs-up is `confirm.resolve(True)` and needs
    to know nothing about what it confirmed. Phase 5's work is the perception
    half (MediaPipe → a stable gesture), not the plumbing.

**Definition of done:** thumbs-up reliably confirms a pending action, in normal
room lighting, without false triggers during a 10-minute session.

**Built 2026-09-09 — three notes on what the plan didn't anticipate.**

*No landmark geometry was written.* The bullet above says MediaPipe Hands "for
landmark detection", implying Jarvis would decide what a thumbs-up is from 21
points. It doesn't need to: MediaPipe's Tasks API ships a canned classifier
whose labels are already this vocabulary, and it reports a confidence — which
is what tells a held gesture from a hand passing through the shape. Doc 02's
matching bullet is revised too. **Pin mediapipe to the 0.10 line**: 1.0.x
aborts the whole process on any hand graph on macOS/arm64 (requirements.txt
carries the detail).

*"Gesture mode is active" got an exact definition: while a confirmation is
pending.* Doc 02 asks for the camera to be open only then, without saying when
"then" is. Tying it to an armed `jarvis.confirm` action means the camera light
is on only in a window that always corresponds to a question Jarvis just asked
out loud — and it disposes of most of the false-trigger requirement, because
for the majority of a session there is nothing a gesture could resolve.

*The plan's prediction about the plumbing was right.* "Phase 5's work is the
perception half, not the plumbing" — no file from Phases 1-4 changed. The
gesture handler calls `confirm.resolve()`, the same call a spoken "yes" makes,
and knows nothing about what it confirmed.

**And one correction to the Definition of done's own difficulty.** "Without
false triggers" is the hard half, not "thumbs-up confirms". At the first
settings tried (a 0.6s hold) a soak fired a false *cancel* inside a minute,
because an open palm is the most common incidental hand shape in front of a
laptop. The hold is now ~1.2s. Judge the two failure directions separately: a
false *cancel* drops a pending action and Jarvis asks again; a false *confirm*
submits a login form.

---

## Phase 6 — Memory & personalization

**Goal:** Jarvis remembers things across sessions instead of starting blank every time.

- Local memory store (simple: SQLite or flat files) for: known portals/projects
  and their aliases ("my project" → path), preferences (preferred voice, preferred
  model backend per task type), and a rolling conversation history window.
- Retrieval: pull relevant memory into the model context per request rather than
  dumping everything in (keeps latency and cost sane).
- **Added 2026-09-09, during the phase:** two tools, `remember` and
  `open_project`. Not in the original list, and worth being explicit about
  because this phase's own ground rule is that the tool list grows only when a
  phase says so. The Definition of done below is a sentence with two halves —
  *telling* Jarvis what "my project" means, and *using* it — and nothing in
  Phases 0-5 could do either. Before this, an alias could only be learned as a
  side effect of Jarvis asking where a project was (`jarvis/followup.py`), and
  nothing opened a folder at all.

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
