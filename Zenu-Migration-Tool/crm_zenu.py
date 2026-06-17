import pandas as pd
import sqlite3
import os
import re
import math
import base64
from datetime import datetime, timedelta

# Excel hard limit is 1,048,576 rows; reserve one for the header row.
EXCEL_MAX_ROWS = 1_048_575

# property_identifier prefix for the simple property groups. NOTE: the JSON says
# "Add Pr_{id}" for BOTH Prospect and Appraisal. Flip "Appraisal" to "Appr_" if
# that was the real intent.
# Fallback property_identifier prefix if a group's note doesn't specify one.
# The actual prefix is normally read from the rule note ("Add Appr_{id}").
IDENTIFIER_PREFIX = {"Prospect": "Pr_", "Appraisal": "Appr_"}

# The four "early pipeline" statuses used by several groups.
EARLY_STATUSES = {"property", "appraisal", "listing presentation", "pre-appraisal"}
APPR_STATUSES = {"appraisal", "listing presentation", "pre-appraisal"}

NOTE_GROUPS = ("Contact Notes", "Contact Notes (Communication)")

# Groups whose identifier lookup must NOT expand to _c2+ even if the JSON note
# says "include those split" (explicit override). Vendor: "No need to include _c2".
NO_EXPAND_GROUPS = {"Vendor"}

# Inspection date output: day-first (Australian) -> 4/03/2016. Set False for
# month-first (4/03/2016 meaning April 3).
INSPECTION_DAYFIRST = True


