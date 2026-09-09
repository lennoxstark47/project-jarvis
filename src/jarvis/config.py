"""
Configuration & API-key resolution — Phases 2-5.

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
`actions.login` block (what the login tool is allowed to do).

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


def default_backend() -> str:
    return load_config().get("backend", DEFAULTS["backend"])


def save_alias(name: str, path: str | Path) -> bool:
    """Record `name` -> `path` in config/jarvis.json's actions.projects.aliases.

    Written back into the *user's* file rather than the merged defaults, so
    nothing they've edited gets flattened by a value they never set. This is how
    "where is that project?" only ever has to be asked once — the spoken answer
    becomes a permanent alias.
    """
    name = (name or "").strip()
    if not name:
        return False
    try:
        raw = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read %s to save an alias (%s).", CONFIG_PATH, exc)
        return False

    aliases = raw.setdefault("actions", {}).setdefault("projects", {}).setdefault("aliases", {})
    if aliases.get(name) == str(path):
        return True
    aliases[name] = str(path)
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(raw, indent=2) + "\n")
    except OSError as exc:
        logger.error("Could not save the alias for %r (%s).", name, exc)
        return False
    logger.info("learned that %r means %s", name, path)
    return True


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
