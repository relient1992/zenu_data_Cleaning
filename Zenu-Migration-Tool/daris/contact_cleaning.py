import re
from collections import Counter

# =====================================================================
# CONTACT CLEANING  - Python ports of Daryl's Excel macros
# ---------------------------------------------------------------------
#   split_names()           <- Contact_name_splitting.bas  (SplitContactNames)
#   ContactDetailsCleaner   <- Clean_contactdetails.bas    (CleanContactDetails v2)
# Rule-for-rule ports, quirks included. The spec is the pair of guides in
# reference/guides/; tests/test_contact_cleaning.py checks every worked
# example in them. If the macro and this file ever disagree, the macro wins.
#
# VBA details kept on purpose:
#   * Trim$ only strips spaces (not tabs / newlines)
#   * WorksheetFunction.Trim also collapses runs of spaces
#   * Like "[A-Za-z]" is ASCII-only
# =====================================================================

# --------------------------------------------------------------------
# NAME SPLITTING
# --------------------------------------------------------------------
NAME_TITLES = {"mr": "Mr", "ms": "Ms", "miss": "Miss", "mrs": "Mrs", "dr": "Dr"}
NAME_PLACEHOLDERS = {"n/a", "na", "none", "-", "--", "+"}
MAX_PEOPLE = 5
NAME_DEFAULTS = {
    "strip_caps_labels": True,     # macro constant STRIP_CAPS_LABELS
}

_ALPHA_WORD = re.compile(r"[A-Za-z'\-]+")
_CAPS_WORD = re.compile(r"[A-Z'\-]{2,}")


def _clean_name(s) -> str:
    """CleanName: nbsp/tab -> space, Excel TRIM, placeholders -> blank."""
    s = "" if s is None else str(s)
    s = s.replace("\xa0", " ").replace("\t", " ")
    s = re.sub(r" {2,}", " ", s).strip(" ")
    return "" if s.lower() in NAME_PLACEHOLDERS else s


def _norm_title(s: str) -> str:
    s = s.strip(" ").lower()
    if s.endswith("."):
        s = s[:-1]
    return NAME_TITLES.get(s, "")


def _take_title(s: str):
    """'Dr Sofija' -> ('Sofija', 'Dr'); no title -> (s, '')."""
    if not s:
        return s, ""
    sp = s.find(" ")
    word = s if sp < 0 else s[:sp]
    title = _norm_title(word)
    if not title:
        return s, ""
    return ("" if sp < 0 else s[sp + 1:].strip(" ")), title


def _strip_caps_label(s: str):
    """'Megan SEA TEMPLE' -> ('Megan', 'SEA TEMPLE'). The first word is never a label,
    and a label is only removed when a mixed/lower-case word precedes it."""
    if not s:
        return s, ""
    w = s.split(" ")
    k = len(w)
    for i in range(len(w) - 1, 0, -1):
        if _CAPS_WORD.fullmatch(w[i]):
            k = i
        else:
            break
    if k > len(w) - 1:
        return s, ""
    if not any(w[i] != w[i].upper() for i in range(k)):
        return s, ""
    return " ".join(w[:k]), " ".join(w[k:])


def _split_parts(s: str):
    """Split on ' and ' (any case), ',', '&', '/', '+', ';'."""
    if not s:
        return []
    s = re.sub(" and ", "|", " " + s + " ", flags=re.IGNORECASE)
    for sep in (",", "&", "/", "+", ";"):
        s = s.replace(sep, "|")
    return [p for p in (_clean_name(x) for x in s.split("|")) if p]


_LEADING_SEP = re.compile(r"^(?:&|\+|/|and(?=\s))\s*(.*)$", re.IGNORECASE)


def _partner_in_surname(d: str, e: str, notes: list):
    """First 'Munny', surname '& Brawna Kumar' -> first 'Munny & Brawna', surname 'Kumar' (shared).
    Surname '& Stephen' (one word) -> first 'Finlae & Stephen', no surname. Only when the first name
    holds one person and the rest of the surname holds one."""
    m = _LEADING_SEP.match(e) if d and e else None
    if not m or len(_split_parts(d)) != 1:
        return d, e
    rest = _clean_name(m.group(1))
    if not rest or len(_split_parts(rest)) != 1:
        return d, e
    words = rest.split(" ")
    notes.append("second person's first name was in the surname column")
    if len(words) == 1:
        return f"{d} & {rest}", ""
    return f"{d} & {' '.join(words[:-1])}", words[-1]


