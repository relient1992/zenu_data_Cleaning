# DARIS — Daryl's Automated Reasoning & Virtual Intelligence System

Data-cleaning engine for Zenu migrations. Runs on the Dell XPS 13 9310 (i7-1185G7,
16 GB RAM, Iris Xe, Windows 11) with a local AI by default; Claude or ChatGPT can be
switched on in Settings when a stronger model is needed. Builds on the existing
**Consolidate Excel** and **Reconciliation** logic in `main.py` / `reconciliation.py`.
UI: `daris.html` (chat assistant) — the classic UI (`index.html`) is unchanged apart
from a **DARIS AI** button and a DARIS mode in Consolidate Excel.

## Why a layered "AI", not just an LLM

A laptop CPU runs a 3B-parameter model at roughly 8–15 tokens/sec. Asking an LLM
about every cell of a 200k-row export would take days. So DARIS uses the cheapest
layer that can answer each question, and only escalates the ambiguous ones:

```
                    ┌───────────────────────────────────────────┐
  Excel / CSV ───►  │ 1. INGEST     header-row detection,       │
  (many files)      │               every sheet, CSV + XLSX     │
                    └──────────────────┬────────────────────────┘
                                       ▼
                    ┌───────────────────────────────────────────┐
                    │ 2. PROFILE    sample values → semantic    │  pure Python
                    │               type (email/phone/date/...) │  (instant)
                    └──────────────────┬────────────────────────┘
                                       ▼
                    ┌───────────────────────────────────────────┐
                    │ 3. MATCH HEADERS → Zenu schema            │
                    │   a. Memory   (your past confirmations)   │  SQLite
                    │   b. Exact / alias (zenu_matcher.js list) │  dictionary
                    │   c. Fuzzy + type-compatibility score     │  difflib
                    │   d. Local LLM for the grey zone only     │  Ollama (opt.)
                    └──────────────────┬────────────────────────┘
                                       ▼
                    ┌───────────────────────────────────────────┐
                    │ 4. CONSOLIDATE  align columns across      │
                    │                 files, trace every row    │
                    └──────────────────┬────────────────────────┘
                                       ▼
                    ┌───────────────────────────────────────────┐
                    │ 5. DETECT DUPES  email / phone / name+pc  │  union-find
                    │                  / identifier collisions  │  (no rows dropped)
                    └──────────────────┬────────────────────────┘
                                       ▼
                    ┌───────────────────────────────────────────┐
                    │ 6. RECONCILE  rows in = rows out,         │
                    │               filled cells per column in  │
                    │               = out, PASS / WARN / FAIL   │
                    └──────────────────┬────────────────────────┘
                                       ▼
                    ┌───────────────────────────────────────────┐
                    │ 7. EXPLAIN    LLM narrative (or template) │
                    └───────────────────────────────────────────┘
                                       ▼
            consolidated .xlsx  (Consolidated, Column Mapping, Reconciliation,
                                 Column Check, Duplicates, Issues, DARIS Summary)
```

### The "learning" loop
Every mapping you confirm (`POST /api/daris/learn`) is stored in
`daris/daris_memory.db`. Next migration, that header is matched instantly with
100 % confidence. LLM answers are cached there too (lower confidence) so the model
is never asked the same question twice. This is how DARIS gets smarter per client
without any model training.

## Hardware-fit model choice (Ollama)

| Model (Ollama tag) | RAM used | Speed on i7-1185G7 | Use |
|---|---|---|---|
| `qwen2.5:3b` (default) | ~2.5 GB | ~10–15 tok/s | header mapping, summaries |
| `llama3.2:3b` / `phi4-mini` | ~2.5–3 GB | ~10 tok/s | alternatives |
| `qwen2.5:7b` | ~5 GB | ~4–6 tok/s | higher accuracy, slower |

Leave 16 GB headroom for pandas: a 3B model + a 500k-row workbook fits comfortably.
Set `DARIS_MODEL` to switch models; `OLLAMA_HOST` if not on `127.0.0.1:11434`.
DARIS works with **no LLM installed** — the LLM only improves grey-zone matches
and writes the narrative summary.

### Measured on this laptop (Nov 2026, Ollama 0.40.13, `qwen2.5:3b`)
- ~5–8 s per grey-zone header question, ~30 s for the narrative summary.
- Header picks with sheet context: right on ~5 of 8 hard cases — no better than the
  rules' own suggestion. As a yes/no verifier it scored 6–9 of 14 (near chance).
- **Decision:** LLM picks are shown as *suggestions* (`suggested_by = llm` in the
  Column Mapping tab) and never rename a column on their own. Set
  `DARIS_LLM_AUTO_MAP=1` only after a bigger model (e.g. `qwen2.5:7b`) scores well
  on `verifier_eval`-style checks.
