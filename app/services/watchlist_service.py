"""Watchlists: what to track, and what changed about it.

A watchlist holds targets — BINs, institutions, countries, saved searches —
and each target carries a snapshot of the record as it stood when it was
added. After a database update the snapshots are re-taken and diffed, and the
differences become events the user can read.

Everything lives in the user-data store, so a database update replaces the
intelligence database without touching a single watchlist.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select

from app.core.errors import ValidationError
from app.core.logging_config import get_logger, log_event
from app.database.user_store import UserDataStore
from app.models.user_entities import (
    ChangeType,
    Notification,
    Watchlist,
    WatchlistEvent,
    WatchlistItem,
    WatchTargetType,
)
from app.services.change_detection import (
    ChangeDetectionService,
    ChangeScanResult,
    serialise,
    watched_targets,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WatchlistSummary:
    """A watchlist as the interface lists it."""

    id: int
    name: str
    description: str
    item_count: int
    unread_events: int
    notify: bool
    created_at: datetime
    updated_at: datetime

    @property
    def subtitle(self) -> str:
        parts = [f"{self.item_count:,} item{'s' if self.item_count != 1 else ''}"]
        if self.unread_events:
            parts.append(f"{self.unread_events:,} new alert(s)")
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class WatchedItem:
    """One target inside a watchlist."""

    id: int
    watchlist_id: int
    target_type: WatchTargetType
    target_value: str
    label: str
    notes: str
    has_snapshot: bool
    created_at: datetime

    @property
    def display_label(self) -> str:
        return self.label or self.target_value


@dataclass(frozen=True, slots=True)
class WatchAlert:
    """A detected change, as the interface presents it."""

    id: int
    watchlist_id: int
    watchlist_name: str
    change_type: ChangeType
    target_type: WatchTargetType
    target_value: str
    summary: str
    field: str | None
    previous_value: str | None
    current_value: str | None
    from_version: str | None
    to_version: str | None
    detected_at: datetime
    acknowledged: bool

    @property
    def severity(self) -> str:
        return self.change_type.severity


class WatchlistService:
    """Creates watchlists, tracks targets and records what changed."""

    def __init__(self, store: UserDataStore, detection: ChangeDetectionService) -> None:
        self._store = store
        self._detection = detection

    def set_database_path(self, path: Path) -> None:
        self._detection.set_database_path(path)

    # -- watchlists -------------------------------------------------------
    def create(self, name: str, description: str = "", *, notify: bool = True) -> WatchlistSummary:
        cleaned = " ".join((name or "").split())[:128]
        if not cleaned:
            raise ValidationError("Give the watchlist a name.")
        with self._store.transaction() as session:
            existing = session.execute(
                select(Watchlist).where(func.lower(Watchlist.name) == cleaned.lower())
            ).scalar_one_or_none()
            if existing is not None:
                raise ValidationError(f"A watchlist called “{cleaned}” already exists.")
            watchlist = Watchlist(name=cleaned, description=description or None, notify=notify)
            session.add(watchlist)
            session.flush()
            summary = WatchlistSummary(
                id=watchlist.id,
                name=watchlist.name,
                description=watchlist.description or "",
                item_count=0,
                unread_events=0,
                notify=watchlist.notify,
                created_at=watchlist.created_at,
                updated_at=watchlist.updated_at,
            )
        log_event(logger, "Watchlist created", items=0)
        return summary

    def rename(self, watchlist_id: int, name: str, description: str | None = None) -> None:
        cleaned = " ".join((name or "").split())[:128]
        if not cleaned:
            raise ValidationError("Give the watchlist a name.")
        with self._store.transaction() as session:
            watchlist = session.get(Watchlist, watchlist_id)
            if watchlist is None:
                raise ValidationError("That watchlist no longer exists.")
            clash = session.execute(
                select(Watchlist).where(
                    func.lower(Watchlist.name) == cleaned.lower(),
                    Watchlist.id != watchlist_id,
                )
            ).scalar_one_or_none()
            if clash is not None:
                raise ValidationError(f"A watchlist called “{cleaned}” already exists.")
            watchlist.name = cleaned
            if description is not None:
                watchlist.description = description or None

    def set_notify(self, watchlist_id: int, notify: bool) -> None:
        with self._store.transaction() as session:
            watchlist = session.get(Watchlist, watchlist_id)
            if watchlist is not None:
                watchlist.notify = notify

    def delete(self, watchlist_id: int) -> None:
        with self._store.transaction() as session:
            watchlist = session.get(Watchlist, watchlist_id)
            if watchlist is not None:
                session.delete(watchlist)
        log_event(logger, "Watchlist deleted")

    def list(self) -> list[WatchlistSummary]:
        with self._store.session() as session:
            counts = dict(
                session.execute(
                    select(WatchlistItem.watchlist_id, func.count(WatchlistItem.id)).group_by(
                        WatchlistItem.watchlist_id
                    )
                ).all()
            )
            unread = dict(
                session.execute(
                    select(WatchlistEvent.watchlist_id, func.count(WatchlistEvent.id))
                    .where(WatchlistEvent.acknowledged.is_(False))
                    .group_by(WatchlistEvent.watchlist_id)
                ).all()
            )
            rows = (
                session.execute(select(Watchlist).order_by(Watchlist.name)).scalars().all()
            )
            return [
                WatchlistSummary(
                    id=row.id,
                    name=row.name,
                    description=row.description or "",
                    item_count=int(counts.get(row.id, 0)),
                    unread_events=int(unread.get(row.id, 0)),
                    notify=row.notify,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

    def count(self) -> int:
        with self._store.session() as session:
            return int(session.execute(select(func.count()).select_from(Watchlist)).scalar() or 0)

    def item_count(self) -> int:
        with self._store.session() as session:
            return int(
                session.execute(select(func.count()).select_from(WatchlistItem)).scalar() or 0
            )

    # -- items ------------------------------------------------------------
    def add_item(
        self,
        watchlist_id: int,
        target_type: WatchTargetType,
        target_value: str,
        label: str = "",
        notes: str = "",
        *,
        database_version: str | None = None,
    ) -> WatchedItem:
        """Add a target and capture its current state as the baseline."""
        value = (target_value or "").strip()
        if not value:
            raise ValidationError("Nothing to watch — provide a BIN, institution or country.")
        if target_type is WatchTargetType.COUNTRY:
            value = value.upper()

        snapshot = self._detection.snapshot(target_type, value)
        with self._store.transaction() as session:
            if session.get(Watchlist, watchlist_id) is None:
                raise ValidationError("That watchlist no longer exists.")
            existing = session.execute(
                select(WatchlistItem).where(
                    WatchlistItem.watchlist_id == watchlist_id,
                    WatchlistItem.target_type == target_type.value,
                    WatchlistItem.target_value == value,
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ValidationError(f"“{label or value}” is already on this watchlist.")

            item = WatchlistItem(
                watchlist_id=watchlist_id,
                target_type=target_type.value,
                target_value=value,
                label=label or None,
                notes=notes or None,
                snapshot=serialise(snapshot),
                snapshot_version=database_version,
            )
            session.add(item)
            session.flush()
            result = WatchedItem(
                id=item.id,
                watchlist_id=watchlist_id,
                target_type=target_type,
                target_value=value,
                label=label or "",
                notes=notes or "",
                has_snapshot=snapshot is not None,
                created_at=item.created_at,
            )
        return result

    def remove_item(self, item_id: int) -> None:
        with self._store.transaction() as session:
            item = session.get(WatchlistItem, item_id)
            if item is not None:
                session.delete(item)

    def items(self, watchlist_id: int) -> list[WatchedItem]:
        with self._store.session() as session:
            rows = (
                session.execute(
                    select(WatchlistItem)
                    .where(WatchlistItem.watchlist_id == watchlist_id)
                    .order_by(WatchlistItem.target_type, WatchlistItem.target_value)
                )
                .scalars()
                .all()
            )
            return [
                WatchedItem(
                    id=row.id,
                    watchlist_id=row.watchlist_id,
                    target_type=WatchTargetType(row.target_type),
                    target_value=row.target_value,
                    label=row.label or "",
                    notes=row.notes or "",
                    has_snapshot=bool(row.snapshot),
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def is_watched(self, target_type: WatchTargetType, target_value: str) -> bool:
        with self._store.session() as session:
            return (
                session.execute(
                    select(WatchlistItem.id).where(
                        WatchlistItem.target_type == target_type.value,
                        WatchlistItem.target_value == target_value.strip(),
                    )
                ).scalar()
                is not None
            )

    def watchlists_for(self, target_type: WatchTargetType, target_value: str) -> list[int]:
        with self._store.session() as session:
            return list(
                session.execute(
                    select(WatchlistItem.watchlist_id).where(
                        WatchlistItem.target_type == target_type.value,
                        WatchlistItem.target_value == target_value.strip(),
                    )
                )
                .scalars()
                .all()
            )

    # -- change detection --------------------------------------------------
    def scan_for_changes(
        self, *, from_version: str | None = None, to_version: str | None = None
    ) -> list[WatchAlert]:
        """Diff every watched target and record what changed.

        Called after a database update completes. Snapshots are refreshed in
        the same pass, so the next update compares against this release.
        """
        with self._store.session() as session:
            rows = session.execute(select(WatchlistItem)).scalars().all()
            targets = watched_targets(rows)
            index = {
                (row.target_type, row.target_value): (row.id, row.watchlist_id) for row in rows
            }
        if not targets:
            return []

        result: ChangeScanResult = self._detection.scan(
            targets, from_version=from_version, to_version=to_version
        )
        alerts = self._record(result, index, from_version, to_version)
        if alerts:
            log_event(
                logger,
                "Watchlist changes detected",
                alerts=len(alerts),
                from_version=from_version,
                to_version=to_version,
            )
        return alerts

    def _record(
        self,
        result: ChangeScanResult,
        index: dict[tuple[str, str], tuple[int, int]],
        from_version: str | None,
        to_version: str | None,
    ) -> list[WatchAlert]:
        alerts: list[WatchAlert] = []
        with self._store.transaction() as session:
            names = dict(session.execute(select(Watchlist.id, Watchlist.name)).all())
            notify = dict(session.execute(select(Watchlist.id, Watchlist.notify)).all())

            for change in result.changes:
                key = (change.target_type.value, change.target_value)
                entry = index.get(key)
                if entry is None:
                    continue
                item_id, watchlist_id = entry
                event = WatchlistEvent(
                    watchlist_id=watchlist_id,
                    item_id=item_id,
                    change_type=change.change_type.value,
                    target_type=change.target_type.value,
                    target_value=change.target_value,
                    field=change.field,
                    previous_value=change.previous_value,
                    current_value=change.current_value,
                    summary=change.summary,
                    from_version=from_version,
                    to_version=to_version,
                )
                session.add(event)
                session.flush()
                alerts.append(
                    WatchAlert(
                        id=event.id,
                        watchlist_id=watchlist_id,
                        watchlist_name=str(names.get(watchlist_id, "")),
                        change_type=change.change_type,
                        target_type=change.target_type,
                        target_value=change.target_value,
                        summary=change.summary,
                        field=change.field,
                        previous_value=change.previous_value,
                        current_value=change.current_value,
                        from_version=from_version,
                        to_version=to_version,
                        detected_at=event.detected_at,
                        acknowledged=False,
                    )
                )
                if notify.get(watchlist_id, True):
                    session.add(
                        Notification(
                            title=f"Watchlist alert — {names.get(watchlist_id, 'Watchlist')}",
                            body=change.summary,
                            kind=change.change_type.severity,
                            source="watchlist",
                            action="watchlists",
                        )
                    )

            # Refresh every snapshot so the next update compares against this
            # release rather than the one the item was first added under.
            for (target_type, target_value), snapshot in result.snapshots.items():
                entry = index.get((target_type, target_value))
                if entry is None:
                    continue
                item = session.get(WatchlistItem, entry[0])
                if item is not None:
                    item.snapshot = json.dumps(snapshot, sort_keys=True, default=str)
                    item.snapshot_version = to_version
        return alerts

    # -- events -----------------------------------------------------------
    def events(
        self,
        watchlist_id: int | None = None,
        *,
        limit: int = 200,
        unread_only: bool = False,
    ) -> list[WatchAlert]:
        with self._store.session() as session:
            names = dict(session.execute(select(Watchlist.id, Watchlist.name)).all())
            statement = select(WatchlistEvent).order_by(WatchlistEvent.detected_at.desc())
            if watchlist_id is not None:
                statement = statement.where(WatchlistEvent.watchlist_id == watchlist_id)
            if unread_only:
                statement = statement.where(WatchlistEvent.acknowledged.is_(False))
            rows = session.execute(statement.limit(limit)).scalars().all()
            return [
                WatchAlert(
                    id=row.id,
                    watchlist_id=row.watchlist_id,
                    watchlist_name=str(names.get(row.watchlist_id, "")),
                    change_type=_change_type(row.change_type),
                    target_type=WatchTargetType(row.target_type),
                    target_value=row.target_value,
                    summary=row.summary,
                    field=row.field,
                    previous_value=row.previous_value,
                    current_value=row.current_value,
                    from_version=row.from_version,
                    to_version=row.to_version,
                    detected_at=row.detected_at,
                    acknowledged=row.acknowledged,
                )
                for row in rows
            ]

    def unread_count(self) -> int:
        with self._store.session() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(WatchlistEvent)
                    .where(WatchlistEvent.acknowledged.is_(False))
                ).scalar()
                or 0
            )

    def acknowledge(self, event_ids: Sequence[int] | None = None) -> int:
        """Mark events as read. With no ids, marks everything read."""
        with self._store.transaction() as session:
            statement = select(WatchlistEvent).where(WatchlistEvent.acknowledged.is_(False))
            if event_ids:
                statement = statement.where(WatchlistEvent.id.in_(list(event_ids)))
            rows = session.execute(statement).scalars().all()
            for row in rows:
                row.acknowledged = True
                row.acknowledged_at = datetime.now(UTC)
            return len(rows)

    def clear_events(self, watchlist_id: int | None = None) -> int:
        with self._store.transaction() as session:
            statement = delete(WatchlistEvent)
            if watchlist_id is not None:
                statement = statement.where(WatchlistEvent.watchlist_id == watchlist_id)
            result = session.execute(statement)
            return int(result.rowcount or 0)


def _change_type(value: str) -> ChangeType:
    try:
        return ChangeType(value)
    except ValueError:  # pragma: no cover - defensive
        return ChangeType.INSTITUTION_CHANGED
