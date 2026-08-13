import pandas as pd
import os
import re


class RexProcessor:
    """
    Rex CRM -> Zenu unified schema.

    Groups implemented (matching the mapping JSON):
        Contact Relationship, Contact Requirement, Contact Notes, Appraisal,
        Prospect, Contact property Relationship, Tasks, Enquiries, Inspections,
        Buyer, Seller

    Plus one extra Rex-only output: Property_Notes, produced automatically
    whenever note_properties.csv is uploaded.

    Rex spreads everything across link tables (note_contacts, contact_reln_*,
    feedback_contacts, ...), so most groups are "base link table -> join out".

    Dates: Rex exports Australian d/m/Y already, so everything is normalised
    back to dd/mm/yyyy, same as the Agentbox / VaultRE / Eagle engines.
    """

    # ---- Zenu property identifier prefixes -------------------------------
    APPRAISAL_PREFIX = "Appr_"
    PROSPECT_PREFIX = "Pr_"

    # ---- Business lookups from the mapping notes -------------------------
    RELATIONSHIP_TYPE_MAP = {
        'employee': 'Other', 'other': 'Other', 'parent_company': 'Other',
        'employee_director': 'Other', 'employer_director': 'Other',
        'coworker': 'Other', 'sub_company': 'Other', 'employer': 'Other',
        'partner': 'partner', 'parent': 'Mother', 'sibling': 'Brother',
        'child': 'Son', 'spouse': 'partner',
    }
    PIPELINE_RATING_MAP = {'hot': 'A', 'warm': 'B', 'cold': 'C'}
    ENQUIRY_STATUS_MAP = {'approved': 'Completed', 'trashed': 'Cancelled'}
    INTEREST_MAP = {'hot': 'YES', 'warm': 'MAYBE', 'cold': 'NO'}

    def __init__(self, engine):
        self.engine = engine
        self.conn = engine.conn
        self.job_id = engine.job_id
        self.workspace = engine.workspace
        self.log = engine.log

        self.dicts_loaded = False
        self.property_notes_done = False

        # contact_cleaned.csv
        self.contact_primary = {}     # orig id -> first CONTACT_IDENTIFIER (_c/_c1)
        self.contact_splits = {}      # orig id -> [all CONTACT_IDENTIFIERs]
        self.contact_names = {}       # CONTACT_IDENTIFIER -> Contact Name

        # Indexed source tables
        self.props_df = pd.DataFrame()
        self.appraisals_df = pd.DataFrame()
        self.listings_df = pd.DataFrame()
        self.notes_df = pd.DataFrame()
        self.feedback_df = pd.DataFrame()
        self.contracts_df = pd.DataFrame()
        self.profile_map_df = pd.DataFrame()

        # Property identifier resolution
        self.appraisal_by_property = {}   # property_id -> Appr_<appraisal id>
        self.prospect_ids = set()         # property_ids that are prospects
        self.listing_to_property = {}     # listing_id -> property_id
        self.listings_by_property = {}    # property_id -> [listing_id, ...]

    # ==================================================================
    # Generic helpers
    # ==================================================================
    def _tables(self):
        cur = self.conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        return [t[0] for t in cur.fetchall()]

    def _find_table(self, wanted):
        """Case-insensitive table lookup, tolerant of the odd typo in the mapping JSON."""
        target = str(wanted).strip().lower().replace(',csv', '.csv')
        tables = self._tables()
        for t in tables:
            if t.strip().lower() == target:
                return t
        # 'listing.csv' <-> 'listings.csv' style singular/plural slips
        stem = target[:-4] if target.endswith('.csv') else target
        for t in tables:
            ts = t.strip().lower()
            ts = ts[:-4] if ts.endswith('.csv') else ts
            if ts in (stem + 's', stem.rstrip('s')):
                return t
        return None

    def _read_table(self, wanted):
        table = self._find_table(wanted)
        if not table:
            return pd.DataFrame()
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{table}"', self.conn)
            df.columns = df.columns.str.strip().str.lower()
            return df
        except Exception as e:
            self.log(f"[{self.job_id}] Warning: could not read {wanted} - {e}")
            return pd.DataFrame()

    def _read_first(self, *candidates):
        """
        Read whichever of these filenames the client actually exported.
        Rex account exports disagree on names (match_suburb_criteria.csv vs
        match_profile_suburb_criteria.csv), so try each in turn.
        """
        for name in candidates:
            if self._find_table(name):
                df = self._read_table(name)
                if not df.empty:
                    self.log(f"[{self.job_id}]   using {name}")
                    return df
        self.log(f"[{self.job_id}]   none of these were found: {', '.join(candidates)}")
        return pd.DataFrame()

    def _index_by(self, wanted, key='id'):
        """Read a table and index it by a cleaned key column."""
        df = self._read_table(wanted)
        if df.empty or key not in df.columns:
            return pd.DataFrame()
        df['_key'] = self._clean_id(df[key])
        return df.drop_duplicates('_key').set_index('_key')

    @staticmethod
    def _clean_id(series):
        """
        Ids as clean strings. A missing value becomes '' rather than the literal
        text 'nan' - astype(str) on a NaN yields 'nan', which would otherwise be
        written into the output as if it were a real id.
        """
        out = (series.astype(str)
                     .str.replace(r'\.0$', '', regex=True)
                     .str.strip())
        return out.mask(out.str.lower().isin(['nan', 'none', 'null', 'nat', '']), '')

    @staticmethod
    def _blank(series):
        """Empty strings become true blanks so Excel shows an empty cell."""
        return series.replace('', pd.NA)

    @staticmethod
    def _sid(val):
        """Scalar id cleaner."""
        if pd.isna(val):
            return ''
        return re.sub(r'\.0$', '', str(val).strip())

    @staticmethod
    def _clean_text(val):
        """Trim, collapse whitespace, strip control characters."""
        if pd.isna(val):
            return pd.NA
        s = str(val)
        if s.strip().lower() in ['', 'nan', 'none', 'null']:
            return pd.NA
        s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)
        s = re.sub(r'[ \t]+', ' ', s)
        s = re.sub(r'\s*\n\s*', '\n', s)
        s = s.strip()
        # A column with blanks makes pandas read whole numbers as floats, so a
        # unit/street number arrives as "54.0". Never let that reach an address.
        if re.fullmatch(r'-?\d+\.0', s):
            s = s[:-2]
        return s or pd.NA

    @staticmethod
    def _date_only(val):
        """
        Strip the time and keep the date EXACTLY as the source wrote it.

        Rex already exports Australian d/m/Y, so there is nothing to convert -
        "26/08/2025  11:11:10 AM" simply becomes "26/08/2025". No parsing, no
        reformatting and no timezone is involved, which is the only way to
        guarantee a date can never move by a day.
        """
        if pd.isna(val):
            return pd.NA
        s = str(val).strip()
        if s.lower() in ['', 'nan', 'none', 'null', '0', '0.0', 'nat']:
            return pd.NA

        # Cut the time off: everything before the first space or 'T'.
        token = re.split(r'[\sT]', s, maxsplit=1)[0].strip()

        # Already Australian d/m/y - hand it straight back, untouched.
        if re.fullmatch(r'\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}', token):
            return token.replace('-', '/').replace('.', '/')

        # ISO y-m-d - reorder to AU. Only the layout changes, never the day.
        iso = re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', token)
        if iso:
            y, m, d = iso.groups()
            return f"{int(d):02d}/{int(m):02d}/{y}"

        # A bare epoch carries no wall clock, so it has to be rendered in
        # Australian local time - that is the date the office actually sees.
        epoch = re.fullmatch(r'(\d{9,13})(\.0)?', token)
        if epoch:
            digits = epoch.group(1)
            try:
                dt = pd.to_datetime(int(digits), unit='ms' if len(digits) > 10 else 's',
                                    utc=True, errors='coerce')
                if pd.notna(dt):
                    try:
                        dt = dt.tz_convert('Australia/Sydney')
                    except Exception:
                        pass
                    return dt.strftime('%d/%m/%Y')
            except Exception:
                pass

        # Anything unrecognised is passed through as-is rather than guessed at.
        return token or pd.NA

    @classmethod
    def _date_time(cls, date_val, time_val):
        """Combine a date column and a time column into dd/mm/yyyy HH:MM."""
        d = cls._date_only(date_val)
        if pd.isna(d):
            return pd.NA
        if pd.isna(time_val):
            return d
        t = str(time_val).strip()
        if t.lower() in ['', 'nan', 'none', 'null']:
            return d
        # time may arrive as '14:30:00', '2:30 PM', or a full datetime
        m = re.search(r'(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp][Mm])?', t)
        if not m:
            return d
        hour, minute, ampm = int(m.group(1)), m.group(2), m.group(3)
        if ampm:
            up = ampm.upper()
            if up == 'PM' and hour < 12:
                hour += 12
            if up == 'AM' and hour == 12:
                hour = 0
        return f"{d} {hour:02d}:{minute}"

    @staticmethod
    def _num(val):
        """Numeric passthrough that blanks zeros and junk."""
        if pd.isna(val):
            return pd.NA
        s = str(val).strip().replace(',', '')
        if s.lower() in ['', 'nan', 'none', 'null']:
            return pd.NA
        n = pd.to_numeric(s, errors='coerce')
        if pd.isna(n) or n == 0:
            return pd.NA
        return int(n) if float(n).is_integer() else n

    # ==================================================================
    # Caches
    # ==================================================================
    def _build_global_dictionaries(self):
        if self.dicts_loaded:
            return
        self.log(f"[{self.job_id}] Building Rex Global Cache Dictionaries...")

        self._load_contact_cleaned()

        self.props_df = self._index_by('properties.csv', 'id')
        self.appraisals_df = self._index_by('appraisals.csv', 'id')
        self.listings_df = self._index_by('listings.csv', 'id')
        self.notes_df = self._index_by('notes.csv', 'id')
        self.feedback_df = self._index_by('feedback.csv', 'id')
        self.contracts_df = self._index_by('listing_contracts.csv', 'id')

        for label, df in [('properties', self.props_df), ('appraisals', self.appraisals_df),
                          ('listings', self.listings_df), ('notes', self.notes_df),
                          ('feedback', self.feedback_df), ('listing_contracts', self.contracts_df)]:
            if not df.empty:
                self.log(f"[{self.job_id}]   {label}.csv: {len(df)} rows")

        self.profile_map_df = self._read_table('Contact_profile_mapping.csv')
        self._build_property_identifier_maps()

        self.dicts_loaded = True

    def _load_contact_cleaned(self):
        """orig contact id -> split identifiers, and identifier -> contact name."""
        table = self._find_table('contact_cleaned.csv')
        if not table:
            self.log(f"[{self.job_id}] Warning: contact_cleaned.csv not found - "
                     f"contact identifiers will fall back to the raw Rex ids.")
            return
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{table}"', self.conn)
            match_col = ('Raw_ORIG_CONTACT_IDENTIFIER'
                         if 'Raw_ORIG_CONTACT_IDENTIFIER' in df.columns
                         else 'ORIG CONTACT_IDENTIFIER')
            if match_col not in df.columns or 'CONTACT_IDENTIFIER' not in df.columns:
                self.log(f"[{self.job_id}] Warning: contact_cleaned.csv missing "
                         f"'{match_col}' / 'CONTACT_IDENTIFIER'.")
                return

            df[match_col] = self._clean_id(df[match_col])
            df['CONTACT_IDENTIFIER'] = df['CONTACT_IDENTIFIER'].astype(str).str.strip()

            name_col = next((c for c in df.columns if c.strip().lower() == 'contact name'), None)

            for _, row in df.iterrows():
                orig = row[match_col]
                ident = row['CONTACT_IDENTIFIER']
                if not orig or orig.lower() in ('nan', 'none', ''):
                    continue
                self.contact_splits.setdefault(orig, []).append(ident)
                if orig not in self.contact_primary and re.search(r'_c1?$', ident):
                    self.contact_primary[orig] = ident
                if name_col:
                    nm = self._clean_text(row[name_col])
                    if pd.notna(nm):
                        self.contact_names[ident] = nm

            # Any contact whose splits are not suffixed _c/_c1 still needs a primary
            for orig, idents in self.contact_splits.items():
                self.contact_primary.setdefault(orig, idents[0])

            self.log(f"[{self.job_id}]   contact_cleaned.csv: "
                     f"{len(self.contact_primary)} contacts, "
                     f"{sum(len(v) for v in self.contact_splits.values())} splits")
        except Exception as e:
            self.log(f"[{self.job_id}] Warning: Error building contact map: {e}")

    def _build_property_identifier_maps(self):
        """
        Zenu property identifiers:
            Appraisal -> "Appr_<appraisal id>"
            Prospect  -> "Pr_<property id>"  (properties with no appraisal and no listing)
        """
        if not self.appraisals_df.empty and 'property_id' in self.appraisals_df.columns:
            for appr_id, prop_id in zip(self.appraisals_df.index,
                                        self.appraisals_df['property_id']):
                pid = self._sid(prop_id)
                if pid and pid not in self.appraisal_by_property:
                    self.appraisal_by_property[pid] = f"{self.APPRAISAL_PREFIX}{appr_id}"

        listing_props = set()
        if not self.listings_df.empty and 'property_id' in self.listings_df.columns:
            for lid, prop_id in zip(self.listings_df.index, self.listings_df['property_id']):
                pid = self._sid(prop_id)
                if pid:
                    listing_props.add(pid)
                    self.listing_to_property[self._sid(lid)] = pid
                    # one property can carry several listings
                    self.listings_by_property.setdefault(pid, []).append(self._sid(lid))

        if not self.props_df.empty:
            appraised = set(self.appraisal_by_property.keys())
            self.prospect_ids = {pid for pid in self.props_df.index
                                 if pid not in appraised and pid not in listing_props}

        self.log(f"[{self.job_id}]   property identifiers: "
                 f"{len(self.appraisal_by_property)} appraisal, {len(self.prospect_ids)} prospect")

    # ==================================================================
    # Shared resolvers
    # ==================================================================
    def _contact_ident(self, contact_id, all_splits=False):
        """Zenu contact identifier(s) for a raw Rex contact id."""
        cid = self._sid(contact_id)
        if not cid:
            return [] if all_splits else pd.NA
        if all_splits:
            return self.contact_splits.get(cid, [cid] if not self.contact_splits else [])
        if not self.contact_primary:
            return cid  # no cleaned file supplied - fall back to the raw id
        return self.contact_primary.get(cid, pd.NA)

    def _contact_name(self, identifier):
        if pd.isna(identifier):
            return pd.NA
        return self.contact_names.get(str(identifier).strip(), pd.NA)

    def _property_ident(self, property_id=None, listing_id=None):
        """
        Convert to Appr_/Pr_ only when the id ITSELF belongs to an appraisal or
        a prospect, per the mapping note: "if any of this Property_id or
        listing_id matches on the appraisal and prospect ... if not just use
        the listing_id value".

        A listing is deliberately NOT dereferenced to its property. An enquiry
        against an active listing keeps the listing id even if that property
        happens to have been appraised at some earlier point - otherwise a live
        listing would be rewritten as an appraisal.
        """
        pid = self._sid(property_id)
        lid = self._sid(listing_id)

        for candidate in (pid, lid):
            if not candidate:
                continue
            if candidate in self.appraisal_by_property:
                return self.appraisal_by_property[candidate]
            if candidate in self.prospect_ids:
                return f"{self.PROSPECT_PREFIX}{candidate}"

        return lid or pid or pd.NA

    def _property_status(self, identifier, listing_id=None):
        """Prospect / Appraisal from the identifier, else the listing state."""
        ident = '' if pd.isna(identifier) else str(identifier)
        if ident.startswith(self.APPRAISAL_PREFIX):
            return 'Appraisal'
        if ident.startswith(self.PROSPECT_PREFIX):
            return 'Prospect'
        lid = self._sid(listing_id) or ident
        if lid and not self.listings_df.empty and lid in self.listings_df.index:
            return self._clean_text(self.listings_df.at[lid, 'system_listing_state']) \
                if 'system_listing_state' in self.listings_df.columns else pd.NA
        return pd.NA

    def _prop_val(self, property_id, col):
        pid = self._sid(property_id)
        if not pid or self.props_df.empty or col not in self.props_df.columns:
            return pd.NA
        if pid in self.props_df.index:
            return self.props_df.at[pid, col]
        return pd.NA

    def _full_address(self, property_id):
        """'54/5 Tenby Street, Blacktown, NSW 2148'"""
        pid = self._sid(property_id)
        if not pid or self.props_df.empty or pid not in self.props_df.index:
            return pd.NA

        def part(col):
            v = self._clean_text(self._prop_val(pid, col))
            return '' if pd.isna(v) else str(v).strip()

        unit = part('adr_unit_number')
        st_no = part('adr_street_number')
        st_name = part('adr_street_name')
        suburb = part('adr_suburb_or_town')
        state = part('adr_state_or_region')
        postcode = part('adr_postcode').replace('.0', '')

        street = f"{unit}/{st_no}" if unit and st_no else (unit or st_no)
        line1 = ' '.join(x for x in [street, st_name] if x).strip()

        tail = ' '.join(x for x in [state, postcode] if x).strip()
        pieces = [p for p in [line1, suburb, tail] if p]
        return ', '.join(pieces) if pieces else pd.NA

    @classmethod
    def _land_unit(cls, val):
        """Rex 'm2' -> Zenu 'SQM'."""
        v = '' if pd.isna(val) else str(val).strip().lower()
        if v in ['', 'nan', 'none', 'null']:
            return pd.NA
        if v in ('m2', 'sqm', 'sqmetre', 'sqmetres', 'square_metres', 'square metres'):
            return 'SQM'
        return str(val).strip().upper()

    # ==================================================================
    # Router
    # ==================================================================
    def process_group(self, group_name, rules):
        self._build_global_dictionaries()

        key = re.sub(r'[^a-z]', '', str(group_name).lower())
        handlers = {
            'contactrelationship': self._group_contact_relationship,
            'contactrequirement': self._group_contact_requirement,
            'contactnotes': self._group_contact_notes,
            'contactnote': self._group_contact_notes,
            'appraisal': self._group_appraisal,
            'appraisals': self._group_appraisal,
            'prospect': self._group_prospect,
            'contactpropertyrelationship': self._group_contact_property_relationship,
            'tasks': self._group_tasks,
            'task': self._group_tasks,
            'enquiries': self._group_enquiries,
            'enquiry': self._group_enquiries,
            'inspections': self._group_inspections,
            'inspection': self._group_inspections,
            'buyer': self._group_buyer,
            'seller': self._group_seller,
            'propertynotes': lambda r: self._process_property_notes(r),
        }

        handler = handlers.get(key)
        if not handler:
            self.log(f"[{self.job_id}] Rex: group '{group_name}' is not implemented yet. Skipped.")
            return

        try:
            handler(rules)
        except Exception as e:
            self.log(f"[{self.job_id}] Rex ERROR in group '{group_name}': {e}")
            return

        if key in ('contactnotes', 'contactnote'):
            self._process_property_notes()

    # ==================================================================
    # 1. Contact Relationship
    # ==================================================================
    def _group_contact_relationship(self, rules):
        base = self._read_table('contact_relationships.csv')
        if base.empty:
            self.log(f"[{self.job_id}] Rex Contact Relationship: contact_relationships.csv not found.")
            return

        out = pd.DataFrame(index=base.index)
        cid = self._clean_id(base['contact_id']) if 'contact_id' in base else pd.Series('', index=base.index)
        rid = self._clean_id(base['related_contact_id']) if 'related_contact_id' in base else pd.Series('', index=base.index)

        out['contact_identifier'] = cid.apply(lambda v: self._contact_ident(v))
        out['contact_partner_identifier'] = rid.apply(lambda v: self._contact_ident(v))
        out['contact_partnership_id'] = [f"{a}_{b}_r" if a and b else pd.NA
                                         for a, b in zip(cid, rid)]

        if 'relationship_type_id' in base.columns:
            out['contact_partnership_type'] = base['relationship_type_id'].apply(
                lambda v: self.RELATIONSHIP_TYPE_MAP.get(
                    str(v).strip().lower().replace(' ', '_'), 'Other')
                if pd.notna(v) and str(v).strip() else pd.NA)
        else:
            out['contact_partnership_type'] = pd.NA

        out['Contact Name'] = out['contact_identifier'].apply(self._contact_name)
        out['Partner Name'] = out['contact_partner_identifier'].apply(self._contact_name)

        out = out[out['contact_identifier'].notna()].reset_index(drop=True)
        self._export_data("Contact_Relationship", out)

    # ==================================================================
    # 2. Contact Requirement
    # ==================================================================
    def _group_contact_requirement(self, rules):
        profiles = self._read_table('match_profiles.csv')
        if profiles.empty:
            self.log(f"[{self.job_id}] Rex Contact Requirement: match_profiles.csv not found.")
            return

        crit = self._read_first('match_profile_criteria.csv',
                                'match_criteria.csv')
        list_crit = self._read_first('match_profile_list_criteria.csv',
                                     'match_list_criteria.csv')
        suburbs = self._read_first('match_profile_suburb_criteria.csv',
                                   'match_suburb_criteria.csv',
                                   'match_profile_suburbs.csv')

        def grouped(df, value_col, where=None):
            """match_profile_id -> first value (or comma-joined uniques)."""
            if df.empty or 'match_profile_id' not in df.columns or value_col not in df.columns:
                return {}
            d = df.copy()
            if where:
                col, want = where
                if col in d.columns:
                    d = d[d[col].astype(str).str.strip().str.lower() == want]
            d['_k'] = self._clean_id(d['match_profile_id'])
            d = d[d[value_col].notna()]
            return d.groupby('_k')[value_col].apply(
                lambda s: ','.join(dict.fromkeys(
                    str(x).strip() for x in s if str(x).strip().lower() not in ['', 'nan', 'none']))
            ).to_dict()

        def first_of(df, col):
            if df.empty or 'match_profile_id' not in df.columns or col not in df.columns:
                return {}
            d = df.copy()
            d['_k'] = self._clean_id(d['match_profile_id'])
            d = d[d[col].notna()]
            return d.drop_duplicates('_k').set_index('_k')[col].to_dict()

        price_min = first_of(crit, 'price_match__min')
        price_max = first_of(crit, 'price_match__max')
        bed_min, bed_max = first_of(crit, 'attr_bedrooms__min'), first_of(crit, 'attr_bedrooms__max')
        bath_min, bath_max = first_of(crit, 'attr_bathrooms__min'), first_of(crit, 'attr_bathrooms__max')
        car_min, car_max = first_of(crit, 'car_spaces__min'), first_of(crit, 'car_spaces__max')

        # land from/to live in the list-criteria table per the mapping, but fall
        # back to the criteria table when an export puts them there instead
        land_min = first_of(list_crit, 'attr_landarea_m2__min') or first_of(crit, 'attr_landarea_m2__min')
        land_max = first_of(list_crit, 'attr_landarea_m2__max') or first_of(crit, 'attr_landarea_m2__max')

        prop_types = grouped(list_crit, 'criteria_value', where=('criteria_type', 'listing_categories'))
        suburb_map = grouped(suburbs, 'suburb_or_town')

        # category -> sale method / category, from Contact_profile_mapping.csv
        sale_method_map, category_map = {}, {}
        pm = self.profile_map_df
        if not pm.empty and 'category' in pm.columns:
            pm = pm.copy()
            pm['_c'] = pm['category'].astype(str).str.strip().str.lower()
            for src, dest in [('contact_criteria_sale_method', sale_method_map),
                              ('contact_criteria_category', category_map)]:
                if src in pm.columns:
                    dest.update(pm.drop_duplicates('_c').set_index('_c')[src].to_dict())

        rows = []
        pid_col = 'id' if 'id' in profiles.columns else None
        for _, prof in profiles.iterrows():
            profile_id = self._sid(prof.get(pid_col)) if pid_col else ''
            contact_id = self._sid(prof.get('contact_id'))
            cat = str(prof.get('category', '')).strip().lower()

            lf = self._num(land_min.get(profile_id))
            lt = self._num(land_max.get(profile_id))

            record = {
                'contact_criteria_sale_method': self._clean_text(sale_method_map.get(cat)),
                'contact_criteria_category': self._clean_text(category_map.get(cat)),
                'contact_criteria_property_type': self._clean_text(prop_types.get(profile_id)),
                'contact_criteria_price_from': self._num(price_min.get(profile_id)),
                'contact_criteria_price_to': self._num(price_max.get(profile_id)),
                'contact_criteria_land_from': lf,
                'contact_criteria_land_to': lt,
                'contact_criteria_land_unit': 'SQM' if (pd.notna(lf) or pd.notna(lt)) else pd.NA,
                # "if max is blank use min"
                'contact_criteria_bedrooms': self._num(bed_max.get(profile_id)) if pd.notna(self._num(bed_max.get(profile_id))) else self._num(bed_min.get(profile_id)),
                'contact_criteria_bathrooms': self._num(bath_max.get(profile_id)) if pd.notna(self._num(bath_max.get(profile_id))) else self._num(bath_min.get(profile_id)),
                'contact_criteria_carspaces': self._num(car_max.get(profile_id)) if pd.notna(self._num(car_max.get(profile_id))) else self._num(car_min.get(profile_id)),
                'contact_criteria_suburbs': self._clean_text(suburb_map.get(profile_id)),
            }

            # "reflect including those _c2 onwards" -> one row per split
            idents = self._contact_ident(contact_id, all_splits=True)
            if not idents:
                single = self._contact_ident(contact_id)
                idents = [] if pd.isna(single) else [single]
            for ident in idents:
                rows.append({'contact_identifier': ident, **record})

        out = pd.DataFrame(rows)
        self._export_data("Contact_Requirement", out)

    # ==================================================================
    # 3. Contact Notes
    # ==================================================================
    def _group_contact_notes(self, rules):
        link = self._read_table('note_contacts.csv')
        if link.empty or self.notes_df.empty:
            self.log(f"[{self.job_id}] Rex Contact Notes: needs note_contacts.csv and notes.csv.")
            return

        note_col = next((c for c in ['note_id', 'noteid'] if c in link.columns), None)
        cid_col = next((c for c in ['contact_id', 'contactid'] if c in link.columns), None)
        if not note_col or not cid_col:
            self.log(f"[{self.job_id}] Rex Contact Notes: note_contacts.csv needs note_id + contact_id.")
            return

        base = link.copy()
        base['_note'] = self._clean_id(base[note_col])
        base['_contact'] = self._clean_id(base[cid_col])

        before = len(base)
        base = base[base['_note'].isin(self.notes_df.index)]
        self.log(f"[{self.job_id}] Rex Contact Notes: {len(base)} of {before} links matched a note.")
        if base.empty:
            return

        # notes attached to a property, so the note rows can carry property context
        note_to_property = {}
        np_df = self._read_table('note_properties.csv')
        if not np_df.empty:
            npc = next((c for c in ['note_id', 'noteid'] if c in np_df.columns), None)
            npp = next((c for c in ['property_id', 'propertyid'] if c in np_df.columns), None)
            if npc and npp:
                note_to_property = dict(zip(self._clean_id(np_df[npc]),
                                            self._clean_id(np_df[npp])))

        def nval(key, col):
            if col not in self.notes_df.columns or key not in self.notes_df.index:
                return pd.NA
            return self.notes_df.at[key, col]

        date_col = next((c for c in ['system_ctime', 'system_modtime', 'system_createtime']
                         if c in self.notes_df.columns), None)

        out = pd.DataFrame(index=base.index)
        out['contact_identifier'] = base['_contact'].apply(lambda v: self._contact_ident(v))
        out['contact_note_created_date'] = base['_note'].apply(lambda k: self._date_only(nval(k, date_col)))
        out['contact_note_team_member'] = base['_note'].apply(
            lambda k: self._clean_text(nval(k, 'system_created_user_name')))
        out['contact_notes'] = base['_note'].apply(self._compose_note)

        # property context, joined through note_properties.csv where available
        prop_ids = base['_note'].map(note_to_property)
        out['property_identifier'] = prop_ids.apply(
            lambda p: self._property_ident(property_id=p) if isinstance(p, str) and p else pd.NA)
        out['property_appraisal_date'] = prop_ids.apply(self._appraisal_date_for_property)

        if not note_to_property:
            self.log(f"[{self.job_id}] Rex Contact Notes: no note_properties.csv, so "
                     f"property_identifier / property_appraisal_date are blank.")

        kept = out['contact_notes'].notna()
        dropped = int((~kept).sum())
        if dropped:
            self.log(f"[{self.job_id}] Rex Contact Notes: dropped {dropped} rows with an empty note body.")
        self._export_data("Contact_Notes", out[kept].reset_index(drop=True))

    def _compose_note(self, note_key):
        """'{note_type_id} - {note}'"""
        if note_key not in self.notes_df.index:
            return pd.NA
        body = self._clean_text(self.notes_df.at[note_key, 'note']) \
            if 'note' in self.notes_df.columns else pd.NA
        if pd.isna(body):
            return pd.NA
        # exports name this either note_type_id or note_type
        type_col = next((c for c in ['note_type_id', 'note_type']
                         if c in self.notes_df.columns), None)
        tid = self.notes_df.at[note_key, type_col] if type_col else pd.NA
        tid_s = self._sid(tid)
        return f"{tid_s} - {body}" if tid_s and tid_s.lower() not in ['nan', 'none'] else body

    def _appraisal_date_for_property(self, property_id):
        pid = self._sid(property_id)
        if not pid or self.appraisals_df.empty or 'property_id' not in self.appraisals_df.columns:
            return pd.NA
        if 'appraisal_date' not in self.appraisals_df.columns:
            return pd.NA
        hit = self.appraisals_df[self._clean_id(self.appraisals_df['property_id']) == pid]
        if hit.empty:
            return pd.NA
        return self._date_only(hit.iloc[0]['appraisal_date'])

    # ==================================================================
    # 4. Appraisal
    # ==================================================================
    def _group_appraisal(self, rules):
        appr = self._read_table('appraisals.csv')
        if appr.empty:
            self.log(f"[{self.job_id}] Rex Appraisal: appraisals.csv not found.")
            return

        out = pd.DataFrame(index=appr.index)
        aid = self._clean_id(appr['id']) if 'id' in appr else pd.Series('', index=appr.index)
        pid = self._clean_id(appr['property_id']) if 'property_id' in appr else pd.Series('', index=appr.index)

        out['property_identifier'] = aid.apply(lambda v: f"{self.APPRAISAL_PREFIX}{v}" if v else pd.NA)
        out['property_appraisal_date'] = appr['appraisal_date'].apply(self._date_only) \
            if 'appraisal_date' in appr else pd.NA
        out['property_team_member_1'] = appr['agent_1_name'].apply(self._clean_text) \
            if 'agent_1_name' in appr else pd.NA
        out['property_team_member_2'] = appr['agent_2_name'].apply(self._clean_text) \
            if 'agent_2_name' in appr else pd.NA

        # "If price_max [blank] use the price_min"
        pmax = appr['price_max'] if 'price_max' in appr else pd.Series(pd.NA, index=appr.index)
        pmin = appr['price_min'] if 'price_min' in appr else pd.Series(pd.NA, index=appr.index)
        out['property_vendor_price'] = [
            self._num(a) if pd.notna(self._num(a)) else self._num(b) for a, b in zip(pmax, pmin)]

        rent = appr['price_rent'] if 'price_rent' in appr else pd.Series(pd.NA, index=appr.index)
        out['property_rent_per_week'] = rent.apply(self._num)

        for field, col in [('property_unit_number', 'adr_unit_number'),
                           ('property_street_number', 'adr_street_number'),
                           ('property_street_name', 'adr_street_name'),
                           ('property_suburb', 'adr_suburb_or_town'),
                           ('property_postcode', 'adr_postcode'),
                           ('property_state', 'adr_state_or_region'),
                           ('property_building_name', 'adr_building_name')]:
            out[field] = pid.apply(lambda p, c=col: self._clean_text(self._prop_val(p, c)))

        out['property_full_address'] = pid.apply(self._full_address)

        # "if appraisal_type_id = Rent, value is Lease, or if price_rent has value -> Lease, else Sale"
        atype = appr['appraisal_type_id'] if 'appraisal_type_id' in appr else pd.Series('', index=appr.index)
        out['property_sale_method'] = [
            'Lease' if ('rent' in str(t).strip().lower() or pd.notna(self._num(r))) else 'Sale'
            for t, r in zip(atype, rent)]

        for field, col in [('property_bedrooms', 'attr_bedrooms'),
                           ('property_bathrooms', 'attr_bathrooms'),
                           ('property_toilets', 'attr_toilets'),
                           ('property_garages', 'attr_garages'),
                           ('property_carports', 'attr_carports'),
                           ('property_open_parking_spaces', 'attr_open_spaces'),
                           ('property_land_size_m2', 'attr_landarea_m2'),
                           ('property_year_built', 'attr_build_year')]:
            out[field] = pid.apply(lambda p, c=col: self._num(self._prop_val(p, c)))

        out['listing_land_size_system'] = pid.apply(
            lambda p: self._land_unit(self._prop_val(p, 'attr_landarea_unit_id')))
        out['property_timeline_status'] = 'Appraisal'
        out['property_notes'] = pid.apply(lambda p: self._clean_text(self._prop_val(p, 'property_note')))

        # Appraisal: "If value = Land, use Business"
        # (Prospect uses a different rule - Land becomes Residential there.)
        def appraisal_category(p):
            val = self._clean_text(self._prop_val(p, 'property_category_id'))
            if pd.notna(val) and str(val).strip().lower() == 'land':
                return 'Business'
            return val
        out['property_category'] = pid.apply(appraisal_category)

        # The appraisal's OWN system_modtime, not the linked property's - those
        # are different records and their dates routinely differ by a day.
        out['property_modified_date'] = appr['system_modtime'].apply(self._date_only) \
            if 'system_modtime' in appr else pd.NA

        stage = appr['pipeline_stage_name'] if 'pipeline_stage_name' in appr else pd.Series('', index=appr.index)
        out['property_pipeline_rating'] = stage.apply(
            lambda v: self.PIPELINE_RATING_MAP.get(str(v).strip().lower(), 'D'))

        out['appraisal_state'] = appr['appraisal_state'].apply(self._clean_text) \
            if 'appraisal_state' in appr else pd.NA
        out['Property ID'] = pid
        out['For Import'] = self._appraisal_for_import(out)

        self._export_data("Appraisal", out.reset_index(drop=True))

    def _appraisal_for_import(self, out):
        """
        'N' when the address is blank or the appraisal is archived. Otherwise
        de-duplicate on address + category + sale method, keeping only the row
        with the latest appraisal date as 'Y'.
        """
        flags = pd.Series('Y', index=out.index)

        blank_addr = out['property_full_address'].isna()
        flags[blank_addr] = 'N'

        if 'appraisal_state' in out.columns:
            archived = out['appraisal_state'].astype(str).str.strip().str.lower() == 'archived'
            flags[archived] = 'N'

        eligible = out[flags == 'Y'].copy()
        if eligible.empty:
            return flags

        eligible['_sort'] = pd.to_datetime(eligible['property_appraisal_date'],
                                           dayfirst=True, errors='coerce')
        key_cols = ['property_full_address', 'property_category', 'property_sale_method']
        key_cols = [c for c in key_cols if c in eligible.columns]
        eligible['_key'] = eligible[key_cols].astype(str).agg('|'.join, axis=1)

        # latest appraisal date wins; NaT sorts last so a dated row is preferred
        winners = (eligible.sort_values('_sort', ascending=False, na_position='last')
                           .drop_duplicates('_key').index)
        losers = eligible.index.difference(winners)
        flags[losers] = 'N'

        dupes = len(losers)
        if dupes:
            self.log(f"[{self.job_id}] Rex Appraisal: {dupes} duplicate appraisal(s) marked 'N' "
                     f"(kept the most recent per address/category/method).")
        return flags

    # ==================================================================
    # 5. Prospect
    # ==================================================================
    def _group_prospect(self, rules):
        props = self._read_table('properties.csv')
        if props.empty:
            self.log(f"[{self.job_id}] Rex Prospect: properties.csv not found.")
            return
        if 'id' not in props.columns:
            self.log(f"[{self.job_id}] Rex Prospect: properties.csv has no 'id' column.")
            return

        props = props.copy()
        props['_pid'] = self._clean_id(props['id'])

        before = len(props)
        props = props[props['_pid'].isin(self.prospect_ids)]
        self.log(f"[{self.job_id}] Rex Prospect: {len(props)} of {before} properties are prospects "
                 f"(excluded those present in appraisals.csv or listings.csv).")
        if props.empty:
            return

        out = pd.DataFrame(index=props.index)
        out['property_identifier'] = props['_pid'].apply(lambda v: f"{self.PROSPECT_PREFIX}{v}")
        out['property_modified_date'] = props['system_modtime'].apply(self._date_only) \
            if 'system_modtime' in props else pd.NA
        out['property_timeline_status'] = 'Prospect'
        out['property_sale_method'] = 'Sale'
        out['property_team_member_1'] = props['system_owner_user_name'].apply(self._clean_text) \
            if 'system_owner_user_name' in props else pd.NA

        for field, col in [('property_unit_number', 'adr_unit_number'),
                           ('property_street_number', 'adr_street_number'),
                           ('property_street_name', 'adr_street_name'),
                           ('property_suburb', 'adr_suburb_or_town'),
                           ('property_postcode', 'adr_postcode'),
                           ('property_state', 'adr_state_or_region'),
                           ('property_building_name', 'adr_building_name')]:
            out[field] = props[col].apply(self._clean_text) if col in props else pd.NA

        out['property_full_address'] = props['_pid'].apply(self._full_address)

        for field, col in [('property_bedrooms', 'attr_bedrooms'),
                           ('property_bathrooms', 'attr_bathrooms'),
                           ('property_toilets', 'attr_toilets'),
                           ('property_garages', 'attr_garages'),
                           ('property_carports', 'attr_carports'),
                           ('property_open_parking_spaces', 'attr_open_spaces'),
                           ('property_living_area_m2', 'attr_buildarea_m2'),
                           ('property_land_size_m2', 'attr_landarea_m2'),
                           ('property_year_built', 'attr_build_year')]:
            out[field] = props[col].apply(self._num) if col in props else pd.NA

        out['listing_land_size_system'] = props['attr_landarea_unit_id'].apply(self._land_unit) \
            if 'attr_landarea_unit_id' in props else pd.NA
        out['property_notes'] = props['property_note'].apply(self._clean_text) \
            if 'property_note' in props else pd.NA

        # "If land use Residential the rest use the actual value"
        if 'property_category_id' in props.columns:
            out['property_category'] = props['property_category_id'].apply(
                lambda v: 'Residential' if str(v).strip().lower() == 'land'
                else self._clean_text(v))
        else:
            out['property_category'] = pd.NA

        out['Prospect State'] = props['system_record_state'].apply(self._clean_text) \
            if 'system_record_state' in props else pd.NA
        out['Property ID'] = props['_pid']

        self._export_data("Prospect", out.reset_index(drop=True))

    # ==================================================================
    # 6. Contact property Relationship
    # ==================================================================
    def _group_contact_property_relationship(self, rules):
        base = self._read_table('contact_reln_property.csv')
        if base.empty:
            self.log(f"[{self.job_id}] Rex Contact property Relationship: contact_reln_property.csv not found.")
            return

        if 'reln_type_id' in base.columns:
            before = len(base)
            base = base[base['reln_type_id'].astype(str).str.strip().str.lower() == 'owner']
            self.log(f"[{self.job_id}] Rex Contact property Relationship: kept {len(base)} of "
                     f"{before} rows where reln_type_id = owner.")
        if base.empty:
            return

        rows = []
        for _, r in base.iterrows():
            contact_id = self._sid(r.get('contact_id'))
            prop_id = self._sid(r.get('property_id'))
            ident = self._property_ident(property_id=prop_id)

            record = {
                'contact_sale_type': 'Seller',
                'property_identifier': ident,
                'Property Address': self._full_address(prop_id),
                'Status': self._property_status(ident),
            }
            # "Create instance as well for _c2 and above"
            idents = self._contact_ident(contact_id, all_splits=True)
            if not idents:
                single = self._contact_ident(contact_id)
                idents = [] if pd.isna(single) else [single]
            for ci in idents:
                rows.append({'contact_identifier': ci,
                             'Contact Name': self._contact_name(ci), **record})

        out = pd.DataFrame(rows)
        if not out.empty:
            out = out[['contact_identifier', 'contact_sale_type', 'property_identifier',
                       'Contact Name', 'Property Address', 'Status']]
        self._export_data("Contact_Property_Relationship", out)

    # ==================================================================
    # 7. Tasks
    # ==================================================================
    def _group_tasks(self, rules):
        base = self._read_table('reminders.csv')
        if base.empty:
            self.log(f"[{self.job_id}] Rex Tasks: reminders.csv not found.")
            return

        out = pd.DataFrame(index=base.index)
        tid = self._clean_id(base['id']) if 'id' in base else pd.Series('', index=base.index)
        prop_id = self._clean_id(base['property_id']) if 'property_id' in base else pd.Series('', index=base.index)
        list_id = self._clean_id(base['listing_id']) if 'listing_id' in base else pd.Series('', index=base.index)
        cont_id = self._clean_id(base['contact_id']) if 'contact_id' in base else pd.Series('', index=base.index)

        out['task_identifier'] = tid.apply(lambda v: f"{v}_T" if v else pd.NA)
        out['task_status'] = base['system_record_state'].apply(self._clean_text) \
            if 'system_record_state' in base else pd.NA
        out['task_team_member_1'] = base['system_created_user_name'].apply(self._clean_text) \
            if 'system_created_user_name' in base else pd.NA
        # Blank means blank - never write 'nan'/'none' into an id column.
        out['property_id'] = self._blank(prop_id)
        out['contact_id'] = self._blank(cont_id)
        out['listing_id'] = self._blank(list_id)

        idents = [self._property_ident(property_id=p, listing_id=l) for p, l in zip(prop_id, list_id)]
        out['property_identifier'] = self._blank(pd.Series(idents, index=out.index))
        out['contact_identifier'] = cont_id.apply(lambda v: self._contact_ident(v))
        out['task_subject'] = base['reminder_type_id'].apply(self._clean_text) \
            if 'reminder_type_id' in base else pd.NA
        out['task_notes'] = base['reminder_description'].apply(self._clean_text) \
            if 'reminder_description' in base else pd.NA
        out['task_date_due'] = base['reminder_date'].apply(self._date_only) \
            if 'reminder_date' in base else pd.NA
        out['Property Status'] = [self._property_status(i, listing_id=l)
                                  for i, l in zip(idents, list_id)]

        self._export_data("Tasks", out.reset_index(drop=True))

    # ==================================================================
    # 8. Enquiries
    # ==================================================================
    def _group_enquiries(self, rules):
        base = self._read_table('feedback.csv')
        if base.empty:
            self.log(f"[{self.job_id}] Rex Enquiries: feedback.csv not found.")
            return

        if 'feedback_type_id' in base.columns:
            before = len(base)
            base = base[base['feedback_type_id'].astype(str).str.strip().str.lower()
                        .str.contains('enquiry', na=False)]
            self.log(f"[{self.job_id}] Rex Enquiries: kept {len(base)} of {before} rows "
                     f"where feedback_type_id is an Enquiry.")
        if base.empty:
            return

        # feedback id -> contact id
        fb_contacts = {}
        fc = self._read_table('feedback_contacts.csv')
        if not fc.empty and 'feedback_id' in fc.columns and 'contact_id' in fc.columns:
            fb_contacts = dict(zip(self._clean_id(fc['feedback_id']),
                                   self._clean_id(fc['contact_id'])))

        out = pd.DataFrame(index=base.index)
        fid = self._clean_id(base['id']) if 'id' in base else pd.Series('', index=base.index)
        lid = self._clean_id(base['listing_id']) if 'listing_id' in base else pd.Series('', index=base.index)

        out['enquiry_identifier'] = fid.apply(lambda v: f"{v}_E" if v else pd.NA)
        out['contact_identifier'] = fid.apply(
            lambda f: self._contact_ident(fb_contacts.get(f, '')))
        out['zenu_contact_id'] = pd.NA
        out['Listing ID'] = lid

        idents = [self._property_ident(listing_id=l) for l in lid]
        out['property_identifier'] = idents
        out['zenu_property_id'] = pd.NA

        out['enquiry_status'] = base['system_record_state'].apply(
            lambda v: self.ENQUIRY_STATUS_MAP.get(str(v).strip().lower(), 'Active')) \
            if 'system_record_state' in base else 'Active'
        out['enquiry_team_member_1'] = base['agent_name'].apply(self._clean_text) \
            if 'agent_name' in base else pd.NA
        out['enquiry_source'] = base['enquiry_source_name'].apply(self._clean_text) \
            if 'enquiry_source_name' in base else pd.NA
        out['enquiry_date_created'] = base['date_of'].apply(self._date_only) \
            if 'date_of' in base else pd.NA
        out['enquiry_notes'] = base['feedback_detail'].apply(self._clean_text) \
            if 'feedback_detail' in base else pd.NA
        out['Property Status'] = [self._property_status(i, listing_id=l)
                                  for i, l in zip(idents, lid)]

        self._export_data("Enquiries", out.reset_index(drop=True))

    # ==================================================================
    # 9. Inspections
    # ==================================================================
    def _group_inspections(self, rules):
        base = self._read_table('feedback_contacts.csv')
        if base.empty:
            self.log(f"[{self.job_id}] Rex Inspections: feedback_contacts.csv not found.")
            return
        if 'feedback_id' not in base.columns:
            self.log(f"[{self.job_id}] Rex Inspections: feedback_contacts.csv has no feedback_id.")
            return

        base = base.copy()
        base['_fid'] = self._clean_id(base['feedback_id'])
        base['_cid'] = self._clean_id(base['contact_id']) if 'contact_id' in base else ''

        # "17279317_1, _2, _3" - running count within each feedback_id
        base['_seq'] = base.groupby('_fid').cumcount() + 1

        # feedback_individual: per-contact detail, keyed by (feedback_id, contact_id)
        indiv_detail, indiv_interest, indiv_price = {}, {}, {}
        fi = self._read_table('feedback_individual.csv')
        fic = self._read_table('feedback_individual_contacts.csv')
        if not fi.empty and not fic.empty:
            fi_key = 'id' if 'id' in fi.columns else None
            link_key = next((c for c in ['feedback_individual_id', 'feedback_invidual_id']
                             if c in fic.columns), None)
            if fi_key and link_key and 'contact_id' in fic.columns:
                fi_idx = fi.copy()
                fi_idx['_k'] = self._clean_id(fi_idx[fi_key])
                fi_idx = fi_idx.drop_duplicates('_k').set_index('_k')
                for _, r in fic.iterrows():
                    ind_id = self._sid(r.get(link_key))
                    cid = self._sid(r.get('contact_id'))
                    if ind_id not in fi_idx.index:
                        continue
                    row = fi_idx.loc[ind_id]
                    fid = self._sid(row.get('feedback_id'))
                    k = (fid, cid)
                    if 'feedback_detail' in fi_idx.columns:
                        indiv_detail[k] = row.get('feedback_detail')
                    if 'interest_level_id' in fi_idx.columns:
                        indiv_interest[k] = row.get('interest_level_id')
                    if 'price_indication' in fi_idx.columns:
                        indiv_price[k] = row.get('price_indication')

        def fb(fid, col):
            if col not in self.feedback_df.columns or fid not in self.feedback_df.index:
                return pd.NA
            return self.feedback_df.at[fid, col]

        out = pd.DataFrame(index=base.index)
        out['inspection_identifier'] = [f"{f}_{s}" for f, s in zip(base['_fid'], base['_seq'])]
        out['contact_identifier'] = base['_cid'].apply(lambda v: self._contact_ident(v))
        out['zenu_contact_id'] = pd.NA

        listing_ids = base['_fid'].apply(lambda f: self._sid(fb(f, 'listing_id')))
        out['Listing ID/Property ID'] = listing_ids
        idents = [self._property_ident(listing_id=l) for l in listing_ids]
        out['property_identifier'] = idents
        out['zenu_property_id'] = pd.NA

        out['inspection_is_private'] = base['_fid'].apply(
            lambda f: 'TRUE' if 'inspection' in str(fb(f, 'feedback_type_id')).strip().lower()
            else 'FALSE')
        out['inspection_team_member_1'] = base['_fid'].apply(
            lambda f: self._clean_text(fb(f, 'agent_name')))
        out['inspection_start_date'] = base['_fid'].apply(
            lambda f: self._date_time(fb(f, 'date_of'), fb(f, 'date_time_start')))
        out['inspection_end_date'] = base['_fid'].apply(
            lambda f: self._date_time(fb(f, 'date_of'), fb(f, 'date_time_finish')))

        # per-contact feedback first, falling back to the shared feedback row
        pairs = list(zip(base['_fid'], base['_cid']))

        def note_for(k):
            """Per-contact feedback, else the shared feedback row, else 'N/A'."""
            detail = self._clean_text(indiv_detail.get(k))
            if pd.isna(detail):
                detail = self._clean_text(fb(k[0], 'feedback_detail'))
            return 'N/A' if pd.isna(detail) else detail

        out['inspection_notes'] = [note_for(k) for k in pairs]
        out['inspection_is_interested'] = [
            self.INTEREST_MAP.get(str(indiv_interest.get(k, '')).strip().lower(), pd.NA)
            for k in pairs]
        out['inspection_feedback_price'] = [self._num(indiv_price.get(k)) for k in pairs]

        self._export_data("Inspections", out.reset_index(drop=True))

    # ==================================================================
    # 10. Buyer
    # ==================================================================
    def _group_buyer(self, rules):
        base = self._read_table('listing_contract_purchtenants.csv')
        if base.empty:
            self.log(f"[{self.job_id}] Rex Buyer: listing_contract_purchtenants.csv not found.")
            return

        def con(cid, col):
            k = self._sid(cid)
            if not k or self.contracts_df.empty or col not in self.contracts_df.columns:
                return pd.NA
            if k in self.contracts_df.index:
                return self.contracts_df.at[k, col]
            return pd.NA

        out = pd.DataFrame(index=base.index)
        pt = self._clean_id(base['purchtenant_id']) if 'purchtenant_id' in base else pd.Series('', index=base.index)
        ct = self._clean_id(base['contract_id']) if 'contract_id' in base else pd.Series('', index=base.index)

        out['contact_identifier'] = pt.apply(lambda v: self._contact_ident(v))
        out['zenu_contact_id'] = pd.NA
        out['contact_sale_type'] = 'PURCHASER'
        out['Solicitor ID'] = ct.apply(
            lambda c: self._contact_ident(self._sid(con(c, 'purchtenant_solicitor_id'))))
        out['zenu_solicitor_id'] = pd.NA

        listing_ids = ct.apply(lambda c: self._sid(con(c, 'listing_id')))
        out['property_identifier'] = [self._property_ident(listing_id=l) for l in listing_ids]
        out['Property ID'] = listing_ids.apply(lambda l: self.listing_to_property.get(l, pd.NA))
        out['zenu_property_id'] = pd.NA
        out['property_sold_price'] = ct.apply(
            lambda c: self._num(con(c, 'detail_sale_price_or_lease_pa')))
        out['property_contract_date'] = ct.apply(
            lambda c: self._date_only(con(c, 'date_actual_accepted')))
        out['Status'] = listing_ids.apply(
            lambda l: self._clean_text(self.listings_df.at[l, 'system_listing_state'])
            if l and not self.listings_df.empty and l in self.listings_df.index
            and 'system_listing_state' in self.listings_df.columns else pd.NA)
        out['Buyer Name'] = out['contact_identifier'].apply(self._contact_name)
        out['Property Address'] = out['Property ID'].apply(self._full_address)

        self._export_data("Buyer", out.reset_index(drop=True))

    # ==================================================================
    # 11. Seller
    # ==================================================================
    def _group_seller(self, rules):
        base = self._read_table('contact_reln_listings.csv')
        if base.empty:
            self.log(f"[{self.job_id}] Rex Seller: contact_reln_listings.csv not found.")
            return

        if 'reln_type_id' in base.columns:
            before = len(base)
            base = base[base['reln_type_id'].astype(str).str.strip().str.lower() == 'owner']
            self.log(f"[{self.job_id}] Rex Seller: kept {len(base)} of {before} rows "
                     f"where reln_type_id = owner.")
        if base.empty:
            return

        out = pd.DataFrame(index=base.index)
        cid = self._clean_id(base['contact_id']) if 'contact_id' in base else pd.Series('', index=base.index)
        lid = self._clean_id(base['listing_id']) if 'listing_id' in base else pd.Series('', index=base.index)

        out['contact_identifier'] = cid.apply(lambda v: self._contact_ident(v))
        out['zenu_contact_id'] = pd.NA
        out['contact_sale_type'] = 'SELLER'
        out['property_identifier'] = lid
        out['Property ID'] = lid.apply(lambda l: self.listing_to_property.get(l, pd.NA))
        out['zenu_property_id'] = pd.NA
        out['Property Status'] = lid.apply(
            lambda l: self._clean_text(self.listings_df.at[l, 'system_listing_state'])
            if l and not self.listings_df.empty and l in self.listings_df.index
            and 'system_listing_state' in self.listings_df.columns else pd.NA)
        out['Owner Name'] = out['contact_identifier'].apply(self._contact_name)
        out['Property Address'] = out['Property ID'].apply(self._full_address)

        self._export_data("Seller", out.reset_index(drop=True))

    # ==================================================================
    # Extra: Property_Notes (triggered by note_properties.csv)
    # ==================================================================
    def _process_property_notes(self, rules=None):
        """Pull the note_ids listed in note_properties.csv out of notes.csv."""
        if self.property_notes_done:
            return
        link = self._read_table('note_properties.csv')
        if link.empty:
            return
        self.property_notes_done = True

        if self.notes_df.empty:
            self.log(f"[{self.job_id}] Rex Property Notes: note_properties.csv found but "
                     f"notes.csv is missing. Skipped.")
            return

        note_col = next((c for c in ['note_id', 'noteid'] if c in link.columns), None)
        if not note_col:
            self.log(f"[{self.job_id}] Rex Property Notes: no note id column in note_properties.csv.")
            return
        prop_col = next((c for c in ['property_id', 'propertyid'] if c in link.columns), None)

        base = link.copy()
        base['_note'] = self._clean_id(base[note_col])
        requested = base['_note'].nunique()
        base = base[base['_note'].isin(self.notes_df.index)]
        self.log(f"[{self.job_id}] Rex Property Notes: requested {requested} note ids; "
                 f"{base['_note'].nunique()} found in notes.csv.")
        if base.empty:
            return

        date_col = next((c for c in ['system_ctime', 'system_modtime'] if c in self.notes_df.columns), None)
        has_user = 'system_created_user_name' in self.notes_df.columns
        prop_ids = self._clean_id(base[prop_col]) if prop_col else pd.Series('', index=base.index)

        def listing_status(lid):
            if (not lid or self.listings_df.empty or lid not in self.listings_df.index
                    or 'system_listing_state' not in self.listings_df.columns):
                return pd.NA
            return self._clean_text(self.listings_df.at[lid, 'system_listing_state'])

        # Expanded 1-to-many against the property's listings. One property can
        # carry several listings, so each note repeats once per listing with
        # that listing's status. Appraisal/Prospect notes keep their original
        # row as well; notes on an already-listed property get listing rows only.
        rows = []
        originals = listing_rows = orphans = 0

        for note_key, prop_id in zip(base['_note'], prop_ids):
            body = self._compose_note(note_key)
            if pd.isna(body):
                continue

            common = {
                'note_id': note_key,
                'property_identifier Raw': prop_id or pd.NA,
                'property_note_created_date':
                    self._date_only(self.notes_df.at[note_key, date_col]) if date_col else pd.NA,
                'property_note_team_member':
                    self._clean_text(self.notes_df.at[note_key, 'system_created_user_name'])
                    if has_user else pd.NA,
                'property_notes': body,
            }

            if prop_id in self.appraisal_by_property:
                base_ident, base_status = self.appraisal_by_property[prop_id], 'Appraisal'
            elif prop_id in self.prospect_ids:
                base_ident, base_status = f"{self.PROSPECT_PREFIX}{prop_id}", 'Prospect'
            else:
                base_ident = base_status = None

            if base_ident:
                rows.append({**common, 'property_identifier': base_ident,
                             'Property Status': base_status})
                originals += 1

            for lid in self.listings_by_property.get(prop_id, []):
                rows.append({**common, 'property_identifier': lid,
                             'Property Status': listing_status(lid)})
                listing_rows += 1

            if not base_ident and not self.listings_by_property.get(prop_id):
                # neither appraised, prospect, nor listed - keep the raw id
                rows.append({**common, 'property_identifier': prop_id or pd.NA,
                             'Property Status': pd.NA})
                orphans += 1

        if not rows:
            self.log(f"[{self.job_id}] Rex Property Notes: no rows produced.")
            return

        out = pd.DataFrame(rows)[[
            'note_id', 'property_identifier Raw', 'property_identifier',
            'property_note_created_date', 'property_note_team_member',
            'property_notes', 'Property Status']]

        self.log(f"[{self.job_id}] Rex Property Notes: {len(out)} rows "
                 f"({originals} appraisal/prospect originals + {listing_rows} listing rows"
                 + (f" + {orphans} unlinked" if orphans else "") + ").")
        self._export_data("Property_Notes", out.reset_index(drop=True))

    # ==================================================================
    # Export
    # ==================================================================
    def _export_data(self, group_name, df):
        """Handles splitting massive files into safe chunks for Excel (XLSX)."""
        if df is None or df.empty:
            self.log(f"[{self.job_id}] Rex: {group_name} produced 0 rows - nothing exported.")
            return

        safe_group_name = str(group_name).replace(" ", "_").replace("/", "_")
        chunk_limit = self.engine.chunk_size
        total_rows = len(df)

        if chunk_limit > 0 and total_rows > chunk_limit:
            self.log(f"[{self.job_id}] Result size ({total_rows}) exceeds limit. "
                     f"Splitting into chunks of {chunk_limit}...")
            num_chunks = (total_rows // chunk_limit) + (1 if total_rows % chunk_limit != 0 else 0)
            for i in range(num_chunks):
                chunk_df = df.iloc[i * chunk_limit:(i + 1) * chunk_limit]
                if not chunk_df.empty:
                    path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final_pt{i+1}.xlsx")
                    chunk_df.to_excel(path, index=False)
                    self.log(f"[{self.job_id}] SUCCESS: Created {path} ({len(chunk_df)} rows)")
        else:
            path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final.xlsx")
            df.to_excel(path, index=False)
            self.log(f"[{self.job_id}] SUCCESS: Created {path} ({total_rows} rows)")
