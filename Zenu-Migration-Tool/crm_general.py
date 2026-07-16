import pandas as pd
import sqlite3
import os
import re
import sys
import numpy as np
from collections import Counter

# ---------------------------------------------------------------------------
# COUPLE DETECTION & TITLE HELPERS
# ---------------------------------------------------------------------------

_COUPLE_PATTERN = re.compile(
    r'\band\b|\s*&\s*|\s*/\s*|\s*,\s*',
    flags=re.IGNORECASE
)

_COMPANY_KEYWORDS = re.compile(
    r'\b(pty\s*ltd|pty|ltd|inc|llc|corp|corporation|trust|group|holdings|'
    r'enterprises|proprietary|limited|p/l)\b',
    flags=re.IGNORECASE
)

_JUNK_CHARS = re.compile(r'[0-9!@#$%^*()_+=\[\]{};:"\\|<>/]+')


def _is_company(text: str) -> bool:
    return bool(_COMPANY_KEYWORDS.search(text))


def _clean_name_text(text: str) -> str:
    """Strip junk characters, collapse whitespace, title-case."""
    text = _JUNK_CHARS.sub('', text)
    text = re.sub(r' +', ' ', text).strip()
    return text.title()


def _is_couple(name_val: str) -> bool:
    """Return True if the name field contains two people joined by 'and' / '&'."""
    return bool(_COUPLE_PATTERN.search(str(name_val)))


def _split_couple_firstnames(name_val: str) -> list[str]:
    """Split a couple name into individual first names."""
    parts = _COUPLE_PATTERN.split(str(name_val))
    result = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        tokens = p.split()
        result.append(tokens[0].title() if tokens else '')
    return [n for n in result if n]


def _extract_shared_surname(name_val: str):
    """For a single-field couple like 'John and Jane Smith', pull the trailing
    surname ('Smith') that should be shared by both partners. Returns pd.NA if
    the field carries only first names (e.g. 'John and Jane')."""
    parts = _COUPLE_PATTERN.split(str(name_val))
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return pd.NA
    last_tokens = parts[-1].split()
    if len(last_tokens) >= 2:
        return _clean_name_text(" ".join(last_tokens[1:]))
    return pd.NA


# ---------------------------------------------------------------------------
# SINGLE-FIELD NAME SPLITTER  (ported from the legacy VB / EPPlus routine)
# ---------------------------------------------------------------------------

# Business / trust indicators meaning "this is not a person" -> do not split.
# " The " is matched case-sensitively (as in the VB Regex "The\s") so it catches
# trust names like "The Smith Family Trust" without tripping on a lowercase
# 'the' inside an ordinary name. The remaining keywords are matched
# case-insensitively (the VB version compared a few of these against a
# lower-cased string with capitalised needles, so they never actually fired -
# here they all work as intended).
_VB_THE_PATTERN = re.compile(r"The\s")
_VB_SKIP_KEYWORDS = (
    " pty ltd", " pty limited", " trustee", " accounts ",
    " corporation", " development", " conveyancing",
)


def _normalize_name_separators(text: str) -> str:
    """Replicate the VB string-cleanup chain: turn every couple separator
    (&, ;, +, comma, ' And ') into a single lower-case ' and ' and strip dots.
    The order of replacements matches the original exactly."""
    text = str(text)
    text = text.replace(" & ", " and ")
    text = text.replace(";", " and ")
    text = text.replace(" And ", " and ")
    text = text.replace(" + ", " and ")
    text = text.replace(" . ", "")
    text = text.replace(".", " ")
    text = text.replace(". ", "")
    text = text.replace(", ", " and ")
    text = text.replace(",", " and ")    # bare comma: 'Adelle,Paul Rogers'
    text = text.replace("/", " and ")    # slash:      'Chris / Caroline ...'
    text = text.replace(" &", " and ")
    text = text.replace("& ", " and ")
    text = text.replace("- ", "-")
    return text


def _vb_should_skip(cell: str) -> bool:
    """True when the (already-normalized) name looks like a company / trust /
    business and should NOT be split into first + surname."""
    if _VB_THE_PATTERN.search(cell):
        return True
    low = cell.lower()
    return any(kw in low for kw in _VB_SKIP_KEYWORDS)


def split_single_field_name(name_value, surname_value=None):
    """Port of the legacy VB single-field name splitter.

    Returns (is_person, people):
      * is_person is False for company / trust rows (caller routes to company_name)
      * people is a list of {"first": <str|NA>, "surname": <str|NA>} dicts,
        one per person (1 for a single, 2+ for a couple).

    Faithful to the VB rules:
      * separators &, ;, +, comma and ' And ' all collapse to ' and '
      * the field is split on ' and ' into individual people
      * an optional surname-column value is appended to every person
      * per person: first name = everything before the LAST space,
        surname = the LAST token
      * a person with no surname inherits the surname of the next person who
        has one (couples sharing a family name)
    """
    if pd.isna(name_value):
        return True, []

    cell = _normalize_name_separators(str(name_value))
    cell = re.sub(r" +", " ", cell).strip()
    if not cell or cell.lower() in ("nan", "none", "null"):
        return True, []

    if _vb_should_skip(cell):
        return False, []

    surname_extra = ""
    if surname_value is not None and not pd.isna(surname_value):
        surname_extra = re.sub(r" +", " ", str(surname_value)).strip()
        if surname_extra.lower() in ("nan", "none", "null"):
            surname_extra = ""

    parts = [p.strip() for p in cell.split(" and ") if p.strip()]

    people = []
    for part in parts:
        # Pull any leading title (Mr/Mrs/Ms/Miss/Dr...) off the NAME portion
        # only; the surname column is appended afterwards.
        tokens = part.split()
        title, tokens = _extract_title_from_tokens(tokens)
        part_wo_title = " ".join(tokens).strip()

        full = (f"{part_wo_title} {surname_extra}".strip()
                if surname_extra else part_wo_title)

        if not full:
            # The name held nothing but a title (e.g. "Mr").
            people.append({"title": title, "first": pd.NA, "surname": pd.NA})
        elif " " not in full:
            # Single token -> first name only; surname filled by inheritance.
            people.append({"title": title,
                           "first": _clean_name_text(full), "surname": pd.NA})
        else:
            cut = full.rfind(" ")
            people.append({
                "title":   title,
                "first":   _clean_name_text(full[:cut].strip()),
                "surname": _clean_name_text(full[cut + 1:].strip()),
            })

    # Inherit a missing surname from the next person who has one.
    n = len(people)
    for i in range(n):
        if pd.isna(people[i]["surname"]):
            for j in range(i + 1, n):
                if not pd.isna(people[j]["surname"]):
                    people[i]["surname"] = people[j]["surname"]
                    break

    return True, people


