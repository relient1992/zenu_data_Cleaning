import sqlite3
import pandas as pd
import json
import os

class MigrationEngine:
    def __init__(self, job_id, workspace, source_crm="Agentbox", chunk_size=200000):
        self.job_id = job_id
        self.workspace = workspace
        self.source_crm = source_crm  
        self.chunk_size = chunk_size # Limit rows per Excel file
        self.db_path = os.path.join(self.workspace, f"{job_id}_raw_data.db")
        self.log_path = os.path.join(self.workspace, "process.log")
        self.conn = sqlite3.connect(self.db_path)
        
    def log(self, message):
        """Prints to terminal AND writes to the live log file for the web UI."""
        print(message)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(message + "\n")
            f.flush()
            
    def check_missing_files(self, rules):
        """Scans the JSON mapping and checks if required files are missing from the folder."""
        required_files = set()
        
        # Scrape all files mentioned in the JSON
        for group_name, group_rules in rules.items():
            if not group_rules: continue
            for rule in group_rules:
                for source in rule.get("sources", []):
                    if "file" in source:
                        required_files.add(source["file"])
                for lookup in rule.get("lookupConfig", []):
                    if "targetFile" in lookup:
                        required_files.add(lookup["targetFile"])
                        
        # Check against the actual folder
        available_files = set(os.listdir(self.workspace))
        missing_files = required_files - available_files
        
        if missing_files:
            self.log(f"\n[{self.job_id}] ⚠️ WARNING: MISSING REQUIRED FILES!")
            self.log(f"[{self.job_id}] The following files are required by the JSON but missing in the folder:")
            for mf in missing_files:
                self.log(f"   ❌ {mf}")
            self.log(f"[{self.job_id}] The engine will attempt to continue, but related mappings will output blank values.\n")
        else:
            self.log(f"[{self.job_id}] ✅ All required mapping files are present.")

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
                        if "codec can't decode" not in error_msg and "utf-8" not in error_msg and "encoding" not in error_msg:
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
            
        # 1. Trigger Missing File Prompt
        self.check_missing_files(rules)
            
        # 2. Dynamic CRM Routing
        processor = None
        if self.source_crm == "Agentbox":
            try:
                from crm_agentbox import AgentboxProcessor
                processor = AgentboxProcessor(self)
            except ImportError:
                self.log(f"[{self.job_id}] CRITICAL: crm_agentbox.py not found in directory!")
                return
        elif self.source_crm == "Eagle":
            self.log(f"[{self.job_id}] Eagle logic not yet implemented. Create crm_eagle.py")
            return
        elif self.source_crm == "VaultRE":
            self.log(f"[{self.job_id}] VaultRE logic not yet implemented. Create crm_vaultre.py")
            return
        else:
            self.log(f"[{self.job_id}] Unknown CRM System: {self.source_crm}")
            return
            
        # 3. Process Groups and Export
        for group_name, group_rules in rules.items():
            if not group_rules: continue
            self.log(f"[{self.job_id}] Processing Group: {group_name} using {self.source_crm} logic")
            
            # Delegate heavy lifting to the specific CRM processor
            zenu_output = processor.process_group(group_name, group_rules)
            
            # Export and split files if needed
            if zenu_output is not None and not zenu_output.empty:
                self.export_data(group_name, zenu_output)
                
    def export_data(self, group_name, df):
        """Handles splitting massive files into safe 200k chunks for Excel."""
        safe_group_name = group_name.replace(" ", "_").replace("/", "_")
        total_rows = len(df)
        
        if self.chunk_size and total_rows > self.chunk_size:
            self.log(f"[{self.job_id}] ⚠️ Data size ({total_rows} rows) exceeds {self.chunk_size} limit. Splitting into chunks...")
            
            num_chunks = (total_rows // self.chunk_size) + 1
            for i in range(num_chunks):
                start_idx = i * self.chunk_size
                end_idx = start_idx + self.chunk_size
                chunk_df = df.iloc[start_idx:end_idx]
                
                if not chunk_df.empty:
                    output_path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final_pt{i+1}.csv")
                    chunk_df.to_csv(output_path, index=False)
                    self.log(f"[{self.job_id}] SUCCESS: Created {output_path} ({len(chunk_df)} rows)")
        else:
            # Normal Export
            output_path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final.csv")
            df.to_csv(output_path, index=False)
            self.log(f"[{self.job_id}] SUCCESS: Created {output_path} ({total_rows} rows)")

    def close(self):
        self.conn.close()
        self.log(f"[{self.job_id}] Database connection closed.")