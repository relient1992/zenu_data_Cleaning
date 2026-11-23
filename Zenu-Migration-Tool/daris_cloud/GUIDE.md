# DARVIS Guide

DARVIS is the claude.ai version of DARVIS (Daryl's Automated Reasoning & Virtual Intelligence
System) for Zenu CRM migrations. It has four tools: Contact cleaning, Migration, Import Prep and the Roadmap,
plus the DARVIS chat.

## Contact cleaning: two handlers
Both use the same rules: name splitting, phone and email cleaning, and address cleaning, ported
from Daryl's macros (Contact_name_splitting.bas, Clean_contactdetails.bas and the Contact Details
Cleaning workbook). Only the column names differ between CRMs.
- **Agentbox contacts**: upload Agentbox's contact.csv with contact_category.csv,
  contact_x_agent.csv and agent.csv. Columns are mapped by Agentbox's fixed names. Contact Type and
  Tags come from contact_category.csv (categories on the Agentbox type list are types, the rest are
  tags). Team member 1 is the contact's agent from contact_x_agent.csv (primary_owner first), as a
  name from agent.csv. Comments marked [base64] are decoded into CONTACT_NOTES.
- **General cleaning**: any other CRM's export. Columns are mapped in this order: what the team
  confirmed before (LEARNED), the General Field Names guide, the Zenu field names, then Claude for
  anything left (marked AI, always checked by a person before running).

## Steps
1. Drop the files in the data bay (or load an example). Agentbox files are recognised by their headers.
2. Check the column mapping review. Change any dropdown; a column can only be used once.
3. Set the ID date tag (default today, ddmmyyyy). IDs become <id>_<tag>_c1, _c2 ... for split people
   and <id>_<tag>_c for one person. Clear it for plain 1_c1, 1_c2.
4. Run. The Summary lists every main contact first, then the split people at the bottom.
5. Export the workbook: Summary, Name SPlitting, Contact Details, Address Cleaning, Column Mapping,
   Checks and Issues.

## Change values before export
After a run, "Change values before export" (under the issues) changes values in the Summary:
- Set every cell to a value (e.g. CONTACT_TEAM_MEMBER_1 = one agent for all records).
- Fill blank cells with a value.
- Replace a value: cells equal to it (ignoring case and outer spaces) get the new value, or blank.
- Clear the column.
- Combine columns into this one: pick up to 4 columns in order - Summary columns or columns of the uploaded file
  (listed under "Uploaded file", even ones that aren't mapped, e.g. Comments) - and a separator (type 
 for a new
  line). Blanks are skipped, so there are no stray separators; "Put each column's name before its value" gives
  e.g. "Comments: Prefers email". A split person gets the values of the row they were split from.
Each change can be limited to main contacts or split people, and to rows where another column is, contains,
is blank or isn't blank. The preview, Export workbook and Export contact_cleaned.csv all use the changed
values; the workbook adds a Changes sheet listing each change and how many cells it changed. Changes stay
applied if the cleaning is re-run, until undone (Undo, Undo all changes) or Start new. The ID columns
(CONTACT_IDENTIFIER, Raw ORIG CONTACT_IDENTIFIER, partner and partnership IDs) can only have single values
replaced, so records stay linked. The Name SPlitting, Contact Details and Address Cleaning sheets keep the
cleaned values. In the chat: "set CONTACT_TEAM_MEMBER_1 to <name> for all records", "undo changes", or ask
in your own words.

## Couples in separate rows (spouse ID)
Some CRMs export each partner as their own row with a column holding the partner's contact ID (e.g.
spouse_contact_id). Map it to "Spouse / partner's contact ID" (DARVIS finds it by name). Both rows then get
each other as CONTACT_PARTNER_IDENTIFIER, one shared CONTACT_PARTNERSHIP_ID (<first id>_<second id>_r, like a
split couple) and CONTACT_PARTNERSHIP_TYPE = Partner, so they stay related after import. A link works when only
one row has the spouse ID. Left unlinked (with a WARN or INFO in Issues): a spouse ID that isn't a contact ID in
the file, one pointing at a contact whose own spouse ID is someone else or who is already linked, a row that
points at itself, and rows that already split into several people (e.g. "Glen & Leanne"), whose people are
linked to each other instead. Each linked row gets a review note on the Name SPlitting sheet.