def _split_couple_titles(title_val, n_people: int) -> list:
    """Split a title cell into exactly n_people title values."""
    if pd.isna(title_val):
        return [pd.NA] * n_people

    raw = str(title_val).strip()
    if not raw or raw.lower() in ('nan', 'none', 'null'):
        return [pd.NA] * n_people

    parts = _COUPLE_PATTERN.split(raw)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) >= n_people:
        return [p.title() for p in parts[:n_people]]

    if len(parts) == 1:
        return [parts[0].title()] * n_people

    result = [p.title() for p in parts]
    while len(result) < n_people:
        result.append(result[0])
    return result


# ---------------------------------------------------------------------------
# TITLE EXTRACTION  (pull Mr / Mrs / Ms / Miss / Dr ... out of name fields)
# ---------------------------------------------------------------------------

# Maps a recognised title (lower-case, dots/spaces stripped) to its canonical
# display form. Extend this freely if more titles show up in the data.
_TITLE_LOOKUP = {
    "mr": "Mr", "mister": "Mr",
    "mrs": "Mrs",
    "ms": "Ms",
    "miss": "Miss",
    "dr": "Dr", "doctor": "Dr",
    "prof": "Prof", "professor": "Prof",
    "sir": "Sir",
    "mx": "Mx",
    "master": "Master",
    "madam": "Madam", "madame": "Madam",
    "rev": "Rev", "reverend": "Rev",
    "hon": "Hon",
    "lady": "Lady",
}


def _extract_title_from_tokens(tokens: list):
    """Pull a single leading title token off a token list.
    Returns (canonical_title_or_None, remaining_tokens)."""
    if not tokens:
        return None, tokens
    key = tokens[0].strip().strip(".").lower()
    if key in _TITLE_LOOKUP:
        return _TITLE_LOOKUP[key], tokens[1:]
    return None, tokens


def _pick_title(column_title, name_title):
    """Choose the final title for a contact.

    The title column is checked FIRST: if it already holds a value, that value
    wins (so when the same title also sits inside the name we keep just the one
    copy and never duplicate it). Only when the column is empty do we fall back
    to the title lifted out of the name."""
    if column_title is not None and not pd.isna(column_title) and str(column_title).strip():
        return column_title
    if name_title:
        return name_title
    return pd.NA


def _norm_title(title):
    """Canonicalise a title for comparison, or None when blank."""
    if title is None or pd.isna(title):
        return None
    t = str(title).strip().title()
    return t or None


def _partnership_type_for(self_title, partner_title=None) -> str:
    """Per-ROW partnership role.

    Husband/Wife is assigned ONLY when the couple is a Mr + Mrs pairing
    (either order): the Mr reads 'Husband', the Mrs reads 'Wife'. Every other
    combination - Ms + Ms, Mr + Ms, Mr + Mr, a lone title, no titles, Dr, etc.
    - reads 'Partner' on both rows."""
    self_t    = _norm_title(self_title)
    partner_t = _norm_title(partner_title)
    if {self_t, partner_t} == {"Mr", "Mrs"}:
        return "Husband" if self_t == "Mr" else "Wife"
    return "Partner"


def _split_couple_first_and_titles(name_val: str):
    """For a DUAL-field first-name column that holds a couple (e.g.
    'Mr John & Mrs Jane'), return (first_names, name_titles) with any embedded
    titles stripped off the names."""
    parts = _COUPLE_PATTERN.split(str(name_val))
    firsts, titles = [], []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        toks = p.split()
        t, toks = _extract_title_from_tokens(toks)
        if not toks:
            continue
        firsts.append(toks[0].title())
        titles.append(t)
    return firsts, titles


# ---------------------------------------------------------------------------
# MAIN NAME PARSER  
# ---------------------------------------------------------------------------

def advanced_name_parser(val, target_field: str, split_mode: str = "single_field"):
    if pd.isna(val):
        return pd.NA
    text = str(val).strip()
    if not text or text.lower() in ('nan', 'none', 'null'):
        return pd.NA

    if _is_company(text):
        return text.title() if target_field == 'company_name' else pd.NA
    else:
        if target_field == 'company_name':
            return pd.NA

    text = _clean_name_text(text)
    if not text:
        return pd.NA

    if split_mode == "single_field":
        parts = text.split()
        if not parts:
            return pd.NA
        if len(parts) == 1:
            return parts[0] if target_field == 'contact_first_name' else pd.NA
        if target_field == 'contact_first_name':
            return parts[0]
        else:  
            return " ".join(parts[1:])

    elif split_mode == "dual_field":
        return text  

    return text


# ---------------------------------------------------------------------------
# COUPLE EXPANSION & PARTNERSHIP GENERATOR
# ---------------------------------------------------------------------------

