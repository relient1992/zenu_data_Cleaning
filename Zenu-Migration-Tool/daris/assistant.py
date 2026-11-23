import difflib
import json
import os
import re
import shutil
import uuid
from collections import Counter
from datetime import datetime

from . import general_flow
from .consolidator import SmartConsolidator
from .knowledge import load_fields
from .llm import get_llm
from .memory import MappingMemory
from .references import library

# =====================================================================
# DARIS CHAT ASSISTANT
# ---------------------------------------------------------------------
# Backs daris.html. A session is a folder holding the uploaded files,
# the outputs and state.json. Each chat message is routed:
#   1. deterministic intents (clean -> mapping review card -> General
#      cleaning, issues, teach, suburb lookup, references ...) -> run the
#      real tool, answer with a card
#   2. anything else -> the AI chosen in Settings answers, grounded ONLY in
#      this session's files, last run results and the reference guides
#      relevant to the question (reference/ folder)
# so actions never depend on a model understanding the request.
# Replies stream as NDJSON events: status / token / text / card / error / done.
# =====================================================================

SID_RE = re.compile(r"^js_[0-9a-f]{10}$")
ALLOWED_EXT = (".xlsx", ".xls", ".csv")
HISTORY_TURNS = 6

TEACH_RE = re.compile(
    r"^\s*(?:please\s+)?(?:map|treat|set|send|put)\s+(?:the\s+)?(?:column\s+)?[\"'`]?(.+?)[\"'`]?\s+"
    r"(?:to|as|into|=|->)\s+(?:the\s+)?(?:field\s+)?[\"'`]?([a-z0-9_]+)[\"'`]?\s*[.!]?\s*$",
    re.IGNORECASE)
IGNORE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:(?:ignore|don'?t map|do not map|never map)\s+(?:the\s+)?(?:column\s+)?"
    r"[\"'`]?(.+?)[\"'`]?|keep\s+(?:the\s+)?(?:column\s+)?[\"'`]?(.+?)[\"'`]?\s+(?:as[- ]is|unmapped))"
    r"\s*[.!]?\s*$", re.IGNORECASE)

INTENTS = [
    ("cleaning", re.compile(r"\b(?:show|view|open)\b.*\bclean\w*", re.I)),
    ("agentbox", re.compile(r"\bagent\s?box\b", re.I)),
    ("general", re.compile(r"\bgeneral\s+(?:clean\w*|handler|contacts?)\b|\bclean\w*\s+(?:it\s+|them\s+|the\s+)?"
                           r"(?:as\s+|with\s+|using\s+)?general\b", re.I)),
    ("clean", re.compile(r"\b(clean\w*|tidy\w*|split (?:the )?names|format (?:the )?(?:phones?|numbers)|"
                         r"(?:split|separate|fix|parse) (?:out )?(?:the )?(?:mailing )?(?:address\w*|suburbs?))\b",
                         re.I)),
    ("merge", re.compile(r"\b(consolidat\w*|merge|combine|reconcil\w*|process|"
                         r"map (?:the|these|my|all) (?:files?|data)|"
                         r"run (?:it|them|daris)|go ahead|do it)\b", re.I)),
    ("duplicates", re.compile(r"\b(dup\w*|same email|same phone|clash\w*|collision\w*)\b", re.I)),
    ("issues", re.compile(r"\b(issues?|problems?|errors?|warnings?|fail\w*)\b", re.I)),
    ("mapping", re.compile(r"\b(mapp\w*|columns?|headers?|fields?|suggestions?)\b", re.I)),
    ("files", re.compile(r"\b(files?|uploaded|profile|analy[sz]\w*|inspect|what(?:'s| is) in)\b", re.I)),
    # last, so "help me clean the contacts" cleans
    ("help", re.compile(r"\b(help|what can you do|commands|how do (?:i|you) use)\b", re.I)),
]
QUESTION_RE = re.compile(r"\?\s*$|^\s*(why|how|what|which|should|can|could|is|are|does|"
                         r"do (?:i|you|we|they|these|the|my)|explain|tell me)\b", re.I)
# "can you split the addresses please?" is a request, not a question
POLITE_COMMAND_RE = re.compile(
    r"^\s*(?:(?:can|could|would|will) you (?:please |also |now )*|please |pls |now |also )"
    r"(?:split|separate|clean|tidy|merge|consolidate|combine|format|fix|parse|run|reconcile|process)\b",
    re.I)

