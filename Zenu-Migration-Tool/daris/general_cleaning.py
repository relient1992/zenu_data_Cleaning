import re
from collections import Counter
from datetime import datetime

import pandas as pd

from .contact_cleaning import ContactDetailsCleaner, is_valid_email, split_names

# =====================================================================
# GENERAL CONTACT CLEANING  - mirrors "Contact Details Cleaning - General.xlsb"
# ---------------------------------------------------------------------
# The workbook's pipeline, step for step:
#   Transfer   raw ID = <contact id>_<ddmmyyyy tag>          (Import_agentbox.bas)
#   Name SPlitting   SplitContactNames macro  + the I:BK formulas
#                    (IDs _c / _c1.._c5, partner ID, partnership ID, type)
#   Contact Details  CleanContactDetails macro + the P:AB formulas
#                    (Extra Notes, Invalid Numbers, Email Okay?)
#   Address Cleaning TRIM(CLEAN()) of each raw field, postcode 0 -> blank
#                    (+ DARVIS: suburb split out of line 1, see address_cleaning.py)
#   Summary          FillSummary macro + the M:BB formulas: one row per person,
#                    all main contacts first, every split contact at the bottom.
# Partners take their own Mobile 2 / Email2 when present, otherwise the main
# contact's (the Summary formulas' fallback); everything else is shared.
# DARVIS change (split_multi_values, on by default): when a split record's mobile
# or email cell held several values ('A | B'), person 1 gets A, person 2 gets B ...
# instead of everyone getting 'A | B'. Values left over (more values than people)
# stay with person 1. The Contact Details tab is unchanged - it is the workbook's.
# =====================================================================

# role -> (label shown in the review card, Zenu field used to propose a column)
ROLES = {
    "id": ("Contact ID", "contact_identifier"),
    "spouse_id": ("Spouse / partner's contact ID", None),
    "company": ("Company name", "contact_company_name"),
    "title": ("Title", "contact_title"),
    "first": ("First name", "contact_first_name"),
    "last": ("Last name", "contact_surname"),
    "title2": ("Second person - title", None),
    "first2": ("Second person - first name", None),
    "last2": ("Second person - last name", None),
    "email": ("Email", "contact_email_address"),
    "mobile": ("Mobile", "contact_mobile"),
    "work": ("Work phone", "contact_phone_work"),
    "home": ("Home phone", "contact_phone_home"),
    "fax": ("Fax", "contact_fax"),
    "mobile2": ("Second person - mobile", None),
    "email2": ("Second person - email", None),
    "street": ("Address line 1 / street", "contact_mailing_address_line1"),
    "street2": ("Address line 2", "contact_mailing_address_line2"),
    "suburb": ("Suburb", "contact_mailing_address_suburb"),
    "postcode": ("Postcode", "contact_mailing_address_postcode"),
    "state": ("State", "contact_mailing_address_state"),
    "country": ("Country", "contact_mailing_address_country"),
    "salutation": ("Name on letters / salutation", "contact_name_on_letters"),
    "legal": ("Legal name", "contact_legal_name"),
    "position": ("Company position / job title", "contact_company_position"),
    "type": ("Contact type", "contact_type"),
    "tags": ("Contact tags", "contact_tags"),
    "source": ("Enquiry source", "contact_enquiry_source"),
    "notes": ("Notes / comments", "contact_notes"),
    "team1": ("Team member 1", "contact_team_member_1"),
    "created": ("Date created", "contact_date_created"),
    "modified": ("Date modified", "contact_date_modified"),
    "do_not_sms": ("Text / SMS permission", None),
    "do_not_email": ("Email permission", None),
    "do_not_mail": ("Letter / mail permission", None),
    "do_not_call": ("Phone permission", None),
    "subscribed": ("Newsletter / marketing (unsubscribe)", None),
    "contactable": ("Contactable (fills blank permissions)", None),
    # the rest of the Zenu contact fields: carried across as they are (trimmed; dates as dd/mm/yyyy)
    "zenu_id": ("Zenu contact ID (existing Zenu record)", "zenu_contact_id"),
    "subscriptions": ("Subscriptions", "contact_subscriptions"),
    "town_city": ("Town / city", "contact_mailing_address_town_city"),
    "children": ("Children's names", "contact_childrens_names"),
    "rating": ("Rating", "contact_rating"),
    "last_contact": ("Last contact date", "contact_last_contact_date"),
    "team2": ("Team member 2", "contact_team_member_2"),
    "team3": ("Team member 3", "contact_team_member_3"),
    "team4": ("Team member 4", "contact_team_member_4"),
    "team5": ("Team member 5", "contact_team_member_5"),
    "team6": ("Team member 6", "contact_team_member_6"),
    "team7": ("Team member 7", "contact_team_member_7"),
    "team8": ("Team member 8", "contact_team_member_8"),
    "team9": ("Team member 9", "contact_team_member_9"),
    "team10": ("Team member 10", "contact_team_member_10"),
    "team11": ("Team member 11", "contact_team_member_11"),
    "team12": ("Team member 12", "contact_team_member_12"),
    "team13": ("Team member 13", "contact_team_member_13"),
    "team14": ("Team member 14", "contact_team_member_14"),
    "team15": ("Team member 15", "contact_team_member_15"),
    "sale_type": ("Sale type", "contact_sale_type"),
    "solicitor_id": ("Zenu solicitor ID", "zenu_solicitor_id"),
    "company_id": ("Zenu company ID", "zenu_company_id"),
}
# Header words that propose the roles without a Zenu field
ROLE_HINTS = {
    "title2": ("title2", "title 2", "partner title", "second title"),
    "first2": ("firstname2", "first name 2", "first2", "partner first name", "second first name"),
    "last2": ("surname2", "lastname2", "last name 2", "surname 2", "partner surname", "partner last name"),
    "mobile2": ("mobile2", "mobile 2", "partner mobile", "second mobile"),
    "email2": ("email2", "email 2", "partner email", "second email"),
    "do_not_sms": ("do not sms", "donotsms", "no sms"),
    "do_not_email": ("do not email", "donotemail", "no email"),
    "do_not_mail": ("do not mail", "donotmail", "no mail"),
    "do_not_call": ("do not call", "donotcall", "no call"),
    "subscribed": ("subscribed newsletters", "subscribed newsletter", "newsletter"),
}

