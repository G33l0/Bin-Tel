"""Maintenance command line for Bin-Tel database administrators.

These tools operate on the same database, importers and services the desktop
application uses. They are for building, inspecting and repairing a database —
they do not replace the desktop application.

    python -m app.cli init-db
    python -m app.cli import-data --source data.csv --dry-run
    python -m app.cli dedupe
    python -m app.cli verify-db
    python -m app.cli stats
    python -m app.cli export --bin 414720
    python -m app.cli version
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - direct-script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import ConfigManager
from app.core.constants import APP_NAME, APP_VERSION, SCHEMA_VERSION, UNKNOWN_DISPLAY
from app.core.errors import BinTelError
from app.core.logging_config import get_logger, setup_logging
from app.core.paths import get_paths, reset_paths_cache
from app.database.engine import DatabaseManager
from app.database.schema import create_schema, list_indexes, rebuild_indexes, write_metadata
from app.models.entities import DatabaseMetadata
from app.services.bin_list import BIN_LIST_FILENAME
from app.utils.formatting import format_bytes, format_number

logger = get_logger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID = 2


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


def _resolve_database(args: argparse.Namespace) -> Path:
    if getattr(args, "database", None):
        return Path(args.database).expanduser()
    if getattr(args, "data_dir", None):
        import os

        os.environ["BINTEL_DATA_DIR"] = str(Path(args.data_dir).expanduser())
        reset_paths_cache()
    config = ConfigManager(get_paths())
    config.load()
    return config.database_path()


def _open(args: argparse.Namespace, *, create: bool = False) -> DatabaseManager:
    path = _resolve_database(args)
    if not create and not path.exists():
        raise BinTelError(
            f"No database at {path}. Run `python -m app.cli init-db` first, or launch "
            f"{APP_NAME} to download one.",
            title="Database not found",
        )
    manager = DatabaseManager(path)
    manager.open(create_if_missing=create)
    return manager


def _print_table(rows: list[tuple[str, str]], indent: str = "  ") -> None:
    if not rows:
        return
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{indent}{label:<{width}}  {value}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_init_db(args: argparse.Namespace) -> int:
    """Create an empty database with the full schema and index set."""
    path = _resolve_database(args)
    if path.exists() and not args.force:
        print(f"error: {path} already exists. Use --force to recreate it.", file=sys.stderr)
        return EXIT_INVALID
    if path.exists():
        path.unlink()
        for suffix in ("-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)

    manager = DatabaseManager(path)
    manager.open(create_if_missing=True)
    create_schema(manager.engine)

    from app.services.ingest_service import IngestService

    with manager.transaction() as session:
        IngestService(session, source_code="cli", source_name="CLI").seed_reference_data()
        write_metadata(
            session,
            {
                DatabaseMetadata.VERSION: args.version,
                DatabaseMetadata.SCHEMA_VERSION: SCHEMA_VERSION,
                DatabaseMetadata.PUBLISHER: args.publisher,
                DatabaseMetadata.RECORD_COUNT: 0,
            },
        )
    indexes = len(list_indexes(manager.engine))
    manager.close()
    print(f"Created {path}")
    print(f"  schema version {SCHEMA_VERSION} · {indexes} indexes · reference data seeded")
    return EXIT_OK


def cmd_import_data(args: argparse.Namespace) -> int:
    """Stream a CSV/JSON/JSONL/SQLite source into the database."""
    from app.importers.base import ImportOptions
    from app.importers.batch import BatchImporter
    from app.importers.registry import importer_for

    source = Path(args.source).expanduser()
    options = ImportOptions(
        source=source,
        dry_run=args.dry_run,
        update=args.update,
        dedupe=args.dedupe,
        batch_size=args.batch_size,
        limit=args.limit,
        source_code=args.source_code,
        source_name=args.source_name,
        encoding=args.encoding,
        delimiter=args.delimiter,
    )
    manager = _open(args, create=args.create)
    if args.create:
        create_schema(manager.engine)

    try:
        if args.stage and not args.dry_run:
            return _import_through_staging(manager, options, source, args)
        if source.is_dir():
            batch = BatchImporter(options)
            print(f"Importing {len(batch.files())} file(s) from {source}…")
            summary = batch.run_all(manager, progress=_progress)
            print(f"\n{summary.summary}")
            for path, reason in summary.failures:
                print(f"  ! {path.name}: {reason}", file=sys.stderr)
        else:
            importer = importer_for(options, name=args.format)
            total = importer.estimated_total()
            print(
                f"Importing {source.name} with the {importer.name} importer"
                + (f" (~{format_number(total)} rows)" if total else "")
                + (" [dry run]" if args.dry_run else "")
                + "…"
            )
            summary = importer.run(manager, progress=_progress)
            print(f"\n{summary.summary}")
            if summary.dedupe_summary:
                print(f"Deduplication: {summary.dedupe_summary}")
            for error in summary.result.errors[:10]:
                print(f"  ! {error}", file=sys.stderr)
            if len(summary.result.errors) > 10:
                print(
                    f"  ! …and {len(summary.result.errors) - 10} more skipped rows",
                    file=sys.stderr,
                )
        if not args.dry_run:
            rebuild_indexes(manager.engine)
            _refresh_record_count(manager)
    finally:
        manager.close()
    return EXIT_OK


def _import_through_staging(
    manager: DatabaseManager,
    options: Any,
    source: Path,
    args: argparse.Namespace,
) -> int:
    """Import via the staging layer — the default, and the safe path.

    Records land in ``staging_records``, are normalized, validated and
    resolved there, and only reach production once they pass. A source with
    bad rows then spoils the staging table rather than the database people
    are looking things up in.
    """
    from app.importers.batch import BatchImporter
    from app.importers.registry import importer_for
    from app.services.ingest_service import IngestService
    from app.services.staging_service import StagingService

    importer = (
        BatchImporter(options) if source.is_dir() else importer_for(options, name=args.format)
    )
    print(f"Importing {source.name} through staging…")

    session = manager.new_session()
    try:
        staging = StagingService(session)
        ingest = IngestService(
            session,
            source_code=options.source_code,
            source_name=options.source_name,
        )
        ingest.seed_reference_data()
        report = staging.run(importer.iter_records(), ingest)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    print(f"\n{report.summary}")
    for note in dict.fromkeys(report.issues):
        print(f"  · {note}")
    if report.held:
        print(
            f"\n{report.held} record(s) held back. "
            f"Inspect them with:\n  python -m app.cli staging --batch {report.batch_id}"
        )
    if report.promoted:
        rebuild_indexes(manager.engine)
        _refresh_record_count(manager)
    return EXIT_OK


def _progress(processed: int, message: str) -> None:
    print(f"\r  {message}", end="", flush=True)


def _refresh_record_count(manager: DatabaseManager) -> None:
    from sqlalchemy import func, select

    from app.models.entities import Bin

    with manager.transaction() as session:
        count = int(session.execute(select(func.count()).select_from(Bin)).scalar() or 0)
        write_metadata(session, {DatabaseMetadata.RECORD_COUNT: count})


def cmd_rebuild(args: argparse.Namespace) -> int:
    """Rebuild the whole database from the personal BIN list."""
    from app.services.bin_list import default_bin_list_path
    from app.services.rebuild_service import RebuildService

    path = _resolve_database(args)
    list_path = Path(args.list).expanduser() if args.list else default_bin_list_path()

    manager = DatabaseManager(path)
    if path.exists():
        manager.open()

    service = RebuildService(manager, path)
    try:
        outcome = service.rebuild(
            list_path,
            version=args.db_version,
            progress=lambda message: print(f"  {message}"),
            allow_shrink=args.allow_shrink,
        )
    finally:
        manager.close()

    print()
    print(f"Database {outcome.version} is live.")
    _print_table(
        [
            ("BINs", f"{outcome.accepted:,}"),
            ("Institutions", f"{outcome.institutions:,}"),
            ("Ranges", f"{outcome.ranges:,}"),
            ("Duplicates superseded", f"{outcome.duplicates:,}"),
            ("Rows skipped", f"{outcome.rejected:,}"),
            ("Conflicts recorded", f"{outcome.conflicts:,}"),
            ("Replaced version", outcome.previous_version or "none"),
            ("Rollback available", "yes" if outcome.can_roll_back else "no"),
            ("Elapsed", f"{outcome.elapsed_seconds:.1f}s"),
        ]
    )
    if outcome.problems:
        print()
        print(f"{outcome.rejected:,} row(s) were skipped:")
        for problem in outcome.problems:
            print(f"  {problem}")
        if outcome.rejected > len(outcome.problems):
            print(f"  … and {outcome.rejected - len(outcome.problems):,} more")
    return EXIT_OK


def cmd_rollback(args: argparse.Namespace) -> int:
    """Put back the database the last rebuild replaced."""
    from app.services.rebuild_service import RebuildService

    path = _resolve_database(args)
    manager = DatabaseManager(path)
    if path.exists():
        manager.open()
    service = RebuildService(manager, path)
    try:
        service.rollback()
    finally:
        manager.close()
    print(f"Rolled back. {path} is the previous database again.")
    print("Run the same command to roll forward: neither copy is discarded.")
    return EXIT_OK


def cmd_check_list(args: argparse.Namespace) -> int:
    """Read the BIN list and report on it without touching the database."""
    from app.services.bin_list import default_bin_list_path, read_bin_list

    list_path = Path(args.list).expanduser() if args.list else default_bin_list_path()
    report = read_bin_list(list_path)
    print(f"{list_path}")
    _print_table(
        [
            ("Columns used", ", ".join(report.columns)),
            ("BINs accepted", f"{report.accepted:,}"),
            ("Duplicates superseded", f"{report.duplicates:,}"),
            ("Rows skipped", f"{report.rejected:,}"),
        ]
    )
    if report.problems:
        print()
        for problem in report.problems:
            print(f"  {problem}")
        return EXIT_ERROR if args.strict else EXIT_OK
    return EXIT_OK


def cmd_dedupe(args: argparse.Namespace) -> int:
    """Find and, where the evidence supports it, resolve duplicate records."""
    from app.services.dedupe_service import DedupeService

    manager = _open(args)
    try:
        session = manager.new_session()
        try:
            service = DedupeService(session, dry_run=args.dry_run)
            report = service.run(merge=not args.detect_only)
            if args.dry_run:
                session.rollback()
            else:
                session.commit()
        finally:
            session.close()
    finally:
        manager.close()

    print(("[dry run] " if args.dry_run else "") + report.summary)
    if report.merged:
        print("\nMerged:")
        for candidate in report.merged[:20]:
            print(f"  {candidate.label}\n      {candidate.reason}")
    if report.review_candidates:
        print(f"\nAwaiting review ({len(report.review_candidates)}):")
        for candidate in report.review_candidates[:20]:
            print(f"  {candidate.label}\n      {candidate.reason}")
    if report.conflicts_recorded:
        print(f"\n{report.conflicts_recorded} conflicting claim(s) recorded rather than overwritten.")
    return EXIT_OK


def cmd_verify_db(args: argparse.Namespace) -> int:
    """Run the same verification the application runs before activating a database."""
    from app.database.integrity import verify_database

    path = _resolve_database(args)
    report = verify_database(path, quick=args.quick)
    print(f"{path}")
    _print_table(
        [
            ("Result", "PASS" if report.ok else "FAIL"),
            ("Integrity check", report.integrity_result or "—"),
            ("Database version", report.database_version or "unknown"),
            ("Schema version", str(report.schema_version or "unknown")),
            ("BIN records", format_number(report.bin_count)),
            ("Institutions", format_number(report.institution_count)),
            ("Size", format_bytes(report.size_bytes)),
        ]
    )
    for warning in report.warnings:
        print(f"  warning: {warning}")
    for error in report.errors:
        print(f"  error:   {error}", file=sys.stderr)
    return EXIT_OK if report.ok else EXIT_ERROR


def cmd_stats(args: argparse.Namespace) -> int:
    """Print database counts and coverage."""
    from app.repositories.metadata_repository import MetadataRepository
    from app.repositories.stats_repository import StatsRepository

    manager = _open(args)
    try:
        stats = StatsRepository(manager).stats()
        metadata = MetadataRepository(manager).all()
        top_countries = StatsRepository(manager).top_countries(8)
        top_networks = StatsRepository(manager).top_networks(8)
    finally:
        manager.close()

    if args.json:
        print(
            json.dumps(
                {
                    "metadata": metadata,
                    "counts": stats.model_dump(),
                    "top_countries": dict(top_countries),
                    "top_networks": dict(top_networks),
                },
                indent=2,
            )
        )
        return EXIT_OK

    print(f"{APP_NAME} database")
    _print_table(
        [
            ("Version", metadata.get(DatabaseMetadata.VERSION, "unknown")),
            ("Schema", metadata.get(DatabaseMetadata.SCHEMA_VERSION, "unknown")),
            ("Publisher", metadata.get(DatabaseMetadata.PUBLISHER, "unknown")),
            ("BINs", format_number(stats.bins)),
            ("BIN ranges", format_number(stats.bin_ranges)),
            ("Institutions", format_number(stats.institutions)),
            ("Aliases", format_number(stats.aliases)),
            ("Addresses", format_number(stats.addresses)),
            ("Countries covered", format_number(stats.countries)),
            ("Networks", format_number(stats.networks)),
            ("Open conflicts", format_number(stats.conflicts_open)),
        ]
    )
    if top_countries:
        print("\nTop countries")
        _print_table([(name, format_number(count)) for name, count in top_countries])
    if top_networks:
        print("\nTop networks")
        _print_table([(name, format_number(count)) for name, count in top_networks])
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    """Export a BIN record or an institution's BIN list."""
    from app.repositories.bin_repository import BinRepository
    from app.repositories.institution_repository import InstitutionRepository
    from app.services.bank_service import BankService
    from app.services.export_service import ExportFormat, ExportService
    from app.services.lookup_service import LookupService

    if not args.bin and not args.bank:
        print("error: pass --bin or --bank.", file=sys.stderr)
        return EXIT_INVALID

    manager = _open(args)
    exports = ExportService()
    fmt = ExportFormat(args.format)
    try:
        if args.bin:
            result = LookupService(BinRepository(manager)).lookup(args.bin)
            if not result.found or result.best is None:
                print(f"No record found for {args.bin}.", file=sys.stderr)
                return EXIT_ERROR
            content = exports.render_record(result.best, fmt)
        else:
            service = BankService(InstitutionRepository(manager), BinRepository(manager))
            matches = service.search(args.bank, limit=1)
            if not matches.found or matches.best is None:
                print(f"No institution matched {args.bank!r}.", file=sys.stderr)
                return EXIT_ERROR
            rows = service.all_bins(matches.best.id)
            content = exports.render_rows(rows, fmt, title=matches.best.display_name)
    finally:
        manager.close()

    if args.output:
        destination = Path(args.output).expanduser()
        exports.write(destination, content)
        print(f"Wrote {destination}")
    else:
        print(content)
    return EXIT_OK


