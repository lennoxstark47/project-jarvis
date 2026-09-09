"""
Configuration & API-key resolution — Phases 2-6.

Everything Jarvis needs to pick a brain lives in one JSON file,
`config/jarvis.json` (created on first read with the defaults below), so
swapping backends is the config change doc 03 promised rather than a code
edit:

    {
      "backend": "nvidia",
      "backends": {
        "claude": {"model": "claude-opus-5", "effort": "low", "timeout": 60},
        "openai": {"model": "gpt-4o", "base_url": null, "timeout": 60},
        "nvidia": {
          "model": "openai/gpt-oss-20b",
          "base_url": "https://integrate.api.nvidia.com/v1",
          "timeout": 60
        },
        "ollama": {"model": "granite4.1:3b", "host": "http://localhost:11434"}
      }
    }

Phase 3 added an `actions` block to the same file — what Jarvis is allowed to
*do* (which projects `run_claude_code` may touch, what permissions the Claude
Code sub-agent runs with, how the browser is launched), as opposed to which
model decides to do it.

See DEFAULTS below for what each field means and why it's set the way it is;
a file that only overrides some of them still inherits the rest.

Phase 4 added a `speech` block (which voice reads the replies out) and an
`actions.login` block (what the login tool is allowed to do). Phase 5 added a
`gestures` block (when the camera is on, and how sure a gesture has to be).
Phase 6 added a `memory` block (jarvis/memory.py — where the SQLite store
lives, and how much of it reaches a prompt).

Phase 6 also added a **fourth layer**, below the environment: a preference the
user set *out loud* ("use the Daniel voice from now on") is stored in the
memory database and applied over the file. It sits below the environment
deliberately — a variable in a launchd plist is something you configured on
purpose, and a sentence you said months ago should not quietly beat it. Only
the paths in PREFERENCE_KEYS can be reached this way; see the comment there for
why that list is short and closed.

Phase 5 added an **environment layer** on top of that file, so switching brains
is a one-line edit rather than a JSON surgery: `.env` in the project root (or
any real environment variable) wins over `config/jarvis.json`, which wins over
DEFAULTS. `JARVIS_BACKEND=openrouter` is the whole switch; see ENV_OVERRIDES
below for the per-backend knobs (`JARVIS_MODEL`, `JARVIS_OLLAMA_HOST`, ...).
`.env` is read once at import and never overrides a variable the process was
already started with, so a launchd plist's `EnvironmentVariables` still wins.

API keys are deliberately *not* in that file — it's committed-adjacent and
easy to `cat` into a terminal by accident. They're read from the environment
first, then from `secrets/api_keys.json` (the `secrets/` directory Phase 0
created and .gitignore'd). Note that Phase 4's Keychain work
(docs/04-CREDENTIALS_AND_SECURITY.md, jarvis/vault.py) is about the *user's
site credentials*, not these — Jarvis's own service keys stay here, since a
launchd-started process reading them must not need a Keychain unlock prompt
before it can answer anything at all.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.config")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "jarvis.json"
SECRETS_PATH = PROJECT_ROOT / "secrets" / "api_keys.json"
ENV_PATH = PROJECT_ROOT / ".env"

DEFAULTS: dict[str, Any] = {
    # Which backend the agent loop uses by default. One of the keys below.
    "backend": "claude",
    "backends": {
        "claude": {
            "model": "claude-opus-5",
            # Effort trades thoroughness for tokens/latency. Intent routing over
            # a two-tool list is not a hard problem, and this call sits directly
            # in the push-to-talk latency path, so "low" is the right default —
            # raise it if real commands start getting mis-routed.
            "effort": "low",
            # Seconds before a request is abandoned. Jarvis speaks the reply,
            # so a backend that hangs is worse than one that fails: the SDKs
            # default to ten minutes, which would leave the menu bar silently
            # stuck on the old reply for that long.
            "timeout": 60,
        },
        "openai": {
            "model": "gpt-4o",
            # Any OpenAI-*compatible* endpoint works here (see the "nvidia"
            # entry below); null means the real OpenAI API.
            "base_url": None,
            "timeout": 60,
        },
        # NVIDIA's NIM API is OpenAI-compatible and has a free tier, so it's
        # the same backend class pointed somewhere else. Get a key (starts
        # with "nvapi-") from https://build.nvidia.com; list what that key can
        # actually reach with GET {base_url}/models, because the catalogue on
        # the website is wider than any one key's access.
        #
        # gpt-oss-20b measured 2.7s/3.7s on Phase 2's two test commands with
        # correct tool calls both times. Runner-up worth trying:
        # nvidia/nemotron-3-super-120b-a12b (2.0-3.9s, also correct).
        # DO NOT use a vision NIM here (e.g. meta/llama-3.2-90b-vision-instruct):
        # sending it a `tools` array makes the endpoint hang until timeout
        # rather than returning an error.
        "nvidia": {
            "model": "openai/gpt-oss-20b",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "timeout": 60,
        },
        # OpenRouter is one key in front of most of the industry's models, and
        # also OpenAI-dialect — so, like nvidia above, it's the same backend
        # class aimed somewhere else. Keys come from
        # https://openrouter.ai/keys and are normally `sk-or-v1-...`.
        #
        # nex-agi/nex-n2.5-mini:free is the current default, measured
        # 2026-09-09: correct tool calls on all four test commands in 2.7-7.5s,
        # 262k context, and free. Model slugs are `vendor/model` exactly as
        # https://openrouter.ai/models lists them, and the `:free` suffix is
        # part of the slug — dropping it asks for the paid variant. Free models
        # carry stricter rate limits, so if routing starts failing under load
        # this is the first thing to change. Whatever you pick must list
        # `tools` in its supported_parameters (check GET {base_url}/models);
        # a model without tool support can't route anything.
        #
        # `headers` is OpenRouter-specific and optional: it attributes calls to
        # this app on your dashboard and in their public rankings. Nothing
        # about a request depends on it.
        "openrouter": {
            "model": "nex-agi/nex-n2.5-mini:free",
            "base_url": "https://openrouter.ai/api/v1",
            "timeout": 60,
            "headers": {
                "HTTP-Referer": "https://github.com/local/project_jarvis",
                "X-Title": "Jarvis",
            },
        },
        "ollama": {
            # granite4.1:3b, measured 2026-09-08: 2.1 GB, tool-calling declared
            # and confirmed, and it answers a routing call in 0.7-3.7s on a
            # GTX 1660 Ti — against 40-100s on the NVIDIA free tier that day.
            # Doc 03 originally suggested hermes3 here; it was never pulled or
            # run, and naming an uninstalled model as the default only produces
            # a confusing "Ollama has no model" on a fresh config.
            # `host` may be another machine on the LAN — set OLLAMA_HOST to
            # 0.0.0.0:11434 there, since Ollama binds to localhost by default.
            "model": "granite4.1:3b",
            "host": "http://localhost:11434",
        },
    },
    # Phase 3's action layer. Everything here is about what Jarvis is allowed to
    # *do*, as opposed to which model decides to do it.
    "actions": {
        "claude_code": {
            # The CLI to launch. A full path works if `claude` isn't on the PATH
            # the app inherits (a launchd-started process has a much shorter
            # PATH than your shell — check logs/jarvis.log if it can't find it).
            "cli": "claude",
            # "terminal": open a real terminal window, cd into the project and
            # run an interactive Claude Code session you can watch and take
            # over. "headless": run it invisibly and only report the answer.
            # Terminal mode still reports back — it tails the session's own
            # transcript rather than the process's stdout (jarvis/claude_code.py).
            "mode": "terminal",
            # Which terminal to drive in that mode: "auto" (iTerm2 if installed,
            # else Terminal.app), "iTerm2", or "Terminal". macOS will ask for
            # Automation permission the first time.
            "terminal_app": "auto",
            # Terminal mode only. An interactive session never exits, so
            # "finished" means: it wrote prose (not a tool call) and has been
            # quiet this many seconds. Raise it if Jarvis speaks too early on
            # tasks with long pauses.
            "quiet_seconds": 20,
            # THE safety knob. false runs Claude Code with --restricted
            # --strict-mcp-config --disallowedTools=Edit,Write,... so a misheard
            # command investigates and reports instead of rewriting something.
            # Set it true when you want fixes rather than findings; the cost of
            # false is that Claude Code loses Bash, so it investigates with
            # Read/Grep/Glob only.
            #
            # Those exact flags are what survived testing. Two weaker guards
            # were tried on 2026-09-08 and both let a source file get rewritten:
            # --permission-mode plan, and then removing the write tools (which
            # Claude Code routed around with Bash, and told us it had). See
            # READ_ONLY_FLAGS in jarvis/claude_code.py.
            "allow_edits": False,
            # Passed through to --permission-mode. What Jarvis *asks* for, not
            # what's enforced — see allow_edits above. Accepted values are the
            # CLI's: plan, acceptEdits, auto, manual, dontAsk, bypassPermissions.
            "permission_mode": "plan",
            # null = whatever model the CLI is configured to use.
            "model": None,
            # Seconds before Jarvis stops the sub-agent and reports how far it
            # got. Real tasks take minutes, hence 10x the model-call timeout.
            "timeout": 600,
            # Optional hard spend cap per run (passed as --max-budget-usd).
            # null = no cap. A short investigation ran ~$0.16 in testing.
            "max_budget_usd": None,
            # Anything else to append to the argv, e.g. ["--add-dir", "/path"].
            "extra_args": [],
        },
        "browser": {
            # Which browser Jarvis drives.
            #
            # "system-firefox" is *your* Firefox — the one in /Applications, with
            # your profile, your logins and your extensions — driven through
            # geckodriver, which is Mozilla's own driver and the only supported
            # way to automate a stock Firefox build. Selenium fetches the driver
            # itself on first use, so there's nothing to install by hand.
            #
            # "chromium" / "firefox" / "webkit" use Playwright's own downloaded
            # browsers instead, with a profile under memory/. Self-contained, but
            # not the browser in your Dock; each must be downloaded first with
            # `python3 -m playwright install <name>`.
            #
            # Note that Playwright *cannot* drive your real Firefox whatever this
            # says: its Firefox is a patched build speaking a protocol stock
            # Firefox doesn't implement (verified 2026-09-08).
            "engine": "system-firefox",
            # system-firefox only. null finds Firefox in /Applications (including
            # Developer Edition); set it to the executable inside the .app if
            # yours lives somewhere else.
            "binary": None,
            # system-firefox only. A profile name from Firefox's profiles.ini
            # ("default", "dev-edition-default"), a full path, or null for
            # whichever profile Firefox itself opens — which is the one you
            # actually use, hence the default.
            #
            # A profile can only be open in one Firefox at a time, so Jarvis
            # can't use this one while your own Firefox has it open; it says so
            # and asks you to quit. Naming a *different* profile here lets the
            # two run side by side, at the cost of not sharing your logins.
            "profile": None,
            # False so you can watch it and take over — the whole point of
            # open_portal (vs open_url) is a page Jarvis and you share.
            "headless": False,
            # Per-navigation timeout in seconds.
            "timeout": 30,
            # Playwright engines only: their persistent profile, one directory
            # per engine. Cookies and logins survive restarts, so a login happens
            # once per site rather than every morning. Holds real session
            # cookies — keep it under memory/, which is gitignored.
            "user_data_dir": "memory/browser",
        },
        # Phase 4's login layer — see docs/04-CREDENTIALS_AND_SECURITY.md.
        # Note what is deliberately *not* here: any credential. Those live in
        # the macOS Keychain (jarvis/vault.py), never in this file.
        "login": {
            # Save a dictated credential to the Keychain, so the same portal
            # doesn't have to be dictated again next week. false keeps it in
            # memory for the turn and nowhere else.
            "remember": True,
            # Fill the form, then stop and ask before pressing the login button
            # (doc 04, point 4). This is what catches a mis-transcribed password
            # before it becomes a failed-login lockout, or a credential typed
            # into the wrong page. Set false only once you genuinely trust the
            # whole path — it removes that safety net entirely.
            "confirm_before_submit": True,
            # Seconds to wait for the page to settle after submitting, before
            # reporting what happened (and whether a 2FA code is now being asked
            # for, which doc 04 point 6 keeps as a human step on purpose).
            "submit_wait": 8,
        },
        "projects": {
            # Where run_claude_code is allowed to work, and the only folders a
            # spoken project name is matched against. Immediate children only.
            # This is the containment boundary for the one tool that can change
            # files — see jarvis/projects.py.
            "roots": ["~/Desktop", "~/Documents", "~/Developer", "~/Projects", "~/code"],
            # Explicit spoken-name -> path overrides, for a project whose folder
            # name is nothing like what you call it out loud. These are trusted
            # by name (they're your decision, not the model's) so they may point
            # outside the roots above.
            "aliases": {},
        },
    },
    # Phase 4's output layer (jarvis/speech.py). Same shape as "backends"
    # above, and for the same reason: which voice speaks is a config change,
    # not a code change.
    "speech": {
        # false makes Jarvis text-only again — the menu bar still shows every
        # reply. Useful in a room where a talking laptop is unwelcome.
        "enabled": True,
        # One of the keys below.
        "backend": "say",
        "backends": {
            # macOS's built-in synthesiser. Free, offline, always installed.
            # `voice` is any name from `say -v '?'` (Samantha, Daniel, Alex...);
            # null uses the system voice from System Settings -> Accessibility
            # -> Spoken Content. `rate` is words per minute; null = default.
            #
            # Worth setting. Measured 2026-09-08 on this Mac, synthesising the
            # one-line reply "Filled in, want me to submit?" (2.0s of audio):
            # system default 4.8s, Samantha 2.3s, Alex 2.0s, Daniel 1.9s,
            # Karen 1.8s, Fred 1.4s. That cost lands squarely in the pause after
            # you stop talking, so naming a voice roughly halves how long Jarvis
            # takes to answer. A name that isn't installed falls back to the
            # system voice rather than to silence (jarvis/speech.py).
            "say": {"voice": None, "rate": None},
            # Local neural TTS — much better than `say`, and still offline.
            # Needs `pip install piper-tts` plus a downloaded .onnx voice.
            # length_scale > 1 speaks slower, < 1 faster.
            "piper": {"binary": "piper", "model": None, "length_scale": None},
            # The cloud option: best quality, costs money per utterance, and
            # sends the text of Jarvis's replies to OpenAI. Credentials never
            # reach it — doc 04 keeps those local — but a reply can quote a
            # page Jarvis is looking at, so this is a real decision.
            "openai": {"model": "gpt-4o-mini-tts", "voice": "alloy", "timeout": 30},
        },
    },
    # Phase 5's second input channel (jarvis/gestures.py). A gesture doesn't get
    # its own tool or its own branch in the agent loop — it resolves the same
    # pending action a spoken "yes" resolves, so everything here is about
    # *perception*: when the camera is on, and how sure Jarvis has to be.
    "gestures": {
        # false leaves Jarvis voice-only and never opens the camera at all.
        "enabled": True,
        # Open the camera only while something is waiting to be confirmed
        # (doc 02: "don't leave the camera hot all the time — both for battery
        # and for the obvious trust reasons"). It also does most of the work of
        # the phase's "no false triggers" requirement: with nothing armed there
        # is nothing a gesture could trigger. true is the strongly recommended
        # setting; false watches continuously, which costs a permanently lit
        # camera light for the ability to gesture before being asked.
        "only_when_pending": True,
        # Which camera. 0 is the built-in one — same index permissions.py uses.
        "camera_index": 0,
        # What to ask the camera for. This Mac's webcam defaults to 1920x1080,
        # which is a lot of pixels to move per frame for a model that downsizes
        # internally anyway; 640x480 is plenty to see a hand and much cheaper.
        # null for either leaves the camera on its own default.
        "frame_width": 640,
        "frame_height": 480,
        # Frames per second to classify. 10 is plenty for a held gesture and
        # leaves the CPU alone; the recogniser itself runs in a few ms.
        "fps": 10,
        # How many consecutive frames the same gesture must hold before it
        # counts, and the main false-trigger knob.
        #
        # 12 (~1.2s at 10 fps), raised from the 6 this shipped with for half an
        # hour: a soak run on 2026-09-09 fired a false "cancel" 46 seconds in,
        # with nobody gesturing deliberately. That is what 0.6s buys you — an
        # open palm is the most common *incidental* hand shape in front of a
        # laptop (a hand resting, a hand near a face), and holding one still
        # for six frames happens by accident all the time. Holding it for
        # 1.2s much less so, while a deliberate gesture is easy to hold that
        # long once you know that's the deal.
        #
        # NOT yet re-validated by a full 10-minute soak — see the tracker's
        # Phase 5 notes. If false triggers persist, raise this before touching
        # min_confidence: a longer hold costs a little patience, while a higher
        # floor can make a gesture unfirable outright (see below).
        "hold_frames": 12,
        # Minimum classifier confidence for a *named* gesture to count at all
        # (MediaPipe's own "None" category is ignored whatever it scores).
        #
        # 0.5, and measured rather than guessed: on MediaPipe's own reference
        # photos this classifier scores a clear thumbs-up at 0.73-0.74 and a
        # clear open palm at 0.60. A 0.7 floor — the first value tried here —
        # would therefore have made "cancel" essentially unfirable while
        # looking perfectly reasonable in the config file. Stability comes from
        # hold_frames below, not from demanding a high per-frame score.
        "min_confidence": 0.5,
        # After a gesture fires, ignore everything for this long — your hand
        # travels through other shapes on its way down.
        "cooldown_seconds": 2.0,
        # MediaPipe's own hand-detection threshold, and how many hands to
        # consider. Two hands doing different things is an ambiguity with no
        # right answer, so: one.
        "min_detection_confidence": 0.5,
        "num_hands": 1,
        # Gesture -> what it means. Keys must be MediaPipe's own labels:
        # None, Closed_Fist, Open_Palm, Pointing_Up, Thumb_Down, Thumb_Up,
        # Victory, ILoveYou (a typo is warned about, not silently ignored).
        # Values go to jarvis.confirm: "confirm" presses the button, "cancel"
        # drops the action. Deliberately two entries — the phase plan's own
        # rule is to resist supporting many gestures before the few work.
        # Thumb_Down is *not* bound to cancel on purpose: an open palm is the
        # universal "stop" and is far easier to hold steadily.
        "bindings": {
            "Thumb_Up": "confirm",
            "Open_Palm": "cancel",
        },
    },
    # Phase 6's memory store (jarvis/memory.py). Doc 02: SQLite, long-term
    # (aliases, preferences) plus a short-term conversation window.
    "memory": {
        # false makes Jarvis stateless again — every utterance a fresh
        # conversation, exactly as Phases 2-5 behaved. Nothing else breaks:
        # every read degrades to empty and every write to a no-op.
        "enabled": True,
        # Relative paths hang off the project root. memory/ is gitignored, and
        # the file is created 0600 — it holds what you said out loud.
        "path": "memory/jarvis.db",
        # How many recent turns go into a prompt. Every one of these is tokens
        # on the path between you finishing a sentence and hearing an answer,
        # on a small model where that path *is* the experience. 6 is three
        # exchanges — enough for the four-turn spoken login of Phase 4, short
        # enough that a small model doesn't answer the wrong one of them.
        "history_turns": 6,
        # ...and how old a turn may be before it's dropped from that window.
        # 15 minutes, for the reason jarvis.followup and jarvis.confirm each
        # spend a docstring on: something said an hour ago must not silently
        # become context for something said now.
        "history_ttl_seconds": 900,
        # The cap on how many remembered facts one utterance may pull in.
        # Retrieval is already selective (only aliases whose names were
        # actually said), so this is the backstop that keeps a store which has
        # grown for a year from making Jarvis slower every month.
        "max_facts": 6,
    },
}

# --- the environment layer ---------------------------------------------------
#
# Which env var maps onto which config path. The point is that changing brains
# is `JARVIS_BACKEND=openrouter` in .env and nothing else — no JSON editing, no
# code edit, and the same variable works whether Jarvis is started from a
# shell, from launchd, or from the .app.
#
# Values are dotted paths into the config dict. Two shapes exist:
#   "backends.*.model" — the `*` is filled in with the *active* backend, so one
#                        JARVIS_MODEL follows whichever backend is selected.
#   "backends.nvidia.model" — an explicit, per-backend override.
# The second shape is generated for every backend name in DEFAULTS below, so a
# new backend gets its whole set of variables for free.
ENV_OVERRIDES: dict[str, str] = {
    "JARVIS_BACKEND": "backend",
    "JARVIS_MODEL": "backends.*.model",
    "JARVIS_BASE_URL": "backends.*.base_url",
    "JARVIS_TIMEOUT": "backends.*.timeout",
    "JARVIS_SPEECH_ENABLED": "speech.enabled",
    "JARVIS_SPEECH_BACKEND": "speech.backend",
    "JARVIS_SPEECH_VOICE": "speech.backends.say.voice",
    "JARVIS_ALLOW_EDITS": "actions.claude_code.allow_edits",
    "JARVIS_GESTURES_ENABLED": "gestures.enabled",
    "JARVIS_GESTURES_CAMERA_INDEX": "gestures.camera_index",
    "JARVIS_GESTURES_HOLD_FRAMES": "gestures.hold_frames",
    "JARVIS_GESTURES_MIN_CONFIDENCE": "gestures.min_confidence",
    "JARVIS_GESTURES_ONLY_WHEN_PENDING": "gestures.only_when_pending",
    "JARVIS_MEMORY_ENABLED": "memory.enabled",
    "JARVIS_MEMORY_PATH": "memory.path",
    "JARVIS_MEMORY_HISTORY_TURNS": "memory.history_turns",
    "JARVIS_MEMORY_HISTORY_TTL": "memory.history_ttl_seconds",
}

for _name, _entry in DEFAULTS["backends"].items():
    for _field in _entry:
        if isinstance(_entry[_field], dict):  # e.g. openrouter's `headers`
            continue
        ENV_OVERRIDES[f"JARVIS_{_name.upper()}_{_field.upper()}"] = (
            f"backends.{_name}.{_field}"
        )
del _name, _entry, _field

# env var checked first for each backend's key
ENV_KEYS = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge `override` into a copy of `base`, recursing into nested dicts.

    Means a config file that only sets `{"backend": "ollama"}` still gets every
    default model/host, instead of blanking them out.
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_dotenv(path: Path = ENV_PATH) -> dict[str, str]:
    """Read `.env` into os.environ, without clobbering what's already set.

    Deliberately a dozen lines rather than a dependency: the format Jarvis
    needs is `KEY=value` per line, `#` comments, optional surrounding quotes,
    and an optional `export ` prefix so the same file can be `source`d in a
    shell. Anything fancier belongs in the shell, not here.

    Real environment variables win, so `JARVIS_BACKEND=ollama .venv/bin/python3
    run.py` overrides the file for one run, and a launchd plist's
    `EnvironmentVariables` overrides it permanently.
    """
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        logger.error("Could not read %s (%s).", path, exc)
        return {}

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)
    if loaded:
        logger.debug("Loaded %d name(s) from %s", len(loaded), path)
    return loaded


# Read once, at import, so every later config read sees the same environment.
load_dotenv()


def _coerce(value: str) -> Any:
    """Turn an env var's string into the JSON type the config field expects.

    Env vars are always strings; `"timeout": "60"` and `"enabled": "false"`
    would both be silently wrong (the latter is *truthy*). JSON's own literals
    are the least surprising rule here — `null`, `true`, `60`, `0.5` — and
    anything that isn't valid JSON stays the plain string it was, which is what
    a model id or a URL needs.
    """
    text = value.strip()
    if text == "":
        return ""
    lowered = text.lower()
    if lowered in {"null", "none"}:
        return None
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _set_path(target: dict[str, Any], dotted: str, value: Any) -> None:
    """Assign `value` at a dotted path, creating intermediate dicts as needed."""
    keys = dotted.split(".")
    cursor = target
    for key in keys[:-1]:
        nxt = cursor.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[key] = nxt
        cursor = nxt
    cursor[keys[-1]] = value


def _apply_env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    """Overlay ENV_OVERRIDES onto an already-merged config.

    Applied last, so precedence is env > config/jarvis.json > DEFAULTS.
    `JARVIS_BACKEND` is resolved first because the wildcard paths
    (`backends.*.model`) mean "the backend that is actually selected" — setting
    JARVIS_BACKEND=openrouter and JARVIS_MODEL=... in the same .env has to
    point the model at OpenRouter, not at whatever the JSON file named.
    """
    selected = os.environ.get("JARVIS_BACKEND")
    if selected:
        cfg["backend"] = selected.strip().lower()

    for env_name, dotted in ENV_OVERRIDES.items():
        if env_name == "JARVIS_BACKEND":
            continue
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        path = dotted.replace("*", str(cfg.get("backend", DEFAULTS["backend"])))
        _set_path(cfg, path, _coerce(raw))
        logger.debug("%s overrode %s", env_name, path)
    return cfg


def load_config() -> dict[str, Any]:
    """Read config/jarvis.json, writing the defaults out if it doesn't exist.

    Returns DEFAULTS <- the file <- the environment (see _apply_env_overrides).
    """
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2) + "\n")
        logger.info("Wrote default config to %s", CONFIG_PATH)
        return _apply_env_overrides(_deep_merge(DEFAULTS, {}))

    try:
        user_config = json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # A typo'd config shouldn't take the whole assistant down — fall back to
        # defaults, loudly.
        logger.error("Could not read %s (%s) — using defaults.", CONFIG_PATH, exc)
        return _apply_env_overrides(_deep_merge(DEFAULTS, {}))

    return _apply_env_overrides(_deep_merge(DEFAULTS, user_config))


def backend_config(name: str) -> dict[str, Any]:
    return load_config().get("backends", {}).get(name, {})


def actions_config(name: str) -> dict[str, Any]:
    """One entry from the `actions` block ("claude_code", "browser", "projects").

    Read on every call rather than cached, so editing config/jarvis.json — the
    permission mode especially — takes effect on the next command instead of
    the next restart.
    """
    return load_config().get("actions", {}).get(name, {})


def speech_config() -> dict[str, Any]:
    """The `speech` block — which voice speaks Jarvis's replies (jarvis/speech.py).

    Read on every call, like actions_config: switching voice or muting Jarvis
    should take effect on the next reply, not the next restart.
    """
    return load_config().get("speech", DEFAULTS["speech"])


def gestures_config() -> dict[str, Any]:
    """The `gestures` block — the webcam input channel (jarvis/gestures.py).

    Read on every call, like actions_config and speech_config: turning gestures
    off, or loosening `hold_frames` after a false trigger, should take effect on
    the next confirmation rather than the next restart.
    """
    return load_config().get("gestures", DEFAULTS["gestures"])


def memory_config() -> dict[str, Any]:
    """The `memory` block — Phase 6's store (jarvis/memory.py).

    Note what this does *not* do: apply the preference layer. It can't, because
    jarvis.memory reads this to find its own database, and a preference lives
    inside that database. This is the one config accessor that has to stay
    file-and-environment only, and it's why the layering is described as
    "below the environment" rather than "on top of everything".
    """
    return load_config().get("memory", DEFAULTS["memory"])


# --- the preference layer ----------------------------------------------------
#
# Which config paths a *spoken* preference is allowed to change. Everything the
# user could reasonably want to change by voice, and nothing else.
#
# The closed list is the point, and it is a safety boundary rather than
# tidiness. A preference is set from a transcript, which means it is set by a
# sentence Whisper reconstructed and a small model interpreted — the same
# untrusted path every other spoken instruction takes. So it must not be able
# to reach `actions.claude_code.allow_edits` or `permission_mode`, which decide
# whether the coding sub-agent may write to your files, or
# `actions.projects.roots`, which is the containment boundary jarvis/projects.py
# exists to enforce. Those stay decisions you make in a file, with your hands.
PREFERENCE_KEYS = frozenset({
    "backend",
    "speech.enabled",
    "speech.backend",
    "speech.backends.say.voice",
    "speech.backends.say.rate",
    "speech.backends.piper.model",
    "speech.backends.openai.voice",
})


def _with_preferences(cfg: dict[str, Any]) -> dict[str, Any]:
    """Overlay the user's spoken preferences onto an already-merged config.

    Imported lazily: jarvis.memory imports this module, so a module-level
    import would be a cycle. Any failure to read preferences leaves `cfg`
    exactly as it was — memory being unavailable degrades Jarvis to its Phase 5
    behaviour rather than breaking it (jarvis/memory.py's fourth rule).
    """
    try:
        from jarvis import memory
    except ImportError:  # pragma: no cover - only if the module is missing
        return cfg

    stored = memory.preferences()
    if not stored:
        return cfg

    result = dict(cfg)
    for key, value in stored.items():
        if key not in PREFERENCE_KEYS:
            continue
        # An environment variable is a deliberate act of configuration; a
        # sentence said months ago is not. The environment wins.
        env_var = next((var for var, path in ENV_OVERRIDES.items() if path == key), None)
        if env_var and os.environ.get(env_var):
            continue
        _set_path(result, key, _coerce(value))
    return result


def speech_config() -> dict[str, Any]:
    """The `speech` block — which voice speaks Jarvis's replies (jarvis/speech.py).

    Read on every call, like actions_config: switching voice or muting Jarvis
    should take effect on the next reply, not the next restart. Phase 6 layers
    a spoken preference over it, which is what makes "use the Daniel voice from
    now on" outlive the session it was said in.
    """
    merged = _with_preferences(load_config())
    return merged.get("speech", DEFAULTS["speech"])


def default_backend(task_type: str = "") -> str:
    """Which brain answers. A spoken preference beats the file; a plist beats both.

    `task_type` is doc 01's "preferred model backend per task type" — a
    preference stored as `backend_for.<task>` (jarvis.memory.backend_for). It is
    honoured here so the store and the router agree on what it means, but
    **nothing classifies a task type yet**: routing a request to a *kind* is
    Phase 7's job (doc 01, multi-agent orchestration), and inventing a
    classifier here would be doing Phase 7's work with none of its design. Until
    then this argument is only ever passed explicitly, by a script comparing
    backends.
    """
    if task_type:
        try:
            from jarvis import memory

            chosen = memory.backend_for(task_type)
        except ImportError:  # pragma: no cover
            chosen = None
        if chosen:
            return chosen
    return _with_preferences(load_config()).get("backend", DEFAULTS["backend"])


def api_key(backend: str) -> str | None:
    """Resolve `backend`'s API key: environment first, then secrets/api_keys.json.

    Returns None rather than raising — a missing key for a backend you're not
    using isn't an error, and the backend itself reports a clear failure if it
    is the one being used.
    """
    # A provider added to config/jarvis.json by hand has no ENV_KEYS entry, so
    # fall back to the obvious name — "groq" reads GROQ_API_KEY.
    env_name = ENV_KEYS.get(backend) or f"{backend.upper().replace('-', '_')}_API_KEY"
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]

    if SECRETS_PATH.exists():
        try:
            keys = json.loads(SECRETS_PATH.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not read %s (%s).", SECRETS_PATH, exc)
            return None
        value = keys.get(backend) or (keys.get(env_name) if env_name else None)
        if value:
            return value

    return None
