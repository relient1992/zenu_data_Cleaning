import sqlite3
import pandas as pd
import json
import os
import base64
import re

class MigrationEngine:
    def __init__(self, job_id, workspace):
        self.job_id = job_id
        self.workspace = workspace
        self.db_path = os.path.join(self.workspace, f"{job_id}_raw_data.db")
        self.conn = sqlite3.connect(self.db_path)
        
    def load_csvs_to_sqlite(self):
        print(f"[{self.job_id}] Building SQLite Database...")
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
                        
                        print(f"[{self.job_id}] Loaded {file} successfully using '{enc}' encoding.")
                        loaded = True
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "codec can't decode" in error_msg or "utf-8" in error_msg or "encoding" in error_msg:
                            print(f"[{self.job_id}] '{enc}' encoding failed for {file}. Trying next...")
                        else:
                            print(f"[{self.job_id}] Warning: Could not load {file} - {e}")
                            break
                            
                if not loaded:
                    print(f"[{self.job_id}] All strict encodings failed. Forcing load with error replacement for {file}...")
                    try:
                        cursor = self.conn.cursor()
                        cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                        self.conn.commit()
                        for chunk in pd.read_csv(file_path, chunksize=chunksize, low_memory=False, encoding='utf-8', on_bad_lines='skip', encoding_errors='replace'):
                            chunk.to_sql(table_name, self.conn, if_exists='append', index=False)
                        print(f"[{self.job_id}] Loaded {file} successfully using fallback replacement mode.")
                    except Exception as e:
                        print(f"[{self.job_id}] CRITICAL: Completely failed to load {file}. Error: {e}")

    def run_mapping(self, mapping_json_path):
        print(f"[{self.job_id}] Reading JSON Mapping Rules...")
        with open(mapping_json_path, 'r') as f:
            rules = json.load(f)
            
        for group_name, group_rules in rules.items():
            if not group_rules: continue
            print(f"[{self.job_id}] Processing Group: {group_name}")
            self._process_group(group_name, group_rules)
            
    def _process_group(self, group_name, rules):
        zenu_output = pd.DataFrame()
        
        # 1. Determine the Default Base Table
        source_files = [rule.get("sources", [{}])[0].get("file") for rule in rules if rule.get("sources")]
        if not source_files:
            print(f"[{self.job_id}] Skipping {group_name}: No base file found.")
            return
            
        from collections import Counter
        base_file = Counter([f for f in source_files if f]).most_common(1)[0][0]

        if group_name == "Contact_Notes":
            base_file = "note_x_contact.csv"

        # ---------------------------------------------------------
        # 2. LOAD BASE DATA (WITH SPLIT-CONTACT MERGES)
        # ---------------------------------------------------------
        if group_name == "Contact Requirements":
            try:
                cursor = self.conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [t[0] for t in cursor.fetchall()]
                clean_file = next((t for t in tables if t.lower() == 'contact_cleaned.csv'), None)
                req_file = next((t for t in tables if t.lower() == 'contact_requirement.csv'), None)
                
                if clean_file and req_file:
                    clean_df = pd.read_sql_query(f'SELECT * FROM "{clean_file}"', self.conn)
                    req_df = pd.read_sql_query(f'SELECT * FROM "{req_file}"', self.conn)
                    match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'Raw ORIG CONTACT_IDENTIFIER'
                    base_df = pd.merge(clean_df, req_df, left_on=match_col, right_on='contact_id', how='inner')
                else:
                    base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
            except Exception as e:
                print(f"[{self.job_id}] Error building split contact base table: {e}")
                return
                
        elif group_name == "Prospect Owners":
            try:
                base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
                base_df.columns = base_df.columns.str.strip().str.lower()
                
                # STRICT PRE-FILTER: Prospect Owners must only contain specific Seller roles
                if 'role' in base_df.columns:
                    allowed_roles = ['owner', 'owner_occupier', 'landlord', 'owner_absentee', 'vendor', 'prospective_vendor']
                    base_df = base_df[base_df['role'].astype(str).str.strip().str.lower().isin(allowed_roles)]
                
                cursor = self.conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [t[0] for t in cursor.fetchall()]
                clean_file = next((t for t in tables if t.lower() == 'contact_cleaned.csv'), None)
                
                if clean_file:
                    clean_df = pd.read_sql_query(f'SELECT * FROM "{clean_file}"', self.conn)
                    match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'Raw ORIG CONTACT_IDENTIFIER'
                    
                    # 1-to-Many MERGE: This duplicates the property row for every split contact!
                    base_df = pd.merge(clean_df, base_df, left_on=match_col, right_on='contact_id', how='inner')
            except Exception as e:
                 print(f"[{self.job_id}] Error building split Prospect Owners base table: {e}")
                 return
                 
        else:
            try:
                base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
                base_df.columns = base_df.columns.str.strip().str.lower()
            except Exception as e:
                 print(f"[{self.job_id}] Error loading base table {base_file}: {e}")
                 return

        # ---------------------------------------------------------
        # 3. GLOBAL LOOKUP DICTIONARIES
        # ---------------------------------------------------------
        contact_cleaned_mapping = None
        note_date_map, note_agent_map, note_cat_map, note_head_map, note_desc_map = {}, {}, {}, {}, {}
        agent_mapping, note_to_listing, listing_to_prop, prop_mapping = {}, {}, {}, {}
        appraisal_agents_map = {}
        prospect_agents_map = {}

        if group_name in ["Contact_Notes", "Appraisal", "Prospect"]:
            try:
                clean_df = pd.read_sql_query('SELECT * FROM "contact_cleaned.csv"', self.conn)
                match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'Raw ORIG CONTACT_IDENTIFIER'
                clean_df['CONTACT_IDENTIFIER'] = clean_df['CONTACT_IDENTIFIER'].astype(str)
                primary_mask = clean_df['CONTACT_IDENTIFIER'].str.contains(r'_c1$|_c$', regex=True)
                clean_df = clean_df[primary_mask].drop_duplicates(subset=[match_col])
                contact_cleaned_mapping = clean_df.set_index(match_col)['CONTACT_IDENTIFIER'].to_dict()

                agent_df = pd.read_sql_query('SELECT * FROM "agent.csv"', self.conn)
                agent_df.columns = agent_df.columns.str.strip().str.lower()
                if 'agent_id' in agent_df.columns:
                    agent_df['full_name'] = agent_df.get('first_name', pd.Series(dtype=str)).fillna('') + ' ' + agent_df.get('last_name', pd.Series(dtype=str)).fillna('')
                    agent_mapping = agent_df.set_index('agent_id')['full_name'].str.strip().to_dict()

                note_df = pd.read_sql_query('SELECT * FROM "note.csv"', self.conn)
                note_df.columns = note_df.columns.str.strip().str.lower()
                
                def decode_b64(text):
                    try:
                        if pd.isna(text) or str(text).lower() in ['nan', 'none', '']: return ""
                        s = str(text).strip()
                        if s.startswith('[base64]'): s = s.replace('[base64]', '')
                        s += "=" * ((4 - len(s) % 4) % 4) 
                        return base64.b64decode(s).decode('utf-8', errors='ignore')
                    except: return str(text)

                if 'note_id' in note_df.columns:
                    raw_ndates = note_df.get('date', pd.Series(dtype=object)).astype(str).str.strip().str.lower()
                    bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00', '01/01/1970', '1/01/1970', '01-01-1970', '1-01-1970']
                    raw_ndates = raw_ndates.replace(bad_dates, pd.NA)

                    note_df['date_clean'] = pd.to_datetime(
                        raw_ndates, errors='coerce', dayfirst=True
                    ).dt.strftime('%d/%m/%Y').replace(['NaT', 'nan'], pd.NA)
                    
                    note_date_map = note_df.set_index('note_id')['date_clean'].dropna().to_dict()
                    note_agent_map = note_df.set_index('note_id')['agent_id'].to_dict() if 'agent_id' in note_df.columns else {}
                    note_cat_map = note_df.set_index('note_id')['category'].fillna('').to_dict() if 'category' in note_df.columns else {}
                    note_head_map = note_df.set_index('note_id')['headline'].apply(decode_b64).to_dict() if 'headline' in note_df.columns else {}
                    note_desc_map = note_df.set_index('note_id')['description'].apply(decode_b64).to_dict() if 'description' in note_df.columns else {}

                nx_listing = pd.read_sql_query('SELECT * FROM "note_x_listing.csv"', self.conn)
                nx_listing.columns = nx_listing.columns.str.strip().str.lower()
                if 'note_id' in nx_listing.columns and 'listing_id' in nx_listing.columns:
                    note_to_listing = nx_listing.drop_duplicates(subset=['note_id']).set_index('note_id')['listing_id'].to_dict()

                list_dfs = []
                for table in ["listing_sale.csv", "listing_lease.csv", "listing_appraisal.csv"]:
                    try: 
                        temp_df = pd.read_sql_query(f'SELECT * FROM "{table}"', self.conn)
                        temp_df.columns = temp_df.columns.str.strip().str.lower()
                        if 'listing_id' in temp_df.columns and 'property_id' in temp_df.columns:
                            list_dfs.append(temp_df[['listing_id', 'property_id']])
                    except: pass
                
                all_listed_props = pd.DataFrame(columns=['property_id'])
                if list_dfs:
                    all_listings = pd.concat(list_dfs).drop_duplicates(subset=['listing_id'])
                    listing_to_prop = all_listings.set_index('listing_id')['property_id'].to_dict()
                    all_listed_props = pd.concat(list_dfs).dropna(subset=['property_id'])
                
                listed_prop_ids_set = set(all_listed_props['property_id'].astype(str).str.replace(r'\.0$', '', regex=True).unique())

                prop_df = pd.read_sql_query('SELECT * FROM "property.csv"', self.conn)
                prop_df.columns = prop_df.columns.str.strip().str.lower()
                
                def format_address(row):
                    def clean_val(v):
                        if pd.isna(v) or v is None: return ""
                        s = str(v).strip()
                        if s.lower() in ['nan', 'none', 'null', '']: return ""
                        if s.endswith('.0'): s = s[:-2]
                        return s

                    unit = clean_val(row.get('unit_num'))
                    st_num = clean_val(row.get('street_num'))
                    st_name = clean_val(row.get('street_name'))
                    st_type = clean_val(row.get('street_type'))
                    suburb = clean_val(row.get('suburb'))
                    state = clean_val(row.get('state'))
                    postcode = clean_val(row.get('postcode'))
                    
                    st_parts = []
                    if unit and st_num: st_parts.append(f"{unit}/{st_num}")
                    elif unit: st_parts.append(unit)
                    elif st_num: st_parts.append(st_num)
                    
                    if st_name: st_parts.append(st_name)
                    if st_type: st_parts.append(st_type)
                    street_address = " ".join(st_parts).strip()
                    
                    state_pc = " ".join([p for p in [state, postcode] if p]).strip()
                    
                    final_parts = []
                    if street_address: final_parts.append(street_address)
                    if suburb: final_parts.append(suburb)
                    if state_pc: final_parts.append(state_pc)
                    
                    return ", ".join(final_parts)

                if 'property_id' in prop_df.columns:
                    prop_df['formatted_address'] = prop_df.apply(format_address, axis=1)
                    prop_mapping = prop_df.set_index('property_id')['formatted_address'].to_dict()

            except Exception as e: print(f"[{self.job_id}] Warning: Error building Maps: {e}")

        if group_name == "Appraisal":
            try:
                prop_df = pd.read_sql_query('SELECT * FROM "property.csv"', self.conn)
                prop_df.columns = prop_df.columns.str.strip().str.lower()
                if 'property_id' in prop_df.columns:
                    prop_df = prop_df.drop_duplicates(subset=['property_id'])
                
                if 'property_id' in base_df.columns and 'property_id' in prop_df.columns:
                    base_df = pd.merge(base_df, prop_df, on='property_id', how='left')

                lxa_df = pd.read_sql_query('SELECT * FROM "listing_x_agent.csv"', self.conn)
                lxa_df.columns = lxa_df.columns.str.strip().str.lower()
                
                if 'listing_id' in lxa_df.columns and 'agent_id' in lxa_df.columns and 'role' in lxa_df.columns:
                    is_appr = lxa_df['role'].astype(str).str.lower().str.contains('appraisal')
                    appr_lxa = lxa_df[is_appr]
                    for _, row in appr_lxa.iterrows():
                        lid = row['listing_id']
                        aid = row['agent_id']
                        name = agent_mapping.get(aid, '')
                        if name:
                            if lid not in appraisal_agents_map: appraisal_agents_map[lid] = []
                            if name not in appraisal_agents_map[lid]: appraisal_agents_map[lid].append(name)
            except Exception as e: print(f"[{self.job_id}] Warning: Error building Appraisal maps: {e}")

        elif group_name == "Prospect":
            try:
                if 'property_id' in base_df.columns:
                    base_df['property_id_clean'] = base_df['property_id'].astype(str).str.replace(r'\.0$', '', regex=True)
                    base_df = base_df[~base_df['property_id_clean'].isin(listed_prop_ids_set)]

                pxa_df = pd.read_sql_query('SELECT * FROM "property_x_agent.csv"', self.conn)
                pxa_df.columns = pxa_df.columns.str.strip().str.lower()
                
                if 'property_id' in pxa_df.columns and 'agent_id' in pxa_df.columns:
                    for _, row in pxa_df.iterrows():
                        pid = str(row['property_id']).replace('.0', '')
                        aid = row['agent_id']
                        name = agent_mapping.get(aid, '')
                        if name:
                            if pid not in prospect_agents_map: prospect_agents_map[pid] = []
                            if name not in prospect_agents_map[pid]: prospect_agents_map[pid].append(name)
            except Exception as e: print(f"[{self.job_id}] Warning: Error building Prospect maps: {e}")


        # ---------------------------------------------------------
        # 4. PROCESS THE RULES LOOP
        # ---------------------------------------------------------
        for rule in rules:
            target_field = rule.get("targetField")
            action = rule.get("action")
            sources = rule.get("sources", [])

            # --- OVERRIDE: PROSPECT OWNERS ---
            if group_name == "Prospect Owners":
                if target_field == "property_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'property_id'
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).apply(
                        lambda x: f"Pr_{x}" if pd.notna(x) and x.strip() and x.lower() not in ['nan', 'none', 'null', ''] else pd.NA
                    )
                    continue

                elif target_field == "contact_identifier":
                    # Because we pre-merged Contact_cleaned in Step 2, we can just grab the exact CONTACT_IDENTIFIER
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', pd.NA)
                    continue
                    
                elif target_field == "contact_sale_type":
                    zenu_output[target_field] = rule.get("valueExpression", "Seller")
                    continue

            
            # --- OVERRIDE: PROSPECT ---
            elif group_name == "Prospect":
                if target_field == "property_identifier":
                    zenu_output[target_field] = base_df.get('property_id_clean', base_df.get('property_id', pd.Series(dtype=object))).apply(
                        lambda x: f"Pr_{x}" if pd.notna(x) and str(x).strip() else pd.NA
                    )
                    continue
                
                elif target_field == "property_timeline_status":
                    zenu_output[target_field] = "Prospect"
                    continue

                elif target_field in ["property_modified_date", "property_last_sold_date"]:
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else None
                    if s1_col and s1_col in base_df.columns:
                        raw_dates = base_df[s1_col].astype(str).str.strip().str.lower()
                        bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00', '01/01/1970', '1/01/1970', '01-01-1970', '1-01-1970']
                        raw_dates = raw_dates.replace(bad_dates, pd.NA)
                        
                        parsed_dates = pd.to_datetime(raw_dates, errors='coerce', dayfirst=True)
                        formatted_dates = parsed_dates.dt.strftime('%d/%m/%Y')
                        zenu_output[target_field] = formatted_dates.replace(['NaT', 'nan'], pd.NA)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue

                elif target_field == "property_type":
                    cat_val = base_df.get('category', pd.Series(dtype=str))
                    zenu_output[target_field] = (cat_val.astype(str)
                        .str.replace(r';\s*', ',', regex=True)
                        .str.replace('land', 'Vacant Land', case=False, regex=False)
                        .str.replace('vacant vacant land', 'Vacant Land', case=False, regex=False)
                        .str.strip())
                    continue

                elif target_field == "property_sale_method":
                    rent_val = base_df.get('current_rent', pd.Series(dtype=object))
                    def get_method(r):
                        if pd.notna(r) and str(r).strip() not in ['', '0', '0.0', 'nan', 'none']:
                            return "Lease"
                        return "Sale"
                    zenu_output[target_field] = rent_val.apply(get_method)
                    continue

                elif str(target_field).startswith("property_team_member_"):
                    idx = int(target_field.split('_')[-1]) - 1
                    def get_agent(pid):
                        if pd.isna(pid): return pd.NA
                        agents = prospect_agents_map.get(str(pid), [])
                        return agents[idx] if len(agents) > idx else pd.NA
                    zenu_output[target_field] = base_df.get('property_id_clean', pd.Series(dtype=object)).apply(get_agent)
                    continue

                elif target_field == "property_unit_number":
                    def build_unit(row):
                        parts = []
                        for c in ['lot_num', 'level_num', 'unit_num']:
                            v = row.get(c)
                            if pd.isna(v) or v is None: continue
                            s = str(v).strip()
                            if s.lower() in ['nan', 'none', 'null', '']: continue
                            if s.endswith('.0'): s = s[:-2]
                            if s: parts.append(s)
                        res = " ".join(parts)
                        return res if res else pd.NA
                    zenu_output[target_field] = base_df.apply(build_unit, axis=1)
                    continue

                elif target_field == "property_street_name":
                    def build_st_name(row):
                        parts = [str(row.get(c, '')).replace('nan','').replace('None','') for c in ['street_name', 'street_type']]
                        res = " ".join([p.strip() for p in parts if p.strip() and p.lower() != 'none'])
                        return res if res else pd.NA
                    zenu_output[target_field] = base_df.apply(build_st_name, axis=1)
                    continue

                elif target_field == "property_full_address":
                    def format_prospect_address(row):
                        def clean_val(v):
                            if pd.isna(v) or v is None: return ""
                            s = str(v).strip()
                            if s.lower() in ['nan', 'none', 'null', '']: return ""
                            if s.endswith('.0'): s = s[:-2]
                            return s
                        unit = clean_val(row.get('unit_num'))
                        lot = clean_val(row.get('lot_num'))
                        lvl = clean_val(row.get('level_num'))
                        unit_parts = " ".join([p for p in [lot, lvl, unit] if p])
                        st_num = clean_val(row.get('street_num'))
                        st_name = clean_val(row.get('street_name'))
                        st_type = clean_val(row.get('street_type'))
                        suburb = clean_val(row.get('suburb'))
                        state = clean_val(row.get('state'))
                        postcode = clean_val(row.get('postcode'))
                        
                        st_parts = []
                        if unit_parts and st_num: st_parts.append(f"{unit_parts}/{st_num}")
                        elif unit_parts: st_parts.append(unit_parts)
                        elif st_num: st_parts.append(st_num)
                        if st_name: st_parts.append(st_name)
                        if st_type: st_parts.append(st_type)
                        street_address = " ".join(st_parts).strip()
                        state_pc = " ".join([p for p in [state, postcode] if p]).strip()
                        
                        final_parts = []
                        if street_address: final_parts.append(street_address)
                        if suburb: final_parts.append(suburb)
                        if state_pc: final_parts.append(state_pc)
                        return ", ".join(final_parts)
                    zenu_output[target_field] = base_df.apply(format_prospect_address, axis=1)
                    continue

                elif target_field == "property_notes":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else None
                    if s1_col and s1_col in base_df.columns:
                        def decode_b64(text):
                            try:
                                if pd.isna(text) or str(text).lower() in ['nan', 'none', 'null', '']: return pd.NA
                                s = str(text).strip()
                                if s.startswith('[base64]'): 
                                    s = s.replace('[base64]', '')
                                    s += "=" * ((4 - len(s) % 4) % 4)
                                    return base64.b64decode(s).decode('utf-8', errors='ignore')
                                return s
                            except: return str(text)
                        zenu_output[target_field] = base_df[s1_col].apply(decode_b64)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field in ["property_bedrooms", "property_bathrooms", "property_carports", "property_street_number", "property_land_size_m2", "property_last_sold_price", "property_last_rent_pw"]:
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else None
                    if s1_col and s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].astype(str).str.replace(r'\.0$', '', regex=True).replace(['nan', 'None'], pd.NA)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue

            # --- ORIGINAL OVERRIDES ---
            elif group_name == "Appraisal":
                if target_field == "property_identifier":
                    zenu_output[target_field] = base_df.get('listing_id', pd.Series(dtype=object)).apply(
                        lambda x: f"Appr_{x}" if pd.notna(x) and str(x).strip() else pd.NA
                    )
                    continue
                
                elif target_field in ["property_modified_date", "property_appraisal_date", "property_last_sold_date"]:
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else None
                    if not s1_col and rule.get("lookupConfig"):
                        s1_col = str(rule["lookupConfig"][0]["extractFields"][0]).strip().lower()
                    
                    if s1_col and s1_col in base_df.columns:
                        raw_dates = base_df[s1_col].astype(str).str.strip().str.lower()
                        bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00', '01/01/1970', '1/01/1970', '01-01-1970', '1-01-1970']
                        raw_dates = raw_dates.replace(bad_dates, pd.NA)
                        
                        parsed_dates = pd.to_datetime(raw_dates, errors='coerce', dayfirst=True)
                        formatted_dates = parsed_dates.dt.strftime('%d/%m/%Y')
                        
                        zenu_output[target_field] = formatted_dates.replace(['NaT', 'nan'], pd.NA)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue

                elif target_field == "property_type":
                    cat_val = base_df.get('category', pd.Series(dtype=str))
                    zenu_output[target_field] = (cat_val.astype(str)
                        .str.replace(r';\s*', ',', regex=True)
                        .str.replace('land', 'Vacant Land', case=False, regex=False)
                        .str.replace('vacant vacant land', 'Vacant Land', case=False, regex=False)
                        .str.strip())
                    continue

                elif str(target_field).startswith("property_team_member_"):
                    idx = int(target_field.split('_')[-1]) - 1
                    def get_agent(lid):
                        if pd.isna(lid): return pd.NA
                        agents = appraisal_agents_map.get(lid, [])
                        return agents[idx] if len(agents) > idx else pd.NA
                    zenu_output[target_field] = base_df.get('listing_id', pd.Series(dtype=object)).apply(get_agent)
                    continue
                
                elif target_field == "property_unit_number":
                    def build_unit(row):
                        parts = []
                        for c in ['lot_num', 'level_num', 'unit_num']:
                            v = row.get(c)
                            if pd.isna(v) or v is None: continue
                            s = str(v).strip()
                            if s.lower() in ['nan', 'none', 'null', '']: continue
                            if s.endswith('.0'): s = s[:-2]
                            if s: parts.append(s)
                        res = " ".join(parts)
                        return res if res else pd.NA
                    zenu_output[target_field] = base_df.apply(build_unit, axis=1)
                    continue

                elif target_field == "property_street_name":
                    def build_st_name(row):
                        parts = [str(row.get(c, '')).replace('nan','').replace('None','') for c in ['street_name', 'street_type']]
                        res = " ".join([p.strip() for p in parts if p.strip() and p.lower() != 'none'])
                        return res if res else pd.NA
                    zenu_output[target_field] = base_df.apply(build_st_name, axis=1)
                    continue

                elif target_field == "property_notes":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else None
                    if s1_col and s1_col in base_df.columns:
                        def decode_b64(text):
                            try:
                                if pd.isna(text) or str(text).lower() in ['nan', 'none', 'null', '']: return pd.NA
                                s = str(text).strip()
                                if s.startswith('[base64]'): 
                                    s = s.replace('[base64]', '')
                                    s += "=" * ((4 - len(s) % 4) % 4)
                                    return base64.b64decode(s).decode('utf-8', errors='ignore')
                                return s
                            except: return str(text)
                        zenu_output[target_field] = base_df[s1_col].apply(decode_b64)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue

                elif target_field == "property_full_address":
                    def format_appraisal_address(row):
                        def clean_val(v):
                            if pd.isna(v) or v is None: return ""
                            s = str(v).strip()
                            if s.lower() in ['nan', 'none', 'null', '']: return ""
                            if s.endswith('.0'): s = s[:-2]
                            return s

                        unit = clean_val(row.get('unit_num'))
                        lot = clean_val(row.get('lot_num'))
                        lvl = clean_val(row.get('level_num'))
                        
                        unit_parts = " ".join([p for p in [lot, lvl, unit] if p])
                        st_num = clean_val(row.get('street_num'))
                        st_name = clean_val(row.get('street_name'))
                        st_type = clean_val(row.get('street_type'))
                        suburb = clean_val(row.get('suburb'))
                        state = clean_val(row.get('state'))
                        postcode = clean_val(row.get('postcode'))
                        
                        st_parts = []
                        if unit_parts and st_num: st_parts.append(f"{unit_parts}/{st_num}")
                        elif unit_parts: st_parts.append(unit_parts)
                        elif st_num: st_parts.append(st_num)
                        
                        if st_name: st_parts.append(st_name)
                        if st_type: st_parts.append(st_type)
                        street_address = " ".join(st_parts).strip()
                        
                        state_pc = " ".join([p for p in [state, postcode] if p]).strip()
                        
                        final_parts = []
                        if street_address: final_parts.append(street_address)
                        if suburb: final_parts.append(suburb)
                        if state_pc: final_parts.append(state_pc)
                        
                        return ", ".join(final_parts)
                    zenu_output[target_field] = base_df.apply(format_appraisal_address, axis=1)
                    continue

                elif action == "lookup" and rule.get("lookupConfig") and rule["lookupConfig"][0]["targetFile"] == "property.csv":
                    ext_col = rule["lookupConfig"][0]["extractFields"][0].lower()
                    if ext_col in base_df.columns:
                        val = base_df[ext_col]
                        if target_field in ["property_bedrooms", "property_bathrooms", "property_carports", "property_street_number"]:
                            val = val.astype(str).str.replace(r'\.0$', '', regex=True).replace('nan', pd.NA).replace('None', pd.NA)
                        zenu_output[target_field] = val
                    else:
                        zenu_output[target_field] = pd.NA
                    continue


            elif group_name == "Contact_Notes":
                if target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('contact_id', pd.Series(dtype=object)).map(contact_cleaned_mapping)
                    continue
                elif target_field == "contact_note_created_date":
                    zenu_output[target_field] = base_df.get('note_id', pd.Series(dtype=object)).map(note_date_map)
                    continue
                elif target_field == "contact_note_team_member":
                    zenu_output[target_field] = base_df.get('note_id', pd.Series(dtype=object)).map(note_agent_map).map(agent_mapping)
                    continue
                elif target_field == "contact_notes":
                    note_ids = base_df.get('note_id', pd.Series(dtype=object))
                    def build_mega_note(nid):
                        if pd.isna(nid): return pd.NA
                        
                        cat = str(note_cat_map.get(nid, '')).replace('nan', '').strip()
                        head = str(note_head_map.get(nid, '')).replace('nan', '').strip()
                        desc = str(note_desc_map.get(nid, '')).replace('nan', '').strip()
                        address = str(prop_mapping.get(listing_to_prop.get(note_to_listing.get(nid)), "")).strip()
                        
                        parts = []
                        if address: parts.append(f"Regarding Property: - {address}")
                        if cat: parts.append(cat)
                        if head: parts.append(head)
                        if desc: parts.append(desc)
                        
                        return " - ".join(parts).strip(" - ")

                    zenu_output[target_field] = note_ids.map(build_mega_note)
                    continue

            elif group_name == "Contact Requirements":
                if target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', base_df.get('contact_id', pd.NA))
                    continue
                elif target_field == "contact_criteria_property_type":
                    raw_val = base_df.get('property_categories', pd.Series(dtype=str))
                    zenu_output[target_field] = (raw_val.astype(str).str.replace(r';\s*', ',', regex=True).str.replace('land', 'Vacant Land', case=False, regex=False).str.replace('vacant vacant land', 'Vacant Land', case=False, regex=False).str.strip())
                    continue
                elif target_field == "contact_criteria_sale_method":
                    raw_val = base_df.get('search_type', pd.Series(dtype=str))
                    zenu_output[target_field] = raw_val.astype(str).str.title().apply(lambda x: x if x in ['Sale', 'Lease'] else pd.NA)
                    continue
                elif target_field in ["contact_criteria_bedrooms", "contact_criteria_bathrooms", "contact_criteria_carspaces"]:
                    if len(sources) >= 2:
                        val_to = pd.to_numeric(base_df.get(sources[0].get("field")), errors='coerce').replace(0, pd.NA)
                        val_from = pd.to_numeric(base_df.get(sources[1].get("field")), errors='coerce')
                        zenu_output[target_field] = val_to.fillna(val_from)
                    continue
                elif target_field in ["contact_criteria_land_from", "contact_criteria_land_to"]:
                    if len(sources) > 0: zenu_output[target_field] = pd.to_numeric(base_df.get(sources[0].get("field")), errors='coerce').round().astype('Int64')
                    continue
                elif target_field == "contact_criteria_land_unit":
                    if len(sources) >= 2:
                        has_s1 = pd.to_numeric(base_df.get(sources[0].get("field")), errors='coerce') > 0
                        has_s2 = pd.to_numeric(base_df.get(sources[1].get("field")), errors='coerce') > 0
                        zenu_output[target_field] = (has_s1 | has_s2).map({True: 'SQM', False: pd.NA})
                    continue

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
                        else: contact_cleaned_mapping = {}
                    except Exception: contact_cleaned_mapping = {}

                if target_field in ["contact_identifier", "contact_partner_identifier"] and len(sources) > 0:
                    s1_col = str(sources[0].get("field")).strip().lower()
                    zenu_output[target_field] = base_df.get(s1_col).map(contact_cleaned_mapping)
                    continue
                elif target_field == "contact_partnership_id" and len(sources) >= 2:
                    s1_col = str(sources[0].get("field")).strip().lower()
                    s2_col = str(sources[1].get("field")).strip().lower()
                    c1 = base_df.get(s1_col).map(contact_cleaned_mapping).astype(str)
                    c2 = base_df.get(s2_col).map(contact_cleaned_mapping).astype(str)
                    valid_mask = (c1 != 'nan') & (c2 != 'nan') & c1.notna() & c2.notna()
                    zenu_output[target_field] = pd.Series(pd.NA, index=base_df.index)
                    zenu_output.loc[valid_mask, target_field] = c1[valid_mask] + "_" + c2[valid_mask] + "_r"
                    continue
                elif target_field == "contact_partnership_type" and len(sources) > 0:
                    s1_col = str(sources[0].get("field")).strip().lower()
                    allowed_types = ["Aunt", "Brother", "Business Partner", "Co-Owner", "Colleague", "Cousin", "Daughter", "Daughter-In-Law", "De Facto", "Deceased", "Ex-Partner", "Executor Of Will", "Father", "Father-In-Law", "Friend", "Granddaughter", "Grandfather", "Grandmother", "Grandson", "Husband", "Mother", "Mother-In-Law", "Nephew", "Niece", "Other", "Other Relative", "Partner", "Power Of Attorney", "Principal", "Sister", "Son", "Son-In-Law", "Uncle", "Wife"]
                    raw_val = base_df.get(s1_col).astype(str).str.strip().str.title()
                    raw_val = raw_val.replace('Spouse', 'Partner')
                    zenu_output[target_field] = raw_val.where(raw_val.isin(allowed_types), 'Other')
                    continue

            # --- GENERIC ACTIONS ---
            if action == "direct" and len(sources) > 0:
                s1_field = str(sources[0].get("field")).strip().lower()
                zenu_output[target_field] = base_df.get(s1_field, pd.NA)

            elif action == "lookup":
                if not rule.get("lookupConfig") or len(rule["lookupConfig"]) == 0:
                    zenu_output[target_field] = pd.NA
                    continue
                lkp = rule["lookupConfig"][0]
                target_file, match_key, extract_fields = lkp.get("targetFile"), lkp.get("matchKey"), lkp.get("extractFields", [])
                if not target_file or not extract_fields:
                    zenu_output[target_field] = pd.NA
                    continue
                extract_col = str(extract_fields[0]).strip().lower()
                match_key = str(match_key).strip().lower()
                s1_field = str(sources[0].get("field")).strip().lower() if sources else match_key
                try:
                    target_df = pd.read_sql_query(f'SELECT * FROM "{target_file}"', self.conn)
                    target_df.columns = target_df.columns.str.strip().str.lower()
                    if match_key in target_df.columns and extract_col in target_df.columns:
                        mapping_dict = target_df.drop_duplicates(subset=[match_key]).set_index(match_key)[extract_col].to_dict()
                        zenu_output[target_field] = base_df.get(s1_field).map(mapping_dict)
                    else:
                        zenu_output[target_field] = pd.NA
                except Exception: zenu_output[target_field] = pd.NA

            elif action == "static": zenu_output[target_field] = rule.get("valueExpression", "")
            elif action == "concat": zenu_output[target_field] = "[Concatenated Result Here]"

        # ---------------------------------------------------------
        # 5. GLOBAL CLEANUP AND EXPORT
        # ---------------------------------------------------------
        
        # Inject note_id as the first column for Contact_Notes to help with verification
        if group_name == "Contact_Notes" and 'note_id' in base_df.columns:
            zenu_output.insert(0, 'source_note_id', base_df['note_id'])
            
        # Inject original role into Prospect Owners to easily identify why they were included
        if group_name == "Prospect Owners" and 'role' in base_df.columns:
            zenu_output['source_role'] = base_df['role'].astype(str).str.title()

        # THE GLOBAL ZENU SCRUBBER
        def global_cleaner(val):
            if pd.isna(val): 
                return val
            text = str(val)
            text = text.replace('"', "'")
            text = text.encode('ascii', 'ignore').decode('ascii')
            text = re.sub(r"[^a-zA-Z0-9 ,\-.:;{}\[\]_&'\\/<>&%+=@#!\$^\*()?\r\n]+", '', text)
            text = re.sub(r' +', ' ', text)
            return text.strip()

        # Apply the scrubber to every single column in the final export file
        for col in zenu_output.columns:
            zenu_output[col] = zenu_output[col].apply(global_cleaner)

        # Convert empty strings and 'nan' text back to true nulls so pandas can drop them
        zenu_output = zenu_output.replace('', pd.NA).replace('nan', pd.NA).replace('None', pd.NA)

        # SPECIFIC DROPS
        if group_name == "Contact_Notes":
            if 'contact_identifier' in zenu_output.columns:
                zenu_output = zenu_output.dropna(subset=['contact_identifier'])
            if 'contact_notes' in zenu_output.columns:
                zenu_output = zenu_output.dropna(subset=['contact_notes'])
                
        if group_name in ["Appraisal", "Prospect"]:
            if 'property_identifier' in zenu_output.columns:
                zenu_output = zenu_output.dropna(subset=['property_identifier'])
                
        if group_name == "Prospect Owners":
            if 'contact_identifier' in zenu_output.columns:
                zenu_output = zenu_output.dropna(subset=['contact_identifier'])

        # Save to CSV
        safe_group_name = group_name.replace(" ", "_").replace("/", "_")
        output_path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final.csv")
        zenu_output.to_csv(output_path, index=False)
        print(f"[{self.job_id}] SUCCESS: Created {output_path}")

    def close(self):
        self.conn.close()
        print(f"[{self.job_id}] Database connection closed.")