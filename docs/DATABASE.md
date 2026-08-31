# The database

Bin-Tel keeps **two** SQLite databases, and the separation is the single most
important decision in the storage design.

| | Intelligence database | User-data store |
|---|---|---|
| File | `bintel.sqlite` | `bintel-user.sqlite` |
| Contents | BINs, institutions, countries, networks, addresses, provenance | Saved searches, favourites, watchlists, licence, history, templates, telemetry queue |
| Lifetime | **Replaceable** — an update swaps the whole file | **Durable** — updates never touch it |
| Written by | The distribution pipeline | The application, as you use it |
| Schema version | `SCHEMA_VERSION` in `app/core/constants.py` | `USER_SCHEMA_VERSION` in `app/database/user_store.py` |

An update replaces the intelligence file wholesale — that is what makes an
install atomic and a rollback trivial. If your watchlists lived in it, every
update would erase them. So they do not.

**Cross-references are by value, never by row id.** A watchlist item stores the
BIN digits `414720` or an institution's stable `uid`, not `bins.id`. Row ids are
an implementation detail of one particular release and are free to change
between packages; the values are the contract.

---

## Intelligence schema

### Reference tables

| Table | Purpose |
|---|---|
| `countries` | ISO 3166 with `iso2`, `iso3`, numeric code, currency, region, endonyms folded into `normalized_name` |
| `networks` | Card schemes — Visa, Mastercard, Amex, UnionPay, JCB, Discover, Diners, Maestro and the rest |
| `sources` | Where a claim came from, with a trust score. **Never shown in a normal result or report.** |
| `database_metadata` | Key/value: version, schema version, publisher, record counts, build time |
| `database_versions` | One row per published release, with what changed relative to the previous one |
| `database_statistics` | Precomputed aggregates so the dashboard does not recount on every open |

### Core tables

**`bins`** — one row per issuer identification number.

`bin`, `iin`, `iin_length`, `bin_int`, `prefix6`, `prefix8`, `network_id`,
`brand`, `card_type`, `funding_type`, `is_prepaid`, `is_commercial`,
`country_id`, `currency_code`, `status`, `confidence`, `first_seen`,
`last_updated`.

`bin_int`, `prefix6` and `prefix8` are denormalised deliberately: prefix and
range queries become integer comparisons and index scans instead of `LIKE`
against a text column.

**`institutions`** — one row per financial institution.

`uid` is the stable identifier that survives a database replacement. Also
`display_name`, `legal_name`, both normalized forms, `short_name`,
`institution_type`, `parent_id` (banks own banks), `country_id`, `website`,
`swift_bic`, `status`, `confidence`.

**`bin_institutions`** — the many-to-many link, with `relationship_type`
(issuer, acquirer, processor, sponsor), `is_primary` and a per-link confidence.
A BIN can legitimately have several institutions; one is primary.

**`bin_ranges`** — allocated ranges, with integer endpoints and a `width`, so a
BIN that has no exact row can still be resolved to its allocation.

**`institution_aliases`** — every name an institution is known by, with an
`alias_type` (legal, trading, former, abbreviation, endonym) and confidence.
This is what makes "NTB", "Meridian Trust" and "Meridian Trust Bank, N.A." find
the same institution.

**`addresses`** — headquarters and branch addresses with normalized city and
postal code and a `fingerprint` used for deduplication.

**`bin_claims`** — a per-source, per-field record of what each source asserted.
Provenance is kept so a conflict can be explained, not so it can be displayed:
sources never appear in a normal lookup result or report.

**`conflicts`** — where two sources disagree, with both values, both
confidences, and a status. Recorded rather than silently resolved.

**`normalization_events`** — an audit trail of what normalization changed.

### History

`bin_history` and `institution_history` record what each release did to a
record — created, updated, retired, reassigned, merged — keyed by the *value*
(`bin`, `institution_uid`) and stamped with the database version.