# "postcode of Tooradin", "check suburb Mt Coolum", "is Toradin a real suburb?"
SUBURB_RES = [
    re.compile(r"\b(?:postcode|post code|postal code|state)\s+(?:of|for)\s+(.+?)\s*\??\s*$", re.I),
    re.compile(r"^\s*(?:check|look ?up|find|verify|validate)\s+(?:the\s+)?suburbs?\s+(.+?)\s*\??\s*$", re.I),
    re.compile(r"^\s*is\s+(.+?)\s+an?\s+(?:real\s+|valid\s+|proper\s+)?suburb\s*\??\s*$", re.I),
]
REFERENCE_RE = re.compile(
    r"^\s*(?:show|list|open|view)\b.*\b(?:references?|guides?|library|lookups?|dictionar\w*)\b"
    r"|\bwhat (?:references|guides|lookups) (?:do you have|are loaded)\b", re.I)


def _ev(kind, **kw):
    return {"type": kind, **kw}


def classify(message: str, lookup_suburb=None) -> dict:
    """Decide what a chat message asks for - the single source of truth for routing
    (tests/test_commands.py checks every example in reference/guides/DARIS Commands.md).
    -> {"kind": teach | suburb | reference | help | merge | clean | agentbox | general | card | ask, ...}"""
    is_question = bool(QUESTION_RE.search(message)) and not POLITE_COMMAND_RE.search(message)
    # Every Zenu field contains '_', so "map the files to zenu" is not a teach command
    m = TEACH_RE.match(message)
    if m and "_" in m.group(2) and not QUESTION_RE.search(message):
        return {"kind": "teach", "header": m.group(1).strip(), "target": m.group(2).strip().lower()}
    m = IGNORE_RE.match(message)
    if m and not QUESTION_RE.search(message):
        return {"kind": "teach", "header": (m.group(1) or m.group(2)).strip(), "target": ""}
    for i, rx in enumerate(SUBURB_RES):
        m = rx.search(message)
        if not m:
            continue
        found = lookup_suburb(m.group(1)) if lookup_suburb else {"matches": [], "close": []}
        # "state of the import" isn't a place: the loose pattern needs a real hit
        if i == 0 and not (found["matches"] or found["close"]):
            break
        return {"kind": "suburb", "found": found}
    if REFERENCE_RE.search(message):
        return {"kind": "reference"}
    intent = next((name for name, rx in INTENTS if rx.search(message)), None)
    if intent == "help":
        return {"kind": "help"}
    if intent in ("merge", "clean", "agentbox", "general") and not is_question:
        return {"kind": intent}
    if intent in ("cleaning", "duplicates", "issues", "mapping", "files"):
        return {"kind": "card", "card": intent, "question": is_question}
    return {"kind": "ask"}


class DarisSession:
    def __init__(self, root, sid):
        self.sid = sid
        self.dir = os.path.join(root, sid)
        self.files_dir = os.path.join(self.dir, "files")
        self.out_dir = os.path.join(self.dir, "outputs")
        self.state_path = os.path.join(self.dir, "state.json")
        self.state = {"created": datetime.now().isoformat(timespec="seconds"),
                      "files": [], "last_run": None, "history": []}
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                self.state.update(json.load(f))

    @classmethod
    def create(cls, root):
        sid = f"js_{uuid.uuid4().hex[:10]}"
        s = cls(root, sid)
        os.makedirs(s.files_dir, exist_ok=True)
        os.makedirs(s.out_dir, exist_ok=True)
        s.save()
        return s

    @classmethod
    def load(cls, root, sid):
        if not sid or not SID_RE.match(sid) or not os.path.isdir(os.path.join(root, sid)):
            return None
        return cls(root, sid)

    def save(self):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1, default=str)

    def public_state(self):
        run = self.state.get("last_general")
        return {"session_id": self.sid,
                "files": [{k: v for k, v in p.items() if k != "mapping"} for p in self.state["files"]],
                "last_run": {"overall": run["overall"], "output_file": run["output_file"],
                             "rows_out": run["stats"]["total_rows"], "generated": run["generated"]}
                if run else None}

    def file_paths(self):
        return [os.path.join(self.files_dir, p["name"]) for p in self.state["files"]
                if not p.get("error") and os.path.exists(os.path.join(self.files_dir, p["name"]))]

    # -----------------------------------------------------------------
    def add_files(self, uploads):
        """uploads: list of (filename, fileobj). Returns the new profiles."""
        added = []
        for fname, fobj in uploads:
            name = os.path.basename(str(fname).replace("\\", "/"))
            if not name.lower().endswith(ALLOWED_EXT):
                added.append({"name": name, "error": "Unsupported type - use .xlsx, .xls or .csv."})
                continue
            dest = os.path.join(self.files_dir, name)
            with open(dest, "wb") as out:
                shutil.copyfileobj(fobj, out)
            prof = profile_file(dest)
            self.state["files"] = [p for p in self.state["files"] if p["name"] != name] + [prof]
            added.append(prof)
        self.save()
        return added

    def remove_file(self, name):
        name = os.path.basename(name)
        self.state["files"] = [p for p in self.state["files"] if p["name"] != name]
        path = os.path.join(self.files_dir, name)
        if os.path.exists(path):
            os.remove(path)
        self.save()

    def output_path(self, fname):
        path = os.path.join(self.out_dir, os.path.basename(fname))
        return path if os.path.exists(path) else None