def expand_couples_in_df(
    df: pd.DataFrame,
    name_col: str | None,
    surname_col: str | None,
    id_col: str | None,
    title_col: str | None = None,
    split_mode: str = "single_field",
    auto_generate_ids: bool = False,
    id_prefix: str = "",
) -> pd.DataFrame:
    expanded_rows = []
    consumed_cols = {c for c in (name_col, surname_col, id_col, title_col) if c}

    for row_pos, (_, row) in enumerate(df.iterrows(), start=1):
        raw_name    = row[name_col]    if (name_col    and name_col    in df.columns) else pd.NA
        raw_surname = row[surname_col] if (surname_col and surname_col in df.columns) else pd.NA
        raw_id      = row[id_col]      if (id_col      and id_col      in df.columns) else pd.NA
        raw_title   = row[title_col]   if (title_col   and title_col   in df.columns) else pd.NA

        str_id = str(raw_id).replace('.0', '').strip() if not pd.isna(raw_id) else ''
        # No usable source ID -> optionally mint a unique one for this source row.
        # Each source row gets a single base; the _c / _c1 / _c2 suffixes are
        # appended downstream exactly as they are for real IDs.
        if not str_id and auto_generate_ids:
            str_id = f"{id_prefix}{row_pos}"
        is_co = _is_company(str(raw_name)) if not pd.isna(raw_name) else False
        other = {k: v for k, v in row.items() if k not in consumed_cols}
        # Every split produced from THIS source row shares this group id, so the
        # phone/email distributor can hand value #1 to split #1, #2 to #2, etc.
        other['_src_group'] = row_pos

        # Handle Companies
        if is_co:
            new_row = dict(other)
            new_row['contact_title']              = pd.NA
            new_row['contact_first_name']         = pd.NA
            new_row['contact_surname']            = pd.NA
            new_row['company_name']               = _clean_name_text(str(raw_name))
            new_row['contact_identifier']         = f"{str_id}_c" if str_id else pd.NA
            new_row['contact_partner_identifier'] = pd.NA
            new_row['contact_partnership_id']     = pd.NA
            new_row['contact_partnership_type']   = pd.NA
            expanded_rows.append(new_row)
            continue

        name_str = str(raw_name).strip() if not pd.isna(raw_name) else ''

        # ================================================================
        # SINGLE-FIELD MODE  -> legacy VB splitter
        #   first = everything before the last space, surname = last token,
        #   ' and '/&/;/+/comma separate a couple, surname shared across it.
        # ================================================================
        if split_mode == "single_field":
            is_person, people = split_single_field_name(raw_name, raw_surname)

            # Company / trust / business -> no split, route to company_name.
            if not is_person:
                new_row = dict(other)
                new_row['contact_title']              = pd.NA
                new_row['contact_first_name']         = pd.NA
                new_row['contact_surname']            = pd.NA
                new_row['company_name']               = _clean_name_text(str(raw_name))
                new_row['contact_identifier']         = f"{str_id}_c" if str_id else pd.NA
                new_row['contact_partner_identifier'] = pd.NA
                new_row['contact_partnership_id']     = pd.NA
                new_row['contact_partnership_type']   = pd.NA
                expanded_rows.append(new_row)
                continue

            # Empty / unusable name -> keep the row but leave names blank.
            if not people:
                new_row = dict(other)
                new_row['contact_title']              = pd.NA
                new_row['contact_first_name']         = pd.NA
                new_row['contact_surname']            = pd.NA
                new_row['contact_identifier']         = f"{str_id}_c" if str_id else pd.NA
                new_row['contact_partner_identifier'] = pd.NA
                new_row['contact_partnership_id']     = pd.NA
                new_row['contact_partnership_type']   = pd.NA
                expanded_rows.append(new_row)
                continue

            if len(people) >= 2:
                # ---- Couple / partnership ----
                col_titles   = _split_couple_titles(raw_title, len(people))
                final_titles = [_pick_title(col_titles[i], people[i].get("title"))
                                for i in range(len(people))]

                for idx, person in enumerate(people, start=1):
                    self_i = idx - 1
                    partner_i = 1 if idx == 1 else 0  # c1<->c2, c3+ -> c1
                    new_row = dict(other)
                    new_row['contact_title']      = final_titles[self_i]
                    new_row['contact_first_name'] = person["first"]
                    new_row['contact_surname']    = person["surname"]
                    new_row['contact_identifier'] = f"{str_id}_c{idx}" if str_id else pd.NA

                    if idx == 1:
                        partner_idx = 2
                        partnership_id = f"{str_id}_c1_{str_id}_c2_r"
                    elif idx == 2:
                        partner_idx = 1
                        partnership_id = f"{str_id}_c1_{str_id}_c2_r"
                    else:
                        partner_idx = 1
                        partnership_id = f"{str_id}_c1_{str_id}_c{idx}_r"

                    new_row['contact_partner_identifier'] = f"{str_id}_c{partner_idx}" if str_id else pd.NA
                    new_row['contact_partnership_id']     = partnership_id if str_id else pd.NA
                    new_row['contact_partnership_type']   = _partnership_type_for(
                        final_titles[self_i], final_titles[partner_i])
                    expanded_rows.append(new_row)
            else:
                # ---- Single contact ----
                person        = people[0]
                col_title     = _split_couple_titles(raw_title, 1)[0]

                new_row = dict(other)
                new_row['contact_title']              = _pick_title(col_title, person.get("title"))
                new_row['contact_first_name']         = person["first"]
                new_row['contact_surname']            = person["surname"]
                new_row['contact_identifier']         = f"{str_id}_c" if str_id else pd.NA
                new_row['contact_partner_identifier'] = pd.NA
                new_row['contact_partnership_id']     = pd.NA
                new_row['contact_partnership_type']   = pd.NA
                expanded_rows.append(new_row)
            continue

        # ================================================================
        # DUAL-FIELD MODE  -> first name and surname already in separate
        # columns; a couple may still live inside the first-name field.
        # ================================================================
        couple = _is_couple(name_str)

        clean_surname = pd.NA
        if not pd.isna(raw_surname):
            clean_surname = advanced_name_parser(raw_surname, 'contact_surname', 'dual_field')

        if couple:
            # Strip any embedded titles ('Mr John & Mrs Jane') off the names.
            first_names, name_titles = _split_couple_first_and_titles(name_str)
            if len(first_names) < 2:
                couple = False

        # Handle Couples (Partnership Generation)
        if couple:
            col_titles   = _split_couple_titles(raw_title, len(first_names))
            final_titles = [_pick_title(col_titles[i], name_titles[i])
                            for i in range(len(first_names))]

            # Iterate and assign Partner IDs
            for idx, fname in enumerate(first_names, start=1):
                self_i = idx - 1
                partner_i = 1 if idx == 1 else 0  # c1<->c2, c3+ -> c1
                new_row = dict(other)
                new_row['contact_title']      = final_titles[self_i]
                new_row['contact_first_name'] = fname
                new_row['contact_surname']    = clean_surname
                new_row['contact_identifier'] = f"{str_id}_c{idx}" if str_id else pd.NA
                
                # Logic: _c1's partner is _c2 | _c2's partner is _c1 | _c3+ partner is _c1
                if idx == 1:
                    partner_idx = 2
                    partnership_id = f"{str_id}_c1_{str_id}_c2_r"
                elif idx == 2:
                    partner_idx = 1
                    partnership_id = f"{str_id}_c1_{str_id}_c2_r"
                else:
                    partner_idx = 1
                    partnership_id = f"{str_id}_c1_{str_id}_c{idx}_r"
                
                new_row['contact_partner_identifier'] = f"{str_id}_c{partner_idx}" if str_id else pd.NA
                new_row['contact_partnership_id']     = partnership_id if str_id else pd.NA
                new_row['contact_partnership_type']   = _partnership_type_for(
                    final_titles[self_i], final_titles[partner_i])
                
                expanded_rows.append(new_row)
        else:
            # Handle Singles
            col_title  = _split_couple_titles(raw_title, 1)[0]

            name_title = None
            if name_str:
                fn_tokens = str(raw_name).split()
                name_title, fn_tokens = _extract_title_from_tokens(fn_tokens)
                clean_first = _clean_name_text(" ".join(fn_tokens)) if fn_tokens else pd.NA
            else:
                clean_first = pd.NA

            new_row = dict(other)
            new_row['contact_title']              = _pick_title(col_title, name_title)
            new_row['contact_first_name']         = clean_first
            new_row['contact_surname']            = clean_surname
            new_row['contact_identifier']         = f"{str_id}_c" if str_id else pd.NA
            new_row['contact_partner_identifier'] = pd.NA
            new_row['contact_partnership_id']     = pd.NA
            new_row['contact_partnership_type']   = pd.NA
            expanded_rows.append(new_row)

    return pd.DataFrame(expanded_rows)