def cmd_lookup(args: argparse.Namespace) -> int:
    """Resolve a BIN from the command line."""
    from app.repositories.bin_repository import BinRepository
    from app.services.lookup_service import LookupService

    manager = _open(args)
    try:
        result = LookupService(BinRepository(manager)).lookup(args.bin)
    finally:
        manager.close()

    if not result.found or result.best is None:
        print(f"No record found for {args.bin}.", file=sys.stderr)
        return EXIT_ERROR

    record = result.best
    print(f"{record.bin}  ({result.match_label}, {result.elapsed_ms:.1f} ms)")
    _print_table([(label, value) for label, value in record.to_field_pairs()])

    # The reasoning, not just the answer: which relationships the allocation
    # supports, whether anything disagrees, and what the query did not reach.
    if result.relationships:
        print("\nRelationships")
        _print_table(
            [
                (
                    f"{item.relationship_label} ({item.standing_label})",
                    f"{item.display_name}"
                    + (
                        f" · {item.effective_period}"
                        if item.effective_period != UNKNOWN_DISPLAY
                        else ""
                    ),
                )
                for item in result.relationships
            ]
        )
    else:
        print("\nNo institution relationship is recorded for this prefix.")

    print(f"\nConfidence: {result.confidence_level.capitalize()} ({result.confidence_percent}%)")
    for reason in result.confidence_reasons:
        print(f"  · {reason}")

    if result.is_conflicted:
        names = ", ".join(item.display_name for item in result.conflicting_institutions)
        print(f"\nConflict: records also name {names}. Both readings are kept.")
    if result.more_specific_count:
        print(
            f"\n{result.more_specific_count} more specific assignment(s) exist beneath "
            "this prefix and may belong to other institutions."
        )
    return EXIT_OK


