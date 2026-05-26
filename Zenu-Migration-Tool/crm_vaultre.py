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

        try:
            staff_df = pd.read_sql_query('SELECT userid, firstname, lastname FROM "Staff.csv"', self.conn)
            staff_df.columns = staff_df.columns.str.strip().str.lower()
            if 'userid' in staff_df.columns:
                staff_df['userid'] = staff_df['userid'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                staff_df['full_name'] = staff_df.get('firstname', pd.Series(dtype=str)).fillna('') + ' ' + staff_df.get('lastname', pd.Series(dtype=str)).fillna('')
                self.staff_map = staff_df.set_index('userid')['full_name'].str.strip().to_dict()
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

        else:
            try:
                base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
                base_df.columns = base_df.columns.str.strip().str.lower()
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
            # Simple Time Stripper (Preserves Australian Date Flow)
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
                                
                        if 'date' in target_field.lower():
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

            # Standard cleanup for specific groups
            if 'contact_identifier' in zenu_output.columns and group_name == "Contact Requirement":
                zenu_output = zenu_output.dropna(subset=['contact_identifier'])
                
            if 'property_identifier' in zenu_output.columns and group_name in ["AllProperties", "Prospect"]:
                zenu_output = zenu_output.dropna(subset=['property_identifier'])

        # --- DUPLICATION CHECK HELPERS ---

        def _extract_date(val):
            """Parse a date string to a comparable value; returns '' if missing/invalid."""
            if pd.isna(val): return ''
            s = str(val).strip()
            if s.lower() in ('', 'nan', 'none', 'null', '0', 'nat'): return ''
            return s  # ISO-format dates sort lexicographically correctly (YYYY-MM-DD)

        def _extract_id_num(val):
            """Pull the trailing numeric portion of an ID (e.g. 'Pr_1234' -> 1234). Higher = more recent."""
            if pd.isna(val): return -1
            digits = re.sub(r'[^0-9]', '', str(val))
            return int(digits) if digits else -1

        def add_duplication_check_prospect(df):
            """
            Within each duplicate group (same full_address + modified_date + sale_method):
              - Y = most recent PROPERTY_MODIFIED_DATE
              - Tie/blank -> Y = highest PROPERTY_IDENTIFIER numeric suffix
            All others in the group get N.
            """
            df = df.copy().reset_index(drop=True)

            # Build composite group key
            def _col(name):
                c = next((x for x in df.columns if x.lower() == name), None)
                return df[c].astype(str).str.strip().str.lower().fillna('') if c else pd.Series([''] * len(df))

            group_key = _col('property_full_address') + '|' + _col('property_modified_date') + '|' + _col('property_sale_method')

            # Sort keys for winner selection: higher modified_date first, then higher ID first
            id_col   = next((x for x in df.columns if x.lower() == 'property_identifier'), None)
            mod_col  = next((x for x in df.columns if x.lower() == 'property_modified_date'), None)

            df['_mod_sort'] = df[mod_col].apply(_extract_date) if mod_col else ''
            df['_id_sort']  = df[id_col].apply(_extract_id_num) if id_col else -1
            df['_group']    = group_key
            df['_orig_idx'] = df.index

            # Within each group pick winner: max mod_date, then max id
            winner_idx = (
                df.sort_values(['_group', '_mod_sort', '_id_sort'], ascending=[True, False, False])
                  .groupby('_group', sort=False)['_orig_idx']
                  .first()
            )
            winner_set = set(winner_idx.values)
            df['Duplication_Check'] = df['_orig_idx'].apply(lambda i: 'Y' if i in winner_set else 'N')
            df = df.drop(columns=['_mod_sort', '_id_sort', '_group', '_orig_idx'])
            # Move Duplication_Check to first column
            cols = ['Duplication_Check'] + [c for c in df.columns if c != 'Duplication_Check']
            return df[cols]

        def add_duplication_check_appraisal(df):
            """
            Within each duplicate group (same full_address + modified_date + appraisal_date):
              - Y = most recent PROPERTY_APPRAISAL_DATE
              - Tie/blank -> Y = most recent PROPERTY_MODIFIED_DATE
              - Still tied/blank -> Y = highest PROPERTY_IDENTIFIER numeric suffix
            All others in the group get N.
            """
            df = df.copy().reset_index(drop=True)

            def _col(name):
                c = next((x for x in df.columns if x.lower() == name), None)
                return df[c].astype(str).str.strip().str.lower().fillna('') if c else pd.Series([''] * len(df))

            group_key = _col('property_full_address') + '|' + _col('property_modified_date') + '|' + _col('property_appraisal_date')

            id_col       = next((x for x in df.columns if x.lower() == 'property_identifier'), None)
            mod_col      = next((x for x in df.columns if x.lower() == 'property_modified_date'), None)
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
            df = df.drop(columns=['_appr_sort', '_mod_sort', '_id_sort', '_group', '_orig_idx'])
            cols = ['Duplication_Check'] + [c for c in df.columns if c != 'Duplication_Check']
            return df[cols]

        # --- EXPORT LOGIC ---
        if group_name == "AllProperties":
            # Export full AllProperties file (no duplication check)
            self._export_data("AllProperties", zenu_output)

            if 'property_timeline_status' in zenu_output.columns:
                ts = zenu_output['property_timeline_status']

                # --- Prospect ---
                prospect_mask = ts.isna() | ts.astype(str).str.lower().isin(
                    ['prospect', 'not currently listed', 'prospect/not currently listed', 'nan', 'none', '']
                )
                prospect_df = zenu_output[prospect_mask].copy()
                if not prospect_df.empty:
                    prospect_df = add_duplication_check_prospect(prospect_df)
                    self._export_data("Prospect", prospect_df)
                    self.log(f"[{self.job_id}] Prospect file derived from AllProperties ({len(prospect_df)} rows)")
                else:
                    self.log(f"[{self.job_id}] Warning: No Prospect rows found in AllProperties output.")

                # --- Appraisal ---
                appraisal_fields = [
                    "property_identifier", "property_type", "property_unit_number", "property_street_number",
                    "property_street_name", "property_suburb", "property_postcode", "property_state",
                    "property_full_address", "property_bedrooms", "property_bathrooms", "property_category",
                    "property_land_size_m2", "property_year_built", "property_toilets", "property_garages",
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
                    self.log(f"[{self.job_id}] Appraisal file derived from AllProperties ({len(appraisal_df)} rows)")
                else:
                    self.log(f"[{self.job_id}] Warning: No Appraisal rows found in AllProperties output.")

            else:
                self.log(f"[{self.job_id}] Warning: property_timeline_status not in output; skipping Prospect and Appraisal export.")

        elif group_name == "Prospect":
            # Standalone Prospect call (if configured separately)
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