def split_names(title, first, last, options=None):
    """One record's raw Title / First / Last -> (people, note).
    people: up to 5 (title, first, surname) tuples; note: '; '-joined review notes."""
    opt = {**NAME_DEFAULTS, **(options or {})}
    notes = []
    c, d, e = _clean_name(title), _clean_name(first), _clean_name(last)

    title_c = _norm_title(c)
    if c and not title_c:
        if not d and _ALPHA_WORD.fullmatch(c):
            d = c
        else:
            notes.append(f"invalid title '{c}' ignored")

    if opt["strip_caps_labels"]:
        d, label = _strip_caps_label(d)
        if label:
            notes.append(f"label '{label}' removed")
        e, label = _strip_caps_label(e)
        if label:
            notes.append(f"label '{label}' removed")

    d, e = _partner_in_surname(d, e, notes)
    d_parts, e_parts = _split_parts(d), _split_parts(e)
    nd, ne = len(d_parts), len(e_parts)
    total = max(nd, ne)
    if total == 0:
        if title_c:
            notes.append("title only, no name")
        return [], "; ".join(notes)
    if total > MAX_PEOPLE:
        notes.append(f"{total} people found, first {MAX_PEOPLE} kept")
        total = MAX_PEOPLE

    people = []
    for i in range(total):
        fn = d_parts[i] if i < nd else ""
        if ne == 1:
            sn = e_parts[0]                     # one surname is shared by everyone
        elif i < ne:
            sn = e_parts[i]
        else:
            sn = ""
        fn, tt = _take_title(fn)
        if not tt:
            sn, tt = _take_title(sn)
        if not sn and " " in fn:                # embedded full name: last word = surname
            sp = fn.rfind(" ")
            fn, sn = fn[:sp].strip(" "), fn[sp + 1:]
        if i == 0 and not tt:
            tt = title_c
        if fn or sn:
            people.append((tt, fn, sn))
    if len(people) > 1 and ne == 0:
        notes.append("no surname in raw data")
    return people, "; ".join(notes)


# --------------------------------------------------------------------
# PHONES & EMAILS
# --------------------------------------------------------------------
DETAIL_DEFAULTS = {
    "dedupe_in_row": True,               # DEDUPE_IN_ROW
    "multi_sep": " | ",                  # MULTI_SEP
    "intl_min": 8,                       # INTL_MIN
    "intl_max": 15,                      # INTL_MAX (E.164)
    "intl_require_plus": False,          # INTL_REQUIRE_PLUS
    "keep_extras_after_merge": False,    # KEEP_EXTRAS_AFTER_MERGE
    "merge_extra_emails_to_o": True,     # MERGE_EXTRA_EMAILS_TO_O
}

# Output slots (macro columns) and their meaning
SLOTS = ("G", "H", "I", "J", "K", "L", "M", "N")
SLOT_NAMES = {"G": "Mobile", "H": "Mobile Landline", "I": "Home Mobile", "J": "HomeLandline",
              "K": "Work Mobile", "L": "WorkLandline", "M": "Fax Mobile", "N": "Fax Landline"}
INVALID_COLS = {"MOB": "W", "HOME": "X", "WORK": "Y", "FAX": "Z"}
MOBILE_TARGETS = {"MOB": (("G", "I", "K", "M"), "M"), "HOME": (("G", "I"), "I"),
                  "WORK": (("G", "K"), "K"), "FAX": (("G", "M"), "M")}
LANDLINE_TARGETS = {"MOB": (("J", "L"), "H"), "HOME": (("J", "L"), "H"),
                    "WORK": (("L", "J"), "H"), "FAX": (("N",), "H")}

_HAS_LETTER = re.compile(r"[A-Za-z]")
_KEEP_PHONE_CHAR = re.compile(r"[0-9A-Za-z+]")
_LOCAL_EMAIL = re.compile(r"[A-Za-z0-9._%+'\-]+")
_DOMAIN_EMAIL = re.compile(r"[A-Za-z0-9.\-]+")
_TLD = re.compile(r"[A-Za-z]{2,}")
_EMAIL_WRAP = "<>()[]\"'."


