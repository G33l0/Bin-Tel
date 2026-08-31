#!/usr/bin/env python3
"""Build a publishable Bin-Tel database release from source data.

This is the distribution-side counterpart to the desktop application: it takes
raw issuer data, runs it through the same normalization, ingestion and
deduplication the application uses, and produces the artefacts a distribution
server needs — a verified package, its checksum, and the manifest that points
at it.

The pipeline, in order:

    normalize → dedupe → validate → index → integrity → compress → checksum
    → manifest → release directory

Every step either succeeds or stops the build. A release that fails
verification is never written to the output directory, because the desktop
application would refuse it anyway and the failure belongs here, not on a
user's machine.

    python scripts/build_release.py \\
        --source data/issuers/ \\
        --output dist/release \\
        --version 2026.02.1 \\
        --compression xz

Source data must contain issuer metadata only. Never feed this pipeline full
card numbers, cardholder names or any payment authentication data — see
docs/PRIVACY.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.constants import (  # noqa: E402
    APP_VERSION,
    MIN_SCHEMA_VERSION,
    SCHEMA_VERSION,
)
from app.database.engine import DatabaseManager  # noqa: E402
from app.database.integrity import verify_database  # noqa: E402
from app.database.schema import (  # noqa: E402
    analyze,
    create_schema,
    list_indexes,
    rebuild_indexes,
    vacuum,
    write_metadata,
)
from app.importers.base import ImportOptions  # noqa: E402
from app.importers.batch import BatchImporter  # noqa: E402
from app.importers.registry import importer_for  # noqa: E402
from app.models.entities import DatabaseMetadata, DatabaseVersion  # noqa: E402
from app.providers.compression import compress, normalise, suffix_for  # noqa: E402
from app.services.dedupe_service import DedupeService  # noqa: E402
from app.utils.hashing import file_checksum  # noqa: E402

STEP_WIDTH = 34


class BuildFailed(RuntimeError):
    """A pipeline step failed; the release is not published."""


def step(number: int, total: int, title: str) -> None:
    print(f"[{number}/{total}] {title.ljust(STEP_WIDTH)}", end="", flush=True)


def done(detail: str = "ok") -> None:
    print(detail)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def build_release(
    source: Path,
    output_dir: Path,
    version: str,
    *,
    edition: str = "community",
    publisher: str = "Bin-Tel Project",
    compression: str = "xz",
    notes: str = "",
    minimum_app_version: str = APP_VERSION,
    source_code: str = "release",
    source_name: str = "Release pipeline",
    keep_staging: bool = False,
) -> tuple[Path, Path]:
    """Run the whole pipeline. Returns ``(artefact, manifest)``."""
    started = time.monotonic()
    total = 9

    staging_dir = output_dir.parent / f".{output_dir.name}-staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    staged = staging_dir / f"bintel-{version}.sqlite"

    try:
        # -- 1. schema ------------------------------------------------------
        step(1, total, "Creating the schema")
        manager = DatabaseManager(staged)
        manager.open(create_if_missing=True)
        create_schema(manager.engine)
        done()

        # -- 2. normalize and ingest ---------------------------------------
        step(2, total, "Normalizing and ingesting")
        options = ImportOptions(
            source=source,
            dry_run=False,
            update=True,
            dedupe=False,  # a single pass at the end is cheaper and safer
            source_code=source_code,
            source_name=source_name,
            record_normalization=True,
        )
        if source.is_dir():
            # A folder is imported file by file, so one unreadable file cannot
            # abort a release build that is otherwise sound.
            result = BatchImporter(options).run_all(manager).totals
        else:
            result = importer_for(options).run(manager).result
        if result.processed == 0:
            raise BuildFailed(f"No records were read from {source}")
        done(
            f"{result.created:,} created, "
            f"{result.updated:,} updated, "
            f"{result.skipped:,} skipped"
        )

        # -- 3. dedupe ------------------------------------------------------
        step(3, total, "Deduplicating")
        with manager.transaction() as session:
            report = DedupeService(session).run(merge=True)
        done(
            f"{report.merged_institutions:,} merged, "
            f"{len(report.review_candidates):,} for review, "
            f"{report.conflicts_recorded:,} conflicts recorded"
        )

        # -- 4. counts and metadata ----------------------------------------
        step(4, total, "Stamping metadata")
        counts = _counts(manager)
        release_date = datetime.now(UTC)
        with manager.transaction() as session:
            session.add(
                DatabaseVersion(
                    version=version,
                    schema_version=SCHEMA_VERSION,
                    edition=edition,
                    release_date=release_date,
                    record_count=counts["bins"],
                    institution_count=counts["institutions"],
                    notes=notes or None,
                )
            )
            write_metadata(
                session,
                {
                    DatabaseMetadata.VERSION: version,
                    DatabaseMetadata.SCHEMA_VERSION: SCHEMA_VERSION,
                    DatabaseMetadata.RELEASE_DATE: release_date.isoformat(),
                    DatabaseMetadata.RECORD_COUNT: counts["bins"],
                    DatabaseMetadata.PUBLISHER: publisher,
                    DatabaseMetadata.BUILD_ID: f"release-{version}",
                    DatabaseMetadata.NOTES: notes,
                },
            )
        done(f"{counts['bins']:,} BINs, {counts['institutions']:,} institutions")

        # -- 5. index and compact ------------------------------------------
        step(5, total, "Indexing and compacting")
        rebuild_indexes(manager.engine)
        analyze(manager.engine)
        index_count = len(list_indexes(manager.engine))
        manager.close()
        # VACUUM needs its own connection with nothing else in flight.
        compacting = DatabaseManager(staged)
        compacting.open()
        vacuum(compacting.engine)
        compacting.close()
        for suffix in ("-wal", "-shm"):
            staged.with_name(staged.name + suffix).unlink(missing_ok=True)
        done(f"{index_count} indexes")

        # -- 6. verify ------------------------------------------------------
        step(6, total, "Verifying integrity")
        verification = verify_database(staged)
        if not verification.ok:
            raise BuildFailed(
                "The built package did not pass verification: "
                + "; ".join(verification.errors)
            )
        done(f"{verification.integrity_result}, schema {verification.schema_version}")

        # -- 7. compress ----------------------------------------------------
        database_size = staged.stat().st_size
        compression = normalise(compression)
        step(7, total, "Compressing")
        if compression == "none":
            artefact_staged = staged
            done("skipped (uncompressed release)")
        else:
            artefact_staged = staged.with_name(staged.name + suffix_for(compression))
            compress(staged, artefact_staged, compression)
            saved = 1 - (artefact_staged.stat().st_size / database_size)
            done(f"{compression}, {_size(artefact_staged.stat().st_size)} ({saved:.0%} saved)")

        # -- 8. checksum ----------------------------------------------------
        step(8, total, "Checksumming")
        digest = file_checksum(artefact_staged, "sha256")
        done(f"sha256:{digest[:16]}…")

        # -- 9. publish -----------------------------------------------------
        step(9, total, "Writing the release")
        output_dir.mkdir(parents=True, exist_ok=True)
        artefact = output_dir / artefact_staged.name
        shutil.copy2(artefact_staged, artefact)
        if compression != "none" and keep_staging:
            shutil.copy2(staged, output_dir / staged.name)

        manifest: dict[str, Any] = {
            "version": version,
            "schema_version": SCHEMA_VERSION,
            "min_schema_version": MIN_SCHEMA_VERSION,
            "release_date": release_date.isoformat(),
            "database_size": database_size,
            "compressed_size": (
                artefact.stat().st_size if compression != "none" else 0
            ),
            "record_count": counts["bins"],
            "institution_count": counts["institutions"],
            "sha256": digest,
            "download_url": artefact.name,
            "compression": compression,
            "edition": edition,
            "publisher": publisher,
            "notes": notes,
            "minimum_app_version": minimum_app_version,
            "deltas": [],
        }
        manifest_path = output_dir / "database-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        _write_release_notes(output_dir, version, counts, report, verification, notes)
        done(str(output_dir))
    finally:
        if not keep_staging and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)

    elapsed = time.monotonic() - started
    print(f"\nBuilt {version} in {elapsed:,.1f}s")
    return artefact, manifest_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _counts(manager: DatabaseManager) -> dict[str, int]:
    from app.repositories.stats_repository import StatsRepository

    stats = StatsRepository(manager).stats()
    return {
        "bins": stats.bins,
        "institutions": stats.institutions,
        "countries": stats.countries,
        "networks": stats.networks,
        "ranges": stats.bin_ranges,
        "aliases": stats.aliases,
        "conflicts": stats.conflicts_open,
    }


def _size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} GB"


def _write_release_notes(
    output_dir: Path,
    version: str,
    counts: dict[str, int],
    dedupe_report: Any,
    verification: Any,
    notes: str,
) -> None:
    """A human-readable summary beside the artefacts, for the release record."""
    lines = [
        f"# Bin-Tel database {version}",
        "",
        f"Released {datetime.now(UTC):%Y-%m-%d %H:%M UTC}",
        "",
        "## Contents",
        "",
        f"- {counts['bins']:,} BIN records",
        f"- {counts['institutions']:,} institutions",
        f"- {counts['ranges']:,} allocated ranges",
        f"- {counts['aliases']:,} institution aliases",
        f"- {counts['countries']:,} countries covered",
        f"- {counts['networks']:,} networks covered",
        "",
        "## Build",
        "",
        f"- Schema version {verification.schema_version}",
        f"- Integrity check: {verification.integrity_result}",
        f"- Deduplication: {dedupe_report.summary}",
        f"- Open conflicts carried: {counts['conflicts']:,}",
    ]
    if notes:
        lines += ["", "## Notes", "", notes]
    (output_dir / "RELEASE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a publishable Bin-Tel database release.",
        epilog="Source data must contain issuer metadata only.",
    )
    parser.add_argument("--source", type=Path, required=True, help="file or directory of source data")
    parser.add_argument("--output", type=Path, default=Path("dist/release"))
    parser.add_argument(
        "--version",
        default=datetime.now(UTC).strftime("%Y.%m.%d"),
        help="release version (default: today's date)",
    )
    parser.add_argument(
        "--edition",
        default="community",
        choices=["community", "professional", "business", "enterprise"],
    )
    parser.add_argument("--publisher", default="Bin-Tel Project")
    parser.add_argument(
        "--compression", default="xz", choices=["none", "gzip", "xz", "bz2"]
    )
    parser.add_argument("--notes", default="", help="release notes for the manifest")
    parser.add_argument(
        "--minimum-app-version",
        default=APP_VERSION,
        help="oldest application version that may install this release",
    )
    parser.add_argument(
        "--keep-staging",
        action="store_true",
        help="keep the uncompressed database and the staging directory",
    )
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"error: {args.source} does not exist.", file=sys.stderr)
        return 2

    print(f"Building Bin-Tel database {args.version} ({args.edition})\n")
    try:
        artefact, manifest = build_release(
            args.source,
            args.output,
            args.version,
            edition=args.edition,
            publisher=args.publisher,
            compression=args.compression,
            notes=args.notes,
            minimum_app_version=args.minimum_app_version,
            keep_staging=args.keep_staging,
        )
    except BuildFailed as exc:
        print(f"\nBuild failed: {exc}", file=sys.stderr)
        return 1

    print(f"Package:  {artefact}  ({_size(artefact.stat().st_size)})")
    print(f"Manifest: {manifest}")
    print(f"\nServe it with:\n  python scripts/serve_database.py --directory {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
