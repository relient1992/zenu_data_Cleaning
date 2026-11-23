import json
import os
import urllib.error
import urllib.request

from . import settings as daris_settings
from .privacy import redact, shape

# =====================================================================
# AI PROVIDERS
# ---------------------------------------------------------------------
# DARIS asks an AI three kinds of question: "which Zenu field is this
# column?", "summarise this run", and free chat. LLMTasks writes those
# prompts once; each provider only knows how to reach its model:
#   LocalLLM  - Ollama on this PC (default, nothing leaves the device)
#   ClaudeLLM - Anthropic API   (daris/provider_claude.py)
#   OpenAILLM - OpenAI API      (daris/provider_openai.py)
# get_llm() returns the one chosen in Settings. Every call returns None /
# yields nothing on failure, so DARIS falls back to rules + fuzzy matching.
#
#   Local install:  winget install Ollama.Ollama  ->  ollama pull qwen2.5:3b
# =====================================================================

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
if not DEFAULT_HOST.startswith("http"):
    DEFAULT_HOST = "http://" + DEFAULT_HOST
DEFAULT_MODEL = daris_settings.DEFAULTS["local_model"]

# Field-choice answer shape - providers that support JSON schemas enforce it
FIELD_CHOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "holds": {"type": "string"},
        "field": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["holds", "field", "confidence"],
    "additionalProperties": False,
}


def parse_json(text):
    """Model text -> dict. Tolerates prose around the JSON object."""
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                return None
    return None


class LLMTasks:
    """Prompts shared by every provider. Subclasses implement generate() and stream()."""
    provider = "local"
    label = "Local AI"
    is_cloud = False
    send_samples = True        # cloud providers: set from Settings
    calls = 0
    last_error = ""

    def generate(self, prompt, system="", as_json=False, max_tokens=300, schema=None):
        raise NotImplementedError

    def stream(self, prompt="", system="", messages=None, max_tokens=400):
        raise NotImplementedError

    # ----- privacy ----------------------------------------------------
    @property
    def protect(self) -> bool:
        """True when data is leaving the device and real values are not allowed."""
        return self.is_cloud and not self.send_samples

    def safe_samples(self, values):
        return [shape(v) for v in values] if self.protect else list(values)

    def safe_text(self, text):
        return redact(text) if self.protect else text

    # ----- column -> Zenu field ----------------------------------------
    def choose_field(self, header, sample_values, candidates, entity=None, siblings=()):
        """Pick one Zenu field (or None) for an ambiguous header.
        candidates: list of field dicts {name, group, desc}. entity/siblings describe the
        sheet so a model can rule out e.g. task fields in a contact export.
        Returns (name, confidence) or None."""
        options = "\n".join(f"- {c['name']} [{c.get('group', '?')}]: {self._describe(c)}"
                            for c in candidates)
        options += "\n- NONE: the column holds something none of these fields describe"
        context = ""
        if entity:
            context += f"This sheet is a {entity.upper()} export.\n"
        if siblings:
            context += f"Its other columns include: {', '.join(list(siblings)[:25])}\n"
        samples = self.safe_samples(sample_values[:5])
        note = ("(Values are shown as patterns: 9 = digit, a/A = letter.)\n" if self.protect else "")
        prompt = (
            f"{context}"
            f'Column to map: "{header}"\n'
            f"Sample values: {json.dumps(samples)}\n{note}\n"
            f"Candidate Zenu CRM fields [entity]:\n{options}\n\n"
            "First say in a few words what the column holds, then pick the field whose "
            "description matches that data for this sheet's entity. Pick NONE only when no "
            "candidate describes the same data.\n"
            'Reply as JSON: {"holds": "<what the column contains>", '
            '"field": "<candidate name or NONE>", "confidence": <0.0-1.0>}'
        )
        system = ("You map Australian real-estate CRM export columns onto the Zenu import schema. "
                  "Judge by the sample values and the sheet's entity, not just header wording.")
        names = [c["name"] for c in candidates]
        schema = json.loads(json.dumps(FIELD_CHOICE_SCHEMA))
        schema["properties"]["field"]["enum"] = names + ["NONE"]
        ans = self.generate(prompt, system=system, as_json=True, max_tokens=100, schema=schema)
        if not isinstance(ans, dict):
            return None
        field = str(ans.get("field", "")).strip()
        if field not in names:
            return None
        try:
            conf = float(ans.get("confidence", 0.7))
        except (TypeError, ValueError):
            conf = 0.7
        return field, max(0.0, min(conf, 1.0))

    @staticmethod
    def _describe(field):
        text = field.get("desc", "")[:90]
        if field.get("accepted"):
            text += f" (allowed values: {', '.join(field['accepted'][:6])})"
        return text

    # ----- run summary --------------------------------------------------
    SUMMARY_SYSTEM = "You are DARIS, a precise data-migration assistant."

    def summary_prompt(self, facts: dict) -> str:
        return self.safe_text(
            "Facts from a data-migration consolidation run (JSON):\n"
            f"{json.dumps(facts, default=str)[:6000]}\n\n"
            "Write 4-6 short bullet points for the migration specialist: overall result, "
            "row reconciliation, columns that need review, duplicate risk, and the single "
            "most important next action. Plain text bullets starting with '- '. No preamble."
        )

    def summarize(self, facts: dict):
        """Plain-English sign-off summary of a consolidation/reconciliation run."""
        text = self.generate(self.summary_prompt(facts), system=self.SUMMARY_SYSTEM, max_tokens=350)
        if not text:
            return None
        # Small models like to wrap bullets in backticks or double the dash
        lines = []
        for line in text.replace("`", "").splitlines():
            line = line.strip().lstrip("-* ").strip()
            if line:
                lines.append("- " + line)
        return "\n".join(lines)


