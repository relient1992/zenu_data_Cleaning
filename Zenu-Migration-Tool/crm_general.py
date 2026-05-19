import pandas as pd
import sqlite3
import os
import re
import numpy as np
from collections import Counter

# ---------------------------------------------------------------------------
# COUPLE DETECTION & TITLE HELPERS
# ---------------------------------------------------------------------------

_COUPLE_PATTERN = re.compile(
    r'\band\b|\s*&\s*',
    flags=re.IGNORECASE
)

_COMPANY_KEYWORDS = re.compile(
    r'\b(pty\s*ltd|pty|ltd|inc|llc|corp|corporation|trust|group|holdings|'
    r'enterprises|proprietary|limited|p/l)\b',
    flags=re.IGNORECASE
)

_JUNK_CHARS = re.compile(r'[0-9!@#$%^*()_+=\[\]{};:"\\|<>/]+')


def _is_company(text: str) -> bool:
    return bool(_COMPANY_KEYWORDS.search(text))


def _clean_name_text(text: str) -> str:
    """Strip junk characters, collapse whitespace, title-case."""
    text = _JUNK_CHARS.sub('', text)
    text = re.sub(r' +', ' ', text).strip()
    return text.title()


def _is_couple(name_val: str) -> bool:
    """Return True if the name field contains two people joined by 'and' / '&'."""
    return bool(_COUPLE_PATTERN.search(str(name_val)))


def _split_couple_firstnames(name_val: str) -> list[str]:
    """Split a couple name into individual first names."""
    parts = _COUPLE_PATTERN.split(str(name_val))
    result = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        tokens = p.split()
        result.append(tokens[0].title() if tokens else '')
    return [n for n in result if n]


def _split_couple_titles(title_val, n_people: int) -> list:
    """Split a title cell into exactly n_people title values."""
    if pd.isna(title_val):
        return [pd.NA] * n_people

    raw = str(title_val).strip()
    if not raw or raw.lower() in ('nan', 'none', 'null'):
        return [pd.NA] * n_people

    parts = _COUPLE_PATTERN.split(raw)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) >= n_people:
        return [p.title() for p in parts[:n_people]]

    if len(parts) == 1:
        return [parts[0].title()] * n_people

    result = [p.title() for p in parts]
    while len(result) < n_people:
        result.append(result[0])
    return result


# ---------------------------------------------------------------------------
# MAIN NAME PARSER  
# ---------------------------------------------------------------------------

def advanced_name_parser(val, target_field: str, split_mode: str = "single_field"):
    if pd.isna(val):
        return pd.NA
    text = str(val).strip()
    if not text or text.lower() in ('nan', 'none', 'null'):
        return pd.NA

    if _is_company(text):
        return text.title() if target_field == 'company_name' else pd.NA
    else:
        if target_field == 'company_name':
            return pd.NA

    text = _clean_name_text(text)
    if not text:
        return pd.NA

    if split_mode == "single_field":
        parts = text.split()
        if not parts:
            return pd.NA
        if len(parts) == 1:
            return parts[0] if target_field == 'contact_first_name' else pd.NA
        if target_field == 'contact_first_name':
            return parts[0]
        else:  
            return " ".join(parts[1:])

    elif split_mode == "dual_field":
        return text  

    return text


# ---------------------------------------------------------------------------
# COUPLE EXPANSION & PARTNERSHIP GENERATOR
# ---------------------------------------------------------------------------

