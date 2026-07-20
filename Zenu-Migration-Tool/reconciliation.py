import sqlite3
import pandas as pd
import os
import re
import json
from datetime import datetime

# =====================================================================
# POST-MIGRATION VALIDATION & RECONCILIATION ENGINE
# ---------------------------------------------------------------------
# Runs AFTER a migration job completes. It inspects:
#   1. The raw SQLite database  ({job_id}_raw_data.db)  -> SOURCE side
#   2. The generated output files in the workspace       -> TARGET side
#      (Cleaned_*.csv, Zenu_*_Final*.xlsx, zenu_*.xlsx,
#       Ownership_Relations_*.xlsx, including _pt / _part chunks)
#
# Checks performed:
#   - Source table row counts vs Output group row counts
#   - Empty output detection (0 rows = FAIL)
#   - Duplicate identifier detection on master entity files
#   - Orphaned relationships (contact/property identifiers that do
#     not exist in the detected master files)
#   - Column null-rate profiling (100% empty mapped columns = WARN)
#   - Date column sanity (unparseable dates with dayfirst=True)
#
# Outputs:
#   - Reconciliation_Report.xlsx   (multi-sheet, client-shareable)
#   - reconciliation_summary.json  (consumed by the web UI)
#   - Progress lines appended to process.log (visible in live log UI)
# =====================================================================

OUTPUT_PREFIXES = ("cleaned_", "zenu_", "ownership_relations_")
CHUNK_PATTERN = re.compile(r"_(?:pt|part)(\d+)$", re.IGNORECASE)

# Values treated as "empty" when profiling null rates
EMPTY_TOKENS = {"", "nan", "none", "null", "n/a", "na"}

# Filename keywords that disqualify a file from being a MASTER entity list
NON_MASTER_KEYWORDS = ("note", "relationship", "requirement", "task",
                       "enquir", "offer", "owner", "inspection", "relation")


