"""Database access layer: engine, sessions, schema management, integrity, backups."""

from app.database.engine import (
    DatabaseManager,
    apply_pragmas,
    create_database_engine,
    get_database_manager,
)
from app.database.schema import (
    OPTIONAL_TABLES,
    REQUIRED_TABLES,
    analyze,
    create_schema,
    read_metadata,
    rebuild_indexes,
    write_metadata,
)

__all__ = [
    "OPTIONAL_TABLES",
    "REQUIRED_TABLES",
    "DatabaseManager",
    "analyze",
    "apply_pragmas",
    "create_database_engine",
    "create_schema",
    "get_database_manager",
    "read_metadata",
    "rebuild_indexes",
    "write_metadata",
]
