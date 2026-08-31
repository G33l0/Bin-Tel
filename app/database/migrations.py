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
# it. Future steps register here, for example::
#
#     @register(2, "add bin_history and institution_history", reindex=True)
#     def _to_v2(engine: Engine) -> None:
#         BinHistory.__table__.create(engine, checkfirst=True)
#         InstitutionHistory.__table__.create(engine, checkfirst=True)
#
# The runner applies them in order against the *staged* download, so a failure
# never touches the database currently in use.


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
