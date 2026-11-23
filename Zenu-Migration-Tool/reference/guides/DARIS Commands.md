# DARIS Commands

Everything you can type in DARIS, and what it does. DARIS reads this guide too, so you can
also just ask it "what command do I use to …?".

---

## How DARIS listens
1. **Commands run real tools.** DARIS looks for the key words below anywhere in your message
   ("clean these up please" works the same as "clean the contacts").
2. **Polite requests are commands:** "can you…", "could you…", "please…", "also…" followed by an
   action word (split, separate, clean, tidy, merge, consolidate, combine, format, fix, parse, run,
   reconcile, process).
3. **Questions go to the AI** — a message ending with `?`, or starting with why / how / what /
   which / should / is / are / does / explain / tell me. The AI only *answers* (from your files, the
   last run and the guides); it never changes data.
4. Anything that isn't a command is also answered by the AI. If you ask for something DARIS
   can't do yet, it will say so.

---

## Commands

### Files
| You type | What happens |
|---|---|
| *(drop files / paperclip / Ingest Files)* | Adds `.xlsx` `.xls` `.csv` to the session and profiles them: rows, header row, how many columns match Zenu |
| `show files` · `what's in the files` · `analyse the files` · `inspect` | Shows the file profile card again |

### Contact cleaning — two handlers
Both use the same cleaning rules (names, phones, emails, addresses) and the same two steps:
**1 — mapping review** (nothing runs yet), **2 — Run** on the card.