# ---------------------------------------------------------------------------
# ROBUST PHONE & EMAIL FORMATTING
# ---------------------------------------------------------------------------

def clean_and_classify_phone(raw_num):
    if pd.isna(raw_num): return pd.NA, 'Invalid'
    s = str(raw_num)
    digits = re.sub(r'[^\d+]', '', s)

    # --- normalisation: collapse international / stray-prefix forms to local 0... ---
    if digits.startswith('+61'):
        digits = '0' + digits[3:]
    elif digits.startswith('0061'):
        digits = '0' + digits[4:]
    elif digits.startswith('0614') and len(digits) == 12:
        # Stray-0 international mobile (VB ^0614\d{8}$): 0 + 61 + 4xxxxxxxx -> 04xxxxxxxx
        digits = '0' + digits[3:]
    elif digits.startswith('61') and len(digits) >= 10:
        digits = '0' + digits[2:]

    digits = digits.replace('+', '')
    if not digits: return s, 'Invalid'

    # --- mobiles ---
    if digits.startswith('04') and len(digits) == 10:
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}", 'Mobile'
    elif digits.startswith('4') and len(digits) == 9:
        return f"0{digits[:3]} {digits[3:6]} {digits[6:]}", 'Mobile'
    elif digits.startswith('64') and len(digits) == 11:
        # NZ number (VB Validate: length 11 starting 64) -> treated as Mobile
        return f"{digits[:2]} {digits[2:]}", 'Mobile'

    # --- landlines (area-code form) ---
    elif digits.startswith('0') and len(digits) == 10 and digits[1] in '2378':
        return f"({digits[:2]}) {digits[2:6]} {digits[6:]}", 'Landline'
    elif len(digits) == 9 and digits[0] in '2378':
        return f"(0{digits[:1]}) {digits[1:5]} {digits[5:]}", 'Landline'

    # --- landlines (local, no area code) ---
    elif len(digits) == 8 and digits[0] in '23456789':
        # 8-digit local landline (VB ^[2-9]\d{7}$)
        return f"{digits[:1]} {digits[1:]}", 'Landline'
    elif len(digits) == 9 and digits[0] == '0' and digits[1] in '23456789':
        # 8-digit local landline with stray leading 0 (VB ^0[2-9]\d{7}$)
        d = digits[1:]
        return f"{d[:1]} {d[1:]}", 'Landline'

    # --- service numbers ---
    elif (digits.startswith('1300') or digits.startswith('1800')) and len(digits) == 10:
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}", 'Landline'
    elif digits.startswith('13') and len(digits) == 6:
        return f"{digits[:2]} {digits[2:4]} {digits[4:]}", 'Landline'

    return s, 'Invalid'

