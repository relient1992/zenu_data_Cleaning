import re
import warnings

import pandas as pd

# =====================================================================
# COLUMN PROFILER
# ---------------------------------------------------------------------
# Looks at a sample of a column's values and guesses what they are.
# Cheap, deterministic, and the main signal that stops the matcher
# from mapping "Phone" onto an email column just because names look alike.
# =====================================================================

RE_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
RE_NUMBER = re.compile(r"^-?\$?[\d,]+(\.\d+)?$")
RE_POSTCODE = re.compile(r"^\d{4}$")
RE_DATEISH = re.compile(
    r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}([ T]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?(\s?[AaPp][Mm])?)?$")
RE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")
BOOL_TOKENS = {"true", "false", "yes", "no", "y", "n", "0", "1"}
EMPTY_TOKENS = {"", "nan", "none", "null", "n/a", "na", "nat"}

SAMPLE_SIZE = 300


def non_empty(values):
    out = []
    for v in values:
        s = "" if v is None else str(v).strip()
        if s.lower() not in EMPTY_TOKENS:
            out.append(s)
    return out


def _is_phone(s: str) -> bool:
    if "@" in s or RE_DATEISH.match(s):
        return False
    digits = re.sub(r"[\s()\-+.]", "", s)
    if not digits.isdigit():
        return False
    return 8 <= len(digits) <= 12


def _share(values, pred) -> float:
    return sum(1 for v in values if pred(v)) / len(values) if values else 0.0


def profile(values) -> dict:
    """Return {type, fill, unique_ratio, sample}."""
    vals = non_empty(values)
    total = len(values) if len(values) else 1
    result = {"fill": round(len(vals) / total, 3), "unique_ratio": 0.0,
              "sample": vals[:5], "type": "empty"}
    if not vals:
        return result

    sample = vals[:SAMPLE_SIZE]
    result["unique_ratio"] = round(len(set(sample)) / len(sample), 3)

    if _share(sample, lambda v: RE_EMAIL.match(v) is not None) >= 0.6:
        result["type"] = "email"
    elif _share(sample, lambda v: v.lower() in BOOL_TOKENS) >= 0.9 and len(set(sample)) <= 4:
        result["type"] = "boolean"
    elif _share(sample, lambda v: RE_POSTCODE.match(v) is not None) >= 0.8:
        result["type"] = "postcode"
    elif _share(sample, _is_phone) >= 0.6:
        result["type"] = "phone"
    elif _share(sample, lambda v: RE_DATEISH.match(v) is not None) >= 0.7 or _looks_like_dates(sample):
        result["type"] = "date"
    elif _share(sample, lambda v: RE_NUMBER.match(v) is not None) >= 0.9:
        # Distinct-per-row integers are almost always keys, not quantities
        result["type"] = "id" if result["unique_ratio"] >= 0.95 and len(sample) >= 20 else "number"
    elif (result["unique_ratio"] >= 0.95 and len(sample) >= 20
          and _share(sample, lambda v: RE_ID.match(v) is not None) >= 0.95):
        result["type"] = "id"
    else:
        result["type"] = "text"
    return result


def cell_matches(value, col_type):
    """Does one cell look like the data in a column of col_type?
    True / False, or None when the type can't tell (free text, ids, empty)."""
    v = "" if value is None else str(value).strip()
    if not v or col_type in ("text", "id", "empty"):
        return None
    if col_type == "email":
        return RE_EMAIL.match(v) is not None
    if col_type == "phone":
        parts = [p for p in re.split(r"[,;/]", v) if p.strip()]
        return bool(parts) and all(_is_phone(p.strip()) for p in parts)
    if col_type == "postcode":
        return RE_POSTCODE.match(v) is not None
    if col_type == "date":
        return RE_DATEISH.match(v) is not None or _looks_like_dates([v])
    if col_type == "number":
        return RE_NUMBER.match(v) is not None
    if col_type == "boolean":
        return v.lower() in BOOL_TOKENS
    return None


def _looks_like_dates(sample) -> bool:
    """Catch '2024-03-01 00:00:00' and Excel datetimes stringified by pandas."""
    if _share(sample, lambda v: v[:1].isdigit()) < 0.9:
        return False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(pd.Series(sample[:100]), dayfirst=True, errors="coerce",
                                format="mixed")
    return parsed.notna().mean() >= 0.8


# How well an observed type fits what a Zenu field expects (-0.25 .. +0.1).
_COMPATIBLE = {
    ("number", "id"), ("id", "number"), ("postcode", "number"), ("number", "postcode"),
    ("id", "text"), ("text", "id"), ("boolean", "number"),
}


def type_bonus(observed: str, expected: str) -> float:
    if observed == "empty" or not expected:
        return 0.0
    if observed == expected:
        # 'text' is the catch-all, so agreeing on it proves nothing
        return 0.1 if expected != "text" else 0.0
    if (observed, expected) in _COMPATIBLE:
        return 0.0
    if observed == "text" or expected == "text":
        return -0.08      # weak conflict: odd formats often profile as text
    return -0.25          # hard conflict, e.g. emails under a phone header
