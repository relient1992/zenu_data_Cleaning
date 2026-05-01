import pandas as pd
import sqlite3
import os
import numpy as np

class EagleProcessor:
    def __init__(self, engine):
        self.engine = engine
        self.conn = engine.conn
        self.job_id = engine.job_id

    def process_group(self, group_name, rules):
        group_lower = group_name.strip().lower()
        if group_lower == "contact requirement":
            self.process_contact_requirement(rules)
        elif group_lower == "contact relationship":
            self.process_contact_relationship(rules)
        elif group_lower == "prospect":
            self.process_prospect(rules)
        else:
            self.engine.log(f"[{self.job_id}] Eagle Processor: Group '{group_name}' logic pending.")

    # =========================================================================================
    # CONTACT REQUIREMENT (Untouched)
    # =========================================================================================
    def process_contact_requirement(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Contact Requirement...")
        
        base_file = "contacts.csv" 
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing. Cannot process Contact Requirements.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
            
            if 'bp_listing_types' in df.columns:
                df['bp_listing_types'] = df['bp_listing_types'].fillna('').astype(str).apply(
                    lambda x: x.split(';')[0].strip()
                )

            price_cols = ['bp_min_price', 'bp_max_price', 'bp_min_rent', 'bp_max_rent']
            for col in price_cols:
                if col in df.columns:
                    df[col] = df[col].replace(['', 'nan', 'NaN', 'None'], np.nan)

            has_sale = pd.Series(False, index=df.index)
            has_lease = pd.Series(False, index=df.index)

            if 'bp_min_price' in df.columns and 'bp_max_price' in df.columns:
                has_sale = df['bp_min_price'].notna() | df['bp_max_price'].notna()
                
            if 'bp_min_rent' in df.columns and 'bp_max_rent' in df.columns:
                has_lease = df['bp_min_rent'].notna() | df['bp_max_rent'].notna()

            intent_list = []
            for s, l in zip(has_sale, has_lease):
                if s and l:
                    intent_list.append(['SALE', 'LEASE']) 
                elif l:
                    intent_list.append(['LEASE'])
                else:
                    intent_list.append(['SALE']) 

            df['_calculated_sale_method'] = intent_list
            
            self.engine.log(f"[{self.job_id}] Duplicating rows for contacts with dual Sale/Lease pricing...")
            df = df.explode('_calculated_sale_method').reset_index(drop=True)

            zenu_df = pd.DataFrame(index=df.index)

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None

                if target_field.strip().lower() == 'contact_criteria_sale_method':
                    zenu_df[target_field] = df['_calculated_sale_method']
                    continue 

                if action == 'direct':
                    if primary_src_field and primary_src_field in df.columns:
                        zenu_df[target_field] = df[primary_src_field].astype(str).str.strip()
                        if rule.get('notes') and "Replace \";\" to \",\"" in rule.get('notes'):
                            zenu_df[target_field] = zenu_df[target_field].str.replace(';', ',', regex=False).str.lstrip(',')

                elif action == 'static':
                    zenu_df[target_field] = rule.get('valueExpression', '')

                elif action == 'concat':
                    expression = str(rule.get('valueExpression', '')).upper()
                    
                    if "SALE" in expression:
                        price_col = primary_src_field
                        rent_col = price_col.replace('price', 'rent')
                        
                        if price_col in df.columns and rent_col in df.columns:
                            zenu_df[target_field] = df.apply(
                                lambda row: row[price_col] if str(row['_calculated_sale_method']) == 'SALE' else row[rent_col], 
                                axis=1
                            )
                            
                    elif "BLANK" in expression or '""' in expression:
                        max_col = primary_src_field
                        min_col = max_col.replace('max', 'min') if 'max' in max_col else max_col
                        
                        if max_col in df.columns and min_col in df.columns:
                            s_max = df[max_col].replace(['', 'nan', 'NaN', 'None'], np.nan)
                            s_min = df[min_col].replace(['', 'nan', 'NaN', 'None'], np.nan)
                            zenu_df[target_field] = s_max.fillna(s_min)
                            
                    elif "SQUARE METERS" in expression or "SQM" in expression:
                        if primary_src_field in df.columns:
                            val = df[primary_src_field].astype(str).str.strip().str.upper()
                            zenu_df[target_field] = val.replace({
                                'SQUARE METERS': 'SQM',
                                'ACRES': 'ACRE'
                            })
                    else:
                        if primary_src_field in df.columns:
                            zenu_df[target_field] = df[primary_src_field].astype(str)

                elif action == 'lookup':
                    lookup_configs = rule.get('lookupConfig', [])
                    for config in lookup_configs:
                        target_file = config.get('targetFile')
                        match_key = config.get('matchKey')
                        extract_fields = config.get('extractFields', [])
                        
                        if target_file and match_key and extract_fields:
                            try:
                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                grouped_lookup = lookup_df.groupby(match_key)[extract_fields[0]].apply(list).to_dict()
                                zenu_df[target_field] = df[primary_src_field].map(grouped_lookup)
                                
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            list_cols = [col for col in zenu_df.columns if zenu_df[col].apply(type).eq(list).any()]
            for col in list_cols:
                zenu_df = zenu_df.explode(col)

            zenu_df = zenu_df.replace(
                ['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], 
                np.nan
            )
            
            strict_criteria_columns = [
                'contact_criteria_category',
                'contact_criteria_property_type',
                'contact_criteria_price_from',
                'contact_criteria_price_to',
                'contact_criteria_bedrooms',
                'contact_criteria_bathrooms',
                'contact_criteria_carspaces',
                'contact_criteria_land_from',
                'contact_criteria_land_to',
                'contact_criteria_land_unit'
            ]
            
            existing_strict_cols = [col for col in strict_criteria_columns if col in zenu_df.columns]
            if existing_strict_cols:
                zenu_df = zenu_df.dropna(subset=existing_strict_cols, how='all')

            if 'contact_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['contact_identifier'])
                
            zenu_df = zenu_df.fillna('')

            output_file = os.path.join(self.engine.workspace, "zenu_contact_requirement.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_contact_requirement", self.conn, if_exists='replace', index=False)
            
            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Contact Requirements for Eagle.")

        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Contact Requirement mapping: {e}")

    # =========================================================================================
    # CONTACT RELATIONSHIP (Untouched)
    # =========================================================================================
    def process_contact_relationship(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Contact Relationship...")
        
        base_file = "contact_relationships.csv" 
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] Warning: '{base_file}' table missing. Skipping Contact Relationships.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)

            name_map = {} 
            
            if 'contact1_id' in df.columns and 'contact2_id' in df.columns:
                self.engine.log(f"[{self.job_id}] Generating bidirectional interchange rows...")
                
                df_swapped = df.copy()
                df_swapped['contact1_id'] = df['contact2_id']
                df_swapped['contact2_id'] = df['contact1_id']
                
                df = pd.concat([df, df_swapped], ignore_index=True)
                df = df.drop_duplicates(subset=['contact1_id', 'contact2_id'], keep='first')
                
                try:
                    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='contact_cleaned.csv';")
                    if cursor.fetchone():
                        self.engine.log(f"[{self.job_id}] Mapping IDs and extracting names from contact_cleaned.csv...")
                        
                        cleaned_df = pd.read_sql_query('SELECT "Raw ORIG CONTACT_IDENTIFIER", "CONTACT_IDENTIFIER", "Contact Name" FROM "contact_cleaned.csv"', self.conn)
                        
                        cleaned_df['Raw ORIG CONTACT_IDENTIFIER'] = cleaned_df['Raw ORIG CONTACT_IDENTIFIER'].astype(str)
                        cleaned_unique_raw = cleaned_df.drop_duplicates(subset=['Raw ORIG CONTACT_IDENTIFIER'], keep='first')
                        
                        id_map = cleaned_unique_raw.set_index('Raw ORIG CONTACT_IDENTIFIER')['CONTACT_IDENTIFIER'].to_dict()
                        
                        cleaned_unique_id = cleaned_df.drop_duplicates(subset=['CONTACT_IDENTIFIER'], keep='first')
                        name_map = cleaned_unique_id.set_index('CONTACT_IDENTIFIER')['Contact Name'].to_dict()
                        
                        df['contact1_id'] = df['contact1_id'].astype(str).map(id_map).fillna(df['contact1_id'])
                        df['contact2_id'] = df['contact2_id'].astype(str).map(id_map).fillna(df['contact2_id'])
                except Exception as e:
                    self.engine.log(f"[{self.job_id}] Warning: Could not map cleaned IDs/Names - {e}")

            zenu_df = pd.DataFrame(index=df.index)

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None

                if action == 'direct':
                    if primary_src_field and primary_src_field in df.columns:
                        zenu_df[target_field] = df[primary_src_field].astype(str).str.strip()
                        
                        if target_field == 'contact_partnership_type':
                            zenu_df[target_field] = zenu_df[target_field].str.title()
                            zenu_df[target_field] = zenu_df[target_field].replace('Spouse', 'Partner')
                            
                            zenu_df[target_field] = zenu_df[target_field].replace(
                                ['Nan', 'None', '', '<Na>', 'Null'], 'Other'
                            )
                            zenu_df[target_field] = zenu_df[target_field].fillna('Other')

                elif action == 'static':
                    zenu_df[target_field] = rule.get('valueExpression', '')

                elif action == 'concat':
                    if target_field == 'contact_partnership_id':
                        if 'contact_identifier' in zenu_df.columns and 'contact_partner_identifier' in zenu_df.columns:
                            zenu_df[target_field] = zenu_df['contact_identifier'].astype(str) + "_" + zenu_df['contact_partner_identifier'].astype(str) + "_r"
                        elif len(sources) >= 2 and sources[0]['field'] in df.columns and sources[1]['field'] in df.columns:
                            zenu_df[target_field] = df[sources[0]['field']].astype(str) + "_" + df[sources[1]['field']].astype(str) + "_r"
                    else:
                        if primary_src_field in df.columns:
                            zenu_df[target_field] = df[primary_src_field].astype(str)

                elif action == 'lookup':
                    lookup_configs = rule.get('lookupConfig', [])
                    for config in lookup_configs:
                        target_file = config.get('targetFile')
                        match_key = config.get('matchKey')
                        extract_fields = config.get('extractFields', [])
                        
                        if target_file and match_key and extract_fields:
                            try:
                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                grouped_lookup = lookup_df.groupby(match_key)[extract_fields[0]].apply(list).to_dict()
                                zenu_df[target_field] = df[primary_src_field].map(grouped_lookup)
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            list_cols = [col for col in zenu_df.columns if zenu_df[col].apply(type).eq(list).any()]
            for col in list_cols:
                zenu_df = zenu_df.explode(col)

            zenu_df = zenu_df.replace(
                ['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], 
                np.nan
            )
            
            if 'contact_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['contact_identifier'])
            if 'contact_partner_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['contact_partner_identifier'])

            if name_map:
                if 'contact_identifier' in zenu_df.columns:
                    zenu_df['Contact Name'] = zenu_df['contact_identifier'].astype(str).map(name_map)
                if 'contact_partner_identifier' in zenu_df.columns:
                    zenu_df['Partner Name'] = zenu_df['contact_partner_identifier'].astype(str).map(name_map)

            zenu_df = zenu_df.fillna('')

            output_file = os.path.join(self.engine.workspace, "zenu_contact_relationships.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_contact_relationships", self.conn, if_exists='replace', index=False)
            
            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Contact Relationships for Eagle.")

        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Contact Relationship mapping: {e}")

    # =========================================================================================
    # PROSPECT (Updated with Properties.csv bridging & strict name cleaning)
    # =========================================================================================
    def process_prospect(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Prospect...")
        
        base_file = "addresses.csv"
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing. Cannot process Prospects.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)

            def safe_str(x):
                if pd.isna(x) or str(x).strip().lower() == 'nan': return ''
                s = str(x).strip()
                return s[:-2] if s.endswith('.0') else s

            # ==========================================
            # SOURCE PRE-PROCESSING: EXCLUDE LISTINGS & ENRICH USER_ID
            # ==========================================
            self.engine.log(f"[{self.job_id}] Cross-referencing Listings to isolate true Prospects...")
            exclusion_ids = set()
            
            tables_to_check = ['Listing_sale.csv', 'Listing_lease.csv', 'Listing_appraisal.csv'] 
            
            for table_name in tables_to_check:
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';")
                if cursor.fetchone():
                    try:
                        col_query = f"PRAGMA table_info('{table_name}')"
                        cols = [row[1] for row in cursor.execute(col_query).fetchall()]
                        link_col = 'address_id' if 'address_id' in cols else ('property_id' if 'property_id' in cols else None)
                        if link_col:
                            ids = pd.read_sql_query(f'SELECT "{link_col}" FROM "{table_name}" WHERE "{link_col}" IS NOT NULL', self.conn)
                            exclusion_ids.update(ids[link_col].astype(str).apply(safe_str).tolist())
                    except Exception:
                        pass
            
            if exclusion_ids:
                initial_count = len(df)
                df_safe_id = df['id'].apply(safe_str)
                df = df[~df_safe_id.isin(exclusion_ids)].reset_index(drop=True)
                self.engine.log(f"[{self.job_id}] Filtered out {initial_count - len(df)} addresses already linked to Active Listings.")
                
            # ENRICH addresses.csv with user_id from properties.csv if missing
            try:
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='properties.csv';")
                if cursor.fetchone():
                    self.engine.log(f"[{self.job_id}] Pulling user_id from properties.csv to enrich Prospects...")
                    props_df = pd.read_sql_query('SELECT address_id, user_id FROM "properties.csv" WHERE address_id IS NOT NULL AND user_id IS NOT NULL', self.conn)
                    props_df['address_id'] = props_df['address_id'].apply(safe_str)
                    props_df = props_df.drop_duplicates(subset=['address_id'], keep='last')
                    
                    user_id_map = props_df.set_index('address_id')['user_id'].to_dict()
                    df_safe_id = df['id'].apply(safe_str)
                    
                    if 'user_id' not in df.columns:
                        df['user_id'] = df_safe_id.map(user_id_map)
                    else:
                        df['user_id'] = df['user_id'].apply(safe_str).replace('', np.nan)
                        df['user_id'] = df['user_id'].fillna(df_safe_id.map(user_id_map))
            except Exception as e:
                self.engine.log(f"[{self.job_id}] Warning mapping user_id from properties: {e}")
            # ==========================================

            zenu_df = pd.DataFrame(index=df.index)

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None

                if target_field == 'property_identifier' and primary_src_field in df.columns:
                    zenu_df[target_field] = "Pr_" + df[primary_src_field].apply(safe_str)
                    continue

                if target_field == 'property_type' and primary_src_field in df.columns:
                    zenu_df[target_field] = df[primary_src_field].apply(safe_str).str.replace('..', ',', regex=False)
                    continue

                if target_field == 'property_sale_method':
                    tenant_col = 'tenant_uid' 
                    if tenant_col in df.columns:
                        has_tenant = df[tenant_col].notna() & (df[tenant_col].apply(safe_str) != '')
                        zenu_df[target_field] = np.where(has_tenant, 'Lease', 'Sale')
                    else:
                        zenu_df[target_field] = 'Sale'
                    continue

                if target_field == 'property_unit_number':
                    lot = df.get('lot_no', pd.Series(['']*len(df))).apply(safe_str)
                    unit = df.get('unit', pd.Series(['']*len(df))).apply(safe_str)
                    combined = (lot + " " + unit).str.strip()
                    zenu_df[target_field] = combined
                    continue

                if target_field == 'property_full_address':
                    zenu_df[target_field] = ''
                    continue

                if action == 'direct':
                    if primary_src_field and primary_src_field in df.columns:
                        zenu_df[target_field] = df[primary_src_field].apply(safe_str)

                elif action == 'static':
                    zenu_df[target_field] = rule.get('valueExpression', '')

                elif action == 'concat':
                    if primary_src_field in df.columns:
                        zenu_df[target_field] = df[primary_src_field].apply(safe_str)

                elif action == 'lookup':
                    lookup_configs = rule.get('lookupConfig', [])
                    for config in lookup_configs:
                        target_file = config.get('targetFile')
                        match_key = config.get('matchKey')
                        extract_fields = config.get('extractFields', [])
                        
                        if target_file and match_key and extract_fields:
                            try:
                                if target_file.lower() == 'agents.csv':
                                    agent_col_query = f"PRAGMA table_info('{target_file}')"
                                    agent_cols = [row[1] for row in cursor.execute(agent_col_query).fetchall()]
                                    # Dynamically correct match_key if needed
                                    if match_key not in agent_cols and 'id' in agent_cols:
                                        self.engine.log(f"[{self.job_id}] Auto-correcting {target_file} matchKey from {match_key} to id...")
                                        match_key = 'id'

                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                
                                lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                                mapping_dict = lookup_df.drop_duplicates(subset=[match_key]).set_index(match_key)[extract_fields[0]].to_dict()
                                
                                safe_keys = df[primary_src_field].apply(safe_str)
                                
                                zenu_df[target_field] = safe_keys.map(mapping_dict)
                                
                                # Strip and clean output text specifically for Team Members/Names
                                zenu_df[target_field] = zenu_df[target_field].astype(str).str.strip().replace(['nan', 'NaN', 'None'], '')
                                
                                zenu_df.loc[safe_keys == '', target_field] = ''
                                
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            # ==========================================
            # POST-PROCESSING: FULL ADDRESS BUILDER & CLEANUP
            # ==========================================
            if 'property_full_address' in zenu_df.columns:
                def build_address(row):
                    u = str(row.get('property_unit_number', '')).strip()
                    sn = str(row.get('property_street_number', '')).strip()
                    st = str(row.get('property_street_name', '')).strip()
                    sub = str(row.get('property_suburb', '')).strip()
                    state = str(row.get('property_state', '')).strip()
                    pc = str(row.get('property_postcode', '')).strip()
                    
                    street_part = ""
                    if u and sn:
                        street_part = f"{u}/{sn}"
                    elif u:
                        street_part = u
                    elif sn:
                        street_part = sn
                        
                    addr1 = f"{street_part} {st}".strip()
                    
                    parts = []
                    if addr1: parts.append(addr1)
                    if sub: parts.append(sub)
                    
                    state_pc = f"{state} {pc}".strip()
                    if state_pc: parts.append(state_pc)
                    
                    return ", ".join(parts)
                
                zenu_df['property_full_address'] = zenu_df.apply(build_address, axis=1)

            zenu_df = zenu_df.replace(['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], np.nan)
            
            if 'property_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['property_identifier'])
                
            zenu_df = zenu_df.fillna('')

            zero_cols = ['property_bedrooms', 'property_bathrooms', 'property_garages', 'property_carports']
            for zc in zero_cols:
                if zc in zenu_df.columns:
                    zenu_df[zc] = zenu_df[zc].astype(str).replace(['0', '0.0', '0.00'], '')

            output_file = os.path.join(self.engine.workspace, "zenu_prospect.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_prospect", self.conn, if_exists='replace', index=False)
            
            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Prospects for Eagle.")

        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Prospect mapping: {e}")