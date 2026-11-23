import openai

from .llm import LLMTasks, parse_json

# =====================================================================
# CHATGPT (OpenAI API) - optional cloud provider
# ---------------------------------------------------------------------
# Uses the official SDK (pip install openai). Key from Settings or
# OPENAI_API_KEY. What is sent is limited by LLMTasks' privacy rules.
# Model names change often - set the one your account uses in Settings.
# No temperature / reasoning settings are sent, so any chat model works.
# =====================================================================

DEFAULT_OPENAI_MODEL = "gpt-5-mini"


def _error_text(e: Exception, model: str) -> str:
    if isinstance(e, openai.AuthenticationError):
        return "OpenAI rejected the API key - check it in Settings."
    if isinstance(e, openai.PermissionDeniedError):
        return "This API key isn't allowed to use that model."
    if isinstance(e, openai.NotFoundError):
        return f"Model '{model}' was not found - check the model name in Settings."
    if isinstance(e, openai.RateLimitError):
        return "OpenAI rate limit or quota reached - check your plan/billing."
    if isinstance(e, openai.APIConnectionError):
        return "Can't reach api.openai.com - check the internet connection."
    if isinstance(e, openai.APIStatusError):
        return f"OpenAI error {e.status_code}: {e.message}"
    return f"ChatGPT error: {e}"


class OpenAILLM(LLMTasks):
    provider = "openai"
    label = "ChatGPT"
    is_cloud = True

    def __init__(self, model: str = None, api_key: str = "", timeout: float = 120.0):
        self.model = (model or DEFAULT_OPENAI_MODEL).strip()
        self.calls = 0
        self.last_error = "" if api_key else "No OpenAI API key - add one in Settings."
        self.client = openai.OpenAI(api_key=api_key, timeout=timeout) if api_key else None

    @property
    def available(self) -> bool:
        return self.client is not None

    def status(self) -> dict:
        return {"provider": self.provider, "label": self.label, "cloud": True,
                "online": self.available, "model": self.model, "model_installed": self.available,
                "installed_models": [], "host": "api.openai.com", "error": self.last_error}

    @staticmethod
    def _messages(system, msgs):
        return ([{"role": "system", "content": system}] if system else []) + list(msgs)

    # -----------------------------------------------------------------
    def generate(self, prompt, system="", as_json=False, max_tokens=300, schema=None):
        if not self.available:
            return None
        kw = {"model": self.model, "max_completion_tokens": 16000,
              "messages": self._messages(system, [{"role": "user", "content": prompt}])}
        if as_json:
            kw["response_format"] = (
                {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": True}}
                if schema else {"type": "json_object"})
        try:
            self.calls += 1
            try:
                resp = self.client.chat.completions.create(**kw)
            except openai.BadRequestError:
                # A model without schema support: fall back to plain JSON mode
                if not (as_json and schema):
                    raise
                kw["response_format"] = {"type": "json_object"}
                resp = self.client.chat.completions.create(**kw)
        except openai.APIError as e:
            self.last_error = _error_text(e, self.model)
            return None
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            self.last_error = "ChatGPT declined this request."
            return None
        text = msg.content or ""
        return parse_json(text) if as_json else text.strip()

    def stream(self, prompt="", system="", messages=None, max_tokens=400):
        if not self.available:
            return
        msgs = [m for m in (messages if messages is not None else
                            [{"role": "user", "content": prompt}]) if m.get("content")]
        if not msgs:
            return
        try:
            self.calls += 1
            for chunk in self.client.chat.completions.create(
                    model=self.model, max_completion_tokens=16000, stream=True,
                    messages=self._messages(system, msgs)):
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except openai.APIError as e:
            self.last_error = _error_text(e, self.model)
            return
