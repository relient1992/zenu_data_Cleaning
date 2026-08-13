import sqlite3
import pandas as pd
import json
import os
import time

# Import our dedicated CRM processors
from crm_agentbox import AgentboxProcessor
from crm_eagle import EagleProcessor
from crm_general import GeneralProcessor
from crm_mailing import MailingProcessor
from crm_vaultre import VaultREProcessor
from crm_zenu import ZenuProcessor
from crm_rex import RexProcessor

class MigrationEngine:
    def __init__(self, job_id, workspace, source_crm="Agentbox", chunk_size=500000):
        self.job_id = job_id
        self.workspace = workspace
        self.source_crm = source_crm
        self.chunk_size = chunk_size
        self.db_path = os.path.join(self.workspace, f"{job_id}_raw_data.db")
        self.log_path = os.path.join(self.workspace, "process.log")
        self.progress_path = os.path.join(self.workspace, "progress.json")
        self.conn = sqlite3.connect(self.db_path)

        self._started_at = time.time()
        self.set_progress(1, "Starting up", "Preparing workspace")

    def set_progress(self, percent, stage, detail=""):
        """
        Publish real progress for the web UI to poll. Never raises - progress
        reporting must not be able to break a migration.
        """
        try:
            pct = max(0.0, min(100.0, round(float(percent), 1)))
            payload = {
                "percent": pct,
                "stage": stage,
                "detail": detail,
                "elapsed_seconds": round(time.time() - self._started_at, 1),
                "done": pct >= 100,
            }
            with open(self.progress_path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception:
            pass

    def log(self, message):
        """Prints to terminal AND writes to the live log file for the web UI."""
        print(message)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(message + "\n")
            f.flush()

    def load_csvs_to_sqlite(self):
        self.log(f"[{self.job_id}] Building SQLite Database for {self.source_crm}...")

        # Phase 1 of 2 (1% -> 40%): one step per source CSV
        csv_files = sorted(f for f in os.listdir(self.workspace) if f.endswith('.csv'))
        total_files = len(csv_files) or 1
        self.set_progress(2, "Loading source data", f"0/{total_files} files")

        for file_idx, file in enumerate(csv_files):
            self.set_progress(
                2 + (file_idx / total_files) * 38,
                "Loading source data",
                f"{file} ({file_idx + 1}/{total_files})"
            )
            table_name = file
            file_path = os.path.join(self.workspace, file)
            chunksize = 50000
            
            encodings = ['utf-8-sig', 'cp1252', 'latin1']
            loaded = False
            
            for enc in encodings:
                if loaded: break
                try:
                    cursor = self.conn.cursor()
                    cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                    self.conn.commit()

                    for chunk in pd.read_csv(file_path, chunksize=chunksize, low_memory=False, encoding=enc, on_bad_lines='skip'):
                        chunk.to_sql(table_name, self.conn, if_exists='append', index=False)
                    
                    self.log(f"[{self.job_id}] Loaded {file} successfully using '{enc}' encoding.")
                    loaded = True
                except Exception as e:
                    error_msg = str(e).lower()
                    if "codec can't decode" in error_msg or "utf-8" in error_msg or "encoding" in error_msg:
                        self.log(f"[{self.job_id}] '{enc}' encoding failed for {file}. Trying next...")
                    else:
                        self.log(f"[{self.job_id}] Warning: Could not load {file} - {e}")
                        break
                        
            if not loaded:
                self.log(f"[{self.job_id}] All strict encodings failed. Forcing load with error replacement for {file}...")
                try:
                    cursor = self.conn.cursor()
                    cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                    self.conn.commit()
                    for chunk in pd.read_csv(file_path, chunksize=chunksize, low_memory=False, encoding='utf-8', on_bad_lines='skip', encoding_errors='replace'):
                        chunk.to_sql(table_name, self.conn, if_exists='append', index=False)
                    self.log(f"[{self.job_id}] Loaded {file} successfully using fallback replacement mode.")
                except Exception as e:
                    self.log(f"[{self.job_id}] CRITICAL: Completely failed to load {file}. Error: {e}")

        self.set_progress(40, "Source data loaded", f"{total_files} file(s) indexed")

    def run_mapping(self, mapping_json_path):
        self.log(f"[{self.job_id}] Reading JSON Mapping Rules...")
        with open(mapping_json_path, 'r') as f:
            rules = json.load(f)
            
        # ---------------------------------------------------------
        # THE ROUTER: Select the correct CRM logic file
        # ---------------------------------------------------------
        processor = None
        if self.source_crm == "Agentbox":
            processor = AgentboxProcessor(self)
        elif self.source_crm == "Eagle":
            processor = EagleProcessor(self)
        elif self.source_crm == "VaultRE":
            processor = VaultREProcessor(self)
        elif self.source_crm == "General":
            processor = GeneralProcessor(self)
        elif self.source_crm == "MailingProspect":
            processor = MailingProcessor(self)
        elif self.source_crm == "ZenuTransfer": 
            processor = ZenuProcessor(self)
        elif self.source_crm == "Rex":
            processor = RexProcessor(self)
        else:
            self.log(f"[{self.job_id}] Unknown CRM: {self.source_crm}")
            self.set_progress(100, "Stopped", f"Unknown CRM: {self.source_crm}")
            return

        # Phase 2 of 2 (40% -> 98%): one step per mapping group
        groups = [(n, r) for n, r in rules.items() if r]
        total_groups = len(groups) or 1

        for group_idx, (group_name, group_rules) in enumerate(groups):
            self.set_progress(
                40 + (group_idx / total_groups) * 58,
                "Transforming data",
                f"{group_name} ({group_idx + 1}/{total_groups})"
            )
            self.log(f"[{self.job_id}] Processing Group: {group_name} using {self.source_crm} logic")
            processor.process_group(group_name, group_rules)

        self.set_progress(98, "Writing output files", f"{total_groups} matrix/matrices complete")

    def close(self):
        self.conn.close()
        # close() runs in process_job's `finally`, so this fires on success and
        # on failure alike - the UI never gets stuck on a half-filled bar.
        self.set_progress(100, "Complete", "All files written")
        self.log(f"[{self.job_id}] Database connection closed.")