def cmd_reindex(args: argparse.Namespace) -> int:
    """Recreate every index and refresh the query planner's statistics."""
    from app.database.schema import analyze

    manager = _open(args)
    try:
        created = rebuild_indexes(manager.engine)
        analyze(manager.engine)
        total = len(list_indexes(manager.engine))
    finally:
        manager.close()
    print(f"Indexes verified: {total} present ({len(created)} covering indexes ensured).")
    return EXIT_OK


def cmd_backup(args: argparse.Namespace) -> int:
    """Create a verified snapshot of the database."""
    from app.database.backup import create_backup, list_backups, prune_backups

    path = _resolve_database(args)
    destination = Path(args.output).expanduser() if args.output else get_paths().backups_dir
    backup = create_backup(path, destination)
    if args.keep:
        prune_backups(destination, args.keep)
    print(f"Backup written to {backup} ({format_bytes(backup.stat().st_size)})")
    print(f"{len(list_backups(destination))} backup(s) in {destination}")
    return EXIT_OK


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore a snapshot after verifying it."""
    from app.database.backup import restore_backup

    path = _resolve_database(args)
    backup = Path(args.backup).expanduser()
    restore_backup(backup, path)
    print(f"Restored {backup} to {path}")
    return EXIT_OK


def cmd_quality(args: argparse.Namespace) -> int:
    """Measure and print the database's quality metrics."""
    from app.repositories.metadata_repository import MetadataRepository
    from app.services.quality_service import DataQualityService

    manager = _open(args)
    try:
        version = MetadataRepository(manager).all().get(DatabaseMetadata.VERSION)
        service = DataQualityService(manager)
        report = service.evaluate(database_version=version)
        if report.error:
            print(report.error, file=sys.stderr)
            return EXIT_ERROR
        if args.store:
            written = service.store(report)
            print(f"Stored {written} metric(s) in the database.\n")
    finally:
        manager.close()

    if args.json:
        print(
            json.dumps(
                {
                    "database_version": report.database_version,
                    "computed_at": report.computed_at.isoformat(),
                    "metrics": [
                        {
                            "key": metric.key,
                            "label": metric.label,
                            "numerator": metric.numerator,
                            "denominator": metric.denominator,
                            "ratio": metric.ratio,
                        }
                        for metric in report.metrics
                    ],
                },
                indent=2,
            )
        )
        return EXIT_OK

    print(f"{APP_NAME} data quality — {report.database_version or 'unknown version'}")
    _print_table(
        [(metric.label, f"{metric.display}  ({metric.detail})") for metric in report.metrics]
    )
    print(f"\n{report.summary}")
    return EXIT_OK


