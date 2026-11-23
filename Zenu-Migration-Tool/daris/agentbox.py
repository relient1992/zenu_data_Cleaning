import base64
import binascii
import re
from collections import OrderedDict, defaultdict

import pandas as pd

# =====================================================================
# AGENTBOX CONTACTS  - the Agentbox parts of "Contact Details Cleaning - General.xlsb"
# ---------------------------------------------------------------------
# Agentbox exports four CSVs (Import_agentbox.bas):
#   contact.csv            the people - mapped by its fixed header names (contact tab A:AR)
#   contact_category.csv   categories -> CONTACT_TYPE (Agentbox type list) / CONTACT_TAGS (the rest)
#   contact_x_agent.csv    + agent.csv -> CONTACT_TEAM_MEMBER_1 (primary_owner first)
#   comments               '[base64]...' -> decoded text (the DecodeBase64 UDF) -> CONTACT_NOTES
# Each derived value becomes a column on the contact frame, so the normal mapping review
# shows it and General cleaning (general_cleaning.py) does the rest.
#
# Two deliberate differences from the workbook:
#  - The workbook's Summary looks these up by the TAGGED id ('123_04112026') in tabs that hold
#    the raw id ('123'), so Type / Tags / Team member / Notes / Source / Name on letters /
#    dates come out blank. DARIS joins on the raw contact_id.
#  - DecodeBase64 reads the bytes with the Windows codepage and errors on plain text (which
#    the Summary then drops). DARIS decodes UTF-8 and keeps a plain-text comment as it is.
# =====================================================================

TYPE_LIST = ("Buyer|Vendor|Bidder|Past Vendor|Investor|Web Enquiry|Prospective Rental Provider|Rental Provider|"
             "Website Signup|Purchaser|Owner|Prospective Vendor|Prospective Landlord|Enquiry|Unknown|Landlord|"
             "Accountant|Auctioneer|Business Buyer|Solicitor|Buyers Agent|Conjunctional Agent|Developer|"
             "Managing Agent|Owner Absentee|Absentee Owner|Owner Occupier|Occupier|Past Buyer|Past Landlord|"
             "Past Owner|Past Tenant|Prospective Buyer|Prospective Tenant|Referrer|Strata Manager|Supplier|"
             "Tradesperson|Valuer|Vendor Solicitor|Tenant|Potential Buyer|Potential Seller|Past Vendors|"
             "Developers|VIP|Past Purchaser|Renter|Potential Vendor|Prospective Buyers|Owners|Tenants|Lessor|"
             "Lessee|Sellers").split("|")
TYPES = {}
for _t in TYPE_LIST:
    TYPES.setdefault(_t.lower(), _t)

TYPE_COL = "Contact Type (from contact_category.csv)"
TAGS_COL = "Contact Tags (from contact_category.csv)"
TEAM_COL = "Team member 1 (from contact_x_agent.csv + agent.csv)"
NOTES_COL = "comments (Base64 decoded)"

# role -> contact.csv header (Import_agentbox.bas transfer + the Summary's contact!A:AR lookups)
CONTACT_MAPPING = {
    "id": "contact_id", "title": "title", "first": "first_name", "last": "last_name",
    "company": "company_name", "email": "email", "home": "home_phone", "work": "work_phone",
    "mobile": "mobile", "fax": "fax", "street": "street_address", "suburb": "suburb", "state": "state",
    "postcode": "postcode", "country": "country", "salutation": "salutation", "legal": "legal_name",
    "position": "job_title", "source": "source", "created": "date_created", "modified": "last_modified",
    "do_not_call": "do_not_call", "do_not_sms": "do_not_sms", "do_not_email": "do_not_email",
    "do_not_mail": "do_not_mail", "subscribed": "subscribed_newsletters",
}
DERIVED_MAPPING = {"type": TYPE_COL, "tags": TAGS_COL, "team1": TEAM_COL, "notes": NOTES_COL}

FILES = OrderedDict([
    ("contact", "contact.csv"), ("contact_category", "contact_category.csv"),
    ("contact_x_agent", "contact_x_agent.csv"), ("agent", "agent.csv"),
])


def _h(headers):
    return {str(h).strip().lower(): h for h in headers}


def kind_of(headers):
    """Which Agentbox export a sheet is, from its header names (None = not Agentbox)."""
    h = _h(headers)
    if {"contact_id", "first_name", "last_name"} <= h.keys() and ({"street_address", "mobile", "email"} & h.keys()):
        return "contact"
    if {"contact_id", "category_name"} <= h.keys():
        return "contact_category"
    if {"contact_id", "agent_id", "role"} <= h.keys():
        return "contact_x_agent"
    if {"agent_id", "first_name", "last_name"} <= h.keys() and "contact_id" not in h:
        return "agent"
    return None


def find_sources(sources):
    """{kind: source} for the first sheet of each Agentbox export kind."""
    found = {}
    for src in sources:
        k = kind_of(src["headers"])
        if k and k not in found:
            found[k] = src
    return found


def _key(v) -> str:
    s = str(v).strip()
    return s[:-2] if re.fullmatch(r"-?\d+\.0", s) else s


def _xl_trim(s) -> str:
    """Excel TRIM (spaces only) after turning non-breaking spaces into spaces."""
    return re.sub(r" {2,}", " ", str(s).replace(" ", " ")).strip(" ")


def split_type_tags(raw: str):
    """SplitTypeTags: split on ',', trim, list match (canonical casing) -> type, else tag;
    dedupe case-insensitively; rejoin with ',' (no space)."""
    types, tags, seen_t, seen_g = [], [], set(), set()
    for part in str(raw or "").split(","):
        s = _xl_trim(part)
        if not s:
            continue
        k = s.lower()
        if k in TYPES:
            if k not in seen_t:
                seen_t.add(k)
                types.append(TYPES[k])
        elif k not in seen_g:
            seen_g.add(k)
            tags.append(s)
    return ",".join(types), ",".join(tags)


