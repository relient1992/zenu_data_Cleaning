import os
import re
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache

from .knowledge import expected_type, load_aliases, load_fields, normalize_header
from .profiler import non_empty, profile, type_bonus

# =====================================================================
# LAYERED HEADER MATCHER
# ---------------------------------------------------------------------
# For each source column decide which Zenu field it is, cheapest layer first:
#   1. memory   - a mapping you (or the LLM) already decided     conf 1.0 / 0.8
#   2. exact    - header IS a Zenu field name                    conf 1.0
#   3. alias    - header is a known alias (zenu_matcher.js)       ~0.95
#   4. fuzzy    - string similarity, nudged by value-type fit     score
#   5. llm      - local model picks among the top candidates,
#                 only for the grey zone (GREY <= score < AUTO)
# Anything below that keeps its original header and is reported for review,
# because a wrong silent mapping is worse than an unmapped column in a migration.
#
# LLM picks are SUGGESTIONS by default: measured on real headers, qwen2.5:3b
# was right ~60% of the time - useful for review, not for silent renames.
# Set DARIS_LLM_AUTO_MAP=1 (e.g. with a bigger model) to let them apply.
# =====================================================================

AUTO = 0.85     # at or above: map automatically
GREY = 0.55     # between GREY and AUTO: ask the LLM, else suggest only
FUZZY_CAP = 0.93            # a fuzzy guess never outranks an exact alias (0.95)
GROUP_BIAS = 0.03           # nudge toward the sheet's main entity
CROSS_GROUP_PENALTY = 0.12  # e.g. 'Status' in a contact sheet is not task_status
NO_FIELD = "\0none"         # cached LLM answer: no candidate fits

# Value shapes for headerless sheets: '12 Smith St', '3/45A King Rd', 'Unit 4 9 Bay Pde'
RE_STREET = re.compile(r"^\s*(?:(?:unit|u|lot|shop|apt)\s*)?\d+[a-z]?(?:\s*/\s*\d+[a-z]?)?\s+[a-z]", re.I)
RE_NAME_WORD = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")

_GROUP_WORDS = {"contact", "property", "listing", "enquiry", "inspection", "offer", "task"}


def _phrases_for_field(name: str):
    """'contact_mailing_address_suburb' -> {'contact mailing address suburb',
    'mailing address suburb', 'address suburb'}."""
    full = normalize_header(name)
    out = {full}
    words = full.split()
    if words and words[0] in _GROUP_WORDS and len(words) > 1:
        rest = words[1:]
        out.add(" ".join(rest))
        if rest[0] in ("mailing", "criteria") and len(rest) > 1:
            out.add(" ".join(rest[1:]))
    return out


def _strip_group(s: str) -> str:
    words = [w for w in s.split() if w not in _GROUP_WORDS]
    return " ".join(words) if words else s


def _raw_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    sm = SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", ""))
    if sm.real_quick_ratio() < 0.6 or sm.quick_ratio() < 0.6:
        return jac                               # cheap upper bounds already too low
    return max(sm.ratio(), jac)


@lru_cache(maxsize=200_000)
def _similarity(a: str, b: str) -> float:
    # 'contact name' vs 'contact surname' is really 'name' vs 'surname', but
    # 'property types' must keep 'property' - so average the two views.
    raw = _raw_similarity(a, b)
    sa, sb = _strip_group(a), _strip_group(b)
    if (sa, sb) == (a, b):
        return raw
    return (raw + _raw_similarity(sa, sb)) / 2