def process_email(val):
    if pd.isna(val): return pd.NA
    e = str(val).lower().replace(" ", "")
    e = e.rstrip('.,;')
    if not e or e in ['nan', 'none', 'n/a', 'na', 'unknown']: return pd.NA
    return e


# ---------------------------------------------------------------------------
# CONTACT-INFO DISTRIBUTION  (phones + emails, split-aware)
# ---------------------------------------------------------------------------

# Single-value target fields and the email target field.
_PHONE_TARGET_FIELDS = ('contact_mobile', 'contact_phone_work',
                        'contact_phone_home', 'contact_fax')
_EMAIL_TARGET_FIELD  = 'contact_email_address'

# Values inside one cell may be separated by comma / semicolon / slash / 'and'.
_CONTACT_SEP = re.compile(r'\s*(?:,|;|/|\band\b)\s*', flags=re.IGNORECASE)
_EMAIL_RE    = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _split_contact_values(value):
    """Break a single cell into individual tokens on , ; / and."""
    if pd.isna(value):
        return []
    s = str(value).strip()
    if not s or s.lower() in ('nan', 'none', 'null', 'n/a', 'na', 'unknown'):
        return []
    return [t.strip() for t in _CONTACT_SEP.split(s) if t and t.strip()]


def _looks_like_email(token) -> bool:
    return bool(_EMAIL_RE.match(str(token).strip()))