class ZenuProcessor:
    """
    Zenu -> Zenu migration logic. All Zenu-source group transformations live
    here. process_group() is generic and driven by the JSON mapping rules,
    with group-specific post-processing for the trickier cases.
    """

    def __init__(self, engine):
        self.engine = engine
        self.db = engine.conn
        self.expand_one_to_many = getattr(engine, "expand_one_to_many", True)

    def log(self, message):
        self.engine.log(message)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def decode_base64_custom(self, text):
        if isinstance(text, str) and text.startswith("[base64]"):
            try:
                return base64.b64decode(text.replace("[base64]", "")).decode("utf-8")
            except Exception as e:
                self.log(f"Base64 decoding failed for string: {text[:20]}... Error: {e}")
                return text
        return text

    def clean_empty_markers(self, df, columns):
        na_markers = ["N/A", "Unknown", "NA", "None", "none"]
        for col in columns:
            if col in df.columns:
                df[col] = df[col].replace(na_markers, "", regex=False)
        return df

    def _trim_clean(self, series):
        return (series.fillna("").astype(str)
                .str.replace(r"[\r\n\t]+", " ", regex=True)
                .str.replace(r"\s+", " ", regex=True)
                .str.strip())

    def _table_names(self):
        cur = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return [r[0] for r in cur.fetchall()]

    def _resolve_table(self, name):
        """Tolerate JSON typos like 'listing.csv' vs 'listings.csv'."""
        tables = self._table_names()
        if name in tables:
            return name
        low = {t.lower(): t for t in tables}
        if name.lower() in low:
            return low[name.lower()]
        base = name[:-4] if name.lower().endswith(".csv") else name
        for cand in [base + ".csv", base + "s.csv", base.rstrip("s") + ".csv",
                     base.rstrip("s") + "s.csv"]:
            if cand in tables:
                return cand
            if cand.lower() in low:
                return low[cand.lower()]
        return None

    def _read_table(self, name, columns=None):
        resolved = self._resolve_table(name) or name
        if columns:
            seen = list(dict.fromkeys(columns))
            cols = ", ".join([f'"{c}"' for c in seen])
            df = pd.read_sql_query(f'SELECT {cols} FROM "{resolved}"', self.db)
        else:
            df = pd.read_sql_query(f'SELECT * FROM "{resolved}"', self.db)
        return self._normalize_numeric(df)

    def _normalize_numeric(self, df):
        """Numeric columns read back from sqlite as floats render '2070.0'.
        Convert to clean strings: 2070.0 -> '2070', 12.5 -> '12.5', NaN -> ''."""
        for c in df.columns:
            if pd.api.types.is_float_dtype(df[c]) or pd.api.types.is_integer_dtype(df[c]):
                def f(v):
                    if pd.isna(v):
                        return ""
                    fv = float(v)
                    return str(int(fv)) if fv.is_integer() else str(v)
                df[c] = df[c].map(f)
        return df

    def _comma_join(self, series):
        seen, out = set(), []
        for v in series.dropna().astype(str):
            v = v.strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return ",".join(out)

    def _val(self, x):
        if x is None:
            return ""
        s = str(x).strip()
        if s.lower() == "nan":
            return ""
        if re.fullmatch(r"-?\d+\.0+", s):
            s = s.split(".")[0]
        return s

    def _format_address_row(self, unit, st_no, st_name, suburb, state, postcode):
        unit, st_no, st_name = self._val(unit), self._val(st_no), self._val(st_name)
        suburb, state, postcode = self._val(suburb), self._val(state), self._val(postcode)
        street_no = " ".join(p for p in [unit, st_no] if p)
        line1 = " ".join(p for p in [street_no, st_name] if p)
        statepc = " ".join(p for p in [state, postcode] if p)
        return ", ".join(p for p in [line1, suburb, statepc] if p)

    def _address_series(self, df):
        return df.apply(lambda r: self._format_address_row(
            r.get("unit_number"), r.get("street_number"), r.get("street_name"),
            r.get("suburb"), r.get("state"), r.get("postcode")), axis=1)

    def _parse_ts(self, s):
        """{ts '2016-05-28 15:31:00'} or 2016-05-28[ 15:31[:00]] -> datetime."""
        if s is None:
            return None
        s = str(s)
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", s)
        if m:
            y, mo, d, hh, mm, ss = m.groups()
            return datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0))
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            y, mo, d = m.groups()
            return datetime(int(y), int(mo), int(d))
        return None

    def _parse_clock(self, s):
        """'11:00AM' / '10:30am' / '9:45 AM' -> (hour24, minute) or None."""
        if s is None:
            return None
        t = str(s).strip().upper().replace(" ", "")
        m = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", t)
        if not m:
            return None
        hh, mm, ap = int(m.group(1)), int(m.group(2)), m.group(3)
        if ap == "PM" and hh != 12:
            hh += 12
        elif ap == "AM" and hh == 12:
            hh = 0
        return (hh % 24, mm)

    def _fmt_dt(self, dt):
        """datetime -> '4/03/2016 8:00 AM' (day-first by default). Built manually
        to stay correct on Windows (no %-I / %#I dependency)."""
        if dt is None:
            return ""
        hour12 = dt.hour % 12 or 12
        ampm = "AM" if dt.hour < 12 else "PM"
        if INSPECTION_DAYFIRST:
            date = f"{dt.day}/{dt.month:02d}/{dt.year}"
        else:
            date = f"{dt.month}/{dt.day:02d}/{dt.year}"
        return f"{date} {hour12}:{dt.minute:02d} {ampm}"

    def _build_inspection_dates(self, base_df):
        """Returns (start_list, end_list) aligned to base_df rows.
        Match opens on (listing_id, calendar date of inspection_date):
          - match    -> date + opens.inspection_start_time / + end_time
          - no match -> inspections.inspection_date (with time) / + 15 min
        """
        omap = {}
        try:
            opens = self._read_table("opens.csv")
            for _, r in opens.iterrows():
                dt = self._parse_ts(r.get("inspection_date"))
                if dt is None:
                    continue
                key = (str(r.get("listing_id", "")).strip(), dt.date().isoformat())
                if key not in omap:
                    omap[key] = (r.get("inspection_start_time", ""),
                                 r.get("inspection_end_time", ""))
        except Exception as e:
            self.log(f"[{self.engine.job_id}] opens.csv unavailable for inspection dates: {e}")

        starts, ends = [], []
        for _, r in base_df.iterrows():
            dt = self._parse_ts(r.get("inspection_date"))
            if dt is None:
                starts.append("")
                ends.append("")
                continue
            key = (str(r.get("listing_id", "")).strip(), dt.date().isoformat())
            if key in omap:
                st, et = omap[key]
                st_t, et_t = self._parse_clock(st), self._parse_clock(et)
                sdt = dt.replace(hour=st_t[0], minute=st_t[1], second=0) if st_t else dt
                edt = dt.replace(hour=et_t[0], minute=et_t[1], second=0) if et_t else dt
            else:
                sdt = dt
                # Midnight means no real time was recorded -> default to 8:00 AM
                if dt.hour == 0 and dt.minute == 0:
                    sdt = dt.replace(hour=8, minute=0, second=0)
                edt = sdt + timedelta(minutes=15)
            starts.append(self._fmt_dt(sdt))
            ends.append(self._fmt_dt(edt))
        return starts, ends

    def _listing_status(self, base_df, id_col="listing_id"):
        """Resolve listing_status for each base row by matching its listing_id
        against the listings table id. Returns a base-length string Series."""
        if id_col not in base_df.columns:
            return pd.Series([""] * len(base_df), index=base_df.index)
        try:
            ldf = self._read_table("listings.csv", ["id", "listing_status"])
            ldf = ldf.drop_duplicates("id", keep="first")
            merged = base_df[[id_col]].merge(ldf, left_on=id_col, right_on="id", how="left")
            return merged["listing_status"].fillna("").reset_index(drop=True)
        except Exception as e:
            self.log(f"[{self.engine.job_id}] listing_status lookup failed: {e}")
            return pd.Series([""] * len(base_df), index=base_df.index)

    @staticmethod
    def _is_empty_col_note(n):
        n = n.lower()
        return "no cell value" in n or "no need to add cell value" in n

    @staticmethod
    def _wants_expand(n):
        # Only these phrasings mean "duplicate rows for each split (_c2, _c3...)".
        # Negated notes like "no need to include c2 onwards" must NOT match.
        n = n.lower()
        return ("1 to many" in n) or ("include those split" in n)

    # ------------------------------------------------------------------ #
    # Entry point (wrapped so one bad group can't abort the whole job)
    # ------------------------------------------------------------------ #
    def process_group(self, group_name, rules):
        try:
            self._process_group_impl(group_name, rules)
        except Exception as e:
            import traceback
            self.log(f"[{self.engine.job_id}] ERROR in group '{group_name}': {e}")
            self.log(traceback.format_exc())

    def _process_group_impl(self, group_name, rules):
        self.log(f"[{self.engine.job_id}] Initializing Zenu schema transformations for Matrix: {group_name}")

        source_files = [r["sources"][0]["file"] for r in rules
                        if r.get("sources") and "file" in r["sources"][0]]
        if not source_files:
            self.log(f"[{self.engine.job_id}] No valid base sources for {group_name}. Skipping.")
            return

        base_file = max(set(source_files), key=source_files.count)
        self.log(f"[{self.engine.job_id}] Primary source node locked: {base_file}")

        try:
            base_df = self._read_table(base_file).reset_index(drop=True)
        except Exception as e:
            self.log(f"[{self.engine.job_id}] FAILED to load {base_file}. Error: {e}")
            return

        # ---- Row filter: "only include those without IDs on <cols>" -------- #
        filter_cols = set()
        for rule in rules:
            n = rule.get("notes", "")
            if "only include those without" in n.lower():
                for c in base_df.columns:
                    if re.search(r"\b" + re.escape(c) + r"\b", n, re.IGNORECASE):
                        filter_cols.add(c)
        if filter_cols:
            mask = pd.Series(True, index=base_df.index)
            for c in filter_cols:
                col = base_df[c].astype(str).str.strip().str.lower()
                mask &= base_df[c].isna() | col.isin(["", "nan", "none"])
            kept = base_df[mask].reset_index(drop=True)
            self.log(f"[{self.engine.job_id}] Filter on {sorted(filter_cols)}: kept {len(kept)}/{len(base_df)} rows.")
            base_df = kept

        # ---- deleted_date exclusion (Prospect/Appraisal or any group whose
        #      notes mention deleted_date e.g. Tasks, Enquiries) -------------- #
        notes_mention_deleted = any("deleted_date" in r.get("notes", "").lower() for r in rules)
        if (group_name in ("Prospect", "Appraisal") or notes_mention_deleted) \
                and "deleted_date" in base_df.columns:
            before = len(base_df)
            val = base_df["deleted_date"].astype(str).str.strip().str.lower()
            keep = base_df["deleted_date"].isna() | val.isin(["", "nan", "none"])
            base_df = base_df[keep].reset_index(drop=True)
            self.log(f"[{self.engine.job_id}] Excluded deleted_date rows: kept {len(base_df)}/{before}.")

        out_df = pd.DataFrame(index=base_df.index)
        deferred_expansions = []

        def lookup_base_length(source_col, lkp, kind):
            tfile, mkey = lkp["targetFile"], lkp["matchKey"]
            fields = lkp.get("extractFields", [])
            lkp_df = self._read_table(tfile, [mkey] + fields)
            first = lkp_df.drop_duplicates(mkey, keep="first")
            merged = base_df[[source_col]].merge(
                first, left_on=source_col, right_on=mkey, how="left")
            if kind == "address":
                return self._address_series(merged).values
            if kind == "name":
                s = (merged[fields[0]].fillna("").astype(str) + " "
                     + merged[fields[1]].fillna("").astype(str))
                return self._trim_clean(s).values
            return merged[fields[0]].fillna("").values

        # -------------------------------------------------------------- #
        # Execute rules
        # -------------------------------------------------------------- #
        for rule in rules:
            target = rule["targetField"]
            action = rule.get("action", "direct")
            notes = rule.get("notes", "").lower()

            # header-only column (no values)
            if self._is_empty_col_note(notes):
                out_df[target] = ""
                continue

            if action == "direct":
                col = rule["sources"][0]["field"]
                out_df[target] = base_df[col] if col in base_df.columns else ""

            elif action == "static":
                out_df[target] = rule.get("valueExpression", "")

            elif action == "lookup":
                # "property status" maps listing_id -> listings.listing_status
                if target.strip().lower() == "property status":
                    out_df[target] = self._listing_status(base_df, "listing_id").values
                    continue

                source_col = rule["sources"][0]["field"]
                if source_col not in base_df.columns:
                    out_df[target] = ""
                    continue
                lkp = rule.get("lookupConfig", [{}])[0]
                fields = lkp.get("extractFields", [])
                if not fields:
                    out_df[target] = ""
                    continue

                is_aggregate = ("combine the data" in notes and "comma" in notes)
                is_address = (len(fields) >= 2 and "street_name" in fields and "suburb" in fields)
                is_name = (len(fields) >= 2 and not is_address)

                try:
                    if is_aggregate:
                        f = fields[0]
                        lkp_df = self._read_table(lkp["targetFile"], [lkp["matchKey"], f])
                        agg = (lkp_df.groupby(lkp["matchKey"])[f]
                               .apply(self._comma_join).reset_index(name="__agg"))
                        merged = base_df[[source_col]].merge(
                            agg, left_on=source_col, right_on=lkp["matchKey"], how="left")
                        out_df[target] = merged["__agg"].fillna("").values
                    elif is_address:
                        out_df[target] = lookup_base_length(source_col, lkp, "address")
                    elif is_name:
                        out_df[target] = lookup_base_length(source_col, lkp, "name")
                    elif self._wants_expand(notes) and group_name not in NO_EXPAND_GROUPS:
                        out_df[target] = ""
                        jc = f"__join__{target}"
                        out_df[jc] = base_df[source_col].values
                        deferred_expansions.append({
                            "target": target, "join_col": jc,
                            "target_file": lkp["targetFile"],
                            "match_key": lkp["matchKey"], "field": fields[0]})
                    else:
                        out_df[target] = lookup_base_length(source_col, lkp, "single")
                except Exception as e:
                    fb = fields[0] if fields else None
                    if fb and fb in base_df.columns:
                        out_df[target] = base_df[fb].values  # JSON lookup target wrong; field is on base
                        self.log(f"[{self.engine.job_id}] {target}: lookup failed, using base column '{fb}'.")
                    else:
                        self.log(f"[{self.engine.job_id}] Lookup failed for {target}: {e}")
                        out_df[target] = ""

            elif action == "concat":
                if target == "property_full_address":
                    out_df[target] = self._address_series(base_df)
                elif "contact_partnership_id" in target:
                    cid = out_df["contact_identifier"] if "contact_identifier" in out_df.columns \
                        else base_df.get("contact_id", "")
                    pid = out_df["contact_partner_identifier"] if "contact_partner_identifier" in out_df.columns \
                        else base_df.get("related_contact_id", "")
                    out_df[target] = (cid.fillna("").astype(str) + "_"
                                      + pid.fillna("").astype(str) + "_r")
                elif target == "enquiry_identifier":
                    out_df[target] = base_df.get("id", "").astype(str) + "_E"
                else:
                    out_df[target] = ""

        # -------------------------------------------------------------- #
        # POST-PROCESSING (base-length, before row expansion)
        # -------------------------------------------------------------- #
        criteria_cols = [c for c in out_df.columns if not c.startswith("__join__")]
        out_df = self.clean_empty_markers(out_df, criteria_cols)
        for col in criteria_cols:
            if pd.api.types.is_object_dtype(out_df[col]) or pd.api.types.is_string_dtype(out_df[col]):
                out_df[col] = out_df[col].apply(self.decode_base64_custom)

        for rule in rules:
            n = rule.get("notes", "").lower()
            raw = rule.get("notes", "")
            tgt = rule["targetField"]
            action = rule.get("action", "direct")
            if tgt not in out_df.columns:
                continue

            # timestamp -> date
            if "{ts" in n or ("timestamp" in n and "remove" in n):
                out_df[tgt] = out_df[tgt].astype(str).str.extract(r"\{ts '(\d{4}-\d{2}-\d{2})")[0].fillna("")

            # Squaremeter -> SQM
            if "squaremeter convert" in n or ("squaremeter" in n and "convert" in n):
                out_df[tgt] = out_df[tgt].replace(r"(?i)^\s*square\s*meter\s*$", "SQM", regex=True)

            # trim & clean
            if "trim and clean" in n and tgt != "contact_notes":
                out_df[tgt] = self._trim_clean(out_df[tgt])

            # identifier suffix (_T, _I, _E from notes like "47199_T")
            if action == "direct" and re.search(r"(look like|sample|concatenate|add _)", n):
                mm = re.search(r"_([A-Za-z])\b", raw)
                if mm:
                    suf = mm.group(1).upper()
                    out_df[tgt] = out_df[tgt].fillna("").astype(str).apply(
                        lambda v: f"{v}_{suf}" if v != "" else v)

        # Unwrap any leftover {ts 'YYYY-MM-DD HH:MM:SS'} -> inner value
        for c in out_df.columns:
            if c.startswith("__join__"):
                continue
            if pd.api.types.is_object_dtype(out_df[c]) or pd.api.types.is_string_dtype(out_df[c]):
                out_df[c] = out_df[c].astype(str).str.replace(
                    r"\{ts '([^']*)'\}", r"\1", regex=True).replace("nan", "")

        # land unit default (Contact Requirement)
        if "contact_criteria_land_unit" in out_df.columns:
            lf = out_df.get("contact_criteria_land_from", pd.Series("", index=out_df.index)).fillna("").astype(str).str.strip()
            lt = out_df.get("contact_criteria_land_to", pd.Series("", index=out_df.index)).fillna("").astype(str).str.strip()
            unit = out_df["contact_criteria_land_unit"].fillna("").astype(str).str.strip()
            has_land = (lf != "") | (lt != "")
            out_df.loc[has_land & (unit == ""), "contact_criteria_land_unit"] = "SQM"

        # value maps (Inspection)
        if "inspection_is_private" in out_df.columns:
            v = out_df["inspection_is_private"].fillna("").astype(str).str.strip()
            out_df["inspection_is_private"] = ["TRUE" if x == "1" else "FALSE" for x in v]
        if "inspection_is_interested" in out_df.columns:
            v = out_df["inspection_is_interested"].fillna("").astype(str).str.strip()
            mp = {"0": "NO", "1": "YES", "2": "MAYBE"}
            out_df["inspection_is_interested"] = [mp.get(x, "") for x in v]

        # Inspection: start/end dates (matched on opens) + N/A notes default
        if group_name == "Inspection":
            if len(base_df) == len(out_df):
                starts, ends = self._build_inspection_dates(base_df)
                out_df["inspection_start_date"] = starts
                out_df["inspection_end_date"] = ends
            if "inspection_notes" in out_df.columns:
                out_df["inspection_notes"] = out_df["inspection_notes"].fillna("").astype(str).apply(
                    lambda v: "N/A" if v.strip() == "" else v)


        # contact_notes composition (Notes / Communication)
        for rule in rules:
            if rule["targetField"] != "contact_notes":
                continue
            n = rule.get("notes", "").lower()
            note_txt = self._trim_clean(out_df["contact_notes"]) if "contact_notes" in out_df.columns else pd.Series([""] * len(out_df))
            addr = out_df["Property Address"].fillna("").astype(str) if "Property Address" in out_df.columns else pd.Series([""] * len(out_df))
            if "textjoin" in n and "property address" in n:
                out_df["contact_notes"] = [(f"Property: {a} - {t}" if a else t) for a, t in zip(addr, note_txt)]
            elif "concatenate these fields" in n:
                ctype = self._trim_clean(base_df["communication_type"]) if "communication_type" in base_df.columns else pd.Series([""] * len(out_df))
                out_df["contact_notes"] = [" - ".join(p for p in [c, a, t] if p) for c, a, t in zip(ctype, addr, note_txt)]

        # ---- status-based row filters + property_identifier prefixes ------- #
        status_lower = None
        if group_name in ("Contact Property Relationship", "Vendor", "Tasks", "Enquiries"):
            status_lower = self._listing_status(base_df, "listing_id").str.strip().str.lower()

        if group_name == "Contact Property Relationship" and "property_identifier" in out_df.columns:
            keep = status_lower.isin(EARLY_STATUSES)
            lid = out_df["property_identifier"].fillna("").astype(str)
            out_df["property_identifier"] = [
                ("" if i == "" else (f"Pr_{i}" if s == "property" else f"Appr_{i}"))
                for s, i in zip(status_lower, lid)]
            out_df = out_df[keep.values].reset_index(drop=True)
            self.log(f"[{self.engine.job_id}] CPR status filter -> {len(out_df)} rows.")

        elif group_name == "Vendor":
            keep = ~status_lower.isin(EARLY_STATUSES)
            out_df = out_df[keep.values].reset_index(drop=True)
            self.log(f"[{self.engine.job_id}] Vendor status filter -> {len(out_df)} rows.")

        elif group_name in ("Tasks", "Enquiries") and "property_identifier" in out_df.columns:
            lid = out_df["property_identifier"].fillna("").astype(str)
            out_df["property_identifier"] = [
                ("" if i == "" else
                 (f"Appr_{i}" if s in APPR_STATUSES else (f"Pr_{i}" if s == "prospect" else i)))
                for s, i in zip(status_lower, lid)]

        # Buyer: drop cancelled
        if group_name == "Buyer" and "property status" in out_df.columns:
            sl = out_df["property status"].fillna("").astype(str).str.strip().str.lower()
            keep = ~sl.isin({"contract cancelled", "lease cancelled", "sale cancelled"})
            out_df = out_df[keep.values].reset_index(drop=True)
            self.log(f"[{self.engine.job_id}] Buyer cancelled filter -> {len(out_df)} rows.")

        # Include in Import File?
        if "Include in Import File?" in out_df.columns:
            if group_name == "Tasks":
                st = self._listing_status(base_df, "listing_id").str.strip().str.lower()
                # base_df may have been filtered above; align by recomputing on current out_df length
                if len(st) != len(out_df):
                    st = pd.Series([""] * len(out_df))
                pid = out_df["property_identifier"].fillna("").astype(str) if "property_identifier" in out_df.columns else pd.Series([""] * len(out_df))
                out_df["Include in Import File?"] = [
                    "Initial Import" if (s in EARLY_STATUSES or p == "") else "REA Import"
                    for s, p in zip(st, pid)]
            elif group_name == "Enquiries":
                ci = out_df["contact_identifier"].fillna("").astype(str).str.strip() if "contact_identifier" in out_df.columns else pd.Series([""] * len(out_df))
                out_df["Include in Import File?"] = ["N" if v == "" else "Y" for v in ci]

        # property_timeline_status filter + normalize (Prospect/Appraisal)
        if "property_timeline_status" in out_df.columns:
            col = out_df["property_timeline_status"].fillna("").astype(str).str.strip().str.lower()
            if group_name == "Prospect":
                out_df = out_df[col == "property"].copy()
                out_df["property_timeline_status"] = "Prospect"
            elif group_name == "Appraisal":
                out_df = out_df[col.isin(APPR_STATUSES)].copy()
                out_df["property_timeline_status"] = "Appraisal"
            self.log(f"[{self.engine.job_id}] {group_name} status filter -> {len(out_df)} rows.")

        # -------------------------------------------------------------- #
        # ROW EXPANSION: 1-to-many identifier lookups
        # -------------------------------------------------------------- #
        for exp in deferred_expansions:
            target = exp["target"]
            try:
                id_map = self._read_table(exp["target_file"], [exp["match_key"], exp["field"]])
                id_map = id_map.rename(columns={exp["field"]: target})[[exp["match_key"], target]]
                if not self.expand_one_to_many:
                    id_map = id_map.drop_duplicates(exp["match_key"], keep="first")
                out_df = out_df.drop(columns=[target])
                out_df = out_df.merge(id_map, left_on=exp["join_col"],
                                      right_on=exp["match_key"], how="left")
                out_df[target] = out_df[target].fillna("")
                drop = [exp["join_col"]]
                if exp["match_key"] != target:
                    drop.append(exp["match_key"])
                out_df = out_df.drop(columns=[c for c in drop if c in out_df.columns])
            except Exception as e:
                self.log(f"[{self.engine.job_id}] Expansion failed for {target}: {e}")
                out_df[target] = ""
                out_df = out_df.drop(columns=[c for c in [exp["join_col"]] if c in out_df.columns])

        # simple property_identifier prefix (Prospect/Appraisal) - read the
        # prefix straight from the rule note ("Add Appr_{id}") so it always
        # matches the JSON; fall back to IDENTIFIER_PREFIX if the note is absent.
        if "property_identifier" in out_df.columns and group_name in IDENTIFIER_PREFIX:
            pref = IDENTIFIER_PREFIX[group_name]
            for r in rules:
                if r["targetField"] == "property_identifier":
                    m = re.search(r"add\s+([A-Za-z]+_)\s*\{?id\}?", r.get("notes", ""), re.IGNORECASE)
                    if m:
                        pref = m.group(1)
                    break
            out_df["property_identifier"] = out_df["property_identifier"].apply(
                lambda x: f"{pref}{x}" if pd.notna(x) and str(x) != "" else x)

        # source id onto Note / Communication outputs
        if group_name in NOTE_GROUPS and "id" in base_df.columns and len(base_df) == len(out_df):
            out_df.insert(0, "id", base_df["id"].values)

        # Reorder to JSON schema, drop temp columns
        ordered = [r["targetField"] for r in rules]
        if group_name in NOTE_GROUPS and "id" in out_df.columns:
            ordered = ["id"] + ordered
        out_df = out_df[[c for c in ordered if c in out_df.columns]]

        self.export_data(group_name, out_df)

    def _global_clean(self, df):
        """Final pass over every output cell: normalize quotes, strip non-ASCII
        and disallowed characters, collapse spaces, and blank out None/nan."""
        allowed = re.compile(r"[^a-zA-Z0-9 ,\-.:;{}\[\]_&'\\/<>&%+=@#!\$^\*()?\r\n]+")

        def clean(val):
            if pd.isna(val):
                return ""
            text = str(val)
            if text in ("None", "nan", "NaN", "NULL", "null", "NaT"):
                return ""
            text = text.replace('"', "'")
            text = text.encode("ascii", "ignore").decode("ascii")
            text = allowed.sub("", text)
            text = re.sub(r" +", " ", text)
            return text.strip()

        for c in df.columns:
            df[c] = df[c].map(clean)
        return df

    # ------------------------------------------------------------------ #
    # Export (xlsx)
    # ------------------------------------------------------------------ #
    def export_data(self, group_name, df):
        df = self._global_clean(df)
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", group_name.lower())
        output_file = os.path.join(self.engine.workspace, f"zenu_{safe_name}.xlsx")

        chunk_size = self.engine.chunk_size if self.engine.chunk_size and self.engine.chunk_size > 0 else len(df)
        if chunk_size <= 0 or chunk_size > EXCEL_MAX_ROWS:
            chunk_size = EXCEL_MAX_ROWS

        if len(df) <= chunk_size:
            df.to_excel(output_file, index=False, engine="openpyxl")
            self.log(f"[{self.engine.job_id}] Built {len(df)} rows for {group_name} -> {output_file}")
        else:
            num_chunks = math.ceil(len(df) / chunk_size)
            for i in range(num_chunks):
                chunk = df.iloc[i * chunk_size:(i + 1) * chunk_size]
                if chunk.empty:
                    continue
                chunk.to_excel(output_file.replace(".xlsx", f"_part{i + 1}.xlsx"),
                               index=False, engine="openpyxl")
            self.log(f"[{self.engine.job_id}] Split {len(df)} rows across {num_chunks} files for {group_name}")