"""SQLite engine construction and connection lifecycle.

SQLite is tuned for a read-mostly, index-heavy workload: WAL journalling so a
background update never blocks a lookup, ``foreign_keys=ON`` so the
relationships in :mod:`app.models.entities` are actually enforced, and a
memory-mapped read window so prefix scans over millions of BINs stay fast.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, event, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import DatabaseError, DatabaseMissingError
from app.core.logging_config import get_logger

logger = get_logger(__name__)

#: PRAGMA settings applied to every connection.
CONNECTION_PRAGMAS: dict[str, str | int] = {
    "foreign_keys": "ON",
    "journal_mode": "WAL",
    "synchronous": "NORMAL",
    "temp_store": "MEMORY",
    "cache_size": -64000,  # 64 MiB page cache (negative = KiB)
    "mmap_size": 268435456,  # 256 MiB memory-mapped reads
    "busy_timeout": 5000,
}

#: Applied to a read-only attachment (verification of a downloaded package).
READONLY_PRAGMAS: dict[str, str | int] = {
    "query_only": "ON",
    "temp_store": "MEMORY",
    "busy_timeout": 5000,
}


def apply_pragmas(connection: sqlite3.Connection, pragmas: dict[str, str | int]) -> None:
    """Apply *pragmas* to a raw DBAPI connection."""
    cursor = connection.cursor()
    try:
        for name, value in pragmas.items():
            try:
                cursor.execute(f"PRAGMA {name}={value}")
            except sqlite3.DatabaseError:  # pragma: no cover - read-only volumes
                logger.debug("PRAGMA %s=%s was refused", name, value)
    finally:
        cursor.close()


def create_database_engine(
    path: Path | str,
    *,
    read_only: bool = False,
    echo: bool = False,
    create_if_missing: bool = True,
) -> Engine:
    """Build a SQLAlchemy engine for the SQLite file at *path*."""
    if str(path) == ":memory:":
        url = "sqlite://"
    else:
        db_path = Path(path)
        if not create_if_missing and not db_path.exists():
            raise DatabaseMissingError(
                "The Bin-Tel database has not been installed yet.",
                detail=f"Expected database at {db_path}",
            )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if read_only:
            url = f"sqlite:///file:{db_path}?mode=ro&uri=true"
        else:
            url = f"sqlite:///{db_path}"

    engine = create_engine(
        url,
        echo=echo,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30.0, "uri": read_only},
        pool_pre_ping=True,
    )

    pragmas = READONLY_PRAGMAS if read_only else CONNECTION_PRAGMAS

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        apply_pragmas(dbapi_connection, pragmas)

    return engine


class DatabaseManager:
    """Owns the active engine and hands out short-lived sessions.

    The manager can be closed and re-opened at runtime, which is what makes an
    atomic database swap possible: the update service closes the manager,
    replaces the file, then re-opens it — the UI keeps running throughout.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._lock = threading.RLock()

    # -- lifecycle --------------------------------------------------------
    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._engine is not None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            raise DatabaseError(
                "The database is not open.",
                detail="DatabaseManager.engine accessed before open()",
            )
        return self._engine

    def open(self, path: Path | None = None, *, create_if_missing: bool = False) -> Engine:
        """Open (or re-open) the database, closing any previous engine."""
        with self._lock:
            if path is not None:
                self._path = Path(path)
            if self._path is None:
                raise DatabaseError("No database path has been configured.")
            self.close()
            self._engine = create_database_engine(
                self._path, create_if_missing=create_if_missing
            )
            self._session_factory = sessionmaker(
                bind=self._engine, expire_on_commit=False, future=True
            )
            logger.info("Database opened", extra={"context": {"path": str(self._path)}})
            return self._engine

    def close(self) -> None:
        """Dispose of the engine and release every file handle."""
        with self._lock:
            if self._engine is not None:
                try:
                    with self._engine.connect() as connection:
                        # Fold the WAL back into the main file so the database
                        # can be copied or replaced safely.
                        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:  # noqa: BLE001 - closing must never raise
                    logger.debug("WAL checkpoint on close was not possible", exc_info=True)
                self._engine.dispose()
                logger.info("Database closed")
            self._engine = None
            self._session_factory = None

    def reopen(self) -> Engine:
        return self.open(self._path)

    # -- sessions ---------------------------------------------------------
    def new_session(self) -> Session:
        if self._session_factory is None:
            raise DatabaseError(
                "The database is not open.",
                detail="DatabaseManager.new_session() before open()",
            )
        return self._session_factory()

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Read-oriented session; rolls back on error, always closes."""
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
        """Write session wrapped in a single transaction."""
        session = self.new_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def __enter__(self) -> DatabaseManager:  # pragma: no cover - convenience
        return self

    def __exit__(self, *exc: object) -> None:  # pragma: no cover - convenience
        self.close()


_manager: DatabaseManager | None = None
_manager_lock = threading.Lock()


def get_database_manager(path: Path | None = None) -> DatabaseManager:
    """Process-wide database manager singleton."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = DatabaseManager(path)
        elif path is not None and _manager.path != Path(path):
            _manager.close()
            _manager = DatabaseManager(path)
        return _manager


def reset_database_manager() -> None:
    """Drop the singleton (tests, and switching the database directory)."""
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.close()
        _manager = None