- Every answer, including "no field fits", is cached in `daris_memory.db`, so a
  repeat export asks the model nothing (mapping: 18 s → 4.5 s on 15k rows).

## Modules (`daris/`)

| File | Role |
|---|---|
| `knowledge.py` | Loads the Zenu field list from `zenu_schema.js` and aliases from `zenu_matcher.js` (single source of truth shared with Smart Mapper) |
| `profiler.py` | Semantic type detection from sample values |
| `memory.py` | SQLite store of learned header → field mappings (`daris_memory.db`, git-ignored) |
| `llm.py` | Shared AI tasks (column choice, summaries) + local Ollama provider + `get_llm()` |
| `provider_claude.py` | Claude via the official `anthropic` SDK (optional) |
| `provider_openai.py` | ChatGPT via the official `openai` SDK (optional) |
| `settings.py` | Which AI to use, keys, privacy switches (`daris_settings.json`, git-ignored) |
| `privacy.py` | `shape()` / `redact()` — what cloud models are allowed to see |
| `references.py` | Reads `reference/` — suburbs, spelling fixes, Zenu dictionary, guides |
| `matcher.py` | Layered header matcher with confidence + method |
| `consolidator.py` | Ingest → align → dedupe → reconcile → (clean) → report |
| `contact_cleaning.py` | Rule-for-rule ports of `Contact_name_splitting.bas` and `Clean_contactdetails.bas` |
| `general_cleaning.py` | Port of `Contact Details Cleaning - General.xlsb`: Transfer IDs, Name SPlitting (I:BK formulas), Contact Details (P:AB), Address Cleaning, Summary (main contacts first, split people at the bottom) |
| `general_flow.py` | The chat flow for it: mapping-review cards → run with the confirmed mapping → result card |
| `assistant.py` | Chat sessions, intent routing, result cards, grounded Q&A |

## Contact cleaning in `daris.html` — Agentbox and General handlers

- **Agentbox** (`agentbox.py`, "clean agentbox contacts"): Agentbox's 4 CSVs are detected by their
  headers. contact.csv is mapped by its fixed names; contact_category.csv → Contact Type / Tags
  (SplitTypeTags + the Agentbox type list), contact_x_agent.csv + agent.csv → Team member 1
  (primary_owner first), `[base64]` comments → CONTACT_NOTES. Each becomes a derived column on
  the contact frame, so the same review card and `GeneralCleaner` do the rest. Joins use the raw
  contact_id — the workbook's Summary used the tagged ID and left those columns blank.
  Checked on the workbook's own tabs: category join and type/tag split 9,671/9,671, agent names
  10/10 (Team member 1 isn't stored in the workbook, so it is covered by unit tests).
- **General** ("general cleaning"): any other CRM export, role → column proposed per sheet.
- "clean the contacts" picks Agentbox when its contact.csv is uploaded, otherwise General.
- General column mapping, first match wins: `RoleMemory` (every column confirmed on a run with
  *Remember my changes*, per role) → headers taught for a Zenu field → names in
  `reference/guides/General Field Names.md` (user-editable, re-read on change) → matcher →
  `general_flow.ai_fill()`: one schema-constrained AI call for the roles still empty, picks
  sanity-checked against the sample values and shown as **AI** on the card. AI picks are never
  learned until the user Runs with them.


1. **Review** — `general_flow.review_cards()` proposes role → column per sheet (memory, aliases,
   fuzzy match, header hints; no AI guessing) and shows sample values. Nothing is written.
2. **Run** — the card's button posts the confirmed mapping + ID date tag to
   `POST /api/daris/session/{id}/general-clean`; `GeneralCleaner` builds the same tabs as the
   workbook. Optional "Remember my changes" teaches the edited columns to memory.

Verified against the workbook's own tabs (9,693 records): 697,893 of 697,896 cells identical.
The 3 differences are address lines where Excel's CLEAN glues a line break
(`CourtROCHEDALE`); DARIS turns the break into a space on purpose. Tests:
`python -m unittest tests.test_general_cleaning tests.test_agentbox tests.test_field_mapping -v`.

One deliberate change from the workbook (Summary only): when a split record's mobile or email
cell holds several values, each person gets their own in order (`split_multi_values=True`,
leftovers stay with person 1). `GeneralCleaner(..., split_multi_values=False)` restores the
workbook's rule of giving everyone the joined value.

Smart merge & reconcile and duplicate scanning are switched off in `daris.html`; the engine
(`consolidator.py`) stays for the classic UI's Consolidate button and for file profiling.
Macro workbooks (`.xlsb` / `.xlsm`) in `reference/` are listed, never read, so their client rows
stay out of the AI context.

## Contact cleaning in the classic Consolidate (checkbox in Consolidate Excel)

Runs after the merge on the Zenu contact columns, exactly as the two macros do:
names → Contact 1 in `contact_title / first_name / surname`, extra people in
`contact2_*` … `contact5_*`; phones → `contact_mobile` (G), `contact_phone_home` (J),
`contact_phone_work` (L), `contact_fax` (N); emails → `contact_email_address` (O);
rejects → `invalid_mobile / home / work / fax`, `invalid_extra_email`; review notes →
`cleaning_notes`. The macros' constants are options (`NAME_DEFAULTS`, `DETAIL_DEFAULTS`).

Workbook tabs: **Consolidated** (raw, untouched) · **Cleaned Contacts** · **Cleaning
Audit** (every changed cell, before → after) · **Cleaning Summary** (the macro's own
counts + DARIS checks). Every phone value is reconciled: values in = placed +
in-row duplicates + invalid, and the placed count is re-checked in the output cells.
DARIS also flags what Zenu won't accept (titles outside Mr/Mrs/Miss/Ms, several
emails in one field) and logs text the phone macro deletes.

