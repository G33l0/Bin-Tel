"""Importer for a foreign SQLite database.

Reads any table whose columns can be mapped onto the Bin-Tel record shape, in
server-side cursor fashion so a large donor database is never fully loaded.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from app.core.errors import ImportError_
from app.importers.base import FIELD_ALIASES, BaseImporter, ImportOptions
from app.services.ingest_service import RawBinRecord

_CANDIDATE_TABLES = ("bins", "bin", "iins", "records", "data", "bin_list")
_FETCH_SIZE = 2000


class SQLiteImporter(BaseImporter):
    """Pulls records out of another SQLite file."""

    name = "sqlite"
    extensions = (".sqlite", ".sqlite3", ".db")

    def __init__(self, options: ImportOptions, table: str | None = None) -> None:
        super().__init__(options)
        self._table = table or self._detect_table()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                f"file:{self.options.source}?mode=ro", uri=True, timeout=15.0
            )
        except sqlite3.DatabaseError as exc:
            raise ImportError_(
                "The SQLite file could not be opened.", detail=str(exc)
            ) from exc
        connection.row_factory = sqlite3.Row
        return connection

    def _detect_table(self) -> str:
        """Pick the table that most looks like a BIN table."""
        with self._connect() as connection:
            names = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            if not names:
                raise ImportError_("That SQLite file contains no tables.")
            for candidate in _CANDIDATE_TABLES:
                if candidate in names:
                    return candidate
            best: tuple[int, str] | None = None
            for name in names:
                columns = {
                    str(row[1]).lower()
                    for row in connection.execute(f'PRAGMA table_info("{name}")').fetchall()
                }
                score = len(columns & set(FIELD_ALIASES))
                if any(column in columns for column in ("bin", "iin", "bin_number")):
                    score += 5
                if best is None or score > best[0]:
                    best = (score, name)
            if best is None or best[0] == 0:
                raise ImportError_(
                    "No table in that SQLite file looks like a BIN table.",
                    detail=f"Tables inspected: {', '.join(names)}",
                )
            return best[1]

    @property
    def table(self) -> str:
        return self._table

    def estimated_total(self) -> int | None:
        try:
            with self._connect() as connection:
                # ``_table`` comes from sqlite_master, never from user input.
                return int(
                    connection.execute(f'SELECT COUNT(*) FROM "{self._table}"').fetchone()[0]
                )
        except sqlite3.DatabaseError:  # pragma: no cover
            return None

    def iter_records(self) -> Iterator[RawBinRecord]:
        connection = self._connect()
        try:
            cursor = connection.execute(f'SELECT * FROM "{self._table}"')
            while rows := cursor.fetchmany(_FETCH_SIZE):
                for row in rows:
                    record = self.to_record(self.map_row(dict(row)))
                    if record is not None:
                        yield record
        except sqlite3.DatabaseError as exc:
            raise ImportError_(
                "The SQLite file could not be read.", detail=str(exc)
            ) from exc
        finally:
            connection.close()