def _digits(s: str) -> str:
    return "".join(ch for ch in s if "0" <= ch <= "9")


def _append(target: str, value: str, sep: str) -> str:
    return value if not target else target + sep + value


def is_valid_email(e: str) -> bool:
    if len(e) < 6:
        return False
    at = e.find("@")
    if at < 1 or "@" in e[at + 1:] or ".." in e:
        return False
    local, domain = e[:at], e[at + 1:]
    if local.startswith(".") or local.endswith("."):
        return False
    if "." not in domain or domain[0] in ".-" or domain[-1] in ".-":
        return False
    if not _LOCAL_EMAIL.fullmatch(local) or not _DOMAIN_EMAIL.fullmatch(domain):
        return False
    return bool(_TLD.fullmatch(domain[domain.rfind(".") + 1:]))


def _strip_email_wrap(e: str) -> str:
    if e[:7].lower() == "mailto:":
        e = e[7:]
    while e and e[0] in _EMAIL_WRAP:
        e = e[1:]
    while e and e[-1] in _EMAIL_WRAP:
        e = e[:-1]
    return e


def _strip_text_parens(s: str) -> str:
    """Remove '(work)', '(mob)'; keep '(07)', '(20)'."""
    a = s.find("(")
    while a >= 0:
        b = s.find(")", a + 1)
        if b < 0:
            break
        if _HAS_LETTER.search(s[a + 1:b]):
            s = s[:a] + " " + s[b + 1:]
            a = s.find("(", a)
        else:
            a = s.find("(", b + 1)
    return s


def _clean_phone_text(s: str) -> str:
    out = []
    for ch in s:
        if _KEEP_PHONE_CHAR.fullmatch(ch):
            out.append(ch)
        elif ch in (" ", "\t", "\xa0"):
            out.append(" ")
    out = re.sub(r" {2,}", " ", "".join(out)).strip(" ")
    if out.startswith("+ "):
        out = "+" + out[2:]
    return out


def _drop_leading_digits(s: str, n: int) -> str:
    count = 0
    for i, ch in enumerate(s):
        if "0" <= ch <= "9":
            count += 1
            if count == n:
                return s[i + 1:].strip(" ")
    return ""


