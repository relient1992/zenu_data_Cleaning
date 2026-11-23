import json
import os
import re
import time
from datetime import datetime

from . import agentbox
from .address_cleaning import AddressSplitter
from .consolidator import SmartConsolidator
from .general_cleaning import (DATE_MODES, DATE_ROLES, FLAG_ROLES, ROLES, GeneralCleaner, _FALSE_WORDS, _TRUE_WORDS,
                               clean_flag_mode, format_date, parse_date, propose, suggest_flag_mode, write_general_workbook)
from .knowledge import is_placeholder
from .memory import MappingMemory, RoleMemory
from .references import library

# =====================================================================
# CONTACT CLEANING IN A DARIS SESSION - two handlers, same cleaning rules
#   agentbox  Agentbox's 4 CSVs: contact.csv mapped by its fixed headers, plus Contact
#             Type / Tags, Team member 1 and decoded comments from the other CSVs
#             (agentbox.py)
#   general   any other contact export: DARIS proposes role -> column per sheet
#             (memory + aliases + fuzzy, no AI guessing)
# ---------------------------------------------------------------------
#   1. review_cards(session, profile) -> editable mapping card(s): the user checks /
#      changes the dropdowns and the ID date tag
#   2. run(session, ...)      -> runs GeneralCleaner with the CONFIRMED mapping and
#      writes the Summary workbook (main contacts first, split people at the bottom)
# Nothing is written before the user presses Run.
# =====================================================================

# Card sections, in the order the workbook's tabs use them
GROUPS = [
    ("Contact", ("id", "company", "title", "first", "last")),
    ("Couples in separate rows", ("spouse_id",)),
    ("Zenu IDs (records already in Zenu)", ("zenu_id", "company_id", "solicitor_id")),
    ("Second person (if the file has separate columns)", ("title2", "first2", "last2", "mobile2", "email2")),
    ("Phones & email", ("email", "mobile", "home", "work", "fax")),
    ("Address", ("street", "street2", "suburb", "town_city", "state", "postcode", "country")),
    ("Other details", ("salutation", "legal", "position", "type", "tags", "subscriptions", "source", "rating",
                       "sale_type", "children", "notes", "created", "modified", "last_contact")),
    ("Team members", tuple(f"team{i}" for i in range(1, 16))),
    ("Permissions", ("do_not_sms", "do_not_email", "do_not_mail", "do_not_call", "subscribed", "contactable")),
]
assert sorted(r for _g, rs in GROUPS for r in rs) == sorted(ROLES), "every role needs a card group"

SAMPLES = 3
SAMPLE_LEN = 48


def today_tag():
    return datetime.now().strftime("%d%m%Y")


def _ingest(paths):
    c = SmartConsolidator(use_llm=False, log=lambda _m: None)
    c.ingest(paths)
    c.map_columns()
    return c


def _samples(series):
    out, seen = [], set()
    for v in series.tolist():
        s = re.sub(r"\s+", " ", str(v)).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s if len(s) <= SAMPLE_LEN else s[:SAMPLE_LEN - 1] + "…")
            if len(out) == SAMPLES:
                break
    return out


def _filled(series):
    return int((series.astype(str).str.strip() != "").sum())


PROFILE_NAMES = {"general": "General cleaning", "agentbox": "Agentbox cleaning"}
SATELLITES = ("contact_category", "contact_x_agent", "agent")


def _flag_counts(series):
    """1/yes, 0/no, empty and other values in a column - the page works out TRUE/FALSE for any setting from these."""
    t = f = b = o = 0
    for v in series.astype(str).tolist():
        s = v.strip().lower()
        if not s:
            b += 1
        elif s in _TRUE_WORDS:
            t += 1
        elif s in _FALSE_WORDS:
            f += 1
        else:
            o += 1
    return {"yes": t, "no": f, "empty": b, "other": o}


