# Contact Cleaning — Name Splitting

**Source of truth:** `Contact_name_splitting.bas` (macro `SplitContactNames`, tab *Name SPlitting*).
This guide restates that macro rule-for-rule so DARIS (and people) apply it exactly the same way.
If this guide and the macro ever disagree, the macro wins — update this guide.

---

## 1. Purpose
One CRM record often holds several people ("John & Mary Smith", "Frank/Merlyn Armer").
Zenu needs **one person per contact**. This step splits a record's raw title / first name / last
name into up to **5 separate people**, each with their own Title, First Name and Surname.

## 2. Input and output

| Raw input (from row 3) | Column |
|---|---|
| Title | C |
| First Name | D |
| Last Name | E |

| Output | Columns (Title · First · Surname) |
|---|---|
| Contact 1 | M · N · O |
| Contact 2 | T · U · V |
| Contact 3 | AA · AB · AC |
| Contact 4 | AH · AI · AJ |
| Contact 5 | AO · AP · AQ |

Columns between the blocks hold workbook formulas and are never written to.
If any output block already has data, the user is asked before overwriting.

## 3. Rules, in the order they are applied

### Step 1 — Clean every input value (C, D, E)
- Non-breaking spaces and tabs become normal spaces; leading, trailing and repeated spaces are removed.
- Placeholder values become **blank**: `N/A`, `NA`, `None`, `-`, `--`, `+` (case-insensitive, whole value only).

### Step 2 — Title (column C)
- Accepted titles (case-insensitive, a trailing `.` is allowed): **Mr, Ms, Miss, Mrs, Dr**.
  They are written in that exact form (`mrs.` → `Mrs`).
- The column C title applies to **Contact 1 only**.
- If C holds something that is not a title:
  - and First Name (D) is blank **and** C is a single plain word (letters, `'` and `-` only)
    → C is used as the **first name** (e.g. C = `Sarah`, D blank → first name `Sarah`).
  - otherwise C is **ignored** and the row is logged: `invalid title 'X' ignored`
    (e.g. `Mr & Mrs`, `Prof`, `Rev`).

### Step 3 — Remove trailing ALL-CAPS business labels (switch: `STRIP_CAPS_LABELS = True`)
Applied separately to First Name (D) and Last Name (E).
- Starting from the **end** of the value, consecutive ALL-CAPS words are a "label" when each
  word is at least 2 characters and contains only `A–Z`, `'` or `-`.
- The **first word is never removed**.
- The label is removed **only if** at least one word before it is mixed/lower case.
- The removed text is logged: `label 'X' removed`.

| Raw | Result | Logged |
|---|---|---|
| `Megan SEA TEMPLE` | `Megan` | label 'SEA TEMPLE' removed |
| `JOHN SMITH` | `JOHN SMITH` (unchanged — nothing mixed-case before it) | — |
| `John A` | `John A` (single letters are not labels) | — |
| `Mary O'NEIL` | `Mary` ⚠ | label 'O'NEIL' removed |
| `John & MARY` | `John &` → only John ⚠ | label 'MARY' removed |
| `John & MARY SMITH` | `John &` → only John, no surname ⚠ | label 'MARY SMITH' removed |
| `JOHN & MARY` | unchanged → John + Mary | — |

> ⚠ The label check runs on the **whole cell before splitting into people**, so an ALL-CAPS
> surname — or a whole ALL-CAPS **partner** — typed after a mixed-case name is removed as a label.
> The removed text is only in the review log. See Open Questions.

### Step 3b — Second person's first name typed into the surname (DARIS rule)
When the **Last Name starts with a separator** (`&`, `and`, `+` or `/`) and the First Name holds one
person, the words after the separator are the **second person's name**:
- two or more words → all but the last word are person 2's first name, the **last word is the shared
  surname**: `Munny` + `& Brawna Kumar` → `Munny & Brawna` / `Kumar` → Munny Kumar, Brawna Kumar;
- one word → it is person 2's first name and nobody has a surname: `Finlae` + `& Stephen` → Finlae, Stephen.
Log `second person's first name was in the surname column`. Not applied when the First Name is blank
or already holds several people, or the rest of the surname holds several people.

### Step 4 — Split into people
First Name (D) and Last Name (E) are each split on these separators:
`,`  `&`  `/`  `+`  `;`  and the word **`and`** (only as a separate word, any case).
- `Andrew` is **not** split (the "and" is inside a word); `Ann and Bob` / `Ann AND Bob` are.
- Hyphens are **not** separators (`Mary-Jane` stays whole).
- Each part is cleaned again (Step 1) and empty parts are dropped.

The number of people = **the larger** of (first-name parts, last-name parts).
- No parts at all → no contact. If only a title was present, log `title only, no name`.
- More than **5** people → keep the first 5, log `N people found, first 5 kept`.

