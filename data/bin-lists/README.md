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

Three samples, ~20–30 rows each, of a personally compiled BIN dataset. They
are here so the repository has something real to build and test against; the
full files replace them in place, keeping the same names or taking new ones.

| File | Shape |
|---|---|
| `sample-a-issuer-contacts.tsv` | `BIN Brand Type Category Issuer IssuerPhone IssuerUrl isoCode2 isoCode3 CountryName` |
| `sample-b-by-country.tsv` | `BIN Pays Emetteur Marque Type Niveau` — French headers, grouped by country |
| `sample-c-coordinates.tsv` | `bin brand type category issuer alpha_2 alpha_3 country latitude longitude bank_phone` |

All three are tab-separated. All three parse without conversion.

BIN prefixes, issuer names, schemes and countries only. No card numbers, no
cardholder data — the reader refuses anything longer than eight digits.

---

## These files have been through a spreadsheet

**A and C need `--pad-short-bins`. B does not** — it is clean six-digit
throughout, and the flag is harmless to it.

Excel read the BIN column as a number and dropped the leading zeros, one zero
from the five-digit values and two from the four-digit ones. It also turned one
phone number into `5.51732E+11`, which is how we know the file passed through a
spreadsheet at all. That value is dropped on read; its digits are gone.

Padding restores a BIN to a length something is actually assigned at — four and
five digits become six, seven becomes eight. **It cannot restore an eight-digit
BIN beginning `00`**, which survives as six digits and is indistinguishable
from a genuine six-digit BIN.

So these files are recoverable, not clean. If a source can be re-exported with
the BIN column formatted as **Text**, that export is better than the padded
read of this one, and should replace it.

## Two things the data says that are worth knowing

**`Pays` in B is the country of issuance, not the bank's home.** Cards issued
in Afghanistan by Bank Alfalah (Pakistani) and CSCBank SAL (a Lebanese
processor) are filed under Afghanistan. That is probably right for the cards
and definitely not a claim about where those institutions are registered.

**`latitude`/`longitude` in C are country centroids.** `37.0902, -95.7129` is
the geographic centre of the United States, repeated on every US row. The
columns are recognised so the file loads and deliberately never stored: a
country centroid is not a bank's address.