# the Zenu contact fields in import order, between the workbook's helper columns
SUMMARY_COLUMNS = [
    "Indicator",
    "Contact Count",
    "Raw ORIG CONTACT_IDENTIFIER",
    "CONTACT_IDENTIFIER",
    "ZENU_CONTACT_ID",
    "CONTACT_TITLE",
    "CONTACT_FIRST_NAME",
    "CONTACT_SURNAME",
    "CONTACT_IS_COMPANY",
    "CONTACT_COMPANY_NAME",
    "CONTACT_NAME_ON_LETTERS",
    "CONTACT_LEGAL_NAME",
    "CONTACT_COMPANY_POSITION",
    "CONTACT_TYPE",
    "CONTACT_TAGS",
    "CONTACT_SUBSCRIPTIONS",
    "CONTACT_MAILING_ADDRESS_LINE1",
    "CONTACT_MAILING_ADDRESS_LINE2",
    "CONTACT_MAILING_ADDRESS_SUBURB",
    "CONTACT_MAILING_ADDRESS_TOWN_CITY",
    "CONTACT_MAILING_ADDRESS_STATE",
    "CONTACT_MAILING_ADDRESS_COUNTRY",
    "CONTACT_MAILING_ADDRESS_POSTCODE",
    "CONTACT_EMAIL_ADDRESS",
    "CONTACT_MOBILE",
    "CONTACT_PHONE_WORK",
    "CONTACT_PHONE_HOME",
    "CONTACT_FAX",
    "CONTACT_ALLOW_CONTACT_VIA_TEXT",
    "CONTACT_ALLOW_CONTACT_VIA_EMAIL",
    "CONTACT_ALLOW_CONTACT_VIA_LETTER",
    "CONTACT_ALLOW_CONTACT_VIA_PHONE",
    "CONTACT_UNSUBSCRIBE_FROM_MARKETING",
    "CONTACT_CHILDRENS_NAMES",
    "CONTACT_ENQUIRY_SOURCE",
    "CONTACT_RATING",
    "CONTACT_PARTNER_IDENTIFIER",
    "CONTACT_PARTNERSHIP_ID",
    "CONTACT_PARTNERSHIP_TYPE",
    "CONTACT_LAST_CONTACT_DATE",
    "CONTACT_DATE_CREATED",
    "CONTACT_DATE_MODIFIED",
    "CONTACT_TEAM_MEMBER_1",
    "CONTACT_TEAM_MEMBER_2",
    "CONTACT_TEAM_MEMBER_3",
    "CONTACT_TEAM_MEMBER_4",
    "CONTACT_TEAM_MEMBER_5",
    "CONTACT_TEAM_MEMBER_6",
    "CONTACT_TEAM_MEMBER_7",
    "CONTACT_TEAM_MEMBER_8",
    "CONTACT_TEAM_MEMBER_9",
    "CONTACT_TEAM_MEMBER_10",
    "CONTACT_TEAM_MEMBER_11",
    "CONTACT_TEAM_MEMBER_12",
    "CONTACT_TEAM_MEMBER_13",
    "CONTACT_TEAM_MEMBER_14",
    "CONTACT_TEAM_MEMBER_15",
    "CONTACT_SALE_TYPE",
    "ZENU_SOLICITOR_ID",
    "ZENU_COMPANY_ID",
    "CONTACT_NOTES",
    "Contact Name",
]

_NONPRINTABLE = re.compile(r"[\x00-\x1f]")
_TRUE_WORDS = {"true", "1", "yes", "y"}
_FALSE_WORDS = {"false", "0", "no", "n"}


def excel_trim_clean(v) -> str:
    """=TRIM(CLEAN(x)). Line breaks become a space first - Excel's CLEAN would glue
    '9 Gavia Court<newline>ROCHEDALE SOUTH' into 'CourtROCHEDALE'."""
    s = "" if v is None else str(v)
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = _NONPRINTABLE.sub("", s)
    return re.sub(r" {2,}", " ", s).strip(" ")


def _cell(v) -> str:
    # Only a real missing value is empty - the text 'Nan' is a first name
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v)
    return s[:-2] if re.fullmatch(r"-?\d+\.0", s) else s


def _relationship(a: str, b: str) -> str:
    """Name SPlitting L / S / BA formulas (Excel '=' is case-insensitive)."""
    a, b = a.lower(), b.lower()
    if a == "mr" and b == "mrs":
        return "Husband"
    if a == "mrs" and b == "mr":
        return "Wife"
    return "Partner"


def contact_no(contact_id: str) -> int:
    """FillSummary ContactNo: '..._c' -> 1, '..._c1' -> 1, '..._c3' -> 3."""
    p = contact_id.rfind("_c")
    if p < 0:
        return 1
    tail = contact_id[p + 2:]
    return int(tail) if tail.isdigit() else 1


# ---- dates: the same reader as engine.js (DARVIS) ----
DATE_MODES = {"date": "Date only (dd/mm/yyyy)", "datetime": "Date and time (dd/mm/yyyy hh:mm)",
              "datetime_s": "Date and time with seconds (dd/mm/yyyy hh:mm:ss)"}
DATE_ROLES = ("created", "modified", "last_contact")
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_ODBC = re.compile(r"^\{\s*(?:ts|d|t)\s*'([^']*)'\s*\}$", re.I)
_ISO = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*([AP]M)?(?:Z|[+-]\d{2}:?\d{2})?)?$", re.I)
_DMY = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s?([AP]M))?)?$", re.I)
_D_MON_Y = re.compile(r"^(\d{1,2})[ -]([A-Za-z]{3,9})\.?[ -]+(\d{4})$")
_MON_D_Y = re.compile(r"^([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})$")


def _valid_date(y, m, d):
    try:
        datetime(y, m, d)
        return 1000 <= y <= 9999
    except ValueError:
        return False


