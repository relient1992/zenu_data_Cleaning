/* DARVIS JSON Import.
 * Turns the Zenu CSV import (the 171-column template that Import Prep writes) into the proposed JSON import
 * (import.schema.json, schema_version 1) for one office: each row is split into contact, listing, task, enquiry,
 * inspection and offer records, rows with the same identifier are merged, and the records are linked by id
 * (a Zenu id for an existing record, a projected id such as projected_contact_1 for one the file creates).
 * Rules: import.csv-mapping.md and import.mapping.md from the import-schema proposal.
 * Separate from engine.js on purpose: nothing in the existing DARVIS tools uses it. */
(function (root) {
  "use strict";

  /** [CSV column, JSON key, record, status, guide note, how DARVIS handles a gap] in template order. */
  const MAP = [
    ["contact_identifier", "contact.identifier", "Contact", "Mapped", "", ""],
    ["zenu_contact_id", "contact.contact_id", "Contact", "Link", "Record goes in contacts.update.", ""],
    ["contact_title", "contact.title", "Contact", "Mapped", "", "Mr, Mrs, Miss, Ms or Dr. Other values are left out and listed."],
    ["contact_first_name", "contact.first_name", "Contact", "Mapped", "", ""],
    ["contact_surname", "contact.last_name", "Contact", "Mapped", "", ""],
    ["contact_is_company", "contact.is_company", "Contact", "Mapped", "If the row also has a first or last name, write two contacts: the company, and the person with company_contact_id set to the company's id.", "One company contact per company name in the file; its identifier is the first row's contact_identifier + \"_company\". The company gets is_company, company_name and the primary staff member; the person keeps the rest."],
    ["contact_company_name", "contact.company_name", "Contact", "Mapped", "On the company contact.", ""],
    ["contact_name_on_letters", "contact.name_on_letters", "Contact", "Mapped", "", ""],
    ["contact_legal_name", "contact.legal_name", "Contact", "Mapped", "", ""],
    ["contact_company_position", "contact.company_position", "Contact", "Mapped", "On the person, not the company.", ""],
    ["contact_type", "contact.types[]", "Contact", "Mapped", "", ""],
    ["contact_tags", "contact.tags[]", "Contact", "Mapped", "", ""],
    ["contact_subscriptions", "contact.subscription_ids[]", "Contact", "Mapped", "", ""],
    ["contact_mailing_address_line1", "contact.address_line_1", "Contact", "Mapped", "", ""],
    ["contact_mailing_address_line2", "contact.address_line_2", "Contact", "Mapped", "", ""],
    ["contact_mailing_address_suburb", "contact.suburb", "Contact", "Mapped", "", ""],
    ["contact_mailing_address_town_city", "contact.town_city", "Contact", "Mapped", "", ""],
    ["contact_mailing_address_state", "contact.state", "Contact", "Mapped", "", ""],
    ["contact_mailing_address_country", "contact.country", "Contact", "Mapped", "", ""],
    ["contact_mailing_address_postcode", "contact.post_code", "Contact", "Mapped", "", ""],
    ["contact_email_address", "contact.email, contact.extra_emails[]", "Contact", "Mapped", "First email in email, the rest in extra_emails.", ""],
    ["contact_mobile", "contact.mobile_phone, contact.extra_phones[]", "Contact", "Mapped", "First number in mobile_phone, the rest as { \"label\": \"Mobile\", \"number\": … }.", ""],
    ["contact_phone_work", "contact.work_phone, contact.extra_phones[]", "Contact", "Mapped", "As above, label Work.", ""],
    ["contact_phone_home", "contact.home_phone, contact.extra_phones[]", "Contact", "Mapped", "As above, label Home.", ""],
    ["contact_fax", "contact.fax, contact.extra_phones[]", "Contact", "Mapped", "As above, label Fax.", ""],
    ["contact_allow_contact_via_text", "contact.allows_text", "Contact", "Mapped", "", ""],
    ["contact_allow_contact_via_email", "contact.allows_email", "Contact", "Mapped", "", ""],
    ["contact_allow_contact_via_letter", "contact.allows_letter", "Contact", "Mapped", "", ""],
    ["contact_allow_contact_via_phone", "contact.allows_phone", "Contact", "Mapped", "", ""],
    ["contact_unsubscribe_from_marketing", "contact.is_unsubscribed", "Contact", "Mapped", "", ""],
    ["contact_childrens_names", "", "Contact", "No longer imported", "No longer imported. Leave out.", ""],
    ["contact_enquiry_source", "contact.source", "Contact", "Mapped", "", ""],
    ["contact_rating", "contact.rating", "Contact", "Mapped", "", "Hot, Warm or Cold. Other values are left out and listed."],
    ["contact_partner_identifier", "contact.relationships[].contact_id", "Contact", "Link", "The id of the contact with this identifier. Add it to one of the two contacts only; the other direction is added for you.", "Links to the contact with that contact_identifier in the loaded files. Not found: the link is left out and listed."],
    ["contact_partnership_id", "contact.relationships[].contact_id", "Contact", "Link", "Rows with the same value are partners. Used when contact_partner_identifier is empty.", "Every contact in the group is linked to the first one, once."],
    ["contact_partnership_type", "contact.relationships[].type", "Contact", "Mapped", "", "\"Partner\" when empty (unknown types become Other in Zenu)."],
    ["contact_last_contact_date", "contact.last_contacted_date", "Contact", "Mapped", "", ""],
    ["contact_date_created", "contact.created_date", "Contact", "Mapped", "", ""],
    ["contact_date_modified", "contact.modified_date", "Contact", "Mapped", "Unless it is update. See [How values change](#how-values-change).", "update = full update of an existing contact (zenu_contact_id). Anything else is the modified date."],
    ["contact_criteria_sale_method", "criteria[].sale_method", "Criteria", "Mapped", "", ""],
    ["contact_criteria_category", "criteria[].category", "Criteria", "Mapped", "", ""],
    ["contact_criteria_property_type", "criteria[].property_types[]", "Criteria", "Mapped", "", ""],
    ["contact_criteria_title_type", "criteria[].title_types[]", "Criteria", "Mapped", "", ""],
    ["contact_criteria_price_from", "criteria[].min_price", "Criteria", "Mapped", "", ""],
    ["contact_criteria_price_to", "criteria[].max_price", "Criteria", "Mapped", "", ""],
    ["contact_criteria_bedrooms", "criteria[].min_bedrooms", "Criteria", "Mapped", "", ""],
    ["contact_criteria_bathrooms", "criteria[].min_bathrooms", "Criteria", "Mapped", "", ""],
    ["contact_criteria_carspaces", "criteria[].min_car_spaces", "Criteria", "Mapped", "", ""],
    ["contact_criteria_land_from", "criteria[].min_land_size", "Criteria", "Mapped", "", ""],
    ["contact_criteria_land_to", "criteria[].max_land_size", "Criteria", "Mapped", "", ""],
    ["contact_criteria_land_unit", "criteria[].land_size_unit", "Criteria", "Mapped", "sqm, acre, hectare or square.", ""],
    ["contact_criteria_living_area_from", "criteria[].min_living_area", "Criteria", "Mapped", "", ""],
    ["contact_criteria_living_area_to", "criteria[].max_living_area", "Criteria", "Mapped", "", ""],
    ["contact_criteria_living_area_unit", "criteria[].living_area_unit", "Criteria", "Mapped", "sqm or square.", ""],
    ["contact_criteria_suburbs", "criteria[].suburbs[]", "Criteria", "Mapped", "Names only. Swap any post code for its suburb names.", "Post codes swapped for their suburb names from the AU suburb list. At most 10 suburbs per criteria."],
    ["contact_team_member_1", "contact.primary_staff_name", "Contact", "Mapped", "", ""],
    ["contact_team_member_2", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_3", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_4", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_5", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_6", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_7", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_8", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_9", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_10", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_11", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_12", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_13", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_14", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_team_member_15", "contact.secondary_staff_names[]", "Contact", "Mapped", "In order.", ""],
    ["contact_sale_type", "listing.vendor_contact_ids[] or listing.contract.buyer_contact_ids[]", "Contact", "Link", "seller adds the contact to the listing's vendor_contact_ids. purchaser adds it to the contract's buyer_contact_ids.", "seller / vendor / owner / landlord = vendor. purchaser / buyer / tenant = buyer. The row's person is linked, not the company."],
    ["zenu_solicitor_id", "listing.vendor_solicitor_contact_id or listing.contract.buyer_solicitor_contact_id", "Contact", "Link", "Vendor's solicitor when contact_sale_type is seller, buyer's when purchaser.", "New listings only. An update can't set a solicitor. A contact_identifier in the file (not a number) links to that contact."],
    ["zenu_company_id", "contact.company_contact_id", "Contact", "Link", "", "Used instead of creating a company contact. A contact_identifier in the file (not a number, as contact_cleaned.csv writes) links to that contact."],
    ["contact_notes", "contact.notes[].text", "Contact", "Mapped", "One note per \\", ""],
    ["contact_note_created_date", "contact.notes[].created_date", "Contact", "Mapped", "Same for every note from the row.", ""],
    ["contact_note_team_member", "contact.notes[].staff_name", "Contact", "Mapped", "Same for every note from the row.", ""],
    ["property_identifier", "listing.identifier", "Listing", "Mapped", "", ""],
    ["zenu_property_id", "listing.listing_id", "Listing", "Link", "Record goes in listings.update.", ""],
    ["property_unit_number", "listing.unit_number", "Listing", "Mapped", "", ""],
    ["property_street_number", "listing.street_number", "Listing", "Mapped", "", ""],
    ["property_street_name", "listing.street_name", "Listing", "Mapped", "", ""],
    ["property_suburb", "listing.suburb", "Listing", "Mapped", "", ""],
    ["property_postcode", "listing.post_code", "Listing", "Mapped", "", ""],
    ["property_state", "listing.state", "Listing", "Mapped", "", ""],
    ["property_country", "listing.country", "Listing", "Mapped", "", ""],
    ["property_full_address", "", "Listing", "Split first", "No longer accepted. Split it into unit_number, street_number, street_name, suburb, state and post_code.", "Only used when property_street_name is empty: DARVIS splits it with the AU suburb list."],
    ["property_building_name", "listing.building_name", "Listing", "Mapped", "", ""],
    ["property_sale_method", "listing.sale_method", "Listing", "Mapped", "", ""],
    ["property_category", "listing.category", "Listing", "Mapped", "", ""],
    ["property_type", "listing.property_type", "Listing", "Mapped", "", ""],
    ["property_bedrooms", "listing.bedrooms", "Listing", "Mapped", "", ""],
    ["property_bathrooms", "listing.bathrooms", "Listing", "Mapped", "", ""],
    ["property_toilets", "listing.toilets", "Listing", "Mapped", "", ""],
    ["property_total_rooms", "listing.total_rooms", "Listing", "Mapped", "", ""],
    ["property_garages", "listing.garages", "Listing", "Mapped", "", ""],
    ["property_carports", "listing.carports", "Listing", "Mapped", "", ""],
    ["property_open_parking_spaces", "listing.open_parking_spaces", "Listing", "Mapped", "", ""],
    ["property_living_area_m2", "listing.living_area", "Listing", "Mapped", "", ""],
    ["property_land_size_m2", "listing.land_size", "Listing", "Mapped", "", ""],
    ["listing_land_size_system", "listing.land_size_unit", "Listing", "Mapped", "", ""],
    ["property_modified_date", "listing.modified_date", "Listing", "Mapped", "", ""],
    ["property_appraisal_date", "listing.appraisal.date", "Listing", "Mapped", "", ""],
    ["property_timeline_status", "listing.status", "Listing", "Mapped", "Prospect values become Prospect; appraisal and listing presentation values become Appraisal. If empty: Sold or Settled when there are contract dates, otherwise Prospect.", "Anything else (Listing, Withdrawn and so on) is not a JSON status: the empty-status rule is used and the count is listed."],
    ["property_team_member_1", "listing.lister_staff_names[]", "Listing", "Mapped", "In order. property_team_member_1 is also listing.appraisal.staff_name.", ""],
    ["property_team_member_2", "listing.lister_staff_names[]", "Listing", "Mapped", "In order. property_team_member_1 is also listing.appraisal.staff_name.", ""],
    ["property_team_member_3", "listing.lister_staff_names[]", "Listing", "Mapped", "In order. property_team_member_1 is also listing.appraisal.staff_name.", ""],
    ["property_team_member_4", "listing.lister_staff_names[]", "Listing", "Mapped", "In order. property_team_member_1 is also listing.appraisal.staff_name.", ""],
    ["property_is_occupied_by_owner", "listing.is_owner_occupied", "Listing", "Mapped", "", ""],
    ["property_appraisal_source", "listing.appraisal.source", "Listing", "Mapped", "", ""],
    ["property_is_selling_within_7_days", "", "Listing", "No longer imported", "No longer imported. Leave out.", ""],
    ["property_search_price", "listing.search_price", "Listing", "Mapped", "", ""],
    ["property_vendor_price", "listing.appraisal.price", "Listing", "Mapped", "", ""],
    ["property_estimated_commission", "", "Listing", "No longer imported", "No longer imported. Leave out.", ""],
    ["property_pipeline_rating", "listing.appraisal.motivation, listing.prospect.pipeline_rating", "Listing", "Mapped", "appraisal.motivation when status is Appraisal, prospect.pipeline_rating when it is Prospect.", "Dropped when the status is Sold or Settled."],
    ["property_contract_date", "listing.contract.contract_date", "Listing", "Mapped", "", ""],
    ["property_unconditional_date", "listing.contract.unconditional_date", "Listing", "Mapped", "", ""],
    ["property_settlement_date", "listing.contract.settlement_date", "Listing", "Mapped", "", ""],
    ["property_sold_price", "listing.contract.sold_price", "Listing", "Mapped", "", ""],
    ["property_sale_team_member", "listing.contract.staff[].staff_name", "Listing", "Mapped", "", ""],
    ["property_sale_team_member_role", "listing.contract.staff[].role", "Listing", "Mapped", "Lister or Seller.", "Seller when empty."],
    ["property_sale_team_member_recognition_percentage", "listing.contract.staff[].share_percentage", "Listing", "Mapped", "", ""],
    ["property_sale_team_member_recognition_amount", "listing.contract.staff[].share_amount", "Listing", "Mapped", "", ""],
    ["property_sale_gross_commission", "listing.contract.gross_commission", "Listing", "Mapped", "", ""],
    ["property_year_built", "listing.year_built", "Listing", "Mapped", "", ""],
    ["property_council_name", "listing.council_name", "Listing", "Mapped", "", ""],
    ["property_council_zoning", "listing.council_zoning", "Listing", "Mapped", "", ""],
    ["property_lot_number", "listing.lot_number", "Listing", "Mapped", "", ""],
    ["property_title_number", "listing.title_number", "Listing", "Mapped", "", ""],
    ["property_rent_per_week", "listing.weekly_rent", "Listing", "Mapped", "", ""],
    ["property_last_listed_price", "listing.prospect.last_listed_price", "Listing", "Mapped", "", ""],
    ["property_current_price", "listing.prospect.current_price", "Listing", "Mapped", "", ""],
    ["property_last_listed_date", "listing.prospect.last_listed_date", "Listing", "Mapped", "", ""],
    ["property_last_contract_date", "listing.prospect.last_contract_date", "Listing", "Mapped", "", ""],
    ["property_last_sold_date", "listing.prospect.last_sold_date", "Listing", "Mapped", "", ""],
    ["property_last_sold_price", "listing.prospect.last_sold_price", "Listing", "Mapped", "", ""],
    ["property_last_listed_by_agency", "listing.prospect.last_listed_agency", "Listing", "Mapped", "", ""],
    ["property_last_listed_by_agent", "listing.prospect.last_listed_agent", "Listing", "Mapped", "", ""],
    ["property_last_leased_agency", "listing.prospect.last_leased_agency", "Listing", "Mapped", "", ""],
    ["property_last_rent_pw", "", "Listing", "No longer imported", "No longer imported. Leave out.", ""],
    ["property_notes", "listing.notes[].text", "Listing", "Mapped", "One note per \\", ""],
    ["property_note_created_date", "listing.notes[].created_date", "Listing", "Mapped", "Same for every note from the row.", ""],
    ["property_note_team_member", "listing.notes[].staff_name", "Listing", "Mapped", "Same for every note from the row.", ""],
    ["task_identifier", "task.identifier", "Task", "Mapped", "", ""],
    ["zenu_task_id", "", "Task", "Not imported: record exists", "Already exists. Leave the task out.", ""],
    ["task_subject", "task.subject", "Task", "Mapped", "", ""],
    ["task_notes", "task.notes", "Task", "Mapped", "", ""],
    ["task_status", "task.status", "Task", "Mapped", "Active, Completed or Cancelled.", ""],
    ["task_team_member_1", "task.staff_name", "Task", "Mapped", "", ""],
    ["task_date_due", "task.due_date", "Task", "Mapped", "", ""],
    ["enquiry_identifier", "enquiry.identifier", "Enquiry", "Mapped", "", ""],
    ["zenu_enquiry_id", "", "Enquiry", "Not imported: record exists", "Already exists. Leave the enquiry out.", ""],
    ["enquiry_notes", "enquiry.notes", "Enquiry", "Mapped", "", ""],
    ["enquiry_status", "enquiry.status", "Enquiry", "Mapped", "Active or Completed.", ""],
    ["enquiry_source", "enquiry.source", "Enquiry", "Mapped", "", ""],
    ["enquiry_team_member_1", "enquiry.staff_name", "Enquiry", "Mapped", "", ""],
    ["enquiry_date_created", "enquiry.created_date", "Enquiry", "Mapped", "", ""],
    ["inspection_identifier", "inspection.identifier", "Inspection", "Mapped", "", ""],
    ["zenu_inspection_id", "", "Inspection", "Not imported: record exists", "Already exists. Leave the inspection out.", ""],
    ["inspection_notes", "inspection.notes", "Inspection", "Mapped", "", ""],
    ["inspection_start_date", "inspection.start_date", "Inspection", "Mapped", "", ""],
    ["inspection_end_date", "inspection.end_date", "Inspection", "Mapped", "", ""],
    ["inspection_is_private", "inspection.is_private", "Inspection", "Mapped", "", ""],
    ["inspection_team_member_1", "inspection.staff_name", "Inspection", "Mapped", "", ""],
    ["inspection_is_interested", "inspection.interest", "Inspection", "Mapped", "Yes, No or Maybe.", ""],
    ["inspection_feedback_price", "inspection.feedback_price", "Inspection", "Mapped", "", ""],
    ["offer_identifier", "offer.identifier", "Offer", "Mapped", "", ""],
    ["zenu_offer_id", "", "Offer", "Not imported: record exists", "Already exists. Leave the offer out.", ""],
    ["offer_price", "offer.price", "Offer", "Mapped", "", ""],
    ["offer_terms", "offer.terms", "Offer", "Mapped", "", ""],
    ["offer_date", "offer.created_date", "Offer", "Mapped", "", ""],
    ["offer_team_member_1", "offer.staff_name", "Offer", "Mapped", "", ""],
  ];
  const SCHEMA = {"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"https://zenu.com.au/schemas/import.schema.json","title":"Zenu data import","description":"An import file for one office. \"create\" adds new records; \"update\" changes existing ones. Each record has an id: a Zenu id (a number) for an existing record, or a projected id (such as \"projected_contact_1\") for one this file creates. Other records can use a projected id; it is swapped for the real id once the record is added. Each record also has an identifier: its id in the system it came from. In an update, only updatable keys are allowed, a missing key is left alone and null clears the value. Lists (notes, tags, vendors and so on) only ever add. Staff are given by name, as \"First Last\". See import.mapping.md for where each key is saved.","type":"object","required":["schema_version","office_id"],"additionalProperties":false,"properties":{"$schema":{"type":"string"},"schema_version":{"const":1},"office_id":{"$ref":"#/$defs/zenu_id"},"contacts":{"type":"object","additionalProperties":false,"properties":{"create":{"type":"array","items":{"$ref":"#/$defs/contact_create"}},"update":{"type":"array","items":{"$ref":"#/$defs/contact_update"}}}},"listings":{"type":"object","additionalProperties":false,"properties":{"create":{"type":"array","items":{"$ref":"#/$defs/listing_create"}},"update":{"type":"array","items":{"$ref":"#/$defs/listing_update"}}}},"tasks":{"description":"Create only. Existing tasks cannot be updated.","type":"object","additionalProperties":false,"properties":{"create":{"type":"array","items":{"$ref":"#/$defs/task"}}}},"enquiries":{"description":"Create only. Existing enquiries cannot be updated.","type":"object","additionalProperties":false,"properties":{"create":{"type":"array","items":{"$ref":"#/$defs/enquiry"}}}},"inspections":{"description":"Create only. Existing inspections cannot be updated.","type":"object","additionalProperties":false,"properties":{"create":{"type":"array","items":{"$ref":"#/$defs/inspection"}}}},"offers":{"description":"Create only. Existing offers cannot be updated.","type":"object","additionalProperties":false,"properties":{"create":{"type":"array","items":{"$ref":"#/$defs/offer"}}}}},"$defs":{"zenu_id":{"description":"The id of an existing Zenu record.","type":"integer","minimum":1},"projected_contact_id":{"type":"string","pattern":"^projected_contact_[A-Za-z0-9_]+$"},"projected_listing_id":{"type":"string","pattern":"^projected_listing_[A-Za-z0-9_]+$"},"projected_task_id":{"type":"string","pattern":"^projected_task_[A-Za-z0-9_]+$"},"projected_enquiry_id":{"type":"string","pattern":"^projected_enquiry_[A-Za-z0-9_]+$"},"projected_inspection_id":{"type":"string","pattern":"^projected_inspection_[A-Za-z0-9_]+$"},"projected_offer_id":{"type":"string","pattern":"^projected_offer_[A-Za-z0-9_]+$"},"contact_ref":{"description":"An existing contact id or a projected contact id from this file.","oneOf":[{"$ref":"#/$defs/zenu_id"},{"$ref":"#/$defs/projected_contact_id"}]},"contact_refs":{"type":"array","uniqueItems":true,"items":{"$ref":"#/$defs/contact_ref"}},"listing_ref":{"description":"An existing listing id or a projected listing id from this file.","oneOf":[{"$ref":"#/$defs/zenu_id"},{"$ref":"#/$defs/projected_listing_id"}]},"identifier":{"description":"The record's id in the system it came from. Saved so a later import can find the same record.","type":"string","minLength":1,"maxLength":100},"staff_name":{"description":"A staff member at the office, as \"First Last\", such as \"Daryl Dixon\". Never a staff id. Must match exactly one active staff member.","type":"string","pattern":"^\\S+( \\S+)+$","not":{"pattern":"^[0-9 ]+$"}},"staff_names":{"type":"array","uniqueItems":true,"items":{"$ref":"#/$defs/staff_name"}},"date":{"type":["string","null"],"pattern":"^\\d{4}-\\d{2}-\\d{2}$"},"date_time":{"type":["string","null"],"pattern":"^\\d{4}-\\d{2}-\\d{2}(T\\d{2}:\\d{2}(:\\d{2})?)?$"},"text":{"type":["string","null"]},"money":{"type":["number","null"],"minimum":0},"count":{"type":["integer","null"],"minimum":0},"area":{"type":["number","null"],"minimum":0},"boolean":{"type":["boolean","null"]},"names":{"type":"array","uniqueItems":true,"items":{"type":"string","minLength":1}},"category":{"enum":["Residential","Commercial","Rural","Business",null]},"sale_method":{"enum":["Sale","Lease",null]},"hot_warm_cold":{"enum":["Hot","Warm","Cold",null]},"land_size_unit":{"enum":["sqm","acre","hectare","square"],"default":"sqm"},"notes":{"description":"Always added, never replaced.","type":"array","items":{"type":"object","additionalProperties":false,"required":["text"],"properties":{"text":{"type":"string","minLength":1},"created_date":{"description":"Defaults to now.","$ref":"#/$defs/date"},"staff_name":{"description":"Who wrote the note. Defaults to the contact's primary staff member or the listing's first lister.","$ref":"#/$defs/staff_name"}}}},"contact_updatable":{"description":"Contact keys that can be updated.","type":"object","properties":{"title":{"enum":["Mr","Mrs","Miss","Ms","Dr",null]},"first_name":{"type":["string","null"],"maxLength":150},"last_name":{"type":["string","null"],"maxLength":150},"is_company":{"$ref":"#/$defs/boolean"},"company_name":{"$ref":"#/$defs/text"},"company_contact_id":{"description":"The company contact this person works for.","oneOf":[{"$ref":"#/$defs/contact_ref"},{"type":"null"}]},"name_on_letters":{"$ref":"#/$defs/text"},"legal_name":{"$ref":"#/$defs/text"},"types":{"description":"Contact types, such as Buyer. Added to the contact's types.","$ref":"#/$defs/names"},"tags":{"description":"Added to the contact's tags.","$ref":"#/$defs/names"},"address_line_1":{"$ref":"#/$defs/text"},"address_line_2":{"$ref":"#/$defs/text"},"suburb":{"$ref":"#/$defs/text"},"state":{"$ref":"#/$defs/text"},"post_code":{"$ref":"#/$defs/text"},"country":{"$ref":"#/$defs/text"},"email":{"type":["string","null"],"pattern":"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"},"mobile_phone":{"$ref":"#/$defs/text"},"work_phone":{"$ref":"#/$defs/text"},"home_phone":{"$ref":"#/$defs/text"},"fax":{"$ref":"#/$defs/text"},"is_unsubscribed":{"description":"Unsubscribed from marketing emails and texts. true sets unsubscribed_date to today, unless one is given. false clears it.","$ref":"#/$defs/boolean"},"unsubscribed_date":{"$ref":"#/$defs/date"},"rating":{"$ref":"#/$defs/hot_warm_cold"},"primary_staff_name":{"description":"The contact's main staff member.","$ref":"#/$defs/staff_name"}}},"contact_additions":{"description":"Contact lists. They add to what is there, on create and update.","type":"object","properties":{"secondary_staff_names":{"type":"array","maxItems":14,"$ref":"#/$defs/staff_names"},"extra_emails":{"description":"Emails besides the main one.","type":"array","uniqueItems":true,"items":{"type":"string","pattern":"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"}},"extra_phones":{"description":"Phone numbers besides the main ones.","type":"array","items":{"type":"object","additionalProperties":false,"required":["label","number"],"properties":{"label":{"enum":["Mobile","Work","Home","Fax"]},"number":{"type":"string","minLength":1}}}},"subscription_ids":{"description":"The office's mailing lists the contact is subscribed to.","type":"array","uniqueItems":true,"items":{"$ref":"#/$defs/zenu_id"}},"relationships":{"description":"The matching relationship is also added the other way round.","type":"array","items":{"type":"object","additionalProperties":false,"required":["contact_id","type"],"properties":{"contact_id":{"$ref":"#/$defs/contact_ref"},"type":{"description":"What the other contact is to this one, such as Wife. Unknown types become Other.","type":"string","minLength":1}}}},"criteria":{"description":"What the contact is looking for. Each item is saved separately.","type":"array","items":{"$ref":"#/$defs/criteria"}},"notes":{"$ref":"#/$defs/notes"}}},"contact_create_only":{"type":"object","properties":{"company_position":{"$ref":"#/$defs/text"},"town_city":{"$ref":"#/$defs/text"},"allows_email":{"$ref":"#/$defs/boolean"},"allows_text":{"$ref":"#/$defs/boolean"},"allows_letter":{"$ref":"#/$defs/boolean"},"allows_phone":{"$ref":"#/$defs/boolean"},"source":{"description":"How the contact found the office.","$ref":"#/$defs/text"},"last_contacted_date":{"$ref":"#/$defs/date"},"created_date":{"$ref":"#/$defs/date"},"modified_date":{"$ref":"#/$defs/date"}}},"criteria":{"type":"object","additionalProperties":false,"minProperties":1,"properties":{"sale_method":{"$ref":"#/$defs/sale_method"},"category":{"$ref":"#/$defs/category"},"property_types":{"$ref":"#/$defs/names"},"title_types":{"$ref":"#/$defs/names"},"min_price":{"$ref":"#/$defs/money"},"max_price":{"$ref":"#/$defs/money"},"min_bedrooms":{"$ref":"#/$defs/count"},"min_bathrooms":{"$ref":"#/$defs/count"},"min_car_spaces":{"$ref":"#/$defs/count"},"min_land_size":{"$ref":"#/$defs/area"},"max_land_size":{"$ref":"#/$defs/area"},"land_size_unit":{"$ref":"#/$defs/land_size_unit"},"min_living_area":{"$ref":"#/$defs/area"},"max_living_area":{"$ref":"#/$defs/area"},"living_area_unit":{"enum":["sqm","square"],"default":"sqm"},"suburbs":{"description":"Suburb names, such as Richmond. Matched by name in the office's state. Post codes are not accepted.","type":"array","maxItems":10,"uniqueItems":true,"items":{"type":"string","minLength":1,"not":{"pattern":"^[0-9]+$"}}}}},"contact_create":{"type":"object","allOf":[{"$ref":"#/$defs/contact_updatable"},{"$ref":"#/$defs/contact_additions"},{"$ref":"#/$defs/contact_create_only"}],"required":["contact_id","identifier"],"properties":{"contact_id":{"$ref":"#/$defs/projected_contact_id"},"identifier":{"$ref":"#/$defs/identifier"}},"anyOf":[{"required":["first_name"]},{"required":["last_name"]},{"required":["company_name"]}],"unevaluatedProperties":false},"contact_update":{"type":"object","allOf":[{"$ref":"#/$defs/contact_updatable"},{"$ref":"#/$defs/contact_additions"}],"required":["contact_id","identifier"],"properties":{"contact_id":{"$ref":"#/$defs/zenu_id"},"identifier":{"$ref":"#/$defs/identifier"}},"unevaluatedProperties":false},"listing_additions":{"description":"Listing lists. They add to what is there, on create and update.","type":"object","properties":{"vendor_contact_ids":{"description":"The owners selling or leasing. The first is the main vendor.","$ref":"#/$defs/contact_refs"},"notes":{"$ref":"#/$defs/notes"}}},"listing_create":{"type":"object","allOf":[{"$ref":"#/$defs/listing_additions"}],"required":["listing_id","identifier"],"properties":{"listing_id":{"$ref":"#/$defs/projected_listing_id"},"identifier":{"$ref":"#/$defs/identifier"},"unit_number":{"$ref":"#/$defs/text"},"street_number":{"$ref":"#/$defs/text"},"street_name":{"$ref":"#/$defs/text"},"building_name":{"$ref":"#/$defs/text"},"suburb":{"description":"Matched using state and post_code.","$ref":"#/$defs/text"},"state":{"$ref":"#/$defs/text"},"post_code":{"$ref":"#/$defs/text"},"country":{"description":"Country name or code.","$ref":"#/$defs/text"},"sale_method":{"$ref":"#/$defs/sale_method"},"category":{"$ref":"#/$defs/category"},"property_type":{"$ref":"#/$defs/text"},"status":{"description":"Defaults to Prospect, or Sold/Settled when a contract is given.","enum":["Prospect","Appraisal","Sold","Settled"]},"bedrooms":{"$ref":"#/$defs/count"},"bathrooms":{"$ref":"#/$defs/count"},"toilets":{"$ref":"#/$defs/count"},"total_rooms":{"$ref":"#/$defs/count"},"garages":{"$ref":"#/$defs/count"},"carports":{"$ref":"#/$defs/count"},"open_parking_spaces":{"$ref":"#/$defs/count"},"living_area":{"description":"Square metres.","$ref":"#/$defs/area"},"land_size":{"$ref":"#/$defs/area"},"land_size_unit":{"$ref":"#/$defs/land_size_unit"},"year_built":{"type":["integer","null"],"minimum":1000,"maximum":9999},"council_name":{"$ref":"#/$defs/text"},"council_zoning":{"$ref":"#/$defs/text"},"lot_number":{"$ref":"#/$defs/text"},"title_number":{"$ref":"#/$defs/text"},"search_price":{"$ref":"#/$defs/money"},"weekly_rent":{"$ref":"#/$defs/money"},"is_owner_occupied":{"$ref":"#/$defs/boolean"},"modified_date":{"$ref":"#/$defs/date"},"lister_staff_names":{"description":"Up to four listers. The first is the main lister.","type":"array","maxItems":4,"$ref":"#/$defs/staff_names"},"vendor_solicitor_contact_id":{"$ref":"#/$defs/contact_ref"},"appraisal":{"$ref":"#/$defs/appraisal_create"},"prospect":{"$ref":"#/$defs/prospect"},"contract":{"$ref":"#/$defs/contract_create"}},"unevaluatedProperties":false},"listing_update":{"type":"object","allOf":[{"$ref":"#/$defs/listing_additions"}],"required":["listing_id","identifier"],"properties":{"listing_id":{"$ref":"#/$defs/zenu_id"},"identifier":{"$ref":"#/$defs/identifier"},"appraisal":{"$ref":"#/$defs/appraisal_update"},"contract":{"$ref":"#/$defs/contract_update"}},"unevaluatedProperties":false},"appraisal_update":{"description":"Appraisal keys that can be updated.","type":"object","additionalProperties":false,"properties":{"motivation":{"$ref":"#/$defs/hot_warm_cold"}}},"appraisal_create":{"description":"Saved when the listing's status is Appraisal.","type":"object","additionalProperties":false,"properties":{"date":{"$ref":"#/$defs/date"},"source":{"$ref":"#/$defs/text"},"motivation":{"$ref":"#/$defs/hot_warm_cold"},"price":{"description":"The vendor's or appraised price.","$ref":"#/$defs/money"},"staff_name":{"$ref":"#/$defs/staff_name"}}},"prospect":{"description":"Saved when the listing's status is Prospect.","type":"object","additionalProperties":false,"properties":{"pipeline_rating":{"$ref":"#/$defs/hot_warm_cold"},"current_price":{"$ref":"#/$defs/money"},"last_listed_date":{"$ref":"#/$defs/date"},"last_listed_price":{"$ref":"#/$defs/money"},"last_listed_agency":{"$ref":"#/$defs/text"},"last_listed_agent":{"$ref":"#/$defs/text"},"last_contract_date":{"$ref":"#/$defs/date"},"last_sold_date":{"$ref":"#/$defs/date"},"last_sold_price":{"$ref":"#/$defs/money"},"last_leased_agency":{"$ref":"#/$defs/text"}}},"contract_updatable":{"description":"Contract keys that can be updated. The dates are also saved on the listing.","type":"object","properties":{"contract_date":{"$ref":"#/$defs/date"},"unconditional_date":{"$ref":"#/$defs/date"},"settlement_date":{"$ref":"#/$defs/date"},"gross_commission":{"description":"Excluding GST.","$ref":"#/$defs/money"}}},"contract_additions":{"description":"Contract lists. They add to what is there, on create and update.","type":"object","properties":{"buyer_contact_ids":{"description":"The buyers. The first is the main buyer.","$ref":"#/$defs/contact_refs"},"staff":{"description":"Who gets credit for the sale.","type":"array","items":{"type":"object","additionalProperties":false,"required":["staff_name","role"],"properties":{"staff_name":{"$ref":"#/$defs/staff_name"},"role":{"enum":["Lister","Seller"]},"share_percentage":{"type":"number","minimum":0,"maximum":100},"share_amount":{"type":"number","minimum":0}}}}}},"contract_create":{"description":"The sale. A listing has at most one.","type":"object","allOf":[{"$ref":"#/$defs/contract_updatable"},{"$ref":"#/$defs/contract_additions"}],"properties":{"sold_price":{"$ref":"#/$defs/money"},"status":{"description":"Defaults to Settled when settlement_date is given, otherwise Sold.","enum":["Sold","Settled"]},"buyer_solicitor_contact_id":{"$ref":"#/$defs/contact_ref"}},"unevaluatedProperties":false},"contract_update":{"description":"If the listing has no contract, only the listing's dates are updated.","type":"object","allOf":[{"$ref":"#/$defs/contract_updatable"},{"$ref":"#/$defs/contract_additions"}],"unevaluatedProperties":false},"task":{"type":"object","additionalProperties":false,"required":["task_id","identifier","subject"],"anyOf":[{"required":["contact_id"]},{"required":["listing_id"]}],"properties":{"task_id":{"$ref":"#/$defs/projected_task_id"},"identifier":{"$ref":"#/$defs/identifier"},"subject":{"type":"string","minLength":1},"notes":{"$ref":"#/$defs/text"},"status":{"enum":["Active","Completed","Cancelled"],"default":"Active"},"due_date":{"$ref":"#/$defs/date_time"},"staff_name":{"$ref":"#/$defs/staff_name"},"contact_id":{"$ref":"#/$defs/contact_ref"},"listing_id":{"$ref":"#/$defs/listing_ref"}}},"enquiry":{"type":"object","additionalProperties":false,"required":["enquiry_id","identifier","contact_id"],"properties":{"enquiry_id":{"$ref":"#/$defs/projected_enquiry_id"},"identifier":{"$ref":"#/$defs/identifier"},"notes":{"$ref":"#/$defs/text"},"status":{"enum":["Active","Completed"],"default":"Active"},"source":{"description":"Where the enquiry came from, such as a website.","$ref":"#/$defs/text"},"created_date":{"$ref":"#/$defs/date_time"},"staff_name":{"$ref":"#/$defs/staff_name"},"contact_id":{"$ref":"#/$defs/contact_ref"},"listing_id":{"$ref":"#/$defs/listing_ref"}}},"inspection":{"type":"object","additionalProperties":false,"required":["inspection_id","identifier","contact_id","listing_id","start_date"],"properties":{"inspection_id":{"$ref":"#/$defs/projected_inspection_id"},"identifier":{"$ref":"#/$defs/identifier"},"is_private":{"description":"true for a private inspection. false to attend the open home at start_date.","type":"boolean","default":false},"start_date":{"$ref":"#/$defs/date_time"},"end_date":{"description":"When an open home ends.","$ref":"#/$defs/date_time"},"notes":{"$ref":"#/$defs/text"},"interest":{"enum":["Yes","No","Maybe",null]},"feedback_price":{"description":"The price the attendee suggested.","$ref":"#/$defs/money"},"staff_name":{"$ref":"#/$defs/staff_name"},"contact_id":{"$ref":"#/$defs/contact_ref"},"listing_id":{"$ref":"#/$defs/listing_ref"}}},"offer":{"type":"object","additionalProperties":false,"required":["offer_id","identifier","contact_id","listing_id","price"],"properties":{"offer_id":{"$ref":"#/$defs/projected_offer_id"},"identifier":{"$ref":"#/$defs/identifier"},"price":{"$ref":"#/$defs/money"},"terms":{"$ref":"#/$defs/text"},"created_date":{"$ref":"#/$defs/date_time"},"staff_name":{"$ref":"#/$defs/staff_name"},"contact_id":{"$ref":"#/$defs/contact_ref"},"listing_id":{"$ref":"#/$defs/listing_ref"}}}}};

  const STEPS = [
    ["Split each row", "A row holds up to one contact, listing, task, enquiry, inspection and offer. Each becomes its own record in contacts, listings, tasks, enquiries, inspections or offers."],
    ["Give each record an id", "A Zenu id (zenu_contact_id, zenu_property_id) means the record exists: it goes in update with that id. Otherwise it goes in create with a projected id such as projected_contact_1, unique within the file."],
    ["Set identifier", "From the row's *_identifier column. Every record needs one."],
    ["Merge rows with the same identifier", "Into one record. Their notes, tags, criteria, vendors and so on are added to its lists; for single values the first row wins."],
    ["Link the records", "Tasks and enquiries take the row's contact and listing, if any. Inspections and offers need both. contact_sale_type decides whether the contact is the listing's vendor or buyer."],
    ["Leave out existing tasks, enquiries, inspections and offers", "A row with zenu_task_id and so on: they can't be updated."],
  ];
  const VALUE_RULES = [
    ["Dates as dd/mm/yyyy", "yyyy-mm-dd. Times as yyyy-mm-ddThh:mm."],
    ["True / False", "true / false"],
    ["Numbers as text", "Numbers"],
    ["Comma-separated values (contact_type, contact_tags, contact_subscriptions, contact_criteria_suburbs)", "Lists, such as [\"Buyer\", \"Vendor\"]"],
    ["Values separated by | (contact_email_address, phones, notes)", "The first value goes in the main key, the rest in a list."],
    ["Staff (*_team_member* columns)", "The staff member's name, as First Last. Never a staff id."],
    ["Empty column", "Leave the key out."],
    ["contact_date_modified = update", "Put the contact and the row's listing in update, with the keys to change. Without it, an existing record only has its lists added to."],
    ["Column value delete", "null, in an update record."],
    ["property_pipeline_rating A, B, C, D", "Hot, Warm, Cold; leave out for D."],
  ];
  const ASSUMPTIONS = [
    "The proposal leaves a few things open. DARVIS fills them like this, and lists every case in the check report:",
    "Company + person on one row: one company contact per company name (identifier = first row's contact_identifier + \"_company\"), the person linked to it with company_contact_id. The person is the one linked as vendor or buyer.",
    "An existing contact (zenu_contact_id) without contact_date_modified = update still gets company_contact_id when the row names a company, as in the proposal's sample.",
    "A partner link with no contact_partnership_type uses \"Partner\". Partners are linked on one side only; Zenu adds the other.",
    "A sale team member with no role is a Seller.",
    "property_timeline_status values that aren't Prospect, Appraisal, Sold or Settled use the empty-status rule.",
    "A record the schema would reject (a new contact with no name, an inspection without a start date, an offer without a price) is left out, and links to it are dropped. Both are listed.",
    "Post codes in contact_criteria_suburbs are swapped for their suburb names from the AU suburb list.",
  ];

  // ------------------------------------------------------------------ values
  const str = (v) => (v === null || v === undefined ? "" : String(v)).replace(/ /g, " ").trim();
  const low = (v) => str(v).toLowerCase();
  const isDelete = (v) => low(v) === "delete";
  /** A zenu_company_id / zenu_solicitor_id that is a contact_identifier in the file, not a Zenu id (the cleaned contacts do this) */
  const isIdentRef = (v) => !!str(v) && !isDelete(v) && !/^[0-9.,\s]+$/.test(str(v));
  const TITLES = { mr: "Mr", mrs: "Mrs", miss: "Miss", ms: "Ms", dr: "Dr" };
  const HWC = { hot: "Hot", warm: "Warm", cold: "Cold" };
  const CATEGORY = { residential: "Residential", commercial: "Commercial", rural: "Rural", business: "Business" };
  const PIPELINE = { a: "Hot", b: "Warm", c: "Cold", d: null, hot: "Hot", warm: "Warm", cold: "Cold" };
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  const STAFF_RE = /^\S+( \S+)+$/;
  const pad2 = (n) => String(n).padStart(2, "0");

  function saleMethod(v) {
    const s = low(v);
    if (/^(sale|sell|selling|for sale|sold|residential sale|sales)$/.test(s)) return "Sale";
    if (/^(lease|leasing|leased|rent|rental|rentals|for lease|for rent|residential lease)$/.test(s)) return "Lease";
    return undefined;
  }
  function landUnit(v, living) {
    const s = low(v).replace(/[.²]/g, (c) => (c === "²" ? "2" : ""));
    if (/^(sqm|m2|sq m|sq ?metres?|sq ?meters?|square ?metres?|square ?meters?|metres?|meters?)$/.test(s)) return "sqm";
    if (/^(square|squares|sq|sqs)$/.test(s)) return "square";
    if (living) return undefined;
    if (/^(acre|acres|ac)$/.test(s)) return "acre";
    if (/^(hectare|hectares|ha)$/.test(s)) return "hectare";
    return undefined;
  }
  /** dd/mm/yyyy [hh:mm[:ss] [am|pm]] or yyyy-mm-dd[Thh:mm] -> { d: "yyyy-mm-dd", t: "hh:mm" | "" } */
  function parseDate(v) {
    const s = str(v);
    let y, m, d, H = 0, M = 0, hasT = false, ap = "";
    let x = s.match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{4})(?:[ T,]+(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?\s*([ap]\.?m\.?)?)?$/i);
    if (x) { [d, m, y] = [+x[1], +x[2], +x[3]]; if (x[4] !== undefined) { hasT = true; H = +x[4]; M = +x[5]; ap = (x[6] || "").replace(/\./g, "").toLowerCase(); } }
    else if ((x = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?)?(?:Z|[+-]\d{2}:?\d{2})?$/))) {
      [y, m, d] = [+x[1], +x[2], +x[3]]; if (x[4] !== undefined) { hasT = true; H = +x[4]; M = +x[5]; }
    } else return null;
    if (ap === "pm" && H < 12) H += 12;
    if (ap === "am" && H === 12) H = 0;
    if (m < 1 || m > 12 || d < 1 || H > 23 || M > 59) return null;
    const dim = new Date(Date.UTC(y, m, 0)).getUTCDate();
    if (d > dim || y < 1000) return null;
    return { d: `${y}-${pad2(m)}-${pad2(d)}`, t: hasT && (H || M) ? `${pad2(H)}:${pad2(M)}` : "" };
  }
  function parseNumber(v) {
    const s = str(v).replace(/[$,\s]/g, "").replace(/%$/, "");
    if (!/^-?(\d+\.?\d*|\.\d+)$/.test(s)) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }
  function parseBool(v) {
    const s = low(v);
    if (/^(true|yes|y|1|-1|t)$/.test(s)) return true;
    if (/^(false|no|n|0|f)$/.test(s)) return false;
    return null;
  }

  // ------------------------------------------------------------------ the converter
  /**
   * files: [{ name, grid }] where grid is rows of cells (header row first, within the first 10 rows).
   * opts: { officeId (required), postcodeSuburbs(pc) -> [names] (optional), splitAddress(full) -> {unit_number,
   *         street_number, street_name, suburb, state, post_code} | null (optional) }
   */
  function convert(files, opts = {}) {
    const officeId = Number(str(opts.officeId));
    if (!Number.isInteger(officeId) || officeId < 1) throw new Error("Enter the Zenu office id (a whole number) first.");
    const t0 = Date.now();
    const issues = new Map();
    const issue = (sev, text, example) => {
      let it = issues.get(text);
      if (!it) { it = { severity: sev, text, count: 0, examples: [] }; issues.set(text, it); }
      it.count++;
      if (example !== undefined && it.examples.length < 6) it.examples.push(String(example).slice(0, 160));
    };
    const stats = { files: files.length, rows: 0, emptyRows: 0, perFile: [], skippedExisting: { task: 0, enquiry: 0, inspection: 0, offer: 0 },
      links: { vendors: 0, buyers: 0, partners: 0, companies: 0, solicitors: 0 }, splitAddresses: 0, mergedRows: { contact: 0, listing: 0 } };
    const known = new Set(MAP.map((r) => r[0]));

    // ---- read every file into row objects keyed by template column
    const rows = [];
    for (const f of files) {
      const grid = f.grid || [];
      let hr = -1;
      for (let i = 0; i < Math.min(grid.length, 10); i++) {
        const hits = (grid[i] || []).filter((c) => known.has(low(c))).length;
        if (hits >= 3) { hr = i; break; }
      }
      if (hr < 0) { stats.perFile.push({ name: f.name, rows: 0, columns: 0, unknown: [], note: "no import template header found" }); issue("WARN", "Files with no import template header row (contact_identifier, property_identifier and so on): skipped", f.name); continue; }
      const header = grid[hr].map(low);
      const incl = header.indexOf("include in import file?");                    // the migration's own row filter
      const unknown = [...new Set(header.filter((h) => h && !known.has(h) && h !== "include in import file?"))];
      unknown.forEach((h) => issue("INFO", "Columns that aren't in the import template: ignored", `${f.name}: ${h}`));
      let n = 0;
      for (let i = hr + 1; i < grid.length; i++) {
        const g = grid[i] || [];
        if (!g.some((c) => str(c))) continue;
        if (incl >= 0 && /^(no|n|false|0|exclude|excluded)$/i.test(str(g[incl]))) { issue("INFO", "Rows marked No in 'Include in Import File?': left out", `${f.name} row ${i + 1}`); continue; }
        const o = { __file: f.name, __row: i + 1 };
        if (f.status) o.__status = f.status;
        header.forEach((h, j) => { if (known.has(h) && o[h] === undefined) o[h] = str(g[j]); });
        rows.push(o); n++;
      }
      stats.perFile.push({ name: f.name, rows: n, columns: header.filter((h) => known.has(h)).length, unknown, status: f.status || "" });
    }
    stats.rows = rows.length;

    // ---- value readers (each returns undefined to leave the key out, after listing why)
    const where = (r, col) => `${r.__file} row ${r.__row}: ${col} = ${str(r[col]).slice(0, 60)}`;
    const P = {
      text: (v) => v,
      name: (v, r, col) => { if (v.length > 150) { issue("WARN", "Names over 150 characters: cut to 150", where(r, col)); return v.slice(0, 150); } return v; },
      title: (v, r, col) => { const t = TITLES[low(v).replace(/\./g, "")]; if (!t) issue("WARN", "Titles Zenu doesn't accept (only Mr, Mrs, Miss, Ms, Dr): left out", where(r, col)); return t; },
      hwc: (v, r, col) => { const t = HWC[low(v)]; if (!t) issue("WARN", "Ratings that aren't Hot, Warm or Cold: left out", where(r, col)); return t; },
      category: (v, r, col) => { const t = CATEGORY[low(v)]; if (!t) issue("WARN", "Categories that aren't Residential, Commercial, Rural or Business: left out", where(r, col)); return t; },
      saleMethod: (v, r, col) => { const t = saleMethod(v); if (!t) issue("WARN", "Sale methods that aren't Sale or Lease: left out", where(r, col)); return t; },
      landUnit: (v, r, col) => { const t = landUnit(v, false); if (!t) issue("WARN", "Land size units that aren't sqm, acre, hectare or square: left out", where(r, col)); return t; },
      livingUnit: (v, r, col) => { const t = landUnit(v, true); if (!t) issue("WARN", "Living area units that aren't sqm or square: left out", where(r, col)); return t; },
      bool: (v, r, col) => { const b = parseBool(v); if (b === null) { issue("WARN", "True / False values that couldn't be read: left out", where(r, col)); return undefined; } return b; },
      date: (v, r, col) => { const d = parseDate(v); if (!d) { issue("WARN", "Dates that couldn't be read (expected dd/mm/yyyy): left out", where(r, col)); return undefined; } return d.d; },
      dateTime: (v, r, col) => { const d = parseDate(v); if (!d) { issue("WARN", "Dates that couldn't be read (expected dd/mm/yyyy): left out", where(r, col)); return undefined; } return d.t ? `${d.d}T${d.t}` : d.d; },
      money: (v, r, col) => { const n = parseNumber(v); if (n === null || n < 0) { issue("WARN", "Amounts that aren't a number of 0 or more: left out", where(r, col)); return undefined; } return n; },
      area: (v, r, col) => { const n = parseNumber(v); if (n === null || n < 0) { issue("WARN", "Areas that aren't a number of 0 or more: left out", where(r, col)); return undefined; } return n; },
      count: (v, r, col) => { const n = parseNumber(v); if (n === null || n < 0 || !Number.isInteger(n)) { issue("WARN", "Counts that aren't a whole number of 0 or more (bedrooms and so on): left out", where(r, col)); return undefined; } return n; },
      year: (v, r, col) => { const n = parseNumber(v); if (n === null || !Number.isInteger(n) || n < 1000 || n > 9999) { issue("WARN", "Years built that aren't a 4-digit year: left out", where(r, col)); return undefined; } return n; },
      percent: (v, r, col) => { const n = parseNumber(v); if (n === null || n < 0 || n > 100) { issue("WARN", "Sale team percentages that aren't 0 to 100: left out", where(r, col)); return undefined; } return n; },
      staff: (v, r, col) => {
        const s = v.replace(/\s+/g, " ");
        if (!STAFF_RE.test(s) || /^[0-9 ]+$/.test(s)) { issue("WARN", "Staff that aren't a First Last name (a single word or a staff id): left out", where(r, col)); return undefined; }
        return s;
      },
      email: (v, r, col) => { if (!EMAIL_RE.test(v)) { issue("WARN", "Emails that aren't valid: left out", where(r, col)); return undefined; } return v; },
      postcode: (v) => (/^\d{3}$/.test(v) ? "0" + v : v),
      zenuId: (v, r, col) => { const n = parseNumber(v); if (n === null || !Number.isInteger(n) || n < 1) { issue("WARN", "Zenu ids that aren't a whole number: left out", where(r, col)); return undefined; } return n; },
      taskStatus: (v, r, col) => { const s = low(v); const t = /^(active|open|pending|current)$/.test(s) ? "Active" : /^(completed?|done|closed|finished)$/.test(s) ? "Completed" : /^(cancell?ed|cancel)$/.test(s) ? "Cancelled" : undefined; if (!t) issue("WARN", "Task statuses that aren't Active, Completed or Cancelled: left out", where(r, col)); return t; },
      enquiryStatus: (v, r, col) => { const s = low(v); const t = /^(active|open|pending|current)$/.test(s) ? "Active" : /^(completed?|done|closed|finished)$/.test(s) ? "Completed" : undefined; if (!t) issue("WARN", "Enquiry statuses that aren't Active or Completed: left out", where(r, col)); return t; },
      interest: (v, r, col) => { const s = low(v); const t = /^(yes|y|true|interested)$/.test(s) ? "Yes" : /^(no|n|false|not interested)$/.test(s) ? "No" : /^(maybe|unsure|m)$/.test(s) ? "Maybe" : undefined; if (!t) issue("WARN", "Inspection interest that isn't Yes, No or Maybe: left out", where(r, col)); return t; },
      role: (v, r, col) => { const s = low(v); const t = s === "lister" ? "Lister" : s === "seller" ? "Seller" : undefined; if (!t) issue("WARN", "Sale team roles that aren't Lister or Seller: Seller used", where(r, col)); return t || "Seller"; },
    };
    const read = (r, col, type) => { const v = str(r[col]); return v ? P[type](v, r, col) : undefined; };
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    /** Single value: the first row wins. 'delete' writes null, in an update only. */
    function setOne(rec, key, r, col, type, canNull, label) {
      const raw = str(r[col]);
      if (!raw) return;
      let val;
      if (isDelete(raw)) {
        if (!canNull) { issue("INFO", "'delete' on a new record or a key that can't be cleared: ignored", where(r, col)); return; }
        val = null;
      } else val = P[type](raw, r, col);
      if (val === undefined) return;
      if (!(key in rec)) rec[key] = val;
      else if (!same(rec[key], val)) issue("INFO", "Rows with the same identifier but a different value: the first row's value was kept", `${label}: ${key} '${rec[key]}' vs '${val}'`);
    }
    const addTo = (rec, key, items) => {
      if (!items.length) return;
      const list = rec[key] || (rec[key] = []);
      for (const it of items) if (!list.some((x) => same(x, it))) list.push(it);
    };
    const splitPipe = (v) => str(v).split("|").map((s) => s.trim()).filter(Boolean);
    const splitComma = (v) => [...new Set(str(v).split(",").map((s) => s.trim()).filter(Boolean))];

    // ---- pre-pass: contacts flagged for a full update
    const fullUpdate = new Set();
    for (const r of rows) if (low(r.contact_date_modified) === "update" && str(r.contact_identifier)) fullUpdate.add(str(r.contact_identifier));

    // ---- records
    const C = new Map(), L = new Map(), T = { task: new Map(), enquiry: new Map(), inspection: new Map(), offer: new Map() };
    const companies = new Map();
    let nC = 0, nL = 0;
    const nT = { task: 0, enquiry: 0, inspection: 0, offer: 0 };
    const partnerLinks = [], partnerGroups = new Map(), companyRefs = [], solicitorRefs = [];

    function identOf(r, identCol, zidCol, kind) {
      let ident = str(r[identCol]);
      const zid = zidCol ? read(r, zidCol, "zenuId") : undefined;
      if (!ident && zid) { ident = String(zid); issue("WARN", `${kind} rows with a Zenu id but no identifier: the Zenu id was used as the identifier`, where(r, zidCol)); }
      if (ident.length > 100) { issue("WARN", "Identifiers over 100 characters: cut to 100", where(r, identCol)); ident = ident.slice(0, 100); }
      return { ident, zid };
    }
    function contactEntry(ident, zid, r) {
      let e = C.get(ident);
      if (e) {
        stats.mergedRows.contact++;
        if (zid && e.update && e.rec.contact_id !== zid) issue("WARN", "Contacts with one identifier but two Zenu ids: the first was kept", `${ident}: ${e.rec.contact_id} vs ${zid}`);
        if (zid && !e.update) issue("WARN", "Contacts with a Zenu id on some rows only: kept as a new contact", ident);
        return e;
      }
      e = zid ? { rec: { contact_id: zid, identifier: ident }, update: true, full: fullUpdate.has(ident) }
        : { rec: { contact_id: `projected_contact_${++nC}`, identifier: ident }, update: false, full: false };
      e.first = r; C.set(ident, e);
      return e;
    }

    const CONTACT_ONE = [
      // [csv, key, type, create-only]
      ["contact_title", "title", "title"], ["contact_first_name", "first_name", "name"], ["contact_surname", "last_name", "name"],
      ["contact_name_on_letters", "name_on_letters", "text"], ["contact_legal_name", "legal_name", "text"],
      ["contact_company_position", "company_position", "text", 1],
      ["contact_mailing_address_line1", "address_line_1", "text"], ["contact_mailing_address_line2", "address_line_2", "text"],
      ["contact_mailing_address_suburb", "suburb", "text"], ["contact_mailing_address_town_city", "town_city", "text", 1],
      ["contact_mailing_address_state", "state", "text"], ["contact_mailing_address_country", "country", "text"],
      ["contact_mailing_address_postcode", "post_code", "postcode"],
      ["contact_allow_contact_via_text", "allows_text", "bool", 1], ["contact_allow_contact_via_email", "allows_email", "bool", 1],
      ["contact_allow_contact_via_letter", "allows_letter", "bool", 1], ["contact_allow_contact_via_phone", "allows_phone", "bool", 1],
      ["contact_unsubscribe_from_marketing", "is_unsubscribed", "bool"], ["contact_enquiry_source", "source", "text", 1],
      ["contact_rating", "rating", "hwc"], ["contact_last_contact_date", "last_contacted_date", "date", 1],
      ["contact_date_created", "created_date", "date", 1], ["contact_team_member_1", "primary_staff_name", "staff"],
    ];
    const PHONES = [["contact_mobile", "mobile_phone", "Mobile"], ["contact_phone_work", "work_phone", "Work"], ["contact_phone_home", "home_phone", "Home"], ["contact_fax", "fax", "Fax"]];
    const CRITERIA = [
      ["contact_criteria_sale_method", "sale_method", "saleMethod"], ["contact_criteria_category", "category", "category"],
      ["contact_criteria_property_type", "property_types", "list"], ["contact_criteria_title_type", "title_types", "list"],
      ["contact_criteria_price_from", "min_price", "money"], ["contact_criteria_price_to", "max_price", "money"],
      ["contact_criteria_bedrooms", "min_bedrooms", "count"], ["contact_criteria_bathrooms", "min_bathrooms", "count"],
      ["contact_criteria_carspaces", "min_car_spaces", "count"], ["contact_criteria_land_from", "min_land_size", "area"],
      ["contact_criteria_land_to", "max_land_size", "area"], ["contact_criteria_land_unit", "land_size_unit", "landUnit"],
      ["contact_criteria_living_area_from", "min_living_area", "area"], ["contact_criteria_living_area_to", "max_living_area", "area"],
      ["contact_criteria_living_area_unit", "living_area_unit", "livingUnit"], ["contact_criteria_suburbs", "suburbs", "suburbs"],
    ];

    /** The row's contact (the person when a company is split off). Returns the entry or null. */
    function doContact(r) {
      const { ident, zid } = identOf(r, "contact_identifier", "zenu_contact_id", "Contact");
      if (!ident) {
        if (CONTACT_ONE.some(([c]) => str(r[c])) || str(r.contact_email_address) || str(r.contact_mobile)) issue("WARN", "Rows with contact details but no contact_identifier: contact left out", `${r.__file} row ${r.__row}`);
        return null;
      }
      const e = contactEntry(ident, zid, r), rec = e.rec;
      const scalars = !e.update || e.full;             // an existing contact without 'update' only has its lists added to
      const canNull = e.update && e.full;
      const isCo = parseBool(r.contact_is_company) === true;
      const coName = str(r.contact_company_name);
      const hasPerson = !!(str(r.contact_first_name) || str(r.contact_surname));
      const splitCo = isCo && hasPerson && coName && !str(r.zenu_company_id);

      if (scalars) {
        for (const [col, key, type, createOnly] of CONTACT_ONE) {
          if (createOnly && e.update) { if (str(r[col]) && !isDelete(r[col])) e.createOnlySkipped = true; continue; }
          setOne(rec, key, r, col, type, canNull, ident);
        }
        if (!splitCo) {
          setOne(rec, "is_company", r, "contact_is_company", "bool", canNull, ident);
          setOne(rec, "company_name", r, "contact_company_name", "text", canNull, ident);
        }
        const mod = str(r.contact_date_modified);
        if (mod && low(mod) !== "update" && !e.update) setOne(rec, "modified_date", r, "contact_date_modified", "date", false, ident);
        // email + phones: the first value is the main one, the rest are extras
        const emails = isDelete(r.contact_email_address) ? [] : splitPipe(r.contact_email_address).map((v) => P.email(v, r, "contact_email_address")).filter(Boolean);
        if (isDelete(r.contact_email_address)) setOne(rec, "email", r, "contact_email_address", "text", canNull, ident);
        else if (emails.length) {
          if (!("email" in rec)) rec.email = emails.shift();
          addTo(rec, "extra_emails", emails.filter((x) => x !== rec.email));
        }
        for (const [col, key, label] of PHONES) {
          if (isDelete(r[col])) { setOne(rec, key, r, col, "text", canNull, ident); continue; }
          const nums = splitPipe(r[col]);
          if (!nums.length) continue;
          if (!(key in rec)) rec[key] = nums.shift();
          addTo(rec, "extra_phones", nums.filter((x) => x !== rec[key]).map((number) => ({ label, number })));
        }
      } else {
        if (CONTACT_ONE.some(([c]) => str(r[c]))) e.listsOnly = true;
        const emails = splitPipe(r.contact_email_address).map((v) => P.email(v, r, "contact_email_address")).filter(Boolean);
        addTo(rec, "extra_emails", emails);
        for (const [col, , label] of PHONES) addTo(rec, "extra_phones", splitPipe(r[col]).map((number) => ({ label, number })));
      }
      // lists: added on create and update
      if (!isDelete(r.contact_type)) addTo(rec, "types", splitComma(r.contact_type));
      if (!isDelete(r.contact_tags)) addTo(rec, "tags", splitComma(r.contact_tags));
      addTo(rec, "subscription_ids", splitComma(r.contact_subscriptions).map((v) => P.zenuId(v, r, "contact_subscriptions")).filter((x) => x !== undefined));
      const team = [];
      for (let i = 2; i <= 15; i++) { const s = read(r, "contact_team_member_" + i, "staff"); if (s && !team.includes(s)) team.push(s); }
      addTo(rec, "secondary_staff_names", team.filter((s) => s !== rec.primary_staff_name));
      // notes: one per |-separated value, the row's date and staff on each
      const notes = splitPipe(r.contact_notes);
      if (notes.length) {
        const cd = read(r, "contact_note_created_date", "date"), sn = read(r, "contact_note_team_member", "staff");
        addTo(rec, "notes", notes.map((text) => Object.assign({ text }, cd ? { created_date: cd } : {}, sn ? { staff_name: sn } : {})));
      }
      // criteria: one item per row
      const crit = {};
      for (const [col, key, type] of CRITERIA) {
        const v = str(r[col]);
        if (!v || isDelete(v)) continue;
        if (type === "list") { const l = splitComma(v); if (l.length) crit[key] = l; continue; }
        if (type === "suburbs") {
          const out = [];
          for (const s of splitComma(v)) {
            if (/^\d{3,4}$/.test(s)) {
              const names = opts.postcodeSuburbs ? opts.postcodeSuburbs(s.padStart(4, "0")) : [];
              if (names.length) { names.forEach((n) => out.includes(n) || out.push(n)); issue("INFO", "Criteria post codes swapped for their suburb names", `${ident}: ${s} -> ${names.join(", ")}`); }
              else issue("WARN", "Criteria post codes with no suburb in the AU list: left out", `${ident}: ${s}`);
            } else if (!out.some((x) => x.toLowerCase() === s.toLowerCase())) out.push(s);
          }
          if (out.length > 10) issue("WARN", "Criteria with more than 10 suburbs: only the first 10 were kept", `${ident}: ${out.length} suburbs`);
          if (out.length) crit.suburbs = out.slice(0, 10);
          continue;
        }
        const val = P[type](v, r, col);
        if (val !== undefined) crit[key] = val;
      }
      if (Object.keys(crit).length) addTo(rec, "criteria", [crit]);
      // company: an existing one by Zenu id, or one company contact per company name in the file
      const zcoRef = isIdentRef(r.zenu_company_id);
      const zco = zcoRef ? undefined : read(r, "zenu_company_id", "zenuId");
      if (zco !== undefined) linkCompany(e, zco, r);
      else if (zcoRef) companyRefs.push([e, str(r.zenu_company_id), r]);
      else if (splitCo) {
        const key = coName.toLowerCase().replace(/\s+/g, " ");
        let co = companies.get(key);
        if (!co) {
          const cIdent = (ident + "_company").slice(0, 100);
          co = { rec: { contact_id: `projected_contact_${++nC}`, identifier: cIdent, is_company: true, company_name: coName }, update: false, company: true, first: r };
          const staff = read(r, "contact_team_member_1", "staff");
          if (staff) co.rec.primary_staff_name = staff;
          companies.set(key, co); C.set("\u0000company:" + key, co);
        }
        linkCompany(e, co.rec.contact_id, r);
      }
      // partners
      const pIdent = str(r.contact_partner_identifier), pType = str(r.contact_partnership_type);
      if (pIdent) partnerLinks.push([e, pIdent, pType, r]);
      else if (str(r.contact_partnership_id)) {
        const k = str(r.contact_partnership_id);
        const g = partnerGroups.get(k) || { list: [], type: "" };
        if (!g.list.includes(e)) g.list.push(e);
        if (!g.type && pType) g.type = pType;
        partnerGroups.set(k, g);
      }
      return e;
    }
    function linkCompany(e, id, r) {
      const rec = e.rec;
      if (rec.company_contact_id === undefined) { rec.company_contact_id = id; stats.links.companies++; }
      else if (rec.company_contact_id !== id) issue("INFO", "Contacts linked to two companies: the first was kept", `${rec.identifier}`);
    }

    const LISTING_ONE = [
      ["property_unit_number", "unit_number", "text"], ["property_street_number", "street_number", "text"], ["property_street_name", "street_name", "text"],
      ["property_building_name", "building_name", "text"], ["property_suburb", "suburb", "text"], ["property_state", "state", "text"],
      ["property_postcode", "post_code", "postcode"], ["property_country", "country", "text"], ["property_sale_method", "sale_method", "saleMethod"],
      ["property_category", "category", "category"], ["property_type", "property_type", "text"], ["property_bedrooms", "bedrooms", "count"],
      ["property_bathrooms", "bathrooms", "count"], ["property_toilets", "toilets", "count"], ["property_total_rooms", "total_rooms", "count"],
      ["property_garages", "garages", "count"], ["property_carports", "carports", "count"], ["property_open_parking_spaces", "open_parking_spaces", "count"],
      ["property_living_area_m2", "living_area", "area"], ["property_land_size_m2", "land_size", "area"], ["listing_land_size_system", "land_size_unit", "landUnit"],
      ["property_year_built", "year_built", "year"], ["property_council_name", "council_name", "text"], ["property_council_zoning", "council_zoning", "text"],
      ["property_lot_number", "lot_number", "text"], ["property_title_number", "title_number", "text"], ["property_search_price", "search_price", "money"],
      ["property_rent_per_week", "weekly_rent", "money"], ["property_is_occupied_by_owner", "is_owner_occupied", "bool"], ["property_modified_date", "modified_date", "date"],
    ];
    const APPRAISAL = [["property_appraisal_date", "date", "date"], ["property_appraisal_source", "source", "text"], ["property_vendor_price", "price", "money"]];
    const PROSPECT = [
      ["property_current_price", "current_price", "money"], ["property_last_listed_date", "last_listed_date", "date"], ["property_last_listed_price", "last_listed_price", "money"],
      ["property_last_listed_by_agency", "last_listed_agency", "text"], ["property_last_listed_by_agent", "last_listed_agent", "text"],
      ["property_last_contract_date", "last_contract_date", "date"], ["property_last_sold_date", "last_sold_date", "date"],
      ["property_last_sold_price", "last_sold_price", "money"], ["property_last_leased_agency", "last_leased_agency", "text"],
    ];
    const CONTRACT = [["property_contract_date", "contract_date", "date", 1], ["property_unconditional_date", "unconditional_date", "date", 1],
      ["property_settlement_date", "settlement_date", "date", 1], ["property_sold_price", "sold_price", "money", 0], ["property_sale_gross_commission", "gross_commission", "money", 1]];
    const UPDATABLE_LISTING_COLS = new Set(["property_identifier", "zenu_property_id", "property_pipeline_rating", "property_notes", "property_note_created_date",
      "property_note_team_member", "property_timeline_status", "property_contract_date", "property_unconditional_date", "property_settlement_date",
      "property_sale_gross_commission", "property_sale_team_member", "property_sale_team_member_role", "property_sale_team_member_recognition_percentage",
      "property_sale_team_member_recognition_amount"]);

    function doListing(r) {
      const { ident, zid } = identOf(r, "property_identifier", "zenu_property_id", "Listing");
      if (!ident) {
        if (LISTING_ONE.some(([c]) => str(r[c])) || str(r.property_full_address)) issue("WARN", "Rows with property details but no property_identifier: listing left out", `${r.__file} row ${r.__row}`);
        return null;
      }
      let e = L.get(ident);
      if (!e) {
        e = zid ? { rec: { listing_id: zid, identifier: ident }, update: true } : { rec: { listing_id: `projected_listing_${++nL}`, identifier: ident }, update: false };
        e.statusRaw = ""; e.rating = undefined; L.set(ident, e);
      } else {
        stats.mergedRows.listing++;
        if (zid && e.update && e.rec.listing_id !== zid) issue("WARN", "Listings with one identifier but two Zenu ids: the first was kept", `${ident}: ${e.rec.listing_id} vs ${zid}`);
      }
      const rec = e.rec;
      if (!e.statusRaw && str(r.property_timeline_status)) e.statusRaw = str(r.property_timeline_status);
      if (!e.statusFile && r.__status) e.statusFile = r.__status;                   // from the file (Appraisal / Prospect workbook)
      const pr = str(r.property_pipeline_rating);
      if (pr && e.rating === undefined) {
        if (low(pr) in PIPELINE) e.rating = PIPELINE[low(pr)];
        else issue("WARN", "Pipeline ratings that aren't A, B, C, D, Hot, Warm or Cold: left out", where(r, "property_pipeline_rating"));
      }
      if (!e.update) {
        for (const [col, key, type] of LISTING_ONE) setOne(rec, key, r, col, type, false, ident);
        if (!str(r.property_street_name) && str(r.property_full_address) && !rec.street_name) {
          const parts = opts.splitAddress ? opts.splitAddress(str(r.property_full_address)) : null;
          if (parts && parts.street_name) {
            for (const [k, v] of Object.entries(parts)) if (v && !(k in rec)) rec[k] = k === "post_code" ? P.postcode(String(v)) : String(v);
            stats.splitAddresses++;
          } else issue("WARN", "Full addresses that couldn't be split into street and suburb: left out", where(r, "property_full_address"));
        }
        const listers = [];
        for (let i = 1; i <= 4; i++) { const s = read(r, "property_team_member_" + i, "staff"); if (s && !listers.includes(s)) listers.push(s); }
        addTo(rec, "lister_staff_names", listers);
        if (rec.lister_staff_names && rec.lister_staff_names.length > 4) { issue("WARN", "Listings with more than 4 listers across rows: the first 4 were kept", ident); rec.lister_staff_names = rec.lister_staff_names.slice(0, 4); }
        const ap = rec.appraisal || {};
        for (const [col, key, type] of APPRAISAL) setOne(ap, key, r, col, type, false, ident);
        if (Object.keys(ap).length) rec.appraisal = ap;
        const ps = rec.prospect || {};
        for (const [col, key, type] of PROSPECT) setOne(ps, key, r, col, type, false, ident);
        if (Object.keys(ps).length) rec.prospect = ps;
      } else {
        const extra = Object.keys(r).filter((c) => c.startsWith("property_") && !UPDATABLE_LISTING_COLS.has(c) && str(r[c]));
        if (extra.length) issue("INFO", "Existing listings (zenu_property_id): only vendors, notes, motivation and the contract can be updated, other property columns were left out", `${ident}: ${extra.slice(0, 4).join(", ")}${extra.length > 4 ? "…" : ""}`);
      }
      // contract (dates and gross commission can be updated; sold price only on a new listing)
      const ct = rec.contract || {};
      for (const [col, key, type, updatable] of CONTRACT) {
        if (e.update && !updatable) { if (str(r[col])) issue("INFO", "Sold price on an existing listing can't be updated: left out", ident); continue; }
        setOne(ct, key, r, col, type, e.update, ident);
      }
      const sName = read(r, "property_sale_team_member", "staff");
      if (sName) {
        const item = { staff_name: sName, role: str(r.property_sale_team_member_role) ? P.role(str(r.property_sale_team_member_role), r, "property_sale_team_member_role") : "Seller" };
        if (!str(r.property_sale_team_member_role)) issue("INFO", "Sale team members with no role: Seller used", `${ident}: ${sName}`);
        const pc = read(r, "property_sale_team_member_recognition_percentage", "percent"), am = read(r, "property_sale_team_member_recognition_amount", "money");
        if (pc !== undefined) item.share_percentage = pc;
        if (am !== undefined) item.share_amount = am;
        const list = ct.staff || (ct.staff = []);
        if (!list.some((x) => x.staff_name === item.staff_name && x.role === item.role)) list.push(item);
      }
      if (Object.keys(ct).length) rec.contract = ct;
      const notes = splitPipe(r.property_notes);
      if (notes.length) {
        const cd = read(r, "property_note_created_date", "date"), sn = read(r, "property_note_team_member", "staff");
        addTo(rec, "notes", notes.map((text) => Object.assign({ text }, cd ? { created_date: cd } : {}, sn ? { staff_name: sn } : {})));
      }
      return e;
    }

    const OTHER = {
      task: { ident: "task_identifier", zid: "zenu_task_id", list: "tasks", fields: [["task_subject", "subject", "text"], ["task_notes", "notes", "text"], ["task_status", "status", "taskStatus"], ["task_date_due", "due_date", "dateTime"], ["task_team_member_1", "staff_name", "staff"]] },
      enquiry: { ident: "enquiry_identifier", zid: "zenu_enquiry_id", list: "enquiries", fields: [["enquiry_notes", "notes", "text"], ["enquiry_status", "status", "enquiryStatus"], ["enquiry_source", "source", "text"], ["enquiry_date_created", "created_date", "dateTime"], ["enquiry_team_member_1", "staff_name", "staff"]] },
      inspection: { ident: "inspection_identifier", zid: "zenu_inspection_id", list: "inspections", fields: [["inspection_is_private", "is_private", "bool"], ["inspection_start_date", "start_date", "dateTime"], ["inspection_end_date", "end_date", "dateTime"], ["inspection_notes", "notes", "text"], ["inspection_is_interested", "interest", "interest"], ["inspection_feedback_price", "feedback_price", "money"], ["inspection_team_member_1", "staff_name", "staff"]] },
      offer: { ident: "offer_identifier", zid: "zenu_offer_id", list: "offers", fields: [["offer_price", "price", "money"], ["offer_terms", "terms", "text"], ["offer_date", "created_date", "dateTime"], ["offer_team_member_1", "staff_name", "staff"]] },
    };
    function doOther(kind, r, ce, le) {
      const d = OTHER[kind];
      const ident = str(r[d.ident]).slice(0, 100);
      if (str(r[d.zid])) { if (ident || d.fields.some(([c]) => str(r[c]))) stats.skippedExisting[kind]++; return; }
      if (!ident) {
        if (d.fields.some(([c]) => str(r[c]))) issue("WARN", `Rows with ${kind} details but no ${d.ident}: ${kind} left out`, `${r.__file} row ${r.__row}`);
        return;
      }
      let e = T[kind].get(ident);
      if (!e) { e = { rec: { [kind + "_id"]: `projected_${kind}_${++nT[kind]}`, identifier: ident } }; T[kind].set(ident, e); }
      for (const [col, key, type] of d.fields) setOne(e.rec, key, r, col, type, false, ident);
      if (ce && e.rec.contact_id === undefined) e.rec.contact_id = ce.rec.contact_id;
      if (le && e.rec.listing_id === undefined) e.rec.listing_id = le.rec.listing_id;
    }

    // ---- pass over the rows
    for (const r of rows) {
      const ce = doContact(r), le = doListing(r);
      if (!ce && !le && !Object.keys(OTHER).some((k) => str(r[OTHER[k].ident]) || str(r[OTHER[k].zid]))) { stats.emptyRows++; continue; }
      if (ce && le) {
        const st = low(r.contact_sale_type);
        const solRef = isIdentRef(r.zenu_solicitor_id);
        const sol = solRef ? str(r.zenu_solicitor_id) : read(r, "zenu_solicitor_id", "zenuId");
        const setSol = (obj, key) => {                   // first one kept; an identifier is looked up after every row is read
          if (obj[key] !== undefined) return;
          if (solRef) { obj[key] = null; solicitorRefs.push([obj, key, sol, r]); } else { obj[key] = sol; stats.links.solicitors++; }
        };
        if (/^(seller|vendor|owner|landlord|lessor)$/.test(st)) {
          addTo(le.rec, "vendor_contact_ids", [ce.rec.contact_id]);
          if (sol !== undefined) {
            if (le.update) issue("WARN", "Solicitors on existing listings can't be set by an update: left out", `${le.rec.identifier}: ${sol}`);
            else setSol(le.rec, "vendor_solicitor_contact_id");
          }
        } else if (/^(purchaser|buyer|tenant|lessee)$/.test(st)) {
          const ct = le.rec.contract || (le.rec.contract = {});
          addTo(ct, "buyer_contact_ids", [ce.rec.contact_id]);
          if (sol !== undefined) {
            if (le.update) issue("WARN", "Solicitors on existing listings can't be set by an update: left out", `${le.rec.identifier}: ${sol}`);
            else setSol(ct, "buyer_solicitor_contact_id");
          }
        } else if (st) issue("WARN", "contact_sale_type values that aren't seller or purchaser: contact not linked to the listing", where(r, "contact_sale_type"));
        else {
          // normal on a task / enquiry / inspection / offer row, or when an earlier row already linked the two
          const id = ce.rec.contact_id, linked = (le.rec.vendor_contact_ids || []).includes(id) || ((le.rec.contract || {}).buyer_contact_ids || []).includes(id);
          if (!linked && !Object.keys(OTHER).some((k) => str(r[OTHER[k].ident]))) issue("INFO", "Rows with a contact and a listing but no contact_sale_type: not linked as vendor or buyer", `${ce.rec.identifier} / ${le.rec.identifier}`);
        }
      } else if (str(r.contact_sale_type) && ce && !le) issue("INFO", "contact_sale_type on rows with no listing: nothing to link", ce.rec.identifier);
      for (const k of Object.keys(OTHER)) doOther(k, r, ce, le);
    }

    // ---- partners (one side only; Zenu adds the other)
    const pairs = new Set();
    const relate = (a, b, type, ex) => {
      if (a === b) return;
      const ids = [String(a.rec.contact_id), String(b.rec.contact_id)].sort().join("|");
      if (pairs.has(ids)) return;
      pairs.add(ids);
      if (!type) issue("INFO", "Partner links with no contact_partnership_type: \"Partner\" used", ex);
      addTo(a.rec, "relationships", [{ contact_id: b.rec.contact_id, type: type || "Partner" }]);
      stats.links.partners++;
    };
    for (const [e, cIdent, r] of companyRefs) {
      const co = C.get(cIdent);
      if (!co) issue("WARN", "zenu_company_id values that are neither a Zenu id nor a contact_identifier in the loaded files: left out", where(r, "zenu_company_id"));
      else if (co !== e) { linkCompany(e, co.rec.contact_id, r); stats.companyByIdentifier = (stats.companyByIdentifier || 0) + 1; }
    }
    for (const [obj, key, sIdent, r] of solicitorRefs) {
      const so = C.get(sIdent);
      if (so) { obj[key] = so.rec.contact_id; stats.links.solicitors++; }
      else { delete obj[key]; issue("WARN", "zenu_solicitor_id values that are neither a Zenu id nor a contact_identifier in the loaded files: left out", where(r, "zenu_solicitor_id")); }
    }
    for (const [e, pIdent, type, r] of partnerLinks) {
      const other = C.get(pIdent);
      if (!other) issue("WARN", "Partner identifiers not found in the loaded files: link left out", where(r, "contact_partner_identifier"));
      else relate(e, other, type, `${e.rec.identifier} + ${pIdent}`);
    }
    for (const [k, g] of partnerGroups) {
      if (g.list.length < 2) { issue("INFO", "contact_partnership_id values on one contact only: no partner to link", `${g.list[0].rec.identifier}: ${k}`); continue; }
      for (const o of g.list.slice(1)) relate(g.list[0], o, g.type, `${g.list[0].rec.identifier} + ${o.rec.identifier}`);
    }

    // ---- finish the listings: status, rating, appraisal, contract
    for (const e of L.values()) {
      const rec = e.rec, ct = rec.contract;
      const hasDates = !!(ct && (ct.contract_date || ct.unconditional_date || ct.settlement_date));
      if (e.update) {
        if (e.rating) {
          if (/prospect/.test(low(e.statusRaw || e.statusFile || ""))) issue("INFO", "Pipeline ratings on existing prospects can't be updated (only appraisal motivation): left out", rec.identifier);
          else rec.appraisal = { motivation: e.rating };
        }
        continue;
      }
      if (!e.statusRaw && e.statusFile) { e.statusRaw = e.statusFile; stats.statusFromFile = (stats.statusFromFile || 0) + 1; }
      if (!rec.street_name && !rec.suburb) issue("WARN", "New listings with no address (they only appear in vendor, buyer, note or activity rows): created with just the identifier", rec.identifier);
      const raw = low(e.statusRaw);
      const fallback = hasDates ? (ct.settlement_date ? "Settled" : "Sold") : "Prospect";
      let status;
      if (!raw) status = fallback;
      else if (/prospect/.test(raw)) status = "Prospect";
      else if (/apprais|presentation/.test(raw)) status = "Appraisal";
      else if (/settled/.test(raw)) status = "Settled";
      else if (/^sold$|^sold /.test(raw)) status = "Sold";
      else { status = fallback; issue("INFO", `Timeline statuses that aren't Prospect, Appraisal, Sold or Settled: the empty-status rule was used`, `${rec.identifier}: ${e.statusRaw} -> ${status}`); }
      rec.status = status;
      if (e.rating) {
        if (status === "Appraisal") (rec.appraisal || (rec.appraisal = {})).motivation = e.rating;
        else if (status === "Prospect") (rec.prospect || (rec.prospect = {})).pipeline_rating = e.rating;
        else issue("INFO", "Pipeline ratings on Sold or Settled listings: left out", rec.identifier);
      }
      if (rec.appraisal && rec.lister_staff_names && rec.appraisal.staff_name === undefined) rec.appraisal.staff_name = rec.lister_staff_names[0];
    }

    // ---- drop records the schema would reject, then links to them
    const dropped = new Set();
    for (const e of C.values()) {
      if (!e.update && !e.rec.first_name && !e.rec.last_name && !e.rec.company_name) { dropped.add(e.rec.contact_id); issue("WARN", "New contacts with no first name, last name or company name: left out", e.rec.identifier); }
      if (e.listsOnly) issue("INFO", "Existing contacts (zenu_contact_id) without contact_date_modified = update: only their lists (types, tags, notes, extra emails and phones…) were added", e.rec.identifier);
      if (e.createOnlySkipped) issue("INFO", "Existing contacts: keys that can only be set on a new contact (source, created date, town, contact permissions…) were left out", e.rec.identifier);
    }
    const ok = (id, what, ex) => {
      if (id === undefined || id === null) return false;
      if (typeof id === "string" && dropped.has(id)) { issue("WARN", `Links to a contact that was left out: dropped (${what})`, ex); return false; }
      return true;
    };
    for (const e of C.values()) {
      const rec = e.rec;
      if (rec.company_contact_id !== undefined && !ok(rec.company_contact_id, "company", rec.identifier)) { delete rec.company_contact_id; stats.links.companies--; }
      if (rec.relationships) { rec.relationships = rec.relationships.filter((x) => ok(x.contact_id, "partner", rec.identifier) || (stats.links.partners--, false)); if (!rec.relationships.length) delete rec.relationships; }
    }
    for (const e of L.values()) {
      const rec = e.rec;
      if (rec.vendor_contact_ids) { rec.vendor_contact_ids = rec.vendor_contact_ids.filter((id) => ok(id, "vendor", rec.identifier)); if (!rec.vendor_contact_ids.length) delete rec.vendor_contact_ids; }
      if (rec.contract && rec.contract.buyer_contact_ids) { rec.contract.buyer_contact_ids = rec.contract.buyer_contact_ids.filter((id) => ok(id, "buyer", rec.identifier)); if (!rec.contract.buyer_contact_ids.length) delete rec.contract.buyer_contact_ids; }
      if (rec.contract && !Object.keys(rec.contract).length) delete rec.contract;
      stats.links.vendors += (rec.vendor_contact_ids || []).length;
      stats.links.buyers += ((rec.contract || {}).buyer_contact_ids || []).length;
    }
    const REQ = {
      task: [["subject", "subject"], ["contact_id|listing_id", "contact or listing"]],
      enquiry: [["contact_id", "contact"]],
      inspection: [["contact_id", "contact"], ["listing_id", "listing"], ["start_date", "start date"]],
      offer: [["contact_id", "contact"], ["listing_id", "listing"], ["price", "price"]],
    };
    const out = {};
    for (const k of Object.keys(OTHER)) {
      const keep = [];
      for (const e of T[k].values()) {
        const rec = e.rec;
        if (rec.contact_id !== undefined && !ok(rec.contact_id, k, rec.identifier)) delete rec.contact_id;
        const miss = REQ[k].filter(([keys]) => !keys.split("|").some((x) => rec[x] !== undefined)).map(([, label]) => label);
        if (miss.length) { issue("WARN", `${OTHER[k].list[0].toUpperCase() + OTHER[k].list.slice(1)} the schema needs more for (no ${miss.join(", no ")}): left out`, rec.identifier); continue; }
        keep.push(rec);
      }
      out[k] = keep;
    }

    // ---- the document, keys in a readable order
    const ORDER = {
      contact: ["contact_id", "identifier", "title", "first_name", "last_name", "is_company", "company_name", "company_contact_id", "company_position", "name_on_letters", "legal_name", "types", "tags",
        "email", "extra_emails", "mobile_phone", "work_phone", "home_phone", "fax", "extra_phones", "address_line_1", "address_line_2", "suburb", "town_city", "state", "post_code", "country",
        "allows_text", "allows_email", "allows_letter", "allows_phone", "is_unsubscribed", "subscription_ids", "rating", "source", "primary_staff_name", "secondary_staff_names",
        "last_contacted_date", "created_date", "modified_date", "relationships", "criteria", "notes"],
      listing: ["listing_id", "identifier", "unit_number", "street_number", "street_name", "building_name", "suburb", "state", "post_code", "country", "sale_method", "category", "property_type", "status"],
    };
    const ordered = (rec, order) => { const o = {}; for (const k of order) if (k in rec) o[k] = rec[k]; for (const k of Object.keys(rec)) if (!(k in o)) o[k] = rec[k]; return o; };
    const cAll = [...C.values()].filter((e) => !dropped.has(e.rec.contact_id));
    const cCreate = cAll.filter((e) => !e.update).map((e) => ordered(e.rec, ORDER.contact));
    // an existing record with nothing to change is only a link target: its Zenu id is used, no update is written
    const hasChange = (rec) => Object.keys(rec).length > 2;
    const cUpdate = cAll.filter((e) => e.update && hasChange(e.rec)).map((e) => ordered(e.rec, ORDER.contact));
    const lCreate = [...L.values()].filter((e) => !e.update).map((e) => ordered(e.rec, ORDER.listing));
    const lUpdate = [...L.values()].filter((e) => e.update && hasChange(e.rec)).map((e) => e.rec);
    stats.linkOnly = cAll.filter((e) => e.update && !hasChange(e.rec)).length + [...L.values()].filter((e) => e.update && !hasChange(e.rec)).length;
    const doc = { $schema: "./import.schema.json", schema_version: 1, office_id: officeId };
    const put = (key, create, update) => {
      const o = {};
      if (create.length) o.create = create;
      if (update && update.length) o.update = update;
      if (Object.keys(o).length) doc[key] = o;
    };
    put("contacts", cCreate, cUpdate);
    put("listings", lCreate, lUpdate);
    for (const k of Object.keys(OTHER)) put(OTHER[k].list, out[k]);

    const sk = stats.skippedExisting;
    for (const k of Object.keys(sk)) if (sk[k]) issue("INFO", `Existing ${OTHER[k].list} (the row has ${OTHER[k].zid}) can't be updated: left out`, `${sk[k]} ${OTHER[k].list}`);
    if (stats.emptyRows) issue("INFO", "Rows with no identifiers at all: skipped", `${stats.emptyRows} rows`);

    const counts = {
      contactsCreate: cCreate.length, contactsUpdate: cUpdate.length, companiesCreated: companies.size, listingsCreate: lCreate.length, listingsUpdate: lUpdate.length,
      tasks: out.task.length, enquiries: out.enquiry.length, inspections: out.inspection.length, offers: out.offer.length,
      notes: cAll.reduce((n, e) => n + (e.rec.notes || []).length, 0) + [...L.values()].reduce((n, e) => n + (e.rec.notes || []).length, 0),
      criteria: cAll.reduce((n, e) => n + (e.rec.criteria || []).length, 0),
    };
    const sev = { FAIL: 0, WARN: 1, INFO: 2 };
    const list = [...issues.values()].sort((a, b) => sev[a.severity] - sev[b.severity] || b.count - a.count);
    const check = validate(doc);
    return { doc, counts, stats, issues: list, check, ms: Date.now() - t0 };
  }

  // ------------------------------------------------------------------ schema check (the JSON Schema keywords import.schema.json uses)
  function validate(doc, schema = SCHEMA, cap = 200) {
    const errors = [];
    let total = 0;
    const at = (ref) => ref.replace(/^#\//, "").split("/").reduce((o, k) => o[k], schema);
    const typeOk = (t, x) => t === "null" ? x === null : t === "integer" ? Number.isInteger(x) : t === "number" ? typeof x === "number" && Number.isFinite(x)
      : t === "array" ? Array.isArray(x) : t === "object" ? x !== null && typeof x === "object" && !Array.isArray(x) : typeof x === t;
    const reCache = new Map();
    const re = (p) => { if (!reCache.has(p)) reCache.set(p, new RegExp(p, "u")); return reCache.get(p); };
    function v(s, x, path, out) {
      const start = out.length, ev = new Set();
      const fail = (msg) => out.push({ path: path || "(file)", msg });
      if (s.$ref) { const r = v(at(s.$ref), x, path, out); r.ev.forEach((k) => ev.add(k)); }
      if (s.type !== undefined && ![].concat(s.type).some((t) => typeOk(t, x))) fail(`should be ${[].concat(s.type).join(" or ")}`);
      if ("const" in s && x !== s.const) fail(`should be ${JSON.stringify(s.const)}`);
      if (s.enum && !s.enum.includes(x)) fail(`${JSON.stringify(x)} should be one of ${s.enum.map((e) => JSON.stringify(e)).join(", ")}`);
      if (typeof x === "string") {
        if (s.pattern && !re(s.pattern).test(x)) fail(`${JSON.stringify(x).slice(0, 80)} doesn't match ${s.pattern}`);
        if (s.minLength !== undefined && [...x].length < s.minLength) fail(`is shorter than ${s.minLength}`);
        if (s.maxLength !== undefined && [...x].length > s.maxLength) fail(`is longer than ${s.maxLength}`);
      }
      if (typeof x === "number") {
        if (s.minimum !== undefined && x < s.minimum) fail(`${x} is below ${s.minimum}`);
        if (s.maximum !== undefined && x > s.maximum) fail(`${x} is above ${s.maximum}`);
      }
      if (s.not && v(s.not, x, path, []).ok) fail(`${JSON.stringify(x).slice(0, 80)} isn't allowed here`);
      if (s.allOf) for (const sub of s.allOf) v(sub, x, path, out).ev.forEach((k) => ev.add(k));
      if (s.anyOf) {
        let any = false;
        for (const sub of s.anyOf) { const r = v(sub, x, path, []); if (r.ok) { any = true; r.ev.forEach((k) => ev.add(k)); } }
        if (!any) fail(`needs one of: ${s.anyOf.map((a) => (a.required ? a.required.join("+") : a.$ref ? a.$ref.split("/").pop() : "?")).join(", ")}`);
      }
      if (s.oneOf) {
        let n = 0;
        for (const sub of s.oneOf) { const r = v(sub, x, path, []); if (r.ok) { n++; r.ev.forEach((k) => ev.add(k)); } }
        if (n !== 1) fail(n ? "matches more than one allowed form" : `${JSON.stringify(x).slice(0, 80)} isn't an allowed value (an id, a projected id or null)`);
      }
      if (Array.isArray(x)) {
        if (s.items) x.forEach((it, i) => v(s.items, it, `${path}[${i}]`, out));
        if (s.maxItems !== undefined && x.length > s.maxItems) fail(`has ${x.length} items (at most ${s.maxItems})`);
        if (s.uniqueItems) { const seen = new Set(x.map((it) => JSON.stringify(it))); if (seen.size !== x.length) fail("has the same item twice"); }
      } else if (x !== null && typeof x === "object") {
        for (const k of s.required || []) if (!(k in x)) fail(`is missing '${k}'`);
        if (s.properties) for (const k of Object.keys(s.properties)) if (k in x) { ev.add(k); v(s.properties[k], x[k], path ? `${path}.${k}` : k, out); }
        if (s.additionalProperties === false) for (const k of Object.keys(x)) if (!(s.properties || {})[k]) fail(`'${k}' isn't allowed`);
        if (s.minProperties !== undefined && Object.keys(x).length < s.minProperties) fail("is empty");
        if (s.unevaluatedProperties === false) for (const k of Object.keys(x)) if (!ev.has(k)) fail(`'${k}' isn't allowed`);
      }
      return { ok: out.length === start, ev };
    }
    const all = [];
    v(schema, doc, "", all);
    total = all.length;
    for (const e of all) { if (errors.length >= cap) break; errors.push(e); }
    return { ok: total === 0, total, errors };
  }

  /** "contacts.create[3].email" -> the record's identifier, for the check report */
  function errorRecord(doc, path) {
    const m = path.match(/^(\w+)\.(create|update)\[(\d+)\]/);
    if (!m) return "";
    const rec = ((doc[m[1]] || {})[m[2]] || [])[+m[3]];
    return rec ? rec.identifier || "" : "";
  }

  const api = { convert, validate, errorRecord, MAP, SCHEMA, STEPS, VALUE_RULES, ASSUMPTIONS, parseDate };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DarisJsonImport = api;
})(typeof window !== "undefined" ? window : globalThis);