def _date_preview(series):
    """For a column whose first values are dates: the first value in each format, and how many can't be read."""
    vals = [v.strip() for v in series.astype(str).tolist() if v.strip()]
    if not vals or not any(parse_date(v) for v in vals[:5]):
        return None
    first = vals[0]
    return {"sample": first[:60], **{m: format_date(first, m) for m in DATE_MODES},
            "unreadable": sum(1 for v in vals if not parse_date(v))}


def suggested_modes(data, mapping):
    """The page's starting point for permission columns: guessed from each mapped column's header and values."""
    return {r: suggest_flag_mode(r, mapping[r], _samples(data[mapping[r]])) for r in FLAG_ROLES
            if mapping.get(r) and mapping[r] in data.columns}


def _card(src, data, headers, proposal, how, profile, role_how=None):
    columns = [{"name": h, "samples": _samples(data[h]), "filled": _filled(data[h]),
                "method": how.get(h, ""), "flags": _flag_counts(data[h]), "dates": _date_preview(data[h])} for h in headers]
    used = {v for v in proposal.values() if v}
    return {
        "kind": "general_mapping", "profile": profile, "title": PROFILE_NAMES[profile],
        "file": src["file"], "sheet": src["sheet"], "rows": len(data),
        "header_row": src["header_row"], "headerless": bool(src.get("headerless")),
        "columns": columns,
        "groups": [{"title": title, "roles": [
            {"role": r, "label": ROLES[r][0], "column": proposal.get(r, ""),
             "field": ROLES[r][1] or "",
             "method": (role_how or {}).get(r) or how.get(proposal.get(r, ""), "")}
            for r in roles]} for title, roles in GROUPS],
        "mapping": proposal,
        "flag_modes": suggested_modes(data, proposal),
        "flag_roles": list(FLAG_ROLES), "date_roles": list(DATE_ROLES), "date_modes": DATE_MODES,
        "unused": [h for h in headers if h not in used and _filled(data[h])],
        "id_tag": today_tag(),
    }


def _agentbox_card(found):
    df, mapping, info = agentbox.build_frame(found)
    src = found["contact"]
    headers = list(src["headers"]) + info["extra_columns"]
    proposal = {r: mapping.get(r, "") for r in ROLES}
    how = {col: "agentbox" for col in mapping.values()}
    card = _card(src, df, headers, proposal, how, "agentbox")
    if proposal.get("notes") == agentbox.NOTES_COL:          # 'comments' is used, decoded
        card["unused"] = [u for u in card["unused"] if u.strip().lower() != "comments"]
    card["linked"] = [{"kind": k, "expected": name, "file": info["files"][k]} for k, name in agentbox.FILES.items()]
    card["link_issues"] = [{"severity": s, "detail": d} for s, d in agentbox.link_notes(info)]
    return card


def review_cards(session, profile="auto"):
    """Mapping-review cards. profile: 'agentbox' | 'general' | 'auto' (Agentbox when contact.csv is there).
    -> (cards, ingest issues, notes for the user)"""
    paths = session.file_paths()
    if not paths:
        return [], [], []
    c = _ingest(paths)
    found = agentbox.find_sources(c.sources)
    notes = []
    if profile == "auto":
        profile = "agentbox" if "contact" in found else "general"
        if profile == "agentbox":
            notes.append("These are **Agentbox** exports, so I'm using the Agentbox handler. Say **general "
                         "cleaning** if you want the plain General handler instead.")
    if profile == "agentbox":
        if "contact" not in found:
            notes.append("I can't find Agentbox's **contact.csv** (columns contact_id, first_name, last_name, "
                         "email / mobile / street_address ...). Upload it - with contact_category.csv, "
                         "contact_x_agent.csv and agent.csv - or say **general cleaning** for any other file.")
            return [], c.issues, notes
        return [_agentbox_card(found)], c.issues, notes

    cards, skipped = [], []
    for src in c.sources:
        if agentbox.kind_of(src["headers"]) in SATELLITES:
            skipped.append(src["file"])
            continue
        proposal, role_how = _general_proposal(src)
        how = {d["source_column"]: d["method"] for d in src["decisions"]}
        cards.append(_card(src, src["data"], src["headers"], proposal, how, "general", role_how))
    if skipped:
        notes.append(f"Skipped {', '.join(sorted(set(skipped)))} - Agentbox lookup files, not contact lists "
                     f"(the Agentbox handler uses them).")
    return cards, c.issues, notes


