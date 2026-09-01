# Bin-Tel

**Worldwide BIN/IIN and financial-institution intelligence for the desktop.**

Bin-Tel resolves a Bank Identification Number to the institution that issued it,
and tells you what that institution is: its legal name, country, network, card
attributes and address. It runs entirely against a local SQLite database built
from a BIN list you maintain yourself, so every lookup is instant and works
offline.

It is a personal research tool. There is no account, no licence, no telemetry
and nothing phones home.

```
python -m app.main
```

---

## What it is, and what it is not

Bin-Tel is an **issuer metadata** tool. It works with the first 6–8 digits of a
card number — the part that identifies the bank, not the cardholder.

It never stores, collects, validates or processes full payment-card numbers,
CVVs, PINs, magnetic-stripe data, cardholder names or any other payment
authentication information. Enter something card-length and it is refused, not
truncated and not logged. Exports and reports carry BIN and issuer metadata
only. Log output is passed through a redaction filter before it is written.

---

## Features

| | |
|---|---|
| **BIN lookup** | Range-aware and length-aware: an exact 8-digit assignment or an account range always outranks the 6-digit root it sits under. Reports every relationship, its standing and its confidence. |
| **Bank lookup** | Search an institution by display name, legal name or alias — exact, prefix, contains or fuzzy — and browse its whole BIN portfolio. |
| **Advanced search** | Nineteen criteria combined in one query, with saved searches and favourites. |
| **Command palette** | `Ctrl+K` from anywhere: pages, commands, BINs and institutions in one list. |
| **Watchlists** | Track BINs, institutions and countries, and be told exactly what each database update changed. |
| **Analytics** | Coverage, distribution and growth, charted from the database in front of you. |
| **Institution Intelligence** | A full profile and portfolio analysis for one issuer. |
| **Report Centre** | CSV, JSON, TXT, PDF and XLSX, with reusable templates. |
| **Database Administration** | A measured health score, integrity checks, reindex, vacuum, orphan removal, backup and restore. |
| **Your own BIN list** | The database is built from one file you maintain, [`data/bin-list.csv`](docs/BIN_LIST.md). Add rows, rebuild, done. |
| **Reversible rebuilds** | The database a rebuild replaces is kept, so a bad list is one command away from undone. |
| **Updates** | Verified, atomic database installs with automatic rollback. |
| **Five themes** | Midnight, Professional Light, Slate, Ocean and Graphite — every surface, every page. |

---

## Installing

Bin-Tel needs Python 3.12 or newer.

```bash
git clone https://github.com/G33l0/Bin-Tel.git
cd Bin-Tel
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m app.main
```

Optional report formats need one extra package each; both degrade gracefully
when absent:

```bash
pip install reportlab      # PDF reports
pip install openpyxl       # XLSX reports
```

### First run

Bin-Tel has no database when it first starts. Build one from your BIN list:

1. put your BINs in `data/bin-list.csv` — at minimum a `bin,bank` header and one
   row per BIN;
2. run `python -m app.cli rebuild` (or, in the app, **Database → Rebuild from
   BIN list**).

That is the whole loop, and it is the same loop every time the list changes.
The rebuild builds a complete new database in staging, verifies it, and only
then swaps it in — so a rebuild that fails leaves what you had untouched, and
one that succeeds keeps the database it replaced for `python -m app.cli
rollback`. See [docs/BIN_LIST.md](docs/BIN_LIST.md) for the format and every
rule the reader enforces.

To try the interface with nothing of your own, build a synthetic package:

```bash
python scripts/build_sample_database.py --output dist/database --bins 5000
python -m app.main --manifest-url "file://$PWD/dist/database/database-manifest.json"
```

Every record in that package is generated. The issuer names are invented.

---

## Where things live

| Platform | Data | Configuration | Logs |
|---|---|---|---|
| Linux | `~/.local/share/bintel` | `~/.config/bintel` | `~/.local/state/bintel/logs` |
| macOS | `~/Library/Application Support/Bin-Tel` | `~/Library/Preferences/Bin-Tel` | `~/Library/Logs/Bin-Tel` |
| Windows | `%LOCALAPPDATA%\Bin-Tel` | `%APPDATA%\Bin-Tel` | `%LOCALAPPDATA%\Bin-Tel\logs` |

