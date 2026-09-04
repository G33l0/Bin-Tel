# Attribution

`binlist-data.csv` is not ours. It is redistributed here under CC BY 4.0,
which permits that **provided the attribution below travels with it**. If you
copy the file elsewhere, copy this notice too.

---

## binlist-data.csv

**Title** — binlist-data, an open-source list of bank BIN/IIN numbers
**Creator** — Ian Nuttall
**Source** — https://github.com/iannuttall/binlist-data
**Licence** — [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)
(full text in `binlist-data.LICENSE.txt`)
**Retrieved** — 3 September 2026, from `master`
**Modified** — no. The file is byte-for-byte as published.

### What the publisher says about it

Their own words, kept here because they bear on how far any answer built from
this file should be trusted:

> This data is not an official IIN register […] compiled by scraping,
> organising and compiling data from many different sources […] **Use at your
> own risk!**

The repository has been **archived and read-only since 24 December 2020**. It
receives no corrections. A bank that changed hands, a BIN reassigned, or an
institution renamed since then is not reflected in it.

Treat it accordingly: it is a broad starting point, not an authority. Rows you
curate yourself outrank it, and Bin-Tel's confidence scoring already reflects
that — a hand-maintained list row carries 0.9, this file's rows do not
displace it, and any disagreement is recorded as a conflict rather than
silently resolved.

### What is *not* covered by this licence

Fyatu's BIN checker credits this dataset plus contributions from binlist.net,
"merged and extended by Fyatu". Two parts of that are **not** here:

- **binlist.net's data repository** (`github.com/binlist/data`) carries **no
  licence file** — checked, returns 404. Redistribution is therefore not
  established, and it is not included.
- **Fyatu's own merges and extensions** are Fyatu's work, offered through
  their lookup tool. No bulk download is published, `robots.txt` disallows
  `/api/`, and their terms prohibit circumventing rate limits. Nothing from
  them is included.

That is why this folder holds 343,063 rows rather than the ~458,000 a checker
UI may report.