def expand_couples_in_df(
    df: pd.DataFrame,
    name_col: str | None,
    surname_col: str | None,
    id_col: str | None,
    title_col: str | None = None,
    split_mode: str = "single_field",
) -> pd.DataFrame:
    expanded_rows = []
    consumed_cols = {c for c in (name_col, surname_col, id_col, title_col) if c}

    for _, row in df.iterrows():
        raw_name    = row[name_col]    if (name_col    and name_col    in df.columns) else pd.NA
        raw_surname = row[surname_col] if (surname_col and surname_col in df.columns) else pd.NA
        raw_id      = row[id_col]      if (id_col      and id_col      in df.columns) else pd.NA
        raw_title   = row[title_col]   if (title_col   and title_col   in df.columns) else pd.NA

        str_id = str(raw_id).replace('.0', '').strip() if not pd.isna(raw_id) else ''
        is_co = _is_company(str(raw_name)) if not pd.isna(raw_name) else False
        other = {k: v for k, v in row.items() if k not in consumed_cols}

        # Handle Companies
        if is_co:
            new_row = dict(other)
            new_row['contact_title']              = pd.NA
            new_row['contact_first_name']         = pd.NA
            new_row['contact_surname']            = pd.NA
            new_row['company_name']               = _clean_name_text(str(raw_name))
            new_row['contact_identifier']         = f"{str_id}_c" if str_id else pd.NA
            new_row['contact_partner_identifier'] = pd.NA
            new_row['contact_partnership_id']     = pd.NA
            new_row['contact_partnership_type']   = pd.NA
            expanded_rows.append(new_row)
            continue

        name_str = str(raw_name).strip() if not pd.isna(raw_name) else ''
        couple   = _is_couple(name_str) and split_mode == "single_field"

        clean_surname = pd.NA
        if not pd.isna(raw_surname):
            clean_surname = advanced_name_parser(raw_surname, 'contact_surname', 'dual_field')

        if couple:
            first_names = _split_couple_firstnames(name_str)
            if len(first_names) < 2:
                couple = False

        # Handle Couples (Partnership Generation)
        if couple:
            # 1. Determine Partnership Type from Original Title
            t_clean = re.sub(r'[^a-z& ]', '', str(raw_title).lower())
            if "mr and mrs" in t_clean or "mr & mrs" in t_clean:
                ptype = "Husband and Wife"
            elif "mrs and mr" in t_clean or "mrs & mr" in t_clean:
                ptype = "Wife and Husband"
            else:
                ptype = "Partner"

            titles = _split_couple_titles(raw_title, len(first_names))
            
            # 2. Iterate and assign Partner IDs
            for idx, (fname, title) in enumerate(zip(first_names, titles), start=1):
                new_row = dict(other)
                new_row['contact_title']      = title
                new_row['contact_first_name'] = fname
                new_row['contact_surname']    = clean_surname
                new_row['contact_identifier'] = f"{str_id}_c{idx}" if str_id else pd.NA
                
                # Logic: _c1's partner is _c2 | _c2's partner is _c1 | _c3+ partner is _c1
                if idx == 1:
                    partner_idx = 2
                    partnership_id = f"{str_id}_c1_{str_id}_c2_r"
                elif idx == 2:
                    partner_idx = 1
                    partnership_id = f"{str_id}_c1_{str_id}_c2_r"
                else:
                    partner_idx = 1
                    partnership_id = f"{str_id}_c1_{str_id}_c{idx}_r"
                
                new_row['contact_partner_identifier'] = f"{str_id}_c{partner_idx}" if str_id else pd.NA
                new_row['contact_partnership_id']     = partnership_id if str_id else pd.NA
                new_row['contact_partnership_type']   = ptype
                
                expanded_rows.append(new_row)
        else:
            # Handle Singles
            single_titles = _split_couple_titles(raw_title, 1)
            clean_title   = single_titles[0] if single_titles else pd.NA

            if split_mode == "single_field" and name_str:
                clean_first = advanced_name_parser(raw_name, 'contact_first_name', split_mode)
                if pd.isna(clean_surname):
                    clean_surname = advanced_name_parser(raw_name, 'contact_surname', split_mode)
            elif split_mode == "dual_field" and name_str:
                clean_first = advanced_name_parser(raw_name, 'contact_first_name', 'dual_field')
            else:
                clean_first = pd.NA

            new_row = dict(other)
            new_row['contact_title']              = clean_title
            new_row['contact_first_name']         = clean_first
            new_row['contact_surname']            = clean_surname
            new_row['contact_identifier']         = f"{str_id}_c" if str_id else pd.NA
            new_row['contact_partner_identifier'] = pd.NA
            new_row['contact_partnership_id']     = pd.NA
            new_row['contact_partnership_type']   = pd.NA
            expanded_rows.append(new_row)

    return pd.DataFrame(expanded_rows)

# ---------------------------------------------------------------------------
# ROBUST PHONE & EMAIL FORMATTING
# ---------------------------------------------------------------------------