def _general_proposal(src):
    """General role -> column for one sheet: learned > taught > guide > matcher > similar."""
    field_to_column, column_method = {}, {}
    for d in src["decisions"]:
        column_method[d["source_column"]] = d["method"]
        if d["target"] and d["target"] not in field_to_column:
            field_to_column[d["target"]] = d["source_column"]
    learned = RoleMemory().lookup_many(src["headers"])
    return propose(src["headers"], field_to_column, learned, guide_hints(), column_method)


def describe(card):
    """Plain-English note that goes with a review card."""
    m = card["mapping"]
    mapped = [r for r, col in m.items() if col]
    missing_core = [ROLES[r][0] for r in ("id", "first", "last", "email", "mobile", "street") if not m.get(r)]
    where = f"**{card['file']}**" + (f" / {card['sheet']}" if card["sheet"] not in ("csv",) else "")
    if card.get("profile") == "agentbox":
        lines = [f"**Agentbox contacts** - {where} ({card['rows']:,} contacts). I've mapped **{len(mapped)}** "
                 f"fields from Agentbox's fixed column names."]
        have = [x["file"] for x in card["linked"] if x["file"] and x["kind"] != "contact"]
        if have:
            lines.append("Linked: " + ", ".join(f"**{f}**" for f in have)
                         + " - for Contact Type / Tags, Team member 1 (primary owner first). Comments are "
                           "Base64-decoded into CONTACT_NOTES.")
        lines += [f"{'⚠ ' if i['severity'] == 'WARN' else ''}{i['detail']}" for i in card["link_issues"]]
    else:
        lines = [f"Here's how I've mapped {where} ({card['rows']:,} rows) - **{len(mapped)}** of "
                 f"{len(card['columns'])} columns are in use."]
    if card["headerless"]:
        lines.append("This file has **no header row**, so the columns are called Column_1, Column_2 ... "
                     "check the sample values carefully.")
    if missing_core:
        lines.append("Not found: " + ", ".join(missing_core) + ". Pick them from the dropdowns if the "
                     "file has them.")
    if not m.get("id"):
        lines.append("With no Contact ID the rows are numbered 1, 2, 3 ... in file order.")
    if card.get("ai_filled"):
        lines.append(f"The AI suggested **{card['ai_filled']}** more column(s) from their sample values - "
                     f"they're marked **AI** on the card. Check those before you Run.")
    learned = sum(1 for g in card["groups"] for r in g["roles"] if r["method"] == "learned")
    if learned:
        lines.append(f"{learned} column(s) came from what you confirmed on an earlier run (marked LEARNED).")
    if card["unused"]:
        shown = ", ".join(card["unused"][:12]) + (" ..." if len(card["unused"]) > 12 else "")
        lines.append(f"Columns with data I haven't used: {shown}.")
    lines.append(f"Change anything that's wrong, set the ID date tag, then press **Run {card['title']}**. "
                 "Nothing is written until you do.")
    return "\n\n".join(lines)


