# Database updates

An update replaces the whole intelligence database. That is only safe because
nothing is written to the working file until a downloaded copy has proved
itself, and because a verified backup exists to fall back to.

---

## The manifest

A distribution server publishes one JSON manifest describing the current
package:

```json
{
  "version": "2026.02.1",
  "schema_version": 1,
  "min_schema_version": 1,
  "release_date": "2026-02-04T09:00:00Z",
  "database_size": 128450560,
  "compressed_size": 19203344,
  "record_count": 512430,
  "institution_count": 24817,
  "sha256": "3f6c…",
  "download_url": "packages/bintel-2026.02.1.sqlite.xz",
  "compression": "xz",
  "edition": "community",
  "publisher": "Bin-Tel Project",
  "minimum_app_version": "1.0.0",
  "deltas": [
    { "from_version": "2026.01.1", "url": "deltas/2026.01.1-2026.02.1.patch",
      "size": 2118400, "sha256": "a91b…" }
  ]
}
```

Notes on the fields:

- `sha256` is accepted as an alias for `checksum` and folded into
  `sha256:<digest>` form during validation.
- `download_url` may be relative; it is resolved against the manifest's own
  location. Absolute URLs are restricted to `https`, `http` and `file` — any
  other scheme is rejected outright.
- `compression` is one of `none`, `gzip`, `xz`, `bz2`. `transfer_size` is what
  actually crosses the wire; `required_storage` accounts for the staging copy
  plus the expanded database.
- `minimum_app_version`, `schema_version` and `min_schema_version` drive
  `manifest.compatibility(...)`, which is what stops an old client installing a
  package it cannot read, and a new client installing one it has outgrown.

Configure the manifest URL in Settings → Database, with `--manifest-url` on the
command line, or via the `BINTEL_MANIFEST_URL` environment variable. It is
never hard-coded at a call site; `DEFAULT_MANIFEST_URL` in
`app/core/constants.py` is the only default.

---

## The install pipeline

`DatabaseUpdateService.install()` runs these steps in order. A failure at any
step raises, and the working database is left exactly as it was.

1. **Plan** — check whether a delta applies (see below). Log the decision.
2. **Download** to a staging file in the downloads directory, never over the
   live database. Progress is throttled to ~10 Hz.
3. **Checksum** the transferred file against the manifest. A mismatch raises
   `ChecksumMismatchError` and stops here.
4. **Decompress** into staging, if the package is compressed.
5. **Verify** the staged file as a real SQLite database: integrity check,
   expected tables, schema version, foreign keys, non-empty.
6. **Migrate** in staging if the package's schema is behind the application's,
   and only when every intervening migration is registered.
7. **Back up** the current database (unless turned off).
8. **Close** the working database, so no handle is open across the swap.
9. **Replace** atomically — `os.replace` on the same filesystem.
10. **Reopen**, reindex, refresh `ANALYZE`.
11. **Stamp** the new version and record the run in the durable update journal.
12. **Post-install hook** — change detection runs, so watchlists can report what
    the release changed.

If anything fails after step 8, the backup is restored and the previous
database is reopened.

### The journal

The `update_history` table lives *inside* the intelligence database, so it is
destroyed by the very operation it records. The durable record is
`update-history.json` in the configuration directory, written atomically, newest
first, capped at a fixed number of entries. That is what the Updates page shows.

---

## Rollback

Backups are written by SQLite's online backup API into the backups directory,
newest kept according to the retention setting. `restore_latest()` is what the
install pipeline calls on failure; the Database Administration page and
`bin-tel-cli restore` expose the same thing manually.

A restore is followed by a verification pass. A backup that does not verify is
not silently accepted.

---

## Deltas

The manifest can advertise deltas from specific previous versions.
`plan_update()` decides whether one is usable and **logs the reason either
way**:

- the installed version must exactly match a descriptor's `from_version`
- the descriptor must be smaller than the full package by a worthwhile margin
- an applier must be registered for the patch format

`app/providers/delta.py` defines the `DeltaApplier` interface and the planning
logic. **This build registers no applier**, so the plan always resolves to a
full download, with the reason recorded. The architecture is in place; the
patch format is a distribution-side decision that has not been made.

---

## Settings that govern updates

| Setting | Effect |
|---|---|
| `check_mode` | `startup`, `periodic` or `manual` |
| `update_frequency` | daily, weekly or monthly, for periodic checks |
| `download_automatically` | fetch in the background once an update is found |
| `install_automatically` | install without asking (still verified, still backed up) |
| `backup_before_update` | take a snapshot before the swap; on by default |
| `max_backups` | retention |
| `verify_on_startup` | quick-check the database when the application opens |

---

## Failure modes, and what the user sees

| What happened | Behaviour |
|---|---|
| No network | "Bin-Tel could not reach the update server." The application carries on offline; the local database is untouched. |
| Manifest unreadable | The provider is skipped; an optional provider being unavailable is not an error. |
| Checksum mismatch | Refused before anything is unpacked. The staging file is discarded. |
| Downloaded file is not a database | Caught by verification in staging. Nothing is swapped. |
| Package needs a newer application | Rejected with the version it needs, before download. |
| Failure during the swap | Backup restored, previous database reopened, failure recorded in the journal. |
| Disk full | `required_storage` is checked before the download starts. |