def clean_and_classify_phone(raw_num):
    if pd.isna(raw_num): return pd.NA, 'Invalid'
    s = str(raw_num)
    digits = re.sub(r'[^\d+]', '', s)

    if digits.startswith('+61'): digits = '0' + digits[3:]
    elif digits.startswith('61') and len(digits) >= 10: digits = '0' + digits[2:]
    elif digits.startswith('0061'): digits = '0' + digits[4:]
    
    digits = digits.replace('+', '')
    if not digits: return s, 'Invalid'

    if digits.startswith('04') and len(digits) == 10:
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}", 'Mobile'
    elif digits.startswith('4') and len(digits) == 9:
        return f"0{digits[:3]} {digits[3:6]} {digits[6:]}", 'Mobile'

    elif digits.startswith('0') and len(digits) == 10 and digits[1] in '2378':
        return f"({digits[:2]}) {digits[2:6]} {digits[6:]}", 'Landline'
    elif len(digits) == 9 and digits[0] in '2378':
        return f"(0{digits[:1]}) {digits[1:5]} {digits[5:]}", 'Landline'

    elif (digits.startswith('1300') or digits.startswith('1800')) and len(digits) == 10:
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}", 'Landline'
    elif digits.startswith('13') and len(digits) == 6:
        return f"{digits[:2]} {digits[2:4]} {digits[4:]}", 'Landline'

    return s, 'Invalid'

def process_email(val):
    if pd.isna(val): return pd.NA
    e = str(val).lower().replace(" ", "")
    e = e.rstrip('.,;')
    if not e or e in ['nan', 'none', 'n/a', 'na', 'unknown']: return pd.NA
    return e

def process_phones_row(row):
    """ Cross-column shuffler. Assigns valid numbers to appropriate Zenu fields or overflows to notes. """
    phone_cols = [c for c in row.index if any(x in c.lower() for x in ['mobile', 'phone', 'fax', 'landline'])]
    
    unassigned_mobiles = []
    unassigned_landlines = []
    invalids = []
    
    final_slots = {c: pd.NA for c in phone_cols}
    
    # PASS 1: Extract, Classify, and Try to Keep in Original Matching Slot
    for col in phone_cols:
        val = row[col]
        if pd.isna(val) or str(val).lower() in ["", "nan", "none", "n/a", "na", "unknown"]: 
            continue
            
        cleaned, ptype = clean_and_classify_phone(val)
        
        if ptype == 'Invalid':
            invalids.append(str(val))
        elif ptype == 'Mobile':
            if 'mobile' in col.lower() and pd.isna(final_slots[col]):
                final_slots[col] = cleaned
            else:
                unassigned_mobiles.append(cleaned)
        elif ptype == 'Landline':
            if 'mobile' not in col.lower() and pd.isna(final_slots[col]):
                final_slots[col] = cleaned
            else:
                unassigned_landlines.append(cleaned)

    # PASS 2: Cross-Assign Any Unassigned Numbers to Empty Slots of the Correct Type
    for col in phone_cols:
        if pd.isna(final_slots[col]):
            if 'mobile' in col.lower() and unassigned_mobiles:
                final_slots[col] = unassigned_mobiles.pop(0)
            elif 'mobile' not in col.lower() and unassigned_landlines:
                final_slots[col] = unassigned_landlines.pop(0)

    # Update the row with cleanly shifted numbers
    for col in phone_cols:
        row[col] = final_slots[col]

    # PASS 3: Handle Overflow (Extras and Invalids go to contact_notes)
    notes_to_add = []
    for m in unassigned_mobiles: notes_to_add.append(f"Extra Mobile: {m}")
    for l in unassigned_landlines: notes_to_add.append(f"Extra Landline: {l}")
    for i in invalids: notes_to_add.append(f"Invalid Phone Data: {i}")

    if notes_to_add:
        cid = str(row.get('contact_identifier', ''))
        # Protect _c2, _c3, etc. splits from getting duplicate invalid notes
        if not re.search(r'_c[2-9]\d*$', cid):
            existing_notes = row.get('contact_notes', pd.NA)
            append_str = " | ".join(notes_to_add)
            
            if pd.isna(existing_notes) or str(existing_notes).strip() == "":
                row['contact_notes'] = append_str
            else:
                row['contact_notes'] = str(existing_notes) + "\n" + append_str

    return row

# ---------------------------------------------------------------------------
# GeneralProcessor
# ---------------------------------------------------------------------------