def parse_date(v):
    """{y, m, d, H, M, S} or None. Understands ISO, dd/mm/yyyy (+ time, AM/PM), 16 Jul 2024, Jul 16 2024
    and ODBC stamps like {ts '2024-07-16 09:42:03'}."""
    s = str(v or "").strip()
    o = _ODBC.match(s)
    if o:
        s = o.group(1).strip()
    t = (None, None, None, None)
    m = _ISO.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        t = m.group(4, 5, 6, 7)
    elif _DMY.match(s):
        m = _DMY.match(s)
        y = int(m.group(3))
        if len(m.group(3)) == 2:
            y += 2000 if y < 69 else 1900
        mo, d = int(m.group(2)), int(m.group(1))
        t = m.group(4, 5, 6, 7)
    elif _D_MON_Y.match(s) and _D_MON_Y.match(s).group(2)[:3].lower() in _MONTHS:
        m = _D_MON_Y.match(s)
        y, mo, d = int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1))
    elif _MON_D_Y.match(s) and _MON_D_Y.match(s).group(1)[:3].lower() in _MONTHS:
        m = _MON_D_Y.match(s)
        y, mo, d = int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2))
    else:
        return None
    if not _valid_date(y, mo, d):
        return None
    h, mi, sec = int(t[0] or 0), int(t[1] or 0), int(t[2] or 0)
    ap = (t[3] or "").upper()
    if ap == "PM" and h < 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    if h > 23 or mi > 59 or sec > 59:
        return None
    return {"y": y, "m": mo, "d": d, "H": h, "M": mi, "S": sec}


def format_date(v, mode="date"):
    """A date for Zenu: dd/mm/yyyy, or with the time (hh:mm / hh:mm:ss, 00:00 when the source has none).
    Text that isn't a date is kept as it is."""
    s = str(v or "").strip()
    if not s:
        return ""
    if mode not in DATE_MODES:
        mode = "date"
    if mode == "date" and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", s):
        return s
    p = parse_date(s)
    if not p:
        return s
    out = f"{p['d']:02d}/{p['m']:02d}/{p['y']}"
    if mode == "datetime":
        out += f" {p['H']:02d}:{p['M']:02d}"
    elif mode == "datetime_s":
        out += f" {p['H']:02d}:{p['M']:02d}:{p['S']:02d}"
    return out


def _date_ddmmyyyy(v: str) -> str:
    return format_date(v, "date")


# ---- permissions: what the column's values mean ----
FLAG_ROLES = ("do_not_sms", "do_not_email", "do_not_mail", "do_not_call", "subscribed")
FLAG_OUT = {"do_not_sms": "CONTACT_ALLOW_CONTACT_VIA_TEXT", "do_not_email": "CONTACT_ALLOW_CONTACT_VIA_EMAIL",
            "do_not_mail": "CONTACT_ALLOW_CONTACT_VIA_LETTER", "do_not_call": "CONTACT_ALLOW_CONTACT_VIA_PHONE",
            "subscribed": "CONTACT_UNSUBSCRIBE_FROM_MARKETING"}
FLAG_MEANINGS = ("allowed", "blocked", "filled_blocked", "filled_allowed")
FLAG_BLANKS = ("no", "yes", "empty")
_NEG = re.compile(r"(do\s*not|don'?t|opt\s*out|optout|unsub|block|exclude|suppress|\bno\s*(sms|text|e?mail|post|letter|call|phone)\b|\bdnc\b)", re.I)
_DATE_HDR = re.compile(r"(date|time|when|\bat$|\bon$)", re.I)
_DATE_LIKE = re.compile(r"^(\{\s*ts|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})", re.I)


def legacy_flag_mode(role):
    """How DARVIS read these columns before the setting existed (the workbook's rule)."""
    return {"meaning": "allowed" if role == "subscribed" else "blocked", "blank": "no"}


def suggest_flag_mode(role, header, samples=()):
    """Guess from the header (and samples) whether 1/Yes means allowed or blocked, or a filled-in date matters."""
    h = re.sub(r"[_\-.]+", " ", str(header or ""))
    neg = bool(_NEG.search(h))
    vals = [s for s in samples if s]
    dateish = bool(_DATE_HDR.search(h)) or (bool(vals) and all(_DATE_LIKE.match(str(s)) for s in vals))
    meaning = ("filled_blocked" if neg else "filled_allowed") if dateish else ("blocked" if neg else "allowed")
    return {"meaning": meaning, "blank": "no"}


def clean_flag_mode(mode, role):
    mode = mode if isinstance(mode, dict) else {}
    base = legacy_flag_mode(role)
    return {"meaning": mode.get("meaning") if mode.get("meaning") in FLAG_MEANINGS else base["meaning"],
            "blank": mode.get("blank") if mode.get("blank") in FLAG_BLANKS else base["blank"]}


def flag_allowed(value, mode):
    """-> (True / False / None for 'leave empty', recognised?)"""
    v = str(value or "").strip().lower()
    meaning = mode["meaning"]
    if meaning == "filled_blocked":
        return (not v), True
    if meaning == "filled_allowed":
        return bool(v), True
    if not v:
        return {"yes": True, "no": False, "empty": None}[mode["blank"]], True
    if v in _TRUE_WORDS:
        return meaning == "allowed", True
    if v in _FALSE_WORDS:
        return meaning != "allowed", True
    return False, False


def flag_mode_text(role, mode):
    sub = role == "subscribed"
    yes, no = ("subscribed", "unsubscribed") if sub else ("allowed", "do not contact")
    m = mode["meaning"]
    text = {"allowed": f"1 / Yes = {yes}", "blocked": f"1 / Yes = {no}",
            "filled_blocked": f"filled in = {no}", "filled_allowed": f"filled in = {yes}"}[m]
    if m in ("allowed", "blocked"):
        text += ", empty = " + {"no": "unsubscribed" if sub else "not allowed", "yes": "subscribed" if sub else "allowed",
                                "empty": "left empty"}[mode["blank"]]
    return text


def flag_text(role, allowed):
    if allowed is None:
        return ""
    if role == "subscribed":                       # the Zenu field is UNSUBSCRIBE_FROM_MARKETING
        return "FALSE" if allowed else "TRUE"
    return "TRUE" if allowed else "FALSE"


