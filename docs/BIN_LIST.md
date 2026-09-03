# The BIN list

Bin-Tel's database is built from files you maintain: **`data/bin-list.csv`**,
plus anything you drop into a **`bin-lists/`** folder beside it.

Add rows, rebuild, and the application is looking at the new data. That is the
entire workflow — there is no import step to remember, no merge to reason
about, and no way for the database to drift away from the lists.

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

`bank` may be blank. A row that gives a BIN, its scheme and its country but no
bank is a real fact, and it is kept with the issuer recorded as unknown —
discarding it would mean answering *not found* to a BIN the list plainly
contains.

Everything else is optional, may be left blank, and may appear in any order:

| Column | What it means |
|---|---|
| `bin` | **required** — 6 to 8 digits |
| `bank` | the institution; may be blank |
| `bin_high` | the last BIN in a range (blank for a single BIN) |
| `range_type` | `issuer_range`, `account_range` or `product_range` |
| `network` | visa, mastercard, amex, discover, jcb, unionpay… |
| `brand` | the card product, e.g. "Signature", "World Elite" |
| `card_level` | the product tier: Standard, Gold, Platinum, World, Titanium |
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
A UTF-8 byte-order mark is handled.

---

## Lists that came from somewhere else

Real datasets do not arrive in Bin-Tel's column names, and they are not
expected to. Four accommodations, none of which involve guessing:

**Other spellings.** `iin`, `issuer`, `bank_name`, `scheme`, `country_code`,
`isoCode2`, `alpha_2`, `IssuerUrl`, `bank_phone` and the French
`Pays` / `Emetteur` / `Marque` / `Niveau` all resolve to the columns above. A
spelling nobody has taught the reader is still an error — see below.

**Tabs, semicolons and pipes.** The delimiter is taken from the header line, so
a `.tsv` export needs no conversion.

**Several lists in one file.** A fresh header part-way down starts a new
section with its own columns, so three lists can be pasted into one file
without reconciling their headers first. Each section may use its own
delimiter. Keeping each dataset as its own file in `bin-lists/` is tidier, and
both work.

**Notices beside a dataset.** A redistributed dataset arrives with the licence
it is redistributed under, and a licence is very often a `.txt` — which is also
a perfectly good format for a list. Files named `LICENSE`, `README`, `NOTICE`,
`ATTRIBUTION`, `COPYING`, `CHANGELOG`, `AUTHORS` or `CONTRIBUTING` are skipped,
matched on whole dot-separated parts of the filename, so `dataset.LICENSE.txt`
is skipped and `licenses-by-bank.csv` is not. The exclusion is by name rather
than by content on purpose: guessing from what is inside would mean silently
skipping a real list whose header had a typo.

**One country, spelled three ways.** A file carrying `alpha_2`, `alpha_3` and
`country` is describing one country. The two-letter code wins, then the
three-letter code, then the name.

Two columns are recognised and deliberately **not** stored: `latitude` and
`longitude`. In every dataset seen so far they hold the *country's* centroid
repeated on every row rather than the bank's address, and storing a country
centroid as an institution's location would be a fabrication with a decimal
point on it.

---

## Lists that have been through a spreadsheet

Excel damages BIN lists in two specific ways, and neither is repaired quietly.

**Long numbers become floats.** A phone number stored as `5.51732E+11` has lost
its digits for good. The value is dropped, the row is kept, and `check-list`
counts what it discarded.

**Leading zeros are stripped.** A `bin` column read as a number turns `042410`
into `42410`. The reader refuses anything under six digits and says what the
value may have been:

```
line 2: '42410' is 5 digits; a BIN is 6 to 8. A spreadsheet strips leading
zeros from a numeric column, so this may be '042410' — rebuild with
--pad-short-bins if you know that is what happened to this file
```

`--pad-short-bins` (**Database → Rebuild**, or the checkbox, which remembers
your answer) left-pads them back to the length an assignment actually comes in — sixes and eights. Four and five digits become six; seven becomes
eight, because nothing is issued at seven digits, so a surviving seven is an
eight that lost one zero.

It is **off by default and should stay off unless you know the file went
through a spreadsheet**: `42410` and `042410` are different BINs, and choosing
between them without evidence would be inventing data.

Signs that the zeros really were stripped: the file contains four- and
five-digit values but *no* six-digit value beginning with `0`, and it also
contains a number rendered in scientific notation — proof it passed through a
spreadsheet.

### What padding cannot recover

