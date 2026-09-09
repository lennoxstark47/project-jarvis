# Jarvis — Personal AI Companion

Your own all-around AI companion: understands natural-language voice commands,
watches for hand gestures through the webcam, executes real tasks (opening portals,
logging you in, driving Claude Code to fix bugs), and talks back in a natural voice.

Runs as a macOS background daemon. The "brain" is model-agnostic — you can point it
at Claude, GPT, OpenRouter, NVIDIA NIM, or a local Ollama model, and swapping between
them is one line in `.env` (`JARVIS_BACKEND=openrouter`), not a code change.

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

Phase 5 (hand gestures) is built and **paused on purpose**: a thumbs-up confirms
whatever Jarvis just asked about and an open palm cancels it, through the same
`jarvis.confirm` door a spoken "yes" goes through, with the camera on only while
something is actually waiting to be confirmed. Two verification runs are left, both
needing a hand in front of a lens. Focus is back on the voice path, which has the
older open items — see the tracker.

Phase 4 is built: Jarvis speaks its replies, and a username/password said out loud
is lifted out of the transcript locally, typed into a real login form in *your* own
Firefox, and saved to the macOS Keychain once the site accepts it — with the model
never seeing the characters and nothing submitting the form until you say so. One
item is carried forward: the four-turn spoken login has never completed start to
finish from the microphone, blocked by the loop having no memory between utterances
(Phase 6). Phase 6 has now landed and unblocked it; the run itself still needs
doing. See the tracker.

Phase 6 is built: Jarvis remembers. A SQLite store under `memory/` holds names you
teach it ("my project" → a folder, "the practice portal" → a URL), preferences like
which voice reads replies out, and a short rolling window of the conversation — so
"open my project" works in a session started days later, and "open that one instead"
means something. Only the facts your sentence actually names are put in front of the
model, and anything the credential layer touches is masked before it reaches the
disk.

Phase 7 is built: Jarvis is a coordinator. Its tools are divided between three
sub-agents — a coding lane (Claude Code inside one of your projects), a browser
lane (the page it can see and type into), and a new research lane that looks
things up on the public web — and one sentence can use two of them without you
sequencing anything. "Look up the latest version of X, then have Claude Code
check what my project pins" runs the lookup, hands its findings to the coding
agent itself, and comes back with one spoken answer. Which brain runs a turn now
depends on what the turn is about, and no agent framework was adopted: doc 03
says why.

`docs/00-IMPLEMENTATION_TRACKER.md` is the authoritative, per-task version of this
— including what's been proven live versus only written.

## Decisions already locked in

- **Model backend:** pluggable/model-agnostic. Claude, GPT, OpenRouter, NVIDIA NIM and
  local Ollama models all sit behind one interface, selected by `JARVIS_BACKEND` in
  `.env` (copy `.env.example`), so you can A/B them per-task instead of committing early.
  Any other OpenAI-compatible provider needs only a `base_url` under `backends` in
  `config/jarvis.json` — no new code.
- **Credentials:** you speak (or spell out) a username/password to Jarvis in the
  moment and it types it in for you. See doc 04 for how we keep that from being
  reckless.
- **Runtime:** macOS background app/daemon with mic + webcam access (not a browser tab).