def cmd_staging(args: argparse.Namespace) -> int:
    """Show what is held in the staging layer, and why."""
    from app.services.staging_service import StagingService

    manager = _open(args)
    try:
        session = manager.new_session()
        try:
            service = StagingService(session)
            counts = service.counts(args.batch)
            held = service.pending(args.batch)
            if args.clear:
                removed = service.clear(args.batch)
                session.commit()
                print(f"Cleared {removed} staged record(s).")
                return EXIT_OK
        finally:
            session.close()
    finally:
        manager.close()

    if not counts:
        print("Nothing is staged.")
        return EXIT_OK

    print("Staged records")
    _print_table([(status.capitalize(), format_number(count)) for status, count in sorted(counts.items())])
    if held:
        print("\nHeld back")
        for row in held:
            print(f"  {row.prefix or '—':12} {row.status:12} {row.issues or ''}")
    return EXIT_OK


def cmd_version(args: argparse.Namespace) -> int:
    """Print application, schema and database versions."""
    import platform

    rows = [
        ("Application", APP_VERSION),
        ("Supported schema", str(SCHEMA_VERSION)),
        ("Python", platform.python_version()),
        ("Platform", f"{platform.system()} {platform.release()} ({platform.machine()})"),
    ]
    path = _resolve_database(args)
    rows.append(("Database file", str(path)))
    if path.exists():
        from app.database.integrity import read_package_metadata

        metadata = read_package_metadata(path)
        rows.append(("Database version", metadata.get(DatabaseMetadata.VERSION, "unknown")))
        rows.append(("Database size", format_bytes(path.stat().st_size)))
    else:
        rows.append(("Database version", "not installed"))
    print(APP_NAME)
    _print_table(rows)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _global_options() -> argparse.ArgumentParser:
    """Options accepted both before and after the subcommand."""
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--database", help="path to a specific database file")
    shared.add_argument("--data-dir", help="use a specific application-data directory")
    shared.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    return shared


