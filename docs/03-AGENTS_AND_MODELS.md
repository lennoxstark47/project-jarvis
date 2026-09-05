# Agents & Models, Explained From Scratch

You said to treat you as a novice on AI agents/automation specifically (not on
software or Claude Code) — this doc is the concept primer the other two docs
assume you've read.

## What is an "agent", really?

Strip away the hype: an agent is **a loop** around an LLM that can:

1. See a goal (your spoken command).
2. Optionally call a **tool** (a real function — open a URL, run a script, search
   the web) instead of just replying with text.
3. See the tool's result, and decide what to do next — reply to you, or call
   another tool.
4. Repeat until it decides it's done.

That's it. There's no separate "agent" technology you need to acquire — it's a
prompt, a list of tool definitions the model is told it can invoke, and a
while-loop in your code that executes whatever the model asks for and feeds the
result back in. Frameworks (below) exist to save you from re-writing that loop,
error handling, and bookkeeping — they don't do anything a loop couldn't do.

This is exactly what the **Agent Loop** box in `02-SYSTEM_DESIGN.md` is.

## Where "Hermes" fits in

You'd heard the name but not what it does — here's the actual answer: **Hermes**
is a series of open-source models from **Nous Research** (e.g. *Hermes 3*, built
on top of Llama), specifically fine-tuned to be good at **tool-calling and
agentic behavior** — the exact "decide what tool to call" skill described above.
It's not a framework or a product you install; it's one option for the model
sitting inside your Model Router when you want a free, local, tool-calling-capable
model instead of a paid API.

Practically: if you run it via **Ollama** (`ollama run hermes3`), it becomes just
another backend behind the Model Router, same as pointing that router at Claude
or GPT. Other reasonable local alternatives to compare it against: **Llama 3.1/3.3**
(Meta, general-purpose, also tool-calling-capable) and **Qwen2.5** (strong at
tool-calling, smaller sizes run well on a laptop). Don't treat "which local model"
as a decision to agonize over now — it's a config value in the router, exactly as
cheap to swap as switching from Claude to GPT.

## Model-agnostic by design

Your instinct — "I can use either Claude or GPT or Ollama, we'll try what works
best" — is the right call, and it's *why* the System Design puts a Model Router
between the Agent Loop and every backend. Two ways to build that router:

- **Hand-roll it:** one Python function per backend, all matching the same input/
  output shape. Maybe 30-60 lines each. Full control, zero extra dependency, and
  honestly fine at this scale.
- **Use `LiteLLM`:** an open-source library that already speaks the Claude API,
  OpenAI API, and Ollama's local API behind one call signature
  (`litellm.completion(model=..., messages=..., tools=...)`). Saves you the
  boilerplate of #1; worth it once you're flipping between backends often enough
  that the hand-rolled version starts to feel like busywork.

Recommendation: hand-roll it for Phase 2 (you'll understand the system better for
having written it), switch to LiteLLM later only if the maintenance becomes annoying.

Cost/latency/privacy reality check, so "try what works best" has real tradeoffs
to weigh, not just vibes:

| Backend | Cost | Speed | Quality (agentic/tool-use) | Needs internet |
|---|---|---|---|---|
| Claude API | pay per token | fast | very strong, especially for the `run_claude_code` coding tool | yes |
| GPT API | pay per token | fast | strong | yes |
| Local Ollama (Hermes/Llama/Qwen) | free (uses your CPU/GPU) | depends on your hardware | good enough for simple intent-routing, weaker for genuinely hard tasks | no |

A practical split worth trying once Phase 2 is up: local model for cheap, frequent
decisions (which tool to call, is this a yes/no confirmation), Claude specifically
for the `run_claude_code` path (you already trust it there), GPT as the easy A/B
comparison point. This is a hypothesis to test with real usage, not a rule to lock in.

## Frameworks: why the plan deliberately avoids one early

Popular options you may run into while researching this:

- **LangChain / LangGraph** — the most widely used; LangGraph specifically models
  agents as graphs of steps, good once you have multiple sub-agents with real
  branching logic between them.
- **CrewAI** — opinionated multi-agent framework built around "roles" (e.g.
  researcher, writer) collaborating.
- **AutoGen / AG2** — Microsoft's multi-agent conversation framework.
- **Claude Agent SDK** — Anthropic's own SDK for building agents, natural fit
  since you already use Claude Code (in fact, Claude Code itself is built on
  this pattern) — worth a look specifically for the `run_claude_code` /
  multi-agent phases if you end up leaning Claude-heavy.

None of these are wrong choices. The plan avoids adopting one in Phase 0-6 on
purpose: with one model, a handful of tools, and no sub-agents yet, a framework
adds indirection you'd have to learn (its abstractions, its bugs, its version
churn) for a loop you could write yourself in an afternoon. The framework
decision is deferred to **Phase 7** (multi-agent orchestration) in the phase
plan, because that's the point where hand-rolled coordination between multiple
agents actually starts to hurt and a framework's graph/routing abstractions start
paying for themselves. Revisit this section then.

## MCP — you already have a head start here

You use Claude Code, so you've likely already encountered **MCP (Model Context
Protocol)** — the standard Claude Code (and this session) uses to give a model
access to external tools/data sources. Worth knowing for Jarvis: your tool layer
(`open_app`, `run_claude_code`, `fill_login_form`, ...) could eventually be
exposed as an MCP server instead of hand-wired Python functions — that would let
*any* MCP-compatible client (Claude Code included) reuse Jarvis's tools, not just
Jarvis's own agent loop. Not needed for Phase 0-6; flagged here because it's a
natural refactor once the tool layer stabilizes and you want it reusable.

## Glossary (quick reference)

- **Tool / function calling:** giving the model a list of typed functions it can
  request to invoke, instead of only replying in text.
- **ReAct loop:** the "reason, then act, then observe the result, repeat" pattern
  — the formal name for the Agent Loop described above.
- **Sub-agent:** an agent invoked *by* another agent as one of its tools (Jarvis
  treats Claude Code as a sub-agent — it calls it, waits, and relays the result).
- **Router / model-agnostic layer:** the abstraction that lets you swap which LLM
  answers a request without changing the code that asks the question.
- **Wake word:** a locally-detected trigger phrase (e.g. "Hey Jarvis") that starts
  listening, so you're not holding a hotkey forever — a Phase 8 concern, not needed
  to prove the concept.