def profile_file(path):
    """Fast look at one file: sheets, rows, header row, how many columns map to Zenu.
    Rules + memory only (no LLM) so an upload answers in seconds."""
    name = os.path.basename(path)
    c = SmartConsolidator(use_llm=False, log=lambda _m: None)
    try:
        c.ingest([path])
    except Exception as e:
        return {"name": name, "error": str(e)}
    if not c.sources:
        detail = "; ".join(i["detail"] for i in c.issues) or "No data rows found."
        return {"name": name, "error": detail}
    c.map_columns()
    sheets = []
    for src in c.sources:
        decs = src["decisions"]
        sheets.append({
            "sheet": src["sheet"], "header_row": src["header_row"], "rows": len(src["data"]),
            "columns": len(decs), "mapped": sum(1 for d in decs if d["target"]),
            "types": dict(Counter(d["type"] for d in decs if d["type"] not in ("text", "empty"))),
            "review": [d["source_column"] for d in decs if not d["target"] and d["suggestion"]][:12],
        })
    return {"name": name, "size_kb": round(os.path.getsize(path) / 1024),
            "rows": sum(s["rows"] for s in sheets), "sheets": sheets, "mapping": c.mapping_rows}


# =====================================================================
class DarisAssistant:
    def __init__(self, session: DarisSession):
        self.s = session
        self.llm = get_llm()
        self.lib = library()
        self.fields = {f["name"]: f for f in load_fields()}

    def handle(self, message: str):
        message = (message or "").strip()
        if not message:
            yield _ev("done")
            return
        try:
            yield from self._route(message)
        except Exception as e:
            yield _ev("error", text=f"Something went wrong: {e}")
        yield _ev("done")

    def _remember(self, role, text):
        hist = self.s.state.setdefault("history", [])
        hist.append({"role": role, "content": text[:1500]})
        self.s.state["history"] = hist[-HISTORY_TURNS * 2:]
        self.s.save()

    # -----------------------------------------------------------------
    def _route(self, message):
        self._remember("user", message)
        r = classify(message, lambda text: self.lib.lookup_suburb(text))
        kind = r["kind"]
        if kind == "teach":
            yield from self._teach(r["header"], r["target"])
        elif kind == "suburb":
            yield from self._suburb(r["found"])
        elif kind == "reference":
            yield _ev("card", card={"kind": "reference", **self._reference_summary()})
            yield from self._reply(CARD_NOTES["reference"])
        elif kind == "help":
            yield from self._reply(HELP_TEXT)
        elif kind == "merge" and MERGE_WORDS.search(message):
            yield from self._reply(MERGE_OFF)
            yield from self._general_review()
        elif kind == "agentbox":
            yield from self._general_review("agentbox")
        elif kind == "general":
            yield from self._general_review("general")
        elif kind in ("merge", "clean") or (kind == "card" and r["card"] == "mapping" and not r["question"]):
            yield from self._general_review()
        elif kind == "card" and r["card"] == "duplicates" and not r["question"]:
            yield from self._reply(DUPLICATES_OFF)
        elif kind == "card":
            card = getattr(self, f"_card_{r['card']}", lambda: None)()
            if card:
                yield _ev("card", card=card)
            if r["question"] or not card:
                yield from self._ask(message)
            else:
                yield from self._reply(CARD_NOTES[r["card"]])
        else:
            yield from self._ask(message)

    def _reply(self, text):
        self._remember("assistant", text)
        yield _ev("text", text=text)

    # -----------------------------------------------------------------
    # TOOLS
    # -----------------------------------------------------------------
    def _general_review(self, profile="auto"):
        """Propose the column mapping and wait - the user confirms it on the card.
        profile: 'agentbox' | 'general' | 'auto' (Agentbox when its contact.csv is uploaded)."""
        if not self.s.file_paths():
            yield from self._reply("There's nothing to clean yet - drop the client's Excel or CSV export into "
                                   "the chat (or use the paperclip), then ask me to clean the contacts.")
            return
        yield _ev("status", text="Reading the columns and proposing a mapping...")
        cards, issues, notes = general_flow.review_cards(self.s, profile)
        if not cards:
            if notes:
                yield from self._reply("\n\n".join(notes))
                return
            detail = "; ".join(i["detail"] for i in issues) or "no data rows were found"
            yield _ev("error", text=f"I couldn't read the files: {detail}")
            return
        # The AI fills roles the rules couldn't (General only - Agentbox headers are fixed)
        todo = [c for c in cards if c["profile"] == "general" and c["unused"]
                and any(not v for v in c["mapping"].values())]
        if todo and self.llm.available:
            cache = self.s.state.setdefault("ai_roles", {})
            for card in todo:
                yield _ev("status", text=f"{self.llm.label} is reading {len(card['unused'])} unmapped "
                                         f"column(s) in {card['file']}...")
                general_flow.ai_fill(card, self.llm, cache)
            self.s.save()
        for card in cards:
            yield _ev("card", card=card)
        text = "\n\n---\n\n".join(general_flow.describe(c) for c in cards)
        if notes:
            text = "\n\n".join(notes) + "\n\n" + text
        if len(cards) > 1:
            text = (f"General cleaning works one sheet at a time - you have **{len(cards)}**, so each has "
                    "its own card and its own output workbook.\n\n") + text
        yield from self._reply(text)

    def run_general(self, file, sheet, mapping, id_tag="", remember=False, profile="general", flag_modes=None, date_modes=None):
        """The review card's Run button (POST /api/daris/session/<id>/general-clean)."""
        name = general_flow.PROFILE_NAMES.get(profile, "General cleaning")
        try:
            yield _ev("status", text=f"{name}: {file} - names, phones, emails, addresses...")
            run_state = general_flow.run(self.s, file, sheet, mapping, id_tag, remember, profile=profile,
                                         flag_modes=flag_modes, date_modes=date_modes)
            self._remember("user", f"[Ran {name} on {file} / {sheet}]")
            yield _ev("card", card=general_flow.result_card(self.s, run_state))
            yield from self._reply(general_flow.summary_text(run_state))
        except Exception as e:
            yield _ev("error", text=f"{name} failed: {e}")
        yield _ev("done")

    def _suburb(self, r):
        if not r["loaded"]:
            yield from self._reply("I don't have a suburb list yet - add one (columns suburb, state, "
                                   "postcode) to `reference/lookups/` and ask again.")
            return
        yield _ev("card", card={"kind": "suburb", **r})
        if r["matches"]:
            places = ", ".join(f"{m['suburb']} {m['state']} {m['postcode']}" for m in r["matches"][:6])
            fix = f"Your spelling list corrects it to **{r['spelling_fix']}**. " if r["spelling_fix"] else ""
            many = " It exists in more than one place - use the state or postcode to pick." \
                if len({(m['state']) for m in r["matches"]}) > 1 else ""
            yield from self._reply(f"{fix}Found: {places}.{many}")
        elif r["close"]:
            yield from self._reply(f"**{r['name']}** isn't in the suburb list. Did you mean: "
                                   + ", ".join(f"**{c}**" for c in r["close"]) + "?")
        else:
            yield from self._reply(f"**{r['name']}** isn't in the suburb list and nothing close matches.")

    def _reference_summary(self):
        s = self.lib.summary()
        return {k: v for k, v in s.items() if k != "root"}

    def _teach(self, header, target):
        if target and target not in self.fields:
            close = difflib.get_close_matches(target, list(self.fields), n=4, cutoff=0.5)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            yield from self._reply(f"I don't know a Zenu field called **{target}**.{hint}")
            return
        if not MappingMemory().remember(header, target, source="user"):
            yield from self._reply(PLACEHOLDER_NOTE.format(header=header))
            return
        if target:
            text = (f"Learned: **{header}** → `{target}`. I'll use this for every future file. "
                    "Ask me to clean the contacts again to apply it.")
        else:
            text = (f"Learned: **{header}** will always be kept under its original name. "
                    "Ask me to clean the contacts again to apply it.")
        yield from self._reply(text)

    # -----------------------------------------------------------------
    # CARDS (structured results rendered by daris.html)
    # -----------------------------------------------------------------
    def _card_cleaning(self):
        run = self.s.state.get("last_general")
        return general_flow.result_card(self.s, run) if run else None

    def _card_issues(self):
        run = self.s.state.get("last_general")
        return {"kind": "issues", "issues": run["issues"]} if run else None

    def _card_files(self):
        if not self.s.state["files"]:
            return None
        return {"kind": "files",
                "files": [{k: v for k, v in p.items() if k != "mapping"} for p in self.s.state["files"]]}

    # -----------------------------------------------------------------
    # GROUNDED Q&A
    # -----------------------------------------------------------------
    def _references_for(self, question):
        budget = 7000 if self.llm.is_cloud else 2500     # the local model has a 4K context
        hits = self.lib.search(question, budget=budget)
        if not hits:
            return ""
        return "\n\n".join(f"[{h['source']} - {h['heading']}]\n{h['text']}" for h in hits)

    def _context(self):
        lines = []
        for p in self.s.state["files"]:
            if p.get("error"):
                lines.append(f"- File {p['name']}: could not be read ({p['error']})")
                continue
            for sh in p["sheets"]:
                header = (f"header on row {sh['header_row']}" if sh["header_row"]
                          else "NO header row (first row is data; columns mapped from their values)")
                lines.append(f"- File {p['name']} / sheet {sh['sheet']}: {sh['rows']} rows, "
                             f"{sh['columns']} columns ({sh['mapped']} match Zenu fields), {header}; "
                             f"needs review: {', '.join(sh['review']) or 'none'}")
        if not lines:
            lines.append("- No files uploaded yet.")
        run = self.s.state.get("last_general")
        if run:
            lines.append(f"\nLast {run.get('title', 'General cleaning')} ({run['generated']}) of {run['file']} / {run['sheet']} "
                         f"-> {run['output_file']} (ID date tag {run['id_tag'] or 'none'}), overall {run['overall']}:")
            lines.append(", ".join(f"{k}={v}" for k, v in run["stats"].items()))
            lines.append("Mapping used: " + ", ".join(f"{general_flow.ROLES[r][0]}={c}"
                                                     for r, c in run["mapping"].items()))
            for i in run["issues"][:10]:
                lines.append(f"- {i['severity']}: {i['detail']}")
        else:
            lines.append("\nGeneral cleaning hasn't been run yet in this session.")
        s = self.lib.summary()
        lines.append(f"\nReference library: {s['suburbs']:,} AU suburbs, {s['spelling_fixes']} suburb "
                     f"spelling fixes, {s['dictionary_fields']} Zenu fields, guides: "
                     + ", ".join(g.split('/')[-1] for g in s["guides"]))
        return "\n".join(lines)

    def _ask(self, message):
        if not self.llm.available:
            reason = (self.llm.last_error if self.llm.is_cloud
                      else "My local AI model isn't running right now")
            yield from self._reply(f"{reason} - so I can only follow commands.\n\n" + HELP_TEXT)
            return
        references = self._references_for(message)
        system = (
            "You are DARIS (Daryl's Automated Reasoning & Virtual Intelligence System), a "
            "data-migration assistant for Zenu (an Australian real-estate CRM). "
            "You clean client contact exports (Excel/CSV) before import with Daryl's cleaning rules. "
            "There are two handlers: Agentbox (Agentbox's contact.csv plus contact_category.csv, "
            "contact_x_agent.csv and agent.csv for Contact Type / Tags / Team member 1, and Base64 "
            "comments decoded into notes) and General (any other CRM export, columns mapped by the user). "
            "Answer ONLY from the SESSION CONTEXT and REFERENCE EXCERPTS below; if the answer isn't "
            "there, say you don't know. When a reference excerpt answers the question, follow it "
            "exactly and name the guide in brackets. The 'Zenu dictionary' excerpts say what Zenu "
            "ACCEPTS on import; the cleaning guides say what the macros PRODUCE. If they disagree, "
            "never merge them - state both and say they conflict.\n"
            "You cannot change data yourself. DARIS's tools - the ONLY actions available - are: "
            "'clean agentbox contacts'; 'general cleaning'; "
            "'clean the contacts' (picks Agentbox when contact.csv is uploaded, else General; both show "
            "the column mapping for the user to check and edit, then on Run "
            "splits names into people - extra people become rows at the bottom of the Summary - formats "
            "and routes phones, sorts emails, splits addresses into street/suburb/state/postcode); "
            "'show mapping'; 'show issues'; 'show cleaning results'; 'postcode of <suburb>'; "
            "'map <column> to <zenu_field>'; 'ignore <column>'. Merging files and duplicate scanning "
            "are switched off. If the user asks for something these "
            "tools don't do, say plainly that DARIS can't do that yet - never tell them to do it "
            "manually and never write code or scripts. If a tool does it, give the exact phrase to type.\n"
            "Be concise: at most 6 short sentences or bullets.\n\n"
            f"SESSION CONTEXT:\n{self.llm.safe_text(self._context())}"
            + (f"\n\nREFERENCE EXCERPTS:\n{references}" if references else "")
        )
        history = self.s.state.get("history", [])[-HISTORY_TURNS * 2:]
        yield _ev("status", text=f"{self.llm.label} is thinking..."
                  + (" (reading your guides)" if references else ""))
        text = []
        for piece in self.llm.stream(system=system, messages=history, max_tokens=400):
            text.append(piece)
            yield _ev("token", text=piece)
        if not text:
            yield _ev("error", text=self.llm.last_error or f"{self.llm.label} didn't answer.")
            return
        self._remember("assistant", "".join(text))