def _source_for(session, file, sheet, profile):
    """-> (frame, headers, proposal, src, info) for the file/sheet being run."""
    if profile == "agentbox":
        c = _ingest(session.file_paths())
        found = agentbox.find_sources(c.sources)
        if "contact" not in found or found["contact"]["file"] != os.path.basename(file):
            raise ValueError(f"'{file}' isn't an Agentbox contact.csv in this session any more - upload it again.")
        df, mapping, info = agentbox.build_frame(found)
        headers = list(found["contact"]["headers"]) + info["extra_columns"]
        return df, headers, mapping, found["contact"], info
    path = next((p for p in session.file_paths() if os.path.basename(p) == os.path.basename(file)), None)
    if not path:
        raise ValueError(f"'{file}' isn't in this session any more - upload it again.")
    c = _ingest([path])
    src = next((s for s in c.sources if s["sheet"] == sheet), None)
    if src is None:
        raise ValueError(f"Sheet '{sheet}' wasn't found in '{file}'.")
    return src["data"], src["headers"], _general_proposal(src)[0], src, None


def run(session, file, sheet, mapping, id_tag="", remember=False, status=lambda _m: None, profile="general",
        flag_modes=None, date_modes=None):
    """Run General / Agentbox cleaning with a confirmed mapping. Returns a dict for the result card."""
    t0 = time.time()
    profile = profile if profile in PROFILE_NAMES else "general"
    status(f"Reading {os.path.basename(file)}...")
    data, headers, proposed, src, info = _source_for(session, file, sheet, profile)
    mapping = {r: (col or "") for r, col in (mapping or {}).items() if r in ROLES}
    bad = sorted({col for col in mapping.values() if col and col not in headers})
    if bad:
        raise ValueError("These columns aren't in the file: " + ", ".join(bad))
    taken = [col for col in mapping.values() if col]
    clash = sorted({col for col in taken if taken.count(col) > 1})
    if clash:
        raise ValueError("Each column can only be used once - mapped more than once: " + ", ".join(clash))
    id_tag = re.sub(r"[^0-9A-Za-z]", "", id_tag or "")

    learned = 0
    if remember and profile == "general":
        roles = RoleMemory()                  # every confirmed choice: the next export maps itself
        for role, col in mapping.items():
            if col:
                roles.remember(col, role)
    if remember:
        memory = MappingMemory()
        derived = set(info["extra_columns"]) if info else set()
        for role, col in mapping.items():
            field = ROLES[role][1]
            if col and field and col != proposed.get(role) and not is_placeholder(col) and col not in derived:
                learned += bool(memory.remember(col, field, source="user"))

    lib = library()
    splitter = AddressSplitter(lib.suburbs, lib.spelling) if lib.suburbs else None
    status(f"Cleaning {len(data):,} rows - names, phones, emails, addresses...")
    # permissions: what the user set on the card, else DARIS's guess from the header; dates: date only unless set
    modes = suggested_modes(data, mapping)
    modes.update({r: clean_flag_mode(m, r) for r, m in (flag_modes or {}).items() if r in FLAG_ROLES and mapping.get(r)})
    dmodes = {r: m for r, m in (date_modes or {}).items() if r in DATE_ROLES and m in DATE_MODES}
    result = GeneralCleaner(mapping, id_tag=id_tag, address_splitter=splitter, flag_modes=modes, date_modes=dmodes).run(data)
    if info:
        result["stats"].update({f"Agentbox: {k.replace('_', ' ')}": v for k, v in info["counts"].items()})
        result["stats"].update({f"Agentbox file: {k}.csv": f or "(not uploaded)" for k, f in info["files"].items()})
        result["issues"] = agentbox.link_notes(info) + result["issues"]
    prefix = "Agentbox" if profile == "agentbox" else "General"
    stem = re.sub(r"[^0-9A-Za-z_-]+", "_", os.path.splitext(os.path.basename(src["file"]))[0]).strip("_")[:40]
    out_name = f"DARIS_{prefix}_{stem}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    status("Writing the Summary workbook...")
    write_general_workbook(result, mapping, os.path.join(session.out_dir, out_name), id_tag)

    area = PROFILE_NAMES[profile]
    issues = [{"severity": s, "area": area, "detail": d} for s, d in result["issues"]]
    sev = {"FAIL": 0, "WARN": 1, "INFO": 2}
    issues.sort(key=lambda i: sev.get(i["severity"], 3))
    overall = next((s for s in ("FAIL", "WARN") if any(i["severity"] == s for i in issues)), "PASS")
    run_state = {
        "profile": profile, "title": area,
        "file": src["file"], "sheet": sheet, "id_tag": id_tag, "output_file": out_name,
        "generated": datetime.now().strftime("%d/%m/%Y %H:%M"), "seconds": round(time.time() - t0, 1),
        "overall": overall, "stats": result["stats"], "issues": issues, "learned": learned,
        "mapping": {r: col for r, col in mapping.items() if col},
        "settings": result.get("settings", {}),
    }
    session.state["last_general"] = run_state
    session.save()
    return run_state


