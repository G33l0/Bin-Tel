# Datasets

Every list file in this folder is read when the database is rebuilt, in
addition to `../bin-list.csv`. Each keeps its own columns and its own
delimiter, so a dataset goes in exactly as it is — no merging, no renaming.

```bash
python -m app.cli check-list --pad-short-bins    # look before building
python -m app.cli rebuild --pad-short-bins
```

Adding a dataset is dropping a file in. Removing one is deleting the file.

---

## What is here

| File | Rows | What it is |
|---|---|---|
| `binlist-data.csv` | 343,063 | A public dataset, CC BY 4.0 — see `ATTRIBUTION.md` |
| `us-bins-2026-08-22.csv` | 3,085 | A 2026 US list, trusted above the archive — see below |
| `sample-a-issuer-contacts.tsv` | 20 | Sample of a personally compiled list |
| `sample-b-by-country.tsv` | 20 | Sample of the same, French headers |

The two samples are placeholders so the repository has something small to test
against; the full files replace them in place.

Three different column vocabularies, two different delimiters, no conversion
needed. BIN prefixes, issuer names, schemes and countries only. No card
numbers, no cardholder data — the reader refuses anything longer than eight
digits.

`sample-c-coordinates.tsv` used to be here and is gone: it turned out to be a
30-row slice of `binlist-data.csv`, byte-identical, so keeping both would have
restated the same thirty facts.

---

## These files have been through a spreadsheet

**`binlist-data.csv` and `sample-a` need `--pad-short-bins`. `sample-b` does
not** — it is clean six-digit throughout, and the flag is harmless to it.

The damage is *upstream*, in the dataset as published, not something that
happened here. Across all 343,063 rows of `binlist-data.csv`:

* **not one BIN begins with `0`**, while 7 are five digits long;
* **57 phone numbers are rendered in scientific notation** (`9.67E+11`).

The second is proof the file passed through a spreadsheet during its
compilation; the first is what that spreadsheet did to the BIN column. Those
phone numbers are unrecoverable and are dropped on read.

Padding restores a BIN to a length something is actually assigned at — four and
five digits become six, seven becomes eight. **It cannot restore an eight-digit
BIN beginning `00`**, which survives as six digits and is indistinguishable
from a genuine six-digit BIN.

So these files are recoverable, not clean. If a source can be re-exported with
the BIN column formatted as **Text**, that export is better than the padded
read of this one, and should replace it. For `binlist-data.csv` that is not
possible: the repository is archived, so the published file is as good as it
gets, and 7 affected rows in 343,063 is the scale of it.

---

## The 2026 US list, and how it sits against the archive

`us-bins-2026-08-22.csv` is 3,085 US BINs. It overlaps the archive almost
completely — 3,076 of its BINs are already there, 9 are not — so it is worth
having for what it *corrects*, not for what it adds.

Measured against `binlist-data.csv`:

| | |
|---|---|
| BINs the archive does not have | 9 |
| bank filled in where the archive had none | 352 |
| card level filled in where the archive had none | 739 |
| card type filled in where the archive had none | 53 |
| both name a bank | 2,656 |
| — the same name | 1,044 |
| — the same name spelt differently | 58 |
| — a different name | 1,554 |

**The 2026 file wins a disagreement.** That is the ranking in its sidecar
(0.9, against the archive's 0.5) and it rests on the disagreements themselves:
they are dominated by renames and mergers the 2020 archive predates —
`WACHOVIA` → `WELLS FARGO`, `FIA CARD SERVICES, N.A.` → `BANK OF AMERICA,
NATIONAL ASSOCIATION`, `HSBC BANK NEVADA, N.A.` → `CAPITAL ONE`, `RBS
CITIZENS, N.A.` → `CITIZENS BANK, N.A.`, `FAA C.U.` → `TRUE SKY C.U.`

To reverse it, set that one number below 0.5. **Neither reading loses
anything**: the archive keeps its own row either way, both are shown, and
`python -m app.cli origin <bin>` prints each source row as it arrived.

`binlist-data.csv` itself is **not** edited. It is redistributed under CC BY
4.0 with `ATTRIBUTION.md` recording *Modified — no*, and rewriting it would
make that untrue. The merge happens in the database, at build time.

### How LEVEL and BANK were separated

The source was a printed table with no delimiter between the level and the
bank, aligned for the eye: card types are different lengths, so column offsets
do not survive, and the gap before the bank name is six spaces on one row and
one space on the next.

Once the BIN and the card type are taken off the front, **2,672 of the 3,085
rows still have a gap of two or more spaces at that boundary** and say outright
where the level ends. Those rows are the evidence the rest is derived from:
they give the closed level vocabulary the column actually uses, and the bank
names as *this* file spells them, including `AMERICAN EXPRESS US CONSUMER`,
which the archive does not hold at all. The two vocabularies are then grown
against each other over the remaining rows until they stop growing.

The outcome: 2,672 rows split by the file's own spacing, 261 by a level from
that vocabulary, 78 by a bank name alone with no level in front of it, 3 left
undecided and flagged, and 71 that name no bank in the source at all. Every
one of the 3,085 rebuilds its source line exactly.

The `notes` column carries the level and bank text as it was printed, before
the split. The reader never ingests it, so it is free, and it keeps every
split checkable against what was actually read.

## What the columns mean

`Emetteur` in sample-b is **the issuer**, confirmed by the list's maintainer.
It is left mapped that way. Where it disagrees with `binlist-data.csv` about
who issues a BIN — 17 of the 19 they share — that is a genuine disagreement
between two sources, not a difference of meaning, and it is recorded as a
conflict rather than reconciled by remapping a column.

Which of the two is right is not something the files can settle. The trust
levels decide which is *presented* (`binlist-data.csv` is set to 0.5 in its
sidecar, a curated list defaults to 0.9), both are always shown, and
`python -m app.cli learn --local-only` turns each disagreement into a proposal
you can decide one at a time.

## Two things the data says that are worth knowing

**`Pays` in sample-b is the country the card WORKS IN.** Not where it was
issued, not the bank's home, and not a property of the BIN. The file declares
this itself with a `# bintel: Pays = accepted_in` line, so the value is kept
and never stored as the BIN's country. Without that line the column read as an
issuing country and attributed Russian-issued BINs (`404059` ZHELDORBANK JSB,
`417628` QIWI BANK) to Afghanistan.

**`latitude`/`longitude` in `binlist-data.csv` are country centroids.**
`37.0902, -95.7129` is the geographic centre of the United States, repeated on
every US row. The columns are recognised so the file loads and deliberately
never stored as an address: a country centroid is not a bank's location. The
values are still kept verbatim in the source-row archive — `python -m app.cli
origin <bin>` shows them.
