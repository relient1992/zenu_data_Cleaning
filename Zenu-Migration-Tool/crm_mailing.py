import pandas as pd
import os
import re
from crm_general import GeneralProcessor

class MailingProcessor(GeneralProcessor):
    def __init__(self, engine):
        super().__init__(engine)

    def process_group(self, group_name, rules):
        self.engine.log(f"[{self.job_id}] Executing Mailing Address -> Prospect & Ownership Logic...")
        
        # 1. Run the standard JSON mapping processing first
        # This generates a temporary CSV via GeneralProcessor
        super().process_group(group_name, rules)
        
        safe_group_name = group_name.replace(" ", "_").replace("/", "_")
        base_output_path = os.path.join(self.workspace, f"Cleaned_{safe_group_name}.csv")
        
        if os.path.exists(base_output_path):
            try:
                # Read the base output for further manipulation
                df = pd.read_csv(base_output_path)
                
                # ---------------------------------------------------------
                # 2. DATA CLEANING
                # Remove non-meaningful values and fix pandas float formatting
                # ---------------------------------------------------------
                def clean_junk(val):
                    if pd.isna(val) or val is None: return ""
                    v_str = str(val)
                    if v_str.upper() in ['N/A', 'NA', 'UNKNOWN', 'NAN', 'NONE']: 
                        return ''
                    
                    # Fix for pandas float conversion adding .0 to numeric fields like postcodes
                    if v_str.endswith('.0'):
                        v_str = v_str[:-2]
                        
                    return v_str
                
                for col in df.columns:
                    df[col] = df[col].apply(clean_junk)
                    
                # ---------------------------------------------------------
                # 3. IDENTIFIER FORMATTING
                # Append the 'Mailing_to_add_' prefix to the raw contact ID
                # ---------------------------------------------------------
                if 'property_identifier' in df.columns:
                    df['property_identifier'] = df['property_identifier'].apply(
                        lambda x: f"Mailing_to_add_{x}" if pd.notna(x) and str(x) != '' else pd.NA
                    )
                    
                # ---------------------------------------------------------
                # 4. GRANULAR ADDRESS SPLITTING (6-Column Format)
                # Parse address_line1 into Unit, Street Number, and Name
                # ---------------------------------------------------------
                if 'property_street_name' in df.columns:
                    def parse_address(addr):
                        if pd.isna(addr) or addr == '': return "", "", ""
                        
                        s = str(addr) # Purposely avoiding .strip() to preserve formatting
                        
                        # Match Unit X / Y Street
                        m1 = re.match(r'^(?:unit|apt|suite|flat)?\s*([a-z0-9]+)\s*/\s*([0-9a-z\-]+)\s+(.*)', s, re.IGNORECASE)
                        if m1: 
                            return m1.group(1), m1.group(2), m1.group(3)
                        
                        # Match X Street
                        m2 = re.match(r'^([0-9a-z\-]+)\s+(.*)', s, re.IGNORECASE)
                        if m2 and re.match(r'^\d', m2.group(1)):
                            return "", m2.group(1), m2.group(2)
                            
                        # Fallback to pure street name
                        return "", "", s

                    parsed = df['property_street_name'].apply(parse_address)
                    df['property_unit_number'] = parsed.apply(lambda x: x[0])
                    df['property_street_number'] = parsed.apply(lambda x: x[1])
                    df['property_street_name'] = parsed.apply(lambda x: x[2])

                # ---------------------------------------------------------
                # 5. DYNAMIC FULL ADDRESS CONCATENATION
                # ---------------------------------------------------------
                def build_full_address(row):
                    unit = str(row.get('property_unit_number', ''))
                    st_num = str(row.get('property_street_number', ''))
                    st_name = str(row.get('property_street_name', ''))
                    suburb = str(row.get('property_suburb', ''))
                    state = str(row.get('property_state', ''))
                    postcode = str(row.get('property_postcode', ''))
                    
                    street_part = ""
                    if unit and st_num: street_part = f"{unit}/{st_num}"
                    elif unit: street_part = unit
                    elif st_num: street_part = st_num
                        
                    addr1 = f"{street_part} {st_name}"
                    if addr1 == " ": addr1 = "" 
                    
                    state_pc = f"{state} {postcode}"
                    if state_pc == " ": state_pc = ""

                    parts = [p for p in [addr1, suburb, state_pc] if p and p.replace(" ", "") != ""]
                    return ", ".join(parts)

                df['property_full_address'] = df.apply(build_full_address, axis=1)
                
                # ---------------------------------------------------------
                # Export the updated Prospect file as XLSX
                # ---------------------------------------------------------
                final_prospect_path = os.path.join(self.workspace, f"Zenu_{safe_group_name}_Final.xlsx")
                df.to_excel(final_prospect_path, index=False, engine='openpyxl')
                self.engine.log(f"[{self.job_id}] SUCCESS: Updated Mailing Prospects -> {final_prospect_path}")
                
                # ---------------------------------------------------------
                # 6. EXTRACT OWNERSHIP RELATIONSHIPS
                # ---------------------------------------------------------
                self.engine.log(f"[{self.job_id}] Generating Ownership Relationships...")
                
                if 'property_identifier' in df.columns:
                    own_df = pd.DataFrame()
                    own_df['property_identifier'] = df['property_identifier']
                    
                    # Reverse-engineer the contact_id from the property_identifier
                    own_df['contact_identifier'] = df['property_identifier'].apply(
                        lambda x: str(x).replace("Mailing_to_add_", "") if pd.notna(x) else pd.NA
                    )
                    own_df['contact_sale_type'] = "Seller" 
                    
                    # Clean up and export as XLSX
                    own_df = own_df.dropna(subset=['property_identifier', 'contact_identifier'])
                    own_df = own_df.drop_duplicates()
                    
                    own_path = os.path.join(self.workspace, f"Ownership_Relations_{safe_group_name}.xlsx")
                    own_df.to_excel(own_path, index=False, engine='openpyxl')
                    self.engine.log(f"[{self.job_id}] SUCCESS: Exported Ownerships -> {own_path}")

                # Clean up the intermediate base CSV
                try:
                    os.remove(base_output_path)
                except:
                    pass

            except Exception as e:
                self.engine.log(f"[{self.job_id}] Error generating Prospect/Ownership relations: {e}")