## Contact fields (the Summary)
The Summary has the 58 Zenu contact fields in import order: contact_identifier, zenu_contact_id, title, first
name, surname, is_company, company name, name on letters, legal name, company position, type, tags,
subscriptions, mailing address line 1, line 2, suburb, town/city, state, country, postcode, email, mobile, phone
work, phone home, fax, allow contact via text / email / letter / phone, unsubscribe from marketing, children's
names, enquiry source, rating, partner identifier, partnership id, partnership type, last contact date, date
created, date modified, team members 1-15, sale type, zenu_solicitor_id, zenu_company_id, notes. They sit
between the helper columns Indicator, Contact Count, Raw ORIG CONTACT_IDENTIFIER (first) and Contact Name (last).
Names, phones, emails, addresses, permissions and partner fields are cleaned by the rules below; the other fields
are carried across from the mapped column as they are (trimmed; last contact, created and modified dates as
dd/mm/yyyy). Split people share the household's fields, except zenu_contact_id, which stays with the main
contact (a split person is a new Zenu contact). contact_cleaned.csv for migrations keeps its 37 columns.

## Permission and date settings (mapping review)
Permission columns (Text / SMS, Email, Letter / mail, Phone permission and Newsletter / marketing) show a
setting under the column: what the column's values mean, and what an empty cell means.
- 1 / Yes = allowed (e.g. is_allow_text), 1 / Yes = do not contact (e.g. do_not_sms), filled in (e.g. an
  opt-out date) = do not contact, or filled in (e.g. an opt-in date) = allowed. For the newsletter: subscribed /
  unsubscribed, and "filled in (e.g. an unsubscribe date) = unsubscribed".
- Empty cells: not allowed, allowed, or leave empty.
DARVIS guesses the setting from the column name and values (marked GUESSED): names with allow / opt in mean
allowed; do not / opt out / unsubscribe / no sms / dnc mean do not contact; a date column means filled in.
The live line shows how many rows become TRUE and FALSE in the Zenu field (CONTACT_ALLOW_CONTACT_VIA_... or
CONTACT_UNSUBSCRIBE_FROM_MARKETING). Values that aren't yes / no (true, 1, yes, y / false, 0, no, n) are treated
as not allowed and reported in Issues.
Contactable (e.g. is_contactable): when it is 1 / Yes and a permission's own cell is blank, that permission
becomes allowed - CONTACT_ALLOW_CONTACT_VIA_TEXT / EMAIL / LETTER / PHONE TRUE and CONTACT_UNSUBSCRIBE_FROM_MARKETING
FALSE. A filled permission cell always wins (is_allow_text = 0 stays FALSE; an unsubscribe date stays unsubscribed).
Permissions that aren't in the export at all are filled too. Contactable 0 or blank changes nothing (the
empty-cell setting applies). Issues says how many cells were filled this way.
Date fields (Date created, Date modified, Last contact date) have a Format: date only (dd/mm/yyyy), date and time
(dd/mm/yyyy hh:mm) or with seconds. DARVIS reads ISO dates, dd/mm/yyyy (with time and AM/PM), 16 Jul 2024,
Jul 16 2024 and database stamps like {ts '2024-07-16 09:42:03'}. With a time format, dates without a time get
00:00. Values that aren't dates are left as they are and reported in Issues. The workbook's Column Mapping sheet
lists each setting. In the chat: "is_allow_text means allowed", "keep the time on date created".

## History (saved runs on your PC)
With the DARVIS bridge (DARVIS open in the Claude desktop app), every Contact cleaning run is saved on the PC
automatically, in the Migration Tool's client_migrations/_contact_cleaning folder: the uploaded file(s) exactly as
they were, plus the handler, sheet, column mapping, ID tag, permission / date settings and value changes. Later
runs and value changes on the same files update the same saved run; new files start a new one. The History panel
(left of the Contact cleaning tab) lists them newest first with a search box: Open reloads the file, puts back the
mapping and settings and runs it again - the same Summary as before - so you can re-export, change values, ask the
chat about it, or change the mapping and run again. Delete asks to confirm and removes that saved run's folder.
Example data is never saved. In the chat: "show history".

## Results and checks
- Every phone value is accounted for: placed, skipped as a duplicate in the same row, or invalid.
- Invalid numbers stay in the Invalid columns and are added to CONTACT_NOTES ("Invalid Numbers: ...").
- "Email Okay?" is NO when CONTACT_EMAIL_ADDRESS holds several emails or odd characters.
- Two mobiles or emails in one cell of a split record: person 1 gets the first, person 2 the second
  (a DARVIS rule; the workbook gives everyone both). Leftovers stay with person 1.
