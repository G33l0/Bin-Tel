"""Verification of a SQLite database file.

Nothing is ever activated on the strength of a checksum alone: a downloaded
package must also *open* as SQLite, pass an integrity check, declare a schema
version this build understands, contain the required tables and hold at least
one BIN. Only then is it allowed to replace the working database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.core.constants import SCHEMA_VERSION
from app.core.logging_config import get_logger
from app.models.entities import DatabaseMetadata

logger = get_logger(__name__)

#: SQLite files begin with this 16-byte header.
_SQLITE_MAGIC = b"SQLite format 3\x00"

REQUIRED_TABLES: tuple[str, ...] = (
    "countries",
    "networks",
    "institutions",
    "institution_aliases",
    "addresses",
    "bins",
    "bin_ranges",
    "bin_institutions",
    "database_metadata",
)


@dataclass(slots=True)
class VerificationReport:
    """Structured outcome of :func:`verify_database`."""

    path: Path
    ok: bool = False
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: int | None = None
    database_version: str | None = None
    bin_count: int = 0
    institution_count: int = 0
    size_bytes: int = 0
    integrity_result: str = ""

    def fail(self, message: str) -> VerificationReport:
        self.errors.append(message)
        self.ok = False
        return self

    def warn(self, message: str) -> VerificationReport:
        self.warnings.append(message)
        return self

    @property
    def summary(self) -> str:
        if self.ok and not self.warnings:
            return "Database verified successfully."
        if self.ok:
            return f"Database verified with {len(self.warnings)} warning(s)."
        return self.errors[0] if self.errors else "Database verification failed."


def looks_like_sqlite(path: Path) -> bool:
    """Cheap header check before spending time opening the file."""
    try:
        with path.open("rb") as handle:
            return handle.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def verify_database(
    path: Path,
    *,
    quick: bool = False,
    require_content: bool = True,
    max_schema_version: int = SCHEMA_VERSION,
) -> VerificationReport:
    """Run the full verification pipeline against the file at *path*.

    *quick* runs ``PRAGMA quick_check`` instead of the much slower
    ``integrity_check``; it is what the startup path uses so launching stays
    fast, while the Database page's "Verify Database" button runs the full one.
    """
    report = VerificationReport(path=path)

    if not path.exists():
        return report.fail("The database file does not exist.")
    try:
        report.size_bytes = path.stat().st_size
    except OSError as exc:  # pragma: no cover - race with deletion
        return report.fail(f"The database file could not be read ({exc.strerror}).")
    if report.size_bytes == 0:
        return report.fail("The database file is empty.")
    if not looks_like_sqlite(path):
        return report.fail("The file is not a valid SQLite database.")

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15.0)
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()

        pragma = "quick_check" if quick else "integrity_check"
        rows = cursor.execute(f"PRAGMA {pragma}").fetchall()
        result = rows[0][0] if rows else "unknown"
        report.integrity_result = str(result)
        if str(result).lower() != "ok":
            return report.fail(f"SQLite reported database corruption: {result}")

        present = {
            row[0]
            for row in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = [name for name in REQUIRED_TABLES if name not in present]
        if missing:
            return report.fail(
                "The database is missing required tables: " + ", ".join(sorted(missing))
            )

        metadata = {
            row["key"]: row["value"]
            for row in cursor.execute("SELECT key, value FROM database_metadata").fetchall()
        }
        report.database_version = metadata.get(DatabaseMetadata.VERSION)
        raw_schema = metadata.get(DatabaseMetadata.SCHEMA_VERSION)
        try:
            report.schema_version = int(raw_schema) if raw_schema is not None else None
        except (TypeError, ValueError):
            report.schema_version = None

        if report.schema_version is None:
            report.warn("The database does not declare a schema version.")
        elif report.schema_version > max_schema_version:
            return report.fail(
                f"This database needs a newer version of Bin-Tel "
                f"(database schema {report.schema_version}, this build supports "
                f"{max_schema_version})."
            )

        report.bin_count = int(cursor.execute("SELECT COUNT(*) FROM bins").fetchone()[0])
        report.institution_count = int(
            cursor.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
        )
        if require_content and report.bin_count == 0:
            return report.fail("The database contains no BIN records.")
        if report.institution_count == 0:
            report.warn("The database contains no institution records.")

        if not quick:
            violations = cursor.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                report.warn(
                    f"{len(violations)} foreign-key inconsistencies were found in the database."
                )

        report.ok = True
        return report
    except sqlite3.DatabaseError as exc:
        return report.fail(f"The database could not be read: {exc}")
    finally:
        if connection is not None:
            connection.close()


def read_package_metadata(path: Path) -> dict[str, str]:
    """Read ``database_metadata`` from a file that is not currently open."""
    if not looks_like_sqlite(path):
        return {}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0) as connection:
            rows = connection.execute("SELECT key, value FROM database_metadata").fetchall()
        return {str(key): "" if value is None else str(value) for key, value in rows}
    except sqlite3.DatabaseError:
        return {}


def count_rows(path: Path, table: str) -> int:
    """Row count for *table* without opening a full SQLAlchemy engine."""
    if not looks_like_sqlite(path):
        return 0
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0) as connection:
            # Table names cannot be parameterised; the caller passes a literal
            # from REQUIRED_TABLES/OPTIONAL_TABLES, never user input.
            if table not in (*REQUIRED_TABLES, "sources", "bin_claims", "conflicts"):
                raise ValueError(f"Refusing to count unknown table {table!r}")
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.DatabaseError:
        return 0
