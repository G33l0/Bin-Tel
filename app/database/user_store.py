"""The user-data database.

Kept deliberately separate from the intelligence database. The intelligence
database is a *replaceable artefact* — every update swaps the whole file —
whereas everything the user creates must survive that swap untouched. Two
files, two lifecycles, no possibility of an update destroying a watchlist.

The store owns its own schema version and migrates itself forward in place.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import DatabaseError
from app.core.logging_config import get_logger
from app.database.engine import create_database_engine
from app.models.user_entities import UserBase, UserMetadata

logger = get_logger(__name__)

#: Schema version of the user-data database. Bump when adding a migration.
USER_SCHEMA_VERSION = 1

USER_DATABASE_FILENAME = "bintel-user.sqlite"


class UserDataStore:
    """Owns the user-data engine and its sessions."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    # -- lifecycle --------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._engine is not None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self.open()
        assert self._engine is not None  # noqa: S101 - open() guarantees this
        return self._engine

    def open(self) -> Engine:
        """Open (creating and migrating if necessary) the user database."""
        if self._engine is not None:
            return self._engine
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_database_engine(self._path, create_if_missing=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False, future=True
        )
        UserBase.metadata.create_all(self._engine)
        self._migrate()
        logger.info("User data store opened", extra={"context": {"path": str(self._path)}})
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            logger.info("User data store closed")
        self._engine = None
        self._session_factory = None

    # -- sessions ---------------------------------------------------------
    def new_session(self) -> Session:
        if self._session_factory is None:
            self.open()
        if self._session_factory is None:  # pragma: no cover - defensive
            raise DatabaseError("The user data store could not be opened.")
        return self._session_factory()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.new_session()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        session = self.new_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # -- metadata and migration -------------------------------------------
    def get_metadata(self, key: str, default: str | None = None) -> str | None:
        with self.session() as session:
            row = session.get(UserMetadata, key)
            return row.value if row is not None else default

    def set_metadata(self, key: str, value: str | None) -> None:
        with self.transaction() as session:
            row = session.get(UserMetadata, key)
            if row is None:
                session.add(UserMetadata(key=key, value=value))
            else:
                row.value = value
                row.updated_at = datetime.now(UTC)

    @property
    def schema_version(self) -> int:
        raw = self.get_metadata(UserMetadata.SCHEMA_VERSION)
        try:
            return int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return 0

    @property
    def install_id(self) -> str:
        """A random per-installation identifier.

        Generated locally, it contains nothing about the machine or the person
        using it, and can be reset by deleting the user database.
        """
        existing = self.get_metadata(UserMetadata.INSTALL_ID)
        if existing:
            return existing
        generated = uuid.uuid4().hex
        self.set_metadata(UserMetadata.INSTALL_ID, generated)
        return generated

    def _migrate(self) -> None:
        """Bring an existing user database up to :data:`USER_SCHEMA_VERSION`."""
        current = self.schema_version
        if current == USER_SCHEMA_VERSION:
            return
        if current == 0:
            # Fresh database, or one created before versions were stamped.
            self.set_metadata(UserMetadata.SCHEMA_VERSION, str(USER_SCHEMA_VERSION))
            self.set_metadata(UserMetadata.CREATED_AT, datetime.now(UTC).isoformat())
            logger.info(
                "User data store initialised",
                extra={"context": {"schema_version": USER_SCHEMA_VERSION}},
            )
            return
        if current > USER_SCHEMA_VERSION:
            logger.warning(
                "The user data store was written by a newer version of Bin-Tel",
                extra={"context": {"found": current, "supported": USER_SCHEMA_VERSION}},
            )
            return

        # Future migrations run here, one step at a time.
        for step in range(current + 1, USER_SCHEMA_VERSION + 1):
            migration = _MIGRATIONS.get(step)
            if migration is not None:
                logger.info("Migrating the user data store to version %s", step)
                migration(self)
            self.set_metadata(UserMetadata.SCHEMA_VERSION, str(step))

    # -- maintenance ------------------------------------------------------
    def tables(self) -> list[str]:
        return sorted(inspect(self.engine).get_table_names())

    def counts(self) -> dict[str, int]:
        """Row counts per table, for the Database Administration page."""
        from sqlalchemy import func

        results: dict[str, int] = {}
        with self.session() as session:
            for name, table in UserBase.metadata.tables.items():
                results[name] = int(
                    session.execute(select(func.count()).select_from(table)).scalar() or 0
                )
        return results

    def size_bytes(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def vacuum(self) -> None:
        with self.engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.exec_driver_sql("VACUUM")


#: Migration callables keyed by the schema version they produce.
_MIGRATIONS: dict[int, object] = {}
