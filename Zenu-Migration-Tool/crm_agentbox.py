import pandas as pd
import os
import base64
import re
from collections import Counter

class AgentboxProcessor:
    def __init__(self, engine):
        # We inherit tools from the main etl_engine
        self.engine = engine
        self.conn = engine.conn
        self.job_id = engine.job_id
        self.workspace = engine.workspace
        self.log = engine.log


    # ------------------------------------------------------------------
    # STATIC HELPER METHODS (hoisted out of the rules loop)
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_id(x):
        """Strip trailing .0 and whitespace from an ID string."""
        if pd.isna(x):
            return None
        s = str(x).strip()
        if s.lower() in ('nan', 'none', 'null', ''):
            return None
        return re.sub(r'\.0$', '', s)

    @staticmethod
    def _decode_b64(text):
        """Decode a [base64]-prefixed string, or return as-is."""
        try:
            if pd.isna(text) or str(text).lower() in ('nan', 'none', 'null', ''):
                return pd.NA
            s = str(text).strip()
            if s.startswith('[base64]'):
                s = s.replace('[base64]', '')
                s += '=' * ((4 - len(s) % 4) % 4)
                return base64.b64decode(s).decode('utf-8', errors='ignore')
            return s
        except Exception:
            return str(text)

    @staticmethod
    def _parse_date(d):
        """Parse a date string to dd/mm/YYYY, returning pd.NA for bad/missing values."""
        BAD_DATES = {
            '0', '0.0', '', 'none', 'nan', 'null', 'nat',
            '0000-00-00', '0000-00-00 00:00:00',
            '1970-01-01', '1970-01-01 00:00:00',
            '01/01/1970', '1/01/1970', '01-01-1970', '1-01-1970',
        }
        if pd.isna(d):
            return pd.NA
        d_str = str(d).strip().lower()
        if d_str in BAD_DATES:
            return pd.NA
        d_clean = d_str.split()[0].replace('/', '-')
        for fmt in ('%d-%m-%Y', '%Y-%m-%d', '%d-%m-%y', '%Y-%m-%y'):
            try:
                dt = pd.to_datetime(d_clean, format=fmt)
                if dt.year == 1970:
                    return pd.NA
                return dt.strftime('%d/%m/%Y')
            except ValueError:
                continue
        try:
            dt = pd.to_datetime(d_str, dayfirst=True, errors='coerce')
            if pd.isna(dt) or dt.year == 1970:
                return pd.NA
            return dt.strftime('%d/%m/%Y')
        except Exception:
            return pd.NA

    @staticmethod
    def _clean_val(v):
        """Return a clean string, empty string for null/None/nan."""
        if pd.isna(v) or v is None:
            return ''
        s = str(v).strip()
        if s.lower() in ('nan', 'none', 'null', ''):
            return ''
        if s.endswith('.0'):
            s = s[:-2]
        return s

    @staticmethod
    def _format_address(row):
        """Format a property address dict/Series into a single string."""
        def cv(v):
            if pd.isna(v) or v is None: return ''
            s = str(v).strip()
            if s.lower() in ('nan', 'none', 'null', ''): return ''
            if s.endswith('.0'): s = s[:-2]
            return s
        unit = cv(row.get('unit_num'))
        lot  = cv(row.get('lot_num'))
        lvl  = cv(row.get('level_num'))
        unit_parts = ' '.join(p for p in [lot, lvl, unit] if p)
        st_num  = cv(row.get('street_num'))
        st_name = cv(row.get('street_name'))
        st_type = cv(row.get('street_type'))
        suburb   = cv(row.get('suburb'))
        state    = cv(row.get('state'))
        postcode = cv(row.get('postcode'))
        st_parts = []
        if unit_parts and st_num:
            st_parts.append(f'{unit_parts}/{st_num}')
        elif unit_parts:
            st_parts.append(unit_parts)
        elif st_num:
            st_parts.append(st_num)
        if st_name: st_parts.append(st_name)
        if st_type: st_parts.append(st_type)
        street_address = ' '.join(st_parts).strip()
        state_pc = ' '.join(p for p in [state, postcode] if p).strip()
        final_parts = [p for p in [street_address, suburb, state_pc] if p]
        return ', '.join(final_parts)


    @staticmethod
    def _build_unit(row):
        parts = []
        for c in ('lot_num', 'level_num', 'unit_num'):
            v = row.get(c)
            if pd.isna(v) or v is None:
                continue
            s = str(v).strip()
            if s.lower() in ('nan', 'none', 'null', ''):
                continue
            if s.endswith('.0'):
                s = s[:-2]
            if s:
                parts.append(s)
        res = ' '.join(parts)
        return res if res else pd.NA


    @staticmethod
    def _build_street_name(row):
        parts = [str(row.get(c, '')).replace('nan', '').replace('None', '')
                 for c in ('street_name', 'street_type')]
        res = ' '.join(p.strip() for p in parts if p.strip() and p.lower() != 'none')
        return res if res else pd.NA

    def process_group(self, group_name, rules):
        zenu_output = pd.DataFrame()
        
        # 1. Determine the Default Base Table
        source_files = [rule.get("sources", [{}])[0].get("file") for rule in rules if rule.get("sources")]
        if not source_files:
            self.log(f"[{self.job_id}] Skipping {group_name}: No base file found.")
            return
            
        base_file = Counter([f for f in source_files if f]).most_common(1)[0][0]

        # FORCE SPINE FILES
        if group_name == "Contact_Notes":
            base_file = "note_x_contact.csv"
        elif group_name == "Task":
            base_file = "task.csv"
        elif group_name.lower() == "property_notes(notelisting)":
            base_file = "note_x_listing.csv"
        elif group_name.lower() == "property_notes(noteproperty)":
            base_file = "note_x_property.csv"

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
                self.log(f"[{self.job_id}] Error building split contact base table: {e}")
                return
                
        elif group_name in ["Prospect Owners", "Appraisal Owners"]:
            try:
                base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
                base_df.columns = base_df.columns.str.strip().str.lower()
                
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
                    base_df = pd.merge(clean_df, base_df, left_on=match_col, right_on='contact_id', how='inner')
            except Exception as e:
                 self.log(f"[{self.job_id}] Error building split {group_name} base table: {e}")
                 return
                 
        elif group_name in ["Listing Vendor", "Listing Buyer"]:
            try:
                base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
                base_df.columns = base_df.columns.str.strip().str.lower()
                
                if 'role' in base_df.columns:
                    if group_name == "Listing Vendor":
                        allowed_roles = ['owner', 'owner_occupier', 'landlord', 'owner_absentee', 'vendor', 'prospective_vendor']
                    elif group_name == "Listing Buyer":
                        allowed_roles = ['buyer', 'purchaser']
                    base_df = base_df[base_df['role'].astype(str).str.strip().str.lower().isin(allowed_roles)]
            except Exception as e:
                 self.log(f"[{self.job_id}] Error building {group_name} base table: {e}")
                 return

        elif group_name.lower() == "property_notes(noteproperty)":
            try:
                base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
                base_df.columns = base_df.columns.str.strip().str.lower()
                
                list_dfs = []
                for tbl in ["listing_sale.csv", "listing_lease.csv", "listing_appraisal.csv"]:
                    try:
                        temp = pd.read_sql_query(f'SELECT listing_id, property_id FROM "{tbl}"', self.conn)
                        temp.columns = temp.columns.str.strip().str.lower()
                        list_dfs.append(temp)
                    except Exception:
                        pass
                    
                if list_dfs:
                    all_listings = pd.concat(list_dfs).drop_duplicates()
                    base_df = pd.merge(base_df, all_listings, on='property_id', how='left')
            except Exception as e:
                self.log(f"[{self.job_id}] Error building {group_name} base table: {e}")
                return

        else:
            try:
                base_df = pd.read_sql_query(f'SELECT * FROM "{base_file}"', self.conn)
                base_df.columns = base_df.columns.str.strip().str.lower()
            except Exception as e:
                 self.log(f"[{self.job_id}] Error loading base table {base_file}: {e}")
                 return

        # ---------------------------------------------------------
        # 3. GLOBAL LOOKUP DICTIONARIES
        # ---------------------------------------------------------
        contact_cleaned_mapping = {}
        contact_cleaned_name_mapping = {}
        note_date_map, note_agent_map, note_cat_map, note_head_map, note_desc_map = {}, {}, {}, {}, {}
        agent_mapping, note_to_listing, listing_to_prop, prop_mapping = {}, {}, {}, {}
        appraisal_agents_map = {}
        prospect_agents_map = {}
        appraisal_listing_ids_set = set()
        sale_lease_listing_ids_set = set()
        vendor_solicitor_map = {}
        buyer_solicitor_map = {}
        listing_contract_date_map = {}
        listing_sold_price_map = {}
        listing_status_map = {}
        
        note_to_contact_map = {} 
        raw_contact_name_map = {}
        
        task_to_contact_map = {}
        task_to_listing_map = {}
        task_to_agent_map = {}

        if group_name in ["Contact_Notes", "Appraisal", "Prospect", "Prospect Owners", "Appraisal Owners", "Listing Vendor", "Listing Buyer", "Enquiry", "Inspection", "Task", "Contact Relationship"] or group_name.lower() in ["property_notes(notelisting)", "property_notes(noteproperty)"]:
            try:
                self.log(f"[{self.job_id}] Generating Dictionaries...")
                
                try:
                    raw_c_df = pd.read_sql_query('SELECT contact_id, first_name, last_name FROM "contact.csv"', self.conn)
                    raw_c_df.columns = raw_c_df.columns.str.strip().str.lower()
                    if 'contact_id' in raw_c_df.columns:
                        raw_c_df['cid_clean'] = raw_c_df['contact_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                        raw_c_df['full_name'] = raw_c_df.get('first_name', pd.Series(dtype=str)).fillna('') + ' ' + raw_c_df.get('last_name', pd.Series(dtype=str)).fillna('')
                        raw_contact_name_map = raw_c_df.set_index('cid_clean')['full_name'].str.strip().to_dict()
                except Exception:
                    pass

                clean_df = pd.read_sql_query('SELECT * FROM "contact_cleaned.csv"', self.conn)
                match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'Raw ORIG CONTACT_IDENTIFIER'
                
                clean_df['CONTACT_IDENTIFIER'] = clean_df['CONTACT_IDENTIFIER'].astype(str).str.strip()
                clean_df[match_col] = clean_df[match_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                
                primary_mask = clean_df['CONTACT_IDENTIFIER'].str.contains(r'_c1$|_c$', regex=True)
                clean_df_primary = clean_df[primary_mask].drop_duplicates(subset=[match_col])
                contact_cleaned_mapping = clean_df_primary.set_index(match_col)['CONTACT_IDENTIFIER'].to_dict()

                if 'first_name' in clean_df_primary.columns and 'last_name' in clean_df_primary.columns:
                    clean_df_primary['computed_name'] = clean_df_primary['first_name'].fillna('') + ' ' + clean_df_primary['last_name'].fillna('')
                    contact_cleaned_name_mapping = clean_df_primary.set_index('CONTACT_IDENTIFIER')['computed_name'].str.strip().to_dict()
                elif 'contact name' in clean_df_primary.columns:
                    contact_cleaned_name_mapping = clean_df_primary.set_index('CONTACT_IDENTIFIER')['contact name'].astype(str).str.strip().to_dict()

                agent_df = pd.read_sql_query('SELECT * FROM "agent.csv"', self.conn)
                agent_df.columns = agent_df.columns.str.strip().str.lower()
                if 'agent_id' in agent_df.columns:
                    agent_df['agent_id_clean'] = agent_df['agent_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    agent_df['full_name'] = agent_df.get('first_name', pd.Series(dtype=str)).fillna('') + ' ' + agent_df.get('last_name', pd.Series(dtype=str)).fillna('')
                    agent_mapping = agent_df.set_index('agent_id_clean')['full_name'].str.strip().to_dict()

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
                    note_df['note_id_clean'] = note_df['note_id'].astype(str).str.replace(r'\.0$', '', regex=True)

                    raw_ndates = note_df.get('date', pd.Series(dtype=object)).astype(str).str.strip().str.lower()
                    bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00', '01/01/1970', '1/01/1970', '01-01-1970', '1-01-1970']
                    raw_ndates = raw_ndates.replace(bad_dates, pd.NA)

                    note_df['date_clean'] = pd.to_datetime(
                        raw_ndates, errors='coerce', dayfirst=True
                    ).dt.strftime('%d/%m/%Y').replace(['NaT', 'nan'], pd.NA)
                    
                    note_date_map = note_df.set_index('note_id_clean')['date_clean'].dropna().to_dict()
                    note_agent_map = note_df.set_index('note_id_clean')['agent_id'].astype(str).str.replace(r'\.0$', '', regex=True).to_dict() if 'agent_id' in note_df.columns else {}
                    note_cat_map = note_df.set_index('note_id_clean')['category'].fillna('').to_dict() if 'category' in note_df.columns else {}
                    note_head_map = note_df.set_index('note_id_clean')['headline'].apply(decode_b64).to_dict() if 'headline' in note_df.columns else {}
                    note_desc_map = note_df.set_index('note_id_clean')['description'].apply(decode_b64).to_dict() if 'description' in note_df.columns else {}

                nx_listing = pd.read_sql_query('SELECT * FROM "note_x_listing.csv"', self.conn)
                nx_listing.columns = nx_listing.columns.str.strip().str.lower()
                if 'note_id' in nx_listing.columns and 'listing_id' in nx_listing.columns:
                    nx_listing['note_id_clean'] = nx_listing['note_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    nx_listing['listing_id_clean'] = nx_listing['listing_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    note_to_listing = nx_listing.drop_duplicates(subset=['note_id_clean']).set_index('note_id_clean')['listing_id_clean'].to_dict()

                try:
                    nxc = pd.read_sql_query('SELECT note_id, contact_id FROM "note_x_contact.csv"', self.conn)
                    nxc.columns = nxc.columns.str.strip().str.lower()
                    if 'note_id' in nxc.columns and 'contact_id' in nxc.columns:
                        nxc['note_id_clean'] = nxc['note_id'].astype(str).str.replace(r'\.0$', '', regex=True)
                        nxc['contact_id_clean'] = nxc['contact_id'].astype(str).str.replace(r'\.0$', '', regex=True)
                        note_to_contact_map = nxc.drop_duplicates('note_id_clean').set_index('note_id_clean')['contact_id_clean'].to_dict()
                except Exception:
                    pass

                list_dfs = []
                status_dfs = []
                for table in ["listing_sale.csv", "listing_lease.csv", "listing_appraisal.csv"]:
                    try: 
                        temp_df = pd.read_sql_query(f'SELECT * FROM "{table}"', self.conn)
                        temp_df.columns = temp_df.columns.str.strip().str.lower()
                        if 'listing_id' in temp_df.columns and 'property_id' in temp_df.columns:
                            list_dfs.append(temp_df[['listing_id', 'property_id']])
                        if 'listing_id' in temp_df.columns and 'status' in temp_df.columns:
                            status_dfs.append(temp_df[['listing_id', 'status']])
                    except Exception:
                        pass
                
                all_listed_props = pd.DataFrame(columns=['property_id'])
                if list_dfs:
                    all_listings = pd.concat(list_dfs).drop_duplicates(subset=['listing_id'])
                    listing_to_prop = all_listings.set_index('listing_id')['property_id'].to_dict()
                    all_listed_props = pd.concat(list_dfs).dropna(subset=['property_id'])
                
                listed_prop_ids_set = set(all_listed_props['property_id'].astype(str).str.replace(r'\.0$', '', regex=True).unique())

                if status_dfs:
                    all_statuses = pd.concat(status_dfs).drop_duplicates(subset=['listing_id'])
                    all_statuses['listing_id_clean'] = all_statuses['listing_id'].astype(str).str.replace(r'\.0$', '', regex=True)
                    listing_status_map = all_statuses.set_index('listing_id_clean')['status'].to_dict()

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

            except Exception as e: self.log(f"[{self.job_id}] Warning: Error building Maps: {e}")

        if group_name in ["Appraisal Owners", "Enquiry", "Inspection", "Task"] or group_name.lower() in ["property_notes(notelisting)", "property_notes(noteproperty)"]:
            try:
                appr_check_df = pd.read_sql_query('SELECT * FROM "listing_appraisal.csv"', self.conn)
                appr_check_df.columns = appr_check_df.columns.str.strip().str.lower()
                if 'listing_id' in appr_check_df.columns:
                    appraisal_listing_ids_set = set(appr_check_df['listing_id'].astype(str).str.replace(r'\.0$', '', regex=True).unique())
            except Exception as e: self.log(f"[{self.job_id}] Warning: Error building Appraisal validation set: {e}")

        if group_name in ["Listing Vendor", "Listing Buyer"]:
            try:
                sl_df = pd.read_sql_query('SELECT * FROM "listing_sale.csv"', self.conn)
                sl_df.columns = sl_df.columns.str.strip().str.lower()
                ll_df = pd.read_sql_query('SELECT listing_id FROM "listing_lease.csv"', self.conn)
                
                sl_ids = sl_df['listing_id'].astype(str).str.replace(r'\.0$', '', regex=True) if 'listing_id' in sl_df.columns else pd.Series(dtype=str)
                ll_ids = ll_df['listing_id'].astype(str).str.replace(r'\.0$', '', regex=True) if 'listing_id' in ll_df.columns else pd.Series(dtype=str)
                sale_lease_listing_ids_set = set(pd.concat([sl_ids, ll_ids]).unique())
                
                if 'listing_id' in sl_df.columns:
                    sl_df['listing_id_clean'] = sl_df['listing_id'].astype(str).str.replace(r'\.0$', '', regex=True)
                    if 'contract_date' in sl_df.columns:
                        listing_contract_date_map = sl_df.set_index('listing_id_clean')['contract_date'].to_dict()
                    if 'sold_price' in sl_df.columns:
                        listing_sold_price_map = sl_df.set_index('listing_id_clean')['sold_price'].to_dict()
                
                lxc_df = pd.read_sql_query('SELECT listing_id, contact_id, role FROM "listing_x_contact.csv"', self.conn)
                lxc_df.columns = lxc_df.columns.str.strip().str.lower()
                
                if 'listing_id' in lxc_df.columns and 'contact_id' in lxc_df.columns:
                    lxc_df['listing_id_clean'] = lxc_df['listing_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    lxc_df['contact_id_clean'] = lxc_df['contact_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    
                    if group_name == "Listing Vendor":
                        solicitors = lxc_df[lxc_df['role'].astype(str).str.strip().str.lower() == 'vendor_solicitor']
                        vendor_solicitor_map = solicitors.set_index('listing_id_clean')['contact_id_clean'].to_dict()
                    elif group_name == "Listing Buyer":
                        solicitors = lxc_df[lxc_df['role'].astype(str).str.strip().str.lower() == 'buyer_solicitor']
                        buyer_solicitor_map = solicitors.set_index('listing_id_clean')['contact_id_clean'].to_dict()
            except Exception as e: self.log(f"[{self.job_id}] Warning: Error building {group_name} validation sets: {e}")

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
            except Exception as e: self.log(f"[{self.job_id}] Warning: Error building Appraisal maps: {e}")

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
            except Exception as e: self.log(f"[{self.job_id}] Warning: Error building Prospect maps: {e}")

        if group_name == "Task":
            try:
                self.log(f"[{self.job_id}] Generating Task Junction Maps...")
                txc = pd.read_sql_query('SELECT task_id, contact_id FROM "task_x_contact.csv"', self.conn)
                txc.columns = txc.columns.str.strip().str.lower()
                if 'task_id' in txc.columns and 'contact_id' in txc.columns:
                    txc['task_id_clean'] = txc['task_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    txc['contact_id_clean'] = txc['contact_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    task_to_contact_map = txc.drop_duplicates('task_id_clean').set_index('task_id_clean')['contact_id_clean'].to_dict()
                
                txl = pd.read_sql_query('SELECT task_id, listing_id FROM "task_x_listing.csv"', self.conn)
                txl.columns = txl.columns.str.strip().str.lower()
                if 'task_id' in txl.columns and 'listing_id' in txl.columns:
                    txl['task_id_clean'] = txl['task_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    txl['listing_id_clean'] = txl['listing_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    task_to_listing_map = txl.drop_duplicates('task_id_clean').set_index('task_id_clean')['listing_id_clean'].to_dict()
                
                txa = pd.read_sql_query('SELECT task_id, agent_id FROM "task_x_agent.csv"', self.conn)
                txa.columns = txa.columns.str.strip().str.lower()
                if 'task_id' in txa.columns and 'agent_id' in txa.columns:
                    txa['task_id_clean'] = txa['task_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    txa['agent_id_clean'] = txa['agent_id'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    task_to_agent_map = txa.drop_duplicates('task_id_clean').set_index('task_id_clean')['agent_id_clean'].to_dict()
            except Exception as e:
                self.log(f"[{self.job_id}] Warning: Error building Task junction maps: {e}")

        # ---------------------------------------------------------
        # 4. PROCESS THE RULES LOOP
        # ---------------------------------------------------------
        for rule in rules:
            target_field = rule.get("targetField")
            action = rule.get("action")
            sources = rule.get("sources", [])

            # --- OVERRIDE: PROPERTY NOTES (NOTEPROPERTY) ---
            if group_name.lower() == "property_notes(noteproperty)":
                if target_field == "property_identifier":
                    def format_noteprop_id(row):
                        lid = row.get('listing_id')
                        pid = row.get('property_id')
                        
                        if pd.notna(lid) and str(lid).strip() not in ['', 'nan', 'none']:
                            l_clean = re.sub(r'\.0$', '', str(lid).strip())
                            if l_clean in appraisal_listing_ids_set:
                                return f"Appr_{l_clean}"
                            return l_clean
                        
                        if pd.notna(pid) and str(pid).strip() not in ['', 'nan', 'none']:
                            p_clean = re.sub(r'\.0$', '', str(pid).strip())
                            return f"Pr_{p_clean}"
                            
                        return pd.NA
                        
                    zenu_output[target_field] = base_df.apply(format_noteprop_id, axis=1)
                    continue
                    
                elif target_field in ["zenu_property_id", "zenu_contact_id"]:
                    zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field == "property_note_created_date":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'note_id'
                    col_to_use = s1_col if s1_col in base_df.columns else 'note_id'
                    if col_to_use in base_df.columns:
                        clean_nids = base_df[col_to_use].astype(str).str.replace(r'\.0$', '', regex=True)
                        zenu_output[target_field] = clean_nids.map(note_date_map)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field == "property_note_team_member":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'note_id'
                    col_to_use = s1_col if s1_col in base_df.columns else 'note_id'
                    if col_to_use in base_df.columns:
                        clean_nids = base_df[col_to_use].astype(str).str.replace(r'\.0$', '', regex=True)
                        zenu_output[target_field] = clean_nids.map(note_agent_map).map(agent_mapping)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field == "agentbox_status":
                    def get_status(row):
                        lid = row.get('listing_id')
                        if pd.notna(lid) and str(lid).strip().lower() not in ['', 'nan', 'none']:
                            l_clean = re.sub(r'\.0$', '', str(lid).strip())
                            val = listing_status_map.get(l_clean, pd.NA)
                            if pd.notna(val) and str(val).strip().lower() not in ['', 'nan', 'none']:
                                return str(val)
                        return "Prospect"

                    zenu_output[target_field] = base_df.apply(get_status, axis=1)
                    continue

                elif target_field == "property_notes":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'note_id'
                    col_to_use = s1_col if s1_col in base_df.columns else 'note_id'
                    
                    def build_prop_note(nid):
                        if pd.isna(nid) or str(nid).lower() == 'nan': return pd.NA
                        nid_clean = re.sub(r'\.0$', '', str(nid).strip())
                        
                        cat = str(note_cat_map.get(nid_clean, '')).replace('nan', '').strip()
                        head = str(note_head_map.get(nid_clean, '')).replace('nan', '').strip()
                        desc = str(note_desc_map.get(nid_clean, '')).replace('nan', '').strip()
                        
                        parts = []
                        if cat: parts.append(cat)
                        if head: parts.append(head)
                        if desc: parts.append(desc)
                        
                        return " - ".join(parts).strip(" - ") if parts else pd.NA

                    if col_to_use in base_df.columns:
                        zenu_output[target_field] = base_df[col_to_use].apply(build_prop_note)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue


            # --- OVERRIDE: PROPERTY NOTES (LISTING) ---
            elif group_name.lower() == "property_notes(notelisting)":
                if target_field == "property_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'listing_id'
                    def format_prop_note_id(x):
                        if pd.isna(x): return pd.NA
                        x_str = str(x).strip()
                        if x_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        x_clean = re.sub(r'\.0$', '', x_str)
                        if x_clean in appraisal_listing_ids_set:
                            return f"Appr_{x_clean}"
                        return x_clean
                    
                    col_to_use = s1_col if s1_col in base_df.columns else 'listing_id'
                    if col_to_use in base_df.columns:
                        zenu_output[target_field] = base_df[col_to_use].apply(format_prop_note_id)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field in ["zenu_property_id", "zenu_contact_id"]:
                    zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field == "property_note_created_date":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'note_id'
                    col_to_use = s1_col if s1_col in base_df.columns else 'note_id'
                    if col_to_use in base_df.columns:
                        clean_nids = base_df[col_to_use].astype(str).str.replace(r'\.0$', '', regex=True)
                        zenu_output[target_field] = clean_nids.map(note_date_map)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field == "property_note_team_member":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'note_id'
                    col_to_use = s1_col if s1_col in base_df.columns else 'note_id'
                    if col_to_use in base_df.columns:
                        clean_nids = base_df[col_to_use].astype(str).str.replace(r'\.0$', '', regex=True)
                        zenu_output[target_field] = clean_nids.map(note_agent_map).map(agent_mapping)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field == "agentbox_status":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'listing_id'
                    def get_status(x):
                        if pd.isna(x): return pd.NA
                        x_clean = re.sub(r'\.0$', '', str(x).strip())
                        val = listing_status_map.get(x_clean, pd.NA)
                        return str(val) if pd.notna(val) and str(val).lower() not in ['nan', 'none', 'null', ''] else pd.NA
                    col_to_use = s1_col if s1_col in base_df.columns else 'listing_id'
                    if col_to_use in base_df.columns:
                        zenu_output[target_field] = base_df[col_to_use].apply(get_status)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field == "contact_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'note_id'
                    def get_note_contact(nid):
                        if pd.isna(nid) or str(nid).lower() == 'nan': return pd.NA
                        nid_clean = re.sub(r'\.0$', '', str(nid).strip())
                        raw_cid = note_to_contact_map.get(nid_clean)
                        if pd.isna(raw_cid): return pd.NA
                        return contact_cleaned_mapping.get(str(raw_cid), pd.NA)
                        
                    col_to_use = s1_col if s1_col in base_df.columns else 'note_id'
                    if col_to_use in base_df.columns:
                        zenu_output[target_field] = base_df[col_to_use].apply(get_note_contact)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue

                elif target_field == "property_notes":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'note_id'
                    col_to_use = s1_col if s1_col in base_df.columns else 'note_id'
                    
                    def build_prop_note(nid):
                        if pd.isna(nid) or str(nid).lower() == 'nan': return pd.NA
                        nid_clean = re.sub(r'\.0$', '', str(nid).strip())
                        
                        raw_cid = note_to_contact_map.get(nid_clean)
                        cname = raw_contact_name_map.get(str(raw_cid), '') if pd.notna(raw_cid) else ''
                        
                        cat = str(note_cat_map.get(nid_clean, '')).replace('nan', '').strip()
                        head = str(note_head_map.get(nid_clean, '')).replace('nan', '').strip()
                        desc = str(note_desc_map.get(nid_clean, '')).replace('nan', '').strip()
                        
                        parts = []
                        if cname and str(cname).strip() and str(cname).strip().lower() not in ['nan', 'none']:
                            parts.append(f"Regarding Contact: {str(cname).strip()}")
                            
                        if cat: parts.append(cat)
                        if head: parts.append(head)
                        if desc: parts.append(desc)
                        
                        return " - ".join(parts).strip(" - ") if parts else pd.NA

                    if col_to_use in base_df.columns:
                        zenu_output[target_field] = base_df[col_to_use].apply(build_prop_note)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue

            # --- OVERRIDE: TASK ---
            elif group_name == "Task":
                if target_field.lower() == "task_identifier":
                    def format_task_id(x):
                        if pd.isna(x): return pd.NA
                        x_str = str(x).strip()
                        if x_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        x_clean = re.sub(r'\.0$', '', x_str)
                        return f"{x_clean}_T"
                    zenu_output[target_field] = base_df.get('task_id', pd.Series(dtype=object)).apply(format_task_id)
                    continue
                    
                elif target_field.lower() == "contact_identifier":
                    def get_task_contact(tid):
                        if pd.isna(tid): return pd.NA
                        tid_clean = re.sub(r'\.0$', '', str(tid).strip())
                        cid = task_to_contact_map.get(tid_clean)
                        return contact_cleaned_mapping.get(cid, pd.NA) if pd.notna(cid) else pd.NA
                    zenu_output[target_field] = base_df.get('task_id', pd.Series(dtype=object)).apply(get_task_contact)
                    continue
                    
                elif target_field.lower() == "property_identifier":
                    def get_task_property(tid):
                        if pd.isna(tid): return pd.NA
                        tid_clean = re.sub(r'\.0$', '', str(tid).strip())
                        lid = task_to_listing_map.get(tid_clean)
                        if pd.isna(lid): return pd.NA
                        lid_clean = re.sub(r'\.0$', '', str(lid).strip())
                        if lid_clean in appraisal_listing_ids_set:
                            return f"Appr_{lid_clean}"
                        return lid_clean
                    zenu_output[target_field] = base_df.get('task_id', pd.Series(dtype=object)).apply(get_task_property)
                    continue
                    
                elif target_field in ["zenu_contact_id", "zenu_property_id"]:
                    zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field in ["task_subject", "task_notes"]:
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else ('headline' if target_field == 'task_subject' else 'description')
                    if s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].apply(self._decode_b64)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field == "agentbox_status":
                    def get_task_status(tid):
                        if pd.isna(tid): return pd.NA
                        tid_clean = re.sub(r'\.0$', '', str(tid).strip())
                        lid = task_to_listing_map.get(tid_clean)
                        if pd.isna(lid): return pd.NA
                        lid_clean = re.sub(r'\.0$', '', str(lid).strip())
                        val = listing_status_map.get(lid_clean, pd.NA)
                        return str(val) if pd.notna(val) and str(val).lower() not in ['nan', 'none', 'null', ''] else pd.NA
                    zenu_output[target_field] = base_df.get('task_id', pd.Series(dtype=object)).apply(get_task_status)
                    continue
                    
                elif target_field == "task_status":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'completed'
                    def parse_status(x):
                        if pd.isna(x): return "Active"
                        x_str = str(x).strip().lower()
                        bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00', '01/01/1970', '1/01/1970', '01-01-1970', '1-01-1970']
                        if x_str in bad_dates:
                            return "Active"
                        return "Completed"
                    if s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].apply(parse_status)
                    else:
                        zenu_output[target_field] = "Active"
                    continue
                    
                elif target_field == "task_team_member_1":
                    def get_task_agent(tid):
                        if pd.isna(tid): return pd.NA
                        tid_clean = re.sub(r'\.0$', '', str(tid).strip())
                        aid = task_to_agent_map.get(tid_clean)
                        return agent_mapping.get(aid, pd.NA) if pd.notna(aid) else pd.NA
                    zenu_output[target_field] = base_df.get('task_id', pd.Series(dtype=object)).apply(get_task_agent)
                    continue
                    
                elif target_field == "task_date_due":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'due_date'
                    if s1_col and s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].apply(self._parse_date)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue

            # --- OVERRIDE: INSPECTION ---
            elif group_name == "Inspection":
                if target_field.lower() == "inspection_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'inspection_id'
                    def format_inspection_id(x):
                        if pd.isna(x): return pd.NA
                        x_str = str(x).strip()
                        if x_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        x_clean = re.sub(r'\.0$', '', x_str)
                        return f"{x_clean}_I"
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).apply(format_inspection_id)
                    continue

                elif target_field.lower() == "inspection_team_member_1":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'agent_id'
                    clean_aids = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_aids.map(agent_mapping)
                    continue

                elif target_field == "agentbox_status":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'listing_id'
                    def get_status(x):
                        if pd.isna(x): return pd.NA
                        x_clean = re.sub(r'\.0$', '', str(x).strip())
                        val = listing_status_map.get(x_clean, pd.NA)
                        return str(val) if pd.notna(val) and str(val).lower() not in ['nan', 'none', 'null', ''] else pd.NA
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).apply(get_status)
                    continue

                elif target_field == "property_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'listing_id'
                    def format_insp_prop_id(x):
                        if pd.isna(x): return pd.NA
                        x_str = str(x).strip()
                        if x_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        x_clean = re.sub(r'\.0$', '', x_str)
                        if x_clean in appraisal_listing_ids_set:
                            return f"Appr_{x_clean}"
                        return x_clean
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).apply(format_insp_prop_id)
                    continue

                elif target_field == "contact_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'contact_id'
                    clean_cids = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_cids.map(contact_cleaned_mapping)
                    continue
                    
                elif target_field in ["zenu_property_id", "zenu_contact_id"]:
                    zenu_output[target_field] = pd.NA
                    continue
                    
                elif target_field in ["inspection_start_date", "inspection_end_date"]:
                    date_col = str(sources[0].get("field")).strip().lower() if sources else 'date'
                    time_col = 'start_time' if target_field == "inspection_start_date" else 'end_time'
                    
                    def build_datetime(row):
                        d = str(row.get(date_col, '')).strip().replace('nan', '').replace('None', '')
                        t = str(row.get(time_col, '')).strip().replace('nan', '').replace('None', '')
                        if not d or d in ['0', '0.0', '0000-00-00', '1970-01-01', '01/01/1970']: return pd.NA
                        dt_str = f"{d} {t}".strip()
                        parsed = pd.to_datetime(dt_str, errors='coerce', dayfirst=True)
                        if pd.isna(parsed) or parsed.year == 1970: return pd.NA
                        return parsed.strftime('%d/%m/%Y %I:%M %p').strip()
                        
                    zenu_output[target_field] = base_df.apply(build_datetime, axis=1)
                    continue
                    
                elif target_field == "inspection_is_private":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'inspection_type'
                    if s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].astype(str).str.strip().str.upper().apply(
                            lambda x: "FALSE" if x == "OFI" else "TRUE"
                        )
                    else:
                        zenu_output[target_field] = "TRUE"
                    continue
                    
                elif target_field == "inspection_is_interested":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'interest_level'
                    def map_interest(x):
                        if pd.isna(x): return ""
                        val = str(x).strip().lower()
                        if val == "cold": return "NO"
                        if val == "warm": return "Maybe"
                        if val == "hot": return "Yes"
                        return ""
                    if s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].apply(map_interest)
                    else:
                        zenu_output[target_field] = ""
                    continue
                    
                elif "note" in target_field.lower() or "comment" in target_field.lower():
                    def decode_and_combine(row):
                        parts = []
                        for src in sources:
                            col = str(src.get("field")).strip().lower()
                            val = row.get(col)
                            if pd.isna(val): continue
                            s = str(val).strip()
                            if s.lower() in ['nan', 'none', 'null', '']: continue
                            
                            if s.startswith('[base64]'):
                                s = s.replace('[base64]', '')
                                s += "=" * ((4 - len(s) % 4) % 4)
                                try:
                                    s = base64.b64decode(s).decode('utf-8', errors='ignore')
                                except:
                                    pass
                            if s: parts.append(s)
                        res = " - ".join(parts) if parts else ""
                        if target_field == "inspection_notes" and not res:
                            return "N/A"
                        return res if res else pd.NA
                        
                    if sources:
                        zenu_output[target_field] = base_df.apply(decode_and_combine, axis=1)
                        if target_field == "inspection_notes":
                            zenu_output[target_field] = zenu_output[target_field].fillna("N/A")
                    else:
                        zenu_output[target_field] = "N/A" if target_field == "inspection_notes" else pd.NA
                    continue
                    
                elif "date" in target_field.lower():
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else None
                    if s1_col and s1_col in base_df.columns:
                        raw_dates = base_df[s1_col].astype(str).str.strip().str.lower()
                        bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00', '01/01/1970', '1/01/1970', '01-01-1970', '1-01-1970']
                        raw_dates = raw_dates.replace(bad_dates, pd.NA)
                        parsed_dates = pd.to_datetime(raw_dates, errors='coerce', dayfirst=True)
                        zenu_output[target_field] = parsed_dates.dt.strftime('%d/%m/%Y').replace(['NaT', 'nan'], pd.NA)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue


            # --- OVERRIDE: ENQUIRY ---
            elif group_name == "Enquiry":
                if target_field == "enquiry_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'enquiry_id'
                    def format_enquiry_id(x):
                        if pd.isna(x): return pd.NA
                        x_str = str(x).strip()
                        if x_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        x_clean = re.sub(r'\.0$', '', x_str)
                        return f"{x_clean}_E"
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).apply(format_enquiry_id)
                    continue

                elif target_field == "enquiry_team_member_1":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'agent_id'
                    clean_aids = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_aids.map(agent_mapping)
                    continue

                elif target_field == "agentbox_status":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'listing_id'
                    def get_status(x):
                        if pd.isna(x): return pd.NA
                        x_clean = re.sub(r'\.0$', '', str(x).strip())
                        val = listing_status_map.get(x_clean, pd.NA)
                        return str(val) if pd.notna(val) and str(val).lower() not in ['nan', 'none', 'null', ''] else pd.NA
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).apply(get_status)
                    continue

                elif target_field == "property_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'listing_id'
                    def format_enquiry_prop_id(x):
                        if pd.isna(x): return pd.NA
                        x_str = str(x).strip()
                        if x_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        x_clean = re.sub(r'\.0$', '', x_str)
                        if x_clean in appraisal_listing_ids_set:
                            return f"Appr_{x_clean}"
                        return x_clean
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).apply(format_enquiry_prop_id)
                    continue

                elif target_field == "contact_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'contact_id'
                    clean_cids = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_cids.map(contact_cleaned_mapping)
                    continue
                    
                elif target_field in ["zenu_property_id", "zenu_contact_id"]:
                    zenu_output[target_field] = pd.NA
                    continue
                    
                elif "note" in target_field.lower() or "comment" in target_field.lower():
                    def decode_and_combine(row):
                        parts = []
                        for src in sources:
                            col = str(src.get("field")).strip().lower()
                            val = row.get(col)
                            if pd.isna(val): continue
                            s = str(val).strip()
                            if s.lower() in ['nan', 'none', 'null', '']: continue
                            
                            if s.startswith('[base64]'):
                                s = s.replace('[base64]', '')
                                s += "=" * ((4 - len(s) % 4) % 4)
                                try:
                                    s = base64.b64decode(s).decode('utf-8', errors='ignore')
                                except:
                                    pass
                            if s: parts.append(s)
                        return " - ".join(parts) if parts else pd.NA
                        
                    if sources:
                        zenu_output[target_field] = base_df.apply(decode_and_combine, axis=1)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue
                    
                elif "date" in target_field.lower():
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else None
                    if s1_col and s1_col in base_df.columns:
                        raw_dates = base_df[s1_col].astype(str).str.strip().str.lower()
                        bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00', '01/01/1970', '1/01/1970', '01-01-1970', '1-01-1970']
                        raw_dates = raw_dates.replace(bad_dates, pd.NA)
                        parsed_dates = pd.to_datetime(raw_dates, errors='coerce', dayfirst=True)
                        zenu_output[target_field] = parsed_dates.dt.strftime('%d/%m/%Y').replace(['NaT', 'nan'], pd.NA)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue

            # --- OVERRIDE: APPRAISAL OWNERS ---
            elif group_name == "Appraisal Owners":
                if target_field == "property_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'listing_id'
                    
                    def format_appr_owner_id(x):
                        if pd.isna(x): return pd.NA
                        x_str = str(x).strip()
                        if x_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        x_clean = re.sub(r'\.0$', '', x_str)
                        
                        if x_clean in appraisal_listing_ids_set:
                            return f"Appr_{x_clean}"
                        return pd.NA
                        
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).apply(format_appr_owner_id)
                    continue

                elif target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', pd.NA)
                    continue
                    
                elif target_field == "contact_sale_type":
                    zenu_output[target_field] = rule.get("valueExpression", "Seller")
                    continue

            # --- OVERRIDE: PROSPECT OWNERS ---
            elif group_name == "Prospect Owners":
                if target_field == "property_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'property_id'
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).apply(
                        lambda x: f"Pr_{x}" if pd.notna(x) and x.strip() and x.lower() not in ['nan', 'none', 'null', ''] else pd.NA
                    )
                    continue

                elif target_field == "contact_identifier":
                    zenu_output[target_field] = base_df.get('CONTACT_IDENTIFIER', pd.NA)
                    continue
                    
                elif target_field == "contact_sale_type":
                    zenu_output[target_field] = rule.get("valueExpression", "Seller")
                    continue

            # --- OVERRIDE: LISTING VENDOR ---
            elif group_name == "Listing Vendor":
                if target_field == "property_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'listing_id'
                    def format_listing_vendor_id(x):
                        if pd.isna(x): return pd.NA
                        x_str = str(x).strip()
                        if x_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        x_clean = re.sub(r'\.0$', '', x_str)
                        if x_clean in sale_lease_listing_ids_set:
                            return x_clean
                        return pd.NA
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).apply(format_listing_vendor_id)
                    continue

                elif target_field == "contact_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'contact_id'
                    clean_cids = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_cids.map(contact_cleaned_mapping)
                    continue
                    
                elif target_field == "contact_sale_type":
                    zenu_output[target_field] = rule.get("valueExpression", "Seller")
                    continue
                    
                elif target_field == "Vendor_solicitor":
                    clean_lids = base_df.get('listing_id', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_lids.map(vendor_solicitor_map).map(contact_cleaned_mapping)
                    continue

                elif target_field in ["zenu_property_id", "zenu_contact_id"]:
                    zenu_output[target_field] = pd.NA
                    continue

            # --- OVERRIDE: LISTING BUYER ---
            elif group_name == "Listing Buyer":
                if target_field == "property_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'listing_id'
                    def format_listing_buyer_id(x):
                        if pd.isna(x): return pd.NA
                        x_str = str(x).strip()
                        if x_str.lower() in ['nan', 'none', 'null', '']: return pd.NA
                        x_clean = re.sub(r'\.0$', '', x_str)
                        if x_clean in sale_lease_listing_ids_set:
                            return x_clean
                        return pd.NA
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).apply(format_listing_buyer_id)
                    continue

                elif target_field == "contact_identifier":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else 'contact_id'
                    clean_cids = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_cids.map(contact_cleaned_mapping)
                    continue
                    
                elif target_field == "contact_sale_type":
                    zenu_output[target_field] = rule.get("valueExpression", "Purchaser")
                    continue
                    
                elif target_field == "Buyer_solicitor":
                    clean_lids = base_df.get('listing_id', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_lids.map(buyer_solicitor_map).map(contact_cleaned_mapping)
                    continue
                    
                elif target_field == "property_contract_date":
                    s1_col = 'listing_id'
                    raw_dates = base_df.get(s1_col, pd.Series(dtype=object)).apply(
                        lambda x: listing_contract_date_map.get(re.sub(r'\.0$', '', str(x).strip()), pd.NA) if pd.notna(x) else pd.NA
                    )
                    raw_dates = raw_dates.astype(str).str.strip().str.lower()
                    bad_dates = ['0', '0.0', '', 'none', 'nan', 'null', 'nat', '0000-00-00', '0000-00-00 00:00:00', '1970-01-01', '1970-01-01 00:00:00', '01/01/1970', '1/01/1970', '01-01-1970', '1-01-1970']
                    raw_dates = raw_dates.replace(bad_dates, pd.NA)
                    parsed_dates = pd.to_datetime(raw_dates, errors='coerce', dayfirst=True)
                    zenu_output[target_field] = parsed_dates.dt.strftime('%d/%m/%Y').replace(['NaT', 'nan'], pd.NA)
                    continue

                elif target_field == "property_sold_price":
                    s1_col = 'listing_id'
                    def get_sold_price(x):
                        if pd.isna(x): return pd.NA
                        x_clean = re.sub(r'\.0$', '', str(x).strip())
                        val = listing_sold_price_map.get(x_clean, pd.NA)
                        return str(val).replace('.0', '') if pd.notna(val) else pd.NA
                    zenu_output[target_field] = base_df.get(s1_col, pd.Series(dtype=object)).apply(get_sold_price).replace(['nan', 'None'], pd.NA)
                    continue

                elif target_field in ["zenu_property_id", "zenu_contact_id"]:
                    zenu_output[target_field] = pd.NA
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
                    zenu_output[target_field] = base_df.apply(self._build_unit, axis=1)
                    continue

                elif target_field == "property_street_name":
                    zenu_output[target_field] = base_df.apply(self._build_street_name, axis=1)
                    continue

                elif target_field == "property_full_address":
                    zenu_output[target_field] = base_df.apply(self._format_address, axis=1)
                    continue

                elif target_field == "property_notes":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else None
                    if s1_col and s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].apply(self._decode_b64)
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
                    zenu_output[target_field] = base_df.apply(self._build_unit, axis=1)
                    continue

                elif target_field == "property_street_name":
                    zenu_output[target_field] = base_df.apply(self._build_street_name, axis=1)
                    continue

                elif target_field == "property_notes":
                    s1_col = str(sources[0].get("field")).strip().lower() if sources else None
                    if s1_col and s1_col in base_df.columns:
                        zenu_output[target_field] = base_df[s1_col].apply(self._decode_b64)
                    else:
                        zenu_output[target_field] = pd.NA
                    continue

                elif target_field == "property_full_address":
                    zenu_output[target_field] = base_df.apply(self._format_address, axis=1)
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
                    clean_cids = base_df.get('contact_id', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_cids.map(contact_cleaned_mapping)
                    continue
                elif target_field == "contact_note_created_date":
                    clean_nids = base_df.get('note_id', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_nids.map(note_date_map)
                    continue
                elif target_field == "contact_note_team_member":
                    clean_nids = base_df.get('note_id', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_nids.map(note_agent_map).map(agent_mapping)
                    continue
                elif target_field == "contact_notes":
                    clean_nids = base_df.get('note_id', pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    def build_mega_note(nid):
                        if pd.isna(nid) or str(nid).lower() in ['nan', 'none', '']: return pd.NA
                        
                        cat = str(note_cat_map.get(nid, '')).replace('nan', '').strip()
                        head = str(note_head_map.get(nid, '')).replace('nan', '').strip()
                        desc = str(note_desc_map.get(nid, '')).replace('nan', '').strip()
                        
                        # Safely lookup listing and property
                        lid_clean = str(note_to_listing.get(nid, '')).replace('.0', '').strip()
                        prop_clean = str(listing_to_prop.get(lid_clean, '')).replace('.0', '').strip()
                        address = str(prop_mapping.get(prop_clean, "")).strip()
                        
                        parts = []
                        if address: parts.append(f"Regarding Property: - {address}")
                        if cat: parts.append(cat)
                        if head: parts.append(head)
                        if desc: parts.append(desc)
                        
                        return " - ".join(parts).strip(" - ") if parts else pd.NA

                    zenu_output[target_field] = clean_nids.apply(build_mega_note)
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
                # NOTE: contact_cleaned_mapping is populated via the main dictionary block above
                # (group now included in that block's condition). This fallback is a safety net only.
                if not contact_cleaned_mapping:
                    try:
                        cursor = self.conn.cursor()
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                        tables = [t[0] for t in cursor.fetchall()]
                        clean_file = next((t for t in tables if t.lower() == 'contact_cleaned.csv'), None)
                        if clean_file:
                            clean_df = pd.read_sql_query(f'SELECT * FROM "{clean_file}"', self.conn)
                            match_col = 'Raw_ORIG_CONTACT_IDENTIFIER' if 'Raw_ORIG_CONTACT_IDENTIFIER' in clean_df.columns else 'Raw ORIG CONTACT_IDENTIFIER'
                            clean_df[match_col] = clean_df[match_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                            # Only map primary contacts (same logic as main block)
                            primary_mask = clean_df['CONTACT_IDENTIFIER'].astype(str).str.contains(r'_c1$|_c$', regex=True)
                            clean_df_primary = clean_df[primary_mask].drop_duplicates(subset=[match_col])
                            contact_cleaned_mapping = clean_df_primary.set_index(match_col)['CONTACT_IDENTIFIER'].to_dict()
                        else:
                            contact_cleaned_mapping = {}
                    except Exception as e:
                        self.log(f"[{self.job_id}] Warning: Could not build Contact Relationship mapping: {e}")
                        contact_cleaned_mapping = {}

                if target_field in ["contact_identifier", "contact_partner_identifier"] and len(sources) > 0:
                    s1_col = str(sources[0].get("field")).strip().lower()
                    clean_ids = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    zenu_output[target_field] = clean_ids.map(contact_cleaned_mapping)
                    continue
                elif target_field == "contact_partnership_id" and len(sources) >= 2:
                    s1_col = str(sources[0].get("field")).strip().lower()
                    s2_col = str(sources[1].get("field")).strip().lower()
                    c1_ids = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    c2_ids = base_df.get(s2_col, pd.Series(dtype=object)).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    c1 = c1_ids.map(contact_cleaned_mapping).astype(str)
                    c2 = c2_ids.map(contact_cleaned_mapping).astype(str)
                    valid_mask = (c1 != 'nan') & (c2 != 'nan') & c1.notna() & c2.notna()
                    zenu_output[target_field] = pd.Series(pd.NA, index=base_df.index)
                    zenu_output.loc[valid_mask, target_field] = c1[valid_mask] + "_" + c2[valid_mask] + "_r"
                    continue
                elif target_field == "contact_partnership_type" and len(sources) > 0:
                    s1_col = str(sources[0].get("field")).strip().lower()
                    allowed_types = ["Aunt", "Brother", "Business Partner", "Co-Owner", "Colleague", "Cousin", "Daughter", "Daughter-In-Law", "De Facto", "Deceased", "Ex-Partner", "Executor Of Will", "Father", "Father-In-Law", "Friend", "Granddaughter", "Grandfather", "Grandmother", "Grandson", "Husband", "Mother", "Mother-In-Law", "Nephew", "Niece", "Other", "Other Relative", "Partner", "Power Of Attorney", "Principal", "Sister", "Son", "Son-In-Law", "Uncle", "Wife"]
                    raw_val = base_df.get(s1_col, pd.Series(dtype=object)).astype(str).str.strip().str.title()
                    # Fix: Series.replace() is exact-match only; use str.replace() for proper substitution
                    raw_val = raw_val.str.replace(r'^Spouse$', 'Partner', regex=True)
                    zenu_output[target_field] = raw_val.where(raw_val.isin(allowed_types), 'Other')
                    continue
                else:
                    # Unhandled target_field for Contact Relationship — prevent fallthrough to generic actions
                    zenu_output[target_field] = pd.NA
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
                        zenu_output[target_field] = base_df.get(s1_field, pd.Series(dtype=object)).map(mapping_dict)
                    else:
                        zenu_output[target_field] = pd.NA
                except Exception: zenu_output[target_field] = pd.NA

            elif action == "static": zenu_output[target_field] = rule.get("valueExpression", "")
            elif action == "concat": zenu_output[target_field] = "[Concatenated Result Here]"

        # ---------------------------------------------------------
        # 5. GLOBAL CLEANUP AND EXPORT
        # ---------------------------------------------------------
        
        if group_name == "Contact_Notes" and 'note_id' in base_df.columns:
            zenu_output.insert(0, 'source_note_id', base_df['note_id'])
            
        if group_name in ["Prospect Owners", "Appraisal Owners", "Listing Vendor", "Listing Buyer"] and 'role' in base_df.columns:
            zenu_output['source_role'] = base_df['role'].astype(str).str.title()

        def global_cleaner(val):
            if pd.isna(val): 
                return val
            text = str(val)
            text = text.replace('"', "'")
            text = text.encode('ascii', 'ignore').decode('ascii')
            text = re.sub(r"[^a-zA-Z0-9 ,\-.:;{}\[\]_&'\\/<>&%+=@#!\$^\*()?\r\n]+", '', text)
            text = re.sub(r' +', ' ', text)
            return text.strip()

        for col in zenu_output.columns:
            zenu_output[col] = zenu_output[col].apply(global_cleaner)

        zenu_output = zenu_output.replace('', pd.NA).replace('nan', pd.NA).replace('None', pd.NA)

        if group_name == "Contact_Notes":
            if 'contact_identifier' in zenu_output.columns:
                zenu_output = zenu_output.dropna(subset=['contact_identifier'])
            if 'contact_notes' in zenu_output.columns:
                zenu_output = zenu_output.dropna(subset=['contact_notes'])

        if group_name.lower() in ["property_notes(notelisting)", "property_notes(noteproperty)"]:
            if 'property_identifier' in zenu_output.columns:
                zenu_output = zenu_output.dropna(subset=['property_identifier'])
            note_col = next((c for c in zenu_output.columns if 'note' in c.lower() and 'date' not in c.lower() and 'team' not in c.lower()), None)
            if note_col:
                zenu_output = zenu_output.dropna(subset=[note_col])
                
        if group_name in ["Appraisal", "Prospect", "Appraisal Owners", "Prospect Owners", "Listing Vendor", "Listing Buyer", "Enquiry", "Inspection"]:
            if 'property_identifier' in zenu_output.columns:
                zenu_output = zenu_output.dropna(subset=['property_identifier'])
                
        if group_name in ["Prospect Owners", "Appraisal Owners", "Listing Vendor", "Listing Buyer", "Enquiry", "Inspection"]:
            if 'contact_identifier' in zenu_output.columns:
                zenu_output = zenu_output.dropna(subset=['contact_identifier'])

        safe_group_name = group_name.replace(" ", "_").replace("/", "_")
        
        chunk_limit = self.engine.chunk_size
        total_rows = len(zenu_output)
        
        if chunk_limit > 0 and total_rows > chunk_limit:
            self.log(f"[{self.job_id}] Result size ({total_rows}) exceeds limit. Splitting into chunks of {chunk_limit}...")
            
            # Calculate how many files we need
            num_chunks = (total_rows // chunk_limit) + (1 if total_rows % chunk_limit != 0 else 0)
            
            for i in range(num_chunks):
                start_idx = i * chunk_limit
                end_idx = start_idx + chunk_limit
                chunk_df = zenu_output.iloc[start_idx:end_idx]
                
                if not chunk_df.empty:
                    # Append _pt1, _pt2, etc. to the filename
                    output_path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final_pt{i+1}.xlsx")
                    chunk_df.to_excel(output_path, index=False, engine='openpyxl')
                    self.log(f"[{self.job_id}] SUCCESS: Created {output_path} ({len(chunk_df)} rows)")
        else:
            # If under the limit, just export normally
            output_path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final.xlsx")
            zenu_output.to_excel(output_path, index=False, engine='openpyxl')
            self.log(f"[{self.job_id}] SUCCESS: Created {output_path}")