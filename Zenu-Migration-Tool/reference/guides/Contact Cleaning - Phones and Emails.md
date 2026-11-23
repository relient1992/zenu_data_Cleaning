# Contact Cleaning — Phones and Emails

**Source of truth:** `Clean_contactdetails.bas` (macro `CleanContactDetails`, v2 – international,
tab *Contact Details*). This guide restates that macro rule-for-rule so DARIS (and people) apply
it exactly the same way. If this guide and the macro ever disagree, the macro wins — update this guide.

---

## 1. Input and output

| Raw input (from row 2) | Column |
|---|---|
| Email | B |
| Mobile | C |
| Work phone | D |
| Home phone | E |
| Fax | F |

| Output column | Holds | Natural Zenu field |
|---|---|---|
| **G** Mobile | every valid mobile, joined with ` \| ` | `contact_mobile` |
| H Mobile Landline | landline overflow (temporary — merged into J) | — |
| I Home Mobile | temporary — merged into G | — |
| **J** HomeLandline | home landline (+ any landline overflow) | `contact_phone_home` |
| K Work Mobile | temporary — merged into G | — |
| **L** WorkLandline | work landline | `contact_phone_work` |
| M Fax Mobile | temporary — merged into G | — |
| **N** Fax Landline | fax | `contact_fax` |
| **O** Email | first valid email (+ extra valid emails joined with ` \| `) | `contact_email_address` |
| **U** Invalid / extra email | invalid emails, joined with ` \| ` | review |
| **W** Invalid Mobile | rejected values from the Mobile column | review |
| **X** Invalid Home | rejected values from the Home column | review |
| **Y** Invalid Work | rejected values from the Work column | review |
| **Z** Invalid Fax | rejected values from the Fax column | review |

All outputs are written as **text**, so a leading `+` is never lost.
If G:O, U or W:Z already hold data, the user is asked before overwriting.

### Settings (as shipped)

| Setting | Value | Meaning |
|---|---|---|
| `DEDUPE_IN_ROW` | True | the same number is placed once per row |
| `MULTI_SEP` | ` \| ` | separator when a cell holds several values |
| `INTL_MIN` / `INTL_MAX` | 8 / 15 | digit range accepted for international numbers |
| `INTL_REQUIRE_PLUS` | False | international numbers do **not** need a `+` |
| `KEEP_EXTRAS_AFTER_MERGE` | False | I, K, M and H are emptied after merging into G and J |
| `MERGE_EXTRA_EMAILS_TO_O` | True | extra valid emails go into O (not U) |

---

