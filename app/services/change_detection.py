"""Detects what a database update changed, for watched records only.

Diffing two multi-million-row databases in full would be slow and pointless:
what matters is whether anything the user is *watching* moved. So the scan is
driven by the watchlist — for each watched BIN, institution or country, the
snapshot recorded when it was added is compared against the newly installed
database, and only genuine differences become events.

Both databases are read through plain, read-only SQLite connections, so this
can run against a staged package before activation as well as after.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging_config import get_logger, log_event
from app.models.user_entities import ChangeType, WatchTargetType

logger = get_logger(__name__)

#: Fields compared for a watched BIN. Anything not listed is ignored, which
#: keeps churn (timestamps, confidence tweaks) out of the event stream.
BIN_FIELDS: dict[str, ChangeType] = {
    "institution": ChangeType.INSTITUTION_CHANGED,
    "network": ChangeType.NETWORK_CHANGED,
    "card_type": ChangeType.CARD_TYPE_CHANGED,
    "funding_type": ChangeType.FUNDING_TYPE_CHANGED,
    "country": ChangeType.COUNTRY_CHANGED,
    "status": ChangeType.STATUS_CHANGED,
    "city": ChangeType.LOCATION_CHANGED,
    "region": ChangeType.LOCATION_CHANGED,
    "postal_code": ChangeType.LOCATION_CHANGED,
}

INSTITUTION_FIELDS: dict[str, ChangeType] = {
    "display_name": ChangeType.INSTITUTION_CHANGED,
    "legal_name": ChangeType.INSTITUTION_CHANGED,
    "country": ChangeType.COUNTRY_CHANGED,
    "website": ChangeType.INSTITUTION_CHANGED,
    "status": ChangeType.STATUS_CHANGED,
    "city": ChangeType.LOCATION_CHANGED,
    "region": ChangeType.LOCATION_CHANGED,
    "bin_count": ChangeType.INSTITUTION_CHANGED,
}

#: Human-readable field names used in event summaries.
_FIELD_LABELS = {
    "institution": "issuer",
    "card_type": "card type",
    "funding_type": "funding type",
    "postal_code": "postal code",
    "display_name": "name",
    "legal_name": "legal name",
    "bin_count": "associated BIN count",
    "region": "state or province",
}


@dataclass(slots=True)
class DetectedChange:
    """One difference found on a watched target."""

    target_type: WatchTargetType
    target_value: str
    change_type: ChangeType
    field: str | None
    previous_value: str | None
    current_value: str | None
    summary: str


@dataclass(slots=True)
class ChangeScanResult:
    """Everything one scan found."""

    changes: list[DetectedChange] = field(default_factory=list)
    scanned: int = 0
    from_version: str | None = None
    to_version: str | None = None
    #: Fresh snapshots to store back against each watched item.
    snapshots: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        if not self.scanned:
            return "Nothing is being watched, so no comparison was needed."
        if not self.changes:
            return f"{self.scanned:,} watched item(s) checked; nothing changed."
        return f"{len(self.changes):,} change(s) across {self.scanned:,} watched item(s)."


def _label(field_name: str) -> str:
    return _FIELD_LABELS.get(field_name, field_name.replace("_", " "))


class ChangeDetectionService:
    """Compares watched records against a newly installed database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    def set_database_path(self, path: Path) -> None:
        self._database_path = Path(path)

    # -- snapshots --------------------------------------------------------
    def snapshot(self, target_type: WatchTargetType, target_value: str) -> dict[str, Any] | None:
        """Capture the comparable state of a target from the live database."""
        connection = self._connect()
        if connection is None:
            return None
        try:
            if target_type is WatchTargetType.BIN:
                return self._bin_snapshot(connection, target_value)
            if target_type is WatchTargetType.INSTITUTION:
                return self._institution_snapshot(connection, target_value)
            if target_type is WatchTargetType.COUNTRY:
                return self._country_snapshot(connection, target_value)
            return None
        finally:
            connection.close()

    # -- scanning ---------------------------------------------------------
    def scan(
        self,
        watched: Sequence[tuple[WatchTargetType, str, dict[str, Any] | None]],
        *,
        from_version: str | None = None,
        to_version: str | None = None,
    ) -> ChangeScanResult:
        """Compare each watched target's stored snapshot against the database.

        *watched* is ``(type, value, previous_snapshot)`` triples; a target with
        no previous snapshot is recorded but produces no events, because there
        is nothing to compare it against yet.
        """
        result = ChangeScanResult(from_version=from_version, to_version=to_version)
        connection = self._connect()
        if connection is None:
            logger.warning("Change detection skipped: the database is not readable")
            return result

        try:
            for target_type, target_value, previous in watched:
                result.scanned += 1
                current = self._snapshot_with(connection, target_type, target_value)
                if current is not None:
                    result.snapshots[(target_type.value, target_value)] = current
                if previous is None:
                    continue
                result.changes.extend(
                    self._compare(target_type, target_value, previous, current)
                )
        finally:
            connection.close()

        log_event(
            logger,
            "Change detection completed",
            scanned=result.scanned,
            changes=len(result.changes),
            from_version=from_version,
            to_version=to_version,
        )
        return result

    def _snapshot_with(
        self, connection: sqlite3.Connection, target_type: WatchTargetType, value: str
    ) -> dict[str, Any] | None:
        if target_type is WatchTargetType.BIN:
            return self._bin_snapshot(connection, value)
        if target_type is WatchTargetType.INSTITUTION:
            return self._institution_snapshot(connection, value)
        if target_type is WatchTargetType.COUNTRY:
            return self._country_snapshot(connection, value)
        return None

    # -- comparison -------------------------------------------------------
    def _compare(
        self,
        target_type: WatchTargetType,
        target_value: str,
        previous: dict[str, Any],
        current: dict[str, Any] | None,
    ) -> list[DetectedChange]:
        if current is None:
            return [self._removal(target_type, target_value, previous)]
        if not previous.get("_exists", True) and current.get("_exists", True):
            return [self._addition(target_type, target_value, current)]
        if not current.get("_exists", True):
            return [self._removal(target_type, target_value, previous)]

        fields = BIN_FIELDS if target_type is WatchTargetType.BIN else INSTITUTION_FIELDS
        if target_type is WatchTargetType.COUNTRY:
            fields = {
                "bin_count": ChangeType.BIN_ADDED,
                "institution_count": ChangeType.INSTITUTION_CHANGED,
            }

        changes: list[DetectedChange] = []
        for name, change_type in fields.items():
            before, after = previous.get(name), current.get(name)
            if before == after:
                continue
            if before in (None, "") and after in (None, ""):
                continue
            changes.append(
                DetectedChange(
                    target_type=target_type,
                    target_value=target_value,
                    change_type=self._refine(change_type, name, before, after),
                    field=name,
                    previous_value=None if before is None else str(before),
                    current_value=None if after is None else str(after),
                    summary=self._describe(target_type, target_value, name, before, after),
                )
            )
        return changes

    @staticmethod
    def _refine(
        change_type: ChangeType, name: str, before: Any, after: Any
    ) -> ChangeType:
        """Turn a count change into the more specific added/removed event."""
        if name in ("bin_count",) and isinstance(before, int) and isinstance(after, int):
            return ChangeType.BIN_ADDED if after > before else ChangeType.BIN_REMOVED
        return change_type

    @staticmethod
    def _present(value: Any) -> str:
        """Render a stored value the way the interface shows it."""
        if isinstance(value, str):
            return value.replace("_", " ").title() if value.islower() else value
        if isinstance(value, int):
            return f"{value:,}"
        return str(value)

    @classmethod
    def _describe(
        cls, target_type: WatchTargetType, value: str, name: str, before: Any, after: Any
    ) -> str:
        subject = f"BIN {value}" if target_type is WatchTargetType.BIN else value
        label = _label(name)
        if before in (None, ""):
            return f"{subject}: {label} set to {cls._present(after)}."
        if after in (None, ""):
            return f"{subject}: {label} is no longer recorded (was {cls._present(before)})."
        return (
            f"{subject}: {label} changed from {cls._present(before)} "
            f"to {cls._present(after)}."
        )

    @staticmethod
    def _removal(
        target_type: WatchTargetType, value: str, previous: dict[str, Any]
    ) -> DetectedChange:
        change_type = (
            ChangeType.BIN_REMOVED
            if target_type is WatchTargetType.BIN
            else ChangeType.INSTITUTION_REMOVED
        )
        subject = f"BIN {value}" if target_type is WatchTargetType.BIN else value
        return DetectedChange(
            target_type=target_type,
            target_value=value,
            change_type=change_type,
            field=None,
            previous_value=json.dumps(previous.get("institution") or previous.get("display_name")),
            current_value=None,
            summary=f"{subject} is no longer present in the database.",
        )

    @staticmethod
    def _addition(
        target_type: WatchTargetType, value: str, current: dict[str, Any]
    ) -> DetectedChange:
        change_type = (
            ChangeType.BIN_ADDED
            if target_type is WatchTargetType.BIN
            else ChangeType.INSTITUTION_ADDED
        )
        subject = f"BIN {value}" if target_type is WatchTargetType.BIN else value
        return DetectedChange(
            target_type=target_type,
            target_value=value,
            change_type=change_type,
            field=None,
            previous_value=None,
            current_value=str(current.get("institution") or current.get("display_name") or ""),
            summary=f"{subject} has been added to the database.",
        )

    # -- snapshot queries --------------------------------------------------
    def _connect(self) -> sqlite3.Connection | None:
        if not self._database_path.exists():
            return None
        try:
            connection = sqlite3.connect(
                f"file:{self._database_path}?mode=ro", uri=True, timeout=15.0
            )
        except sqlite3.DatabaseError:  # pragma: no cover - unreadable file
            return None
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _bin_snapshot(connection: sqlite3.Connection, value: str) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT b.bin, b.card_type, b.funding_type, b.status, b.currency_code,
                   n.display_name AS network, c.name AS country,
                   i.display_name AS institution, i.uid AS institution_uid,
                   a.city, a.region, a.postal_code
            FROM bins b
            LEFT JOIN networks n ON n.id = b.network_id
            LEFT JOIN countries c ON c.id = b.country_id
            LEFT JOIN bin_institutions bi
                   ON bi.bin_id = b.id AND bi.is_primary = 1
            LEFT JOIN institutions i ON i.id = bi.institution_id
            LEFT JOIN addresses a ON a.institution_id = i.id AND a.is_primary = 1
            WHERE b.bin = ?
            LIMIT 1
            """,
            (value,),
        ).fetchone()
        if row is None:
            return {"_exists": False}
        snapshot = {key: row[key] for key in row.keys()}
        snapshot["_exists"] = True
        return snapshot

    @staticmethod
    def _institution_snapshot(connection: sqlite3.Connection, uid: str) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT i.uid, i.display_name, i.legal_name, i.website, i.status,
                   c.name AS country, a.city, a.region,
                   (SELECT COUNT(*) FROM bin_institutions bi
                     WHERE bi.institution_id = i.id) AS bin_count
            FROM institutions i
            LEFT JOIN countries c ON c.id = i.country_id
            LEFT JOIN addresses a ON a.institution_id = i.id AND a.is_primary = 1
            WHERE i.uid = ?
            LIMIT 1
            """,
            (uid,),
        ).fetchone()
        if row is None:
            return {"_exists": False}
        snapshot = {key: row[key] for key in row.keys()}
        snapshot["_exists"] = True
        return snapshot

    @staticmethod
    def _country_snapshot(connection: sqlite3.Connection, iso2: str) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT c.iso2, c.name,
                   (SELECT COUNT(*) FROM bins b WHERE b.country_id = c.id) AS bin_count,
                   (SELECT COUNT(*) FROM institutions i
                     WHERE i.country_id = c.id) AS institution_count
            FROM countries c
            WHERE c.iso2 = ?
            LIMIT 1
            """,
            (iso2.upper(),),
        ).fetchone()
        if row is None:
            return {"_exists": False}
        snapshot = {key: row[key] for key in row.keys()}
        snapshot["_exists"] = True
        return snapshot


def serialise(snapshot: dict[str, Any] | None) -> str | None:
    return None if snapshot is None else json.dumps(snapshot, sort_keys=True, default=str)


def deserialise(payload: str | None) -> dict[str, Any] | None:
    if not payload:
        return None
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def watched_targets(items: Iterable[Any]) -> list[tuple[WatchTargetType, str, dict[str, Any] | None]]:
    """Adapt ``WatchlistItem`` rows into the triples :meth:`scan` expects."""
    targets: list[tuple[WatchTargetType, str, dict[str, Any] | None]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        try:
            target_type = WatchTargetType(item.target_type)
        except ValueError:
            continue
        if target_type is WatchTargetType.SAVED_SEARCH:
            continue
        key = (target_type.value, item.target_value)
        if key in seen:
            continue
        seen.add(key)
        targets.append((target_type, item.target_value, deserialise(item.snapshot)))
    return targets