def result_card(session, run_state):
    return {"kind": "general_result", "profile": run_state.get("profile", "general"),
            "title": run_state.get("title", PROFILE_NAMES["general"]),
            **{k: run_state[k] for k in (
                "file", "sheet", "id_tag", "output_file", "overall", "stats", "issues", "seconds", "learned")},
            "download": f"/api/daris/session/{session.sid}/download/{run_state['output_file']}"}


def summary_text(run_state):
    s = run_state["stats"]
    parts = [f"Done - **{s['total_rows']:,}** Summary rows from {s['source_rows']:,} source rows: "
             f"{s['main_rows']:,} main contacts, then **{s['split_rows']:,}** split people at the bottom "
             f"(from {s['records_split']:,} records with more than one name)."]
    parts.append(f"Phones: {s['phone_values_in']:,} values in, "
                 + ("every one accounted for." if s["phones_reconciled"] else "**NOT all accounted for - check Issues.**"))
    if s["Invalid numbers (W:Z)"]:
        parts.append(f"{s['Invalid numbers (W:Z)']:,} invalid number(s) went to the Invalid columns and CONTACT_NOTES.")
    if s["addresses_split"]:
        parts.append(f"{s['addresses_split']:,} address(es) had the suburb split out of line 1.")
    if run_state.get("profile") == "agentbox":
        a = {k.split(": ", 1)[1]: v for k, v in s.items() if str(k).startswith("Agentbox: ")}
        bits = [f"{a[k]:,} {label}" for k, label in (("with type", "with a Contact Type"),
                                                      ("with tags", "with tags"),
                                                      ("with team member", "with Team member 1"),
                                                      ("comments decoded", "Base64 comments decoded"))
                if k in a]
        if bits:
            parts.append("Agentbox: " + ", ".join(bits) + ".")
    if run_state["learned"]:
        parts.append(f"I remembered {run_state['learned']} column change(s) for future files.")
    return " ".join(parts)


# --------------------------------------------------------------------
# General field names guide (reference/guides/General Field Names.md)
# --------------------------------------------------------------------
GUIDE_NAME = "General Field Names.md"
_guide_cache = {"mtime": None, "hints": {}}


def guide_path():
    return os.path.join(library().root, "guides", GUIDE_NAME)


def guide_hints():
    """{role: [header names]} from the guide's table - re-read whenever the file changes."""
    path = guide_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _guide_cache["mtime"] != mtime:
        hints = {}
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                m = re.match(r"\s*\|\s*`([a-z0-9_]+)`\s*\|[^|]*\|(.*)\|\s*$", line)
                if m and m.group(1) in ROLES:
                    hints[m.group(1)] = [n.strip() for n in re.split(r"·|;", m.group(2)) if n.strip()]
        _guide_cache.update(mtime=mtime, hints=hints)
    return _guide_cache["hints"]