## 2. Processing order for each row
1. **Pull emails out of phone cells.** Any valid email found in Mobile, Home, Work or Fax is
   moved to the email list (after column B's emails) and removed from that phone cell. If what's
   left has no digits (e.g. just `Email:`), the phone cell becomes blank. An **invalid** email in a
   phone cell is *not* moved: if it contains digits (`john1@x`) it stays and is rejected as an
   invalid phone; if it has **no digits** (`john@x`) the "no digits left" rule blanks it and it is
   **lost** — it reaches neither U nor W–Z. ⚠ See Open Questions. (DARIS keeps this output but lists
   the removed text in its review log.)
2. **Mobile column — mobiles only** (and invalid values).
3. **Home, then Work, then Fax** — everything (mobiles and landlines).
4. **Mobile column again — landlines only** (landlines typed into the Mobile column).
5. **Merge:** extra mobiles in I, K, M are appended to **G**; landline overflow in H is
   appended to **J**. I, K, M and H end up blank.
6. **Emails** (column B first, then the ones pulled from phone cells).

Because of this order, the first number in **G** is always the first real mobile from the
Mobile column, and home numbers take priority over work numbers.

## 3. Splitting a phone cell into numbers
These become separators: `,` `;` `/` `&` `|`, line breaks, and the words **`and`** / **`or`**
(as separate words).
A `-` is treated as a separator **only when the cell holds more than 15 digits in total**
(i.e. it clearly contains 2+ numbers — one international number can have up to 15 digits).

## 4. Cleaning and classifying one number
**Cleaning**
1. Bracketed text containing letters is removed: `(work)`, `(mob)`, `(after hours)`.
   Bracketed digits are kept as part of the number: `(07)`, `(20)`.
2. Everything except digits, letters, `+` and spaces is removed: `( ) - . _ # *` …
   Repeated spaces collapse to one; `+ 44 20` becomes `+44 20`.

**Rejected as invalid** (raw value goes to the Invalid column for its source):
- nothing left after cleaning, or no digits at all;
- a `+` anywhere except the very start;
- **any letters left** (`0412 345 678 ext 2`, `x12`, `call after 5`);
- the same digit repeated (`0000000000`, `1111 1111`).

**International dialling prefixes** (when there is no `+`):
`0011…` (more than 4 digits) and `00…` (11+ digits) are treated like a leading `+`
(`0011 44 20 7946 0958` → `+44 20 7946 0958`).

**Australian country code → local form** (remembered as "arrived as +61"):
- 12 digits starting `610` → `0` + the rest (`+61 0412 345 678` → `0412 345 678`);
- 11 digits starting `61` → `0` + the rest (`61412345678` → `0412345678`).

**Leading zero lost by Excel:** exactly **9 digits** with no `+`, starting with
`2, 3, 4, 7 or 8`, get a `0` added (`412345678` → `0412 345 678`, `298765432` → `02 9876 5432`).
*(The macro header only mentions 4, but the code applies this to 2, 3, 4, 7 and 8.)*

### Classification table (checked top to bottom; the first match wins)

| # | Pattern (after the steps above) | Type | Output format |
|---|---|---|---|
| 1 | 10 digits starting `04` | AU mobile | `0412 345 678` — or `+61 412 345 678` if it arrived as +61 |
| 2 | starts with `+`, `64` + 9 digits (11 total), not +61 | NZ mobile | kept as cleaned (`+64 21 123 4567`) |
| 3 | no `+`, starts `02`, and 9 or 11 digits, or 10 digits with a 3rd digit of 0, 1 or 2 | NZ mobile (local) | kept as cleaned (`021 123 4567`) |
| 4 | 10 digits starting `02`, `03`, `07`, `08` | AU landline | `02 9876 5432` — or `+61 2 9876 5432` if it arrived as +61 |
| 5 | 10 digits starting `1300` / `1800` (not +61) | AU inbound | `1300 123 456` |
| 6 | 6 digits starting `13`, no `+` | AU inbound | `13 12 34` |
| 7 | 8 digits starting 2–9, no `+`, not +61 | AU local (no area code) | `9876 5432` — **an area code is never invented** |
| 8 | arrived as +61 but none of the above | **invalid** | raw value → Invalid column |
| 9 | 8–15 digits | International (type unknown) | kept as cleaned (spaces and `+` kept) |
| 10 | anything else (< 8 or > 15 digits) | **invalid** | raw value → Invalid column |

> Note on row 3: 10-digit numbers starting `023`–`029` are **not** NZ — they are Sydney
> landlines (row 4).
> International numbers (row 9) count as **mobiles** when they came from the Mobile column,
> and as **landlines** when they came from Home, Work or Fax.

## 5. Where each number goes

**Duplicates:** with `DEDUPE_IN_ROW`, a number already placed in this row is skipped wherever
it appears again (compared on its digits, so `0412 345 678` = `+61 412 345 678` = `61412345678`).
Invalid values are not de-duplicated.

**Mobiles** (first empty slot wins; when all are full, the last slot gets ` | ` appended):

| Came from | Slot order | Overflow |
|---|---|---|
| Mobile | G → I → K → M | M |
| Home | G → I | I |
| Work | G → K | K |
| Fax | G → M | M |

After the merge (step 5), **every mobile ends up in G**, joined with ` | `.

**Landlines:**

| Came from | Slot order | Overflow |
|---|---|---|
| Home | J → L | H |
| Work | L → J | H |
| Mobile column (step 4) | J → L | H |
| Fax | N | H |

After the merge, overflow H is appended to **J**. Consequences worth knowing:
- a **second home landline** fills WorkLandline (L) if L is still empty;
- a **second fax** (or anything that overflows) ends up appended to **HomeLandline (J)**.

**Invalid values** go to W (from Mobile), X (Home), Y (Work) or Z (Fax), raw text kept,
several joined with ` | `.

## 6. Emails
**Splitting:** a cell is split on `,` `;` `/` `|` tabs, line breaks and **spaces**.

**Unwrapping:** a leading `mailto:` is removed, then any of `< > ( ) [ ] " ' .` are trimmed
from both ends (`<john@x.com>` → `john@x.com`).

**Valid email** — all of these must be true:
- at least 6 characters, exactly one `@`, and it is not the first character;
- no `..` anywhere;
- the part before `@` doesn't start or end with `.`, and uses only `A–Z a–z 0–9 . _ % + ' -`;
- the part after `@` contains a `.`, doesn't start or end with `.` or `-`, and uses only
  `A–Z a–z 0–9 . -`;
- the last part (TLD) is at least 2 characters, letters only.

**Placement:** duplicates are skipped (case-insensitive). The first valid email goes to **O**,
and further valid emails are appended to **O** with ` | `. Invalid values go to **U**,
joined with ` | `.

| Raw (B, plus emails pulled from phone cells) | O | U |
|---|---|---|
| `john@x.com` | john@x.com | |
| `JOHN@x.com; john@x.com` | JOHN@x.com | |
| `john@x.com, mary@y.com.au` | john@x.com \| mary@y.com.au | |
| `mailto:<sam@z.net>` | sam@z.net | |
| `john@x` | | john@x |
| `John Smith <john@x.com>` | john@x.com | John \| Smith ⚠ |

## 7. Worked phone examples

| B | C Mobile | D Work | E Home | F Fax | G | J | L | N | Invalid |
|---|---|---|---|---|---|---|---|---|---|
| | `0412345678` | | | | 0412 345 678 | | | | |
| | `412 345 678` | | | | 0412 345 678 | | | | |
| | `+61 412 345 678` | | | | +61 412 345 678 | | | | |
| | `0412 345 678 / 0498 765 432` | | | | 0412 345 678 \| 0498 765 432 | | | | |
| | `(03) 9876-5432` | | | | | 03 9876 5432 | | | |
| | | `02 9999 0000 (work)` | `0398765432` | | | 03 9876 5432 | 02 9999 0000 | | |
| | `0412 345 678` | | `0412345678` | | 0412 345 678 *(home copy skipped as duplicate)* | | | | |
| | `0412 345 678 ext 2` | | | | | | | | W: 0412 345 678 ext 2 |
| | `+44 (20) 7946-0958` | | | | +44 20 79460958 *(international, from the Mobile column)* | | | | |
| | `98765432` | | | | | 9876 5432 *(no area code added)* | | | |
| | `john@x.com` | | | | | | | | O: john@x.com *(moved from the phone cell)* |
| | | | | `03 9111 2222, 03 9111 3333` | | 03 9111 3333 *(fax overflow)* | | 03 9111 2222 | |

## 8. Run summary shown at the end
Rows processed · Mobile (G) count · Home Landline (J) · Work Landline (L) · Fax (N) ·
Email (O) · Invalid/extra email (U) · NZ mobiles placed · International numbers placed ·
Invalid numbers (W:Z) · Emails moved from phone columns.

## 9. How DARIS should apply this guide
- Follow the processing order in §2 exactly — it decides which number is "primary".
- Never invent digits or area codes; never drop a value: every input is either placed, skipped
  as an in-row duplicate, or written to an Invalid/U column.
- Report the §8 counts after every run so they can be reconciled.

## 10. Open questions for Daryl
1. **Several values in one Zenu field:** G (mobile) and O (email) can hold several values joined
   with ` | `, but Zenu says *"Only one email address per contact"*. Should the extras move to U /
   a second contact, or does the import step handle the ` | `?
2. **Names inside email cells:** `John Smith <john@x.com>` sends `John` and `Smith` to U as
   invalid emails because spaces are separators. Intended?
3. **Fax overflow into HomeLandline:** a second fax number lands in J (HomeLandline). Intended,
   or should it stay with the fax?
4. **Second home landline into WorkLandline:** when Home holds two landlines, the second fills L.
   Intended?
5. **Invalid emails in phone cells are deleted** (found while porting): `john@x` typed in the Mobile
   column is blanked by the "no digits left" rule and appears nowhere in the output. Should it go to
   U (invalid email) or W–Z (invalid phone) instead?
