import pandas as pd
import os
import re
from collections import Counter

class VaultREProcessor:
    def __init__(self, engine):
        self.engine = engine
        self.conn = engine.conn
        self.job_id = engine.job_id
        self.workspace = engine.workspace
        self.log = engine.log

        # State Flag for Performance Cache
        self.dicts_loaded = False

        # Global Dictionaries
        self.contact_cleaned_mapping = {}
        self.req_prop_type_map = {}
        self.req_suburb_map = {}
        
        # AllProperties Dictionaries & DataFrames
        self.staff_map = {}
        self.buildings_map = {}
        self.props_df = pd.DataFrame()
        self.sale_life_df = pd.DataFrame()
        self.lease_life_df = pd.DataFrame()

    def _build_global_dictionaries(self):
        """Builds caching dictionaries specifically for VaultRE."""
        if self.dicts_loaded: return
        self.log(f"[{self.job_id}] Building VaultRE Global Cache Dictionaries...")

        try:
            clean_df = pd.read_sql_query('SELECT * FROM "contact_cleaned.csv"', self.conn)
            match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'ORIG CONTACT_IDENTIFIER'
            
            if match_col in clean_df.columns and 'CONTACT_IDENTIFIER' in clean_df.columns:
                clean_df['CONTACT_IDENTIFIER'] = clean_df['CONTACT_IDENTIFIER'].astype(str).str.strip()
                clean_df[match_col] = clean_df[match_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()

                # For secondary lookups (like Solicitor ID), we still restrict to _c1 to prevent cross-multiplying
                primary_mask = clean_df['CONTACT_IDENTIFIER'].str.contains(r'_c1$|_c$', regex=True)
                if primary_mask.any():
                    clean_df_primary = clean_df[primary_mask].drop_duplicates(subset=[match_col])
                else:
                    clean_df_primary = clean_df.drop_duplicates(subset=[match_col])
                    
                self.contact_cleaned_mapping = clean_df_primary.set_index(match_col)['CONTACT_IDENTIFIER'].to_dict()
        except Exception as e:
            self.log(f"[{self.job_id}] Warning: Error building contact map: {e}")

        try:
            pt_df = pd.read_sql_query('SELECT * FROM "BuyRentRequirementsPropType.csv"', self.conn)
            pt_df.columns = pt_df.columns.str.strip().str.lower()
            if 'requirementid' in pt_df.columns and 'propertytype' in pt_df.columns:
                pt_df['requirementid'] = pt_df['requirementid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                pt_df['propertytype'] = pt_df['propertytype'].astype(str).str.strip()
                self.req_prop_type_map = pt_df.groupby('requirementid')['propertytype'].apply(lambda x: ','.join(x.dropna().unique())).to_dict()
        except Exception as e: pass

        try:
            sub_df = pd.read_sql_query('SELECT * FROM "BuyRentRequirementsSuburb.csv"', self.conn)
            sub_df.columns = sub_df.columns.str.strip().str.lower()
            if 'requirementid' in sub_df.columns and 'suburb' in sub_df.columns:
                sub_df['requirementid'] = sub_df['requirementid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                sub_df['suburb'] = sub_df['suburb'].astype(str).str.strip()
                self.req_suburb_map = sub_df.groupby('requirementid')['suburb'].apply(lambda x: ','.join(x.dropna().unique())).to_dict()
        except Exception as e: pass

        # IMPROVED STAFF MAP: Safely ensures FirstName and LastName are flawlessly concatenated
        try:
            staff_df = pd.read_sql_query('SELECT * FROM "Staff.csv"', self.conn)
            staff_df.columns = staff_df.columns.str.strip().str.lower()
            if 'userid' in staff_df.columns:
                staff_df['userid'] = staff_df['userid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                
                fn = staff_df.get('firstname', pd.Series([''] * len(staff_df))).fillna('').astype(str)
                ln = staff_df.get('lastname', pd.Series([''] * len(staff_df))).fillna('').astype(str)
                
                fn = fn.replace({'nan': '', 'None': '', 'null': ''}).str.strip()
                ln = ln.replace({'nan': '', 'None': '', 'null': ''}).str.strip()
                
                staff_df['full_name'] = (fn + ' ' + ln).str.strip()
                self.staff_map = staff_df.set_index('userid')['full_name'].to_dict()
        except Exception as e: pass

        try:
            bldg_df = pd.read_sql_query('SELECT buildingid, buildingname FROM "Buildings.csv"', self.conn)
            bldg_df.columns = bldg_df.columns.str.strip().str.lower()
            if 'buildingid' in bldg_df.columns:
                bldg_df['buildingid'] = bldg_df['buildingid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                self.buildings_map = bldg_df.set_index('buildingid')['buildingname'].to_dict()
        except Exception as e: pass

        try:
            props = pd.read_sql_query('SELECT * FROM "Properties.csv"', self.conn)
            props.columns = props.columns.str.strip().str.lower()
            if 'propertyid' in props.columns:
                props['propertyid_clean'] = props['propertyid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                self.props_df = props.drop_duplicates('propertyid_clean').set_index('propertyid_clean')
        except Exception as e: pass

        try:
            pls = pd.read_sql_query('SELECT * FROM "PropertyLifeSale.csv"', self.conn)
            pls.columns = pls.columns.str.strip().str.lower()
            if 'salelifeid' in pls.columns:
                pls['salelifeid_clean'] = pls['salelifeid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                self.sale_life_df = pls.drop_duplicates('salelifeid_clean').set_index('salelifeid_clean')
        except Exception as e: pass

        try:
            pll = pd.read_sql_query('SELECT * FROM "PropertyLifeLease.csv"', self.conn)
            pll.columns = pll.columns.str.strip().str.lower()
            if 'leaselifeid' in pll.columns:
                pll['leaselifeid_clean'] = pll['leaselifeid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                self.lease_life_df = pll.drop_duplicates('leaselifeid_clean').set_index('leaselifeid_clean')
        except Exception as e: pass

        self.dicts_loaded = True

    def process_group(self, group_name, rules):
        zenu_output = pd.DataFrame()
        
        self._build_global_dictionaries()

        source_files = [rule.get("sources", [{}])[0].get("file") for rule in rules if rule.get("sources")]
        if not source_files:
            self.log(f"[{self.job_id}] Skipping {group_name}: No base file found.")
            return

        base_file = Counter([f for f in source_files if f]).most_common(1)[0][0]

        # ---------------------------------------------------------
        # 2. LOAD BASE DATA 
        # ---------------------------------------------------------
        if group_name == "Contact Requirement":
            try:
                cursor = self.conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [t[0] for t in cursor.fetchall()]
                clean_file = next((t for t in tables if t.lower() == 'contact_cleaned.csv'), None)
                
                req_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
                req_df.columns = req_df.columns.str.strip().str.lower()

                if clean_file:
                    clean_df = pd.read_sql_query(f'SELECT * FROM "{clean_file}"', self.conn)
                    match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'ORIG CONTACT_IDENTIFIER'
                    
                    clean_df[match_col] = clean_df[match_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    req_df['contactid_clean'] = req_df['contactid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()

                    base_df = pd.merge(clean_df, req_df, left_on=match_col, right_on='contactid_clean', how='inner')
                else:
                    base_df = req_df
            except Exception as e:
                self.log(f"[{self.job_id}] Error building split contact base table: {e}")
                return

        elif group_name in ["AllProperties", "Prospect"]:
            try:
                plh = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
                plh.columns = plh.columns.str.strip().str.lower()
                
                # Check if base file contains life IDs (e.g., PropertyLifeHistory)
                if 'salelifeid' in plh.columns and 'leaselifeid' in plh.columns:
                    sale_mask = plh['salelifeid'].notna() & (plh['salelifeid'].astype(str).str.strip() != '') & (plh['salelifeid'].astype(str).str.strip().str.lower() != 'nan')
                    lease_mask = plh['leaselifeid'].notna() & (plh['leaselifeid'].astype(str).str.strip() != '') & (plh['leaselifeid'].astype(str).str.strip().str.lower() != 'nan')
                    
                    sale_plh = plh[sale_mask].copy()
                    sale_plh['active_life_id'] = sale_plh['salelifeid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    sale_plh['life_type'] = 'Sale'
                    
                    lease_plh = plh[lease_mask].copy()
                    lease_plh['active_life_id'] = lease_plh['leaselifeid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    lease_plh['life_type'] = 'Lease'
                    
                    neither_mask = ~sale_mask & ~lease_mask
                    prop_plh = plh[neither_mask].copy()
                    prop_plh['active_life_id'] = pd.NA
                    prop_plh['life_type'] = 'Prospect_Only'
                    
                    base_df = pd.concat([sale_plh, lease_plh, prop_plh], ignore_index=True)
                else:
                    # Fallback if they map Properties.csv directly
                    base_df = plh.copy()
                    base_df['active_life_id'] = pd.NA
                    base_df['life_type'] = 'Prospect_Only'

            except Exception as e:
                self.log(f"[{self.job_id}] Error building split property base table: {e}")
                return

        elif group_name == "Contact Property Relationship":
            try:
                dfs = []
                for tbl, role, life_col in [("Owners.csv", "SELLER", "salelifeid"), ("Landlords.csv", "LANDLORD", "leaselifeid")]:
                    try:
                        df_temp = pd.read_sql_query(f'SELECT * FROM "{tbl}"', self.conn)
                        df_temp.columns = df_temp.columns.str.strip().str.lower()
                        df_temp['source_role'] = role
                        df_temp['unified_life_id'] = df_temp.get(life_col, pd.NA)
                        df_temp['contactid_clean'] = df_temp.get('contactid', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                        dfs.append(df_temp)
                    except: pass
                
                if dfs:
                    raw_df = pd.concat(dfs, ignore_index=True)
                    try:
                        # INNER JOIN expands rows for _c1, _c2, _c3
                        clean_df = pd.read_sql_query('SELECT * FROM "contact_cleaned.csv"', self.conn)
                        match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'ORIG CONTACT_IDENTIFIER'
                        clean_df[match_col] = clean_df[match_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                        base_df = pd.merge(clean_df, raw_df, left_on=match_col, right_on='contactid_clean', how='inner')
                    except:
                        base_df = raw_df
                else:
                    self.log(f"[{self.job_id}] No Owners or Landlords data found for {group_name}.")
                    return
            except Exception as e:
                self.log(f"[{self.job_id}] Error building base table for {group_name}: {e}")
                return

        # ---------------------------------------------------------
        # VENDOR / BUYER SPECIFIC BASE LOADER
        # ---------------------------------------------------------
        elif group_name in ["Vendor", "Buyer"]:
            try:
                dfs = []
                if group_name == "Vendor":
                    for tbl, role, life_col in [("Owners.csv", "SELLER", "salelifeid"), ("Landlords.csv", "SELLER", "leaselifeid")]:
                        try:
                            df_temp = pd.read_sql_query(f'SELECT * FROM "{tbl}"', self.conn)
                            df_temp.columns = df_temp.columns.str.strip().str.lower()
                            df_temp['source_role'] = role
                            df_temp['unified_life_id'] = df_temp.get(life_col, pd.NA)
                            df_temp['contactid_clean'] = df_temp.get('contactid', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                            dfs.append(df_temp)
                        except: pass
                elif group_name == "Buyer":
                    try:
                        df_temp = pd.read_sql_query('SELECT * FROM "Purchasers.csv"', self.conn)
                        df_temp.columns = df_temp.columns.str.strip().str.lower()
                        df_temp['source_role'] = "PURCHASER"
                        df_temp['unified_life_id'] = df_temp.get('salelifeid', pd.NA)
                        df_temp['contactid_clean'] = df_temp.get('contactid', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                        dfs.append(df_temp)
                    except: pass
                
                if dfs:
                    base_df = pd.concat(dfs, ignore_index=True)
                else:
                    self.log(f"[{self.job_id}] No data found for {group_name}.")
                    return
            except Exception as e:
                self.log(f"[{self.job_id}] Error building base table for {group_name}: {e}")
                return

        else:
            try:
                base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
                base_df.columns = base_df.columns.str.strip().str.lower()
                
                # Universal 1-to-Many Expansion for Generic Groups (Tasks, Notes, Enquiries, Inspections)
                if 'contactid' in base_df.columns:
                    try:
                        clean_df = pd.read_sql_query('SELECT * FROM "contact_cleaned.csv"', self.conn)
                        match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'ORIG CONTACT_IDENTIFIER'
                        clean_df[match_col] = clean_df[match_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                        
                        base_df['contactid_clean'] = base_df['contactid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                        base_df = pd.merge(clean_df, base_df, left_on=match_col, right_on='contactid_clean', how='inner')
                    except: pass
                    
            except Exception as e:
                self.log(f"[{self.job_id}] Error loading base table {base_file}: {e}")
                return

        # ---------------------------------------------------------
        # PROCESSING LOGIC LOOP
        # ---------------------------------------------------------
        if group_name == "Contact Requirement":
            for rule in rules:
                target_field = rule.get("targetField")

                if target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', pd.NA)

                elif target_field == "contact_criteria_category":
                    def clean_category(val):
                        if pd.isna(val): return pd.NA
                        val_clean = str(val).strip()
                        if val_clean.upper() == 'LAND': return 'Residential'
                        return val_clean
                    zenu_output[target_field] = base_df.get('class', pd.Series(dtype=object)).apply(clean_category)

                elif target_field == "contact_criteria_sale_method":
                    is_sale = pd.to_numeric(base_df.get('issale', 0), errors='coerce').fillna(0)
                    is_lease = pd.to_numeric(base_df.get('islease', 0), errors='coerce').fillna(0)

                    def get_method(sale, lease):
                        if sale == 1: return "SALE"
                        if lease == 1: return "LEASE"
                        return "SALE"
                    zenu_output[target_field] = [get_method(s, l) for s, l in zip(is_sale, is_lease)]

                elif target_field in ["contact_criteria_price_from", "contact_criteria_price_to"]:
                    source_col = "minprice" if "from" in target_field else "maxprice"
                    def clean_price_zeros(val):
                        if pd.isna(val): return pd.NA
                        v_str = str(val).strip()
                        if v_str.lower() in ['nan', 'none', 'null', '', '0', '0.0']: return pd.NA
                        return re.sub(r'\.0$', '', v_str)
                    zenu_output[target_field] = base_df.get(source_col, pd.Series(dtype=object)).apply(clean_price_zeros)

                elif target_field in ["contact_criteria_bedrooms", "contact_criteria_bathrooms", "contact_criteria_carspaces", "contact_criteria_land_from", "contact_criteria_land_to"]:
                    src_col_map = {
                        "contact_criteria_bedrooms": "minbed", "contact_criteria_bathrooms": "maxbath",
                        "contact_criteria_carspaces": "mincar", "contact_criteria_land_from": "minlandarea",
                        "contact_criteria_land_to": "maxlandarea"
                    }
                    def clean_req_zeros(val):
                        if pd.isna(val): return pd.NA
                        v_str = str(val).strip()
                        if v_str.lower() in ['nan', 'none', 'null', '', '0', '0.0']: return pd.NA
                        return re.sub(r'\.0$', '', v_str)
                    zenu_output[target_field] = base_df.get(src_col_map[target_field], pd.Series(dtype=object)).apply(clean_req_zeros)

                elif target_field == "contact_criteria_land_unit":
                    def clean_unit(val):
                        if pd.isna(val): return pd.NA
                        v = str(val).strip().lower()
                        if v in ['sqm', 'acre', 'hectare']: return v
                        return pd.NA
                    zenu_output[target_field] = base_df.get('landareatype', pd.Series(dtype=object)).apply(clean_unit)

                elif target_field == "contact_criteria_property_type":
                    req_ids = base_df.get('requirementid', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = req_ids.map(self.req_prop_type_map)

                elif target_field == "contact_criteria_suburbs":
                    req_ids = base_df.get('requirementid', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = req_ids.map(self.req_suburb_map)


        elif group_name in ["AllProperties", "Prospect"]:
            def strip_time(d_str):
                if pd.isna(d_str): return pd.NA
                raw = str(d_str).strip()
                bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00']
                if raw.lower() in bad_dates: return pd.NA
                return raw.split(' ')[0].split('T')[0]

            def get_life_val(active_id, life_type, col_name):
                if pd.isna(active_id): return pd.NA
                if life_type == 'Sale' and not self.sale_life_df.empty and active_id in self.sale_life_df.index:
                    return self.sale_life_df.at[active_id, col_name]
                if life_type == 'Lease' and not self.lease_life_df.empty and active_id in self.lease_life_df.index:
                    return self.lease_life_df.at[active_id, col_name]
                return pd.NA

            def get_prop_val(pid, col_name):
                if pd.isna(pid): return pd.NA
                pid_clean = str(pid).replace('.0', '').strip()
                if not self.props_df.empty and pid_clean in self.props_df.index:
                    return self.props_df.at[pid_clean, col_name]
                return pd.NA
                
            for rule in rules:
                target_field = rule.get("targetField")

                if target_field.lower() == "property_identifier":
                    def format_id(row):
                        if group_name == "Prospect":
                            pid = str(row.get('propertyid', '')).replace('.0', '').strip()
                            return f"Pr_{pid}" if pid and pid.lower() != 'nan' else pd.NA

                        life_id = row['active_life_id']
                        if pd.isna(life_id): 
                            pid = str(row.get('propertyid', '')).replace('.0', '').strip()
                            return f"Pr_{pid}" if pid else pd.NA
                        
                        status = str(get_life_val(life_id, row['life_type'], 'status')).strip().lower()
                        if status in ['prospect', 'not currently listed', 'prospect/not currently listed', 'nan', 'none', '']:
                            return f"Pr_{life_id}"
                        elif status == 'appraisal':
                            return f"Appr_{life_id}"
                        else:
                            return str(life_id)
                    zenu_output[target_field] = base_df.apply(format_id, axis=1)

                elif target_field.lower() == "property_timeline_status":
                    if group_name == "Prospect":
                        zenu_output[target_field] = "Prospect"
                    else:
                        zenu_output[target_field] = base_df.apply(lambda row: get_life_val(row['active_life_id'], row['life_type'], 'status'), axis=1)
                    
                elif target_field.lower() == "property_sale_method":
                    zenu_output[target_field] = base_df['life_type'].apply(lambda x: 'Lease' if x == 'Lease' else ('Sale' if x == 'Sale' else pd.NA))
                
                elif target_field.lower() in ["property_type", "property_unit_number", "property_street_number", "property_street_name", 
                                      "property_suburb", "property_postcode", "property_state", "property_bedrooms", 
                                      "property_bathrooms", "property_year_built", "property_toilets", "property_garages", 
                                      "property_carports", "property_open_parking_spaces"]:
                    
                    col_map = {
                        "property_type": "type", "property_unit_number": "unit", "property_street_number": "streetnum",
                        "property_street_name": "street", "property_suburb": "suburb", "property_postcode": "postcode",
                        "property_state": "state", "property_bedrooms": "bedrooms", "property_bathrooms": "bathrooms",
                        "property_year_built": "yearbuilt", "property_toilets": "toilets", "property_garages": "garages",
                        "property_carports": "carports", "property_open_parking_spaces": "openparkingspaces"
                    }
                    src_col = col_map[target_field.lower()]
                    
                    def get_and_clean_prop_val(pid):
                        val = get_prop_val(pid, src_col)
                        if pd.isna(val): return pd.NA
                        
                        v_str = str(val).strip()
                        if v_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        
                        zero_blank_fields = [
                            "property_year_built", "property_toilets", "property_garages", 
                            "property_carports", "property_open_parking_spaces", 
                            "property_bedrooms", "property_bathrooms"
                        ]
                        if target_field.lower() in zero_blank_fields:
                            if v_str in ['0', '0.0']:
                                return pd.NA
                            return re.sub(r'\.0$', '', v_str)
                            
                        return val
                        
                    zenu_output[target_field] = base_df['propertyid'].apply(get_and_clean_prop_val)
                    
                elif target_field.lower() == "property_full_address":
                    def build_address(pid):
                        def _clean_addr_part(col):
                            v = str(get_prop_val(pid, col))
                            if v.lower() in ['nan', 'none', 'null', '0', '0.0', '']: return ''
                            return re.sub(r'\.0$', '', v).strip()

                        unit = _clean_addr_part('unit')
                        st_num = _clean_addr_part('streetnum')
                        st_name = _clean_addr_part('street')
                        suburb = _clean_addr_part('suburb')
                        state = _clean_addr_part('state')
                        postcode = _clean_addr_part('postcode')
                        
                        parts = []
                        st_parts = []
                        if unit and st_num: st_parts.append(f"{unit}/{st_num}")
                        elif unit: st_parts.append(unit)
                        elif st_num: st_parts.append(st_num)
                        
                        if st_name: st_parts.append(st_name)
                        street_address = " ".join(st_parts).strip()
                        state_pc = " ".join([p for p in [state, postcode] if p]).strip()
                        
                        if street_address: parts.append(street_address)
                        if suburb: parts.append(suburb)
                        if state_pc: parts.append(state_pc)
                        
                        res = ", ".join(parts).strip()
                        return res if res else pd.NA
                        
                    zenu_output[target_field] = base_df['propertyid'].apply(build_address)
                    
                elif target_field.lower() == "property_category":
                    def map_category(pid):
                        val = str(get_prop_val(pid, 'class')).strip().lower()
                        if val == 'business': return 'Commercial'
                        if val == 'holiday rental': return 'Residential'
                        if val == 'land': return 'Rural'
                        return str(get_prop_val(pid, 'class')).title() if val != 'nan' else pd.NA
                    zenu_output[target_field] = base_df['propertyid'].apply(map_category)

                elif target_field.lower() == "property_land_size_m2":
                    def calc_land(pid):
                        area = pd.to_numeric(get_prop_val(pid, 'landarea'), errors='coerce')
                        if pd.isna(area) or area == 0: return pd.NA
                        atype = str(get_prop_val(pid, 'landareatype')).strip().lower()
                        if atype == 'hectare': return area * 10000
                        if atype == 'acre': return area * 4046.86
                        return area
                    zenu_output[target_field] = base_df['propertyid'].apply(calc_land)

                elif target_field.lower() == "listing_land_size_system":
                    # Land size unit of measure from Properties.LandAreaType.
                    # Zenu only accepts sqm / acre / hectare / square (blank defaults to sqm).
                    def map_land_system(pid):
                        atype = str(get_prop_val(pid, 'landareatype')).strip().lower()
                        if atype in ['nan', 'none', 'null', '']: return pd.NA
                        if atype.startswith('hectare'): return 'hectare'
                        if atype.startswith('acre'): return 'acre'
                        # "squares" is the AU building-area unit; "square metres" is not
                        if atype.startswith('square') and 'metre' not in atype and 'meter' not in atype:
                            return 'square'
                        return 'sqm'
                    zenu_output[target_field] = base_df['propertyid'].apply(map_land_system)


                elif target_field.lower() == "property_building_name":
                    def get_bldg(pid):
                        b_id = str(get_prop_val(pid, 'buildingid')).replace('.0', '').strip()
                        return self.buildings_map.get(b_id, pd.NA) if b_id != 'nan' else pd.NA
                    zenu_output[target_field] = base_df['propertyid'].apply(get_bldg)

                elif target_field.lower() == "property_modified_date":
                    zenu_output[target_field] = base_df['propertyid'].apply(lambda pid: strip_time(get_prop_val(pid, 'modifydate')))
                    
                elif target_field.lower() in ["property_appraisal_date", "property_search_price", "property_vendor_price", "display price", "listing date", "display rent"]:
                    col_map = {
                        "property_appraisal_date": "appraisaldate", "property_search_price": "publishpricefrom",
                        "property_vendor_price": "publishpriceto", "display price": "pricetext",
                        "listing date": "authoritystart", "display rent": "publishpricefrom"
                    }
                    src_col = col_map[target_field.lower()]
                    
                    def get_life_date_price(row):
                        if target_field.lower() == "display rent" and row['life_type'] != 'Lease': return pd.NA
                        val = get_life_val(row['active_life_id'], row['life_type'], src_col)
                        
                        if pd.isna(val): return pd.NA
                        
                        if 'date' in target_field.lower() or target_field.lower() == "listing date":
                            return strip_time(val)
                            
                        if target_field.lower() in ["property_search_price", "property_vendor_price", "display price", "display rent"]:
                            v_str = str(val).strip()
                            if v_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                            v_str = re.sub(r'\.0$', '', v_str)
                            
                            if target_field.lower() in ["property_search_price", "property_vendor_price"] and v_str in ['0', '0.0']: 
                                return pd.NA
                            
                            return v_str

                        return val
                    zenu_output[target_field] = base_df.apply(get_life_date_price, axis=1)

                elif target_field.lower() in ["property_unconditional_date", "property_settlement_date", "sold price", "contract date"]:
                    col_map = {
                        "property_unconditional_date": ["unconditionalactioned", "unconditional"], 
                        "property_settlement_date": ["settlementactioned", "settlement"],
                        "sold price": ["saleprice"], 
                        "contract date": ["conditionalactioned", "conditional"]
                    }
                    cols = col_map[target_field.lower()]
                    
                    def get_sale_val(row):
                        if row['life_type'] != 'Sale': return pd.NA
                        val = pd.NA
                        for c in cols:
                            v = get_life_val(row['active_life_id'], 'Sale', c)
                            if pd.notna(v) and str(v).strip().lower() not in ['nan', 'none', '']:
                                val = v
                                break
                                
                        if 'date' in target_field.lower() or target_field.lower() == "contract date":
                            return strip_time(val)
                            
                        if target_field.lower() == "sold price" and pd.notna(val):
                            v_str = str(val).strip()
                            if v_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                            return re.sub(r'\.0$', '', v_str)
                            
                        return val
                    zenu_output[target_field] = base_df.apply(get_sale_val, axis=1)

                elif target_field.lower() in ["property_team_member_1", "property_team_member_2"]:
                    agent_col = "publishagentid1" if target_field.lower() == "property_team_member_1" else "publishagentid2"
                    def get_agent(row):
                        aid = str(get_life_val(row['active_life_id'], row['life_type'], agent_col)).replace('.0', '').strip()
                        return self.staff_map.get(aid, pd.NA) if aid != 'nan' else pd.NA
                    zenu_output[target_field] = base_df.apply(get_agent, axis=1)

        elif group_name == "Contact Property Relationship":
            def get_life_val(active_id, life_type, col_name):
                if pd.isna(active_id): return pd.NA
                if life_type == 'Sale' and not self.sale_life_df.empty and active_id in self.sale_life_df.index:
                    return self.sale_life_df.at[active_id, col_name]
                if life_type == 'Lease' and not self.lease_life_df.empty and active_id in self.lease_life_df.index:
                    return self.lease_life_df.at[active_id, col_name]
                return pd.NA

            for rule in rules:
                target_field = rule.get("targetField")

                if target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', pd.NA)

                elif target_field == "contact_sale_type":
                    zenu_output[target_field] = base_df.get('source_role', 'SELLER')

                elif target_field == "property_identifier":
                    def format_id(row):
                        lid = row.get('unified_life_id')
                        if pd.isna(lid): return pd.NA
                        lid_clean = str(lid).replace('.0', '').strip()
                        if lid_clean in ['nan', 'none', '']: return pd.NA
                        
                        life_type = 'Sale' if row.get('source_role') == 'SELLER' else 'Lease'
                        status = str(get_life_val(lid_clean, life_type, 'status')).strip().lower()
                        
                        if status in ['prospect', 'not currently listed', 'prospect/not currently listed', 'nan', 'none', '']:
                            return f"Pr_{lid_clean}"
                        elif status == 'appraisal':
                            return f"Appr_{lid_clean}"
                        else:
                            return str(lid_clean)
                    zenu_output[target_field] = base_df.apply(format_id, axis=1)

                elif target_field == "Property Status":
                    def get_status(row):
                        lid = row.get('unified_life_id')
                        if pd.isna(lid): return pd.NA
                        lid_clean = str(lid).replace('.0', '').strip()
                        if lid_clean in ['nan', 'none', '']: return pd.NA
                        
                        life_type = 'Sale' if row.get('source_role') == 'SELLER' else 'Lease'
                        val = get_life_val(lid_clean, life_type, 'status')
                        return str(val) if pd.notna(val) else pd.NA
                    zenu_output[target_field] = base_df.apply(get_status, axis=1)

        # ---------------------------------------------------------
        # VENDOR / BUYER LOGIC (NEW)
        # ---------------------------------------------------------
        elif group_name in ["Vendor", "Buyer"]:
            def get_life_val(active_id, life_type, col_name):
                if pd.isna(active_id): return pd.NA
                if life_type == 'Sale' and not self.sale_life_df.empty and active_id in self.sale_life_df.index:
                    return self.sale_life_df.at[active_id, col_name]
                if life_type == 'Lease' and not self.lease_life_df.empty and active_id in self.lease_life_df.index:
                    return self.lease_life_df.at[active_id, col_name]
                return pd.NA

            def strip_time(d_str):
                if pd.isna(d_str): return pd.NA
                raw = str(d_str).strip()
                bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00']
                if raw.lower() in bad_dates: return pd.NA
                return raw.split(' ')[0].split('T')[0]

            for rule in rules:
                target_field = rule.get("targetField")

                if target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('contactid_clean', pd.Series(dtype=object)).map(self.contact_cleaned_mapping)

                elif target_field == "contact_sale_type":
                    zenu_output[target_field] = base_df.get('source_role', 'SELLER')

                elif target_field == "property_identifier":
                    def format_id(row):
                        lid = row.get('unified_life_id')
                        if pd.isna(lid): return pd.NA
                        lid_clean = str(lid).replace('.0', '').strip()
                        if lid_clean in ['nan', 'none', '']: return pd.NA
                        
                        life_type = 'Lease' if row.get('source_role') == 'LANDLORD' else 'Sale'
                        status = str(get_life_val(lid_clean, life_type, 'status')).strip().lower()
                        
                        if status in ['prospect', 'not currently listed', 'prospect/not currently listed', 'nan', 'none', '']:
                            return f"Pr_{lid_clean}"
                        elif status == 'appraisal':
                            return f"Appr_{lid_clean}"
                        else:
                            return str(lid_clean)
                    zenu_output[target_field] = base_df.apply(format_id, axis=1)

                elif target_field.lower() in ["property status", "property_status"]:
                    def get_status(row):
                        lid = row.get('unified_life_id')
                        if pd.isna(lid): return pd.NA
                        lid_clean = str(lid).replace('.0', '').strip()
                        if lid_clean in ['nan', 'none', '']: return pd.NA
                        
                        life_type = 'Lease' if row.get('source_role') == 'LANDLORD' else 'Sale'
                        val = get_life_val(lid_clean, life_type, 'status')
                        return str(val) if pd.notna(val) else pd.NA
                    zenu_output[target_field] = base_df.apply(get_status, axis=1)

                elif target_field in ["Vendor Solicitor Identifier", "Buyer Solicitor Identifier"]:
                    solicitor_col = 'vendorssolicitor' if target_field == "Vendor Solicitor Identifier" else 'purchaserssolicitor'
                    def get_solicitor(row):
                        if row.get('source_role') in ['SELLER', 'PURCHASER']:
                            lid = row.get('unified_life_id')
                            if pd.isna(lid): return pd.NA
                            lid_clean = str(lid).replace('.0', '').strip()
                            if lid_clean in ['nan', 'none', '']: return pd.NA
                            
                            sol_id = get_life_val(lid_clean, 'Sale', solicitor_col)
                            if pd.isna(sol_id): return pd.NA
                            sol_id_clean = str(sol_id).replace('.0', '').strip()
                            if sol_id_clean in ['nan', 'none', '']: return pd.NA
                            
                            return self.contact_cleaned_mapping.get(sol_id_clean, pd.NA)
                        return pd.NA
                    zenu_output[target_field] = base_df.apply(get_solicitor, axis=1)

                elif target_field == "property_contract_date":
                    def get_contract_date(row):
                        lid = row.get('unified_life_id')
                        if pd.isna(lid): return pd.NA
                        lid_clean = str(lid).replace('.0', '').strip()
                        val = get_life_val(lid_clean, 'Sale', 'conditionalactioned')
                        if pd.isna(val) or str(val).strip().lower() in ['', 'nan', 'none']:
                            val = get_life_val(lid_clean, 'Sale', 'conditional')
                        return strip_time(val)
                    zenu_output[target_field] = base_df.apply(get_contract_date, axis=1)

                elif target_field == "property_sold_price":
                    def get_sold_price(row):
                        lid = row.get('unified_life_id')
                        if pd.isna(lid): return pd.NA
                        lid_clean = str(lid).replace('.0', '').strip()
                        val = get_life_val(lid_clean, 'Sale', 'saleprice')
                        v_str = str(val).strip()
                        if v_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        return re.sub(r'\.0$', '', v_str)
                    zenu_output[target_field] = base_df.apply(get_sold_price, axis=1)

                elif target_field == "zenu_solicitor_id":
                    zenu_output[target_field] = pd.NA

        # ---------------------------------------------------------
        # TASKS LOGIC 
        # ---------------------------------------------------------
        elif group_name in ["Task", "Tasks"]:
            def strip_time(d_str):
                if pd.isna(d_str): return pd.NA
                raw = str(d_str).strip()
                bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00']
                if raw.lower() in bad_dates: return pd.NA
                return raw.split(' ')[0].split('T')[0]

            for rule in rules:
                target_field = rule.get("targetField")

                if target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', pd.NA)

                elif target_field in ["zenu_contact_id", "zenu_property_id", "property_identifier"]:
                    zenu_output[target_field] = pd.NA

                elif target_field == "task_identifier":
                    s1_col = str(rule.get("sources", [{}])[0].get("field", "taskid")).strip().lower()
                    def format_task_id(tid):
                        if pd.isna(tid): return pd.NA
                        t_clean = str(tid).replace('.0', '').strip()
                        if t_clean.lower() in ['nan', 'none', '']: return pd.NA
                        return f"{t_clean}_T"
                    if s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].apply(format_task_id)
                    else:
                        zenu_output[target_field] = pd.NA

                elif target_field == "task_subject":
                    s1_col = str(rule.get("sources", [{}])[0].get("field", "subject")).strip().lower()
                    if s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col]
                    else:
                        zenu_output[target_field] = pd.NA

                elif target_field == "task_notes":
                    s1_col = str(rule.get("sources", [{}])[0].get("field", "description")).strip().lower()
                    if s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col]
                    else:
                        zenu_output[target_field] = pd.NA

                elif target_field == "task_status":
                    s1_col = str(rule.get("sources", [{}])[0].get("field", "completed")).strip().lower()
                    def get_task_status(comp):
                        if pd.isna(comp): return "Completed" 
                        c_str = str(comp).strip().replace('.0', '')
                        if c_str == '0': return "Active"
                        return "Completed"
                    if s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].apply(get_task_status)
                    else:
                        zenu_output[target_field] = pd.NA

                elif target_field == "task_date_due":
                    s1_col = str(rule.get("sources", [{}])[0].get("field", "startdate")).strip().lower()
                    if s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].apply(strip_time)
                    else:
                        zenu_output[target_field] = pd.NA

                elif target_field == "task_team_member_1":
                    s1_col = str(rule.get("sources", [{}])[0].get("field", "userid")).strip().lower()
                    def get_agent(uid):
                        if pd.isna(uid): return pd.NA
                        uid_clean = str(uid).replace('.0', '').strip()
                        return self.staff_map.get(uid_clean, pd.NA) if uid_clean != 'nan' else pd.NA
                    if s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].apply(get_agent)
                    else:
                        zenu_output[target_field] = pd.NA

                else:
                    action = rule.get("action")
                    sources = rule.get("sources", [])
                    if action == "direct" and len(sources) > 0:
                        s1_field = str(sources[0].get("field")).strip().lower()
                        if s1_field in base_df.columns:
                            zenu_output[target_field] = base_df[s1_field]
                        else:
                            zenu_output[target_field] = pd.NA
                    elif action == "static":
                        zenu_output[target_field] = rule.get("valueExpression", "")
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

        # ---------------------------------------------------------
        # ENQUIRIES LOGIC
        # ---------------------------------------------------------
        elif group_name == "Enquiries":
            # 1. Filter out _c2, _c3, etc. to retain only _c, _c1 (or original identifiers)
            if 'CONTACT_IDENTIFIER' in base_df.columns:
                mask_c2_onwards = base_df['CONTACT_IDENTIFIER'].astype(str).str.contains(r'_c(?:[2-9]|[1-9]\d+)$', regex=True, na=False)
                base_df = base_df[~mask_c2_onwards].copy()

            try:
                cnt_df = pd.read_sql_query('SELECT * FROM "ContactNoteTypes.csv"', self.conn)
                cnt_df.columns = cnt_df.columns.str.strip().str.lower()
                if 'typeid' in cnt_df.columns and 'typename' in cnt_df.columns:
                    cnt_df['typeid_clean'] = cnt_df['typeid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    cnt_map = cnt_df.drop_duplicates('typeid_clean').set_index('typeid_clean')['typename'].to_dict()
                else:
                    cnt_map = {}
            except Exception as e:
                self.log(f"[{self.job_id}] Warning loading ContactNoteTypes for Enquiries: {e}")
                cnt_map = {}

            # 2. Blank TypeID Fallback Logic
            def is_enquiry_row(row):
                tid = row.get('typeid', pd.NA)
                is_blank_tid = pd.isna(tid) or str(tid).strip().lower() in ['', 'nan', 'none', 'null']
                
                # Check TypeID match if it's not blank
                if not is_blank_tid:
                    tid_clean = str(tid).replace('.0', '').strip()
                    tname = str(cnt_map.get(tid_clean, '')).lower()
                    if 'enquir' in tname: 
                        return True
                
                # Check Body for specific prefixes if TypeID is completely blank
                if is_blank_tid:
                    body_val = str(row.get('body', '')).strip().lower()
                    allowed_prefixes = (
                        'domain.com.au enquiry',
                        'realestate.com.au enquiry',
                        'office website enquiry'
                    )
                    if body_val.startswith(allowed_prefixes):
                        return True
                        
                return False
                
            base_df = base_df[base_df.apply(is_enquiry_row, axis=1)].copy()

            try:
                fb_df = pd.read_sql_query('SELECT * FROM "FeedbackNotes.csv"', self.conn)
                fb_df.columns = fb_df.columns.str.strip().str.lower()
                if 'noteid' in fb_df.columns:
                    fb_df['noteid_clean'] = fb_df['noteid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    fb_df = fb_df.drop_duplicates(subset=['noteid_clean']).set_index('noteid_clean')
            except Exception:
                fb_df = pd.DataFrame()
                
            try:
                plh_df = pd.read_sql_query('SELECT propertyid, salelifeid, leaselifeid FROM "PropertyLifeHistory.csv"', self.conn)
                plh_df.columns = plh_df.columns.str.strip().str.lower()
                life_to_prop = {}
                for _, row in plh_df.iterrows():
                    pid = str(row['propertyid']).replace('.0', '').strip()
                    if pid and pid != 'nan':
                        sid = str(row.get('salelifeid', '')).replace('.0', '').strip()
                        if sid and sid != 'nan': life_to_prop[sid] = pid
                        lid = str(row.get('leaselifeid', '')).replace('.0', '').strip()
                        if lid and lid != 'nan': life_to_prop[lid] = pid
            except:
                life_to_prop = {}

            def get_life_val(active_id, life_type, col_name):
                if pd.isna(active_id): return pd.NA
                if life_type == 'Sale' and not self.sale_life_df.empty and active_id in self.sale_life_df.index:
                    return self.sale_life_df.at[active_id, col_name]
                if life_type == 'Lease' and not self.lease_life_df.empty and active_id in self.lease_life_df.index:
                    return self.lease_life_df.at[active_id, col_name]
                return pd.NA
                
            def get_prop_val(pid, col_name):
                if pd.isna(pid): return pd.NA
                pid_clean = str(pid).replace('.0', '').strip()
                if not self.props_df.empty and pid_clean in self.props_df.index:
                    return self.props_df.at[pid_clean, col_name]
                return pd.NA

            def get_life_id_from_note(nid):
                if fb_df.empty or pd.isna(nid): return None, None
                nid_clean = str(nid).replace('.0', '').strip()
                if nid_clean not in fb_df.index: return None, None
                row = fb_df.loc[nid_clean]
                sid = str(row.get('salelifeid', '')).replace('.0', '').strip()
                lid = str(row.get('leaselifeid', '')).replace('.0', '').strip()
                if sid and sid.lower() != 'nan': return sid, 'Sale'
                if lid and lid.lower() != 'nan': return lid, 'Lease'
                return None, None

            def strip_time(d_str):
                if pd.isna(d_str): return pd.NA
                raw = str(d_str).strip()
                bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00']
                if raw.lower() in bad_dates: return pd.NA
                return raw.split(' ')[0].split('T')[0]

            for rule in rules:
                target_field = rule.get("targetField")

                if target_field == "enquiry_identifier":
                    def format_id(nid):
                        if pd.isna(nid): return pd.NA
                        n_clean = str(nid).replace('.0', '').strip()
                        return f"{n_clean}_E" if n_clean.lower() not in ['nan', 'none', ''] else pd.NA
                    if 'noteid' in base_df.columns: zenu_output[target_field] = base_df['noteid'].apply(format_id)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', pd.NA)

                elif target_field in ["zenu_contact_id", "zenu_property_id"]:
                    zenu_output[target_field] = pd.NA

                elif target_field == "property_identifier":
                    def get_formatted_prop_id(nid):
                        life_id, life_type = get_life_id_from_note(nid)
                        if not life_id: return pd.NA
                        status = str(get_life_val(life_id, life_type, 'status')).strip().lower()
                        if status in ['prospect', 'not currently listed', 'prospect/not currently listed', 'nan', 'none', '']:
                            return f"Pr_{life_id}"
                        elif status == 'appraisal':
                            return f"Appr_{life_id}"
                        else:
                            return life_id
                    if 'noteid' in base_df.columns: zenu_output[target_field] = base_df['noteid'].apply(get_formatted_prop_id)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "enquiry_status":
                    zenu_output[target_field] = "Completed"

                elif target_field == "enquiry_team_member_1":
                    def get_agent(uid):
                        if pd.isna(uid): return pd.NA
                        uid_clean = str(uid).replace('.0', '').strip()
                        return self.staff_map.get(uid_clean, pd.NA) if uid_clean != 'nan' else pd.NA
                    if 'insertuserid' in base_df.columns: zenu_output[target_field] = base_df['insertuserid'].apply(get_agent)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "enquiry_source":
                    if 'typeid' in base_df.columns: 
                        zenu_output[target_field] = base_df['typeid'].apply(lambda tid: cnt_map.get(str(tid).replace('.0', '').strip(), pd.NA))
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "enquiry_date_created":
                    if 'insertdate' in base_df.columns: zenu_output[target_field] = base_df['insertdate'].apply(strip_time)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "enquiry_notes":
                    if 'body' in base_df.columns: zenu_output[target_field] = base_df['body']
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "Property Name":
                    def build_address(nid):
                        life_id, _ = get_life_id_from_note(nid)
                        if not life_id: return pd.NA
                        pid = life_to_prop.get(life_id, pd.NA)
                        if pd.isna(pid): return pd.NA
                        
                        def _clean_addr_part(col):
                            v = str(get_prop_val(pid, col))
                            if v.lower() in ['nan', 'none', 'null', '0', '0.0', '']: return ''
                            return re.sub(r'\.0$', '', v).strip()

                        unit, st_num, st_name, suburb = _clean_addr_part('unit'), _clean_addr_part('streetnum'), _clean_addr_part('street'), _clean_addr_part('suburb')
                        state, postcode = _clean_addr_part('state'), _clean_addr_part('postcode')
                        
                        parts, st_parts = [], []
                        if unit and st_num: st_parts.append(f"{unit}/{st_num}")
                        elif unit: st_parts.append(unit)
                        elif st_num: st_parts.append(st_num)
                        
                        if st_name: st_parts.append(st_name)
                        street_address = " ".join(st_parts).strip()
                        state_pc = " ".join([p for p in [state, postcode] if p]).strip()
                        
                        if street_address: parts.append(street_address)
                        if suburb: parts.append(suburb)
                        if state_pc: parts.append(state_pc)
                        
                        res = ", ".join(parts).strip()
                        return res if res else pd.NA
                        
                    if 'noteid' in base_df.columns: zenu_output[target_field] = base_df['noteid'].apply(build_address)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "Status":
                    def get_status(nid):
                        life_id, life_type = get_life_id_from_note(nid)
                        if not life_id: return pd.NA
                        s_val = get_life_val(life_id, life_type, 'status')
                        return s_val if pd.notna(s_val) else pd.NA
                        
                    if 'noteid' in base_df.columns: zenu_output[target_field] = base_df['noteid'].apply(get_status)
                    else: zenu_output[target_field] = pd.NA

        # ---------------------------------------------------------
        # INSPECTIONS LOGIC
        # ---------------------------------------------------------
        elif group_name in ["Inspections", "Inspection"]:
            # 1. Filter out _c2 onwards
            if 'CONTACT_IDENTIFIER' in base_df.columns:
                mask_c2_onwards = base_df['CONTACT_IDENTIFIER'].astype(str).str.contains(r'_c(?:[2-9]|[1-9]\d+)$', regex=True, na=False)
                base_df = base_df[~mask_c2_onwards].copy()

            try:
                cnt_df = pd.read_sql_query('SELECT * FROM "ContactNoteTypes.csv"', self.conn)
                cnt_df.columns = cnt_df.columns.str.strip().str.lower()
                if 'typeid' in cnt_df.columns and 'typename' in cnt_df.columns:
                    cnt_df['typeid_clean'] = cnt_df['typeid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    cnt_map = cnt_df.drop_duplicates('typeid_clean').set_index('typeid_clean')['typename'].to_dict()
                else:
                    cnt_map = {}
            except Exception as e:
                self.log(f"[{self.job_id}] Warning loading ContactNoteTypes for Inspections: {e}")
                cnt_map = {}

            def is_inspection_row(row):
                tid = row.get('typeid', pd.NA)
                if pd.isna(tid): return False
                tid_clean = str(tid).replace('.0', '').strip()
                tname = str(cnt_map.get(tid_clean, '')).lower()
                return 'inspection' in tname
                
            base_df = base_df[base_df.apply(is_inspection_row, axis=1)].copy()

            try:
                fb_df = pd.read_sql_query('SELECT * FROM "FeedbackNotes.csv"', self.conn)
                fb_df.columns = fb_df.columns.str.strip().str.lower()
                if 'noteid' in fb_df.columns:
                    fb_df['noteid_clean'] = fb_df['noteid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    fb_df = fb_df.drop_duplicates(subset=['noteid_clean']).set_index('noteid_clean')
            except Exception:
                fb_df = pd.DataFrame()
                
            try:
                plh_df = pd.read_sql_query('SELECT propertyid, salelifeid, leaselifeid FROM "PropertyLifeHistory.csv"', self.conn)
                plh_df.columns = plh_df.columns.str.strip().str.lower()
                life_to_prop = {}
                for _, row in plh_df.iterrows():
                    pid = str(row['propertyid']).replace('.0', '').strip()
                    if pid and pid != 'nan':
                        sid = str(row.get('salelifeid', '')).replace('.0', '').strip()
                        if sid and sid != 'nan': life_to_prop[sid] = pid
                        lid = str(row.get('leaselifeid', '')).replace('.0', '').strip()
                        if lid and lid != 'nan': life_to_prop[lid] = pid
            except:
                life_to_prop = {}

            def get_life_val(active_id, life_type, col_name):
                if pd.isna(active_id): return pd.NA
                if life_type == 'Sale' and not self.sale_life_df.empty and active_id in self.sale_life_df.index:
                    return self.sale_life_df.at[active_id, col_name]
                if life_type == 'Lease' and not self.lease_life_df.empty and active_id in self.lease_life_df.index:
                    return self.lease_life_df.at[active_id, col_name]
                return pd.NA
                
            def get_prop_val(pid, col_name):
                if pd.isna(pid): return pd.NA
                pid_clean = str(pid).replace('.0', '').strip()
                if not self.props_df.empty and pid_clean in self.props_df.index:
                    return self.props_df.at[pid_clean, col_name]
                return pd.NA

            def get_life_id_from_note(nid):
                if fb_df.empty or pd.isna(nid): return None, None
                nid_clean = str(nid).replace('.0', '').strip()
                if nid_clean not in fb_df.index: return None, None
                row = fb_df.loc[nid_clean]
                sid = str(row.get('salelifeid', '')).replace('.0', '').strip()
                lid = str(row.get('leaselifeid', '')).replace('.0', '').strip()
                if sid and sid.lower() != 'nan': return sid, 'Sale'
                if lid and lid.lower() != 'nan': return lid, 'Lease'
                return None, None

            for rule in rules:
                target_field = rule.get("targetField")

                if target_field == "inspection_identifier":
                    def format_id(nid):
                        if pd.isna(nid): return pd.NA
                        n_clean = str(nid).replace('.0', '').strip()
                        return f"{n_clean}_I" if n_clean.lower() not in ['nan', 'none', ''] else pd.NA
                    if 'noteid' in base_df.columns: zenu_output[target_field] = base_df['noteid'].apply(format_id)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', pd.NA)

                elif target_field in ["zenu_contact_id", "zenu_property_id"]:
                    zenu_output[target_field] = pd.NA

                elif target_field == "property_identifier":
                    def get_formatted_prop_id(nid):
                        life_id, life_type = get_life_id_from_note(nid)
                        if not life_id: return pd.NA
                        status = str(get_life_val(life_id, life_type, 'status')).strip().lower()
                        if status in ['prospect', 'not currently listed', 'prospect/not currently listed', 'nan', 'none', '']:
                            return f"Pr_{life_id}"
                        elif status == 'appraisal':
                            return f"Appr_{life_id}"
                        else:
                            return life_id
                    if 'noteid' in base_df.columns: zenu_output[target_field] = base_df['noteid'].apply(get_formatted_prop_id)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "inspection_notes":
                    def clean_body(val):
                        if pd.isna(val): return "N/A"
                        v = str(val).strip()
                        if v.lower() in ['', 'nan', 'none', 'null']: return "N/A"
                        return v
                    if 'body' in base_df.columns: zenu_output[target_field] = base_df['body'].apply(clean_body)
                    else: zenu_output[target_field] = "N/A"

                elif target_field == "inspection_start_date":
                    if 'insertdate' in base_df.columns: zenu_output[target_field] = base_df['insertdate']
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "inspection_end_date":
                    def add_15_mins(val):
                        if pd.isna(val): return pd.NA
                        try:
                            # Parse explicitly using dayfirst=True to respect AUS date
                            dt = pd.to_datetime(val, dayfirst=True, errors='coerce')
                            if pd.isna(dt): return val
                            # Reformats the output back to standard Australian format: DD/MM/YYYY HH:MM:SS
                            return (dt + pd.Timedelta(minutes=15)).strftime('%d/%m/%Y %H:%M:%S')
                        except:
                            return val
                    if 'insertdate' in base_df.columns: zenu_output[target_field] = base_df['insertdate'].apply(add_15_mins)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "inspection_is_private":
                    zenu_output[target_field] = "FALSE"

                elif target_field == "inspection_team_member_1":
                    def get_agent(uid):
                        if pd.isna(uid): return pd.NA
                        uid_clean = str(uid).replace('.0', '').strip()
                        return self.staff_map.get(uid_clean, pd.NA) if uid_clean != 'nan' else pd.NA
                    if 'insertuserid' in base_df.columns: zenu_output[target_field] = base_df['insertuserid'].apply(get_agent)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "inspection_feedback_price":
                    def get_fb_price(nid):
                        if pd.isna(nid): return pd.NA
                        nid_clean = str(nid).replace('.0', '').strip()
                        if not fb_df.empty and nid_clean in fb_df.index:
                            return fb_df.at[nid_clean, 'priceopinion']
                        return pd.NA
                    if 'noteid' in base_df.columns: zenu_output[target_field] = base_df['noteid'].apply(get_fb_price)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "Property Name":
                    def build_address(nid):
                        life_id, _ = get_life_id_from_note(nid)
                        if not life_id: return pd.NA
                        pid = life_to_prop.get(life_id, pd.NA)
                        if pd.isna(pid): return pd.NA
                        
                        def _clean_addr_part(col):
                            v = str(get_prop_val(pid, col))
                            if v.lower() in ['nan', 'none', 'null', '0', '0.0', '']: return ''
                            return re.sub(r'\.0$', '', v).strip()

                        unit, st_num, st_name, suburb = _clean_addr_part('unit'), _clean_addr_part('streetnum'), _clean_addr_part('street'), _clean_addr_part('suburb')
                        state, postcode = _clean_addr_part('state'), _clean_addr_part('postcode')
                        
                        parts, st_parts = [], []
                        if unit and st_num: st_parts.append(f"{unit}/{st_num}")
                        elif unit: st_parts.append(unit)
                        elif st_num: st_parts.append(st_num)
                        
                        if st_name: st_parts.append(st_name)
                        street_address = " ".join(st_parts).strip()
                        state_pc = " ".join([p for p in [state, postcode] if p]).strip()
                        
                        if street_address: parts.append(street_address)
                        if suburb: parts.append(suburb)
                        if state_pc: parts.append(state_pc)
                        
                        res = ", ".join(parts).strip()
                        return res if res else pd.NA
                        
                    if 'noteid' in base_df.columns: zenu_output[target_field] = base_df['noteid'].apply(build_address)
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "Status":
                    def get_status(nid):
                        life_id, life_type = get_life_id_from_note(nid)
                        if not life_id: return pd.NA
                        s_val = get_life_val(life_id, life_type, 'status')
                        return s_val if pd.notna(s_val) else pd.NA
                        
                    if 'noteid' in base_df.columns: zenu_output[target_field] = base_df['noteid'].apply(get_status)
                    else: zenu_output[target_field] = pd.NA

        # ---------------------------------------------------------
        # CONTACT NOTES LOGIC
        # ---------------------------------------------------------
        elif group_name in ["Contact Notes", "ContactNote"]:
            # 1. Filter out _c2 onwards: Keep only identifiers ending in _c or _c1
            if 'CONTACT_IDENTIFIER' in base_df.columns:
                mask_keep = base_df['CONTACT_IDENTIFIER'].astype(str).str.strip().str.contains(r'_c1$|_c$', regex=True, na=False)
                base_df = base_df[mask_keep].copy()

            try:
                cnt_df = pd.read_sql_query('SELECT * FROM "ContactNoteTypes.csv"', self.conn)
                cnt_df.columns = cnt_df.columns.str.strip().str.lower()
                if 'contactnotetypes' in cnt_df.columns and 'typename' in cnt_df.columns:
                    cnt_map = cnt_df.set_index('contactnotetypes')['typename'].to_dict()
                elif 'typeid' in cnt_df.columns and 'typename' in cnt_df.columns:
                    cnt_df['typeid_clean'] = cnt_df['typeid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    cnt_map = cnt_df.drop_duplicates('typeid_clean').set_index('typeid_clean')['typename'].to_dict()
                else:
                    cnt_map = {}
            except Exception as e:
                self.log(f"[{self.job_id}] Warning loading ContactNoteTypes for Contact Notes: {e}")
                cnt_map = {}

            exclude_prefixes = tuple(p.lower() for p in [
                "Sent bulk", "Delivery to", "Contact Details updated", "System - Bulk Assign Category:",
                "Mass Communicator", "Market Report Generated", "Matched Properties Emailed",
                "Added by Property Wizard", "Sent RH Automated eCommunication", "Bulk Document Mail Merge",
                "Inserted via Contact Wizard", "Wizard", "Unsubscribed from Email Communications",
                "contact dominantly merge", "Change of Address", "Change of Name", "Change of Mobile",
                "Change of phone", "Email address Changed", "Matched Properties Emailed",
                "Legal description Changed", "Mobile number changed", "Telephone number changed",
                "Vendor Feedback report", "Vendor Report Generated", "Unsubscribe from all", "Sent email",
                "Contact added into VaultRE", "Contact unsubscribed from emails", "Call placed", "Contact Added into MRI Vault",
                "Added to quick attendance", "Contact created by", "Added by Property",
                "domain.com.au enquiry", "realestate.com.au enquiry", "office website enquiry"
            ])

            def is_valid_contact_note(row):
                body_str = str(row.get('body', '')).strip()
                tid = str(row.get('typeid', '')).replace('.0', '').strip()
                tname = str(cnt_map.get(tid, '')).strip()
                
                tname_lower = tname.lower()
                body_lower = body_str.lower()

                # Exclude if TypeName contains Enquiries, Enquiry, Enqui
                if 'enqui' in tname_lower:
                    return False
                
                # Exclude if TypeName or Body has word Inspection
                if 'inspection' in tname_lower or 'inspection' in body_lower:
                    return False

                # Exclude if body starts with specific prefixes
                if body_lower.startswith(exclude_prefixes):
                    return False
                    
                return True

            base_df = base_df[base_df.apply(is_valid_contact_note, axis=1)].copy()

            def strip_date_only(val):
                if pd.isna(val): return pd.NA
                val_str = str(val).strip()
                if val_str.lower() in ['', 'nan', 'none', 'null', '0', '0.0']: return pd.NA
                try:
                    dt = pd.to_datetime(val_str, dayfirst=True, errors='coerce')
                    if pd.notna(dt):
                        return dt.strftime('%d/%m/%Y')
                    return val_str.split(' ')[0].split('T')[0]
                except:
                    return val_str.split(' ')[0].split('T')[0]

            for rule in rules:
                target_field = rule.get("targetField")

                if target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', pd.NA)

                elif target_field == "contact_note_created_date":
                    if 'insertdate' in base_df.columns:
                        zenu_output[target_field] = base_df['insertdate'].apply(strip_date_only)
                    else:
                        zenu_output[target_field] = pd.NA

                elif target_field == "contact_note_team_member":
                    def get_agent(uid):
                        if pd.isna(uid): return pd.NA
                        uid_clean = str(uid).replace('.0', '').strip()
                        return self.staff_map.get(uid_clean, pd.NA) if uid_clean != 'nan' else pd.NA
                    
                    if 'insertuserid' in base_df.columns:
                        zenu_output[target_field] = base_df['insertuserid'].apply(get_agent)
                    else:
                        zenu_output[target_field] = pd.NA

                elif target_field == "contact_notes":
                    def concat_notes(row):
                        tid = str(row.get('typeid', '')).replace('.0', '').strip()
                        tname = str(cnt_map.get(tid, '')).strip()
                        body = str(row.get('body', '')).strip()
                        
                        if tname and body:
                            return f"{tname} - {body}"
                        return tname if tname else (body if body else pd.NA)
                        
                    zenu_output[target_field] = base_df.apply(concat_notes, axis=1)

        # ---------------------------------------------------------
        # PROPERTY NOTES LOGIC
        # ---------------------------------------------------------
        elif group_name in ["Property Notes", "PropertyNote"]:
            # Explode base_df using PropertyLifeHistory to match AllProperties multiple rows per PropertyID
            try:
                plh_df = pd.read_sql_query('SELECT * FROM "PropertyLifeHistory.csv"', self.conn)
                plh_df.columns = plh_df.columns.str.strip().str.lower()
                
                if 'salelifeid' in plh_df.columns and 'leaselifeid' in plh_df.columns and 'propertyid' in plh_df.columns:
                    sale_mask = plh_df['salelifeid'].notna() & (plh_df['salelifeid'].astype(str).str.strip() != '') & (plh_df['salelifeid'].astype(str).str.strip().str.lower() != 'nan')
                    lease_mask = plh_df['leaselifeid'].notna() & (plh_df['leaselifeid'].astype(str).str.strip() != '') & (plh_df['leaselifeid'].astype(str).str.strip().str.lower() != 'nan')
                    
                    sale_plh = plh_df[sale_mask].copy()
                    sale_plh['active_life_id'] = sale_plh['salelifeid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    sale_plh['life_type'] = 'Sale'
                    
                    lease_plh = plh_df[lease_mask].copy()
                    lease_plh['active_life_id'] = lease_plh['leaselifeid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    lease_plh['life_type'] = 'Lease'
                    
                    neither_mask = ~sale_mask & ~lease_mask
                    prop_plh = plh_df[neither_mask].copy()
                    prop_plh['active_life_id'] = pd.NA
                    prop_plh['life_type'] = 'Prospect_Only'
                    
                    expanded_plh = pd.concat([sale_plh, lease_plh, prop_plh], ignore_index=True)
                    expanded_plh['propertyid_clean'] = expanded_plh['propertyid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    
                    # Deduplicate to prevent massive explosion if PLH has dupes, we only want unique life states per property
                    expanded_plh = expanded_plh.drop_duplicates(subset=['propertyid_clean', 'active_life_id', 'life_type'])
                    
                    if 'propertyid' in base_df.columns:
                        base_df['propertyid_clean'] = base_df['propertyid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                        # Inner join to duplicate Property Notes for each distinct life instance
                        base_df = pd.merge(base_df, expanded_plh[['propertyid_clean', 'active_life_id', 'life_type']], on='propertyid_clean', how='inner')
            except Exception as e:
                self.log(f"[{self.job_id}] Warning: Error exploding Property Notes against PropertyLifeHistory: {e}")

            def get_life_val(active_id, life_type, col_name):
                if pd.isna(active_id): return pd.NA
                if life_type == 'Sale' and not self.sale_life_df.empty and active_id in self.sale_life_df.index:
                    return self.sale_life_df.at[active_id, col_name]
                if life_type == 'Lease' and not self.lease_life_df.empty and active_id in self.lease_life_df.index:
                    return self.lease_life_df.at[active_id, col_name]
                return pd.NA

            def strip_date_only(val):
                if pd.isna(val): return pd.NA
                val_str = str(val).strip()
                if val_str.lower() in ['', 'nan', 'none', 'null', '0', '0.0']: return pd.NA
                try:
                    dt = pd.to_datetime(val_str, dayfirst=True, errors='coerce')
                    if pd.notna(dt):
                        return dt.strftime('%d/%m/%Y')
                    return val_str.split(' ')[0].split('T')[0]
                except:
                    return val_str.split(' ')[0].split('T')[0]

            for rule in rules:
                target_field = rule.get("targetField")

                if target_field == "NoteID":
                    if 'noteid' in base_df.columns:
                        zenu_output[target_field] = base_df['noteid']
                    else:
                        zenu_output[target_field] = pd.NA
                        
                elif target_field == "property_identifier":
                    def get_formatted_prop_id_from_exploded(row):
                        pid = str(row.get('propertyid', '')).replace('.0', '').strip()
                        life_id = row.get('active_life_id')
                        life_type = row.get('life_type')
                        
                        if pd.isna(life_id) or str(life_id).strip().lower() == 'nan':
                            return f"Pr_{pid}" if pid and pid.lower() != 'nan' else pd.NA
                            
                        status = str(get_life_val(life_id, life_type, 'status')).strip().lower()
                        if status in ['prospect', 'not currently listed', 'prospect/not currently listed', 'nan', 'none', '']:
                            return f"Pr_{life_id}"
                        elif status == 'appraisal':
                            return f"Appr_{life_id}"
                        else:
                            return str(life_id)
                            
                    if 'propertyid' in base_df.columns and 'active_life_id' in base_df.columns: 
                        zenu_output[target_field] = base_df.apply(get_formatted_prop_id_from_exploded, axis=1)
                    else: 
                        # Fallback if merge failed
                        def fb_format(pid):
                            pid_c = str(pid).replace('.0', '').strip()
                            return f"Pr_{pid_c}" if pid_c and pid_c.lower() != 'nan' else pd.NA
                        zenu_output[target_field] = base_df.get('propertyid', pd.Series([pd.NA]*len(base_df))).apply(fb_format)

                elif target_field == "property_note_created_date":
                    if 'insertdate' in base_df.columns:
                        zenu_output[target_field] = base_df['insertdate'].apply(strip_date_only)
                    else:
                        zenu_output[target_field] = pd.NA

                elif target_field == "property_note_team_member":
                    def get_agent(uid):
                        if pd.isna(uid): return pd.NA
                        uid_clean = str(uid).replace('.0', '').strip()
                        return self.staff_map.get(uid_clean, pd.NA) if uid_clean != 'nan' else pd.NA
                    
                    if 'insertuserid' in base_df.columns:
                        zenu_output[target_field] = base_df['insertuserid'].apply(get_agent)
                    else:
                        zenu_output[target_field] = pd.NA

                elif target_field == "property_notes":
                    if 'body' in base_df.columns: zenu_output[target_field] = base_df['body']
                    else: zenu_output[target_field] = pd.NA

                elif target_field == "Status":
                    def get_status_from_exploded(row):
                        life_id = row.get('active_life_id')
                        life_type = row.get('life_type')
                        if pd.isna(life_id) or str(life_id).strip().lower() == 'nan': return pd.NA
                        
                        s_val = get_life_val(life_id, life_type, 'status')
                        return s_val if pd.notna(s_val) else pd.NA
                        
                    if 'active_life_id' in base_df.columns: 
                        zenu_output[target_field] = base_df.apply(get_status_from_exploded, axis=1)
                    else: 
                        zenu_output[target_field] = pd.NA

        else:
            # ---------------------------------------------------------
            # GENERIC ACTIONS LOOP (Notes)
            # ---------------------------------------------------------
            for rule in rules:
                target_field = rule.get("targetField")
                action = rule.get("action")
                sources = rule.get("sources", [])

                if target_field == "contact_identifier" and 'CONTACT_IDENTIFIER' in base_df.columns:
                    zenu_output[target_field] = base_df['CONTACT_IDENTIFIER']
                    continue

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
        # GLOBAL CLEANUP & EXPORT
        # ---------------------------------------------------------
        def global_cleaner(val):
            if pd.isna(val): return val
            text = str(val)
            text = text.replace('"', "'")
            text = text.encode('ascii', 'ignore').decode('ascii')
            text = re.sub(r"[^a-zA-Z0-9 ,\-.:;{}\[\]_&'\\/<>&%+=@#!\$^\*()?\r\n]+", '', text)
            text = re.sub(r' +', ' ', text)
            return text.strip()

        if not zenu_output.empty:
            for col in zenu_output.columns:
                zenu_output[col] = zenu_output[col].apply(global_cleaner)

            zenu_output = zenu_output.replace('', pd.NA).replace('nan', pd.NA).replace('None', pd.NA)

            if 'contact_identifier' in zenu_output.columns and group_name in ["Contact Requirement", "Contact Property Relationship", "Vendor", "Buyer"]:
                zenu_output = zenu_output.dropna(subset=['contact_identifier'])
                
            if 'property_identifier' in zenu_output.columns and group_name in ["AllProperties", "Prospect", "Contact Property Relationship", "Vendor", "Buyer"]:
                zenu_output = zenu_output.dropna(subset=['property_identifier'])

            if 'task_identifier' in zenu_output.columns and group_name in ["Task", "Tasks"]:
                zenu_output = zenu_output.dropna(subset=['task_identifier'])

            if group_name == "Contact Property Relationship" and 'Property Status' in zenu_output.columns:
                valid_statuses = ['prospect', 'not currently listed', 'prospect/not currently listed', 'appraisal']
                mask = zenu_output['Property Status'].astype(str).str.strip().str.lower().isin(valid_statuses)
                zenu_output = zenu_output[mask]

        # --- DUPLICATION CHECK HELPERS ---
        def _extract_date(val):
            if pd.isna(val): return ''
            s = str(val).strip()
            if s.lower() in ('', 'nan', 'none', 'null', '0', 'nat'): return ''
            return s

        def _extract_id_num(val):
            if pd.isna(val): return -1
            digits = re.sub(r'[^0-9]', '', str(val))
            return int(digits) if digits else -1

        def add_duplication_check_prospect(df):
            df = df.copy().reset_index(drop=True)
            def _col(name):
                c = next((x for x in df.columns if x.lower() == name), None)
                return df[c].astype(str).str.strip().str.lower().fillna('') if c else pd.Series([''] * len(df))

            group_key = _col('property_full_address') + '|' + _col('property_sale_method')
            id_col  = next((x for x in df.columns if x.lower() == 'property_identifier'), None)
            mod_col = next((x for x in df.columns if x.lower() == 'property_modified_date'), None)

            df['_mod_sort'] = df[mod_col].apply(_extract_date) if mod_col else ''
            df['_id_sort']  = df[id_col].apply(_extract_id_num) if id_col else -1
            df['_group']    = group_key
            df['_orig_idx'] = df.index

            winner_idx = (
                df.sort_values(['_group', '_mod_sort', '_id_sort'], ascending=[True, False, False])
                  .groupby('_group', sort=False)['_orig_idx']
                  .first()
            )
            winner_set = set(winner_idx.values)
            df['Duplication_Check'] = df['_orig_idx'].apply(lambda i: 'Y' if i in winner_set else 'N')
            df = df.drop(columns=['_mod_sort', '_id_sort', '_group', '_orig_idx'])
            cols = ['Duplication_Check'] + [c for c in df.columns if c != 'Duplication_Check']
            return df[cols]

        def add_duplication_check_appraisal(df):
            df = df.copy().reset_index(drop=True)
            def _col(name):
                c = next((x for x in df.columns if x.lower() == name), None)
                return df[c].astype(str).str.strip().str.lower().fillna('') if c else pd.Series([''] * len(df))

            group_key = _col('property_full_address') + '|' + _col('property_sale_method')
            id_col        = next((x for x in df.columns if x.lower() == 'property_identifier'), None)
            mod_col       = next((x for x in df.columns if x.lower() == 'property_modified_date'), None)
            appraisal_col = next((x for x in df.columns if x.lower() == 'property_appraisal_date'), None)

            df['_appr_sort'] = df[appraisal_col].apply(_extract_date) if appraisal_col else ''
            df['_mod_sort']  = df[mod_col].apply(_extract_date)       if mod_col       else ''
            df['_id_sort']   = df[id_col].apply(_extract_id_num)      if id_col        else -1
            df['_group']     = group_key
            df['_orig_idx']  = df.index

            winner_idx = (
                df.sort_values(['_group', '_appr_sort', '_mod_sort', '_id_sort'], ascending=[True, False, False, False])
                  .groupby('_group', sort=False)['_orig_idx']
                  .first()
            )
            winner_set = set(winner_idx.values)
            df['Duplication_Check'] = df['_orig_idx'].apply(lambda i: 'Y' if i in winner_set else 'N')

            if appraisal_col:
                no_appraisal_mask = df[appraisal_col].apply(_extract_date) == ''
                df.loc[no_appraisal_mask, 'Duplication_Check'] = 'N'

            df = df.drop(columns=['_appr_sort', '_mod_sort', '_id_sort', '_group', '_orig_idx'])
            cols = ['Duplication_Check'] + [c for c in df.columns if c != 'Duplication_Check']
            return df[cols]

        # --- EXPORT LOGIC ---
        if group_name == "AllProperties":
            self._export_data("AllProperties", zenu_output)

            if 'property_timeline_status' in zenu_output.columns:
                ts = zenu_output['property_timeline_status']

                prospect_mask = ts.isna() | ts.astype(str).str.lower().isin(
                    ['prospect', 'not currently listed', 'prospect/not currently listed', 'nan', 'none', '']
                )
                prospect_df = zenu_output[prospect_mask].copy()
                if not prospect_df.empty:
                    prospect_df = add_duplication_check_prospect(prospect_df)
                    self._export_data("Prospect", prospect_df)
                
                appraisal_fields = [
                    "property_identifier", "property_type", "property_unit_number", "property_street_number",
                    "property_street_name", "property_suburb", "property_postcode", "property_state",
                    "property_full_address", "property_bedrooms", "property_bathrooms", "property_category",
                    "property_land_size_m2", "listing_land_size_system", "property_year_built",
                    "property_toilets", "property_garages",
                    "property_carports", "property_open_parking_spaces", "property_modified_date",
                    "property_building_name", "property_sale_method", "property_appraisal_date",
                    "property_timeline_status", "property_team_member_1", "property_team_member_2",
                    "property_search_price", "property_vendor_price", "property_unconditional_date",
                    "property_settlement_date"
                ]
                appraisal_mask = ts.astype(str).str.lower() == 'appraisal'
                appraisal_df = zenu_output[appraisal_mask].copy()
                appraisal_cols = [c for c in appraisal_df.columns if c.lower() in appraisal_fields]
                appraisal_df = appraisal_df[appraisal_cols]
                if not appraisal_df.empty:
                    appraisal_df = add_duplication_check_appraisal(appraisal_df)
                    self._export_data("Appraisal", appraisal_df)

        elif group_name == "Prospect":
            if 'property_timeline_status' in zenu_output.columns:
                ts = zenu_output['property_timeline_status']
                prospect_mask = ts.isna() | ts.astype(str).str.lower().isin(
                    ['prospect', 'not currently listed', 'prospect/not currently listed', 'nan', 'none', '']
                )
                prospect_only = zenu_output[prospect_mask].copy()
                prospect_only = add_duplication_check_prospect(prospect_only)
                self._export_data("Prospect", prospect_only)
            else:
                self._export_data("Prospect", zenu_output)
        else:
            self._export_data(group_name, zenu_output)

    def _export_data(self, group_name, df):
        """Handles splitting massive files into safe chunks for Excel (XLSX)."""
        safe_group_name = group_name.replace(" ", "_").replace("/", "_")
        chunk_limit = self.engine.chunk_size
        total_rows = len(df)

        if chunk_limit > 0 and total_rows > chunk_limit:
            self.log(f"[{self.job_id}] Result size ({total_rows}) exceeds limit. Splitting into chunks of {chunk_limit}...")
            num_chunks = (total_rows // chunk_limit) + (1 if total_rows % chunk_limit != 0 else 0)

            for i in range(num_chunks):
                start_idx = i * chunk_limit
                end_idx = start_idx + chunk_limit
                chunk_df = df.iloc[start_idx:end_idx]
                if not chunk_df.empty:
                    output_path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final_pt{i+1}.xlsx")
                    chunk_df.to_excel(output_path, index=False)
                    self.log(f"[{self.job_id}] SUCCESS: Created {output_path} ({len(chunk_df)} rows)")
        else:
            output_path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final.xlsx")
            df.to_excel(output_path, index=False)
            self.log(f"[{self.job_id}] SUCCESS: Created {output_path}")