# --------------------------------------------------------------------
# AI fill-in: roles the rules left empty
# --------------------------------------------------------------------
ROLE_HELP = {
    "id": "the record's unique ID / reference number",
    "spouse_id": "the CONTACT ID of this contact's spouse / partner - another row in the same file (not a name)",
    "company": "the business the CONTACT belongs to (Pty Ltd, Trust, firm names) - never a staff member",
    "title": "Mr / Mrs / Ms / Dr ...", "first": "first (given) names", "last": "surnames",
    "title2": "the SECOND person's title", "first2": "the SECOND person's first name",
    "last2": "the SECOND person's surname", "email": "the main email address",
    "mobile": "the main mobile number", "home": "a home / after-hours phone", "work": "a work / business phone",
    "fax": "a fax number", "mobile2": "the SECOND person's mobile", "email2": "the SECOND person's email",
    "street": "street address (number + street)", "street2": "a second address line",
    "suburb": "suburb / town", "state": "state (QLD, NSW ...)", "postcode": "4-digit postcode",
    "country": "country", "salutation": "how letters are addressed ('Dear John')",
    "legal": "full legal name", "position": "job title / position", "type": "contact type / category",
    "tags": "tags / labels / groups", "source": "how the lead came in (enquiry source)",
    "notes": "free-text notes / comments", "team1": "the agency's agent / rep / consultant (a staff person's name, repeated across many contacts) who looks after the contact",
    "created": "date the record was created", "modified": "date it was last changed",
    "do_not_sms": "SMS / text permission: an allow flag, a do-not flag or an opt-out date",
    "do_not_email": "email permission: an allow flag, a do-not flag or an opt-out date",
    "do_not_mail": "letter / mail permission: an allow flag, a do-not flag or an opt-out date",
    "do_not_call": "phone permission: an allow flag, a do-not flag or an opt-out date",
    "subscribed": "newsletter / marketing: subscribed flag, unsubscribed flag or an unsubscribe date",
    "contactable": "an overall contactable flag (1 / yes = the contact may be contacted)",
    "zenu_id": "an existing Zenu contact ID (only when the export came from Zenu)",
    "subscriptions": "the mailing lists / subscriptions the contact is on",
    "town_city": "town or city when the export has it separate from the suburb",
    "children": "names of the contact's children",
    "rating": "a contact rating / grade (Hot, Warm, A, B, 1-5 ...)",
    "last_contact": "date the contact was last contacted",
    "team2": "another agent / staff member who looks after the contact (team member 2)",
    "team3": "another agent / staff member who looks after the contact (team member 3)",
    "team4": "another agent / staff member who looks after the contact (team member 4)",
    "team5": "another agent / staff member who looks after the contact (team member 5)",
    "team6": "another agent / staff member who looks after the contact (team member 6)",
    "team7": "another agent / staff member who looks after the contact (team member 7)",
    "team8": "another agent / staff member who looks after the contact (team member 8)",
    "team9": "another agent / staff member who looks after the contact (team member 9)",
    "team10": "another agent / staff member who looks after the contact (team member 10)",
    "team11": "another agent / staff member who looks after the contact (team member 11)",
    "team12": "another agent / staff member who looks after the contact (team member 12)",
    "team13": "another agent / staff member who looks after the contact (team member 13)",
    "team14": "another agent / staff member who looks after the contact (team member 14)",
    "team15": "another agent / staff member who looks after the contact (team member 15)",
    "sale_type": "the sale type the contact is interested in (e.g. Private Treaty, Auction)",
    "solicitor_id": "the Zenu ID of the contact's solicitor",
    "company_id": "the Zenu ID of the contact's company",
}
AI_MAX_COLUMNS = 25
_FLAG = re.compile(r"^(true|false|yes|no|y|n|0|1|-1)$", re.I)