def decode_comment(text: str):
    """-> (text, how): how is 'empty' | 'decoded' | 'plain' | 'bad'."""
    s = str(text or "")
    if not s.strip():
        return "", "empty"
    if "[base64]" not in s:
        return s, "plain"
    b64 = re.sub(r"\s+", "", s.replace("[base64]", ""))
    try:
        raw = base64.b64decode(b64 + "=" * (-len(b64) % 4), validate=False)
    except (binascii.Error, ValueError):
        return s, "bad"
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc), "decoded"
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "decoded"


def categories_by_contact(df):
    """BuildContactCategory: contact_id -> categories joined with ',' in file order."""
    h = _h(df.columns)
    out = OrderedDict()
    for cid, cat in zip(df[h["contact_id"]].tolist(), df[h["category_name"]].tolist()):
        k = _key(cid)
        if not k:
            continue
        c = str(cat).strip()
        prev = out.setdefault(k, "")
        if c:
            out[k] = f"{prev},{c}" if prev else c
    return out


def agent_names(df):
    """agent!W = TEXTJOIN(' ', TRUE, first_name, last_name)."""
    h = _h(df.columns)
    return {_key(a): " ".join(x for x in (str(f).strip(), str(l).strip()) if x)
            for a, f, l in zip(df[h["agent_id"]], df[h["first_name"]], df[h["last_name"]]) if _key(a)}


def team_member_1(df):
    """contact_x_agent!J: the contact's first agent, primary_owner rows first (stable)."""
    h = _h(df.columns)
    rows = defaultdict(list)
    for cid, aid, role in zip(df[h["contact_id"]], df[h["agent_id"]], df[h["role"]]):
        k = _key(cid)
        if k:
            rows[k].append((str(role).strip() != "primary_owner", _key(aid)))
    return {k: sorted(v, key=lambda x: x[0])[0][1] for k, v in rows.items()}


def build_frame(found):
    """Contact frame + the derived Agentbox columns. -> (frame, mapping, info)."""
    contact = found["contact"]
    df = contact["data"].copy()
    h = _h(df.columns)
    ids = [_key(v) for v in df[h["contact_id"]].tolist()]
    info = {"files": {k: (found[k]["file"] if k in found else "") for k in FILES}, "counts": {}}
    mapping = {role: h[col] for role, col in CONTACT_MAPPING.items() if col in h}
    extra_cols = []

    if "contact_category" in found:
        cats = categories_by_contact(found["contact_category"]["data"])
        split = [split_type_tags(cats.get(i, "")) for i in ids]
        df[TYPE_COL] = [t for t, _g in split]
        df[TAGS_COL] = [g for _t, g in split]
        mapping.update(type=TYPE_COL, tags=TAGS_COL)
        extra_cols += [TYPE_COL, TAGS_COL]
        info["counts"].update(with_type=sum(1 for t, _g in split if t), with_tags=sum(1 for _t, g in split if g),
                              category_contacts_not_in_contact_csv=len(set(cats) - set(ids)))

    if "contact_x_agent" in found:
        first = team_member_1(found["contact_x_agent"]["data"])
        names = agent_names(found["agent"]["data"]) if "agent" in found else {}
        team = [names.get(first.get(i, ""), "") for i in ids]
        df[TEAM_COL] = team
        mapping["team1"] = TEAM_COL
        extra_cols.append(TEAM_COL)
        missing = {first[i] for i in ids if first.get(i) and first[i] not in names}
        info["counts"].update(with_team_member=sum(1 for t in team if t), agents_not_in_agent_csv=len(missing))

    if "comments" in h:
        decoded = [decode_comment(v) for v in df[h["comments"]].tolist()]
        df[NOTES_COL] = [t for t, _how in decoded]
        mapping["notes"] = NOTES_COL
        extra_cols.append(NOTES_COL)
        hows = [how for _t, how in decoded]
        info["counts"].update(comments_decoded=hows.count("decoded"), comments_plain_text=hows.count("plain"),
                              comments_not_decodable=hows.count("bad"))

    info["extra_columns"] = extra_cols
    return df, mapping, info


def link_notes(info):
    """Plain-English lines about the linked CSVs, for the review note and the Issues tab."""
    f, c, lines = info["files"], info["counts"], []
    if not f["contact_category"]:
        lines.append(("WARN", "contact_category.csv isn't uploaded - CONTACT_TYPE and CONTACT_TAGS will be blank."))
    if not f["contact_x_agent"]:
        lines.append(("WARN", "contact_x_agent.csv isn't uploaded - CONTACT_TEAM_MEMBER_1 will be blank."))
    elif not f["agent"]:
        lines.append(("WARN", "agent.csv isn't uploaded - agent IDs can't be turned into names, so "
                              "CONTACT_TEAM_MEMBER_1 will be blank."))
    if c.get("agents_not_in_agent_csv"):
        lines.append(("WARN", f"{c['agents_not_in_agent_csv']} agent ID(s) in contact_x_agent.csv aren't in agent.csv "
                              f"- those contacts get no team member."))
    if c.get("comments_not_decodable"):
        lines.append(("WARN", f"{c['comments_not_decodable']} comment(s) marked [base64] couldn't be decoded - "
                              f"kept as they are."))
    if c.get("category_contacts_not_in_contact_csv"):
        lines.append(("INFO", f"{c['category_contacts_not_in_contact_csv']} contact ID(s) in contact_category.csv "
                              f"aren't in contact.csv (ignored)."))
    return lines
