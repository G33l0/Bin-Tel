"""Schema creation, index management and the metadata key/value store."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from app.core.constants import SCHEMA_VERSION
from app.core.logging_config import get_logger
from app.models.entities import Base, DatabaseMetadata

logger = get_logger(__name__)

#: Tables a database package must contain to be considered usable.
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

#: Present in a full package, but their absence only degrades maintenance
#: tooling — lookups still work, so a package missing them is not rejected.
OPTIONAL_TABLES: tuple[str, ...] = (
    "sources",
    "bin_claims",
    "update_history",
    "conflicts",
    "normalization_events",
    "bin_history",
    "institution_history",
    "database_versions",
    "database_statistics",
)

#: Extra covering indexes that are not expressible as simple column indexes on
#: the ORM models. Created idempotently after schema creation and after an
#: update, so an older package still gets today's index set.
EXTRA_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "ix_bins_lookup_cover",
        "CREATE INDEX IF NOT EXISTS ix_bins_lookup_cover "
        "ON bins (prefix6, bin_int, network_id, country_id)",
    ),
    (
        "ix_institutions_search",
        "CREATE INDEX IF NOT EXISTS ix_institutions_search "
        "ON institutions (normalized_name, display_name, country_id)",
    ),
    (
        "ix_alias_search",
        "CREATE INDEX IF NOT EXISTS ix_alias_search "
        "ON institution_aliases (normalized_alias, institution_id)",
    ),
    (
        "ix_bin_institutions_join",
        "CREATE INDEX IF NOT EXISTS ix_bin_institutions_join "
        "ON bin_institutions (institution_id, bin_id, is_primary)",
    ),
    (
        "ix_addresses_geo",
        "CREATE INDEX IF NOT EXISTS ix_addresses_geo "
        "ON addresses (country_id, region_code, normalized_city)",
    ),
    (
        "ix_bins_analytics",
        "CREATE INDEX IF NOT EXISTS ix_bins_analytics "
        "ON bins (card_type, funding_type, country_id, network_id)",
    ),
    (
        "ix_bins_recent",
        "CREATE INDEX IF NOT EXISTS ix_bins_recent ON bins (last_updated, first_seen)",
    ),
    (
        "ix_bin_history_lookup",
        "CREATE INDEX IF NOT EXISTS ix_bin_history_lookup "
        "ON bin_history (bin, changed_at, action)",
    ),
    (
        "ix_institution_history_lookup",
        "CREATE INDEX IF NOT EXISTS ix_institution_history_lookup "
        "ON institution_history (institution_uid, changed_at, action)",
    ),
)


def create_schema(engine: Engine, *, with_indexes: bool = True) -> None:
    """Create every table and index. Safe to run against a populated file."""
    Base.metadata.create_all(engine)
    if with_indexes:
        rebuild_indexes(engine)
    stamp_schema_version(engine)


def rebuild_indexes(engine: Engine) -> list[str]:
    """Create any missing index, including the extra covering indexes."""
    created: list[str] = []
    Base.metadata.create_all(engine, checkfirst=True)
    existing_tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        for name, statement in EXTRA_INDEXES:
            table = statement.split(" ON ", 1)[1].split(" ", 1)[0]
            if table not in existing_tables:
                continue
            connection.exec_driver_sql(statement)
            created.append(name)
    logger.info("Indexes verified", extra={"context": {"indexes": len(created)}})
    return created


def analyze(engine: Engine) -> None:
    """Refresh SQLite's query planner statistics after a bulk change."""
    with engine.begin() as connection:
        connection.exec_driver_sql("ANALYZE")


def vacuum(engine: Engine) -> None:
    """Compact the database file. Must run outside a transaction."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("VACUUM")


def optimize(engine: Engine) -> None:
    """Cheap maintenance SQLite recommends before closing a connection."""
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA optimize")


def list_indexes(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return sorted(row[0] for row in rows)


def missing_tables(engine: Engine) -> list[str]:
    present = set(inspect(engine).get_table_names())
    return [name for name in REQUIRED_TABLES if name not in present]


# ---------------------------------------------------------------------------
# database_metadata key/value helpers
# ---------------------------------------------------------------------------


def read_metadata(session: Session) -> dict[str, str]:
    """Whole metadata table as a plain mapping."""
    rows = session.query(DatabaseMetadata).all()
    return {row.key: (row.value or "") for row in rows}


def get_metadata(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(DatabaseMetadata, key)
    return row.value if row is not None else default


def write_metadata(session: Session, values: dict[str, Any]) -> None:
    """Upsert metadata keys. The caller commits."""
    for key, value in values.items():
        text_value = None if value is None else str(value)
        row = session.get(DatabaseMetadata, key)
        if row is None:
            session.add(DatabaseMetadata(key=key, value=text_value))
        else:
            row.value = text_value
            row.updated_at = datetime.now(UTC)


def stamp_schema_version(engine: Engine, version: int = SCHEMA_VERSION) -> None:
    """Record the schema version this build wrote, if not already present."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO database_metadata (key, value, updated_at) "
                "VALUES (:key, :value, :now) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"
            ),
            {
                "key": DatabaseMetadata.SCHEMA_VERSION,
                "value": str(version),
                "now": datetime.now(UTC),
            },
        )


def read_schema_version(engine: Engine) -> int | None:
    with engine.connect() as connection:
        try:
            value = connection.execute(
                text("SELECT value FROM database_metadata WHERE key = :key"),
                {"key": DatabaseMetadata.SCHEMA_VERSION},
            ).scalar()
        except Exception:  # noqa: BLE001 - table may not exist yet
            return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
