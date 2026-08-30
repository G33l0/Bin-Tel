"""Dashboard and Database-page statistics."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.core.logging_config import get_logger
from app.models.entities import DatabaseMetadata
from app.models.schemas import DatabaseInfo, DatabaseStats
from app.repositories.metadata_repository import MetadataRepository
from app.repositories.stats_repository import StatsRepository

logger = get_logger(__name__)


class StatsService:
    """Assembles the health-and-scale picture of the local database."""

    def __init__(
        self,
        stats: StatsRepository,
        metadata: MetadataRepository,
        database_path: Path,
    ) -> None:
        self._stats = stats
        self._metadata = metadata
        self._database_path = database_path

    def set_database_path(self, path: Path) -> None:
        self._database_path = path

    def stats(self) -> DatabaseStats:
        if not self._stats.is_available:
            return DatabaseStats()
        return self._stats.stats()

    def info(self) -> DatabaseInfo:
        path = self._database_path
        if not self._metadata.is_available or not path.exists():
            return DatabaseInfo(
                installed=False,
                path=str(path),
                healthy=False,
                status_message="Not installed",
            )
        metadata = self._metadata.all()
        stats = self.stats()
        size = None
        try:
            size = path.stat().st_size
            # WAL content counts toward what the database occupies on disk.
            wal = path.with_name(path.name + "-wal")
            if wal.exists():
                size += wal.stat().st_size
        except OSError:  # pragma: no cover - vanished mid-read
            size = None

        return DatabaseInfo(
            installed=True,
            path=str(path),
            version=metadata.get(DatabaseMetadata.VERSION) or None,
            schema_version=_as_int(metadata.get(DatabaseMetadata.SCHEMA_VERSION)),
            release_date=_as_datetime(metadata.get(DatabaseMetadata.RELEASE_DATE)),
            installed_at=_as_datetime(metadata.get(DatabaseMetadata.INSTALLED_AT)),
            last_verified=_as_datetime(metadata.get(DatabaseMetadata.LAST_VERIFIED)),
            size_bytes=size,
            record_count=_as_int(metadata.get(DatabaseMetadata.RECORD_COUNT)) or stats.bins,
            publisher=metadata.get(DatabaseMetadata.PUBLISHER) or None,
            stats=stats,
            healthy=stats.bins > 0,
            status_message="Ready" if stats.bins > 0 else "Empty database",
        )

    def top_countries(self, limit: int = 8) -> list[tuple[str, int]]:
        return self._stats.top_countries(limit) if self._stats.is_available else []

    def top_networks(self, limit: int = 8) -> list[tuple[str, int]]:
        return self._stats.top_networks(limit) if self._stats.is_available else []

    def card_type_breakdown(self) -> dict[str, int]:
        return self._stats.card_type_breakdown() if self._stats.is_available else {}


def _as_int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
