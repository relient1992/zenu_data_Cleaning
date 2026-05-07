import pandas as pd
import sqlite3
import os
import re
import numpy as np
from collections import Counter

class GeneralProcessor:
    def __init__(self, engine):
        self.engine = engine
        self.conn = engine.conn
        self.job_id = engine.job_id
        self.workspace = engine.workspace

    def process_group(self, group_name, rules):
        self.engine.log(f"[{self.job_id}] Executing General Data Cleaning for '{group_name}'...")
        
        # 1. Dynamically determine the base file for this mapping group
        source_files = []
        for rule in rules:
            for src in rule.get("sources", []):
                file_name = src.get("file")
                # Ignore UI dummy fields when finding the primary table
                if file_name and file_name != "Custom_Dummy_Fields":
                    source_files.append(file_name)
                    
        if not source_files:
            self.engine.log(f"[{self.job_id}] Skipping {group_name}: No valid source files found in mapping.")
            return
            
        base_file = Counter(source_files).most_common(1)[0][0]
        
        # 2. Load the base data
        try:
            # Check if table exists
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing in DB.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
        except Exception as e:
            self.engine.log(f"[{self.job_id}] Error loading base table {base_file}: {e}")
            return

        zenu_output = pd.DataFrame(index=df.index)

        # 3. Process the rules dynamically
        for rule in rules:
            target_field = rule.get("targetField")
            action = rule.get("action")
            sources = rule.get("sources", [])
            
            primary_src_field = sources[0].get("field") if sources else None

            # --- DIRECT MAPPING ---
            if action == "direct":
                if primary_src_field and primary_src_field in df.columns:
                    zenu_output[target_field] = df[primary_src_field]

            # --- STATIC MAPPING ---
            elif action == "static":
                zenu_output[target_field] = rule.get("valueExpression", "")

            # --- CONCAT / FORMULA MAPPING ---
            elif action == "concat":
                expression = str(rule.get("valueExpression", ""))
                if expression:
                    def eval_concat(row):
                        res = expression
                        for src in sources:
                            var_id = f"[{src.get('varId', 'S1')}]"
                            val = str(row.get(src.get('field'), ''))
                            if val.lower() in ['nan', 'none']: val = ''
                            res = res.replace(var_id, val)
                        return res.strip()
                    zenu_output[target_field] = df.apply(eval_concat, axis=1)
                else:
                    # Fallback to direct if no expression provided
                    if primary_src_field in df.columns:
                        zenu_output[target_field] = df[primary_src_field]

            # --- LOOKUP MAPPING ---
            elif action == "lookup":
                lookup_configs = rule.get('lookupConfig', [])
                for config in lookup_configs:
                    target_file = config.get('targetFile')
                    match_key = config.get('matchKey')
                    extract_fields = config.get('extractFields', [])
                    
                    if target_file and match_key and extract_fields and primary_src_field in df.columns:
                        try:
                            extract_col = extract_fields[0]
                            lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_col}" FROM "{target_file}"', self.conn)
                            
                            # Standardize types for matching
                            lookup_df[match_key] = lookup_df[match_key].astype(str).str.replace(r'\.0$', '', regex=True)
                            source_keys = df[primary_src_field].astype(str).str.replace(r'\.0$', '', regex=True)
                            
                            mapping_dict = lookup_df.drop_duplicates(subset=[match_key]).set_index(match_key)[extract_col].to_dict()
                            zenu_output[target_field] = source_keys.map(mapping_dict)
                            
                        except Exception as lookup_err:
                            self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

        # 4. Global Data Sanitization
        def global_cleaner(val):
            if pd.isna(val): return val
            text = str(val).strip()
            if text.lower() in ['nan', 'none', 'null', '']: return pd.NA
            # Remove double spaces
            text = re.sub(r' +', ' ', text)
            return text

        for col in zenu_output.columns:
            zenu_output[col] = zenu_output[col].apply(global_cleaner)

        zenu_output = zenu_output.dropna(how='all')

        # 5. Export to CSV (with chunking support)
        safe_group_name = group_name.replace(" ", "_").replace("/", "_")
        chunk_limit = self.engine.chunk_size
        total_rows = len(zenu_output)
        
        if chunk_limit > 0 and total_rows > chunk_limit:
            self.engine.log(f"[{self.job_id}] Result size ({total_rows}) exceeds limit. Splitting into chunks of {chunk_limit}...")
            num_chunks = (total_rows // chunk_limit) + (1 if total_rows % chunk_limit != 0 else 0)
            
            for i in range(num_chunks):
                start_idx = i * chunk_limit
                end_idx = start_idx + chunk_limit
                chunk_df = zenu_output.iloc[start_idx:end_idx]
                
                if not chunk_df.empty:
                    output_path = os.path.join(self.workspace, f"Cleaned_{safe_group_name}_pt{i+1}.csv")
                    chunk_df.to_csv(output_path, index=False)
                    self.engine.log(f"[{self.job_id}] SUCCESS: Created {output_path} ({len(chunk_df)} rows)")
        else:
            output_path = os.path.join(self.workspace, f"Cleaned_{safe_group_name}.csv")
            zenu_output.to_csv(output_path, index=False)
            self.engine.log(f"[{self.job_id}] SUCCESS: Exported Cleaned Data -> {output_path}")