**An eight-digit BIN beginning `00` comes out of Excel as six digits, and
there is nothing left to tell it apart from a genuine six-digit BIN.**
`00412345` and `412345` are the same six characters once the zeros are gone.
Padding restores the values whose length still shows the damage; it cannot
restore one whose damage left it looking legitimate.

So a padded file is *better* than an unpadded one, not *correct*. The only
way to be sure is to go back to the source: save it as text (`.txt` or
`.tsv`), or format the BIN column as **Text** in the spreadsheet *before*
opening it, and none of this arises. Where the padded file and a clean export
disagree, the clean export is right.

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

**A BIN may appear on several rows**, and that is how you say the interesting
things. Two banks that both use one BIN, and a predecessor that used it until
2024, are separate facts and all of them are kept. Only a row repeating the
*same* institution, relationship and period counts as a correction — the later
one wins, so you can append a fix to the end of the file rather than hunting
for the original.

---

## Several banks, and banks that stopped

The interface never picks a winner the data does not support.

**More than one bank currently using a BIN.** Write a row for each:

```csv
bin,bank
520001,Harbor Mutual Savings
520001,Pacific Coast Savings
```

Both are named in the result, the result is marked **Conflicted**, and no
confidence percentage is shown — a figure beside "conflicted" would read as
"90% sure", which is the opposite of what a conflict means.

**A bank that stopped using a BIN.** Give it `former_issuer` and an end date:

```csv
bin,bank,relationship,effective_from,effective_to
530001,Cascade Federal Bank,former_issuer,2019-01-01,2024-06-30
530001,Meridian Trust Bank,,2024-07-01,
```

The result names **Meridian Trust Bank** as the current issuer and reports
Cascade beneath it as *stopped 2024-06-30*. A succession is a timeline, not a
disagreement, so it is not flagged as a conflict.

**A BIN nobody uses any more.** A former issuer with no successor:

```csv
bin,bank,relationship,effective_to
540001,Northshore Credit Union,former_issuer,2022-03-31
```

The result says **"No current issuer recorded"** and names Northshore as the
previous one, with the date. It never presents a former issuer as the current
one — that is the single most misleading thing this application could say. If
the end date is unknown, leave it blank: the result says "stopped — date not
recorded" rather than inventing one.

Because standing outranks confidence, a row describing an ended relationship
also never supplies the record's present-tense attributes. A BIN whose current
issuer is in the UK is not labelled with the country of the bank that stopped
using it in 2024, whichever row was read first.

Exports carry the same distinction: `current_issuers` and `former_issuers` are
separate fields in JSON, and CSV and text exports have a **Former Issuers**
column beside **Issuer**.

---

## What gets filled in for you

A hand-kept list is uneven: one row for a bank carries its website and city,
the next three name the bank and nothing else. After every rebuild a pass fills
those gaps — from evidence, never from invention.

**From the same institution's other rows.** If any row says where a bank is,
or what its website is, every BIN belonging to *that same institution* gets the
same answer. Nothing is inferred: it is one fact, written once, applied where
it already belonged. Case and spacing do not split a bank in two — `Cascade
Federal Bank`, `cascade federal bank` and `CASCADE  FEDERAL  BANK` are one
institution.

**From the BIN's own digits, for the scheme only.** ISO/IEC 7812 and the
schemes' published ranges settle which network a prefix belongs to, so a blank
`network` is filled from the number: `4…` Visa, `51–55` and `2221–2720`
Mastercard, `34`/`37` Amex, `3528–3589` JCB, `6011`/`644–649`/`65` Discover,
`62` UnionPay, `300–305`/`36`/`38–39` Diners, `2200–2204` Mir.

Where two schemes publish the same prefix, nothing is filled. `622126–622925`
is claimed by both Discover and UnionPay, and the digits genuinely do not
settle it, so the network stays Unknown and the rebuild says how many prefixes
that happened to. State the network on those rows if you know it.

**From the country, for the currency.** A known country supplies the currency
when the row did not.

### What is never filled in

* **A website, address, phone number or legal name that appears nowhere.** It
  stays Unknown. A plausible guess is worse than an honest gap.
* **The card type or funding type.** Nothing about the digits establishes
  credit versus debit.
* **The issuer.** A BIN the list does not name stays unknown; a neighbouring
  BIN's bank is never borrowed.
* **Anything the list already states.** Every fill targets a blank — the list
  is always the authority.

Nothing is filled silently. Each rebuild reports what it filled and by which
rule, and every value is written to the database's `normalization_events`
table with the rule that produced it, so anything the database asserts can be
traced back to the row or the published range it came from.

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
