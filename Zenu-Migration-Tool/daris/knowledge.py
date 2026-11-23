import json
import os
import re
from functools import lru_cache

# =====================================================================
# KNOWLEDGE BASE
# ---------------------------------------------------------------------
# Reads the Zenu field list (zenu_schema.js) and the alias dictionary
# (zenu_matcher.js) so DARIS and the browser Smart Mapper share one
# source of truth. Edit those files, not this one, to teach new names.
# =====================================================================

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_JS = os.path.join(PROJECT_DIR, "zenu_schema.js")
MATCHER_JS = os.path.join(PROJECT_DIR, "zenu_matcher.js")


def normalize_header(h) -> str:
    """'Mobile_Phone', 'mobilePhone', ' MOBILE  phone ' -> 'mobile phone'."""
    s = "" if h is None else str(h)
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)          # camelCase -> camel Case
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_PLACEHOLDER = re.compile(r"^column[_ ]?\d+(?: \(\d+\))?$", re.IGNORECASE)


def is_placeholder(header) -> bool:
    """'Column_5' is a name DARIS invented for a blank/missing header. It means
    nothing across files, so it must never be learned or merged by name."""
    return bool(_PLACEHOLDER.match(str(header or "").strip()))


@lru_cache(maxsize=1)
def load_fields():
    """List of {name, type, group, desc, accepted?} from zenu_schema.js."""
    try:
        with open(SCHEMA_JS, "r", encoding="utf-8") as f:
            text = f.read()
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except Exception:
        return []


@lru_cache(maxsize=1)
def load_aliases():
    """{zenu_field: [alias, ...]} parsed from the ALIASES object in zenu_matcher.js."""
    aliases = {}
    try:
        with open(MATCHER_JS, "r", encoding="utf-8") as f:
            text = f.read()
        block = text[text.index("var ALIASES = {"):]
        block = block[:block.index("};")]
        for field, body in re.findall(r"(\w+)\s*:\s*\[(.*?)\]", block, re.DOTALL):
            aliases[field] = re.findall(r"'([^']*)'", body)
    except Exception:
        pass
    return aliases


def expected_type(field: dict) -> str:
    """Semantic type a Zenu field's values should have (matches profiler.py)."""
    name = field.get("name", "")
    ftype = field.get("type", "")
    if "email" in name:
        return "email"
    if any(k in name for k in ("mobile", "phone", "fax")):
        return "phone"
    if "postcode" in name:
        return "postcode"
    if ftype == "date" and not name.endswith("_price"):
        return "date"
    if ftype == "true/false":
        return "boolean"
    if name.endswith("identifier") or name.endswith("_id"):
        return "id"
    if ftype == "integer":
        return "number"
    return "text"
