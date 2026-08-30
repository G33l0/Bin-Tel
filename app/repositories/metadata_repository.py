"""Access to ``database_metadata`` and ``update_history``."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select

from app.database.schema import read_metadata, write_metadata
from app.models.entities import DatabaseMetadata, UpdateHistory, UpdateStatus
from app.repositories.base import BaseRepository


class MetadataRepository(BaseRepository):
    """The installed package's identity, and the record of every update."""

    def all(self) -> dict[str, str]:
        with self.session() as session:
            return read_metadata(session)

    def get(self, key: str, default: str | None = None) -> str | None:
        with self.session() as session:
            row = session.get(DatabaseMetadata, key)
            return row.value if row is not None else default

    def set_many(self, values: dict[str, object]) -> None:
        with self.transaction() as session:
            write_metadata(session, values)

    def version(self) -> str | None:
        return self.get(DatabaseMetadata.VERSION)

    def schema_version(self) -> int | None:
        raw = self.get(DatabaseMetadata.SCHEMA_VERSION)
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def record_count(self) -> int | None:
        raw = self.get(DatabaseMetadata.RECORD_COUNT)
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def timestamp(self, key: str) -> datetime | None:
        raw = self.get(key)
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    def mark_verified(self, when: datetime | None = None) -> None:
        self.set_many({DatabaseMetadata.LAST_VERIFIED: (when or datetime.now(UTC)).isoformat()})

    # -- update history ---------------------------------------------------
    def start_update(self, from_version: str | None, to_version: str | None) -> int:
        with self.transaction() as session:
            entry = UpdateHistory(
                from_version=from_version,
                to_version=to_version,
                status=UpdateStatus.STARTED.value,
            )
            session.add(entry)
            session.flush()
            return int(entry.id)

    def finish_update(
        self,
        entry_id: int,
        status: UpdateStatus,
        *,
        message: str | None = None,
        bytes_downloaded: int = 0,
        backup_path: str | None = None,
    ) -> None:
        with self.transaction() as session:
            entry = session.get(UpdateHistory, entry_id)
            if entry is None:
                return
            entry.status = status.value
            entry.finished_at = datetime.now(UTC)
            entry.message = message
            entry.bytes_downloaded = bytes_downloaded
            entry.backup_path = backup_path

    def history(self, limit: int = 25) -> list[UpdateHistory]:
        with self.session() as session:
            rows = (
                session.execute(
                    select(UpdateHistory).order_by(desc(UpdateHistory.started_at)).limit(limit)
                )
                .scalars()
                .all()
            )
            # Detach so the caller can read attributes after the session closes.
            for row in rows:
                session.expunge(row)
            return list(rows)

    def last_successful_update(self) -> datetime | None:
        with self.session() as session:
            return session.execute(
                select(UpdateHistory.finished_at)
                .where(UpdateHistory.status == UpdateStatus.SUCCESS.value)
                .order_by(desc(UpdateHistory.finished_at))
                .limit(1)
            ).scalar()
