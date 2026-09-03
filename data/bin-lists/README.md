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
