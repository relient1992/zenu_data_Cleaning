import pandas as pd
import sqlite3
import os
import numpy as np
import re

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
        elif group_lower == "appraisal":
            self.process_appraisal(rules)
        elif group_lower == "prospect owners":
            self.process_prospect_owners(rules)
        elif group_lower == "appraisal owners":
            self.process_appraisal_owners(rules)
        elif group_lower == "buyer":
            self.process_buyer(rules)
        elif group_lower == "vendor":
            self.process_vendor(rules)
        elif group_lower == "enquiries":
            self.process_enquiries(rules)
        elif group_lower in ["contact_notes", "contact notes"]:
            self.process_contact_notes(rules)
        elif group_lower in ["property_notes", "property notes"]:
            self.process_property_notes(rules)
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
    # PROSPECT (Untouched)
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

                if target_field in ['property_full_address', 'For import']:
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
                                    if match_key not in agent_cols and 'id' in agent_cols:
                                        self.engine.log(f"[{self.job_id}] Auto-correcting {target_file} matchKey from {match_key} to id...")
                                        match_key = 'id'

                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                
                                lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                                mapping_dict = lookup_df.drop_duplicates(subset=[match_key]).set_index(match_key)[extract_fields[0]].to_dict()
                                
                                safe_keys = df[primary_src_field].apply(safe_str)
                                
                                zenu_df[target_field] = safe_keys.map(mapping_dict)
                                zenu_df[target_field] = zenu_df[target_field].astype(str).str.strip().replace(['nan', 'NaN', 'None'], '')
                                
                                zenu_df.loc[safe_keys == '', target_field] = ''
                                
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

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

            if 'For import' not in zenu_df.columns:
                zenu_df['For import'] = ''

            self.engine.log(f"[{self.job_id}] Applying deduplication logic for 'For import' flag...")
            
            if 'id' in df.columns:
                zenu_df['_raw_id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0)
            else:
                zenu_df['_raw_id'] = 0
                
            zenu_df = zenu_df.sort_values(['_raw_id'], ascending=[False])
            
            if 'property_sale_method' in zenu_df.columns and 'property_full_address' in zenu_df.columns:
                zenu_df['_temp_method'] = zenu_df['property_sale_method'].astype(str).str.lower().str.strip()
                zenu_df['_temp_addr'] = zenu_df['property_full_address'].astype(str).str.lower().str.strip()

                zenu_df['For import'] = np.where(
                    zenu_df.duplicated(subset=['_temp_method', '_temp_addr'], keep='first'), 
                    'N', 
                    'Y'
                )
                
                zenu_df = zenu_df.drop(columns=['_raw_id', '_temp_method', '_temp_addr']).sort_index()
            else:
                zenu_df['For import'] = 'Y'
                zenu_df = zenu_df.drop(columns=['_raw_id']).sort_index()

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

    # =========================================================================================
    # APPRAISAL (Untouched)
    # =========================================================================================
    def process_appraisal(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Appraisal...")
        
        base_file = "appraisals.csv"
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing. Cannot process Appraisals.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)

            def safe_str(x):
                if pd.isna(x) or str(x).strip().lower() == 'nan': return ''
                s = str(x).strip()
                return s[:-2] if s.endswith('.0') else s

            def parse_au_date(date_series):
                clean_str = date_series.astype(str).str.replace(r'\s*[+-]\d{2}:?\d{2}$', '', regex=True).str.split(' ').str[0].str.strip()
                parsed = pd.to_datetime(clean_str, format='%d/%m/%Y', errors='coerce')
                parsed = parsed.fillna(pd.to_datetime(clean_str, format='%Y-%m-%d', errors='coerce'))
                parsed = parsed.fillna(pd.to_datetime(clean_str, errors='coerce', dayfirst=True))
                return parsed

            zenu_df = pd.DataFrame(index=df.index)

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None

                if target_field == 'property_identifier' and primary_src_field in df.columns:
                    zenu_df[target_field] = "Appr_" + df[primary_src_field].apply(safe_str)
                    continue

                if target_field in ['property_full_address', 'For import']:
                    zenu_df[target_field] = ''
                    continue

                if action == 'direct':
                    if primary_src_field and primary_src_field in df.columns:
                        if target_field == 'property_appraisal_date':
                            raw_dates = parse_au_date(df[primary_src_field])
                            zenu_df[target_field] = raw_dates.dt.strftime('%d/%m/%Y').fillna('')
                        else:
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
                                    if match_key not in agent_cols and 'id' in agent_cols:
                                        match_key = 'id'

                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                                mapping_dict = lookup_df.drop_duplicates(subset=[match_key]).set_index(match_key)[extract_fields[0]].to_dict()
                                
                                safe_keys = df[primary_src_field].apply(safe_str)
                                zenu_df[target_field] = safe_keys.map(mapping_dict)
                                
                                if 'team_member' in target_field.lower():
                                    zenu_df[target_field] = zenu_df[target_field].astype(str).str.strip().replace(['nan', 'NaN', 'None'], '')
                                
                                zenu_df[target_field] = zenu_df[target_field].fillna('')
                                zenu_df.loc[safe_keys == '', target_field] = ''
                                
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            if 'property_full_address' in zenu_df.columns:
                def build_address(row):
                    u = str(row.get('property_unit_number', '')).strip()
                    
                    if u in ['0', '0.0']: 
                        u = ''
                        
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

            if 'For import' in zenu_df.columns and 'property_appraisal_date' in zenu_df.columns and 'property_sale_method' in zenu_df.columns and 'property_full_address' in zenu_df.columns:
                self.engine.log(f"[{self.job_id}] Applying deduplication logic for 'For import' flag...")
                
                if 'appraisal_date' in df.columns:
                    zenu_df['_raw_date'] = parse_au_date(df['appraisal_date'])
                else:
                    zenu_df['_raw_date'] = pd.to_datetime(zenu_df['property_appraisal_date'], format='%d/%m/%Y', errors='coerce')
                
                if 'id' in df.columns:
                    zenu_df['_raw_id'] = pd.to_numeric(df['id'], errors='coerce')
                else:
                    zenu_df['_raw_id'] = 0
                
                zenu_df = zenu_df.sort_values(['_raw_date', '_raw_id'], ascending=[False, False])
                
                zenu_df['_temp_method'] = zenu_df['property_sale_method'].astype(str).str.lower().str.strip()
                zenu_df['_temp_addr'] = zenu_df['property_full_address'].astype(str).str.lower().str.strip()

                zenu_df['For import'] = np.where(
                    zenu_df.duplicated(subset=['_temp_method', '_temp_addr'], keep='first'), 
                    'N', 
                    'Y'
                )
                
                if 'status' in zenu_df.columns:
                    is_lost = zenu_df['status'].astype(str).str.strip().str.lower() == 'lost'
                    zenu_df.loc[is_lost, 'For import'] = 'N'
                    self.engine.log(f"[{self.job_id}] Overrode 'For import' to 'N' for Appraisals with 'Lost' status.")
                
                zenu_df = zenu_df.drop(columns=['_raw_date', '_raw_id', '_temp_method', '_temp_addr']).sort_index()

            zenu_df = zenu_df.replace(['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], np.nan)
            
            if 'property_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['property_identifier'])
                
            zenu_df = zenu_df.fillna('')

            zero_cols = ['property_bedrooms', 'property_bathrooms', 'property_toilets', 'property_garages', 'property_carports', 'property_unit_number']
            for zc in zero_cols:
                if zc in zenu_df.columns:
                    zenu_df[zc] = zenu_df[zc].astype(str).replace(['0', '0.0', '0.00'], '')

            output_file = os.path.join(self.engine.workspace, "zenu_appraisal.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_appraisal", self.conn, if_exists='replace', index=False)
            
            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Appraisals for Eagle.")
        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Appraisal mapping: {e}")

    # =========================================================================================
    # PROSPECT OWNERS (Untouched)
    # =========================================================================================
    def process_prospect_owners(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Prospect Owners...")
        
        base_file = "address_ownerships.csv"
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing. Cannot process Prospect Owners.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)

            def safe_str(x):
                if pd.isna(x) or str(x).strip().lower() == 'nan': return ''
                s = str(x).strip()
                return s[:-2] if s.endswith('.0') else s

            zenu_df = pd.DataFrame(index=df.index)

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None

                if target_field == 'property_identifier' and primary_src_field in df.columns:
                    zenu_df[target_field] = "Pr_" + df[primary_src_field].apply(safe_str)
                    continue

                if target_field == 'Property Name':
                    try:
                        target_file = 'addresses.csv' 
                        if rule.get('lookupConfig'):
                            cfg_file = rule.get('lookupConfig')[0].get('targetFile', '')
                            if cfg_file.lower() == 'address.csv': target_file = 'addresses.csv'
                            elif cfg_file: target_file = cfg_file
                            
                        match_key = rule.get('lookupConfig')[0].get('matchKey', 'id') if rule.get('lookupConfig') else 'id'

                        lookup_df = pd.read_sql_query(f'SELECT * FROM "{target_file}"', self.conn)
                        lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                        
                        def build_prop_name(row):
                            u = safe_str(row.get('unit', ''))
                            if u in ['0', '0.0']: u = ''
                            sn = safe_str(row.get('street_no', ''))
                            st = safe_str(row.get('street', ''))
                            sub = safe_str(row.get('suburb', ''))
                            state = safe_str(row.get('state', ''))
                            pc = safe_str(row.get('postcode', ''))
                            
                            street_part = ""
                            if u and sn: street_part = f"{u}/{sn}"
                            elif u: street_part = u
                            elif sn: street_part = sn
                                
                            addr1 = f"{street_part} {st}".strip()
                            
                            parts = []
                            if addr1: parts.append(addr1)
                            if sub: parts.append(sub)
                            
                            state_pc = f"{state} {pc}".strip()
                            if state_pc: parts.append(state_pc)
                            
                            return ", ".join(parts)

                        lookup_df['__built_address'] = lookup_df.apply(build_prop_name, axis=1)
                        mapping_dict = lookup_df.drop_duplicates(subset=[match_key]).set_index(match_key)['__built_address'].to_dict()
                        
                        safe_keys = df[primary_src_field].apply(safe_str)
                        zenu_df[target_field] = safe_keys.map(mapping_dict)
                    except Exception as e:
                        self.engine.log(f"[{self.job_id}] Lookup warning for Property Name: {e}")
                    continue

                if target_field == 'Contact Name':
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
                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                                
                                grouped_lookup = lookup_df.groupby(match_key)[extract_fields[0]].apply(list).to_dict()
                                
                                safe_keys = df[primary_src_field].apply(safe_str)
                                zenu_df[target_field] = safe_keys.map(grouped_lookup)
                                
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            list_cols = [col for col in zenu_df.columns if zenu_df[col].apply(type).eq(list).any()]
            for col in list_cols:
                zenu_df = zenu_df.explode(col)

            if 'Contact Name' in zenu_df.columns and 'contact_identifier' in zenu_df.columns:
                try:
                    cleaned_df = pd.read_sql_query('SELECT "CONTACT_IDENTIFIER", "Contact Name" FROM "contact_cleaned.csv"', self.conn)
                    cleaned_df['CONTACT_IDENTIFIER'] = cleaned_df['CONTACT_IDENTIFIER'].astype(str)
                    name_map = cleaned_df.drop_duplicates(subset=['CONTACT_IDENTIFIER']).set_index('CONTACT_IDENTIFIER')['Contact Name'].to_dict()
                    
                    zenu_df['Contact Name'] = zenu_df['contact_identifier'].astype(str).map(name_map).fillna('')
                except Exception as e:
                    self.engine.log(f"[{self.job_id}] Warning fetching Contact Names: {e}")

            zenu_df = zenu_df.replace(['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], np.nan)
            
            if 'contact_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['contact_identifier'])
            if 'property_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['property_identifier'])
                
            zenu_df = zenu_df.fillna('')

            output_file = os.path.join(self.engine.workspace, "zenu_prospect_owners.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_prospect_owners", self.conn, if_exists='replace', index=False)
            
            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Prospect Owners for Eagle.")
        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Prospect Owners mapping: {e}")

    # =========================================================================================
    # APPRAISAL OWNERS (Untouched)
    # =========================================================================================
    def process_appraisal_owners(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Appraisal Owners...")
        
        base_file = "appraisal_vendors.csv"
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing. Cannot process Appraisal Owners.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)

            def safe_str(x):
                if pd.isna(x) or str(x).strip().lower() == 'nan': return ''
                s = str(x).strip()
                return s[:-2] if s.endswith('.0') else s

            zenu_df = pd.DataFrame(index=df.index)

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None

                if target_field == 'property_identifier' and primary_src_field in df.columns:
                    zenu_df[target_field] = "Appr_" + df[primary_src_field].apply(safe_str)
                    continue

                if target_field == 'Property Name':
                    try:
                        target_file = 'appraisals.csv' 
                        if rule.get('lookupConfig'):
                            cfg_file = rule.get('lookupConfig')[0].get('targetFile', '')
                            if cfg_file.lower() in ['appraisal.csv', 'appraisals.csv']: 
                                target_file = 'appraisals.csv'
                            elif cfg_file: 
                                target_file = cfg_file
                            
                        match_key = rule.get('lookupConfig')[0].get('matchKey', 'id') if rule.get('lookupConfig') else 'id'

                        lookup_df = pd.read_sql_query(f'SELECT * FROM "{target_file}"', self.conn)
                        lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                        
                        def build_prop_name(row):
                            u = safe_str(row.get('unit', ''))
                            if u in ['0', '0.0']: u = ''
                            sn = safe_str(row.get('street_no', ''))
                            st = safe_str(row.get('street', ''))
                            sub = safe_str(row.get('suburb', ''))
                            state = safe_str(row.get('state', ''))
                            pc = safe_str(row.get('postcode', ''))
                            
                            street_part = ""
                            if u and sn: street_part = f"{u}/{sn}"
                            elif u: street_part = u
                            elif sn: street_part = sn
                                
                            addr1 = f"{street_part} {st}".strip()
                            
                            parts = []
                            if addr1: parts.append(addr1)
                            if sub: parts.append(sub)
                            
                            state_pc = f"{state} {pc}".strip()
                            if state_pc: parts.append(state_pc)
                            
                            return ", ".join(parts)

                        lookup_df['__built_address'] = lookup_df.apply(build_prop_name, axis=1)
                        mapping_dict = lookup_df.drop_duplicates(subset=[match_key]).set_index(match_key)['__built_address'].to_dict()
                        
                        safe_keys = df[primary_src_field].apply(safe_str)
                        zenu_df[target_field] = safe_keys.map(mapping_dict)
                    except Exception as e:
                        self.engine.log(f"[{self.job_id}] Lookup warning for Property Name: {e}")
                    continue

                if target_field == 'Contact Name':
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
                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                                
                                grouped_lookup = lookup_df.groupby(match_key)[extract_fields[0]].apply(list).to_dict()
                                
                                safe_keys = df[primary_src_field].apply(safe_str)
                                zenu_df[target_field] = safe_keys.map(grouped_lookup)
                                
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            list_cols = [col for col in zenu_df.columns if zenu_df[col].apply(type).eq(list).any()]
            for col in list_cols:
                zenu_df = zenu_df.explode(col)

            if 'Contact Name' in zenu_df.columns and 'contact_identifier' in zenu_df.columns:
                try:
                    cleaned_df = pd.read_sql_query('SELECT "CONTACT_IDENTIFIER", "Contact Name" FROM "contact_cleaned.csv"', self.conn)
                    cleaned_df['CONTACT_IDENTIFIER'] = cleaned_df['CONTACT_IDENTIFIER'].astype(str)
                    name_map = cleaned_df.drop_duplicates(subset=['CONTACT_IDENTIFIER']).set_index('CONTACT_IDENTIFIER')['Contact Name'].to_dict()
                    
                    zenu_df['Contact Name'] = zenu_df['contact_identifier'].astype(str).map(name_map).fillna('')
                except Exception as e:
                    self.engine.log(f"[{self.job_id}] Warning fetching Contact Names: {e}")

            zenu_df = zenu_df.replace(['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], np.nan)
            
            if 'contact_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['contact_identifier'])
            if 'property_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['property_identifier'])
                
            zenu_df = zenu_df.fillna('')

            output_file = os.path.join(self.engine.workspace, "zenu_appraisal_owners.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_appraisal_owners", self.conn, if_exists='replace', index=False)
            
            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Appraisal Owners for Eagle.")
        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Appraisal Owners mapping: {e}")

    # =========================================================================================
    # BUYER (Untouched)
    # =========================================================================================
    def process_buyer(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Buyer...")
        
        base_file = "purchasers.csv" 
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing. Cannot process Buyers.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)

            def safe_str(x):
                if pd.isna(x) or str(x).strip().lower() == 'nan': return ''
                s = str(x).strip()
                return s[:-2] if s.endswith('.0') else s

            zenu_df = pd.DataFrame(index=df.index)

            contracts_dict = {}
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='contracts.csv';")
            if cursor.fetchone():
                contracts_df = pd.read_sql_query(f'SELECT * FROM "contracts.csv"', self.conn)
                contracts_df['id'] = contracts_df['id'].apply(safe_str)
                contracts_dict = contracts_df.set_index('id').to_dict(orient='index')

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None
                safe_keys = df[primary_src_field].apply(safe_str) if primary_src_field in df.columns else pd.Series()

                if target_field == 'property_identifier':
                    zenu_df[target_field] = safe_keys.map(lambda k: safe_str(contracts_dict.get(k, {}).get('property_id', '')))
                    continue

                if target_field == 'Buyer Solicitor Identifier':
                    try:
                        solicitor_ids = safe_keys.map(lambda k: safe_str(contracts_dict.get(k, {}).get('purchaser_solicitor_id', '')))
                        
                        cleaned_df = pd.read_sql_query('SELECT "Raw ORIG CONTACT_IDENTIFIER", "CONTACT_IDENTIFIER" FROM "contact_cleaned.csv"', self.conn)
                        cleaned_df['Raw ORIG CONTACT_IDENTIFIER'] = cleaned_df['Raw ORIG CONTACT_IDENTIFIER'].apply(safe_str)
                        grouped_lookup = cleaned_df.groupby('Raw ORIG CONTACT_IDENTIFIER')['CONTACT_IDENTIFIER'].apply(list).to_dict()
                        
                        zenu_df[target_field] = solicitor_ids.map(grouped_lookup)
                    except Exception as e:
                        self.engine.log(f"[{self.job_id}] Lookup error for Buyer Solicitor Identifier: {e}")
                    continue

                if target_field == 'property_contract_date':
                    raw_dates = safe_keys.map(lambda k: str(contracts_dict.get(k, {}).get('acceptance_date', '')))
                    clean_dates = raw_dates.str.replace(r'\s*[+-]\d{2}:?\d{2}$', '', regex=True).str.split(' ').str[0].str.strip()
                    
                    parsed = pd.to_datetime(clean_dates, format='%d/%m/%Y', errors='coerce')
                    parsed = parsed.fillna(pd.to_datetime(clean_dates, format='%Y-%m-%d', errors='coerce'))
                    parsed = parsed.fillna(pd.to_datetime(clean_dates, errors='coerce', dayfirst=True))
                    
                    zenu_df[target_field] = parsed.dt.strftime('%d/%m/%Y').fillna('')
                    continue

                if target_field == 'property_sold_price':
                    zenu_df[target_field] = safe_keys.map(lambda k: safe_str(contracts_dict.get(k, {}).get('sale_price', '')))
                    continue

                if target_field == 'Eagle Status':
                    try:
                        prop_ids = safe_keys.map(lambda k: safe_str(contracts_dict.get(k, {}).get('property_id', '')))
                        
                        props_df = pd.read_sql_query('SELECT id, status FROM "properties.csv"', self.conn)
                        props_df['id'] = props_df['id'].apply(safe_str)
                        props_dict = props_df.drop_duplicates(subset=['id']).set_index('id')['status'].to_dict()
                        
                        zenu_df[target_field] = prop_ids.map(props_dict).fillna('')
                    except Exception as e:
                        self.engine.log(f"[{self.job_id}] Lookup error for Eagle Status: {e}")
                    continue

                if action == 'direct':
                    if primary_src_field and primary_src_field in df.columns:
                        zenu_df[target_field] = df[primary_src_field].apply(safe_str)

                elif action == 'static':
                    zenu_df[target_field] = rule.get('valueExpression', '')

                elif action == 'lookup':
                    lookup_configs = rule.get('lookupConfig', [])
                    config = lookup_configs[0] if lookup_configs else {}
                    target_file = config.get('targetFile')
                    match_key = config.get('matchKey')
                    extract_fields = config.get('extractFields', [])

                    if target_file and match_key and extract_fields:
                        try:
                            lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                            lookup_df[match_key] = lookup_df[match_key].apply(safe_str)

                            grouped_lookup = lookup_df.groupby(match_key)[extract_fields[0]].apply(list).to_dict()
                            zenu_df[target_field] = safe_keys.map(grouped_lookup)
                        except Exception as lookup_err:
                            self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            list_cols = [col for col in zenu_df.columns if zenu_df[col].apply(type).eq(list).any()]
            for col in list_cols:
                zenu_df = zenu_df.explode(col)

            zenu_df = zenu_df.replace(['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], np.nan)

            if 'contact_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['contact_identifier'])
            if 'property_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['property_identifier'])

            zenu_df = zenu_df.fillna('')

            output_file = os.path.join(self.engine.workspace, "zenu_buyer.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_buyer", self.conn, if_exists='replace', index=False)

            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Buyers for Eagle.")
        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Buyer mapping: {e}")

    # =========================================================================================
    # VENDOR (Untouched)
    # =========================================================================================
    def process_vendor(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Vendor...")
        
        base_file = "vendors.csv"
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing. Cannot process Vendors.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)

            def safe_str(x):
                if pd.isna(x) or str(x).strip().lower() == 'nan': return ''
                s = str(x).strip()
                return s[:-2] if s.endswith('.0') else s

            zenu_df = pd.DataFrame(index=df.index)

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None
                safe_keys = df[primary_src_field].apply(safe_str) if primary_src_field in df.columns else pd.Series()

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
                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                                
                                # Use grouped list to preserve 1-to-many splits like 1_c1, 1_c2
                                grouped_lookup = lookup_df.groupby(match_key)[extract_fields[0]].apply(list).to_dict()
                                zenu_df[target_field] = safe_keys.map(grouped_lookup)
                                
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            # ==========================================
            # POST-PROCESSING: EXPLODE & CLEANUP
            # ==========================================
            
            list_cols = [col for col in zenu_df.columns if zenu_df[col].apply(type).eq(list).any()]
            for col in list_cols:
                zenu_df = zenu_df.explode(col)

            # Null Cleanup
            zenu_df = zenu_df.replace(['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], np.nan)

            if 'Vendor Identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['Vendor Identifier'])
                
                self.engine.log(f"[{self.job_id}] Filtering Vendor Identifier to exclude _c2 and above...")
                mask_c2_above = zenu_df['Vendor Identifier'].astype(str).str.contains(r'_c[2-9]\d*$', case=False, regex=True)
                zenu_df = zenu_df[~mask_c2_above]

            if 'property_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['property_identifier'])

            zenu_df = zenu_df.fillna('')

            # Export
            output_file = os.path.join(self.engine.workspace, "zenu_vendor.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_vendor", self.conn, if_exists='replace', index=False)

            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Vendors for Eagle.")
        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Vendor mapping: {e}")

    # =========================================================================================
    # ENQUIRIES (Untouched)
    # =========================================================================================
    def process_enquiries(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Enquiries...")
        
        base_file = "notes.csv"
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing. Cannot process Enquiries.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)

            def safe_str(x):
                if pd.isna(x) or str(x).strip().lower() == 'nan': return ''
                s = str(x).strip()
                return s[:-2] if s.endswith('.0') else s
                
            def parse_au_date(date_series):
                clean_str = date_series.astype(str).str.replace(r'\s*[+-]\d{2}:?\d{2}$', '', regex=True).str.split(' ').str[0].str.strip()
                parsed = pd.to_datetime(clean_str, format='%d/%m/%Y', errors='coerce')
                parsed = parsed.fillna(pd.to_datetime(clean_str, format='%Y-%m-%d', errors='coerce'))
                parsed = parsed.fillna(pd.to_datetime(clean_str, errors='coerce', dayfirst=True))
                return parsed

            if 'note_type' in df.columns:
                self.engine.log(f"[{self.job_id}] Pre-filtering notes.csv to isolate Enquiries...")
                df = df[df['note_type'].astype(str).str.strip().str.lower() == 'enquiry'].reset_index(drop=True)
            else:
                self.engine.log(f"[{self.job_id}] WARNING: 'note_type' missing. Proceeding without filter.")

            zenu_df = pd.DataFrame(index=df.index)

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None
                safe_keys = df[primary_src_field].apply(safe_str) if primary_src_field in df.columns else pd.Series()

                if target_field == 'enquiry_notes' and primary_src_field in df.columns:
                    zenu_df[target_field] = df[primary_src_field].astype(str).str.strip().str.replace(r'^\s*-\s*', '', regex=True)
                    zenu_df[target_field] = zenu_df[target_field].replace(['nan', 'NaN', 'None'], '')
                    continue

                if target_field == 'enquiry_date_created' and primary_src_field in df.columns:
                    raw_dates = parse_au_date(df[primary_src_field])
                    zenu_df[target_field] = raw_dates.dt.strftime('%d/%m/%Y').fillna('')
                    continue
                    
                if target_field == 'contact_identifier' and primary_src_field in df.columns:
                    try:
                        cleaned_df = pd.read_sql_query('SELECT "Raw ORIG CONTACT_IDENTIFIER", "CONTACT_IDENTIFIER" FROM "contact_cleaned.csv"', self.conn)
                        cleaned_df['Raw ORIG CONTACT_IDENTIFIER'] = cleaned_df['Raw ORIG CONTACT_IDENTIFIER'].apply(safe_str)
                        
                        cleaned_dict = cleaned_df.drop_duplicates(subset=['Raw ORIG CONTACT_IDENTIFIER'], keep='first').set_index('Raw ORIG CONTACT_IDENTIFIER')['CONTACT_IDENTIFIER'].to_dict()
                        zenu_df[target_field] = safe_keys.map(cleaned_dict).fillna('')
                    except Exception as e:
                        self.engine.log(f"[{self.job_id}] Lookup error for {target_field}: {e}")
                    continue

                if target_field == 'enquiry_identifier' and primary_src_field in df.columns:
                    zenu_df[target_field] = "Enq_" + df[primary_src_field].apply(safe_str)
                    continue

                if target_field == 'enquiry_team_member_1' and primary_src_field in df.columns:
                    try:
                        agents_df = pd.read_sql_query('SELECT user_id, name FROM "agents.csv"', self.conn)
                        agents_df['user_id'] = agents_df['user_id'].apply(safe_str)
                        agents_dict = agents_df.drop_duplicates(subset=['user_id']).set_index('user_id')['name'].to_dict()
                        
                        zenu_df[target_field] = safe_keys.apply(
                            lambda k: str(agents_dict.get(k, '')).strip() if k != '' else ''
                        ).replace(['nan', 'NaN', 'None'], '')
                    except Exception as e:
                        self.engine.log(f"[{self.job_id}] Lookup error for enquiry_team_member_1: {e}")
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
                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                                
                                mapping_dict = lookup_df.drop_duplicates(subset=[match_key], keep='first').set_index(match_key)[extract_fields[0]].to_dict()
                                zenu_df[target_field] = safe_keys.map(mapping_dict)
                                
                                if 'team_member' in target_field.lower() or 'source' in target_field.lower():
                                    zenu_df[target_field] = zenu_df[target_field].astype(str).str.strip().replace(['nan', 'NaN', 'None'], '')
                                
                                zenu_df[target_field] = zenu_df[target_field].fillna('')
                                zenu_df.loc[safe_keys == '', target_field] = ''
                                
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            zenu_df = zenu_df.replace(['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], np.nan)
            
            if 'enquiry_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['enquiry_identifier'])
                
            zenu_df = zenu_df.fillna('')

            output_file = os.path.join(self.engine.workspace, "zenu_enquiries.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_enquiries", self.conn, if_exists='replace', index=False)
            
            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Enquiries for Eagle.")
        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Enquiries mapping: {e}")

    # =========================================================================================
    # CONTACT NOTES (Untouched)
    # =========================================================================================
    def process_contact_notes(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Contact Notes...")
        
        base_file = "notes.csv"
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing. Cannot process Contact Notes.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)

            def safe_str(x):
                if pd.isna(x) or str(x).strip().lower() == 'nan': return ''
                s = str(x).strip()
                return s[:-2] if s.endswith('.0') else s
                
            def parse_au_date(date_series):
                clean_str = date_series.astype(str).str.replace(r'\s*[+-]\d{2}:?\d{2}$', '', regex=True).str.split(' ').str[0].str.strip()
                parsed = pd.to_datetime(clean_str, format='%d/%m/%Y', errors='coerce')
                parsed = parsed.fillna(pd.to_datetime(clean_str, format='%Y-%m-%d', errors='coerce'))
                parsed = parsed.fillna(pd.to_datetime(clean_str, errors='coerce', dayfirst=True))
                return parsed

            # ==========================================
            # MASSIVE SQL-LIKE PRE-FILTER
            # ==========================================
            self.engine.log(f"[{self.job_id}] Applying strict IN and NOT LIKE filters to notes.csv...")
            
            valid_types = [
                'email', 'inbound sms', 'owner added to address', 'owner removed from address', 
                'property alert email', 'sms', 'tenant added to address', 'tenant removed from address', 
                'unsubscribe', 'update appraisal status', 'update property status', 'websitelog'
            ]
            
            if 'note_type' in df.columns:
                df = df[df['note_type'].astype(str).str.strip().str.lower().isin(valid_types)]
                
            exclusions = [
                'sent bulk', 'delivery to', 'contact details updated', 'system - bulk assign category:',
                'mass communicator', 'market report generated', 'matched properties emailed',
                'added by property wizard', 'sent rh automated ecommunication', 'bulk document mail merge',
                'inserted via contact wizard', 'wizard', 'unsubscribed from email communications',
                'contact dominantly merge', 'change of address', 'change of name', 'change of mobile',
                'change of phone', 'email address changed', 'legal description changed',
                'mobile number changed', 'telephone number changed', 'vendor feedback report',
                'vendor report generated', 'unsubscribe from all', 'contact unsubscribed from emails',
                'call placed', 'contact added into mri vault', 'added to quick attendance',
                'contact created by', 'added by property', 'unsubscribed from market report reason:',
                'assigned to distribution list', 'unsubscribed from', 'mobile number (primary)',
                'first point of contact modified by', 'address changed from',
                'telephone number (work) changed', 'name changed from', 'subject: ',
                '{"sms_batch_row_id"', 'contact merged by duplicate', 'sent action trigger email',
                'property status updated', 'pages visited', 'campaign <a href=', 'market update due',
                'market update', 'property alert email', 'follow up from note',
                "appraisal status updated to 'active'", "property status updated to 'draft'",
                "property status updated to 'active'", "property status updated to 'sold'",
                "property status updated to 'let'", "property status updated to 'under offer'",
                "property status updated to 'withdrawn'", "appraisal status updated to 'won'",
                "contract status updated to 'settled'", "contract status updated to 'accepted'",
                "property status updated to 'deleted'", "appraisal status updated to 'lost'",
                "property status updated to 'off market'", "contract status updated to 'finance approved'",
                "contract status updated to 'deposit received'", "contract status updated to 'unconditional'"
            ]
            
            def is_valid_text(t):
                if pd.isna(t) or str(t).strip() == '': return True
                t_lower = str(t).lower().strip()
                for ex in exclusions:
                    if t_lower.startswith(ex):
                        return False
                return True
                
            if 'text' in df.columns:
                df = df[df['text'].apply(is_valid_text)].reset_index(drop=True)
            # ==========================================
            
            zenu_df = pd.DataFrame(index=df.index)
            
            # Pre-load properties.csv to memory for the complex Contact Note concatenation
            props_dict = {}
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='properties.csv';")
            if cursor.fetchone():
                props_df = pd.read_sql_query('SELECT * FROM "properties.csv"', self.conn)
                props_df['id'] = props_df['id'].apply(safe_str)
                
                def build_prop_addr(row):
                    u = safe_str(row.get('unit', ''))
                    if u in ['0', '0.0']: u = ''
                    sn = safe_str(row.get('street_no', ''))
                    st = safe_str(row.get('street', ''))
                    sub = safe_str(row.get('suburb', ''))
                    state = safe_str(row.get('state', ''))
                    pc = safe_str(row.get('postcode', ''))

                    street_part = ""
                    if u and sn: street_part = f"{u}/{sn}"
                    elif u: street_part = u
                    elif sn: street_part = sn
                    
                    addr1 = f"{street_part} {st}".strip()
                    
                    parts = []
                    if addr1: parts.append(addr1)
                    if sub: parts.append(sub)
                    state_pc = f"{state} {pc}".strip()
                    if state_pc: parts.append(state_pc)
                    
                    return ", ".join(parts)
                    
                if not props_df.empty:
                    props_df['_clean_addr'] = props_df.apply(build_prop_addr, axis=1)
                    props_dict = props_df.set_index('id')['_clean_addr'].to_dict()

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None
                safe_keys = df[primary_src_field].apply(safe_str) if primary_src_field in df.columns else pd.Series(dtype=str)

                if target_field == 'contact_note_created_date' and primary_src_field in df.columns:
                    raw_dates = parse_au_date(df[primary_src_field])
                    zenu_df[target_field] = raw_dates.dt.strftime('%d/%m/%Y').fillna('')
                    continue

                if target_field == 'contact_note_team_member' and primary_src_field in df.columns:
                    try:
                        agents_df = pd.read_sql_query('SELECT user_id, name FROM "agents.csv"', self.conn)
                        agents_df['user_id'] = agents_df['user_id'].apply(safe_str)
                        agents_dict = agents_df.drop_duplicates(subset=['user_id']).set_index('user_id')['name'].to_dict()
                        
                        zenu_df[target_field] = safe_keys.apply(
                            lambda k: str(agents_dict.get(k, '')).strip() if k != '' else ''
                        ).replace(['nan', 'NaN', 'None'], '')
                    except Exception as e:
                        self.engine.log(f"[{self.job_id}] Lookup error for contact_note_team_member: {e}")
                    continue

                if target_field == 'contact_identifier' and primary_src_field in df.columns:
                    try:
                        cleaned_df = pd.read_sql_query('SELECT "Raw ORIG CONTACT_IDENTIFIER", "CONTACT_IDENTIFIER" FROM "contact_cleaned.csv"', self.conn)
                        cleaned_df['Raw ORIG CONTACT_IDENTIFIER'] = cleaned_df['Raw ORIG CONTACT_IDENTIFIER'].apply(safe_str)
                        grouped_lookup = cleaned_df.groupby('Raw ORIG CONTACT_IDENTIFIER')['CONTACT_IDENTIFIER'].apply(list).to_dict()
                        
                        zenu_df[target_field] = safe_keys.map(grouped_lookup)
                    except Exception as e:
                        self.engine.log(f"[{self.job_id}] Lookup error for contact_identifier: {e}")
                    continue

                if target_field == 'contact_notes':
                    def build_final_note(row):
                        prop_id = safe_str(row.get('property_id', ''))
                        addr = props_dict.get(prop_id, "")
                        ntype = str(row.get('note_type', '')).strip()
                        text = str(row.get('text', '')).strip()
                        
                        parts = []
                        if addr: parts.append(f"Property: {addr}")
                        else: parts.append("Property:") 
                        
                        if ntype: parts.append(ntype)
                        if text: parts.append(text)
                        
                        return " - ".join(parts)
                        
                    if df.empty:
                        zenu_df[target_field] = pd.Series(dtype=str)
                    else:
                        zenu_df[target_field] = df.apply(build_final_note, axis=1)
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
                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                                
                                mapping_dict = lookup_df.drop_duplicates(subset=[match_key], keep='first').set_index(match_key)[extract_fields[0]].to_dict()
                                zenu_df[target_field] = safe_keys.map(mapping_dict)
                                
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            # ==========================================
            # POST-PROCESSING: EXPLODE & ILLEGAL CHARACTER CLEANUP
            # ==========================================
            list_cols = [col for col in zenu_df.columns if zenu_df[col].apply(type).eq(list).any()]
            for col in list_cols:
                zenu_df = zenu_df.explode(col)

            if not zenu_df.empty:
                for col in zenu_df.columns:
                    zenu_df[col] = zenu_df[col].astype(str).replace(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', regex=True)
                zenu_df = zenu_df.replace(['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], np.nan)
            
            if 'contact_identifier' in zenu_df.columns:
                zenu_df = zenu_df.dropna(subset=['contact_identifier'])
                
            zenu_df = zenu_df.fillna('')

            output_file = os.path.join(self.engine.workspace, "zenu_contact_notes.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_contact_notes", self.conn, if_exists='replace', index=False)
            
            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Contact Notes for Eagle.")
        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Contact Notes mapping: {e}")

    # =========================================================================================
    # PROPERTY NOTES (Updated `agents.csv` query from 'id' to 'user_id')
    # =========================================================================================
    def process_property_notes(self, rules):
        self.engine.log(f"[{self.job_id}] Executing Eagle Transformation for Property Notes...")
        
        base_file = "notes.csv"
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{base_file}';")
            if not cursor.fetchone():
                self.engine.log(f"[{self.job_id}] CRITICAL: '{base_file}' table missing. Cannot process Property Notes.")
                return

            df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)

            def safe_str(x):
                if pd.isna(x) or str(x).strip().lower() == 'nan': return ''
                s = str(x).strip()
                return s[:-2] if s.endswith('.0') else s
                
            def parse_au_date(date_series):
                clean_str = date_series.astype(str).str.replace(r'\s*[+-]\d{2}:?\d{2}$', '', regex=True).str.split(' ').str[0].str.strip()
                parsed = pd.to_datetime(clean_str, format='%d/%m/%Y', errors='coerce')
                parsed = parsed.fillna(pd.to_datetime(clean_str, format='%Y-%m-%d', errors='coerce'))
                parsed = parsed.fillna(pd.to_datetime(clean_str, errors='coerce', dayfirst=True))
                return parsed

            # ==========================================
            # MASSIVE SQL-LIKE PRE-FILTER (Same as Contact Notes)
            # ==========================================
            valid_types = [
                'email', 'inbound sms', 'owner added to address', 'owner removed from address', 
                'property alert email', 'sms', 'tenant added to address', 'tenant removed from address', 
                'unsubscribe', 'update appraisal status', 'update property status', 'websitelog'
            ]
            
            if 'note_type' in df.columns:
                df = df[df['note_type'].astype(str).str.strip().str.lower().isin(valid_types)]
                
            exclusions = [
                'sent bulk', 'delivery to', 'contact details updated', 'system - bulk assign category:',
                'mass communicator', 'market report generated', 'matched properties emailed',
                'added by property wizard', 'sent rh automated ecommunication', 'bulk document mail merge',
                'inserted via contact wizard', 'wizard', 'unsubscribed from email communications',
                'contact dominantly merge', 'change of address', 'change of name', 'change of mobile',
                'change of phone', 'email address changed', 'legal description changed',
                'mobile number changed', 'telephone number changed', 'vendor feedback report',
                'vendor report generated', 'unsubscribe from all', 'contact unsubscribed from emails',
                'call placed', 'contact added into mri vault', 'added to quick attendance',
                'contact created by', 'added by property', 'unsubscribed from market report reason:',
                'assigned to distribution list', 'unsubscribed from', 'mobile number (primary)',
                'first point of contact modified by', 'address changed from',
                'telephone number (work) changed', 'name changed from', 'subject: ',
                '{"sms_batch_row_id"', 'contact merged by duplicate', 'sent action trigger email',
                'property status updated', 'pages visited', 'campaign <a href=', 'market update due',
                'market update', 'property alert email', 'follow up from note',
                "appraisal status updated to 'active'", "property status updated to 'draft'",
                "property status updated to 'active'", "property status updated to 'sold'",
                "property status updated to 'let'", "property status updated to 'under offer'",
                "property status updated to 'withdrawn'", "appraisal status updated to 'won'",
                "contract status updated to 'settled'", "contract status updated to 'accepted'",
                "property status updated to 'deleted'", "appraisal status updated to 'lost'",
                "property status updated to 'off market'", "contract status updated to 'finance approved'",
                "contract status updated to 'deposit received'", "contract status updated to 'unconditional'"
            ]
            
            def is_valid_text(t):
                if pd.isna(t) or str(t).strip() == '': return True
                t_lower = str(t).lower().strip()
                for ex in exclusions:
                    if t_lower.startswith(ex):
                        return False
                return True
                
            if 'text' in df.columns:
                df = df[df['text'].apply(is_valid_text)].reset_index(drop=True)
            # ==========================================
            
            zenu_df = pd.DataFrame(index=df.index)
            
            # Pre-load contact_cleaned.csv to memory for the complex Property Note concatenation
            contact_dict = {}
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='contact_cleaned.csv';")
            if cursor.fetchone():
                cleaned_df = pd.read_sql_query('SELECT "Raw ORIG CONTACT_IDENTIFIER", "Contact Name" FROM "contact_cleaned.csv"', self.conn)
                cleaned_df['Raw ORIG CONTACT_IDENTIFIER'] = cleaned_df['Raw ORIG CONTACT_IDENTIFIER'].apply(safe_str)
                contact_dict = cleaned_df.drop_duplicates(subset=['Raw ORIG CONTACT_IDENTIFIER'], keep='first').set_index('Raw ORIG CONTACT_IDENTIFIER')['Contact Name'].to_dict()

            for rule in rules:
                target_field = rule.get('targetField')
                action = rule.get('action')
                sources = rule.get('sources', [])
                
                primary_src_field = sources[0]['field'] if sources else None
                safe_keys = df[primary_src_field].apply(safe_str) if primary_src_field in df.columns else pd.Series(dtype=str)

                if target_field == 'property_note_created_date' and primary_src_field in df.columns:
                    raw_dates = parse_au_date(df[primary_src_field])
                    zenu_df[target_field] = raw_dates.dt.strftime('%d/%m/%Y').fillna('')
                    continue

                if target_field == 'property_note_team_member' and primary_src_field in df.columns:
                    try:
                        # FIXED: Select user_id instead of id
                        agents_df = pd.read_sql_query('SELECT user_id, name FROM "agents.csv"', self.conn)
                        agents_df['user_id'] = agents_df['user_id'].apply(safe_str)
                        agents_dict = agents_df.drop_duplicates(subset=['user_id']).set_index('user_id')['name'].to_dict()
                        
                        zenu_df[target_field] = safe_keys.apply(
                            lambda k: str(agents_dict.get(k, '')).strip() if k != '' else ''
                        ).replace(['nan', 'NaN', 'None'], '')
                    except Exception as e:
                        self.engine.log(f"[{self.job_id}] Lookup error for property_note_team_member: {e}")
                    continue

                if target_field == 'property_notes':
                    def build_prop_note(row):
                        cid = safe_str(row.get('contact_id', ''))
                        cname = contact_dict.get(cid, '')
                        ntype = str(row.get('note_type', '')).strip()
                        text = str(row.get('text', '')).strip()
                        
                        parts = []
                        if cname: parts.append(f"Regarding {cname}")
                        else: parts.append("Regarding Contact")
                        
                        if ntype: parts.append(ntype)
                        if text: parts.append(text)
                        
                        return " - ".join(parts)
                        
                    if df.empty:
                        zenu_df[target_field] = pd.Series(dtype=str)
                    else:
                        zenu_df[target_field] = df.apply(build_prop_note, axis=1)
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
                                lookup_df = pd.read_sql_query(f'SELECT "{match_key}", "{extract_fields[0]}" FROM "{target_file}"', self.conn)
                                lookup_df[match_key] = lookup_df[match_key].apply(safe_str)
                                
                                mapping_dict = lookup_df.drop_duplicates(subset=[match_key], keep='first').set_index(match_key)[extract_fields[0]].to_dict()
                                zenu_df[target_field] = safe_keys.map(mapping_dict)
                                
                            except Exception as lookup_err:
                                self.engine.log(f"[{self.job_id}] Lookup warning for {target_field}: {lookup_err}")

            # ==========================================
            # POST-PROCESSING: EXPLODE & ILLEGAL CHARACTER CLEANUP
            # ==========================================
            list_cols = [col for col in zenu_df.columns if zenu_df[col].apply(type).eq(list).any()]
            for col in list_cols:
                zenu_df = zenu_df.explode(col)

            if not zenu_df.empty:
                for col in zenu_df.columns:
                    zenu_df[col] = zenu_df[col].astype(str).replace(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', regex=True)
                zenu_df = zenu_df.replace(['nan', 'NaN', 'NAN', 'None', 'NONE', 'none', '<NA>', ''], np.nan)
            
            # Per JSON instructions: DO NOT dropna for property_identifier! Let it export even if blank.
                
            zenu_df = zenu_df.fillna('')

            output_file = os.path.join(self.engine.workspace, "zenu_property_notes.xlsx")
            zenu_df.to_excel(output_file, index=False, engine='openpyxl')
            zenu_df.to_sql("zenu_property_notes", self.conn, if_exists='replace', index=False)
            
            self.engine.log(f"[{self.job_id}] SUCCESS: Cleaned and mapped {len(zenu_df)} Property Notes for Eagle.")
        except Exception as e:
            self.engine.log(f"[{self.job_id}] ERROR in Property Notes mapping: {e}")