Set `BINTEL_DATA_DIR` to override all three. Create a `portable.marker` file
beside the executable (or set `BINTEL_PORTABLE=1`) to keep everything in one
folder — useful on a USB stick.

**Two databases, deliberately.** The intelligence database is replaceable: an
update swaps the whole file. Your own data — saved searches, favourites,
watchlists, history, templates — lives in a separate
`bintel-user.sqlite` that updates never touch, and cross-references the
intelligence database by value (BIN digits, institution UID) rather than by row
id. See [docs/DATABASE.md](docs/DATABASE.md).

---

## Command line

```bash
python -m app.cli --help

python -m app.cli rebuild                                  # from data/bin-list.csv
python -m app.cli rebuild --list my-bins.csv --allow-shrink
python -m app.cli rollback                                 # undo the last rebuild
python -m app.cli check-list --strict                      # read the list, change nothing
python -m app.cli init-db --database db.sqlite --db-version 2026.01.1
python -m app.cli import-data --source data/issuers.csv   # via staging
python -m app.cli import-data --source data/issuers.csv --no-stage
python -m app.cli verify-db --quick
python -m app.cli stats --json
python -m app.cli lookup 414720
python -m app.cli export --bin 414720 --format json --output record.json
python -m app.cli dedupe --detect-only
python -m app.cli quality --json
python -m app.cli staging --batch <id>
python -m app.cli reindex
python -m app.cli backup --output ./backups --keep 5
python -m app.cli restore ./backups/bintel-2026.01.1.sqlite
```

---

## Keyboard

| | |
|---|---|
| `Ctrl+K` / `Ctrl+P` | Command palette |
| `Ctrl+1` … `Ctrl+7` | Jump to a page |
| `Ctrl+D` | Database · `Ctrl+U` Updates |
| `Ctrl+B` | Collapse the sidebar · `Ctrl+T` cycle theme |
| `Ctrl+,` | Settings · `F5` refresh the current page |
| `Esc` | Clear a search, or close the palette |

---

## Development

```bash
pytest                      # 430 tests
pytest -m "not gui"         # skip the offscreen interface tests
ruff check app tests
mypy app
```

Packaging scripts for each platform live in `scripts/`
(`build_linux.py`, `build_macos.py`, `build_windows.py`).

### Architecture

```
app/
  core/          paths, configuration, errors, logging, the application context
  models/        ORM entities (intelligence + user data) and the DTOs the UI sees
  database/      engine, schema, indexes, integrity, migrations, user-data store
  repositories/  every SQL query in the application
  normalizers/   names, networks, card attributes, geography, confidence scoring
  services/      lookup, search, analytics, reports, watchlists, updates, health…
  providers/     manifests, downloads, compression, delta planning
  workers/       QRunnable wrappers, so nothing blocking runs on the GUI thread
  ui/            themes, widgets, pages, dialogs, windows
```

The rule that shapes all of it: **widgets ask services, services ask
repositories, repositories own the SQL.** No page imports the ORM.

---

## Documentation

| | |
|---|---|
| [BIN_LIST.md](docs/BIN_LIST.md) | The list your database is built from, and the rebuild loop |
| [LOOKUP.md](docs/LOOKUP.md) | The lookup engine: allocations, specificity, confidence, conflicts |
| [DATABASE.md](docs/DATABASE.md) | Schema, the two-database split, indexes, integrity, health |
| [UPDATES.md](docs/UPDATES.md) | Manifests, the install pipeline, rollback, deltas |
| [WATCHLISTS.md](docs/WATCHLISTS.md) | Change detection and alerts |
| [REPORTING.md](docs/REPORTING.md) | Report types, formats and templates |
| [ANALYTICS.md](docs/ANALYTICS.md) | What each figure measures |
| [PRIVACY.md](docs/PRIVACY.md) | Exactly what is and is not collected |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Building the app and publishing a database release |

---

## Licence

See [LICENSE](LICENSE).
