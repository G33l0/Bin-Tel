# Deployment

Two separate things get shipped, on separate schedules, from separate
repositories:

| | The application | The database |
|---|---|---|
| Built by | `scripts/build_linux.py`, `build_macos.py`, `build_windows.py` | `scripts/build_release.py` |
| Ships as | An installer or bundle | A `.sqlite` package plus a manifest |
| Released when | The software changes | The data changes |
| Contains | Code, themes, icons — **no production data** | Data — **no code** |

Keeping them apart is deliberate. A data correction should not require a
software release, and a software release should not force every user to
re-download a large database. The two are tied together only by the schema and
version compatibility fields in the manifest.

---

## Building the application

```bash
pip install -e ".[dev]"

python scripts/build_linux.py              # dist/Bin-Tel/ plus .desktop + icon theme
python scripts/build_linux.py --onefile

python scripts/build_macos.py              # Bin-Tel.app, optionally signed/notarised
python scripts/build_macos.py --sign "Developer ID Application: …"

python scripts/build_windows.py            # dist/Bin-Tel/ plus a versioned .exe
python scripts/build_windows.py --onefile
```

Each script wraps PyInstaller with the right hidden imports, data files
(themes, icons, reference data) and platform metadata. `build_common.py` holds
what they share.

Before tagging a release:

```bash
pytest
ruff check app tests scripts
mypy app
python -m app.main --version
```

### Portable builds

A build is portable if a `portable.marker` file sits beside the executable, or
`BINTEL_PORTABLE=1` is set. Data, configuration and logs then live in a
`data/` folder beside the executable rather than in the user's profile — useful
for a USB stick or a locked-down machine.

---

## Building a database release

`scripts/build_release.py` runs the whole pipeline and refuses to publish
anything that does not verify:

```
normalize → dedupe → validate → index → integrity → compress → checksum
→ manifest → release directory
```

```bash
python scripts/build_release.py \
    --source data/issuers/ \
    --output dist/release \
    --version 2026.02.1 \
    --edition community \
    --compression xz \
    --notes "February refresh: 12,400 new BINs across 38 markets."
```

What it does, step by step:

1. **Schema** — creates a fresh database with the current schema
2. **Normalize and ingest** — runs the source through the same normalizers and
   ingest service the application uses, so a released package is normalised the
   same way an imported one would be. A directory is imported file by file, so
   one unreadable file cannot abort an otherwise sound build.
3. **Dedupe** — a single evidence-based pass; merges are recorded, borderline
   pairs are left for review, disagreements are recorded as conflicts
4. **Stamp** — writes `database_metadata` and a `database_versions` row
5. **Index and compact** — rebuilds every index, runs `ANALYZE`, then `VACUUM`
6. **Verify** — full integrity check; **a failure here stops the build**
7. **Measure quality** — the twelve metrics, counted and stored in the package,
   so every client reads the same figures instead of each recounting
8. **Compress** — xz by default, typically 85–90% smaller on the wire
9. **Checksum** — SHA-256 of the artefact that is actually transferred
10. **Publish** — copies the artefact, writes `database-manifest.json` and a
    human-readable `RELEASE.md` carrying the measured figures

Output:

```
dist/release/
  bintel-2026.02.1.sqlite.xz
  database-manifest.json
  RELEASE.md
```

Staging is a sibling directory that is removed on the way out (`--keep-staging`
keeps it, along with the uncompressed database).

**Source data must contain issuer metadata only.** Never feed this pipeline full
card numbers, cardholder names or payment authentication data — see
[PRIVACY.md](PRIVACY.md).

---

## Publishing

Upload the release directory to whatever serves your manifest URL. Any static
host works — the client needs nothing but HTTPS and byte ranges.

```
https://dist.example.org/database/database-manifest.json
https://dist.example.org/database/bintel-2026.02.1.sqlite.xz
```

`download_url` may be relative, so a release directory can be moved or mirrored
without rewriting the manifest.

### Compatibility

Set these honestly; they are what stop a client installing a package it cannot
read:

| Field | Meaning |
|---|---|
| `schema_version` | The schema this package is built with |
| `min_schema_version` | The oldest schema a client may hold and still install it |
| `minimum_app_version` | The oldest application version that may install it |

An application that is too old is told which version it needs, before the
download starts.

### Serving locally

```bash
python scripts/serve_database.py --directory dist/release
python -m app.main --manifest-url http://127.0.0.1:8770/database-manifest.json
```

The development server supports byte ranges, so resume logic can be exercised
against it.

---

## Editions

`--edition` marks which plan a package is for. The licence names the edition a
user is entitled to, so one distribution server can serve community,
professional, business and enterprise packages through the same pipeline and
the same client code. See [MONETIZATION.md](MONETIZATION.md).

---

## Release checklist

**Application**

- [ ] `pytest`, `ruff`, `mypy` clean
- [ ] `python -m app.main` launches and reaches the dashboard
- [ ] Version bumped in `pyproject.toml` and `app/core/constants.py`
- [ ] Built and launched on each target platform
- [ ] macOS build signed and notarised, Windows build signed
- [ ] `minimum_app_version` in future manifests still correct

**Database**

- [ ] Source data reviewed — issuer metadata only
- [ ] `build_release.py` completed with no failed step
- [ ] `RELEASE.md` quality figures reviewed — resolution rate, 8-digit
      coverage, duplicate and conflict rates
- [ ] `python -m app.cli staging` shows nothing unexpectedly held back
- [ ] `RELEASE.md` counts look right against the previous release
- [ ] Installed over the previous version in a scratch profile
- [ ] Watchlist change detection produced sensible alerts against it
- [ ] Manifest served, checksum matches the uploaded artefact
