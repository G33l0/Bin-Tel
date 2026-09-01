# The BIN list

Bin-Tel's database is built from one file you maintain: **`data/bin-list.csv`**.

Add rows to it, rebuild, and the application is looking at the new data. That
is the entire workflow — there is no import step to remember, no merge to
reason about, and no way for the database to drift away from the list.

```bash
python -m app.cli rebuild
```

or, in the application: **Database → Rebuild from BIN list**.

---

## The format

Only two columns are required:

```csv
bin,bank
410000,Cascade Federal Bank
41000012,Northshore Credit Union
530001,Meridian Trust Bank
```

Everything else is optional, may be left blank, and may appear in any order:

| Column | What it means |
|---|---|
| `bin` | **required** — 6 to 8 digits |
| `bank` | **required** — the institution |
| `bin_high` | the last BIN in a range (blank for a single BIN) |
| `range_type` | `issuer_range`, `account_range` or `product_range` |
| `network` | visa, mastercard, amex, discover, jcb, unionpay… |
| `brand` | the card product, e.g. "Signature", "World Elite" |
| `card_type` | credit, debit, charge, prepaid |
| `funding_type` | how the account is funded |
| `prepaid`, `commercial` | yes/no |
| `country`, `currency`, `city`, `state` | geography |
| `website`, `phone` | contact details |
| `legal_name` | the registered name, when it differs from `bank` |
| `parent` | the parent institution |
| `relationship` | `issuer` (default), `former_issuer`, `program_issuer`, `subsidiary`, `associated_institution` |
| `effective_from`, `effective_to` | `YYYY-MM-DD` |
| `notes` | anything you want to remember; never ingested |

Lines beginning with `#` are ignored, so you can keep notes in the file itself.
A UTF-8 byte-order mark is handled, and common spellings of the column names —
`iin`, `issuer`, `bank_name`, `scheme`, `country_code` — are accepted.

---

## What the reader refuses

Being strict about the *shape* of the file is what keeps the database honest.

**An unrecognised column stops the read.** If you add a `region` column, the
rebuild fails and names it, rather than quietly ignoring it and building a
database that is missing data you thought you had provided. Add the column to
`KNOWN_COLUMNS` in `app/services/bin_list.py` if it really belongs.

**A missing `bin` or `bank` column stops the read.** So does an empty file, a
file with only a header, and a file that cannot be opened.

**Anything longer than 8 digits is refused.** A 16-digit value in this file
would be a card number. It is rejected, and the value is never echoed back into
an error message or a log. Never put full card numbers, CVVs, PINs or
cardholder names in this file.

**A rebuild that would lose most of the database asks first.** If the list
suddenly holds a fraction of what the live database holds — a truncated paste, a
half-saved file — the rebuild stops and says so. Confirm it (or pass
`--allow-shrink`) and it goes ahead.

---

## What it tolerates

**A bad row never costs you the rest of the list.** A row that cannot be
understood is reported with its line number and skipped; everything else still
builds. `python -m app.cli check-list` reads the file and reports on it without
touching the database.

**A repeated BIN is not an error.** The later row wins — so you can append a
correction to the end of the file rather than hunting for the original — and
the collision is counted and reported.

---

## 6-digit and 8-digit BINs

Both are first-class, and neither is derived from the other.

* An 8-digit BIN is stored as an 8-digit BIN. It is never shortened to 6 and
  the 6-digit value is never treated as its issuer.
* A 6-digit BIN is never expanded into invented 8-digit BINs.
* When both are present, the more specific one answers the query:
  `41000012` resolves to whoever the list says holds `41000012`, even if
  `410000` names someone else.
* A BIN the list does not name comes back **unknown**. A numerically adjacent
  BIN is never borrowed to fill the gap — `414720` and `414721` being one apart
  says nothing about whether they share an issuer.

---

## Rebuilding, and getting back

A rebuild never edits the live database. It builds a complete new one in a
staging folder, verifies it, and only then swaps it into place:

```
read the list → build in staging → verify → back up the live one
→ close → swap → reopen → index → stamp
```

Anything that fails before the swap leaves the live database exactly as it was.

The database a rebuild replaces is kept alongside it, so the operation is
reversible:

```bash
python -m app.cli rollback
```

or **Database → Roll back**. Neither copy is discarded, so running it again
rolls forward to where you were.

---

## Commands

| | |
|---|---|
| `python -m app.cli rebuild` | build the database from the list and activate it |
| `python -m app.cli rebuild --list path/to/other.csv` | build from a different file |
| `python -m app.cli rebuild --allow-shrink` | build even though the list is much smaller |
| `python -m app.cli rebuild --db-version 2026.03.1` | stamp a version of your choosing |
| `python -m app.cli rollback` | go back to the database the last rebuild replaced |
| `python -m app.cli check-list` | read the list and report, changing nothing |
| `python -m app.cli check-list --strict` | exit non-zero if any row was skipped |

---

## Keeping the list in the repository

The list is a plain text file, so git is the natural place for it: every
rebuild is traceable to a commit, and `git diff` shows exactly which BINs
changed. Edit it wherever you like — locally, or on GitHub — pull, and rebuild.
