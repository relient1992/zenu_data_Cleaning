import re

# =====================================================================
# PRIVACY FOR CLOUD MODELS
# ---------------------------------------------------------------------
# Client exports hold personal data. When the active AI provider is a
# cloud API (Claude / ChatGPT) and "send real sample values" is off,
# everything DARIS sends goes through these helpers first:
#   shape()  - a value becomes its pattern: '0412 345 678' -> '9999 999 999'
#   redact() - free text keeps its words but loses emails and long numbers
# The model can still tell a phone column from an email column; it never
# sees a real person's details.
# =====================================================================

_EMAIL = re.compile(r"[\w.+'%-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_NUMBER = re.compile(r"\+?\d[\d ()-]{4,}\d")


def shape(value, limit: int = 40) -> str:
    s = str(value)[:limit]
    s = re.sub(r"[A-Z]", "A", s)
    s = re.sub(r"[a-z]", "a", s)
    return re.sub(r"\d", "9", s)


def redact(text: str) -> str:
    text = _EMAIL.sub("[email]", str(text))
    return _LONG_NUMBER.sub("[number]", text)