PLACEHOLDER_NOTE = ("**{header}** is a placeholder I made up because that file has no header row, so I "
                    "can't remember it for future files - every headerless file has a 'Column_1'. "
                    "Add a header row to the file and I'll map and learn it properly.")

MERGE_WORDS = re.compile(r"\b(consolidat\w*|merge|combine|reconcil\w*)\b", re.I)
MERGE_OFF = ("Smart merge & reconcile is switched off in DARIS - we're focusing on contact cleaning "
             "(Agentbox or General). (The classic tool's Consolidate button still merges files.)")
DUPLICATES_OFF = ("Duplicate scanning came with Smart merge, which is switched off. General cleaning does "
                  "check that every Contact ID is unique - any clash is a FAIL under **show issues**.")

CARD_NOTES = {
    "issues": "Fix every **FAIL** before import. WARN items are worth a look; INFO is just context.",
    "files": "Say **clean agentbox contacts** for Agentbox exports or **general cleaning** for anything "
             "else - I'll show you the column mapping to check before anything runs.",
    "cleaning": "The **Summary** tab is the import sheet: every main contact first, then the split people "
                "at the bottom. **Name SPlitting**, **Contact Details** and **Address Cleaning** show each "
                "step; **Checks** and **Issues** are the sign-off.",
    "reference": "This is everything in your `reference` folder. Add or edit a file there and I'll pick "
                 "it up on the next message - no restart needed.",
}

