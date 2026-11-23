import argparse
import csv
import importlib.util
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime

import pandas as pd

from .knowledge import is_placeholder, load_fields, normalize_header
from . import settings as daris_settings
from .address_cleaning import AddressSplitter
from .contact_cleaning import ADDRESS_ROLES, NOTES_COLUMN, ROLE_FIELDS, clean_contacts_frame
from .references import library as reference_library
from .llm import LLMTasks, get_llm
from .matcher import HeaderMatcher
from .memory import MappingMemory
from .profiler import EMPTY_TOKENS, cell_matches, profile

# =====================================================================
# SMART CONSOLIDATION + RECONCILIATION
# ---------------------------------------------------------------------
# Upgrade of /api/consolidate-excel "append" mode:
#   * reads .xlsx / .xls / .csv, every sheet, finds the real header row
#   * maps headers onto Zenu fields (HeaderMatcher) so "Mobile", "Cell"
#     and "Mobile Phone" from three offices land in ONE column
#   * traces every row to its file / sheet / original row number
#   * flags possible duplicates and identifier collisions (never deletes)
#   * reconciles rows and filled cells in vs out, per source column
#   * writes an explained, review-ready workbook + JSON summary
# =====================================================================

SKIP_SHEETS = {"document history", "instructions", "summary"}
HEADER_SCAN_ROWS = 20
EXCEL_MAX_ROWS = 1_000_000
SOURCE_COLS = ["__source_file__", "__source_sheet__", "__source_row__", "__dup_group__"]
ID_FIELDS = ("contact_identifier", "property_identifier")

# Optional speed-ups, used automatically when installed:
#   pip install xlsxwriter python-calamine
READ_ENGINE = "calamine" if importlib.util.find_spec("python_calamine") else None
WRITE_ENGINE = "xlsxwriter" if importlib.util.find_spec("xlsxwriter") else "openpyxl"


def _filled(series: pd.Series) -> pd.Series:
    return ~series.astype(str).str.strip().str.lower().isin(EMPTY_TOKENS)


def _phone_key(value: str):
    digits = re.sub(r"\D", "", value)
    if digits.startswith("61") and len(digits) == 11:
        digits = "0" + digits[2:]
    if len(digits) < 8 or len(set(digits)) == 1:
        return None
    return digits[-9:]


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


