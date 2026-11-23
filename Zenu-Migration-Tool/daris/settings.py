import json
import os

# =====================================================================
# DARIS SETTINGS
# ---------------------------------------------------------------------
# Which AI answers DARIS's questions, and how much data it may see.
# Stored in daris/daris_settings.json (git-ignored - it can hold API keys).
# API keys can instead come from ANTHROPIC_API_KEY / OPENAI_API_KEY; a key
# saved here wins. Keys are never sent back to the browser in full.
# =====================================================================

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daris_settings.json")

PROVIDERS = ("local", "claude", "openai")
DEFAULTS = {
    "provider": "local",
    "local_model": os.environ.get("DARIS_MODEL", "qwen2.5:3b"),
    "claude_model": "claude-opus-5-5",
    "claude_api_key": "",
    "openai_model": "gpt-5-mini",
    "openai_api_key": "",
    # Cloud privacy: off = models see value shapes, never real values
    "send_samples_to_cloud": False,
    # Off = AI picks are suggestions only (see matcher.py)
    "auto_apply_ai_mappings": False,
}
KEY_FIELDS = {"claude_api_key": "ANTHROPIC_API_KEY", "openai_api_key": "OPENAI_API_KEY"}
CLEAR = "__clear__"


def load() -> dict:
    settings = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if settings["provider"] not in PROVIDERS:
        settings["provider"] = "local"
    return settings


def api_key(settings: dict, field: str):
    """(key, source) - a saved key wins over the environment variable."""
    if settings.get(field):
        return settings[field], "saved"
    env = os.environ.get(KEY_FIELDS[field], "")
    return (env, "environment") if env else ("", "none")


def save(update: dict) -> dict:
    """Merge a partial update. A blank key keeps the saved one; CLEAR removes it."""
    settings = load()
    for k, v in update.items():
        if k not in DEFAULTS or v is None:
            continue
        if k in KEY_FIELDS:
            v = str(v).strip()
            if not v:
                continue
            v = "" if v == CLEAR else v
        elif isinstance(DEFAULTS[k], bool):
            v = bool(v)
        else:
            v = str(v).strip() or DEFAULTS[k]
        settings[k] = v
    if settings["provider"] not in PROVIDERS:
        settings["provider"] = "local"
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    return settings


def public(settings: dict = None) -> dict:
    """Safe to send to the browser: keys reduced to their last 4 characters."""
    settings = settings or load()
    out = {k: v for k, v in settings.items() if k not in KEY_FIELDS}
    for field in KEY_FIELDS:
        key, source = api_key(settings, field)
        out[field.replace("_api_key", "_key")] = {
            "set": bool(key), "source": source, "hint": f"••••{key[-4:]}" if key else ""}
    return out
