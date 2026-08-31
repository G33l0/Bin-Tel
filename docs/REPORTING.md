# Reports

The Report Centre builds a report from the database and renders it in whichever
format you need. Every report is metadata only.

---

## Report types

| Type | Built from |
|---|---|
| BIN record | One resolved BIN and its issuer |
| Institution profile | An institution, its BIN portfolio and its distributions |
| Search results | Any advanced-search query, with the criteria recorded |
| Analytics | An analytics snapshot: headline figures, distributions, growth |
| Database health | The seven health checks and what each measured |
| Watchlist activity | Detected changes over a period |

---

## Formats

| Format | Notes |
|---|---|
| CSV | Header block, criteria, summary, then the table |
| JSON | Structured: header, criteria, summary, sections, records, notes |
| TXT | Plain text, readable in a terminal or an email |
| PDF | Branded, paginated, with a cover block and repeating table headers. Needs `reportlab` |
| XLSX | A summary sheet plus a data sheet, with frozen headers. Needs `openpyxl` |

PDF and XLSX are optional dependencies. When one is missing the format is
offered but explains what to install, and the other formats are unaffected.

---

## What a report contains

Title, subtitle, generation timestamp, the database version it was built from,
the criteria that produced it, a summary block, detail sections, the table, and
any notes about truncation.

**What it never contains:** data-source information, source URLs, internal
provider metadata, internal notes, confidence internals — and no cardholder
data of any kind. Provenance exists in the database so conflicts can be
reasoned about, not so it can be printed. Administrative surfaces are where
that lives.

Row caps come from the plan (`Limit.EXPORT_ROWS`). When a report is truncated it
says so explicitly: *"This report contains the first 50,000 of 512,430 matching
records."* — never a silent cut.

---

## Templates

A report definition — type, format, criteria, title — can be saved as a
template and reloaded. Templates are stored in the user-data store, so they
survive database updates. `Limit.REPORT_TEMPLATES` caps how many a plan allows.

---

## Preview

"Preview" renders a trimmed plain-text version so you can see the shape of the
report before generating it. Nothing is written to disk until you export.

---

## Generated reports

Every generated report is recorded — title, type, format, path, row count,
size, database version — and listed under "Recent reports", so a report can be
found again without remembering where it was saved.

---

## Programmatically

```python
content = context.reports.build_search_report(
    query, rows, title="US credit BINs", database_version=context.database_version()
)
result = context.reports.generate(content, ReportFormat.PDF)
print(result.path, result.size_bytes)
```