class ContactDetailsCleaner:
    """One instance per run; clean() is called once per row. Counters mirror the
    macro's summary message, plus accounting so every value can be reconciled."""

    def __init__(self, options=None):
        self.opt = {**DETAIL_DEFAULTS, **(options or {})}
        self.count = Counter()

    # ----- classification ---------------------------------------------
    def classify(self, part: str):
        """-> (kind, formatted, key, tag). kind: M mobile, L landline,
        N international (type unknown), I invalid."""
        o = self.opt
        s = _clean_phone_text(_strip_text_parens(part))
        bad = ("I", part.replace("\xa0", " ").strip(" "), "", "")
        if not s or "+" in s[1:] or _HAS_LETTER.search(s):
            return bad
        has_plus = s.startswith("+")
        d = _digits(s)
        if not d or d == d[0] * len(d):
            return bad

        if not has_plus:                          # 0011 / 00 dialling prefixes act like '+'
            pre = 4 if d.startswith("0011") and len(d) > 4 else \
                2 if d.startswith("00") and len(d) >= 11 else 0
            if pre:
                d = d[pre:]
                s = "+" + _drop_leading_digits(s, pre)
                has_plus = True

        au61 = False
        if d.startswith("610") and len(d) == 12:
            d, au61 = "0" + d[3:], True
        elif d.startswith("61") and len(d) == 11:
            d, au61 = "0" + d[2:], True
        elif not has_plus and len(d) == 9 and d[0] in "23478":
            d = "0" + d                           # leading 0 dropped by Excel

        if len(d) == 10 and d.startswith("04"):
            fmt = (f"+61 {d[1:4]} {d[4:7]} {d[7:10]}" if au61 else f"{d[:4]} {d[4:7]} {d[7:10]}")
            return "M", fmt, d, ""
        if has_plus and not au61 and d.startswith("64") and len(d) == 11:
            return "M", s, "+" + d, "NZ"
        if (not has_plus and not au61 and d.startswith("02")
                and (len(d) in (9, 11) or (len(d) == 10 and d[2] in "012"))):
            return "M", s, "+64" + d[1:], "NZ"
        if len(d) == 10 and d[:2] in ("02", "03", "07", "08"):
            fmt = (f"+61 {d[1]} {d[2:6]} {d[6:10]}" if au61 else f"{d[:2]} {d[2:6]} {d[6:10]}")
            return "L", fmt, d, ""
        if len(d) == 10 and d[:4] in ("1300", "1800") and not au61:
            return "L", f"{d[:4]} {d[4:7]} {d[7:10]}", d, ""
        if len(d) == 6 and d.startswith("13") and not has_plus:
            return "L", f"13 {d[2:4]} {d[4:6]}", d, ""
        if len(d) == 8 and d[0] in "23456789" and not has_plus and not au61:
            return "L", f"{d[:4]} {d[4:8]}", d, ""   # local number - never invent an area code
        if au61:
            return bad                            # +61 but not a real AU pattern
        if o["intl_min"] <= len(d) <= o["intl_max"] and (has_plus or not o["intl_require_plus"]):
            return "N", s, ("+" + d if has_plus else d), "INTL"
        return bad

    # ----- splitting ----------------------------------------------------
    def split_phones(self, s: str):
        s = s.replace("\xa0", " ").replace("\t", " ")
        s = s.replace("\r\n", ",").replace("\r", ",").replace("\n", ",")
        for sep in (";", "/", "&", "|"):
            s = s.replace(sep, ",")
        s = re.sub(" and ", ",", s, flags=re.IGNORECASE)
        s = re.sub(" or ", ",", s, flags=re.IGNORECASE)
        if len(_digits(s)) > self.opt["intl_max"]:   # clearly 2+ numbers: '-' separates too
            s = s.replace("-", ",")
        return s.split(",")

    def extract_emails(self, raw: str, found: list, dropped: list = None) -> str:
        """Move valid emails out of a phone cell; return what's left for the phone logic.
        Macro quirk kept: if what's left has no digits it is blanked - that clears labels
        like 'Email:' but also deletes an invalid email such as 'john@x'. DARIS records
        that text in `dropped` so it reaches the review log instead of vanishing."""
        if "@" not in raw:
            return raw
        s = raw
        for p in ("\r\n", "\r", "\n", "\t", "\xa0", ",", ";", "/", "|", "&"):
            s = s.replace(p, " ")
        for token in s.split(" "):
            if "@" in token:
                e = _strip_email_wrap(token.strip(" "))
                if is_valid_email(e):
                    found.append(e)
                    raw = raw.replace(token, " ")
                    self.count["emails_moved_from_phones"] += 1
        raw = raw.strip(" ")
        if not _digits(raw):
            if raw and dropped is not None:
                dropped.append(raw)
                self.count["text_dropped_from_phones"] += 1
            return ""
        return raw

    # ----- placement ----------------------------------------------------
    def _place(self, slots, targets, overflow, value):
        for t in targets:
            if not slots[t]:
                slots[t] = value
                return
        slots[overflow] = _append(slots[overflow], value, self.opt["multi_sep"])

    def _process(self, raw, src, only_kind, slots, invalid, seen):
        if not raw:
            return
        sep = self.opt["multi_sep"]
        for part in self.split_phones(raw):
            if not part.strip(" "):
                continue
            kind, fmt, key, tag = self.classify(part)
            if kind == "N":                       # international: follow the source column
                kind = "M" if src == "MOB" else "L"
            if kind == "I":
                if only_kind != "L":              # counted once (pass 1 for the Mobile column)
                    invalid[INVALID_COLS[src]] = _append(invalid[INVALID_COLS[src]], fmt, sep)
                    self.count["invalid_numbers"] += 1
                continue
            if only_kind and kind != only_kind:
                continue                          # handled in the other pass
            if self.opt["dedupe_in_row"] and key in seen:
                self.count["duplicates_skipped"] += 1
                continue
            seen.add(key)
            self.count["numbers_placed"] += 1
            if tag == "NZ":
                self.count["nz_mobiles"] += 1
            elif tag == "INTL":
                self.count["international"] += 1
            targets, overflow = (MOBILE_TARGETS if kind == "M" else LANDLINE_TARGETS)[src]
            self._place(slots, targets, overflow, fmt)

    def _merge_extras(self, slots):
        sep, keep = self.opt["multi_sep"], self.opt["keep_extras_after_merge"]
        for idx in ("I", "K", "M"):
            if slots[idx]:
                slots["G"] = _append(slots["G"], slots[idx], sep)
                if not keep:
                    slots[idx] = ""
        if slots["H"]:
            slots["J"] = _append(slots["J"], slots["H"], sep)
            if not keep:
                slots["H"] = ""

    def _emails(self, raw: str):
        primary, extra, seen = "", "", set()
        sep = self.opt["multi_sep"]
        if not raw:
            return primary, extra
        s = raw.replace("\xa0", " ")
        s = s.replace("\r\n", ",").replace("\r", ",").replace("\n", ",")
        for p in (";", " ", "/", "|", "\t"):
            s = s.replace(p, ",")
        for token in s.split(","):
            e = _strip_email_wrap(token.strip(" "))
            if not e:
                continue
            if e.lower() in seen:
                self.count["emails_duplicate_skipped"] += 1
                continue
            seen.add(e.lower())
            if is_valid_email(e) and (not primary or self.opt["merge_extra_emails_to_o"]):
                primary = _append(primary, e, sep)
                self.count["emails_valid"] += 1
            else:
                extra = _append(extra, e, sep)
                self.count["emails_invalid_or_extra"] += 1
        return primary, extra

    # ----- one row ----------------------------------------------------------
    def phone_parts(self, raw: str) -> int:
        return sum(1 for p in self.split_phones(raw) if p.strip(" ")) if raw else 0

    def clean(self, email="", mobile="", work="", home="", fax=""):
        """Raw B..F -> dict with G..O, U, W..Z (macro column letters), plus 'dropped':
        text the macro removes from phone cells without placing it anywhere."""
        text = lambda v: "" if v is None else str(v).strip(" ")
        found, dropped = [], []
        raw_mob = self.extract_emails(text(mobile), found, dropped)
        raw_home = self.extract_emails(text(home), found, dropped)
        raw_work = self.extract_emails(text(work), found, dropped)
        raw_fax = self.extract_emails(text(fax), found, dropped)
        # Accounting: every phone part must end placed, skipped as a duplicate or invalid
        self.count["phone_parts_in"] += sum(self.phone_parts(r) for r in (raw_mob, raw_home, raw_work, raw_fax))

        slots = {k: "" for k in SLOTS}
        invalid = {k: "" for k in INVALID_COLS.values()}
        seen = set()
        self._process(raw_mob, "MOB", "M", slots, invalid, seen)
        self._process(raw_home, "HOME", "", slots, invalid, seen)
        self._process(raw_work, "WORK", "", slots, invalid, seen)
        self._process(raw_fax, "FAX", "", slots, invalid, seen)
        self._process(raw_mob, "MOB", "L", slots, invalid, seen)
        self._merge_extras(slots)

        primary, extra = self._emails(text(email) + "," + ",".join(found))
        self.count["rows"] += 1
        return {**slots, "O": primary, "U": extra, **invalid, "dropped": dropped}

    def reconciliation(self) -> dict:
        c = self.count
        accounted = c["numbers_placed"] + c["duplicates_skipped"] + c["invalid_numbers"]
        return {"phone_parts_in": c["phone_parts_in"], "phone_parts_accounted": accounted,
                "phones_ok": c["phone_parts_in"] == accounted}