# --------------------------------------------------------------------
# NAME SPLITTING  (macro + I:BK formulas)
# --------------------------------------------------------------------
def name_blocks(raw_id, title, first, last, title2="", first2="", last2="", name_options=None):
    """One source row -> (blocks, note). blocks: 7 dicts in sheet order
    (Contact 1-5, Second Name 1-2), each {id, partner, partnership, type, title, first, surname}."""
    people, note = split_names(title, first, last, name_options)
    names = (people + [("", "", "")] * 5)[:5]
    second, note2 = split_names(title2, first2, last2, name_options) if (title2 or first2 or last2) else ([], "")
    seconds = (second + [("", "", "")] * 2)[:2]
    has = [bool(f or s) for _t, f, s in names]
    has_sn = [bool(f or s) for _t, f, s in seconds]
    a = raw_id
    i_id = f"{a}_c1" if (has[1] or has_sn[0]) else f"{a}_c"
    ids = [i_id] + [f"{a}_c{k + 1}" if has[k] else "" for k in range(1, 5)]
    h = sum(has[:4])                               # Contact 1 Split Count (blocks 1-4 only)
    ax = "" if not has_sn[0] else (f"{a}_c2" if not has[1] else f"{a}_c{h + 1}")
    be = "" if not has_sn[1] else (f"{a}_c{int(ax[-1]) + 1}" if ax else "")
    p_id = ids[1]
    joined = "" if (not p_id and not ax) else (
        f"{i_id}_" + "".join(f"{x}_" for x in (ids[1], ids[2], ids[3], ids[4], ax, be) if x) + "r")

    blocks = []
    m_title = names[0][0]
    # Contact 1
    if not p_id and not ax:
        partner, ctype = "", ""
    else:
        partner = p_id if p_id else (ax if i_id.endswith("c1") and ax else p_id)
        if p_id:
            ctype = _relationship(m_title, names[1][0])
        elif ax:
            ctype = _relationship(m_title, seconds[0][0])
        else:
            ctype = "Partner"
    blocks.append({"id": i_id, "partner": partner, "partnership": joined, "type": ctype,
                   "title": names[0][0], "first": names[0][1], "surname": names[0][2]})
    # Contacts 2-5
    for k in range(1, 5):
        if ids[k]:
            ctype = _relationship(names[1][0], m_title) if k == 1 else "Partner"
            blocks.append({"id": ids[k], "partner": i_id, "partnership": joined, "type": ctype,
                           "title": names[k][0], "first": names[k][1], "surname": names[k][2]})
        else:
            blocks.append({"id": "", "partner": "", "partnership": "", "type": "",
                           "title": names[k][0], "first": names[k][1], "surname": names[k][2]})
    # Second Name 1-2
    for k, sid in enumerate((ax, be)):
        if sid:
            ctype = (_relationship(seconds[0][0], m_title) if k == 0 and sid[-1] == "2" else "Partner")
            blocks.append({"id": sid, "partner": f"{a}_c1", "partnership": joined, "type": ctype,
                           "title": seconds[k][0], "first": seconds[k][1], "surname": seconds[k][2]})
        else:
            blocks.append({"id": "", "partner": "", "partnership": "", "type": "",
                           "title": seconds[k][0], "first": seconds[k][1], "surname": seconds[k][2]})
    return blocks, "; ".join(x for x in (note, note2) if x)


# --------------------------------------------------------------------
# CONTACT DETAILS  (macro + P:AB formulas)
# --------------------------------------------------------------------
def email_okay(e: str) -> str:
    """'Email Okay?' formula."""
    if not e or not e.strip():
        return "BLANK"
    if any(ch in e for ch in "&(,;") or " " in e.strip() or e.count("@") > 1:
        return "NO"
    return "YES"


def invalid_numbers_text(row) -> str:
    parts = [f"Invalid Mobile: {row['W']} " if row["W"] else "", f"Invalid Home: {row['X']} " if row["X"] else "",
             f"Invalid Work: {row['Y']} " if row["Y"] else "", f"Invalid Fax: {row['Z']} " if row["Z"] else ""]
    return excel_trim_spaces("".join(parts))


def excel_trim_spaces(s: str) -> str:
    return re.sub(r" {2,}", " ", s).strip(" ")


def extra_notes(row, split: bool) -> str:
    """'Extra Notes' formula - the split version leaves out the mobile overflow (H)."""
    aa = row["AA"]
    bits = ["Invalid Numbers:" + aa if aa else ""]
    if not split:
        bits.append("*MOBILE: " + row["H"] if row["H"] else "")
    bits += ["*HOME: " + row["I"] if row["I"] else "", "*WORK: " + row["K"] if row["K"] else "",
             "*FAX: " + row["M"] if row["M"] else "", "EMAIL: " + row["U"] if row["U"] else ""]
    return excel_trim_spaces(" ".join(bits))


# --------------------------------------------------------------------
# MAPPING PROPOSAL
# --------------------------------------------------------------------
def _norm(h) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()


def propose(columns, field_to_column: dict, learned=None, hints=None, column_method=None):
    """role -> column, plus role -> how it was found. First match wins, pass by pass:
      learned  - confirmed on an earlier run (RoleMemory)
      memory   - a header you taught DARVIS for a Zenu field
      guide    - header names in reference/guides/General Field Names.md (+ ROLE_HINTS)
      matcher  - Zenu aliases / fuzzy match (field_to_column)
      similar  - a multi-word guide name inside the header ('home phone' in 'Home Phone 1')
    field_to_column: Zenu field -> column (DARVIS's matcher); column_method: column -> matcher method."""
    from .knowledge import normalize_header
    learned, column_method = learned or {}, column_method or {}
    names = {role: {normalize_header(h) for h in ROLE_HINTS.get(role, ())} for role in ROLES}
    for role, extra in (hints or {}).items():
        if role in names:
            names[role] |= {normalize_header(h) for h in extra}
    norm = {c: normalize_header(c) for c in columns}
    out, how, used = {r: "" for r in ROLES}, {r: "" for r in ROLES}, set()

    def take(role, col, label):
        if col and col in norm and not out[role] and col not in used:
            out[role], how[role] = col, label
            used.add(col)

    for col, role in learned.items():                                   # 1 learned
        if role in ROLES:
            take(role, col, "learned")
    for role, (_l, field) in ROLES.items():                             # 2 taught Zenu field
        col = field_to_column.get(field, "") if field else ""
        if column_method.get(col) == "memory":
            take(role, col, "memory")
    for role in ROLES:                                                  # 3 guide names
        take(role, next((c for c in columns if c not in used and norm[c] in names[role]), ""), "guide")
    for role, (_l, field) in ROLES.items():                             # 4 matcher
        take(role, field_to_column.get(field, "") if field else "", "matcher")
    for role in ROLES:                                                  # 5 similar
        multi = [h for h in names[role] if " " in h]
        take(role, next((c for c in columns if c not in used
                         and any(f" {h} " in f" {norm[c]} " for h in multi)), ""), "similar")
    return out, how


def propose_mapping(columns, field_to_column: dict, learned=None, hints=None, column_method=None) -> dict:
    """role -> column (or ''). See propose()."""
    return propose(columns, field_to_column, learned, hints, column_method)[0]