def _fits(role, samples):
    """A cheap sanity check on an AI pick, from the sample values."""
    vals = [s for s in samples if s]
    if not vals:
        return False
    digits = lambda v: sum(ch.isdigit() for ch in v)
    if role in ("email", "email2"):
        return all("@" in v for v in vals)
    if role in ("mobile", "home", "work", "fax", "mobile2"):
        return all(digits(v) >= 6 and "@" not in v for v in vals)
    if role == "postcode":
        return all(re.fullmatch(r"\d{3,4}", v.strip()) for v in vals)
    if role in ("created", "modified"):
        return all(digits(v) >= 4 for v in vals)
    if role.startswith("do_not_") or role == "subscribed":
        return all(_FLAG.match(v.strip()) for v in vals)
    if role in ("first", "last", "first2", "last2", "title", "title2", "suburb", "state", "country"):
        return all("@" not in v and digits(v) <= 1 for v in vals)
    if role == "salutation":                  # 'Dear John', 'John & Mary', 'Mr Smith' - never one lowercase word
        return all(digits(v) == 0 and (len(v.split()) >= 2 or v[:1].isupper()) for v in vals)
    if role in ("street", "street2"):
        return any(ch.isalpha() for v in vals for ch in v)
    return True


def ai_fill(card, llm, cache=None):
    """Ask the AI which still-empty role each unused column holds. Picks go on the card marked 'ai'
    (the user confirms them before Run). Returns the number of roles filled."""
    if card.get("profile") != "general" or not llm or not llm.available:
        return 0
    m = card["mapping"]
    empty = [r for r in ROLES if not m.get(r)]
    cols = {c["name"]: c for c in card["columns"]}
    cands = [u for u in card["unused"] if not is_placeholder(u) or card["headerless"]][:AI_MAX_COLUMNS]
    if not empty or not cands:
        return 0
    key = f"{card['file']}|{card['sheet']}|" + "|".join(sorted(cands)) + "|" + ",".join(empty)
    picks = (cache or {}).get(key)
    if picks is None:
        lines = "\n".join(f'- "{c}": {json.dumps(llm.safe_samples(cols[c]["samples"]))}' for c in cands)
        roles = "\n".join(f"- {r}: {ROLE_HELP.get(r, ROLES[r][0])}" for r in empty)
        note = "(Values are shown as patterns: 9 = digit, a/A = letter.)\n" if llm.protect else ""
        prompt = (f"A CRM contact export has these columns that aren't mapped yet (header: sample values):\n"
                  f"{lines}\n{note}\nRoles still empty:\n{roles}\n\n"
                  "For EACH column: first say in a few words what its values are, then the role that "
                  "describes exactly that data, or NONE. Many columns match no role (moods, codes, "
                  "internal flags, ratings, IDs of other things) - answer NONE for those. Use a role at "
                  "most once. 'street' is the main address line; 'street2' only a second line.")
        schema = {"type": "object", "properties": {"picks": {"type": "array", "items": {
            "type": "object", "properties": {"column": {"type": "string", "enum": cands},
                                             "holds": {"type": "string"},
                                             "role": {"type": "string", "enum": empty + ["NONE"]}},
            "required": ["column", "holds", "role"], "additionalProperties": False}}},
            "required": ["picks"], "additionalProperties": False}
        ans = llm.generate(prompt, system="You map Australian real-estate CRM contact exports onto cleaning "
                                          "roles. Answer only with the JSON asked for.",
                           as_json=True, max_tokens=60 * len(cands) + 80, schema=schema)
        picks = {}
        for p in (ans or {}).get("picks", []) if isinstance(ans, dict) else []:
            c, r = str(p.get("column", "")), str(p.get("role", ""))
            if r == "street2" and "street" in empty and "street" not in picks.values():
                r = "street"                  # the only address column is line 1
            if c in cands and r in empty and c not in picks and r not in picks.values():
                picks[c] = r
        if cache is not None and ans is not None:
            cache[key] = picks
    filled = 0
    for c, r in picks.items():
        if not m.get(r) and c in card["unused"] and _fits(r, cols[c]["samples"]):
            m[r] = c
            card["unused"].remove(c)
            for g in card["groups"]:
                for row in g["roles"]:
                    if row["role"] == r:
                        row.update(column=c, method="ai")
            filled += 1
    card["ai_filled"] = filled
    return filled
