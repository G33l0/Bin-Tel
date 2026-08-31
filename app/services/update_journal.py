"""A durable record of database updates.

``update_history`` lives *inside* the database, so it is replaced along with
everything else each time a new package is activated. The journal keeps the
same information in the application-data directory, where it survives every
swap — which is what the Updates page reads.

Both are written: the in-database table describes how *that* database came to
be installed, and the journal describes the whole history of the installation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import _atomic_write
from app.core.logging_config import get_logger

logger = get_logger(__name__)

#: Entries older than this position are discarded, newest first.
MAX_ENTRIES = 50

JournalStatus = Literal["success", "failed", "cancelled", "rolled_back"]


class UpdateEntry(BaseModel):
    """One recorded attempt to install a database package."""

    model_config = ConfigDict(extra="ignore")

    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    from_version: str | None = None
    to_version: str | None = None
    status: JournalStatus = "success"
    bytes_downloaded: int = 0
    message: str = ""

    @property
    def label(self) -> str:
        target = self.to_version or "unknown"
        if self.status == "success":
            return f"Installed {target}"
        return f"{self.status.replace('_', ' ').capitalize()} — {target}"


class UpdateJournal:
    """Append-only JSON log of update attempts."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def entries(self) -> list[UpdateEntry]:
        """Every recorded attempt, newest first. Unreadable files yield ``[]``."""
        if not self._path.exists():
            return []
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("The update journal could not be read; starting a new one")
            return []
        if not isinstance(raw, list):
            return []
        entries: list[UpdateEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(UpdateEntry.model_validate(item))
            except ValidationError:
                continue
        return entries

    def record(self, entry: UpdateEntry) -> UpdateEntry:
        """Prepend *entry* and trim the journal to :data:`MAX_ENTRIES`."""
        entries = [entry, *self.entries()][:MAX_ENTRIES]
        payload = json.dumps(
            [item.model_dump(mode="json") for item in entries], indent=2
        )
        try:
            _atomic_write(self._path, payload)
        except OSError as exc:  # pragma: no cover - read-only data directory
            logger.warning("Could not write the update journal: %s", exc)
        return entry

    def record_success(
        self,
        to_version: str | None,
        from_version: str | None = None,
        *,
        bytes_downloaded: int = 0,
        message: str = "",
    ) -> UpdateEntry:
        return self.record(
            UpdateEntry(
                from_version=from_version,
                to_version=to_version,
                status="success",
                bytes_downloaded=bytes_downloaded,
                message=message,
            )
        )

    def record_failure(
        self,
        to_version: str | None,
        message: str,
        *,
        from_version: str | None = None,
        status: JournalStatus = "failed",
    ) -> UpdateEntry:
        return self.record(
            UpdateEntry(
                from_version=from_version,
                to_version=to_version,
                status=status,
                message=message,
            )
        )

    def last_success(self) -> UpdateEntry | None:
        return next((entry for entry in self.entries() if entry.status == "success"), None)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
