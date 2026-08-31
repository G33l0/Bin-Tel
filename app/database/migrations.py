"""Schema migrations for the intelligence database.

A downloaded package declares the schema it was built against. When that is
older than this build expects, the package is migrated *in staging* — before
it is ever activated — so a migration failure leaves the working database
untouched.

Migrations are ordinary callables registered against the version they produce.
Alembic drives the same steps for development and for the release pipeline
(see ``alembic/``); this runner exists because the desktop client has to be
able to migrate a downloaded artefact without a development environment.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import Engine

from app.core.constants import MAX_SCHEMA_VERSION, MIN_SCHEMA_VERSION
from app.core.errors import SchemaVersionError
from app.core.logging_config import get_logger, log_event
from app.database.schema import (
    read_schema_version,
    rebuild_indexes,
    stamp_schema_version,
)

logger = get_logger(__name__)

#: A migration receives the engine of the *staged* database.
MigrationStep = Callable[[Engine], None]


@dataclass(slots=True)
class Migration:
    """One ordered schema step."""

    #: Schema version this step produces.
    version: int
    description: str
    apply: MigrationStep
    #: Whether the step changes indexes and needs a rebuild afterwards.
    reindex: bool = False


#: Registered migrations, ordered by target version.
MIGRATIONS: list[Migration] = []


def register(version: int, description: str, *, reindex: bool = False):
    """Decorator registering a migration step."""

    def decorator(function: MigrationStep) -> MigrationStep:
        MIGRATIONS.append(
            Migration(version=version, description=description, apply=function, reindex=reindex)
        )
        MIGRATIONS.sort(key=lambda item: item.version)
        return function

    return decorator


@dataclass(slots=True)
class MigrationResult:
    """What a migration run did."""

    from_version: int
    to_version: int
    applied: list[str] = field(default_factory=list)
    migrated: bool = False

    @property
    def summary(self) -> str:
        if not self.migrated:
            return f"No migration needed (schema {self.from_version})."
        return (
            f"Migrated schema {self.from_version} → {self.to_version}: "
            + "; ".join(self.applied)
        )


def pending(from_version: int, to_version: int = MAX_SCHEMA_VERSION) -> list[Migration]:
    """Steps needed to take *from_version* up to *to_version*."""
    return [item for item in MIGRATIONS if from_version < item.version <= to_version]


def can_migrate(from_version: int | None, to_version: int = MAX_SCHEMA_VERSION) -> bool:
    """Whether this build knows how to reach *to_version* from *from_version*."""
    if from_version is None:
        return False
    if from_version > to_version:
        return False
    if from_version == to_version:
        return True
    steps = pending(from_version, to_version)
    if not steps:
        return False
    # Every intermediate version must be covered, with no gaps.
    covered = {item.version for item in steps}
    return covered == set(range(from_version + 1, to_version + 1))


def migrate(
    engine: Engine,
    *,
    target: int = MAX_SCHEMA_VERSION,
    minimum: int = MIN_SCHEMA_VERSION,
) -> MigrationResult:
    """Bring the database behind *engine* up to *target*.

    Raises :class:`~app.core.errors.SchemaVersionError` rather than guessing
    when the gap cannot be bridged — the caller then rejects the package.
    """
    current = read_schema_version(engine)
    if current is None:
        raise SchemaVersionError(
            "The database does not declare a schema version, so Bin-Tel cannot tell "
            "whether it is safe to open.",
            detail="database_metadata has no schema_version row",
        )

    result = MigrationResult(from_version=current, to_version=current)

    if current == target:
        return result
    if current > target:
        raise SchemaVersionError(
            f"This database needs a newer version of Bin-Tel (schema {current}; this "
            f"build supports up to {target}).",
            detail=f"schema {current} > supported {target}",
        )
    if current < minimum and not can_migrate(current, target):
        raise SchemaVersionError(
            f"This database uses schema {current}, which this version of Bin-Tel can no "
            "longer read. Download the current database instead.",
            detail=f"schema {current} < minimum {minimum}",
        )
    if not can_migrate(current, target):
        raise SchemaVersionError(
            f"Bin-Tel does not know how to migrate schema {current} to {target}.",
            detail=f"missing migration steps between {current} and {target}",
        )

    steps = pending(current, target)
    needs_reindex = False
    for step in steps:
        logger.info(
            "Applying schema migration",
            extra={"context": {"version": step.version, "description": step.description}},
        )
        step.apply(engine)
        stamp_schema_version(engine, step.version)
        result.applied.append(f"v{step.version} {step.description}")
        result.to_version = step.version
        needs_reindex = needs_reindex or step.reindex

    if needs_reindex:
        rebuild_indexes(engine)

    result.migrated = True
    log_event(
        logger,
        "Database migration completed",
        from_version=result.from_version,
        to_version=result.to_version,
        steps=len(result.applied),
    )
    return result


# ---------------------------------------------------------------------------
# Registered migrations
# ---------------------------------------------------------------------------
#
# Schema 1 is the initial published schema, so there is nothing to migrate to
# it. The runner applies steps in order against the *staged* download, so a
# failure never touches the database currently in use.


def _columns(engine: Engine, table: str) -> set[str]:
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _add_column(engine: Engine, table: str, column: str, definition: str) -> bool:
    """Add a column if the staged package does not already carry it."""
    if column in _columns(engine, table):
        return False
    with engine.begin() as connection:
        connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return True


@register(2, "range-aware prefix identity and temporal relationships", reindex=True)
def _to_v2(engine: Engine) -> None:
    """Give BINs a real prefix identity and relationships a lifetime.

    A schema-1 package stores ``bins.bin`` without recording how long the
    assignment actually is, so a six-digit root and an eight-digit assignment
    beneath it are indistinguishable once loaded. This step adds the identity
    columns and backfills them from the stored digits, which is lossless: the
    length of the prefix as published *is* its assigned length.

    Relationship rows gain a lifetime and are backfilled as current, because a
    schema-1 package only ever described the present.
    """
    from app.models.entities import (
        DataQualityMetric,
        InstitutionRelationship,
        PrefixType,
        RangeType,
        RecordStatus,
        StagingRecord,
    )

    # -- new tables --------------------------------------------------------
    for model in (InstitutionRelationship, StagingRecord, DataQualityMetric):
        model.__table__.create(engine, checkfirst=True)

    # -- bins: prefix identity --------------------------------------------
    _add_column(engine, "bins", "prefix", "VARCHAR(11) NOT NULL DEFAULT ''")
    _add_column(engine, "bins", "prefix_length", "INTEGER NOT NULL DEFAULT 6")
    _add_column(
        engine, "bins", "prefix_type", f"VARCHAR(16) DEFAULT '{PrefixType.ROOT.value}'"
    )
    _add_column(engine, "bins", "span_low", "INTEGER NOT NULL DEFAULT 0")
    _add_column(engine, "bins", "span_high", "INTEGER NOT NULL DEFAULT 0")

    with engine.begin() as connection:
        # The published digits are the assignment, so length and type follow
        # from them directly rather than being guessed at.
        connection.exec_driver_sql(
            "UPDATE bins SET prefix = bin, prefix_length = length(bin) "
            "WHERE prefix IS NULL OR prefix = ''"
        )
        connection.exec_driver_sql(
            "UPDATE bins SET prefix_type = CASE "
            f"WHEN length(bin) >= 8 THEN '{PrefixType.EXTENDED.value}' "
            f"ELSE '{PrefixType.ROOT.value}' END"
        )
        # The span a prefix covers, at the eight-digit padding width.
        connection.exec_driver_sql(
            "UPDATE bins SET "
            "span_low  = CAST(substr(bin || '00000000', 1, 8) AS INTEGER), "
            "span_high = CAST(substr(bin || '99999999', 1, 8) AS INTEGER)"
        )

    # -- bin_institutions: temporal + evidence -----------------------------
    _add_column(
        engine,
        "bin_institutions",
        "status",
        f"VARCHAR(16) DEFAULT '{RecordStatus.ACTIVE.value}'",
    )
    _add_column(engine, "bin_institutions", "effective_from", "DATETIME")
    _add_column(engine, "bin_institutions", "effective_to", "DATETIME")
    _add_column(engine, "bin_institutions", "is_current", "BOOLEAN DEFAULT 1")
    _add_column(engine, "bin_institutions", "confidence_level", "VARCHAR(16)")
    _add_column(engine, "bin_institutions", "confidence_reasons", "TEXT")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE bin_institutions SET is_current = 1 WHERE is_current IS NULL"
        )
        connection.exec_driver_sql(
            "UPDATE bin_institutions SET effective_from = first_seen "
            "WHERE effective_from IS NULL"
        )

    # -- bin_ranges: type, span and lifetime -------------------------------
    _add_column(
        engine,
        "bin_ranges",
        "range_type",
        f"VARCHAR(24) DEFAULT '{RangeType.ISSUER_RANGE.value}'",
    )
    _add_column(engine, "bin_ranges", "span", "INTEGER NOT NULL DEFAULT 0")
    _add_column(engine, "bin_ranges", "effective_from", "DATETIME")
    _add_column(engine, "bin_ranges", "effective_to", "DATETIME")
    _add_column(engine, "bin_ranges", "is_current", "BOOLEAN DEFAULT 1")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE bin_ranges SET span = range_high_int - range_low_int WHERE span = 0"
        )
        connection.exec_driver_sql(
            "UPDATE bin_ranges SET is_current = 1 WHERE is_current IS NULL"
        )
        connection.exec_driver_sql(
            "UPDATE bin_ranges SET effective_from = first_seen WHERE effective_from IS NULL"
        )


def ensure_optional_tables(engine: Engine) -> list[str]:
    """Create tables this build expects but an older package may not carry.

    A package built before history and statistics existed is still perfectly
    usable for lookups; creating the missing tables empty lets the newer
    features degrade to "no data yet" instead of failing.
    """
    from sqlalchemy import inspect

    from app.models.entities import (
        BinHistory,
        DatabaseStatistic,
        DatabaseVersion,
        InstitutionHistory,
        NormalizationEvent,
        UpdateHistory,
    )

    optional = (
        BinHistory,
        InstitutionHistory,
        DatabaseVersion,
        DatabaseStatistic,
        UpdateHistory,
        NormalizationEvent,
    )
    present = set(inspect(engine).get_table_names())
    created: list[str] = []
    for entity in optional:
        name = entity.__tablename__
        if name not in present:
            entity.__table__.create(engine, checkfirst=True)
            created.append(name)
    if created:
        logger.info(
            "Created optional tables missing from the package",
            extra={"context": {"tables": created}},
        )
    return created