# --------------------------------------------------------------------
# APPLY TO A TABLE  (DARIS consolidated data)
# --------------------------------------------------------------------
# Which Zenu field feeds each macro input
ROLE_FIELDS = {
    "title": "contact_title", "first": "contact_first_name", "last": "contact_surname",
    "email": "contact_email_address", "mobile": "contact_mobile", "work": "contact_phone_work",
    "home": "contact_phone_home", "fax": "contact_fax",
    # address/suburb splitting (address_cleaning.py) - not part of the macros
    "addr_line1": "contact_mailing_address_line1", "addr_suburb": "contact_mailing_address_suburb",
    "addr_state": "contact_mailing_address_state", "addr_postcode": "contact_mailing_address_postcode",
}
ADDRESS_ROLES = ("addr_line1", "addr_suburb", "addr_state", "addr_postcode")
# Macro output slot -> role whose column receives it
SLOT_ROLE = {"G": "mobile", "J": "home", "L": "work", "N": "fax", "O": "email"}
EXTRA_SLOT_COLUMNS = {"H": "mobile_landline_overflow", "I": "home_mobile", "K": "work_mobile",
                      "M": "fax_mobile"}           # only filled when keep_extras_after_merge=True
REVIEW_COLUMNS = {"U": "invalid_extra_email", "W": "invalid_mobile", "X": "invalid_home",
                  "Y": "invalid_work", "Z": "invalid_fax"}