def _dedup_keep_order(seq):
    seen, out = set(), []
    for x in seq:
        k = str(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _distribute(bucket, n):
    """Spread an ordered list of valid values across n split contacts.

    1 value  -> shared by everyone.
    >1 value -> handed out 1:1 (value #1 to contact #1 ...); anything left over
                is returned as 'leftover' for the notes column.
    """
    if not bucket:
        return [pd.NA] * n, []
    if len(bucket) == 1:
        return [bucket[0]] * n, []
    assigned = [pd.NA] * n
    for i in range(n):
        if i < len(bucket):
            assigned[i] = bucket[i]
    leftover = bucket[n:] if len(bucket) > n else []
    return assigned, leftover


def process_contact_group(gdf: pd.DataFrame) -> pd.DataFrame:
    """Route, validate and distribute phone numbers and emails for ONE source
    contact's set of split rows (Australian number rules).

    Rules:
      * Each of contact_mobile / contact_phone_work / contact_phone_home /
        contact_fax holds at most one value.
      * Mobile-format numbers always land in contact_mobile.
      * Landlines sitting in contact_mobile move to contact_phone_work (company)
        or contact_phone_home (person).
      * Emails found in any number field move to contact_email_address.
      * Cells may contain several values separated by , ; / or 'and'.
      * Across split contacts, a single value is shared; multiple values are
        handed out one per split; surplus + invalid go to contact_notes.
    """
    cols = set(gdf.columns)
    phone_cols = [c for c in _PHONE_TARGET_FIELDS if c in cols]
    has_email  = _EMAIL_TARGET_FIELD in cols
    if not phone_cols and not has_email:
        return gdf

    gdf = gdf.copy().reset_index(drop=True)
    n = len(gdf)
    base = gdf.iloc[0]

    is_company = False
    if 'company_name' in cols:
        cv = base.get('company_name')
        is_company = (not pd.isna(cv)) and bool(str(cv).strip())

    # ---- classify every token coming from the number fields ----
    # records: (origin_field, value, kind)  kind in Mobile/Landline/Email/Invalid
    records = []
    for fld in phone_cols:
        for tok in _split_contact_values(base.get(fld)):
            if _looks_like_email(tok):
                records.append((fld, tok.lower(), 'Email'))
                continue
            cleaned, ptype = clean_and_classify_phone(tok)
            records.append((fld, cleaned if ptype != 'Invalid' else tok, ptype))

    mobiles, homes, works, faxes, emails = [], [], [], [], []
    note_items = []

    # Native values are added first so the field's own number wins a tie.
    for fld, val, kind in records:                         # native mobiles
        if fld == 'contact_mobile' and kind == 'Mobile':
            mobiles.append(val)
    for fld, val, kind in records:                         # mobiles seen elsewhere
        if fld != 'contact_mobile' and kind == 'Mobile':
            mobiles.append(val)
    for fld, val, kind in records:                         # native landlines
        if fld == 'contact_phone_home' and kind == 'Landline':
            homes.append(val)
    for fld, val, kind in records:
        if fld == 'contact_phone_work' and kind == 'Landline':
            works.append(val)
    for fld, val, kind in records:
        if fld == 'contact_fax' and kind == 'Landline':
            faxes.append(val)
    for fld, val, kind in records:                         # landline in mobile field
        if fld == 'contact_mobile' and kind == 'Landline':
            (works if is_company else homes).append(val)
    for fld, val, kind in records:                         # emails in number fields
        if kind == 'Email':
            emails.append(val)
    for fld, val, kind in records:                         # invalids
        if kind == 'Invalid':
            note_items.append(f"Invalid Number: {val}")

    # ---- emails from the dedicated email field (these take priority) ----
    if has_email:
        ef_emails = []
        for tok in _split_contact_values(base.get(_EMAIL_TARGET_FIELD)):
            if _looks_like_email(tok):
                ef_emails.append(tok.lower())
            else:
                note_items.append(f"Invalid Email: {tok}")
        emails = ef_emails + emails

    mobiles = _dedup_keep_order(mobiles)
    homes   = _dedup_keep_order(homes)
    works   = _dedup_keep_order(works)
    faxes   = _dedup_keep_order(faxes)
    emails  = _dedup_keep_order(emails)

    # ---- distribute across the split rows ----
    bucket_for = {
        'contact_mobile':     mobiles,
        'contact_phone_home': homes,
        'contact_phone_work': works,
        'contact_fax':        faxes,
    }
    extra_items = []
    for field, bucket in bucket_for.items():
        assigned, leftover = _distribute(bucket, n)
        # Assign whenever there's a value to place (creating the column if it
        # wasn't in the mapping - e.g. a landline pulled out of contact_mobile
        # still needs a home/work field), or to clear/normalise a mapped field.
        # _distribute already shares a single value across every split (c1, c2,
        # ...) and hands multiples out one per split, so c2-onwards are covered.
        if bucket or field in cols:
            gdf[field] = assigned
        # Only true surplus (more numbers than splits) overflows to notes.
        extra_items += [f"Extra phone: {v}" for v in leftover]

    if emails or has_email:
        assigned, leftover = _distribute(emails, n)
        gdf[_EMAIL_TARGET_FIELD] = assigned          # created if not mapped
        extra_items += [f"Extra email: {v}" for v in leftover]

    # ---- overflow + invalid notes go on the FIRST split only ----
    all_notes = extra_items + note_items
    if all_notes:
        if 'contact_notes' not in gdf.columns:
            gdf['contact_notes'] = pd.NA
        existing = gdf.at[0, 'contact_notes']
        joined = " | ".join(all_notes)
        if pd.isna(existing) or not str(existing).strip():
            gdf.at[0, 'contact_notes'] = joined
        else:
            gdf.at[0, 'contact_notes'] = f"{existing}\n{joined}"

    return gdf

# ---------------------------------------------------------------------------
# GeneralProcessor
# ---------------------------------------------------------------------------

class GeneralProcessor:
    def __init__(self, engine):
        self.engine   = engine
        self.conn     = engine.conn
        self.job_id   = engine.job_id
        self.workspace = engine.workspace

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_base_file(self, rules):
        source_files = []
        for rule in rules:
            for src in rule.get("sources", []):
                fn = src.get("file")
                if fn and fn != "Custom_Dummy_Fields":
                    source_files.append(fn)
        if not source_files:
            return None
        return Counter(source_files).most_common(1)[0][0]

    def _load_table(self, table_name) -> pd.DataFrame | None:
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';"
        )
        if not cursor.fetchone():
            self.engine.log(f"[{self.job_id}] CRITICAL: '{table_name}' table missing in DB.")
            return None
        return pd.read_sql_query(f'SELECT * FROM "{table_name}"', self.conn)

    # ------------------------------------------------------------------
    # Interactive prompts
    #
    # Answers are resolved in priority order so the processor works whether
    # it runs in a console, behind a UI, or as an unattended job:
    #   1. A preset supplied on the engine: engine.general_config[key]
    #   2. An engine-provided callback:     engine.ask(question, options)
    #   3. A live console prompt (only when attached to a TTY)
    #   4. The supplied default
    # ------------------------------------------------------------------

    def _resolve_preset(self, key: str):
        cfg = getattr(self.engine, "general_config", None) or {}
        return cfg.get(key)

    def _interactive(self) -> bool:
        # Console prompts are OFF unless explicitly turned on with
        # engine.interactive = True.
        #
        # IMPORTANT: we deliberately do NOT use sys.stdin.isatty() here. Under
        # uvicorn / FastAPI BackgroundTasks, stdin is still attached to the
        # terminal that launched the server, so isatty() returns True and a
        # console prompt would call input() and hang the whole job until the
        # server is killed. The web pipeline never sets this flag, so it stays
        # fully non-blocking; a CLI/dev run can opt in with engine.interactive = True.
        return bool(getattr(self.engine, "interactive", False))

    def _ask(self, key: str, question: str, options: list, default: str) -> str:
        """options: list of (value, label). Returns one of the option values."""
        valid = {value for value, _ in options}

        # 1. Preset answer
        preset = self._resolve_preset(key)
        if preset in valid:
            self.engine.log(f"[{self.job_id}] '{key}' answered from config: {preset}")
            return preset

        # 2. Engine callback (custom UI / web layer)
        cb = getattr(self.engine, "ask", None)
        if callable(cb):
            try:
                ans = cb(question, options)
                if ans in valid:
                    self.engine.log(f"[{self.job_id}] '{key}' answered via engine.ask: {ans}")
                    return ans
            except Exception as e:
                self.engine.log(f"[{self.job_id}] engine.ask failed for '{key}': {e}")

        # 3. Live console
        if self._interactive():
            print(f"\n{question}", flush=True)
            for i, (_, label) in enumerate(options, start=1):
                print(f"  {i}. {label}", flush=True)
            try:
                raw = input(f"Select [1-{len(options)}] (default {default}): ").strip()
            except (EOFError, KeyboardInterrupt):
                raw = ""
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    return options[idx][0]
            if raw in valid:
                return raw

        # 4. Default
        self.engine.log(f"[{self.job_id}] '{key}' using default: {default}")
        return default

    def _ask_split_mode(self) -> str:
        return self._ask(
            key="split_mode",
            question="How are the contact names stored in the source file?",
            options=[
                ("single_field",
                 "All names are in ONE field (first + surname together) - split them"),
                ("dual_field",
                 "First name and surname are already in SEPARATE fields"),
            ],
            default="single_field",
        )

    def _ask_create_identifier(self) -> bool:
        ans = self._ask(
            key="create_identifier",
            question=("No contact identifier was mapped. Should the system "
                      "create a unique Contact_identifier for each row?"),
            options=[
                ("yes",
                 "Yes - generate a unique ID per row (suffixed _c, or _c1/_c2 for couples)"),
                ("no",
                 "No - leave the contact identifier blank"),
            ],
            default="no",
        )
        return ans == "yes"

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process_group(self, group_name: str, rules: list):
        self.engine.log(
            f"[{self.job_id}] Executing General Data Cleaning for '{group_name}'..."
        )

        # ── 1. Identify base file ──────────────────────────────────────
        base_file = self._find_base_file(rules)
        if not base_file:
            self.engine.log(
                f"[{self.job_id}] Skipping {group_name}: No valid source files found."
            )
            return

        # ── 2. Load raw data ──────────────────────────────────────────
        try:
            df = self._load_table(base_file)
            if df is None:
                return
        except Exception as e:
            self.engine.log(f"[{self.job_id}] Error loading {base_file}: {e}")
            return

        # ── 3. Identify name-related rules so we can handle them together ──
        name_rules = {}   
        other_rules = []

        _NAME_TARGETS = {
            'contact_title', 'contact_first_name', 'contact_surname',
            'contact_identifier', 'company_name',
            'contact_partner_identifier', 'contact_partnership_id', 'contact_partnership_type'
        }

        for rule in rules:
            tf = rule.get("targetField", "")
            if tf in _NAME_TARGETS:
                name_rules[tf] = rule
            else:
                other_rules.append(rule)

        # ── 4. Build zenu_output for non-name fields ──────────────────
        # When name rules are present, step 5 rebuilds every non-name rule on
        # the *expanded* rows and discards whatever we'd compute here, so doing
        # it now just runs every concat/lookup twice. Only pre-build when there
        # is no name expansion to follow.
        zenu_output = pd.DataFrame(index=df.index)

        if not name_rules:
            for rule in other_rules:
                zenu_output = self._apply_rule(rule, df, zenu_output)

        # ── 5. Couple-aware name expansion ────────────────────────────
        if name_rules:
            def _src_field(tf):
                r = name_rules.get(tf)
                if not r:
                    return None
                srcs = r.get("sources", [])
                return srcs[0].get("field") if srcs else None

            first_rule  = name_rules.get('contact_first_name', {})

            name_col    = _src_field('contact_first_name')
            surname_col = _src_field('contact_surname')
            id_col      = _src_field('contact_identifier')
            title_col   = _src_field('contact_title')

            # ── Decide the name-splitting strategy ────────────────────
            # Honour an explicit splitRule from the mapping; otherwise ask.
            split_mode = first_rule.get("splitRule")
            if split_mode not in ("single_field", "dual_field"):
                split_mode = self._ask_split_mode()

            # ── Decide how the contact identifier is produced ─────────
            # Resolution order (so the web UI can drive this purely through the
            # mapping JSON, with no console interaction):
            #   1. A flag on the contact_identifier rule
            #      ("autoGenerate" / "generateIfMissing" / "createIdentifier")
            #   2. engine.general_config / engine.ask / console (CLI only)
            #   3. Safe default: do NOT auto-generate (blank identifier)
            id_rule           = name_rules.get('contact_identifier', {}) or {}
            has_id            = bool(id_col) and id_col in df.columns
            auto_generate_ids = False
            id_prefix         = (id_rule.get("idPrefix")
                                 or self._resolve_preset("id_prefix") or "")

            def _coerce_yes_no(v):
                if isinstance(v, bool):
                    return v
                if v is None:
                    return None
                s = str(v).strip().lower()
                if s in ("yes", "true", "1", "y"):
                    return True
                if s in ("no", "false", "0", "n"):
                    return False
                return None

            if has_id:
                # File already has an ID -> reuse it and append _c (existing logic).
                self.engine.log(
                    f"[{self.job_id}] Using existing identifier column "
                    f"'{id_col}' (suffix '_c' appended per contact)."
                )
            else:
                # No identifier mapped. Prefer an explicit flag from the mapping.
                rule_flag = None
                for key in ("autoGenerate", "generateIfMissing", "createIdentifier"):
                    rule_flag = _coerce_yes_no(id_rule.get(key))
                    if rule_flag is not None:
                        break

                if rule_flag is not None:
                    auto_generate_ids = rule_flag
                else:
                    # No mapping flag -> config/callback/console (CLI) or default no.
                    auto_generate_ids = self._ask_create_identifier()

                if auto_generate_ids:
                    self.engine.log(
                        f"[{self.job_id}] No identifier column found; "
                        f"auto-generating Contact_identifier per row "
                        f"(prefix='{id_prefix or '<none>'}')."
                    )
                else:
                    self.engine.log(
                        f"[{self.job_id}] No identifier column found; "
                        f"leaving Contact_identifier blank."
                    )

            self.engine.log(
                f"[{self.job_id}] Running couple-expansion "
                f"(name='{name_col}', surname='{surname_col}', id='{id_col}', "
                f"title='{title_col}', mode='{split_mode}', "
                f"auto_id={auto_generate_ids})..."
            )

            expanded = expand_couples_in_df(
                df, name_col, surname_col, id_col, title_col, split_mode,
                auto_generate_ids=auto_generate_ids, id_prefix=id_prefix,
            )

            name_out_cols = [
                c for c in (
                    'contact_title', 'contact_first_name', 'contact_surname',
                    'contact_identifier', 'company_name',
                    'contact_partner_identifier', 'contact_partnership_id', 'contact_partnership_type'
                )
                if c in expanded.columns
            ]

            zenu_expanded = pd.DataFrame(index=expanded.index)
            for rule in other_rules:
                zenu_expanded = self._apply_rule(rule, expanded, zenu_expanded)

            for col in name_out_cols:
                zenu_expanded[col] = expanded[col].values

            # Carry the source-group id so phone/email distribution can map
            # value #1 -> split #1, value #2 -> split #2, etc.
            if '_src_group' in expanded.columns:
                zenu_expanded['_src_group'] = expanded['_src_group'].values

            zenu_output = zenu_expanded
            
        # ── 6. SMART POST-PROCESSING (PHONES & EMAILS, split-aware) ─────
        # Each source contact (its set of split rows) is processed together so
        # numbers/emails can be validated, routed to the right single-value
        # field, and handed out across the splits, with overflow -> notes.
        if '_src_group' not in zenu_output.columns:
            zenu_output['_src_group'] = range(len(zenu_output))

        contact_info_cols = list(_PHONE_TARGET_FIELDS) + [_EMAIL_TARGET_FIELD]
        if any(c in zenu_output.columns for c in contact_info_cols):
            processed_groups = []
            for _, gdf in zenu_output.groupby('_src_group', sort=False):
                processed_groups.append(process_contact_group(gdf))
            zenu_output = pd.concat(processed_groups, ignore_index=True)

        zenu_output = zenu_output.drop(columns=['_src_group'], errors='ignore')

        # ── 7. Global sanitisation ────────────────────────────────────
        def _clean(val):
            if pd.isna(val):
                return val
            t = str(val).strip()
            if t.lower() in ('nan', 'none', 'null', ''):
                return pd.NA
            return re.sub(r' +', ' ', t)

        for col in zenu_output.columns:
            zenu_output[col] = zenu_output[col].apply(_clean)

        zenu_output = zenu_output.dropna(how='all').reset_index(drop=True)

        self.engine.log(
            f"[{self.job_id}] Expansion complete: {len(df)} source rows → "
            f"{len(zenu_output)} output rows."
        )

        # ── 8. Export ─────────────────────────────────────────────────
        self._export(group_name, zenu_output)

    # ------------------------------------------------------------------
    # Rule application 
    # ------------------------------------------------------------------

    def _apply_rule(
        self,
        rule: dict,
        df: pd.DataFrame,
        zenu_output: pd.DataFrame,
    ) -> pd.DataFrame:
        target_field     = rule.get("targetField")
        action           = rule.get("action")
        sources          = rule.get("sources", [])
        split_rule       = rule.get("splitRule", "single_field")
        primary_src_field = sources[0].get("field") if sources else None

        # DIRECT
        if action == "direct":
            if primary_src_field and primary_src_field in df.columns:
                if target_field in ('contact_first_name', 'contact_surname', 'company_name'):
                    zenu_output[target_field] = df[primary_src_field].apply(
                        lambda x: advanced_name_parser(x, target_field, split_rule)
                    )
                else:
                    zenu_output[target_field] = df[primary_src_field].values

        # STATIC
        elif action == "static":
            zenu_output[target_field] = rule.get("valueExpression", "")

        # CONCAT
        elif action == "concat":
            expression = str(rule.get("valueExpression", ""))
            if expression:
                def eval_concat(row):
                    res = expression
                    for src in sources:
                        var_id = f"[{src.get('varId', 'S1')}]"
                        val    = str(row.get(src.get('field'), ''))
                        if val.lower() in ('nan', 'none'):
                            val = ''
                        res = res.replace(var_id, val)
                    return res.strip()
                zenu_output[target_field] = df.apply(eval_concat, axis=1)
            elif primary_src_field and primary_src_field in df.columns:
                zenu_output[target_field] = df[primary_src_field].values

        # LOOKUP
        elif action == "lookup":
            for config in rule.get('lookupConfig', []):
                target_file    = config.get('targetFile')
                match_key      = config.get('matchKey')
                extract_fields = config.get('extractFields', [])

                if target_file and match_key and extract_fields \
                        and primary_src_field and primary_src_field in df.columns:
                    try:
                        extract_col = extract_fields[0]
                        lookup_df = pd.read_sql_query(
                            f'SELECT "{match_key}", "{extract_col}" FROM "{target_file}"',
                            self.conn,
                        )
                        lookup_df[match_key] = (
                            lookup_df[match_key].astype(str).str.replace(r'\.0$', '', regex=True)
                        )
                        source_keys = (
                            df[primary_src_field].astype(str).str.replace(r'\.0$', '', regex=True)
                        )
                        mapping_dict = (
                            lookup_df.drop_duplicates(subset=[match_key])
                            .set_index(match_key)[extract_col]
                            .to_dict()
                        )
                        zenu_output[target_field] = source_keys.map(mapping_dict).values
                    except Exception as e:
                        self.engine.log(
                            f"[{self.job_id}] Lookup warning for {target_field}: {e}"
                        )

        return zenu_output

    # ------------------------------------------------------------------
    # Export helper
    # ------------------------------------------------------------------

    def _export(self, group_name: str, df: pd.DataFrame):
        safe_name   = group_name.replace(" ", "_").replace("/", "_")
        chunk_limit = self.engine.chunk_size
        total_rows  = len(df)

        if chunk_limit > 0 and total_rows > chunk_limit:
            self.engine.log(
                f"[{self.job_id}] Splitting into chunks of {chunk_limit}..."
            )
            num_chunks = (total_rows // chunk_limit) + (
                1 if total_rows % chunk_limit else 0
            )
            for i in range(num_chunks):
                chunk = df.iloc[i * chunk_limit : (i + 1) * chunk_limit]
                if not chunk.empty:
                    path = os.path.join(self.workspace, f"Cleaned_{safe_name}_pt{i+1}.csv")
                    chunk.to_csv(path, index=False)
                    self.engine.log(
                        f"[{self.job_id}] SUCCESS: Created {path} ({len(chunk)} rows)"
                    )
        else:
            path = os.path.join(self.workspace, f"Cleaned_{safe_name}.csv")
            df.to_csv(path, index=False)
            self.engine.log(
                f"[{self.job_id}] SUCCESS: Exported -> {path} ({total_rows} rows)"
            )