# --------------------------------------------------------------------
# RUN
# --------------------------------------------------------------------
class GeneralCleaner:
    def __init__(self, mapping: dict, id_tag: str = "", address_splitter=None,
                 name_options=None, detail_options=None, split_multi_values=True, flag_modes=None, date_modes=None,
                 fill_state_from_postcode=True):
        self.mapping = {r: (c or "") for r, c in mapping.items() if r in ROLES}
        self.split_multi_values = split_multi_values
        self.id_tag = (id_tag or "").strip()
        self.splitter = address_splitter
        self.name_options = name_options
        self.detail_options = detail_options
        # flag_modes: role -> {meaning, blank} (missing = the workbook's rule); date_modes: role -> date | datetime | datetime_s
        self.flag_modes = {r: clean_flag_mode((flag_modes or {}).get(r), r) if (flag_modes or {}).get(r) else legacy_flag_mode(r)
                           for r in FLAG_ROLES}
        self.date_modes = {r: (date_modes or {}).get(r) if (date_modes or {}).get(r) in DATE_MODES else "date" for r in DATE_ROLES}
        self.fill_state_from_postcode = fill_state_from_postcode
        self.issues = []

    def run(self, df: pd.DataFrame) -> dict:
        n = len(df)
        get = lambda role: ([_cell(v) for v in df[self.mapping[role]].tolist()]
                            if self.mapping.get(role) and self.mapping[role] in df.columns else [""] * n)
        col = {role: get(role) for role in ROLES}

        # ---------- Transfer: raw IDs ----------
        base_ids = col["id"] if self.mapping.get("id") else [str(i + 1) for i in range(n)]
        raw_ids = [(f"{b}_{self.id_tag}" if self.id_tag else b) if b else "" for b in base_ids]
        dupes = [k for k, c in Counter(x for x in raw_ids if x).items() if c > 1]
        if dupes:
            self.issues.append(("FAIL", f"{len(dupes)} contact ID(s) appear more than once (e.g. {', '.join(dupes[:5])}) "
                                        f"- each raw ID must be unique or rows will share their details."))
        if not self.mapping.get("id"):
            self.issues.append(("INFO", "No ID column mapped - rows were numbered 1, 2, 3 ... as IDs."))

        # ---------- Name SPlitting ----------
        blocks_all, name_notes, company_flags = [], [], []
        for i in range(n):
            blocks, note = name_blocks(raw_ids[i], col["title"][i], col["first"][i], col["last"][i],
                                       col["title2"][i], col["first2"][i], col["last2"][i], self.name_options)
            blocks_all.append(blocks)
            name_notes.append(note)
            company_flags.append("TRUE" if col["company"][i] else "FALSE")

        # ---------- Spouse links: a column holding the partner's contact ID (another row) ----------
        # Both rows get each other as CONTACT_PARTNER_IDENTIFIER, one shared CONTACT_PARTNERSHIP_ID and type Partner -
        # like a split couple. Only rows that are one person each; a row is linked once.
        spouse = Counter()
        if self.mapping.get("spouse_id"):
            row_of = {}
            for i, b in enumerate(base_ids):
                if b and raw_ids[i] not in dupes:
                    row_of.setdefault(b.strip(), i)
            linked = set()

            def single(x):
                return not blocks_all[x][0]["partner"] and not any(blk["id"] for blk in blocks_all[x][1:])
            for i in range(n):
                s = col["spouse_id"][i].strip()
                if not s or not raw_ids[i] or s == base_ids[i].strip() or i in linked:
                    continue
                j = row_of.get(s)
                if j is None:
                    spouse["not_found"] += 1
                    continue
                back = col["spouse_id"][j].strip()
                if (back and back != base_ids[i].strip()) or j in linked:
                    spouse["conflict"] += 1
                    continue
                if not (single(i) and single(j)):
                    spouse["split"] += 1
                    continue
                lo, hi = (i, j) if i < j else (j, i)
                joined = f"{blocks_all[lo][0]['id']}_{blocks_all[hi][0]['id']}_r"
                for x, y in ((i, j), (j, i)):
                    blocks_all[x][0].update(partner=blocks_all[y][0]["id"], partnership=joined, type="Partner")
                    name_notes[x] = "; ".join(v for v in (name_notes[x], f"partner {blocks_all[y][0]['id']} linked from the spouse ID") if v)
                linked.update((i, j))
                spouse["linked"] += 1

        # ---------- Summary order (FillSummary) ----------
        people = []                                 # (source row, block index)
        src_no, counter = [0] * n, 0
        for i in range(n):
            if raw_ids[i]:
                counter += 1
                src_no[i] = counter
        for i in range(n):
            if raw_ids[i]:
                people.append((i, 0))
        n_main = len(people)
        for i in range(n):
            if not raw_ids[i]:
                continue
            for b in range(1, 7):
                blk = blocks_all[i][b]
                if blk["id"] and (blk["first"] or blk["surname"]):
                    people.append((i, b))
        persons_per_row = Counter(i for i, _b in people)

        # ---------- Contact Details ----------
        cleaner = ContactDetailsCleaner(self.detail_options)
        second = ContactDetailsCleaner(self.detail_options)       # partner mobile / email
        details = []
        for i in range(n):
            r = cleaner.clean(col["email"][i], col["mobile"][i], col["work"][i], col["home"][i], col["fax"][i])
            r["AA"] = invalid_numbers_text(r)
            r["V"] = extra_notes(r, persons_per_row[i] > 1)
            r["AB"] = email_okay(r["O"])
            r["Mobile 2"] = second.clean(mobile=col["mobile2"][i])["G"] if col["mobile2"][i] else ""
            r["Email2"] = next((e for e in re.split(r"[,;\s|/]+", col["email2"][i]) if is_valid_email(e)), "") \
                if col["email2"][i] else ""
            r["AL"] = email_okay(r["Email2"])
            details.append(r)

        # ---------- Address Cleaning ----------
        addresses, split_count, address_notes = [], 0, []
        pc_state = Counter()
        for i in range(n):
            raw_pc = col["postcode"][i]
            line1 = excel_trim_clean(col["street"][i])
            a = {"line1": line1, "line2": excel_trim_clean(col["street2"][i]),
                 "suburb": excel_trim_clean(col["suburb"][i]), "state": excel_trim_clean(col["state"][i]),
                 "country": excel_trim_clean(col["country"][i]),
                 "postcode": "" if raw_pc.strip() in ("", "0") else raw_pc.strip(), "note": ""}
            if self.splitter and col["street"][i]:
                res = self.splitter.clean(col["street"][i], a["suburb"], a["state"], a["postcode"])
                if "split" in res["flags"]:
                    split_count += 1
                a.update(line1=excel_trim_clean(res["line1"]), suburb=excel_trim_clean(res["suburb"]),
                         state=res["state"], postcode=res["postcode"])
                a["note"] = "; ".join(res["notes"])
            if (self.fill_state_from_postcode and self.splitter and not a["state"] and a["postcode"]
                    and re.sub(r"[^A-Z]", "", a["country"].upper()) in ("", "AUSTRALIA", "AU", "AUS")):
                st, how, note = self.splitter.state_for_postcode(a["postcode"], a["suburb"])
                if st:
                    a["state"] = st
                pc_state[how] += 1
                if note:
                    a["note"] = "; ".join(x for x in (a["note"], note) if x)
            addresses.append(a)
            address_notes.append(a["note"])

        # ---------- Summary rows ----------
        flag_vals, flag_odd = {}, Counter()
        # is_contactable = 1: a permission whose own cell is blank (or that isn't in the export) becomes allowed
        contactable = ([v.strip().lower() in _TRUE_WORDS for v in col["contactable"]] if self.mapping.get("contactable")
                       else [False] * n)
        contact_fills = 0
        for role in FLAG_ROLES:
            mapped = bool(self.mapping.get(role))
            if not mapped and not any(contactable):
                continue
            vals = []
            for i in range(n):
                if contactable[i] and (not mapped or not col[role][i].strip()):
                    vals.append(flag_text(role, True))
                    contact_fills += 1
                    continue
                if not mapped:
                    vals.append("")
                    continue
                allowed, known = flag_allowed(col[role][i], self.flag_modes[role])
                flag_odd[role] += 0 if known else 1
                vals.append(flag_text(role, allowed))
            flag_vals[role] = vals

        def flag_allow(role, i):
            return flag_vals[role][i] if role in flag_vals else ""

        date_odd = Counter()
        for role in DATE_ROLES:
            for v in col[role]:
                if v.strip() and not parse_date(v.strip()):
                    date_odd[role] += 1

        sep = cleaner.opt["multi_sep"]
        rank, given_own = Counter(), {"mobile": 0, "email": 0}

        def own_value(joined, k, people_here, kind):
            """Person k (0-based) of a split record: their own value from 'A | B | ...'."""
            parts = [x for x in joined.split(sep) if x.strip()] if joined else []
            if not self.split_multi_values or people_here < 2 or len(parts) < 2:
                return joined                                 # the workbook's rule
            if k == 0:                                        # person 1 also keeps any leftovers
                return sep.join([parts[0]] + parts[people_here:])
            if k < len(parts):
                given_own[kind] += 1
                return parts[k]
            return parts[0]                                   # fewer values than people: shared

        rows = []
        for i, b in people:
            blk, d, a = blocks_all[i][b], details[i], addresses[i]
            count = contact_no(blk["id"])
            k = rank[i]
            rank[i] += 1
            mobile = (d["Mobile 2"] if count == 2 else "") or own_value(d["G"], k, persons_per_row[i], "mobile")
            email = (d["Email2"] if count == 2 else "") or own_value(d["O"], k, persons_per_row[i], "email")
            first, surname = blk["first"], blk["surname"]
            company = col["company"][i]
            contact_name = company if company_flags[i] == "TRUE" else " ".join(x for x in (first, surname) if x)
            notes = " ".join(x for x in (col["notes"][i].strip(), d["V"]) if x)
            row = {
                "Indicator": src_no[i], "Contact Count": count, "Raw ORIG CONTACT_IDENTIFIER": raw_ids[i],
                "CONTACT_IDENTIFIER": blk["id"], "CONTACT_PARTNER_IDENTIFIER": blk["partner"],
                "CONTACT_PARTNERSHIP_ID": blk["partnership"], "CONTACT_PARTNERSHIP_TYPE": blk["type"],
                "CONTACT_TITLE": blk["title"], "CONTACT_FIRST_NAME": first, "CONTACT_SURNAME": surname,
                "CONTACT_IS_COMPANY": company_flags[i], "CONTACT_COMPANY_NAME": company,
                "CONTACT_MOBILE": mobile, "CONTACT_PHONE_HOME": d["J"], "CONTACT_PHONE_WORK": d["L"],
                "CONTACT_FAX": d["N"], "CONTACT_EMAIL_ADDRESS": email,
                "CONTACT_ALLOW_CONTACT_VIA_TEXT": flag_allow("do_not_sms", i),
                "CONTACT_ALLOW_CONTACT_VIA_EMAIL": flag_allow("do_not_email", i),
                "CONTACT_ALLOW_CONTACT_VIA_LETTER": flag_allow("do_not_mail", i),
                "CONTACT_ALLOW_CONTACT_VIA_PHONE": flag_allow("do_not_call", i),
                "CONTACT_UNSUBSCRIBE_FROM_MARKETING": flag_allow("subscribed", i),
                "CONTACT_MAILING_ADDRESS_LINE1": a["line1"], "CONTACT_MAILING_ADDRESS_LINE2": a["line2"],
                "CONTACT_MAILING_ADDRESS_SUBURB": a["suburb"], "CONTACT_MAILING_ADDRESS_STATE": a["state"],
                "CONTACT_MAILING_ADDRESS_COUNTRY": a["country"], "CONTACT_MAILING_ADDRESS_POSTCODE": a["postcode"],
                "CONTACT_TYPE": col["type"][i].strip(), "CONTACT_ENQUIRY_SOURCE": col["source"][i].strip(),
                "CONTACT_NAME_ON_LETTERS": col["salutation"][i].strip(), "CONTACT_LEGAL_NAME": col["legal"][i].strip(),
                "CONTACT_TAGS": col["tags"][i].strip(), "CONTACT_NOTES": notes,
                "CONTACT_TEAM_MEMBER_1": col["team1"][i].strip(),
                "CONTACT_DATE_MODIFIED": format_date(col["modified"][i].strip(), self.date_modes["modified"]),
                "CONTACT_DATE_CREATED": format_date(col["created"][i].strip(), self.date_modes["created"]), "Contact Name": contact_name,
                "CONTACT_COMPANY_POSITION": col["position"][i].strip(),
                "ZENU_CONTACT_ID": (col["zenu_id"][i].strip() if b == 0 else ""),
                "CONTACT_SUBSCRIPTIONS": col["subscriptions"][i].strip(),
                "CONTACT_MAILING_ADDRESS_TOWN_CITY": col["town_city"][i].strip(),
                "CONTACT_CHILDRENS_NAMES": col["children"][i].strip(),
                "CONTACT_RATING": col["rating"][i].strip(),
                "CONTACT_LAST_CONTACT_DATE": format_date(col["last_contact"][i].strip(), self.date_modes["last_contact"]),
                "CONTACT_TEAM_MEMBER_2": col["team2"][i].strip(),
                "CONTACT_TEAM_MEMBER_3": col["team3"][i].strip(),
                "CONTACT_TEAM_MEMBER_4": col["team4"][i].strip(),
                "CONTACT_TEAM_MEMBER_5": col["team5"][i].strip(),
                "CONTACT_TEAM_MEMBER_6": col["team6"][i].strip(),
                "CONTACT_TEAM_MEMBER_7": col["team7"][i].strip(),
                "CONTACT_TEAM_MEMBER_8": col["team8"][i].strip(),
                "CONTACT_TEAM_MEMBER_9": col["team9"][i].strip(),
                "CONTACT_TEAM_MEMBER_10": col["team10"][i].strip(),
                "CONTACT_TEAM_MEMBER_11": col["team11"][i].strip(),
                "CONTACT_TEAM_MEMBER_12": col["team12"][i].strip(),
                "CONTACT_TEAM_MEMBER_13": col["team13"][i].strip(),
                "CONTACT_TEAM_MEMBER_14": col["team14"][i].strip(),
                "CONTACT_TEAM_MEMBER_15": col["team15"][i].strip(),
                "CONTACT_SALE_TYPE": col["sale_type"][i].strip(),
                "ZENU_SOLICITOR_ID": col["solicitor_id"][i].strip(),
                "ZENU_COMPANY_ID": col["company_id"][i].strip(),
            }
            rows.append({c: row.get(c, "") for c in SUMMARY_COLUMNS})
        summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)

        # ---------- working tabs (same layout idea as the workbook) ----------
        ns_rows = []
        for i in range(n):
            r = {"ID": raw_ids[i], "CONTACT_COMPANY_NAME": col["company"][i], "Title": col["title"][i],
                 "First Name": col["first"][i], "Last Name": col["last"][i],
                 "CONTACT_IS_COMPANY": company_flags[i]}
            for k, label in enumerate(("Contact 1", "Contact 2", "Contact 3", "Contact 4", "Contact 5",
                                       "Second Name 1", "Second Name 2")):
                blk = blocks_all[i][k]
                for key, name in (("id", "ID"), ("partner", "Partner ID"), ("partnership", "Partnership ID"),
                                  ("type", "Type"), ("title", "Title"), ("first", "First"), ("surname", "Surname")):
                    r[f"{label} {name}"] = blk[key]
            r["Review notes"] = name_notes[i]
            ns_rows.append(r)
        cd_rows = []
        for i in range(n):
            d = details[i]
            cd_rows.append({
                "Raw ORIG CONTACT_IDENTIFIER": raw_ids[i], "Raw CONTACT_EMAIL_ADDRESS": col["email"][i],
                "Raw CONTACT_MOBILE": col["mobile"][i], "Raw CONTACT_PHONE_WORK": col["work"][i],
                "Raw CONTACT_PHONE_HOME": col["home"][i], "Raw CONTACT_FAX": col["fax"][i],
                "Mobile": d["G"], "Mobile Landline": d["H"], "Home Mobile": d["I"], "HomeLandline": d["J"],
                "Work Mobile": d["K"], "WorkLandline": d["L"], "Fax Mobile": d["M"], "Fax Landline": d["N"],
                "Email Address": d["O"], "Invalid Email and extra Email": d["U"], "Extra Notes": d["V"],
                "Invalid Mobile - Get Raw mobile": d["W"], "Invalid Home - get raw home": d["X"],
                "Invalid Work - get raw work": d["Y"], "Invalid Fax": d["Z"], "Invalid Numbers": d["AA"],
                "Email Okay?": d["AB"], "Email2": d["Email2"], "Mobile 2": d["Mobile 2"],
                "Removed from phone cells (no digits)": ", ".join(d["dropped"]),
            })
        ad_rows = []
        for i in range(n):
            a = addresses[i]
            ad_rows.append({
                "Raw ORIG CONTACT_IDENTIFIER": raw_ids[i], "Raw street": col["street"][i],
                "Raw suburb": col["suburb"][i], "Raw postcode": col["postcode"][i], "Raw state": col["state"][i],
                "Raw country": col["country"][i], "CONTACT_MAILING_ADDRESS_LINE1": a["line1"],
                "CONTACT_MAILING_ADDRESS_LINE2": a["line2"], "CONTACT_MAILING_ADDRESS_SUBURB": a["suburb"],
                "CONTACT_MAILING_ADDRESS_STATE": a["state"], "CONTACT_MAILING_ADDRESS_COUNTRY": a["country"],
                "CONTACT_MAILING_ADDRESS_POSTCODE": a["postcode"], "DARVIS address note": a["note"],
            })

        # ---------- checks + counts (FillSummary message + macro counts) ----------
        c, rec = cleaner.count, cleaner.reconciliation()
        stats = {
            "source_rows": n, "source_contacts": counter, "main_rows": n_main,
            "split_rows": len(people) - n_main, "total_rows": len(people),
            "records_split": sum(1 for i in range(n) if persons_per_row[i] > 1),
            "name_notes": sum(1 for x in name_notes if x),
            "Mobile (G)": sum(1 for d in details if d["G"]), "Home Landline (J)": sum(1 for d in details if d["J"]),
            "Work Landline (L)": sum(1 for d in details if d["L"]), "Fax (N)": sum(1 for d in details if d["N"]),
            "Email (O)": sum(1 for d in details if d["O"]),
            "Invalid/extra email (U)": sum(1 for d in details if d["U"]),
            "NZ mobiles placed": c["nz_mobiles"], "International numbers placed": c["international"],
            "Invalid numbers (W:Z)": c["invalid_numbers"],
            "Emails moved from phone columns": c["emails_moved_from_phones"],
            "phone_values_in": rec["phone_parts_in"], "phones_reconciled": rec["phones_ok"],
            "text_dropped_from_phones": c["text_dropped_from_phones"],
            "email_not_okay": sum(1 for d in details if d["AB"] == "NO"),
            "addresses_split": split_count, "address_notes": sum(1 for x in address_notes if x),
            "state_from_postcode": pc_state["list"] + pc_state["range"], "state_postcode_ambiguous": pc_state["ambiguous"],
            "couples_linked_from_spouse_id": spouse["linked"], "spouse_id_not_in_file": spouse["not_found"],
            "spouse_id_conflicts": spouse["conflict"], "spouse_id_row_already_split": spouse["split"],
            "permissions_from_contactable": contact_fills,
            "titles_dr": sum(1 for i, b in people if blocks_all[i][b]["title"] == "Dr"),
            "own_mobile_from_shared_cell": given_own["mobile"],
            "own_email_from_shared_cell": given_own["email"],
        }
        if spouse["linked"]:
            self.issues.append(("INFO", f"{spouse['linked']} couple(s) linked from the spouse ID column: each gets the other as "
                                        f"CONTACT_PARTNER_IDENTIFIER, a shared CONTACT_PARTNERSHIP_ID and type Partner."))
        if spouse["not_found"]:
            self.issues.append(("WARN", f"{spouse['not_found']} spouse ID(s) aren't a contact ID in this file - left unlinked."))
        if spouse["conflict"]:
            self.issues.append(("WARN", f"{spouse['conflict']} spouse ID(s) point at a contact whose own spouse ID is someone else "
                                        f"(or who is already linked) - left unlinked."))
        if spouse["split"]:
            self.issues.append(("INFO", f"{spouse['split']} spouse ID(s) not used: the row (or its spouse's row) already splits into "
                                        f"several people, who are linked to each other."))
        if pc_state["list"] or pc_state["range"]:
            self.issues.append(("INFO", f"{pc_state['list'] + pc_state['range']} blank state(s) filled from the postcode "
                                        f"({pc_state['list']} from the suburb list, {pc_state['range']} from Australia Post postcode ranges)."))
        if pc_state["ambiguous"]:
            self.issues.append(("WARN", f"{pc_state['ambiguous']} blank state(s) left blank: the postcode is in more than one "
                                        f"state and the suburb doesn't settle it - see Address Cleaning."))
        if contact_fills:
            self.issues.append(("INFO", f"{contact_fills} permission cell(s) were blank and set to allowed because the contact is contactable ('{self.mapping['contactable']}' = 1)."))
        for role in FLAG_ROLES:
            if flag_odd[role]:
                self.issues.append(("WARN", f"{flag_odd[role]} value(s) in '{self.mapping[role]}' ({ROLES[role][0]}) weren't yes/no "
                                            f"values and were treated as not allowed."))
        for role in DATE_ROLES:
            if date_odd[role]:
                self.issues.append(("WARN", f"{date_odd[role]} value(s) in '{self.mapping[role]}' ({ROLES[role][0]}) couldn't be read "
                                            f"as dates and were left as they are."))
        if not rec["phones_ok"]:
            self.issues.append(("FAIL", f"Phone values don't reconcile ({rec['phone_parts_in']} in, "
                                        f"{rec['phone_parts_accounted']} accounted for)."))
        if stats["Invalid numbers (W:Z)"]:
            self.issues.append(("WARN", f"{stats['Invalid numbers (W:Z)']} invalid phone value(s) - kept in the "
                                        f"Invalid columns and added to CONTACT_NOTES."))
        if stats["text_dropped_from_phones"]:
            self.issues.append(("WARN", f"{stats['text_dropped_from_phones']} value(s) with no digits were removed "
                                        f"from phone cells by the macro rule - see Contact Details."))
        if stats["email_not_okay"]:
            self.issues.append(("WARN", f"{stats['email_not_okay']} record(s) fail 'Email Okay?' (several emails or "
                                        f"odd characters in CONTACT_EMAIL_ADDRESS)."))
        if stats["titles_dr"]:
            self.issues.append(("INFO", f"{stats['titles_dr']} contact(s) titled Dr (allowed by the Contacts "
                                        f"Mapping, not in Zenu's documented list)."))
        if given_own["mobile"] or given_own["email"]:
            self.issues.append(("INFO", f"Split people given their own value from a shared cell: "
                                        f"{given_own['mobile']} mobile(s), {given_own['email']} email(s) "
                                        f"(person 1 gets the first value, person 2 the second ...)."))
        if stats["split_rows"]:
            self.issues.append(("INFO", f"{stats['records_split']} record(s) split into {stats['split_rows']} extra "
                                        f"people - rows {n_main + 2}-{len(people) + 1} of the Summary."))
        settings = {role: flag_mode_text(role, self.flag_modes[role]) for role in FLAG_ROLES if self.mapping.get(role)}
        if self.mapping.get("contactable"):
            settings["contactable"] = "1 / Yes = contactable: blank permissions become allowed"
        settings.update({role: DATE_MODES[self.date_modes[role]] for role in DATE_ROLES if self.mapping.get(role)})
        return {"settings": settings, "summary": summary, "name_splitting": pd.DataFrame(ns_rows),
                "contact_details": pd.DataFrame(cd_rows), "address_cleaning": pd.DataFrame(ad_rows),
                "stats": stats, "issues": self.issues}