- Partners use their own Mobile 2 / Email 2 columns when mapped; home, work, fax, address and notes
  are shared.
- A suburb is split out of address line 1 only when it is in the AU suburb list and the street end
  is clear (a comma, a state or postcode, a street type such as St / Rd / Ave, or a PO Box). A postcode
  is only filled when the address itself contains it.
- A blank state is filled from the postcode (Australian addresses only: country blank or Australia),
  whether or not there is a street: from the AU suburb list when the postcode is in one state; for the 15
  border postcodes in several states (e.g. 2620 NSW / ACT) the suburb decides, otherwise the state stays blank
  (WARN). Postcodes not in the list (e.g. PO Box postcodes) use Australia Post's postcode ranges. A state that
  is already there is never changed. Each fill is noted on the Address Cleaning sheet.
- Issues: FAIL must be fixed before import (for example a duplicate Contact ID or phones that don't
  reconcile). WARN is worth a look. INFO is context.
- Agentbox differences from the workbook, on purpose: the workbook's Summary looked up Type, Tags,
  Team member, Notes, Source, Name on letters and dates by the tagged ID and left them blank; DARVIS
  joins on the raw contact_id. Comments are decoded as UTF-8 and plain-text comments are kept.

## Migration (runs on Daryl's PC)
The Migration tab runs the Zenu Migration Tool's own engine (the same code as the classic tool's
Initialize Migration) on Daryl's PC through the DARVIS bridge, a small local connector registered in
the Claude desktop app as "daris_local". The client's export never leaves the PC. It only works when
DARVIS is open in the Claude desktop app on that PC; only the artifact's owner can use it.
1. Laptop bridge: shows Connected when the bridge answers.
2. Client export folder: browse the PC (inside the user folder) and press Use this folder. DARVIS lists
   the CSV files and guesses the CRM from their names (Agentbox: contact.csv, contact_x_agent.csv,
   note_x_contact.csv; Eagle: agents.csv, addresses.csv, notes.csv; VaultRE: PropertyLifeHistory.csv,
   Owners.csv, Landlords.csv; Rex: feedback_contacts.csv, note_contacts.csv, match_profiles.csv).
3. Client name (required): the agency's name, saved with the job (job_meta.json) so it can be found
   later. Source CRM: Agentbox, Eagle Database, VaultRE, Rex, Zenu Office Transfer, Generic Data Clean /
   Import, or Mailing Address to Prospect / Ownership. Agentbox, Eagle and VaultRE read
   contact_cleaned.csv: run Contact cleaning first, press Export contact_cleaned.csv and save it into
   the export folder.
4. Rules (matrices): the JSON from the classic tool's Export JSON Config. Tick the matrices to execute
   (Contact Notes, Appraisal, Prospect, Enquiry, Inspection, Task ...). Save to team library shares
   the rule set with everyone using DARVIS.
5. Options: split rows per output file (default 500,000; 0 = no split) and Top-up (only records new
   since the previous export; needs the previous export folder).
6. Run on my laptop: the job is created in client_migrations (job_xxxxxxxx) with live progress and
   the log. When it finishes, the Zenu_..._Final.xlsx files are listed; Open job folder shows them.
7. Run reconciliation: checks source vs output counts, duplicates, orphans and dates and writes
   Reconciliation_Report.xlsx in the job folder (FAIL must be fixed, WARN is worth a look).
8. Change values in the output files (on a finished job): set every cell to a value, fill blank cells,
   replace a value, clear a column or combine columns (up to 4, with a separator; blanks skipped), in all output
   files that have the column(s) or in one file, optionally
   only where another column is / contains / is blank / isn't blank something. Add the changes, then
   Write changed files: DARVIS writes copies of every output file into the job's "With changes" folder (same
   file names, plus DARIS_Changes.xlsx listing each change and the cells changed per file). The original
   output files are never changed; only the changed column's cells differ in the copies. Writing again
   replaces the copies with all the current changes; Undo all removes the copies. ID columns (identifier,
   *_id) can only have single values replaced.