Tests: `python -m unittest tests.test_contact_cleaning -v` — every worked example
in the two guides. Change a rule → update the guide → update the test.

## AI providers (Settings → gear icon in `daris.html`)

| Provider | Where | Notes |
|---|---|---|
| Local AI (default) | Ollama on this PC | Free, private; small model — good for summaries, weak at multi-step rules |
| Claude | Anthropic API | Default model `claude-opus-5-5` (Sonnet 5.5 / Haiku 5.5 cheaper). Effort `low` for column choices, `medium` for chat; JSON-schema answers; `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) so a declined request is re-run on Anthropic's recommended fallback model |
| ChatGPT | OpenAI API | Model name set in Settings (names change often) |

Keys: Settings, or `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`. The browser only ever sees
the last 4 characters. Settings endpoints refuse requests from other websites.

**Privacy with a cloud model** (default): sample values are sent as patterns only
(`0412 345 678` → `9999 999 999`); emails and long numbers are blanked out of
summaries and chat context. Sent: column/file/sheet names, run statistics, issue
text, chat messages, relevant guide excerpts. "Send real sample values" is an
explicit opt-in. Merging, cleaning rules and reconciliation never leave the PC.

AI answers are cached per provider (`llm:local`, `llm:claude`, …), so switching to a
stronger model re-asks it instead of reusing the small model's guesses.

## Reference library (`reference/`)

| Folder | Contents | Used for |
|---|---|---|
| `lookups/` | `AU Suburbs.xlsx` (16,875 suburbs + 74 spelling fixes) | Suburb checks now; address/suburb splitting next |
| `guides/` | `zenu dictionaries.xlsx`, the two contact-cleaning guides (`.md`) | AI answers grounded in your rules; the spec for the cleaning engine |
| `past_projects/` | before/after pairs (git-ignored) | Listed only — learning from them is not built yet |

Files are re-read automatically when they change. `.md`, `.txt`, `.xlsx`, `.csv`
work now; `.docx` / `.pdf` / `.xls` / `.xlsb` need `python-docx` / `pypdf` /
`xlrd` / `pyxlsb`. Guides are split into sections and only the sections relevant
to a question are given to the AI.

## API

| Endpoint | Purpose |
|---|---|
| `GET  /api/daris/status` | Active AI, memory size, reference library summary |
| `GET/POST /api/daris/settings` | Read (keys masked) / save AI settings |
| `POST /api/daris/settings/test` | One tiny real request to the active AI |
| `POST /api/daris/consolidate` | Smart consolidation (files, `header_mode`, `output_name`) → .xlsx |
| `POST /api/daris/learn` | Teach a mapping `{header, target}` |
| `POST /api/daris/session`, `…/upload`, `/api/daris/chat` | Chat sessions (see `assistant.py`) |

CLI for testing without the server:
`python -m daris.consolidator file1.xlsx file2.csv -o out.xlsx`

## Roadmap (next steps)
1. **Decide the guides' open questions** (Dr titles, ALL-CAPS partners, multi-value fields, deleted invalid emails), then switch the matching options. **Address/suburb splitting** from the suburb list.
2. **Learn from past projects** — before/after pairs in `reference/past_projects/` → header mappings and house-style value rules, shown for approval.
3. **Fuzzy person matching** — within each duplicate block, name similarity (e.g. "Jon Smith" vs "John Smith") with a merge-suggestion sheet.
4. **Reconciliation explainer** — feed `reconciliation_summary.json` from migration jobs to the AI for a client-ready sign-off paragraph.