class SmartConsolidator:
    def __init__(self, header_mode: str = "zenu", use_llm: bool = True,
                 memory: MappingMemory = None, llm: LLMTasks = None, log=print):
        self.header_mode = header_mode if header_mode in ("zenu", "original") else "zenu"
        self.memory = memory or MappingMemory()
        self.llm = (llm or get_llm()) if use_llm else None
        # None lets DARIS_LLM_AUTO_MAP decide when the Settings switch is off
        auto_apply = daris_settings.load()["auto_apply_ai_mappings"] or None
        self.matcher = HeaderMatcher(memory=self.memory, llm=self.llm, log=log,
                                     llm_auto_apply=auto_apply)
        self.log = log
        self.field_order = {f["name"]: i for i, f in enumerate(load_fields())}

        self.sources = []        # one entry per ingested sheet
        self.issues = []
        self.mapping_rows = []
        self.column_checks = []
        self.duplicate_rows = []
        self.dup_stats = Counter()
        self.id_collisions = []

    def add_issue(self, severity, area, detail):
        self.issues.append({"severity": severity, "area": area, "detail": detail})

    # -----------------------------------------------------------------
    # 1. INGEST
    # -----------------------------------------------------------------
    @staticmethod
    def _read_raw(path):
        """{sheet_name: DataFrame(header=None, str)}"""
        low = path.lower()
        if low.endswith((".csv", ".txt")):
            # csv module, not pd.read_csv: a title line above the header makes pandas
            # fix the row width at 1 and silently drop every real data row
            csv.field_size_limit(2**31 - 1)
            for enc in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    with open(path, "r", encoding=enc, newline="") as f:
                        rows = list(csv.reader(f))
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError(f"Could not decode {os.path.basename(path)}")
            width = max((len(r) for r in rows), default=0)
            return {"csv": pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=str)}
        engine = READ_ENGINE if low.endswith((".xlsx", ".xlsm", ".xlsb", ".xls")) else None
        return pd.read_excel(path, sheet_name=None, header=None, dtype=str, engine=engine)

    def _find_header_row(self, raw: pd.DataFrame) -> int:
        best_row, best_score = 0, -1
        widest = 0
        scan = raw.head(HEADER_SCAN_ROWS).fillna("")
        for r in range(len(scan)):
            cells = [str(c).strip() for c in scan.iloc[r].tolist()]
            widest = max(widest, sum(1 for c in cells if c))
        for r in range(len(scan)):
            cells = [str(c).strip() for c in scan.iloc[r].tolist() if str(c).strip()]
            if len(cells) < max(2, widest * 0.5):
                continue
            texty = sum(1 for c in cells if len(c) <= 60 and not re.fullmatch(r"[-\d.,/: $]+", c))
            known = sum(1 for c in cells if self.matcher.is_known_header(c))
            score = texty + 2 * known
            if score > best_score:
                best_row, best_score = r, score
        return best_row

    def _looks_headerless(self, raw: pd.DataFrame, hrow: int) -> bool:
        """True when the chosen 'header' row is really the first record: none of its
        cells is a known column name, and some sit naturally in their column's data
        (a phone number above phone numbers) while none contradict it ('Mobile' above
        phone numbers would)."""
        below = raw.iloc[hrow + 1:hrow + 201]
        if below.empty:
            return False
        data_like = 0
        for col in raw.columns:
            cell = str(raw.iat[hrow, raw.columns.get_loc(col)]).strip()
            if not cell:
                continue
            if self.matcher.is_known_header(cell):
                return False
            fits = cell_matches(cell, profile(below[col].tolist())["type"])
            if fits is False:
                return False
            data_like += bool(fits)
        return data_like > 0

    def _daris_output_data(self, fname, sheets):
        """A whole DARIS workbook fed back in: read its data sheet only (the cleaned one if
        present), not the report tabs - they would be merged in as rows."""
        names = {str(n).strip().lower(): n for n in sheets}
        if "daris summary" not in names:
            return sheets
        base = "Cleaned Contacts" if "cleaned contacts" in names else "Consolidated"
        keep = {n: df for n, df in sheets.items()
                if str(n) == base or str(n).startswith(base + "_")}
        skipped = [str(n) for n in sheets if n not in keep]
        self.add_issue("INFO", "Ingest",
                       f"'{fname}' is a DARIS output workbook - only its '{base}' data was read; "
                       f"other tabs skipped: {', '.join(skipped)}.")
        return keep

    @staticmethod
    def _unique_headers(cells):
        out, seen = [], Counter()
        for i, c in enumerate(cells):
            h = "" if pd.isna(c) else str(c).strip()
            h = h or f"Column_{i + 1}"
            seen[h.lower()] += 1
            out.append(h if seen[h.lower()] == 1 else f"{h} ({seen[h.lower()]})")
        return out

    def ingest(self, paths):
        for path in paths:
            fname = os.path.basename(path)
            try:
                sheets = self._read_raw(path)
            except Exception as e:
                self.add_issue("FAIL", "Ingest", f"Could not read '{fname}': {e}")
                continue
            sheets = self._daris_output_data(fname, sheets)
            for sheet_name, raw in sheets.items():
                if str(sheet_name).strip().lower() in SKIP_SHEETS or raw.empty:
                    continue
                raw = raw.fillna("")
                hrow = self._find_header_row(raw)
                headerless = self._looks_headerless(raw, hrow)
                if headerless:
                    # Row hrow is the first record - keep it; columns get placeholders
                    headers = [f"Column_{i + 1}" for i in range(len(raw.columns))]
                    data = raw.iloc[hrow:].copy()
                else:
                    headers = self._unique_headers(raw.iloc[hrow].tolist())
                    data = raw.iloc[hrow + 1:].copy()
                    # A DARIS output fed back in already has tracking columns: keep them as data
                    renamed = [h for h in headers if h in SOURCE_COLS]
                    headers = [f"prev_{h.strip('_')}" if h in SOURCE_COLS else h for h in headers]
                    if renamed:
                        self.add_issue("INFO", "Ingest",
                                       f"'{fname}' / '{sheet_name}' came from an earlier DARIS run - its "
                                       f"tracking columns were kept as {', '.join('prev_' + h.strip('_') for h in renamed)}.")
                data.columns = headers
                # Keep the original spreadsheet row number for traceability
                data.insert(0, "__source_row__", [str(i + 1) for i in data.index])
                is_blank = (data[headers].astype(str).apply(lambda s: s.str.strip()) == "").all(axis=1)
                blanks = int(is_blank.sum())
                data = data[~is_blank].reset_index(drop=True)
                # Drop header cells with no data underneath (trailing Column_N noise)
                headers = [h for h in headers
                           if not (h.startswith("Column_") and not _filled(data[h]).any())]
                if data.empty:
                    continue
                self.sources.append({
                    "file": fname, "sheet": str(sheet_name),
                    "header_row": 0 if headerless else hrow + 1, "headerless": headerless,
                    "headers": headers, "data": data[["__source_row__"] + headers],
                    "blank_rows_skipped": blanks,
                })
                if headerless:
                    self.add_issue("WARN", "Ingest",
                                   f"'{fname}' / '{sheet_name}' has no header row - row {hrow + 1} is "
                                   f"the first record and was kept. Columns were mapped from their "
                                   f"values; check them in 'Column Mapping'.")
                elif hrow > 0:
                    self.add_issue("INFO", "Ingest",
                                   f"'{fname}' / '{sheet_name}': header found on row {hrow + 1} "
                                   f"(rows above it skipped).")
        self.log(f"[DARIS] Ingested {len(self.sources)} sheet(s) from {len(paths)} file(s).")

    # -----------------------------------------------------------------
    # 2-3. PROFILE + MATCH, then decide output column names
    # -----------------------------------------------------------------
    def map_columns(self):
        for src in self.sources:
            df = src["data"]
            columns = [(h, df[h].tolist()) for h in src["headers"]]
            src["decisions"] = self.matcher.match_columns(columns)
            if src.get("headerless"):
                self.matcher.map_by_values(src["decisions"], dict(columns))

        used = set()

        def claim(label):
            base, n = label, 2
            while label.lower() in used:
                label, n = f"{base} ({n})", n + 1
            used.add(label.lower())
            return label

        # Pass 1: Zenu-mapped columns claim their labels first (one column per field)
        target_label = {}
        for src in self.sources:
            for d in src["decisions"]:
                if d["target"] and d["target"] not in target_label:
                    target_label[d["target"]] = claim(
                        d["target"] if self.header_mode == "zenu" else d["source_column"])

        # Pass 2: unmapped columns merge by normalized header across sheets;
        # a repeat of the same header inside one sheet gets its own column
        loose_label = {}
        for src in self.sources:
            seen_here = Counter()
            for d in src["decisions"]:
                if d["target"]:
                    d["output_column"] = target_label[d["target"]]
                    continue
                norm = normalize_header(d["source_column"])
                seen_here[norm] += 1
                key = (norm, seen_here[norm])
                if is_placeholder(d["source_column"]):
                    # 'Column_3' in two headerless files is not the same column
                    key += (src["file"], src["sheet"])
                if key not in loose_label:
                    loose_label[key] = claim(d["source_column"])
                d["output_column"] = loose_label[key]

        for src in self.sources:
            for d in src["decisions"]:
                self.mapping_rows.append({
                    "file": src["file"], "sheet": src["sheet"],
                    "source_column": d["source_column"], "output_column": d["output_column"],
                    "zenu_field": d["target"] or "", "method": d["method"],
                    "confidence": d["confidence"], "value_type": d["type"],
                    "fill_pct": round(d["fill"] * 100, 1),
                    "suggestion": d["suggestion"] if not d["target"] else "",
                    "suggested_by": d["suggested_by"] if not d["target"] else "",
                    "sample": " | ".join(d["sample"][:3])[:200],
                })

        # Column order: Zenu fields in schema order, then the rest as first seen
        self.mapped_cols = [target_label[t] for t in
                            sorted(target_label, key=lambda t: self.field_order.get(t, 9999))]
        self.loose_cols = list(dict.fromkeys(loose_label.values()))
        self.target_label = target_label

    # -----------------------------------------------------------------
    # 4. CONSOLIDATE
    # -----------------------------------------------------------------
    def build(self) -> pd.DataFrame:
        frames = []
        for src in self.sources:
            rename = {d["source_column"]: d["output_column"] for d in src["decisions"]}
            df = src["data"].rename(columns=rename)
            df.insert(0, "__source_file__", src["file"])
            df.insert(1, "__source_sheet__", src["sheet"])
            src["out_len"] = len(df)
            frames.append(df)
        combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
        combined["__dup_group__"] = ""
        order = SOURCE_COLS + self.mapped_cols + self.loose_cols
        return combined.reindex(columns=order).fillna("")

    # -----------------------------------------------------------------
    # 5. DUPLICATES (flag, never delete)
    # -----------------------------------------------------------------
    def _cols_for(self, fields=(), types=()):
        cols = [self.target_label[f] for f in fields if f in self.target_label]
        for src in self.sources:
            for d in src["decisions"]:
                if d["type"] in types and d["output_column"] not in cols:
                    cols.append(d["output_column"])
        return cols

    def detect_duplicates(self, df: pd.DataFrame):
        if df.empty:
            return
        uf = _UnionFind()
        reasons = defaultdict(set)
        first_seen = {}

        def link(row, key, reason):
            if key in first_seen:
                uf.union(first_seen[key], row)
                reasons[row].add(reason)
                reasons[first_seen[key]].add(reason)
            else:
                first_seen[key] = row

        email_cols = self._cols_for(("contact_email_address",), ("email",))
        phone_cols = self._cols_for(("contact_mobile", "contact_phone_home", "contact_phone_work"),
                                    ("phone",))
        first = self.target_label.get("contact_first_name")
        last = self.target_label.get("contact_surname")
        locality = (self.target_label.get("contact_mailing_address_postcode")
                    or self.target_label.get("contact_mailing_address_suburb"))
        data_cols = self.mapped_cols + self.loose_cols

        for col in email_cols:
            for i, v in df[col].items():
                v = str(v).strip().lower()
                if "@" in v:
                    link(i, f"email:{v}", "same email")
        for col in phone_cols:
            for i, v in df[col].items():
                k = _phone_key(str(v))
                if k:
                    link(i, f"phone:{k}", "same phone")
        if first and last and locality:
            names = (df[first].str.strip().str.lower() + "|" + df[last].str.strip().str.lower()
                     + "|" + df[locality].str.strip().str.lower())
            for i, v in names.items():
                parts = v.split("|")
                if parts[0] and parts[1] and parts[2]:
                    link(i, f"name:{v}", "same name + locality")
        if data_cols:
            row_hash = pd.util.hash_pandas_object(df[data_cols], index=False)
            filled_counts = df[data_cols].apply(_filled).sum(axis=1)
            for i, h in row_hash.items():
                if filled_counts[i] >= 2:
                    link(i, f"row:{h}", "identical row")

        groups = defaultdict(list)
        for i in reasons:
            groups[uf.find(i)].append(i)
        show = [c for c in (first, last, *email_cols[:1], *phone_cols[:1]) if c]
        for n, root in enumerate(sorted(groups), start=1):
            gid = f"D{n:05d}"
            for i in sorted(groups[root]):
                df.at[i, "__dup_group__"] = gid
                row = {"dup_group": gid, "consolidated_row": i + 2,
                       "source_file": df.at[i, "__source_file__"],
                       "source_row": df.at[i, "__source_row__"],
                       "match_reasons": ", ".join(sorted(reasons[i]))}
                for c in show:
                    row[c] = df.at[i, c]
                self.duplicate_rows.append(row)
            for r in set().union(*(reasons[i] for i in groups[root])):
                self.dup_stats[r] += 1
        self.dup_groups = len(groups)

        # Identifier collisions: one ID used by more than one source row
        for field in ID_FIELDS:
            col = self.target_label.get(field)
            if not col:
                continue
            ids = df.loc[_filled(df[col]), [col, "__source_file__"]]
            counts = ids.groupby(col)["__source_file__"].agg(["count", "nunique"])
            clashes = counts[counts["count"] > 1]
            if clashes.empty:
                continue
            cross = clashes[clashes["nunique"] > 1]
            self.id_collisions.append({
                "identifier": field, "duplicated_values": int(len(clashes)),
                "across_files": int(len(cross)),
                "sample": ", ".join(map(str, clashes.index[:10]))})
            self.add_issue("FAIL", "Identifiers",
                           f"{len(clashes)} {field} value(s) appear on more than one row"
                           + (f" ({len(cross)} across different files - IDs clash between sources)"
                              if len(cross) else "")
                           + f". e.g. {', '.join(map(str, clashes.index[:5]))}")

        if self.dup_groups:
            self.add_issue("WARN", "Duplicates",
                           f"{self.dup_groups} possible duplicate group(s) covering "
                           f"{len(self.duplicate_rows)} rows - see 'Duplicates' sheet. "
                           f"Rows are flagged in __dup_group__, nothing was removed.")

    # -----------------------------------------------------------------
    # 6. RECONCILE
    # -----------------------------------------------------------------
    def reconcile(self, df: pd.DataFrame):
        offset = 0
        self.recon_rows = []
        for src in self.sources:
            n = len(src["data"])
            out = df.iloc[offset:offset + src["out_len"]]
            offset += src["out_len"]
            ok_rows = (len(out) == n and
                       (out["__source_file__"] == src["file"]).all() and
                       (out["__source_row__"].values == src["data"]["__source_row__"].values).all())
            cells_in = cells_out = 0
            for d in src["decisions"]:
                fin = int(_filled(src["data"][d["source_column"]]).sum())
                fout = int(_filled(out[d["output_column"]]).sum())
                cells_in += fin
                cells_out += fout
                status = "OK" if fin == fout else "MISMATCH"
                self.column_checks.append({
                    "file": src["file"], "sheet": src["sheet"],
                    "source_column": d["source_column"], "output_column": d["output_column"],
                    "filled_in": fin, "filled_out": fout, "status": status})
                if status != "OK":
                    self.add_issue("FAIL", "Cell Reconciliation",
                                   f"'{src['file']}' column '{d['source_column']}': {fin} filled "
                                   f"cells in source but {fout} in output.")
            unmapped = [d["source_column"] for d in src["decisions"] if not d["target"]]
            self.recon_rows.append({
                "file": src["file"], "sheet": src["sheet"], "header_row": src["header_row"],
                "rows_in": n, "rows_out": len(out), "blank_rows_skipped": src["blank_rows_skipped"],
                "columns_in": len(src["decisions"]),
                "columns_mapped_to_zenu": len(src["decisions"]) - len(unmapped),
                "cells_in": cells_in, "cells_out": cells_out,
                "status": "OK" if ok_rows and cells_in == cells_out else "MISMATCH"})
            if not ok_rows:
                self.add_issue("FAIL", "Row Reconciliation",
                               f"'{src['file']}' / '{src['sheet']}': {n} rows in, {len(out)} out.")

        # Mapping quality
        if self.header_mode == "zenu":
            review = [m for m in self.mapping_rows if not m["zenu_field"] and m["suggestion"]]
            unmapped = [m for m in self.mapping_rows if not m["zenu_field"] and not m["suggestion"]]
            if review:
                self.add_issue("WARN", "Mapping",
                               f"{len(review)} column(s) have a low-confidence Zenu suggestion and were "
                               f"kept under their original name: "
                               + ", ".join(f"{m['source_column']}->{m['suggestion']}" for m in review[:8]))
            if unmapped:
                names = sorted({m["source_column"] for m in unmapped})
                self.add_issue("INFO", "Mapping",
                               f"{len(names)} column(s) have no Zenu equivalent and were kept as-is: "
                               + ", ".join(names[:12]) + (" ..." if len(names) > 12 else ""))
        fuzzy = [m for m in self.mapping_rows if m["method"] in ("fuzzy", "llm")]
        if fuzzy:
            self.add_issue("INFO", "Mapping",
                           f"{len(fuzzy)} column(s) were matched by fuzzy/LLM logic - spot-check them "
                           f"in 'Column Mapping'.")

        # Columns present in some sources but not others
        if len(self.sources) > 1:
            presence = Counter()
            for src in self.sources:
                for c in {d["output_column"] for d in src["decisions"]}:
                    presence[c] += 1
            partial = [c for c, k in presence.items() if k < len(self.sources)]
            if partial:
                self.add_issue("INFO", "Schema Drift",
                               f"{len(partial)} column(s) exist in only some source sheets "
                               f"(blank for the others): " + ", ".join(partial[:10])
                               + (" ..." if len(partial) > 10 else ""))

    def overall(self):
        sev = {i["severity"] for i in self.issues}
        return "FAIL" if "FAIL" in sev else "WARN" if "WARN" in sev else "PASS"

    # -----------------------------------------------------------------
    # 6b. CONTACT CLEANING (optional) - Daryl's macros, see contact_cleaning.py
    # -----------------------------------------------------------------
    def clean_contacts(self, df):
        labels = {role: self.target_label[field] for role, field in ROLE_FIELDS.items()
                  if field in self.target_label}
        if not labels:
            self.add_issue("INFO", "Cleaning", "No contact name, phone or email columns were found to clean.")
            return None
        accepted = next((f.get("accepted") for f in load_fields() if f["name"] == "contact_title"), None)
        lib = reference_library()
        splitter = AddressSplitter(lib.suburbs, lib.spelling) if lib.suburbs else None
        if splitter is None and any(r in labels for r in ADDRESS_ROLES):
            self.add_issue("INFO", "Cleaning", "Addresses were not split - add an AU suburb list "
                                               "(suburb, state, postcode) to reference/lookups/.")
        cleaned, audit, s = clean_contacts_frame(df, labels, accepted_titles=accepted,
                                                 address_splitter=splitter)
        self.cleaned_df, self.cleaning_audit, self.cleaning = cleaned, audit, s

        if s["addresses_cleaned"]:
            self.add_issue("INFO", "Cleaning",
                           f"Addresses: {s['addresses_split']:,} split into street + suburb; "
                           f"{s['states_from_address']:,} states and {s['postcodes_from_address']:,} postcodes "
                           f"taken from the address text (nothing filled in from the suburb list); "
                           f"{s['suburb_spelling_fixed']:,} suburb spellings fixed.")
            if s["unknown_suburbs"]:
                self.add_issue("WARN", "Cleaning",
                               f"{s['unknown_suburbs']:,} suburb(s) aren't in the AU suburb list - see "
                               f"{NOTES_COLUMN} for 'did you mean' suggestions.")
            odd = s["state_mismatch"] + s["postcode_mismatch"] + s["address_conflicts"]
            if odd:
                self.add_issue("WARN", "Cleaning",
                               f"{odd:,} address(es) disagree with the suburb list or their own suburb "
                               f"column ({s['state_mismatch']} state, {s['postcode_mismatch']} postcode, "
                               f"{s['address_conflicts']} suburb) - nothing was overwritten; see {NOTES_COLUMN}.")
            if s["ambiguous_state"]:
                self.add_issue("INFO", "Cleaning",
                               f"{s['ambiguous_state']:,} suburb(s) exist in several states and had no state "
                               f"- state left blank.")

        if s["details_cleaned"]:
            if not s["phones_reconciled"]:
                self.add_issue("FAIL", "Cleaning",
                               f"Phone values don't reconcile: {s['phone_values_in']:,} in, "
                               f"{s['phone_values_placed']:,} placed + {s['phone_duplicates_skipped']:,} "
                               f"duplicates + {s['Invalid numbers (W:Z)']:,} invalid "
                               f"({s['phone_values_in_output']:,} found in the output).")
            if s["Invalid numbers (W:Z)"]:
                self.add_issue("WARN", "Cleaning",
                               f"{s['Invalid numbers (W:Z)']:,} phone value(s) match no valid format and were "
                               f"moved to invalid_mobile / invalid_home / invalid_work / invalid_fax.")
            if s["text_dropped_from_phones"]:
                self.add_issue("WARN", "Cleaning",
                               f"{s['text_dropped_from_phones']:,} value(s) with no digits were removed from "
                               f"phone cells by the macro rule (e.g. an invalid email typed as a phone) - "
                               f"they are listed in {NOTES_COLUMN}.")
            if s["multi_value_email"]:
                self.add_issue("WARN", "Cleaning",
                               f"{s['multi_value_email']:,} record(s) hold more than one email in "
                               f"contact_email_address (joined with ' | ') - Zenu accepts one per contact.")
            if s["multi_value_mobile"]:
                self.add_issue("INFO", "Cleaning",
                               f"{s['multi_value_mobile']:,} record(s) hold more than one mobile in "
                               f"contact_mobile (joined with ' | ').")
            if s["Invalid/extra email (U)"]:
                self.add_issue("INFO", "Cleaning",
                               f"{s['Invalid/extra email (U)']:,} record(s) have invalid emails in "
                               f"invalid_extra_email.")
        if s["names_cleaned"]:
            if s["labels_removed"]:
                self.add_issue("WARN", "Cleaning",
                               f"{s['labels_removed']:,} record(s) had ALL-CAPS text removed as a business "
                               f"label - check {NOTES_COLUMN}: this also removes a capitalised partner "
                               f"('John & MARY' keeps only John).")
            if s["titles_not_accepted"]:
                self.add_issue("WARN", "Cleaning",
                               "Titles Zenu's contact_title doesn't accept: "
                               + ", ".join(f"{t} x{c}" for t, c in s["titles_not_accepted"].items()) + ".")
            if s["records_split"]:
                self.add_issue("INFO", "Cleaning",
                               f"{s['records_split']:,} record(s) split into 2+ people "
                               f"(extra people in contact2_* ... columns).")
        return s

    # -----------------------------------------------------------------
    # 7. EXPLAIN
    # -----------------------------------------------------------------
    def facts(self, df):
        methods = Counter(m["method"].split(":")[0] for m in self.mapping_rows)
        cleaning = getattr(self, "cleaning", None)
        return {
            **({"cleaning": {k: v for k, v in cleaning.items() if not isinstance(v, dict)}}
               if cleaning else {}),
            "overall": self.overall(),
            "files": len({s["file"] for s in self.sources}), "sheets": len(self.sources),
            "rows_in": sum(r["rows_in"] for r in self.recon_rows),
            "rows_out": len(df),
            "cells_in": sum(r["cells_in"] for r in self.recon_rows),
            "cells_out": sum(r["cells_out"] for r in self.recon_rows),
            "output_columns": len(self.mapped_cols) + len(self.loose_cols),
            "zenu_columns": len(self.mapped_cols), "kept_original_columns": len(self.loose_cols),
            "mapping_methods": dict(methods),
            "duplicate_groups": getattr(self, "dup_groups", 0),
            "duplicate_rows": len(self.duplicate_rows),
            "duplicate_reasons": dict(self.dup_stats),
            "identifier_collisions": self.id_collisions,
            "fail_count": sum(1 for i in self.issues if i["severity"] == "FAIL"),
            "warn_count": sum(1 for i in self.issues if i["severity"] == "WARN"),
            "issues": [f"{i['severity']}: {i['detail']}" for i in self.issues[:15]],
        }

    @staticmethod
    def template_summary(f):
        lines = [
            f"- Overall: {f['overall']} - {f['files']} file(s) / {f['sheets']} sheet(s), "
            f"{f['rows_in']:,} rows in, {f['rows_out']:,} rows out"
            + (" (reconciled)." if f["rows_in"] == f["rows_out"] else " - ROW COUNT MISMATCH."),
            f"- Cells: {f['cells_in']:,} filled cells in, {f['cells_out']:,} out"
            + (" - no data lost in the merge." if f["cells_in"] == f["cells_out"] else " - CHECK 'Column Check'."),
            f"- Columns: {f['output_columns']} output columns; {f['zenu_columns']} matched to Zenu fields, "
            f"{f['kept_original_columns']} kept under original names. Methods: "
            + ", ".join(f"{k} {v}" for k, v in sorted(f["mapping_methods"].items())) + ".",
        ]
        if f["duplicate_groups"]:
            lines.append(f"- Duplicates: {f['duplicate_groups']:,} possible groups over "
                         f"{f['duplicate_rows']:,} rows ("
                         + ", ".join(f"{k}: {v}" for k, v in f["duplicate_reasons"].items())
                         + "). Flagged only, nothing removed.")
        else:
            lines.append("- Duplicates: none detected on email, phone, name+locality or identical rows.")
        c = f.get("cleaning")
        if c:
            parts = []
            if c.get("names_cleaned"):
                parts.append(f"{c['people']:,} people from {c['records_with_names']:,} records "
                             f"({c['records_split']:,} split)")
            if c.get("details_cleaned"):
                parts.append(f"{c['Mobile (G)']:,} mobiles, {c['Home Landline (J)']:,} home / "
                             f"{c['Work Landline (L)']:,} work landlines, {c['Email (O)']:,} emails, "
                             f"{c['Invalid numbers (W:Z)']:,} invalid numbers"
                             + (" - every phone value accounted for" if c["phones_reconciled"]
                                else " - PHONE VALUES DON'T RECONCILE"))
            if c.get("addresses_cleaned"):
                parts.append(f"{c['addresses_split']:,} addresses split into street + suburb, "
                             f"{c['unknown_suburbs']:,} unknown suburbs")
            lines.append("- Contact cleaning: " + "; ".join(parts) + ". See 'Cleaned Contacts'.")
        if f["identifier_collisions"]:
            lines.append("- Identifier collisions: " + "; ".join(
                f"{c['identifier']} x{c['duplicated_values']}" for c in f["identifier_collisions"])
                + " - fix before import or records will merge/overwrite in Zenu.")
        nxt = ("resolve the FAIL items in 'Issues' before import" if f["fail_count"]
               else "review the WARN items and low-confidence rows in 'Column Mapping'" if f["warn_count"]
               else "proceed to Import Prep")
        lines.append(f"- Next action: {nxt}.")
        return "\n".join(lines)

    # -----------------------------------------------------------------
    # REPORT
    # -----------------------------------------------------------------
    @staticmethod
    def _put(writer, frame, name, autofilter=True):
        frame.to_excel(writer, sheet_name=name, index=False, freeze_panes=(1, 0))
        if not autofilter or frame.empty:
            return
        ws = writer.sheets[name]
        if WRITE_ENGINE == "xlsxwriter":
            ws.autofilter(0, 0, len(frame), len(frame.columns) - 1)
        else:
            ws.auto_filter.ref = ws.dimensions

    def write(self, df, out_path, summary_text, llm_text, facts):
        put = self._put
        with pd.ExcelWriter(out_path, engine=WRITE_ENGINE) as writer:
            frames = [(df, "Consolidated")]
            if getattr(self, "cleaning", None):
                frames.append((self.cleaned_df, "Cleaned Contacts"))
            for frame, name in frames:
                if len(frame) <= EXCEL_MAX_ROWS:
                    put(writer, frame, name)
                else:
                    for part, start in enumerate(range(0, len(frame), EXCEL_MAX_ROWS), start=1):
                        put(writer, frame.iloc[start:start + EXCEL_MAX_ROWS], f"{name}_{part}")

            summary = [{"Item": "Generated", "Value": datetime.now().strftime("%d/%m/%Y %H:%M:%S")},
                       {"Item": "Overall Status", "Value": facts["overall"]},
                       {"Item": "Header Mode", "Value": self.header_mode},
                       {"Item": "Local LLM", "Value": (f"{self.llm.model} ({self.matcher.llm_calls} mapping calls)"
                                                       if self.llm and self.llm.available else "offline - rules + fuzzy only")}]
            summary += [{"Item": "", "Value": ""}, {"Item": "DARIS summary", "Value": ""}]
            summary += [{"Item": "", "Value": line} for line in summary_text.splitlines()]
            if llm_text:
                summary += [{"Item": "", "Value": ""}, {"Item": "Local LLM narrative", "Value": ""}]
                summary += [{"Item": "", "Value": line} for line in llm_text.splitlines() if line.strip()]
            put(writer, pd.DataFrame(summary), "DARIS Summary", autofilter=False)

            issues = self.issues or [{"severity": "PASS", "area": "-", "detail": "No issues detected."}]
            sev_rank = {"FAIL": 0, "WARN": 1, "INFO": 2, "PASS": 3}
            put(writer, pd.DataFrame(sorted(issues, key=lambda i: sev_rank[i["severity"]])), "Issues")
            put(writer, pd.DataFrame(self.mapping_rows), "Column Mapping")
            put(writer, pd.DataFrame(self.recon_rows), "Reconciliation")
            put(writer, pd.DataFrame(self.column_checks), "Column Check")
            if self.duplicate_rows:
                put(writer, pd.DataFrame(self.duplicate_rows[:EXCEL_MAX_ROWS]), "Duplicates")
            if getattr(self, "cleaning", None):
                rows = []
                for k, v in self.cleaning.items():
                    if isinstance(v, dict):
                        rows += [{"Item": f"{k}: {kk}", "Value": vv} for kk, vv in v.items()]
                    else:
                        rows.append({"Item": k, "Value": v})
                put(writer, pd.DataFrame(rows), "Cleaning Summary", autofilter=False)
                audit = pd.DataFrame(self.cleaning_audit[:EXCEL_MAX_ROWS],
                                     columns=["consolidated_row", "source_file", "source_row",
                                              "column", "before", "after"])
                put(writer, audit, "Cleaning Audit")

    # -----------------------------------------------------------------
    def run(self, paths, out_path, summary_json=None, narrate=True, clean_contacts=False):
        """narrate=False skips the LLM summary (the chat UI streams its own).
        clean_contacts=True also runs the name-splitting and phone/email macros."""
        t0 = time.time()
        self.log(f"[DARIS] Reading {len(paths)} file(s)...")
        self.ingest(paths)
        if not self.sources:
            raise ValueError("No readable data rows found in the uploaded files.")
        self.log("[DARIS] Matching columns to Zenu fields...")
        self.map_columns()
        df = self.build()
        self.log(f"[DARIS] Consolidated {len(df):,} rows x {len(df.columns)} columns.")
        self.log("[DARIS] Checking duplicates and identifier clashes...")
        self.detect_duplicates(df)
        self.log("[DARIS] Reconciling rows and cells in vs out...")
        self.reconcile(df)
        if clean_contacts:
            self.log("[DARIS] Cleaning names, phones and emails (your macro rules)...")
            self.clean_contacts(df)
        facts = self.facts(df)
        summary_text = self.template_summary(facts)
        llm_text = None
        if narrate and self.llm and self.llm.available:
            self.log(f"[DARIS] {self.llm.label} is writing the summary...")
            llm_text = self.llm.summarize(facts)
        self.log("[DARIS] Writing the workbook...")
        self.write(df, out_path, summary_text, llm_text, facts)

        result = {
            "status": "complete", **facts,
            "seconds": round(time.time() - t0, 1),
            "llm": {"used": bool(self.llm and self.llm.available),
                    "model": self.llm.model if self.llm else None,
                    "mapping_calls": self.matcher.llm_calls},
            "summary": summary_text, "llm_summary": llm_text,
            "mapping": self.mapping_rows, "output_file": os.path.basename(out_path),
            "cleaning": getattr(self, "cleaning", None),
        }
        if summary_json:
            with open(summary_json, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, default=str)
        self.log(f"[DARIS] {facts['overall']} in {result['seconds']}s -> {out_path}")
        return result


# ---------------------------------------------------------------------
# CLI:  python -m daris.consolidator a.xlsx b.csv -o out.xlsx [--original] [--no-llm]
# ---------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser(description="DARIS smart consolidation")
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--output", default="daris_consolidated.xlsx")
    ap.add_argument("--original", action="store_true", help="keep source header names")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--clean", action="store_true", help="also clean names, phones and emails")
    args = ap.parse_args()
    res = SmartConsolidator(header_mode="original" if args.original else "zenu",
                            use_llm=not args.no_llm).run(
        args.files, args.output, os.path.splitext(args.output)[0] + "_summary.json",
        clean_contacts=args.clean)
    print(res["summary"])
    if res["llm_summary"]:
        print("\nLLM:\n" + res["llm_summary"])


if __name__ == "__main__":
    _main()
