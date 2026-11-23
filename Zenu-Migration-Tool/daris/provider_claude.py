import re

import anthropic

from .llm import LLMTasks, parse_json

# =====================================================================
# CLAUDE (Anthropic API) - optional cloud provider
# ---------------------------------------------------------------------
# Uses the official SDK (pip install anthropic). Key from Settings or
# ANTHROPIC_API_KEY. What is sent is limited by LLMTasks' privacy rules.
#   - effort 'low' for column choices, 'medium' for chat and summaries
#   - column choices come back as schema-checked JSON (output_config.format)
#   - fallbacks="default": if a model declines a request, the API re-runs it
#     on Anthropic's recommended fallback model inside the same call
# =====================================================================

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
SUGGESTED_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]

# Models that accept server-side fallbacks="default" on the Claude API
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that take output_config.effort
_EFFORT_MODELS = re.compile(r"^claude-(fable|mythos|opus|sonnet|haiku)-(5|4-[6-9])")


def _error_text(e: Exception, model: str) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return "Anthropic rejected the API key - check it in Settings."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "This API key isn't allowed to use that model."
    if isinstance(e, anthropic.NotFoundError):
        return f"Model '{model}' was not found - check the model name in Settings."
    if isinstance(e, anthropic.RateLimitError):
        return "Anthropic rate limit reached - try again shortly."
    if isinstance(e, anthropic.APIConnectionError):
        return "Can't reach api.anthropic.com - check the internet connection."
    if isinstance(e, anthropic.APIStatusError):
        return f"Anthropic error {e.status_code}: {e.message}"
    return f"Claude error: {e}"


class ClaudeLLM(LLMTasks):
    provider = "claude"
    label = "Claude"
    is_cloud = True

    def __init__(self, model: str = None, api_key: str = "", timeout: float = 120.0):
        self.model = (model or DEFAULT_CLAUDE_MODEL).strip()
        self.calls = 0
        self.last_error = "" if api_key else "No Anthropic API key - add one in Settings."
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout) if api_key else None

    @property
    def available(self) -> bool:
        return self.client is not None

    def status(self) -> dict:
        return {"provider": self.provider, "label": self.label, "cloud": True,
                "online": self.available, "model": self.model, "model_installed": self.available,
                "installed_models": [], "host": "api.anthropic.com", "error": self.last_error}

    def _options(self, effort: str, schema: dict = None, system: str = "") -> dict:
        kw = {"model": self.model}
        output_config = {}
        if _EFFORT_MODELS.match(self.model):
            output_config["effort"] = effort
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            kw["output_config"] = output_config
        if system:
            kw["system"] = system
        if self.model in FALLBACK_MODELS:
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw

    # -----------------------------------------------------------------
    def generate(self, prompt, system="", as_json=False, max_tokens=300, schema=None):
        if not self.available:
            return None
        kw = self._options("low" if as_json else "medium", schema if as_json else None, system)
        messages = [{"role": "user", "content": prompt}]
        try:
            self.calls += 1
            try:
                resp = self.client.beta.messages.create(max_tokens=16000, messages=messages, **kw)
            except anthropic.BadRequestError:
                # An older model without structured outputs: ask again, prompt-only JSON
                if not kw.get("output_config", {}).pop("format", None):
                    raise
                resp = self.client.beta.messages.create(max_tokens=16000, messages=messages, **kw)
        except anthropic.APIError as e:
            self.last_error = _error_text(e, self.model)
            return None
        if resp.stop_reason == "refusal":
            self.last_error = "Claude declined this request."
            return None
        text = "".join(b.text for b in resp.content if b.type == "text")
        return parse_json(text) if as_json else text.strip()

    def stream(self, prompt="", system="", messages=None, max_tokens=400):
        if not self.available:
            return
        msgs = [m for m in (messages if messages is not None else
                            [{"role": "user", "content": prompt}]) if m.get("content")]
        while msgs and msgs[0]["role"] != "user":       # the API needs a user turn first
            msgs.pop(0)
        if not msgs:
            return
        try:
            self.calls += 1
            with self.client.beta.messages.stream(max_tokens=64000, messages=msgs,
                                                  **self._options("medium", None, system)) as s:
                for text in s.text_stream:
                    yield text
                final = s.get_final_message()
            if final.stop_reason == "refusal":
                self.last_error = "Claude declined this request."
                yield "\n\n(Claude declined to answer this one.)"
        except anthropic.APIError as e:
            self.last_error = _error_text(e, self.model)
            return
