import sqlite3
import pandas as pd
import json
import os

class MigrationEngine:
    def __init__(self, job_id, workspace):
        self.job_id = job_id
        self.workspace = workspace
        # This creates the portable SQLite file right inside the client's folder!
        self.db_path = os.path.join(self.workspace, f"{job_id}_raw_data.db")
        self.conn = sqlite3.connect(self.db_path)
        
    def load_csvs_to_sqlite(self):
        """Finds all CSVs in the workspace and streams them into SQLite tables."""
        print(f"[{self.job_id}] Building SQLite Database...")
        for file in os.listdir(self.workspace):
            if file.endswith('.csv'):
                table_name = file
                file_path = os.path.join(self.workspace, file)
                
                chunksize = 50000 
                
                # cp1252 is standard Windows encoding (highly common in Australian CRMs like Agentbox)
                encodings = ['utf-8-sig', 'cp1252', 'latin1']
                loaded = False
                
                for enc in encodings:
                    if loaded:
                        break
                    try:
                        # Safety Check: Clear table if a previous encoding attempt partially loaded before crashing
                        cursor = self.conn.cursor()
                        cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                        self.conn.commit()

                        for chunk in pd.read_csv(
                            file_path, 
                            chunksize=chunksize, 
                            low_memory=False, 
                            encoding=enc,
                            on_bad_lines='skip'
                        ):
                            chunk.to_sql(table_name, self.conn, if_exists='append', index=False)
                        
                        print(f"[{self.job_id}] Loaded {file} successfully using '{enc}' encoding.")
                        loaded = True
                        
                    except Exception as e:
                        error_msg = str(e).lower()
                        # If Pandas throws a wrapped codec error, try the next encoding in the list
                        if "codec can't decode" in error_msg or "utf-8" in error_msg or "encoding" in error_msg:
                            print(f"[{self.job_id}] '{enc}' encoding failed for {file}. Trying next...")
                        else:
                            print(f"[{self.job_id}] Warning: Could not load {file} - {e}")
                            break # This is a different error (like file missing), stop trying encodings
                            
                # Ultimate Fallback: Force load and replace completely corrupted characters with '?'
                if not loaded:
                    print(f"[{self.job_id}] All strict encodings failed. Forcing load with error replacement for {file}...")
                    try:
                        cursor = self.conn.cursor()
                        cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                        self.conn.commit()
                        
                        for chunk in pd.read_csv(
                            file_path, 
                            chunksize=chunksize, 
                            low_memory=False, 
                            encoding='utf-8',
                            on_bad_lines='skip',
                            encoding_errors='replace' # This guarantees it will not crash
                        ):
                            chunk.to_sql(table_name, self.conn, if_exists='append', index=False)
                        print(f"[{self.job_id}] Loaded {file} successfully using fallback replacement mode.")
                    except Exception as e:
                        print(f"[{self.job_id}] CRITICAL: Completely failed to load {file}. Error: {e}")

    def run_mapping(self, mapping_json_path):
        """Reads the JSON rules and builds the final Zenu output."""
        print(f"[{self.job_id}] Reading JSON Mapping Rules...")
        with open(mapping_json_path, 'r') as f:
            rules = json.load(f)
            
        # Loop through your groups (Contact, Contact_Notes, Task, etc.)
        for group_name, group_rules in rules.items():
            if not group_rules:
                continue # Skip empty groups
                
            print(f"[{self.job_id}] Processing Group: {group_name}")
            self._process_group(group_name, group_rules)
            
    def _process_group(self, group_name, rules):
        """
        Executes the JSON rules, including explicit hardcoded logic for complex transformations.
        """
        zenu_output = pd.DataFrame()
        
        # 1. Determine the Default Base Table
        source_files = [rule.get("sources", [{}])[0].get("file") for rule in rules if rule.get("sources")]
        if not source_files:
            print(f"[{self.job_id}] Skipping {group_name}: No base file found.")
            return
            
        from collections import Counter
        base_file = Counter([f for f in source_files if f]).most_common(1)[0][0]

        # ---------------------------------------------------------
        # 2. LOAD BASE DATA (With 1-to-Many Override for Requirements)
        # ---------------------------------------------------------
        if group_name == "Contact Requirements":
            try:
                # Find exact table names to avoid case-sensitivity issues
                cursor = self.conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [t[0] for t in cursor.fetchall()]
                
                clean_file = next((t for t in tables if t.lower() == 'contact_cleaned.csv'), None)
                req_file = next((t for t in tables if t.lower() == 'contact_requirement.csv'), None)
                
                if clean_file and req_file:
                    clean_df = pd.read_sql_query(f'SELECT * FROM "{clean_file}"', self.conn)
                    req_df = pd.read_sql_query(f'SELECT * FROM "{req_file}"', self.conn)
                    
                    match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'Raw ORIG CONTACT_IDENTIFIER'
                    
                    # MERGE: This duplicates the requirement row for every split contact!
                    base_df = pd.merge(
                        clean_df, 
                        req_df, 
                        left_on=match_col, 
                        right_on='contact_id', 
                        how='inner' 
                    )
                    print(f"[{self.job_id}] Successfully merged Split Contacts for Requirements.")
                else:
                    base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
            except Exception as e:
                print(f"[{self.job_id}] Error building split contact base table: {e}")
                return
        else:
            # Standard Base Loading for all other groups
            try:
                base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
            except Exception as e:
                 print(f"[{self.job_id}] Error loading base table {base_file}: {e}")
                 return

        # ---------------------------------------------------------
        # 3. PROCESS THE RULES
        # ---------------------------------------------------------
        
        # This sits OUTSIDE the loop so we only load it once
        contact_cleaned_mapping = None

        for rule in rules:
            target_field = rule.get("targetField")
            action = rule.get("action")
            sources = rule.get("sources", [])
            
            # --- EXPLICIT LOGIC OVERRIDES FOR "CONTACT REQUIREMENTS" ---
            if group_name == "Contact Requirements":
                
                if target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', base_df.get('contact_id', pd.NA))
                    continue
                    
                elif target_field == "contact_criteria_property_type":
                    raw_val = base_df.get('property_categories', pd.Series(dtype=str))
                    zenu_output[target_field] = (
                        raw_val.astype(str)
                        .str.replace(r';\s*', ',', regex=True) 
                        .str.replace('land', 'Vacant Land', case=False, regex=False) 
                        .str.replace('vacant vacant land', 'Vacant Land', case=False, regex=False) 
                        .str.strip()
                    )
                    continue
                    
                elif target_field == "contact_criteria_sale_method":
                    raw_val = base_df.get('search_type', pd.Series(dtype=str))
                    zenu_output[target_field] = raw_val.astype(str).str.title().apply(lambda x: x if x in ['Sale', 'Lease'] else pd.NA)
                    continue

                elif target_field in ["contact_criteria_bedrooms", "contact_criteria_bathrooms", "contact_criteria_carspaces"]:
                    if len(sources) >= 2:
                        s1_col = sources[0].get("field") 
                        s2_col = sources[1].get("field") 
                        val_to = pd.to_numeric(base_df.get(s1_col), errors='coerce').replace(0, pd.NA)
                        val_from = pd.to_numeric(base_df.get(s2_col), errors='coerce')
                        zenu_output[target_field] = val_to.fillna(val_from)
                    continue
                
                elif target_field in ["contact_criteria_land_from", "contact_criteria_land_to"]:
                    if len(sources) > 0:
                        s1_col = sources[0].get("field")
                        zenu_output[target_field] = pd.to_numeric(base_df.get(s1_col), errors='coerce').round().astype('Int64')
                    continue
                
                elif target_field == "contact_criteria_land_unit":
                    if len(sources) >= 2:
                        s1_col = sources[0].get("field")
                        s2_col = sources[1].get("field")
                        has_s1 = pd.to_numeric(base_df.get(s1_col), errors='coerce') > 0
                        has_s2 = pd.to_numeric(base_df.get(s2_col), errors='coerce') > 0
                        zenu_output[target_field] = (has_s1 | has_s2).map({True: 'SQM', False: pd.NA})
                    continue

            # --- EXPLICIT LOGIC OVERRIDES FOR "CONTACT RELATIONSHIP" ---
            elif group_name == "Contact Relationship":
                
                if contact_cleaned_mapping is None:
                    try:
                        cursor = self.conn.cursor()
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                        tables = [t[0] for t in cursor.fetchall()]
                        clean_file = next((t for t in tables if t.lower() == 'contact_cleaned.csv'), None)
                        
                        if clean_file:
                            clean_df = pd.read_sql_query(f'SELECT * FROM "{clean_file}"', self.conn)
                            match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'Raw ORIG CONTACT_IDENTIFIER'
                            clean_df = clean_df.drop_duplicates(subset=[match_col])
                            contact_cleaned_mapping = clean_df.set_index(match_col)['CONTACT_IDENTIFIER'].to_dict()
                        else:
                            contact_cleaned_mapping = {}
                    except Exception as e:
                        print(f"[{self.job_id}] Warning: Could not build clean mapping dict: {e}")
                        contact_cleaned_mapping = {}

                if target_field == "contact_identifier" and len(sources) > 0:
                    s1_col = sources[0].get("field")
                    zenu_output[target_field] = base_df.get(s1_col).map(contact_cleaned_mapping)
                    continue
                
                elif target_field == "contact_partner_identifier" and len(sources) > 0:
                    s1_col = sources[0].get("field")
                    zenu_output[target_field] = base_df.get(s1_col).map(contact_cleaned_mapping)
                    continue

                elif target_field == "contact_partnership_id" and len(sources) >= 2:
                    s1_col = sources[0].get("field") 
                    s2_col = sources[1].get("field")
                    
                    c1 = base_df.get(s1_col).map(contact_cleaned_mapping).astype(str)
                    c2 = base_df.get(s2_col).map(contact_cleaned_mapping).astype(str)
                    
                    valid_mask = (c1 != 'nan') & (c2 != 'nan') & c1.notna() & c2.notna()
                    zenu_output[target_field] = pd.Series(pd.NA, index=base_df.index)
                    zenu_output.loc[valid_mask, target_field] = c1[valid_mask] + "_" + c2[valid_mask] + "_r"
                    continue

                elif target_field == "contact_partnership_type" and len(sources) > 0:
                    s1_col = sources[0].get("field")
                    
                    
                    allowed_types = [
                        "Aunt", "Brother", "Business Partner", "Co-Owner", "Colleague",
                        "Cousin", "Daughter", "Daughter-In-Law", "De Facto", "Deceased",
                        "Ex-Partner", "Executor Of Will", "Father", "Father-In-Law",
                        "Friend", "Granddaughter", "Grandfather", "Grandmother",
                        "Grandson", "Husband", "Mother", "Mother-In-Law", "Nephew",
                        "Niece", "Other", "Other Relative", "Partner", "Power Of Attorney",
                        "Principal", "Sister", "Son", "Son-In-Law", "Uncle", "Wife"
                    ]

                    raw_val = base_df.get(s1_col).astype(str).str.strip().str.title()
                    raw_val = raw_val.replace('Spouse', 'Partner')
                    zenu_output[target_field] = raw_val.where(raw_val.isin(allowed_types), 'Other')
                    continue

            # --- GENERIC ACTIONS (For standard rows that aren't caught by overrides) ---
            if action == "direct" and len(sources) > 0:
                s1_field = sources[0].get("field")
                if s1_field in base_df.columns:
                    zenu_output[target_field] = base_df[s1_field]
                else:
                    zenu_output[target_field] = pd.NA

            elif action == "lookup":
                if not rule.get("lookupConfig") or len(rule["lookupConfig"]) == 0:
                    zenu_output[target_field] = pd.NA
                    continue
                    
                lkp = rule["lookupConfig"][0]
                target_file = lkp.get("targetFile")
                match_key = lkp.get("matchKey") 
                extract_fields = lkp.get("extractFields", [])
                
                if not target_file or not extract_fields:
                    zenu_output[target_field] = pd.NA
                    continue
                    
                extract_col = extract_fields[0] 
                s1_field = sources[0].get("field") if sources else match_key
                
                try:
                    target_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_col}" FROM "{target_file}"', self.conn)
                    target_df = target_df.drop_duplicates(subset=[match_key])
                    mapping_dict = target_df.set_index(match_key)[extract_col].to_dict()
                    zenu_output[target_field] = base_df[s1_field].map(mapping_dict)
                except Exception as e:
                    print(f"[{self.job_id}] Error on lookup for {target_field}: {e}")
                    zenu_output[target_field] = pd.NA

            elif action == "static":
                val = rule.get("valueExpression", "")
                zenu_output[target_field] = val
                
            elif action == "concat":
                zenu_output[target_field] = "[Concatenated Result Here]"

        # ---------------------------------------------------------
        # 4. CLEANUP AND EXPORT
        # This sits completely OUTSIDE the "for rule in rules" loop 
        # so it only runs once per group!
        # ---------------------------------------------------------
        zenu_output = zenu_output.replace('nan', pd.NA).replace('None', pd.NA)

        safe_group_name = group_name.replace(" ", "_").replace("/", "_")
        output_path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final.csv")
        zenu_output.to_csv(output_path, index=False)
        print(f"[{self.job_id}] SUCCESS: Created {output_path}")

    def close(self):
        """Safely close the database connection."""
        self.conn.close()
        print(f"[{self.job_id}] Database connection closed.")