import difflib
import importlib.util
import math
import os
import re
import warnings
from collections import Counter, defaultdict

import pandas as pd

from .knowledge import PROJECT_DIR

# =====================================================================
# REFERENCE LIBRARY  (Zenu-Migration-Tool/reference/)
# ---------------------------------------------------------------------
# Knowledge Daryl controls, re-read automatically when a file changes:
#   lookups/  - tables used exactly: AU suburbs (suburb/state/postcode),
#               spelling fixes ("Mt Coolum" -> "Mount Coolum")
#   guides/   - the Zenu field dictionary (.xlsx with 'Column Name' /
#               'Accepted Values') and written guides (.md / .txt, plus
#               .docx / .pdf when python-docx / pypdf are installed)
#   past_projects/ - before/after pairs (listed, not learned from yet)
# Guides are split into sections; search() hands the AI only the sections
# relevant to a question, so even the small local model stays within context.
# =====================================================================

REF_DIR = os.path.join(PROJECT_DIR, "reference")
STATE_CODES = {"NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"}
STOPWORDS = set("""a an and are as at be by can do does for from has have how i if in into is it its
me my of on or our should so that the their them then there these they this to us was we what when
where which who why will with would you your about please tell show give""".split())
MACRO_WORKBOOKS = (".xlsb", ".xlsm")
OPTIONAL_READERS = {".docx": ("docx", "python-docx"), ".pdf": ("pypdf", "pypdf"),
                    ".xls": ("xlrd", "xlrd"), ".xlsb": ("pyxlsb", "pyxlsb")}
SECTION_LIMIT = 1800


def _norm_place(s) -> str:
    s = re.sub(r"[^A-Z0-9 '\-]", " ", str(s).upper())
    return re.sub(r"\s+", " ", s).strip()


def _postcode(v) -> str:
    p = str(v).strip()
    if p.endswith(".0"):
        p = p[:-2]
    return p.zfill(4) if p.isdigit() else p


def _stem(t: str) -> str:
    """Crude English stemming so 'split' finds 'splitting' and 'names' finds 'name'."""
    for suffix in ("ing", "ed", "es", "s"):
        if t.endswith(suffix) and len(t) - len(suffix) >= 3:
            t = t[:-len(suffix)]
            if len(t) > 3 and t[-1] == t[-2]:          # splitt -> split
                t = t[:-1]
            break
    return t


def _tokens(text):
    return [_stem(t) for t in re.findall(r"[a-z0-9_]+", str(text).lower())
            if len(t) > 1 and t not in STOPWORDS]


def _cell(v) -> str:
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip()