### Step 5 — Pair first names with surnames (for each person 1…N, in order)
1. **First name** = the matching first-name part (blank if there are fewer first names).
2. **Surname:**
   - exactly **one** surname part → it is **shared** by every person;
   - several surname parts → paired **in order** (person 2 gets surname part 2);
   - fewer surnames than people → the extra people get a blank surname.
3. **Title inside a name:** if the first word of the first name is a title, it moves to that
   person's Title (`Dr Sofija` → Title `Dr`, First `Sofija`). If not, the same check is made on
   the start of the surname.
4. **Embedded full name:** if the surname is still blank and the first name contains a space,
   the **last word becomes the surname** (`Kate Griffin` → First `Kate`, Surname `Griffin`).
5. **Contact 1** with no title of its own gets the column C title. A title found inside
   Contact 1's own name (Step 5.3) **wins** over column C.
6. A person with neither a first name nor a surname is skipped; the remaining people move up
   (no gaps between Contact 1, 2, 3…).

After pairing: if there is more than one person **and** the raw Last Name was blank,
log `no surname in raw data`.

### Step 6 — Couples in separate rows (DARIS rule, General cleaning)
When a **spouse / partner contact ID** column is mapped, a row whose spouse ID is another row's contact ID is
linked to it: both get the other as Partner ID, one shared Partnership ID (`<first id>_<second id>_r`) and
type **Partner**. Only rows that are one person each; a row is linked once; a spouse ID not in the file, or
pointing at someone whose own spouse ID differs, is left unlinked and reported.

## 4. Worked examples

| C Title | D First | E Last | Contact 1 | Contact 2 | Contact 3 | Logged |
|---|---|---|---|---|---|---|
| Mr | John | Smith | Mr · John · Smith | | | |
| mrs. | Jane | Lee | Mrs · Jane · Lee | | | |
| Mr | John and Mary | Smith | Mr · John · Smith | · Mary · Smith | | |
| | Frank/Merlyn | Armer | · Frank · Armer | · Merlyn · Armer | | |
| | Brian & Liljana | Armfield/Incao | · Brian · Armfield | · Liljana · Incao | | |
| | John | Smith & Jones | · John · Smith | · · Jones | | |
| | Kate Griffin & Nick Griffin | | · Kate · Griffin | · Nick · Griffin | | no surname in raw data |
| Mrs | Dr Sofija | Petrovic | Dr · Sofija · Petrovic | | | |
| Sarah | | Lee | · Sarah · Lee | | | |
| Mr & Mrs | Tom & Ann | Brown | · Tom · Brown | · Ann · Brown | | invalid title 'Mr & Mrs' ignored |
| | Megan SEA TEMPLE | Lee | · Megan · Lee | | | label 'SEA TEMPLE' removed |
| Dr | N/A | - | | | | title only, no name |
| | A, B, C, D, E, F | Ng | A … E (5 people) | | | 6 people found, first 5 kept |
| | Munny | & Brawna Kumar | · Munny · Kumar | · Brawna · Kumar | | second person's first name was in the surname column |
| | Fabian | &myschelle Bhandari | · Fabian · Bhandari | · myschelle · Bhandari | | second person's first name was in the surname column |
| | Finlae | & Stephen | · Finlae · | · Stephen · | | second person's first name was in the surname column; no surname in raw data |

## 5. Review log
Every row with a note is listed for review (the first 20 in the message box, all of them in the
Immediate window). Notes are joined with `; ` when a row has more than one:
`invalid title '…' ignored` · `label '…' removed` · `title only, no name` ·
`N people found, first 5 kept` · `no surname in raw data`.

## 6. How DARIS should apply this guide
- Apply the steps **exactly in order**; never invent a surname or title that isn't in the data.
- Every split must keep all name text somewhere: in a contact, or in the review log
  (removed labels, ignored titles, people beyond 5). Nothing disappears silently.
- Show the log as review items, not as errors.

## 7. Open questions for Daryl
1. **Dr:** the macro outputs `Dr`, but Zenu's `contact_title` accepts only `Mr, Mrs, Miss, Ms`.
   Should `Dr` be kept, blanked, or moved somewhere else (e.g. a tag)?
2. **ALL-CAPS words after a mixed-case name are removed as labels** — including surnames
   (`Mary O'NEIL` → Mary) and whole partners (`John & MARY` → only John; Mary is lost except for the
   log line). Should the label check run per person *after* splitting, or only when the words look
   like a business (e.g. 2+ words, or known words like PTY, LTD, REALTY)?
3. **"Mr & Mrs" in the title column** is ignored rather than split into Mr (Contact 1) and Mrs
   (Contact 2). Intended?
4. **Surname-only contacts** (`John` + `Smith & Jones` gives Contact 2 with surname `Jones` only).
   Keep, or log for review?