class HeaderMatcher:
    def __init__(self, memory=None, llm=None, max_llm_calls: int = 25, llm_auto_apply=None,
                 log=None):
        self.log = log or (lambda _m: None)
        self.fields = load_fields()
        self.by_name = {f["name"]: f for f in self.fields}
        self.memory = memory
        self.llm = llm
        self.max_llm_calls = max_llm_calls
        self.llm_calls = 0
        self._llm_seen = {}
        if llm_auto_apply is None:
            llm_auto_apply = os.environ.get("DARIS_LLM_AUTO_MAP", "").strip().lower() in ("1", "true", "yes")
        self.llm_auto_apply = llm_auto_apply

        # phrase -> {field: base_score}
        self.phrases = {}
        for f in self.fields:
            for p in _phrases_for_field(f["name"]):
                score = 1.0 if p == normalize_header(f["name"]) else 0.95
                self._add_phrase(p, f["name"], score)
        for field, aliases in load_aliases().items():
            if field not in self.by_name:
                continue
            for a in aliases:
                p = normalize_header(a)
                # very short aliases ('id', 'st', 'ref') are weaker evidence
                self._add_phrase(p, field, 0.95 if len(p) > 3 else 0.88)

    def _add_phrase(self, phrase, field, score):
        if not phrase:
            return
        slot = self.phrases.setdefault(phrase, {})
        slot[field] = max(slot.get(field, 0.0), score)

    def is_known_header(self, header) -> bool:
        return normalize_header(header) in self.phrases

    # -----------------------------------------------------------------
    def _candidates(self, header, observed_type, limit=5):
        """[(field, score, method)] best first."""
        key = normalize_header(header)
        best = {}   # field -> (score, method)

        exact = self.phrases.get(key)
        if exact:
            for field, base in exact.items():
                method = "exact" if base == 1.0 else "alias"
                # An exact Zenu name is authoritative; aliases still respect value type
                bonus = 0.0 if method == "exact" else min(
                    0.0, type_bonus(observed_type, expected_type(self.by_name[field])))
                best[field] = (base + bonus, method)

        for phrase, fields in self.phrases.items():
            if phrase == key:
                continue                          # already scored as exact/alias
            sim = _similarity(key, phrase)
            if sim < 0.6:
                continue
            for field, base in fields.items():
                score = sim * base + type_bonus(observed_type, expected_type(self.by_name[field]))
                score = min(score, FUZZY_CAP)
                if field not in best or score > best[field][0]:
                    best[field] = (score, "fuzzy")

        ranked = sorted(((f, s, m) for f, (s, m) in best.items()), key=lambda x: -x[1])
        return ranked[:limit]

    # -----------------------------------------------------------------
    def match_columns(self, columns):
        """columns: list of (header, values). Returns one decision dict per column, in order."""
        decisions = []
        for header, values in columns:
            prof = profile(values)
            decisions.append({
                "source_column": header, "target": None, "confidence": 0.0,
                "method": "unmatched", "type": prof["type"], "fill": prof["fill"],
                "sample": prof["sample"], "suggestion": None, "suggested_by": "", "_cands": [],
            })

        # Layer 1: memory
        for d in decisions:
            hit = self.memory.lookup(d["source_column"]) if self.memory else None
            if hit is None:
                continue
            target, source = hit
            if source.startswith("llm"):
                # 'llm:claude' etc. - plain 'llm' rows predate tagging and came from local
                answered_by = source.split(":", 1)[1] if ":" in source else "local"
                if self.llm and self.llm.available and answered_by != self.llm.provider:
                    continue                    # a different AI is active now - ask it fresh
                if target == "" or not self.llm_auto_apply:
                    # reuse the model's earlier answer ('' = it found no field), never re-ask
                    d["_llm_cached"] = target or NO_FIELD
                elif target in self.by_name:
                    d.update(target=target, confidence=0.8, method=f"memory:{source}", _locked=True)
            elif target == "":
                d["method"] = "memory:keep"
                d["_locked"] = True
            elif target in self.by_name:
                d.update(target=target, confidence=1.0 if source == "user" else 0.8,
                         method=f"memory:{source}", _locked=True)

        # Layers 2-4: candidates
        for d in decisions:
            if not d.get("_locked"):
                d["_cands"] = self._candidates(d["source_column"], d["type"])

        # Which entity is this sheet mostly about? Breaks ties like 'Suburb'/'ID'.
        groups = Counter()
        for d in decisions:
            top = d["target"] or (d["_cands"][0][0] if d["_cands"] and d["_cands"][0][1] >= 0.9 else None)
            if top:
                groups[self.by_name[top]["group"]] += 1
        dominant = groups.most_common(1)[0][0] if groups else None
        if dominant:
            total = sum(groups.values())

            def adjust(field, score, method):
                g = self.by_name[field]["group"]
                if g == dominant:
                    return score + GROUP_BIAS
                if method != "exact" and groups[g] / total < 0.2:
                    return score - CROSS_GROUP_PENALTY
                return score

            for d in decisions:
                d["_cands"] = sorted(((f, adjust(f, s, m), m) for f, s, m in d["_cands"]),
                                     key=lambda x: -x[1])

        self._sheet_entity = dominant
        self._sheet_headers = [d["source_column"] for d in decisions]

        # Unique assignment: strongest evidence claims a field first
        taken = {d["target"] for d in decisions if d["target"]}
        order = sorted((d for d in decisions if not d.get("_locked") and d["_cands"]),
                       key=lambda d: -d["_cands"][0][1])
        for d in order:
            free = [c for c in d["_cands"] if c[0] not in taken]
            if not free:
                continue
            field, score, method = free[0]
            if score >= AUTO:
                d.update(target=field, confidence=round(min(score, 1.0), 3), method=method)
                taken.add(field)
            elif score >= GREY:
                d.update(suggestion=field, suggested_by="rules", confidence=round(score, 3))
                cached = d.get("_llm_cached")
                if cached == NO_FIELD:
                    picked = None
                elif cached in self.by_name:
                    picked = (cached, 0.8)
                else:
                    picked = self._ask_llm(d, free)
                if not picked or picked[0] in taken:
                    continue
                if self.llm_auto_apply:
                    d.update(target=picked[0], confidence=round(picked[1], 3), method="llm")
                    taken.add(picked[0])
                else:
                    d.update(suggestion=picked[0], suggested_by="llm")

        for d in decisions:
            d.pop("_cands", None)
            d.pop("_locked", None)
            d.pop("_llm_cached", None)
        return decisions

    # -----------------------------------------------------------------
    def map_by_values(self, decisions, values_by_header):
        """Headerless sheet: the column names are placeholders, so map from what the
        values are. Applied directly (there is no original header to lose); the
        consolidator raises a WARN asking for a check."""
        taken = {d["target"] for d in decisions if d["target"]}

        def assign(d, field):
            if field in taken or field not in self.by_name:
                return False
            d.update(target=field, confidence=0.75, method="values", suggestion=None, suggested_by="")
            taken.add(field)
            return True

        name_like = []
        for i, d in enumerate(decisions):
            if d["target"]:
                continue
            vals = non_empty(values_by_header[d["source_column"]])[:300]
            if not vals:
                continue
            share = lambda pred: sum(1 for v in vals if pred(v)) / len(vals)
            if d["type"] == "email":
                assign(d, "contact_email_address")
            elif d["type"] == "phone":
                mobile = share(lambda v: re.sub(r"\D", "", re.split(r"[,;/]", v)[0])
                               .startswith(("04", "614")))
                order = (["contact_mobile"] if mobile >= 0.6 else []) + \
                        ["contact_phone_home", "contact_phone_work"]
                any(assign(d, f) for f in order)
            elif d["type"] == "postcode":
                assign(d, "contact_mailing_address_postcode")
            elif d["type"] == "text" and share(RE_STREET.match) >= 0.6:
                assign(d, "contact_mailing_address_line1")
            elif d["type"] == "text" and share(RE_NAME_WORD.match) >= 0.8:
                name_like.append(i)

        # Two neighbouring single-word name columns: first name, then surname
        for a, b in zip(name_like, name_like[1:]):
            if b == a + 1 and assign(decisions[a], "contact_first_name"):
                assign(decisions[b], "contact_surname")
                break

    def _ask_llm(self, decision, candidates):
        # One question per header per run - the same export split across files
        # repeats every header, and 'no answer' is worth remembering too
        key = normalize_header(decision["source_column"])
        if key in self._llm_seen:
            return self._llm_seen[key]
        if not self.llm or self.llm_calls >= self.max_llm_calls or not self.llm.available:
            return None
        self.llm_calls += 1
        self.log(f"[DARIS] Asking {self.llm.label} about column '{decision['source_column']}'...")
        fields = [self.by_name[f] for f, _s, _m in candidates[:5]]
        siblings = [h for h in self._sheet_headers if h != decision["source_column"]]
        picked = self.llm.choose_field(decision["source_column"], decision["sample"], fields,
                                       entity=self._sheet_entity, siblings=siblings)
        if not (picked and picked[1] >= 0.6):
            picked = None
        if self.memory:
            self.memory.remember(decision["source_column"], picked[0] if picked else "",
                                 source=f"llm:{self.llm.provider}")
        self._llm_seen[key] = picked
        return picked