def build_parser() -> argparse.ArgumentParser:
    shared = _global_options()
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=f"{APP_NAME} database maintenance tools.",
        parents=[shared],
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")

    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, parents=[shared])

    init = add("init-db", "create an empty database with the full schema")
    init.add_argument("--force", action="store_true", help="recreate an existing database")
    init.add_argument(
        "--db-version",
        dest="version",
        default="0.0.0",
        help="database version to stamp into the new file",
    )
    init.add_argument("--publisher", default="Bin-Tel Project")
    init.set_defaults(func=cmd_init_db)

    imp = add("import-data", "import records from a file or folder")
    imp.add_argument("--source", required=True, help="file or directory to import")
    imp.add_argument("--format", choices=["csv", "json", "jsonl", "sqlite"], help="force an importer")
    imp.add_argument("--dry-run", action="store_true", help="parse and report without writing")
    imp.add_argument("--update", action="store_true", default=True, help="update existing records")
    imp.add_argument("--no-update", dest="update", action="store_false")
    imp.add_argument("--dedupe", action="store_true", default=True, help="deduplicate afterwards")
    imp.add_argument("--no-dedupe", dest="dedupe", action="store_false")
    imp.add_argument("--batch-size", type=int, default=1000)
    imp.add_argument("--limit", type=int, help="stop after this many rows")
    imp.add_argument("--encoding", default="utf-8")
    imp.add_argument("--delimiter", help="override CSV delimiter detection")
    imp.add_argument("--source-code", default="import", help="provenance code recorded internally")
    imp.add_argument("--source-name", default="File import")
    imp.add_argument("--create", action="store_true", help="create the database if missing")
    imp.add_argument(
        "--stage",
        action="store_true",
        default=True,
        help="import through the staging layer (the default, and the safe path)",
    )
    imp.add_argument(
        "--no-stage",
        dest="stage",
        action="store_false",
        help="write straight to production, skipping validation and conflict checks",
    )
    imp.set_defaults(func=cmd_import_data)

    reb = add("rebuild", "rebuild the whole database from the BIN list")
    reb.add_argument("--list", help=f"path to the list (default: data/{BIN_LIST_FILENAME})")
    reb.add_argument(
        "--db-version", dest="db_version", help="version to stamp (default: today's date)"
    )
    reb.add_argument(
        "--allow-shrink",
        action="store_true",
        help="build even when the list holds far fewer BINs than the current database",
    )
    reb.set_defaults(func=cmd_rebuild)

    rbk = add("rollback", "restore the database the last rebuild replaced")
    rbk.set_defaults(func=cmd_rollback)

    chk = add("check-list", "read the BIN list and report on it, changing nothing")
    chk.add_argument("--list", help=f"path to the list (default: data/{BIN_LIST_FILENAME})")
    chk.add_argument(
        "--strict", action="store_true", help="exit non-zero if any row was skipped"
    )
    chk.set_defaults(func=cmd_check_list)

    ded = add("dedupe", "detect and resolve duplicate records")
    ded.add_argument("--dry-run", action="store_true")
    ded.add_argument(
        "--detect-only", action="store_true", help="report candidates without merging"
    )
    ded.set_defaults(func=cmd_dedupe)

    ver = add("verify-db", "verify database integrity and schema")
    ver.add_argument("--quick", action="store_true", help="run the fast check")
    ver.set_defaults(func=cmd_verify_db)

    st = add("stats", "print database statistics")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_stats)

    exp = add("export", "export a BIN record or an institution's BINs")
    exp.add_argument("--bin", help="BIN or IIN to export")
    exp.add_argument("--bank", help="institution name to export")
    exp.add_argument("--format", default="json", choices=["json", "csv", "txt"])
    exp.add_argument("--output", help="write to a file instead of stdout")
    exp.set_defaults(func=cmd_export)

    look = add("lookup", "resolve a BIN and print the record")
    look.add_argument("bin")
    look.set_defaults(func=cmd_lookup)

    re_ = add("reindex", "rebuild indexes and refresh statistics")
    re_.set_defaults(func=cmd_reindex)

    bak = add("backup", "create a database backup")
    bak.add_argument("--output", help="destination directory")
    bak.add_argument("--keep", type=int, help="prune to this many backups")
    bak.set_defaults(func=cmd_backup)

    res = add("restore", "restore a verified backup")
    res.add_argument("backup", help="backup file to restore")
    res.set_defaults(func=cmd_restore)

    qual = add("quality", "measure database quality metrics")
    qual.add_argument("--json", action="store_true")
    qual.add_argument(
        "--store", action="store_true", help="write the metrics into the database"
    )
    qual.set_defaults(func=cmd_quality)

    stg = add("staging", "inspect the staging layer")
    stg.add_argument("--batch", help="restrict to one batch id")
    stg.add_argument("--clear", action="store_true", help="discard staged records")
    stg.set_defaults(func=cmd_staging)

    vs = add("version", "print version information")
    vs.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.log_level, get_paths(), console=True)
    try:
        return int(args.func(args))
    except BinTelError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        if exc.detail:
            print(f"       {exc.detail}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - a CLI should not print a traceback
        logger.exception("CLI command failed")
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