class GeneralProcessor:
    def __init__(self, engine):
        self.engine   = engine
        self.conn     = engine.conn
        self.job_id   = engine.job_id
        self.workspace = engine.workspace

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_base_file(self, rules):
        source_files = []
        for rule in rules:
            for src in rule.get("sources", []):
                fn = src.get("file")
                if fn and fn != "Custom_Dummy_Fields":
                    source_files.append(fn)
        if not source_files:
            return None
        return Counter(source_files).most_common(1)[0][0]

    def _load_table(self, table_name) -> pd.DataFrame | None:
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';"
        )
        if not cursor.fetchone():
            self.engine.log(f"[{self.job_id}] CRITICAL: '{table_name}' table missing in DB.")
            return None
        return pd.read_sql_query(f'SELECT * FROM "{table_name}"', self.conn)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process_group(self, group_name: str, rules: list):
        self.engine.log(
            f"[{self.job_id}] Executing General Data Cleaning for '{group_name}'..."
        )

        # ── 1. Identify base file ──────────────────────────────────────
        base_file = self._find_base_file(rules)
        if not base_file:
            self.engine.log(
                f"[{self.job_id}] Skipping {group_name}: No valid source files found."
            )
            return

        # ── 2. Load raw data ──────────────────────────────────────────
        try:
            df = self._load_table(base_file)
            if df is None:
                return
        except Exception as e:
            self.engine.log(f"[{self.job_id}] Error loading {base_file}: {e}")
            return

        # ── 3. Identify name-related rules so we can handle them together ──
        name_rules = {}   
        other_rules = []

        _NAME_TARGETS = {
            'contact_title', 'contact_first_name', 'contact_surname',
            'contact_identifier', 'company_name',
            'contact_partner_identifier', 'contact_partnership_id', 'contact_partnership_type'
        }

        for rule in rules:
            tf = rule.get("targetField", "")
            if tf in _NAME_TARGETS:
                name_rules[tf] = rule
            else:
                other_rules.append(rule)

        # ── 4. Build zenu_output for non-name fields first ────────────
        zenu_output = pd.DataFrame(index=df.index)

        for rule in other_rules:
            zenu_output = self._apply_rule(rule, df, zenu_output)

        # ── 5. Couple-aware name expansion ────────────────────────────
        if name_rules:
            def _src_field(tf):
                r = name_rules.get(tf)
                if not r:
                    return None
                srcs = r.get("sources", [])
                return srcs[0].get("field") if srcs else None

            first_rule  = name_rules.get('contact_first_name', {})
            split_mode  = first_rule.get("splitRule", "single_field")

            name_col    = _src_field('contact_first_name')
            surname_col = _src_field('contact_surname')
            id_col      = _src_field('contact_identifier')
            title_col   = _src_field('contact_title')

            self.engine.log(
                f"[{self.job_id}] Running couple-expansion "
                f"(name='{name_col}', surname='{surname_col}', id='{id_col}', "
                f"title='{title_col}', mode='{split_mode}')..."
            )

            expanded = expand_couples_in_df(
                df, name_col, surname_col, id_col, title_col, split_mode
            )

            name_out_cols = [
                c for c in (
                    'contact_title', 'contact_first_name', 'contact_surname',
                    'contact_identifier', 'company_name',
                    'contact_partner_identifier', 'contact_partnership_id', 'contact_partnership_type'
                )
                if c in expanded.columns
            ]

            zenu_expanded = pd.DataFrame(index=expanded.index)
            for rule in other_rules:
                zenu_expanded = self._apply_rule(rule, expanded, zenu_expanded)

            for col in name_out_cols:
                zenu_expanded[col] = expanded[col].values

            zenu_output = zenu_expanded
            
        # ── 6. SMART POST-PROCESSING (EMAILS & PHONES) ──────────────
        for col in zenu_output.columns:
            if 'email' in col.lower():
                zenu_output[col] = zenu_output[col].apply(process_email)

        # If any phone columns exist, prep for notes and apply the cross-shuffler
        if any(any(x in c.lower() for x in ['mobile', 'phone', 'landline', 'fax']) for c in zenu_output.columns):
            if 'contact_notes' not in zenu_output.columns:
                zenu_output['contact_notes'] = pd.NA
                
            zenu_output = zenu_output.apply(process_phones_row, axis=1)

        # ── 7. Global sanitisation ────────────────────────────────────
        def _clean(val):
            if pd.isna(val):
                return val
            t = str(val).strip()
            if t.lower() in ('nan', 'none', 'null', ''):
                return pd.NA
            return re.sub(r' +', ' ', t)

        for col in zenu_output.columns:
            zenu_output[col] = zenu_output[col].apply(_clean)

        zenu_output = zenu_output.dropna(how='all').reset_index(drop=True)

        self.engine.log(
            f"[{self.job_id}] Expansion complete: {len(df)} source rows → "
            f"{len(zenu_output)} output rows."
        )

        # ── 8. Export ─────────────────────────────────────────────────
        self._export(group_name, zenu_output)

    # ------------------------------------------------------------------
    # Rule application 
    # ------------------------------------------------------------------

    def _apply_rule(
        self,
        rule: dict,
        df: pd.DataFrame,
        zenu_output: pd.DataFrame,
    ) -> pd.DataFrame:
        target_field     = rule.get("targetField")
        action           = rule.get("action")
        sources          = rule.get("sources", [])
        split_rule       = rule.get("splitRule", "single_field")
        primary_src_field = sources[0].get("field") if sources else None

        # DIRECT
        if action == "direct":
            if primary_src_field and primary_src_field in df.columns:
                if target_field in ('contact_first_name', 'contact_surname', 'company_name'):
                    zenu_output[target_field] = df[primary_src_field].apply(
                        lambda x: advanced_name_parser(x, target_field, split_rule)
                    )
                else:
                    zenu_output[target_field] = df[primary_src_field].values

        # STATIC
        elif action == "static":
            zenu_output[target_field] = rule.get("valueExpression", "")

        # CONCAT
        elif action == "concat":
            expression = str(rule.get("valueExpression", ""))
            if expression:
                def eval_concat(row):
                    res = expression
                    for src in sources:
                        var_id = f"[{src.get('varId', 'S1')}]"
                        val    = str(row.get(src.get('field'), ''))
                        if val.lower() in ('nan', 'none'):
                            val = ''
                        res = res.replace(var_id, val)
                    return res.strip()
                zenu_output[target_field] = df.apply(eval_concat, axis=1)
            elif primary_src_field and primary_src_field in df.columns:
                zenu_output[target_field] = df[primary_src_field].values

        # LOOKUP
        elif action == "lookup":
            for config in rule.get('lookupConfig', []):
                target_file    = config.get('targetFile')
                match_key      = config.get('matchKey')
                extract_fields = config.get('extractFields', [])

                if target_file and match_key and extract_fields \
                        and primary_src_field and primary_src_field in df.columns:
                    try:
                        extract_col = extract_fields[0]
                        lookup_df = pd.read_sql_query(
                            f'SELECT "{match_key}", "{extract_col}" FROM "{target_file}"',
                            self.conn,
                        )
                        lookup_df[match_key] = (
                            lookup_df[match_key].astype(str).str.replace(r'\.0$', '', regex=True)
                        )
                        source_keys = (
                            df[primary_src_field].astype(str).str.replace(r'\.0$', '', regex=True)
                        )
                        mapping_dict = (
                            lookup_df.drop_duplicates(subset=[match_key])
                            .set_index(match_key)[extract_col]
                            .to_dict()
                        )
                        zenu_output[target_field] = source_keys.map(mapping_dict).values
                    except Exception as e:
                        self.engine.log(
                            f"[{self.job_id}] Lookup warning for {target_field}: {e}"
                        )

        return zenu_output

    # ------------------------------------------------------------------
    # Export helper
    # ------------------------------------------------------------------

    def _export(self, group_name: str, df: pd.DataFrame):
        safe_name   = group_name.replace(" ", "_").replace("/", "_")
        chunk_limit = self.engine.chunk_size
        total_rows  = len(df)

        if chunk_limit > 0 and total_rows > chunk_limit:
            self.engine.log(
                f"[{self.job_id}] Splitting into chunks of {chunk_limit}..."
            )
            num_chunks = (total_rows // chunk_limit) + (
                1 if total_rows % chunk_limit else 0
            )
            for i in range(num_chunks):
                chunk = df.iloc[i * chunk_limit : (i + 1) * chunk_limit]
                if not chunk.empty:
                    path = os.path.join(self.workspace, f"Cleaned_{safe_name}_pt{i+1}.csv")
                    chunk.to_csv(path, index=False)
                    self.engine.log(
                        f"[{self.job_id}] SUCCESS: Created {path} ({len(chunk)} rows)"
                    )
        else:
            path = os.path.join(self.workspace, f"Cleaned_{safe_name}.csv")
            df.to_csv(path, index=False)
            self.engine.log(
                f"[{self.job_id}] SUCCESS: Exported -> {path} ({total_rows} rows)"
            )