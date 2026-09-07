"""
Configuration & API-key resolution — Phase 2.

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
        "ollama": {"model": "hermes3", "host": "http://localhost:11434"}
      }
    }

See DEFAULTS below for what each field means and why it's set the way it is;
a file that only overrides some of them still inherits the rest.

API keys are deliberately *not* in that file — it's committed-adjacent and
easy to `cat` into a terminal by accident. They're read from the environment
first, then from `secrets/api_keys.json` (the `secrets/` directory Phase 0
created and .gitignore'd). Moving them into the macOS Keychain is Phase 4's
job (see docs/04-CREDENTIALS_AND_SECURITY.md) — these are Jarvis's own
service keys, not the user's site credentials that doc is about.
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
        "ollama": {
            # hermes3 is Nous Research's tool-calling-tuned model (doc 03);
            # llama3.1 and qwen2.5 are the obvious alternatives to compare
            # against. Pull one first: `ollama pull hermes3`.
            "model": "hermes3",
            "host": "http://localhost:11434",
        },
    },
}

# env var checked first for each backend's key
ENV_KEYS = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
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


def load_config() -> dict[str, Any]:
    """Read config/jarvis.json, writing the defaults out if it doesn't exist."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2) + "\n")
        logger.info("Wrote default config to %s", CONFIG_PATH)
        return dict(DEFAULTS)

    try:
        user_config = json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # A typo'd config shouldn't take the whole assistant down — fall back to
        # defaults, loudly.
        logger.error("Could not read %s (%s) — using defaults.", CONFIG_PATH, exc)
        return dict(DEFAULTS)

    return _deep_merge(DEFAULTS, user_config)


def backend_config(name: str) -> dict[str, Any]:
    return load_config().get("backends", {}).get(name, {})


def default_backend() -> str:
    return load_config().get("backend", DEFAULTS["backend"])


def api_key(backend: str) -> str | None:
    """Resolve `backend`'s API key: environment first, then secrets/api_keys.json.

    Returns None rather than raising — a missing key for a backend you're not
    using isn't an error, and the backend itself reports a clear failure if it
    is the one being used.
    """
    env_name = ENV_KEYS.get(backend)
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