# =====================================================================
class LocalLLM(LLMTasks):
    """Ollama on this PC. Stdlib only."""

    def __init__(self, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST, timeout: int = 90):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._available = None
        self.calls = 0

    def _post(self, path, payload, timeout):
        req = urllib.request.Request(
            self.host + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def status(self) -> dict:
        info = {"provider": self.provider, "label": self.label, "cloud": False,
                "online": False, "model": self.model, "model_installed": False,
                "installed_models": [], "host": self.host}
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=2) as resp:
                tags = json.loads(resp.read().decode("utf-8"))
            names = [m.get("name", "") for m in tags.get("models", [])]
            info["online"] = True
            info["installed_models"] = names
            want = self.model if ":" in self.model else self.model + ":latest"
            info["model_installed"] = want in names or self.model in names
        except Exception:
            pass
        return info

    @property
    def available(self) -> bool:
        if self._available is None:
            s = self.status()
            self._available = s["online"] and s["model_installed"]
        return self._available

    def generate(self, prompt, system="", as_json=False, max_tokens=300, schema=None):
        if not self.available:
            return None
        payload = {
            "model": self.model, "prompt": prompt, "system": system, "stream": False,
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 4096},
        }
        if as_json:
            # Ollama constrains output to a JSON schema too - keeps a 3B model inside the enum
            payload["format"] = schema or "json"
        try:
            self.calls += 1
            out = self._post("/api/generate", payload, self.timeout).get("response", "")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            self.last_error = str(e)
            return None
        return parse_json(out) if as_json else out.strip()

    def stream(self, prompt="", system="", messages=None, max_tokens=400):
        """Yield the reply piece by piece (for the chat UI). messages = [{role, content}]
        uses /api/chat with history; otherwise a single prompt via /api/generate."""
        if not self.available:
            return
        options = {"temperature": 0.2, "num_predict": max_tokens, "num_ctx": 4096}
        if messages is not None:
            path = "/api/chat"
            payload = {"model": self.model, "stream": True, "options": options,
                       "messages": ([{"role": "system", "content": system}] if system else []) + messages}
        else:
            path = "/api/generate"
            payload = {"model": self.model, "prompt": prompt, "system": system,
                       "stream": True, "options": options}
        req = urllib.request.Request(
            self.host + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            self.calls += 1
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    data = json.loads(line.decode("utf-8"))
                    piece = (data.get("message") or {}).get("content") if messages is not None \
                        else data.get("response")
                    if piece:
                        yield piece
                    if data.get("done"):
                        break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            self.last_error = str(e)
            return


# =====================================================================
def get_llm(settings: dict = None) -> LLMTasks:
    """The provider chosen in Settings. Cloud providers are imported lazily so a
    missing SDK never stops DARIS from starting - it falls back to local."""
    settings = settings or daris_settings.load()
    provider = settings.get("provider", "local")
    llm = None
    try:
        if provider == "claude":
            from .provider_claude import ClaudeLLM
            key, _src = daris_settings.api_key(settings, "claude_api_key")
            llm = ClaudeLLM(model=settings["claude_model"], api_key=key)
        elif provider == "openai":
            from .provider_openai import OpenAILLM
            key, _src = daris_settings.api_key(settings, "openai_api_key")
            llm = OpenAILLM(model=settings["openai_model"], api_key=key)
    except ImportError as e:
        local = LocalLLM(model=settings["local_model"])
        local.last_error = f"{provider} SDK not installed ({e}) - using local AI"
        return local
    if llm is None:
        return LocalLLM(model=settings["local_model"])
    llm.send_samples = bool(settings.get("send_samples_to_cloud"))
    return llm