9. Combine output files into one workbook (on a finished job, including older jobs opened from Recent jobs):
   tick the output files (All / None), optionally name the workbook, press Combine. DARVIS writes ONE workbook
   with each file on its own tab (named after the file without Zenu_ and _Final) plus a Contents tab (tab,
   source file, rows, columns) into the job's "Consolidated" folder. Values and number formats are copied as
   they are; the output files aren't changed. When the job has "With changes" copies you can combine those
   instead. Earlier combined workbooks are listed; Open Consolidated folder shows them.
Recent jobs lists the PC's jobs from DARVIS and the classic tool, each with its client name; Find a
client searches by client name, job id or CRM. Older jobs show the client from the classic Job Tracker
when it links the job id, otherwise "No client name": open the job and press Save name. Start new only
clears the screen; jobs are never deleted. The chat can check the bridge, find a client's jobs, explain
a job (status, log, reconciliation) by job id or client name, name or rename a job's client, search a
log, run reconciliation and start the run that is set up in the Migration tab when asked.

## Search & Export (look up records in any files and export them)
A tab for searching, reviewing and exporting information from any files: a client's export, a migration job's
output files, a report. Load CSV or Excel files, or a whole folder ("Load a folder", or drag a folder onto the drop
area; sub-folders are read too). Each CSV, and each sheet of an Excel file, becomes one table. DARVIS guesses each
table's ID column (filled and unique, e.g. contact_id); change it under the table if it's wrong.
- Find: type one or more values (one per line or comma-separated): IDs, names, emails, any value. "Whole value"
  matches the whole cell; "Part of a cell" matches inside it. Column limits the search to one column.
- Look in = Every file: shows every row in every file that holds the value. Where the value is a record's own ID
  (e.g. contact 1001 in contacts), its ID and name columns are put on the matching rows of the other files.
- Look in = one file (e.g. contacts): finds the rows there (in its ID column first, then any column; empty =
  every row) and attaches the linked rows of the other files, e.g. the contact's properties and notes. Each attached
  row starts with the found row's ID and name columns (contacts.contact_id, contacts.first_name ...); choose
  which under "Put these columns on the attached rows". DARVIS finds the links itself (the same column name, a
  column named after the file such as contact_id or Owner Contact ID, or matching values) and shows each one,
  e.g. contacts.contact_id = properties.contact_id; pick another column there if it's wrong. It also works the
  other way: find a property and attach its contact.
- Review: each result table shows up to 100 rows; "Columns in the export" picks the columns (default: those
  with data).
- Export results (.xlsx): one sheet per file with all the matching rows (up to 50,000 per file), plus a Request
  sheet saying what was asked, how each file was linked and when. Blank cells are truly empty.
- Chat: "find 1001" (or several values) and "first name of 1001" are answered in the page when this tab is open;
  "export" saves the results; "load the search example" loads 3 made-up files. Requests like "1001 with the contact
  name and properties attached" or "contacts in VIC with their properties" go to Claude, which uses the explore
  tool to run the search in the tab and can export it. Claude gets file and column names and the counts; row
  values only when "Let Claude read the rows I ask about" is ticked.
- The files stay in the page (never uploaded) until you reload it or press Clear / say "clear search".

