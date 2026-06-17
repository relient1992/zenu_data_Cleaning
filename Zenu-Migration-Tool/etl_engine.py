import sqlite3
import pandas as pd
import json
import os

# Import our dedicated CRM processors
from crm_agentbox import AgentboxProcessor
from crm_eagle import EagleProcessor
from crm_general import GeneralProcessor
from crm_mailing import MailingProcessor
from crm_vaultre import VaultREProcessor
from crm_zenu import ZenuProcessor

class MigrationEngine:
    def __init__(self, job_id, workspace, source_crm="Agentbox", chunk_size=500000):
        self.job_id = job_id
        self.workspace = workspace
        self.source_crm = source_crm
        self.chunk_size = chunk_size
        self.db_path = os.path.join(self.workspace, f"{job_id}_raw_data.db")
        self.log_path = os.path.join(self.workspace, "process.log")
        self.conn = sqlite3.connect(self.db_path)
        
    def log(self, message):
        """Prints to terminal AND writes to the live log file for the web UI."""
        print(message)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(message + "\n")
            f.flush()
        
    def load_csvs_to_sqlite(self):
        self.log(f"[{self.job_id}] Building SQLite Database for {self.source_crm}...")
        for file in os.listdir(self.workspace):
            if file.endswith('.csv'):
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
            self.log(f"[{self.job_id}] Rex logic not yet implemented.")
            return
        else:
            self.log(f"[{self.job_id}] Unknown CRM: {self.source_crm}")
            return
            
        # Loop through the JSON and send data to the chosen processor
        for group_name, group_rules in rules.items():
            if not group_rules: continue
            self.log(f"[{self.job_id}] Processing Group: {group_name} using {self.source_crm} logic")
            processor.process_group(group_name, group_rules)

    def close(self):
        self.conn.close()
        self.log(f"[{self.job_id}] Database connection closed.")