| You type | Handler |
|---|---|
| `clean agentbox contacts` · `agentbox` · `clean the agentbox files` · `run agentbox` | **Agentbox.** Upload Agentbox's `contact.csv` with `contact_category.csv`, `contact_x_agent.csv` and `agent.csv`. Columns are mapped by Agentbox's own names; plus **Contact Type / Tags** (categories split against the Agentbox type list), **Team member 1** (the contact's agent, primary owner first, as a name from agent.csv) and **comments** (`[base64]` decoded into CONTACT_NOTES). The card shows which of the 4 files were found. |
| `general cleaning` · `run general cleaning` · `clean it as general` · `use the general handler` | **General** — any other CRM's contact export. Same name / phone / email / address cleaning as Agentbox; only the column names differ, so DARIS maps them: what you confirmed before → the names in `General Field Names.md` → the matcher → **the AI** for anything still empty (marked **AI** on the card). Agentbox lookup files (category / agent links) are skipped. |
| `clean the contacts` · `clean these up` · `tidy the data` | Picks **Agentbox** when an Agentbox `contact.csv` is uploaded (and says so), otherwise **General**. |

Agentbox differences from the workbook (on purpose):
- The workbook's Summary looks Type / Tags / Team member / Notes / Source / Name on letters /
  dates up by the *tagged* ID (`123_05112026`) in tabs that hold the raw ID (`123`), so those
  columns came out **blank**. DARIS joins on the raw `contact_id`, so they're filled.
- Comments are decoded as UTF-8 (accents survive) and a plain-text comment is kept — the
  workbook's decoder garbles accents and drops plain-text comments.

| You type | What happens |
|---|---|
| *(Run … button on the card)* | Uses the mapping *you* confirmed: names split into up to 5 people, phones formatted and routed (mobile / home / work / fax / invalid), emails checked, addresses split into street / suburb / state / postcode. Download the workbook from the result card. |
| `split the names` · `format the phones` · `format the numbers` | Same — shows the mapping review |
| `split the address and suburb` · `separate the suburbs` · `fix the addresses` · `parse the mailing address` | Same — shows the mapping review |
| `run it` · `go ahead` · `do it` · `process the files` | Same — shows the mapping review (press Run on the card) |
| `show cleaning results` · `view the cleaning` | Shows the last result card again (does **not** re-run) |

On the review card:
- **Dropdowns** — pick a different column, or "— not mapped —". A column can only be used once.
  Sections with nothing mapped (Second person, Permissions …) start collapsed — open them to map.
- **ID date tag** — defaults to today (ddmmyyyy). IDs become `<id>_<tag>_c1`, `_c2` … for split
  people and `<id>_<tag>_c` for a single person. Clear it for plain `1_c1`, `1_c2`.
- **AI** chip (amber) — the AI picked this column from its sample values. Check it; changing the
  dropdown removes the chip.
- **LEARNED** chip (green) — you confirmed this column on an earlier run.
- **Remember my changes** — on Run, every column on a General card is remembered for its role
  (including second person and do-not flags), so the next export from the same CRM maps itself.
- To teach DARIS a CRM's names up front, add them to `reference/guides/General Field Names.md`
  (picked up on the next "general cleaning", no restart).

The output workbook (`DARIS_Agentbox_…xlsx` or `DARIS_General_…xlsx`, same tabs as the
*Contact Details Cleaning* workbook):
- **Summary** — the import sheet. One row per person: **all main contacts first, then every split
  person at the bottom**, e.g. `1_c1 Daryl Roallos` near the top and `1_c2 Kim Roallos` below the
  last main contact. Partners get their own Mobile 2 / Email 2 if mapped, otherwise the main
  contact's; home / work / fax / address / notes are shared.
- **Two mobiles or emails in one cell** of a split record (`0400 111 222 | 0400 333 444`): person 1
  gets the first, person 2 the second, and so on (DARIS rule — the General.xlsb gives everyone
  both). Extra values beyond the number of people stay with person 1; a single value is shared;
  a one-person record keeps all its values. The Contact Details tab still shows the cell as the
  workbook does.
- **Name SPlitting**, **Contact Details**, **Address Cleaning** — each step, column for column like
  the workbook's tabs (invalid numbers in the Invalid columns and in CONTACT_NOTES).
- **Column Mapping**, **Checks** (every count, phones reconciled), **Issues** (FAIL / WARN / INFO).

General cleaning works one sheet at a time — several uploaded sheets get one card each. Agentbox
is always one card: contact.csv, with the other three CSVs linked in.

Address rules: a suburb is only split off when it's in your AU suburb list *and* the street end is
clear (comma, line break, state/postcode, a street type like St/Rd/Ave, or a PO Box). **State and
postcode are only filled when the address itself contains them** — nothing is added from the list.

### Review the results
| You type | What happens |
|---|---|
| `show mapping` · `columns` · `headers` · `suggestions` | The mapping review card again |
| `show issues` · `problems` · `errors` · `warnings` | Every FAIL / WARN / INFO from the last run |

**Switched off:** Smart merge & reconcile (`merge the files`, `consolidate`, `combine`, `reconcile`)
and duplicate scanning (`show duplicates`). DARIS says so and shows the mapping review instead. The
classic tool's Consolidate button still merges files. A Contact ID used twice is still a FAIL.

### Teach DARIS (remembered for every future client)
| You type | What happens |
|---|---|
| `map Cell Ph to contact_mobile` | That header always maps to that Zenu field. Also: `treat X as …`, `set X to …`, `map X -> …` |
| `ignore Fav Colour` · `don't map X` · `never map X` · `keep X as-is` · `keep X unmapped` | That header is always kept under its own name |

The Zenu field name must be exact (e.g. `contact_mobile`) — DARIS suggests the closest one if not.
Placeholder names like `Column_3` (files without headers) can't be taught.

### Suburbs
| You type | What happens |
|---|---|
| `postcode of Tooradin` · `postcode for Richmond VIC` · `state of Mount Coolum` | Looks it up in your AU suburb list (spelling fixes applied) |
| `check suburb Mt Coolum` · `look up suburb X` · `verify suburb X` | Same |
| `is Toradin a real suburb?` | Same — with a "did you mean…" if it isn't |

### Reference library
| You type | What happens |
|---|---|
| `show references` · `show guides` · `list lookups` · `show the dictionary` · `what references do you have` | Everything loaded from the `reference` folder |

### Help
| You type | What happens |
|---|---|
| `help` · `what can you do` · `commands` | Short list of commands (this guide is the full version) |

### Questions (answered by the AI)
Examples that work well:
- `is it safe to import?` · `which columns need review?` · `why are there invalid numbers?`
- `how do we split couples?` · `what happens to a second fax number?` · `what titles does Zenu accept?`
- `what does contact_partnership_type mean?`

Topic questions also show the matching card (e.g. "why are there issues?" shows the
issues card *and* an explanation).

---

## Buttons
- **Protocols 01–07** (left panel): Agentbox contacts · General cleaning · Column mapping ·
  Cleaning results · Issue report · Reference library · Capabilities.
- **Run Agentbox cleaning / Run General cleaning** (mapping review card): runs with the mapping on the card.
- **Export** (result card): downloads the workbook.
- **Mic**: speak instead of typing (Chrome/Edge speech service). **Speaker**: read replies aloud.
- **Gear**: AI engine — Local AI, Claude or ChatGPT.
- **+ New session**: start fresh (files and chat of the old session stay in
  `client_migrations/_daris/sessions/` until you delete them).

---

## Tips
- **One action per message.** "clean then show issues" shows the mapping review only — ask for
  the issues after the run.
- **A command word anywhere runs it.** "the clean phones look wrong" would re-run cleaning; ask it
  as a question instead: "why do the phones look wrong?"
- **Nothing is written until you press Run** on the mapping review card.
- **Re-uploading a DARIS workbook is fine** — only its data sheet is read.

## Not available yet
These are answered honestly with "DARIS can't do that yet":
- Filtering or exporting only some rows ("only VIC contacts", "only office A")
- Changing one rule for one run from chat ("leave Dr blank this time")
- Multi-step requests in one message
- Editing individual records, translating, writing emails or code