def write_general_workbook(result: dict, mapping: dict, path: str, id_tag: str = ""):
    settings = result.get("settings") or {}
    mapping_rows = [{"Role": ROLES[r][0], "Source column": c or "(not mapped)", "Setting": settings.get(r, "")}
                    for r, c in mapping.items() if r in ROLES]
    checks = [{"Item": k, "Value": v} for k, v in result["stats"].items()]
    checks += [{"Item": "", "Value": ""}, {"Item": "ID date tag", "Value": id_tag or "(none)"},
               {"Item": "Generated", "Value": datetime.now().strftime("%d/%m/%Y %H:%M:%S")}]
    issues = [{"Severity": s, "Detail": d} for s, d in result["issues"]] or [{"Severity": "PASS", "Detail": "No issues."}]
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for name, frame in (("Summary", result["summary"]), ("Name SPlitting", result["name_splitting"]),
                            ("Contact Details", result["contact_details"]),
                            ("Address Cleaning", result["address_cleaning"]),
                            ("Column Mapping", pd.DataFrame(mapping_rows)), ("Checks", pd.DataFrame(checks)),
                            ("Issues", pd.DataFrame(issues))):
            frame.to_excel(w, sheet_name=name, index=False, freeze_panes=(1, 0))
            ws = w.sheets[name]
            if ws.max_row > 1:
                ws.auto_filter.ref = ws.dimensions