HELP_TEXT = """Here's what I can do:

- **Drop files** (.xlsx, .xls, .csv) - I profile each sheet: rows, real header row, how many columns I recognise.
- **"clean agentbox contacts"** - the **Agentbox** handler. Drop Agentbox's `contact.csv` (plus `contact_category.csv`, `contact_x_agent.csv`, `agent.csv`): columns are mapped by Agentbox's own names, Contact Type / Tags come from the categories, Team member 1 from the agents (primary owner first), and `[base64]` comments are decoded into the notes.
- **"general cleaning"** - the **General** handler for any other CRM export: I propose which column is which and you correct it.
- **"clean the contacts"** - picks Agentbox when `contact.csv` is uploaded, otherwise General.
- Both show you **the column mapping first** - change any dropdown, set the ID date tag, then press **Run**. The workbook's **Summary** has every main contact first and the split people at the bottom (`1_c1` Daryl, `1_c2` Kim), with phones formatted and routed (mobile / home / work / fax / invalid), emails checked and addresses split into street / suburb / state / postcode.
- **"show mapping"** - the mapping review again.
- **"show cleaning results"** / **"show issues"** - the last run's numbers and its FAIL / WARN / INFO list.
- **"map Cell Ph to contact_mobile"** / **"ignore Fav Colour"** - teach me; I remember it for every future client (ticking *Remember my changes* on the card does the same).
- **"postcode of Tooradin"** / **"is Toradin a real suburb?"** - checked against your AU suburb list and spelling fixes.
- **"show references"** - the guides, dictionary and lookups I've loaded from your `reference` folder.
- Or just **ask** - e.g. "how do we split couples?" or "is it safe to import?" - I answer from your guides and this session.
- **Settings** (gear icon) - switch between the local AI and Claude / ChatGPT.

Full list with every phrase I understand: `reference/guides/DARIS Commands.md` (or ask me "what command do I use to ...?").
"""
