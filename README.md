# Jarvis — Personal AI Companion

Your own all-around AI companion: understands natural-language voice commands,
watches for hand gestures through the webcam, executes real tasks (opening portals,
logging you in, driving Claude Code to fix bugs), and talks back in a natural voice.

Runs as a macOS background daemon. The "brain" is model-agnostic — you can point it
at Claude, GPT, or a local Ollama model and swap between them as you learn what
works best.

## Reading order

0. **[docs/00-IMPLEMENTATION_TRACKER.md](docs/00-IMPLEMENTATION_TRACKER.md)** — what's
   actually done vs. still open, phase by phase. Check this **before** starting any
   work, and update it **after** finishing any — see CLAUDE.md, this isn't optional.
1. **[docs/01-PHASE_PLAN.md](docs/01-PHASE_PLAN.md)** — the roadmap. Start here. Each
   phase is small enough to actually finish before starting the next one.
2. **[docs/02-SYSTEM_DESIGN.md](docs/02-SYSTEM_DESIGN.md)** — how the pieces fit
   together: input (mic/webcam) → understanding → decision → action → voice output.
3. **[docs/03-AGENTS_AND_MODELS.md](docs/03-AGENTS_AND_MODELS.md)** — the AI-agent
   concepts explained from scratch (what's an "agent", what's MCP, what is "Hermes",
   which framework to use and why).
4. **[docs/04-CREDENTIALS_AND_SECURITY.md](docs/04-CREDENTIALS_AND_SECURITY.md)** —
   how Jarvis is allowed to hear a username/password out loud and use it, without
   that becoming the reason you get hacked.

## Current status

Phases 0-3 are built and running: a menu-bar background app with mic/camera access,
push-to-talk voice capture with local Whisper transcription, a model-agnostic brain
that turns a transcript into a tool call, and an action layer that opens apps and
URLs, drives its own browser, and hands coding tasks to Claude Code as a sub-agent.

Phase 4 is built: Jarvis speaks its replies, and a username/password said out loud
is lifted out of the transcript locally, typed into a real login form in *your* own
Firefox, and saved to the macOS Keychain once the site accepts it — with the model
never seeing the characters and nothing submitting the form until you say so. One
item is carried forward: the four-turn spoken login has never completed start to
finish from the microphone, blocked by the loop having no memory between utterances
(Phase 6). See the tracker. Phase 5 (hand gestures) is next.

`docs/00-IMPLEMENTATION_TRACKER.md` is the authoritative, per-task version of this
— including what's been proven live versus only written.

## Decisions already locked in

- **Model backend:** pluggable/model-agnostic. Claude, GPT, and local Ollama models
  all sit behind one interface so you can A/B them per-task instead of committing early.
- **Credentials:** you speak (or spell out) a username/password to Jarvis in the
  moment and it types it in for you. See doc 04 for how we keep that from being
  reckless.
- **Runtime:** macOS background app/daemon with mic + webcam access (not a browser tab).
