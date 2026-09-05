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

Planning stage — no code yet. These docs are the spec we'll build against. Expect
to revise them as Phase 1 teaches us things the plan got wrong (it will).

## Decisions already locked in

- **Model backend:** pluggable/model-agnostic. Claude, GPT, and local Ollama models
  all sit behind one interface so you can A/B them per-task instead of committing early.
- **Credentials:** you speak (or spell out) a username/password to Jarvis in the
  moment and it types it in for you. See doc 04 for how we keep that from being
  reckless.
- **Runtime:** macOS background app/daemon with mic + webcam access (not a browser tab).