---

## Indexes

Every hot query is covered:

- `bins`: unique on `bin`; `bin_int`, `prefix6`, `prefix8`; composite covering
  indexes on `(country_id, network_id)`, `(card_type, funding_type)`, and
  `(network_id, card_type)` for the analytics rollups
- `bin_ranges`: `range_low_int` and a `(range_low_int, range_high_int)` span
  index
- `institutions`: unique on `uid`; `normalized_name`, `normalized_legal_name`,
  `country_id`
- `institution_aliases`: `normalized_alias`
- `addresses`: `institution_id`, `normalized_city`, `normalized_postal_code`
- `bin_institutions`: both directions

`ANALYZE` runs after every bulk load, so the query planner has real statistics.

### Connection settings

```
PRAGMA journal_mode = WAL          -- readers never block on a writer
PRAGMA foreign_keys = ON           -- enforced, not assumed
PRAGMA synchronous = NORMAL        -- safe under WAL, much faster than FULL
PRAGMA mmap_size = 268435456       -- memory-mapped reads for large packages
PRAGMA temp_store = MEMORY
```

---

## Integrity

`app/database/integrity.py` runs, in order:

1. The file exists and is non-empty
2. `PRAGMA integrity_check` (or `quick_check` for the fast path)
3. Every expected table is present
4. The schema version is readable and within the supported range
5. `PRAGMA foreign_key_check` reports no violations
6. The database holds at least one BIN record

A package that fails any of these is never installed. An empty database created
by `bin-tel-cli init-db` fails the last check by design — a shell to import into
is not a package to ship.

---

## Health

The Database Administration page scores seven measured checks and reports a
weighted mean. Every figure comes from a query against the database in front of
you; nothing is assumed.

| Check | What it measures |
|---|---|
| Integrity | `PRAGMA integrity_check` |
| Indexes | Every declared index exists |
| Duplicates | Duplicate BIN rows and duplicate aliases |
| Orphans | Rows pointing at records that no longer exist |
| Conflicts | Unresolved conflicting claims |
| Relationships | BINs that resolve to an institution |
| Completeness | Average field coverage across the important columns |

Scores are ratio-based with a tolerance, so one orphan in a million rows is not
scored the same as a structurally broken file.

---

## Maintenance

```bash
python -m app.cli verify-db          # full integrity check
python -m app.cli verify-db --quick  # fast check
python -m app.cli reindex            # rebuild indexes, refresh ANALYZE
python -m app.cli stats --json
python -m app.cli dedupe --detect-only
python -m app.cli backup --output ./backups --keep 5
python -m app.cli restore ./backups/bintel-2026.01.1.sqlite
```

Backups use SQLite's online backup API, so a snapshot can be taken while the
database is open, and the copy is always consistent.

---

## Normalization and deduplication

Records are normalized on the way in, never on the way out:

- **Names** — case, punctuation and whitespace folded; legal suffixes (`N.A.`,
  `plc`, `S.A.`, `AG`, `Inc`) stripped into a separate list; abbreviations
  expanded (`CU` → `credit union`, `FCU`, `NB`, `Intl`), so `Northshore CU` and
  `Northshore Credit Union` normalise to the same core form
- **Networks** — every spelling of a scheme resolves to one code
- **Card attributes** — tri-state booleans, so *unknown* stays distinct from
  *false*
- **Geography** — country names, ISO codes and endonyms (`Deutschland`,
  `España`, `Türkiye`) resolve to one ISO 3166 record; an unknown market is kept
  verbatim rather than guessed at

**Merging requires evidence.** A high name-similarity score alone is never
enough. `MatchScore.can_merge` demands a score above `MERGE_THRESHOLD` **and**
at least one corroborating signal — the same website host, the same SWIFT/BIC
prefix, shared BINs, or the same postal code. Two institutions with identical
names in different countries do not merge; the disagreement is recorded as a
conflict instead.