## Import Prep
Replicates the Import Prep macro's "Internal Output". Give it the input file (xlsx or csv) and the
"Compare To" template (its row 1 holds the Zenu column names). For every sheet (except Document
History, Instructions and Summary) it finds the header row (a cell named contact_identifier,
property_identifier, contact_id, property_id, zenu_contact_id or zenu_property_id; otherwise row 1),
puts the columns in the template's order (template columns missing from the input stay blank),
formats dates as dd/mm/yyyy (inspection_start_date and inspection_end_date also get HH:MM), removes
non-ASCII and special characters (a double quote becomes a single quote; ~ ` | and others are
removed) and writes one CSV per sheet, split every 15,000 rows. Several files come as a ZIP with a
report.

## Roadmap
The team's CRM build progress (Agentbox, Eagle, VaultRE, ZenuTransfer, Rex ...), shared by everyone
who opens DARVIS. 100% means Completed.

## Privacy
Files are read and cleaned inside the page and never uploaded. Claude only sees column names,
counts and issue text; sample values are sent as shapes (Aaaaa 9999) unless "Send real sample
values" is switched on. Column names that look like data (an email, a phone number) are never sent
or saved. Team memory stores only column name -> role.

## Combining columns from the chat
Ask in your own words, e.g. "combine CONTACT_NOTES and the Comments column into CONTACT_NOTES, separated by |" or
"in the job's zenu_contact file put first name + surname into comments". DARVIS applies it and shows the first
changed rows (before -> after) under its reply; the values are drawn in the page and only sent to Claude when "Let
Claude read the rows I ask about" is on. Then say "export" (Contact cleaning) or use the With changes copies /
Combine output files (migration).

## Asking about the data
- Lookups answered in the page (nothing is sent to Claude): "first name of 10509472_09112026_c", "email for
  10509472", "what is the mobile for <ID>", "show contact <ID>". The ID can be the Summary's CONTACT_IDENTIFIER,
  the tagged raw ID, or the export's own ID (which shows every person split from that row). Before the cleaning
  has run, lookups use the uploaded file and its column names. When the ID isn't in Contact cleaning (or nothing
  is loaded there), DARVIS searches the open migration job's files (else the newest job) through the bridge: first
  contact_cleaned.csv, then the Zenu output workbooks, then the other export CSVs copied into the job,
  and shows the matching rows, e.g. "first name of <ID>" -> contact_first_name from zenu_contact.xlsx. Wordings
  like "can you check the first name of contact identifier <ID>" work too.
- Other questions ("how many contacts have no email?", "which contacts are in Tooradin?", "how many per state?")
  go to Claude, which searches the rows with query_records. Claude always gets the number of matching rows; it
  sees the values themselves (up to 20 rows, or counts per value) only when "Let Claude read the rows I ask
  about" is ticked in the Privacy panel. It is off by default. For a migration job's output files Claude uses the
  migration tool's find action, under the same switch.

## Questions beyond look-ups (top, highest, totals, per value)
Ask in your own words, e.g. "in my last job's zenu_buyer, which property_identifier has the highest property_sold_price?",
"top 10 vendors by price", "how many appraisals per suburb?", "average sold price per state", "how many buyers sold over
2,000,000?", "contacts with no email per state". Claude uses the query_data tool, which works on:
- a migration job's files on the PC (an output workbook such as zenu_buyer.xlsx, or an export CSV such as
  contact_cleaned.csv), through the bridge - the open job, else the newest, or name the job or client;
- a Search & Export file;
- Contact cleaning (the cleaned Summary, or the uploaded file before the run).
It can filter (is, not, contains, starts with, blank, filled, greater / less than - numbers or dates such as
17/06/2026), sort highest or lowest first and keep the top N (blanks left out), count, and give the total, average,
minimum or maximum of a column (min / max also show that row), overall or per value of another column. Prices like
$1,200,000 are read as numbers. The answer is drawn as a table under Claude's reply. Claude sees the values only when
"Let Claude read the rows I ask about" is ticked; otherwise it gets the counts and points you to the table. A
follow-up (e.g. "what is that property's address?") is another question: the address is in zenu_appraisal or
zenu_prospect (property_full_address), not in zenu_buyer.

Answers can combine files (a join): "in my last job, match zenu_buyer's property_identifier to listings.csv id and give
the total sold price per state". DARVIS brings the matching file's columns (here listings.csv state) onto each row by the
key, then filters, sorts or groups on them. It works on every file of a job (the Zenu outputs AND the export CSVs copied
into it), on any folder on the PC (e.g. the export folder picked in Migration, before migrating), on the Search & Export
files and on Contact cleaning's uploaded files and Summary. Each answer says how many rows found a match in the other
file (e.g. "31 of 263"): rows without one count as (blank). When the first match of a key is used and the other file has
several rows for it, that's reported too. Ask "which files and columns does my last job have?" if you're not sure which
column holds the key.

## Chat commands that run straight away
- "clean agentbox contacts", "general cleaning" - switch handler
- "load the agentbox example", "load the general example"
- "run it", "run the cleaning" - run with the current mapping
- "export" - save the workbook
- "postcode of Tooradin", "is Toradin a real suburb?" - suburb list
- "open import prep", "open roadmap", "open contact cleaning", "open migration", "open search & export"
- On Search & Export: "find <ID or value>", "export", "load the search example", "clear"
- "export contact_cleaned" - save the Summary as contact_cleaned.csv for a migration
- Anything else goes to Claude, which can read the page state, change the mapping, run, export,
  look up suburbs and Zenu fields, search these guides and update the roadmap.