class ReconciliationEngine:
    def __init__(self, job_id, workspace):
        self.job_id = job_id
        self.workspace = workspace
        self.db_path = os.path.join(workspace, f"{job_id}_raw_data.db")
        self.log_path = os.path.join(workspace, "process.log")
        self.report_xlsx = os.path.join(workspace, "Reconciliation_Report.xlsx")
        self.summary_json = os.path.join(workspace, "reconciliation_summary.json")

        self.issues = []          # list of dicts: severity / area / detail
        self.source_counts = []   # [{table, rows}]
        self.output_groups = {}   # base_name -> {"files": [...], "df": DataFrame}
        self.row_counts = []      # per output group
        self.column_profile = []  # per output group per column
        self.duplicates = []      # duplicate identifier findings
        self.orphans = []         # referential integrity findings
        self.date_checks = []     # date sanity findings

    # -----------------------------------------------------------------
    def log(self, message):
        print(message)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(message + "\n")
                f.flush()
        except Exception:
            pass

    def add_issue(self, severity, area, detail):
        self.issues.append({"severity": severity, "area": area, "detail": detail})

    # -----------------------------------------------------------------
    # 1. SOURCE SIDE - raw SQLite row counts
    # -----------------------------------------------------------------
    def scan_source(self):
        self.log(f"[{self.job_id}] [RECON] Scanning source SQLite database...")
        if not os.path.exists(self.db_path):
            self.add_issue("WARN", "Source",
                           "Raw SQLite database not found - source vs target counts unavailable.")
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            for t in tables:
                try:
                    cursor.execute(f'SELECT COUNT(*) FROM "{t}"')
                    cnt = cursor.fetchone()[0]
                    self.source_counts.append({"table": t, "rows": cnt})
                except Exception as e:
                    self.add_issue("WARN", "Source", f"Could not count table {t}: {e}")
            conn.close()
            self.log(f"[{self.job_id}] [RECON] Found {len(self.source_counts)} source tables.")
        except Exception as e:
            self.add_issue("WARN", "Source", f"Failed to open raw database: {e}")

    # -----------------------------------------------------------------
    # 2. TARGET SIDE - discover and load output files
    # -----------------------------------------------------------------
    def discover_outputs(self):
        self.log(f"[{self.job_id}] [RECON] Discovering migration output files...")
        for file in sorted(os.listdir(self.workspace)):
            low = file.lower()
            if not low.endswith((".csv", ".xlsx")):
                continue
            if not low.startswith(OUTPUT_PREFIXES):
                continue
            if low.startswith("reconciliation_report"):
                continue

            base, _ext = os.path.splitext(file)
            # Group chunked outputs (_pt1 / _part2) under one logical name
            group = CHUNK_PATTERN.sub("", base)
            self.output_groups.setdefault(group, {"files": [], "df": None})
            self.output_groups[group]["files"].append(file)

        if not self.output_groups:
            self.add_issue("FAIL", "Outputs",
                           "No migration output files found in this workspace.")
        else:
            self.log(f"[{self.job_id}] [RECON] Found {len(self.output_groups)} output groups.")

    def load_outputs(self):
        for group, info in self.output_groups.items():
            frames = []
            for file in info["files"]:
                path = os.path.join(self.workspace, file)
                try:
                    if file.lower().endswith(".csv"):
                        frames.append(pd.read_csv(path, low_memory=False, dtype=str,
                                                  keep_default_na=False))
                    else:
                        frames.append(pd.read_excel(path, engine="openpyxl", dtype=str))
                except Exception as e:
                    self.add_issue("WARN", "Outputs", f"Could not read {file}: {e}")
            if frames:
                df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
                df = df.fillna("")
                info["df"] = df

    # -----------------------------------------------------------------
    # 3. ROW COUNTS + EMPTY OUTPUT CHECK
    # -----------------------------------------------------------------
    def check_row_counts(self):
        for group, info in self.output_groups.items():
            df = info["df"]
            rows = 0 if df is None else len(df)
            cols = 0 if df is None else len(df.columns)
            self.row_counts.append({
                "output_group": group,
                "files": len(info["files"]),
                "rows": rows,
                "columns": cols
            })
            if rows == 0:
                self.add_issue("FAIL", "Row Counts",
                               f"Output '{group}' produced 0 rows.")

    # -----------------------------------------------------------------
    # 4. COLUMN NULL-RATE PROFILING
    # -----------------------------------------------------------------
    @staticmethod
    def _is_empty_series(s):
        return s.astype(str).str.strip().str.lower().isin(EMPTY_TOKENS)

    def check_column_profile(self):
        for group, info in self.output_groups.items():
            df = info["df"]
            if df is None or df.empty:
                continue
            total = len(df)
            for col in df.columns:
                empty_cnt = int(self._is_empty_series(df[col]).sum())
                pct = round(empty_cnt / total * 100, 1) if total else 0.0
                self.column_profile.append({
                    "output_group": group,
                    "column": col,
                    "rows": total,
                    "empty": empty_cnt,
                    "empty_pct": pct
                })
                if pct == 100.0:
                    self.add_issue("WARN", "Column Profile",
                                   f"'{group}' column '{col}' is 100% empty - "
                                   f"mapping may not have produced data.")

    # -----------------------------------------------------------------
    # 5. MASTER DETECTION + DUPLICATE IDENTIFIERS
    # -----------------------------------------------------------------
    def _detect_master(self, id_col, name_keywords):
        """Pick the master output group for an identifier column.
        Preference: filename keyword match that is NOT a relationship-type
        file. Fallback: the group with the most unique identifier values."""
        candidates = []
        for group, info in self.output_groups.items():
            df = info["df"]
            if df is None or id_col not in df.columns:
                continue
            low = group.lower()
            is_named = any(k in low for k in name_keywords)
            is_non_master = any(k in low for k in NON_MASTER_KEYWORDS)
            uniq = df[id_col][~self._is_empty_series(df[id_col])].nunique()
            candidates.append((group, is_named and not is_non_master, uniq))

        if not candidates:
            return None
        named = [c for c in candidates if c[1]]
        pool = named if named else candidates
        pool.sort(key=lambda c: c[2], reverse=True)
        return pool[0][0]

    def _id_set(self, group, id_col):
        df = self.output_groups[group]["df"]
        s = df[id_col].astype(str).str.strip()
        s = s[~self._is_empty_series(s)]
        return set(s)

    def check_duplicates_and_orphans(self):
        checks = [
            ("contact_identifier", ("contact",)),
            ("property_identifier", ("prospect", "propert", "mailing")),
        ]
        for id_col, keywords in checks:
            master = self._detect_master(id_col, keywords)
            if master is None:
                continue
            self.log(f"[{self.job_id}] [RECON] Master for {id_col}: {master}")

            # --- duplicates within the master file ---
            mdf = self.output_groups[master]["df"]
            s = mdf[id_col].astype(str).str.strip()
            s = s[~self._is_empty_series(s)]
            dup_counts = s.value_counts()
            dups = dup_counts[dup_counts > 1]
            if len(dups) > 0:
                sample = ", ".join(map(str, dups.index[:10]))
                self.duplicates.append({
                    "output_group": master,
                    "identifier": id_col,
                    "duplicate_values": int(len(dups)),
                    "extra_rows": int((dups - 1).sum()),
                    "sample": sample
                })
                self.add_issue("FAIL", "Duplicates",
                               f"'{master}' has {len(dups)} duplicated {id_col} values "
                               f"(e.g. {sample[:120]}).")

            # --- orphans in every other output referencing this id ---
            master_ids = self._id_set(master, id_col)
            for group, info in self.output_groups.items():
                if group == master:
                    continue
                df = info["df"]
                if df is None or id_col not in df.columns or df.empty:
                    continue
                refs = df[id_col].astype(str).str.strip()
                refs = refs[~self._is_empty_series(refs)]
                missing = refs[~refs.isin(master_ids)]
                if len(missing) > 0:
                    uniq_missing = missing.unique()
                    sample = ", ".join(map(str, uniq_missing[:10]))
                    self.orphans.append({
                        "output_group": group,
                        "identifier": id_col,
                        "master": master,
                        "orphan_rows": int(len(missing)),
                        "orphan_unique": int(len(uniq_missing)),
                        "sample": sample
                    })
                    self.add_issue("FAIL", "Referential Integrity",
                                   f"'{group}' has {len(missing)} rows whose {id_col} "
                                   f"does not exist in master '{master}' "
                                   f"(e.g. {sample[:120]}).")

    # -----------------------------------------------------------------
    # 6. DATE SANITY (Australian day-first)
    # -----------------------------------------------------------------
    def check_dates(self):
        for group, info in self.output_groups.items():
            df = info["df"]
            if df is None or df.empty:
                continue
            date_cols = [c for c in df.columns if "date" in c.lower()]
            for col in date_cols:
                s = df[col].astype(str).str.strip()
                s = s[~self._is_empty_series(s)]
                if s.empty:
                    continue
                sample = s.head(2000)
                parsed = pd.to_datetime(sample, dayfirst=True, errors="coerce")
                bad = int(parsed.isna().sum())
                pct = round(bad / len(sample) * 100, 1)
                self.date_checks.append({
                    "output_group": group,
                    "column": col,
                    "checked": int(len(sample)),
                    "unparseable": bad,
                    "unparseable_pct": pct
                })
                if pct > 5.0:
                    self.add_issue("WARN", "Dates",
                                   f"'{group}' column '{col}': {pct}% of sampled values "
                                   f"could not be parsed as day-first dates.")

    # -----------------------------------------------------------------
    # 7. REPORT GENERATION
    # -----------------------------------------------------------------
    def _overall_status(self):
        sev = {i["severity"] for i in self.issues}
        if "FAIL" in sev:
            return "FAIL"
        if "WARN" in sev:
            return "WARN"
        return "PASS"

    def write_excel_report(self):
        self.log(f"[{self.job_id}] [RECON] Writing Excel reconciliation report...")
        with pd.ExcelWriter(self.report_xlsx, engine="openpyxl") as writer:
            # Summary
            summary_rows = [
                {"Item": "Job ID", "Value": self.job_id},
                {"Item": "Generated", "Value": datetime.now().strftime("%d/%m/%Y %H:%M:%S")},
                {"Item": "Overall Status", "Value": self._overall_status()},
                {"Item": "Source Tables", "Value": len(self.source_counts)},
                {"Item": "Output Groups", "Value": len(self.output_groups)},
                {"Item": "Failures", "Value": sum(1 for i in self.issues if i["severity"] == "FAIL")},
                {"Item": "Warnings", "Value": sum(1 for i in self.issues if i["severity"] == "WARN")},
            ]
            pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Summary", index=False)

            issues_df = pd.DataFrame(self.issues) if self.issues else \
                pd.DataFrame([{"severity": "PASS", "area": "-",
                               "detail": "No issues detected."}])
            issues_df.to_excel(writer, sheet_name="Issues", index=False)

            pd.DataFrame(self.source_counts).to_excel(
                writer, sheet_name="Source Row Counts", index=False)
            pd.DataFrame(self.row_counts).to_excel(
                writer, sheet_name="Output Row Counts", index=False)
            pd.DataFrame(self.column_profile).to_excel(
                writer, sheet_name="Column Profile", index=False)
            if self.duplicates:
                pd.DataFrame(self.duplicates).to_excel(
                    writer, sheet_name="Duplicates", index=False)
            if self.orphans:
                pd.DataFrame(self.orphans).to_excel(
                    writer, sheet_name="Orphans", index=False)
            if self.date_checks:
                pd.DataFrame(self.date_checks).to_excel(
                    writer, sheet_name="Date Checks", index=False)
        self.log(f"[{self.job_id}] [RECON] SUCCESS: Report -> {self.report_xlsx}")

    def write_json_summary(self):
        summary = {
            "status": "complete",
            "job_id": self.job_id,
            "generated": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "overall": self._overall_status(),
            "fail_count": sum(1 for i in self.issues if i["severity"] == "FAIL"),
            "warn_count": sum(1 for i in self.issues if i["severity"] == "WARN"),
            "issues": self.issues,
            "source_counts": self.source_counts,
            "row_counts": self.row_counts,
            "duplicates": self.duplicates,
            "orphans": self.orphans,
            "date_checks": self.date_checks,
            # keep the JSON light: only flag-worthy columns
            "empty_columns": [c for c in self.column_profile if c["empty_pct"] == 100.0],
        }
        with open(self.summary_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

    # -----------------------------------------------------------------
    def run(self):
        self.log(f"[{self.job_id}] [RECON] ===== Starting Post-Migration Reconciliation =====")
        try:
            self.scan_source()
            self.discover_outputs()
            self.load_outputs()
            self.check_row_counts()
            self.check_column_profile()
            self.check_duplicates_and_orphans()
            self.check_dates()
            self.write_excel_report()
            self.write_json_summary()
            self.log(f"[{self.job_id}] [RECON] ===== Reconciliation Complete: "
                     f"{self._overall_status()} =====")
        except Exception as e:
            self.log(f"[{self.job_id}] [RECON] CRITICAL FAILURE: {e}")
            with open(self.summary_json, "w", encoding="utf-8") as f:
                json.dump({"status": "error", "job_id": self.job_id,
                           "message": str(e)}, f, indent=2)