class ReferenceLibrary:
    def __init__(self, root: str = REF_DIR):
        self.root = root
        self._signature = None
        self.refresh()

    # -----------------------------------------------------------------
    def _scan(self):
        found = []
        if os.path.isdir(self.root):
            for folder, _dirs, files in os.walk(self.root):
                for f in sorted(files):
                    if f.startswith("~$"):          # Excel lock files
                        continue
                    p = os.path.join(folder, f)
                    st = os.stat(p)
                    found.append((p, st.st_mtime, st.st_size))
        return tuple(found)

    def refresh(self):
        sig = self._scan()
        if sig != self._signature:
            self._signature = sig
            self._load([p for p, _m, _s in sig])

    def _load(self, paths):
        self.files = []
        self.suburbs = defaultdict(list)      # NORMALISED NAME -> [(Suburb, STATE, postcode)]
        self.spelling = {}                    # NORMALISED WRONG -> corrected
        self.dictionary = {}                  # zenu field -> {type, desc, accepted, examples}
        self.sections = []                    # {source, heading, text, tokens}
        for path in paths:
            rel = os.path.relpath(path, self.root).replace("\\", "/")
            ext = os.path.splitext(path)[1].lower()
            entry = {"file": rel, "status": "loaded", "detail": ""}
            try:
                if rel.split("/")[0] == "past_projects":
                    entry.update(status="listed", detail="before/after learning not built yet")
                elif ext in MACRO_WORKBOOKS:
                    # A macro workbook is a tool full of client rows, not a guide: its rules are
                    # ported into DARIS (general_cleaning.py), its data never enters the AI context
                    entry.update(status="listed", detail="macro workbook - rules built into DARIS, data not read")
                elif ext in (".md", ".txt"):
                    entry["detail"] = self._load_text(rel, self._read_text(path))
                elif ext in OPTIONAL_READERS and not importlib.util.find_spec(OPTIONAL_READERS[ext][0]):
                    entry.update(status="skipped",
                                 detail=f"run: pip install {OPTIONAL_READERS[ext][1]}")
                elif ext == ".docx":
                    import docx
                    text = "\n".join(p.text for p in docx.Document(path).paragraphs)
                    entry["detail"] = self._load_text(rel, text)
                elif ext == ".pdf":
                    import pypdf
                    text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(path).pages)
                    entry["detail"] = self._load_text(rel, text)
                elif ext in (".xlsx", ".xlsm", ".xls", ".xlsb", ".csv"):
                    entry["detail"] = self._load_workbook(rel, path)
                else:
                    entry.update(status="skipped", detail="unsupported file type")
            except Exception as e:                  # one bad file never breaks the library
                entry.update(status="error", detail=str(e)[:160])
            self.files.append(entry)
        self._index()

    @staticmethod
    def _read_text(path):
        for enc in ("utf-8-sig", "cp1252"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        return ""

    # ----- written guides ---------------------------------------------
    def _load_text(self, source, text):
        heading, buf, count = os.path.splitext(os.path.basename(source))[0], [], 0
        title = heading

        def flush():
            nonlocal count
            body = "\n".join(buf).strip()
            if body:
                self._add_section(source, heading, body)
                count += 1

        for line in text.splitlines():
            m = re.match(r"^\s{0,3}#{1,6}\s+(.*)", line)
            if m:
                flush()
                buf = []
                heading = f"{title} > {m.group(1).strip()}"
            else:
                buf.append(line)
        flush()
        return f"{count} sections"

    def _add_section(self, source, heading, text):
        for start in range(0, len(text), SECTION_LIMIT):       # long sections become several
            chunk = text[start:start + SECTION_LIMIT]
            self.sections.append({"source": source, "heading": heading, "text": chunk})

    # ----- spreadsheets -----------------------------------------------
    def _load_workbook(self, source, path):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if path.lower().endswith(".csv"):
                sheets = {"csv": pd.read_csv(path, header=None, dtype=str, keep_default_na=False)}
            else:
                engine = "calamine" if importlib.util.find_spec("python_calamine") else None
                sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str, engine=engine)
        notes = []
        best_suburbs = None
        for name, df in sheets.items():
            df = df.fillna("")
            kind = self._classify(name, df)
            if kind[0] == "suburbs":
                rows = self._suburb_rows(df, kind[1])
                if best_suburbs is None or len(rows) > len(best_suburbs[1]):
                    best_suburbs = (name, rows)
            elif kind[0] == "spelling":
                n = self._load_spelling(df, kind[1])
                notes.append(f"{n} spelling fixes ('{name}')")
            elif kind[0] == "dictionary":
                before = len(self.dictionary)
                n = self._load_dictionary(source, name, df)
                notes.append(f"{n} Zenu fields ('{name}')")
                if len(self.dictionary) == before:
                    # e.g. 'advanced features': same fields, different purpose - keep it readable
                    self._load_table_text(source, name, df)
            else:
                n = self._load_table_text(source, name, df)
                if n:
                    notes.append(f"'{name}' as text")
        if best_suburbs:
            for suburb, state, pc in best_suburbs[1]:
                key = _norm_place(suburb)
                if (suburb, state, pc) not in self.suburbs[key]:
                    self.suburbs[key].append((suburb, state, pc))
            notes.insert(0, f"{len(best_suburbs[1]):,} suburbs ('{best_suburbs[0]}')")
        return "; ".join(notes) or "no usable sheets"

    @staticmethod
    def _header_row(df, required):
        for r in range(min(10, len(df))):
            cells = [_cell(c).lower() for c in df.iloc[r].tolist()]
            if all(any(req in c for c in cells) for req in required):
                return r, cells
        return None, None

    def _classify(self, sheet_name, df):
        r, cells = self._header_row(df, ("suburb", "postcode"))
        if r is not None and any("state" in c for c in cells):
            return "suburbs", r
        r, cells = self._header_row(df, ("column name",))
        if r is not None and any(("accepted" in c or "description" in c) for c in cells):
            return "dictionary", r
        if "spell" in str(sheet_name).lower():
            return "spelling", 0
        r, cells = self._header_row(df, ("updated",))
        if r is not None and sum(1 for c in cells if c) == 2:
            return "spelling", r
        return "table", None

    @staticmethod
    def _suburb_rows(df, hrow):
        head = [_cell(c).lower() for c in df.iloc[hrow].tolist()]

        def col(*names):
            for n in names:
                for i, h in enumerate(head):
                    if h == n:
                        return i
            for n in names:
                for i, h in enumerate(head):
                    if n in h:
                        return i
            return None

        c_sub, c_pc = col("suburb", "locality"), col("postcode", "post code")
        c_state = col("state code", "state")
        rows = []
        for _, r in df.iloc[hrow + 1:].iterrows():
            sub = _cell(r.iloc[c_sub])
            state = _cell(r.iloc[c_state]).upper() if c_state is not None else ""
            if sub and "\t" not in sub:
                rows.append((sub, state if state in STATE_CODES else state[:3],
                             _postcode(r.iloc[c_pc])))
        return rows

    def _load_spelling(self, df, hrow):
        n = 0
        for _, r in df.iloc[hrow + 1:].iterrows():
            wrong, right = _cell(r.iloc[0]), _cell(r.iloc[1]) if len(r) > 1 else ""
            if wrong and right and _norm_place(wrong) != _norm_place(right):
                self.spelling[_norm_place(wrong)] = right
                n += 1
        return n

    def _load_dictionary(self, source, sheet, df):
        """Zenu dictionary: a field row, then continuation rows (blank name) adding
        more accepted values / examples. Rows with only column A are notes/headings."""
        cols, field, n, note = None, None, 0, []
        for _, r in df.iterrows():
            cells = [_cell(c) for c in r.tolist()]
            low = [c.lower() for c in cells]
            if "column name" in low:
                cols = {k: low.index(k) for k in low if k}
                continue
            if cols is None:
                if cells[0]:
                    note.append(cells[0])
                continue
            get = lambda key: cells[cols[key]] if key in cols and cols[key] < len(cells) else ""
            name = get("column name")
            if name and not any(cells[1:]) and " " in name:
                note.append(name)                   # a heading / explanation line
                continue
            if name:
                n += 1
                known = self.dictionary.get(name)
                if known and known["sheet"] != sheet:
                    field = None                    # already defined on an earlier sheet
                    continue
                field = name
                self.dictionary.setdefault(field, {"type": get("type"), "desc": get("description"),
                                                   "accepted": [], "examples": [], "sheet": sheet})
            if field is None:
                continue
            entry = self.dictionary[field]
            if get("accepted values"):
                entry["accepted"].append(get("accepted values"))
            if get("examples"):
                entry["examples"].append(get("examples"))
        if note:
            self._add_section(source, f"{os.path.basename(source)} > {sheet} notes", "\n".join(note))
        return n

    def _load_table_text(self, source, sheet, df):
        rows = [" | ".join(c for c in (_cell(x) for x in r.tolist()) if c)
                for _, r in df.head(300).iterrows()]
        text = "\n".join(r for r in rows if r)
        if text:
            self._add_section(source, f"{os.path.basename(source)} > {sheet}", text)
        return bool(text)

    # ----- search index -----------------------------------------------
    def _index(self):
        for field, e in self.dictionary.items():
            text = f"{field} ({e['type']}): {e['desc']}"
            if e["accepted"]:
                text += f"\nAccepted values: {', '.join(e['accepted'][:40])}"
            if e["examples"]:
                text += f"\nExamples: {', '.join(e['examples'][:5])}"
            self.sections.append({"source": "Zenu dictionary", "heading": field, "text": text})
        df_counts = Counter()
        for s in self.sections:
            s["tokens"] = Counter(_tokens(s["heading"] + " " + s["text"]))
            s["head_tokens"] = set(_tokens(s["heading"]))
            df_counts.update(s["tokens"].keys())
        total = max(len(self.sections), 1)
        self._idf = {t: math.log(1 + total / c) for t, c in df_counts.items()}
        self.suburb_keys = list(self.suburbs)

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------
    def summary(self) -> dict:
        guides = sorted({s["source"] for s in self.sections})
        return {"root": self.root, "files": self.files,
                "suburbs": sum(len(v) for v in self.suburbs.values()),
                "suburb_names": len(self.suburbs), "spelling_fixes": len(self.spelling),
                "dictionary_fields": len(self.dictionary), "guides": guides,
                "sections": len(self.sections)}

    def search(self, query: str, budget: int = 2500, k: int = 4):
        """Most relevant sections for a question, within a character budget."""
        q = set(_tokens(query))
        if not q:
            return []
        scored = []
        for s in self.sections:
            score = sum(s["tokens"].get(t, 0) and (1 + math.log(s["tokens"][t])) * self._idf.get(t, 0)
                        for t in q)
            score += sum(2 * self._idf.get(t, 0) for t in q & s["head_tokens"])
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        out, used = [], 0
        for _score, s in scored[:k]:
            if used + len(s["text"]) > budget and out:
                break
            out.append({"source": s["source"], "heading": s["heading"], "text": s["text"][:budget]})
            used += len(s["text"])
        return out

    def lookup_suburb(self, text: str) -> dict:
        """'Tooradin', 'richmond vic', 'Mt Coolum 4573' -> matches from the suburb list."""
        words = _norm_place(text).split()
        state = next((w for w in words if w in STATE_CODES), None)
        pc = next((w for w in words if re.fullmatch(r"\d{4}", w)), None)
        name = " ".join(w for w in words if w != state and w != pc)
        fixed = self.spelling.get(name)
        key = _norm_place(fixed) if fixed else name
        matches = [m for m in self.suburbs.get(key, [])
                   if (not state or m[1] == state) and (not pc or m[2] == pc)]
        close = [] if matches else difflib.get_close_matches(key, self.suburb_keys, n=5, cutoff=0.82)
        return {"query": text.strip(), "name": key.title(), "state": state, "postcode": pc,
                "spelling_fix": fixed, "loaded": bool(self.suburbs),
                "matches": [{"suburb": s, "state": st, "postcode": p} for s, st, p in matches],
                "close": [self.suburbs[c][0][0] for c in close]}


_LIBRARY = None


def library() -> ReferenceLibrary:
    """Shared library, reloaded only when a file in reference/ changes."""
    global _LIBRARY
    if _LIBRARY is None:
        _LIBRARY = ReferenceLibrary()
    else:
        _LIBRARY.refresh()
    return _LIBRARY