NOTES_COLUMN = "cleaning_notes"
_EXCEL_FLOAT = re.compile(r"^\d+\.0$")


def _cell(v) -> str:
    # Only a real missing value is empty - the text 'Nan' is a first name
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v)


def _phone_cell(v) -> str:
    """Excel keeps phones as numbers: '412345678.0' -> '412345678' (what VBA CStr gives)."""
    s = _cell(v).strip(" ")
    return s[:-2] if _EXCEL_FLOAT.match(s) else s


def clean_contacts_frame(df, labels: dict, name_options=None, detail_options=None,
                         accepted_titles=None, address_splitter=None):
    """Clean every row of `df` (a pandas DataFrame of strings).
    labels: role -> column name in df (roles in ROLE_FIELDS; missing roles allowed).
    address_splitter: an AddressSplitter (needs the suburb list) or None to skip addresses.
    Returns (cleaned_df, audit_rows, summary). The input df is not modified."""
    out = df.copy()
    n = len(df)
    col = {role: labels.get(role) for role in ROLE_FIELDS}
    has_names = any(col[r] for r in ("title", "first", "last"))
    has_details = any(col[r] for r in ("email", "mobile", "work", "home", "fax"))
    has_address = address_splitter is not None and any(col[r] for r in ADDRESS_ROLES)
    get = lambda role, conv=_cell: [conv(v) for v in df[col[role]].tolist()] if col[role] else [""] * n

    def target(role):
        return col[role] or ROLE_FIELDS[role]

    audit, notes = [], [[] for _ in range(n)]
    trace = lambda i: (i + 2, df["__source_file__"].iat[i] if "__source_file__" in df else "",
                       df["__source_row__"].iat[i] if "__source_row__" in df else "")
    summary = {"rows": n, "names_cleaned": has_names, "details_cleaned": has_details,
               "addresses_cleaned": has_address}

    def set_column(name, values, before=None):
        if name not in out.columns:
            if not any(values):
                return
            out[name] = ""
        out[name] = values
        old = before if before is not None else [""] * n
        for i, (b, a) in enumerate(zip(old, values)):
            if b != a:
                audit.append((*trace(i), name, b, a))

    # ---------------- names ----------------
    if has_names:
        raw_t, raw_f, raw_l = get("title"), get("first"), get("last")
        people_all = []
        slot_counts = Counter()
        for i in range(n):
            people, note = split_names(raw_t[i], raw_f[i], raw_l[i], name_options)
            people_all.append(people)
            slot_counts.update(range(1, len(people) + 1))
            if note:
                notes[i].append(note)
        for k, part in enumerate(("title", "first_name", "surname")):
            values = [p[0][k] if p else "" for p in people_all]
            role = ("title", "first", "last")[k]
            set_column(target(role), values, [raw_t, raw_f, raw_l][k])
        max_people = max((len(p) for p in people_all), default=0)
        for slot in range(2, max_people + 1):
            for k, part in enumerate(("title", "first_name", "surname")):
                set_column(f"contact{slot}_{part}",
                           [p[slot - 1][k] if len(p) >= slot else "" for p in people_all])
        titles = Counter(t for p in people_all for (t, _f, _s) in p if t)
        summary.update({
            "records_with_names": sum(1 for p in people_all if p),
            "records_split": sum(1 for p in people_all if len(p) > 1),
            "people": sum(len(p) for p in people_all),
            "contact_slots": {f"Contact {k}": slot_counts[k] for k in range(1, MAX_PEOPLE + 1)},
            "name_notes": sum(1 for nl in notes if nl),
            "labels_removed": sum(1 for nl in notes if any("label '" in x for x in nl)),
            "titles": dict(titles),
            "titles_not_accepted": {t: c for t, c in titles.items()
                                    if accepted_titles and t not in accepted_titles},
        })

    # ---------------- phones & emails ----------------
    if has_details:
        cleaner = ContactDetailsCleaner(detail_options)
        raw = {r: get(r, _phone_cell if r != "email" else _cell)
               for r in ("email", "mobile", "work", "home", "fax")}
        rows = []
        for i in range(n):
            res = cleaner.clean(raw["email"][i], raw["mobile"][i], raw["work"][i],
                                raw["home"][i], raw["fax"][i])
            if res["dropped"]:
                notes[i].append("removed from phone cells (no digits): "
                                + ", ".join(f"'{d}'" for d in res["dropped"]))
            rows.append(res)
        for slot, role in SLOT_ROLE.items():
            set_column(target(role), [r[slot] for r in rows], raw[role])
        for slot, name in {**EXTRA_SLOT_COLUMNS, **REVIEW_COLUMNS}.items():
            set_column(name, [r[slot] for r in rows])

        # Independent check: values sitting in the output phone cells == values placed
        sep = cleaner.opt["multi_sep"]
        placed_out = sum(len(r[s].split(sep)) for r in rows for s in SLOTS if r[s])
        c = cleaner.count
        rec = cleaner.reconciliation()
        filled = lambda slot: sum(1 for r in rows if r[slot])
        summary.update({
            # the macro's own end-of-run message
            "Mobile (G)": filled("G"), "Home Landline (J)": filled("J"),
            "Work Landline (L)": filled("L"), "Fax (N)": filled("N"), "Email (O)": filled("O"),
            "Invalid/extra email (U)": filled("U"), "NZ mobiles placed": c["nz_mobiles"],
            "International numbers placed": c["international"],
            "Invalid numbers (W:Z)": c["invalid_numbers"],
            "Emails moved from phone columns": c["emails_moved_from_phones"],
            # DARIS accounting
            "phone_values_in": rec["phone_parts_in"], "phone_values_placed": c["numbers_placed"],
            "phone_duplicates_skipped": c["duplicates_skipped"],
            "phone_values_in_output": placed_out,
            "phones_reconciled": rec["phones_ok"] and placed_out == c["numbers_placed"],
            "text_dropped_from_phones": c["text_dropped_from_phones"],
            "multi_value_mobile": sum(1 for r in rows if sep in r["G"]),
            "multi_value_email": sum(1 for r in rows if sep in r["O"]),
        })

    # ---------------- addresses ----------------
    if has_address:
        raw_a = {r: get(r, _phone_cell if r == "addr_postcode" else _cell) for r in ADDRESS_ROLES}
        results = [address_splitter.clean(*(raw_a[r][i] for r in ADDRESS_ROLES)) for i in range(n)]
        for role, key in zip(ADDRESS_ROLES, ("line1", "suburb", "state", "postcode")):
            set_column(target(role), [r[key] for r in results], raw_a[role])
        for i, r in enumerate(results):
            notes[i].extend(r["notes"])
        flags = Counter(f for r in results for f in r["flags"])
        summary.update({
            "addresses_checked": sum(1 for i in range(n) if any(raw_a[r][i] for r in ADDRESS_ROLES)),
            "addresses_split": flags["split"], "suburb_spelling_fixed": flags["spelling_fixed"],
            "states_from_address": flags["state_from_address"],
            "postcodes_from_address": flags["postcode_from_address"],
            "states_filled_from_list": flags["state_filled"], "postcodes_filled_from_list": flags["postcode_filled"],
            "unknown_suburbs": flags["unknown_suburb"], "ambiguous_state": flags["ambiguous_state"],
            "address_conflicts": flags["conflict"], "state_mismatch": flags["state_mismatch"],
            "postcode_mismatch": flags["postcode_mismatch"],
        })

    note_values = ["; ".join(nl) for nl in notes]
    if any(note_values):
        out[NOTES_COLUMN] = note_values
    summary["rows_out"] = len(out)
    summary["changed_cells"] = len(audit)
    return out, audit, summary
