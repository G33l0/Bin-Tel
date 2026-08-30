"""The user's own workspace: saved searches, history, favourites, notifications.

All of it lives in the user-data store, so a database update leaves it intact.
Quotas come from the entitlement service rather than being hard-coded, which
is what lets the free tier keep a useful amount of it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from app.core.errors import ValidationError
from app.core.logging_config import get_logger
from app.database.user_store import UserDataStore
from app.models.schemas import AdvancedQuery
from app.models.user_entities import (
    Favorite,
    FavoriteKind,
    GeneratedReport,
    Notification,
    ReportTemplate,
    SavedSearch,
    SearchHistoryEntry,
    SearchKind,
)

logger = get_logger(__name__)

#: Recent searches kept regardless of preference, so the palette has content.
HISTORY_HARD_CAP = 500


@dataclass(frozen=True, slots=True)
class SavedSearchInfo:
    id: int
    name: str
    kind: SearchKind
    query: str
    criteria: AdvancedQuery | None
    pinned: bool
    run_count: int
    last_run_at: datetime | None
    last_result_count: int | None

    @property
    def subtitle(self) -> str:
        if self.criteria is not None and not self.criteria.is_empty:
            return self.criteria.describe()
        return self.query or self.kind.label


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: int
    kind: SearchKind
    query: str
    result_count: int
    searched_at: datetime


@dataclass(frozen=True, slots=True)
class FavoriteInfo:
    id: int
    kind: FavoriteKind
    target_value: str
    label: str
    subtitle: str
    created_at: datetime

    @property
    def display_label(self) -> str:
        return self.label or self.target_value


@dataclass(frozen=True, slots=True)
class NotificationInfo:
    id: int
    title: str
    body: str
    kind: str
    source: str
    action: str | None
    read: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TemplateInfo:
    id: int
    name: str
    description: str
    report_type: str
    output_format: str
    criteria: str
    use_count: int
    last_used_at: datetime | None


class WorkspaceService:
    """Saved searches, recent searches, favourites, templates, notifications."""

    def __init__(self, store: UserDataStore) -> None:
        self._store = store

    # -- saved searches ----------------------------------------------------
    def save_search(
        self,
        name: str,
        *,
        kind: SearchKind = SearchKind.BIN,
        query: str = "",
        criteria: AdvancedQuery | None = None,
        pinned: bool = False,
        limit: int | None = None,
    ) -> SavedSearchInfo:
        cleaned = " ".join((name or "").split())[:128]
        if not cleaned:
            raise ValidationError("Give the saved search a name.")
        if limit is not None and limit >= 0 and self.saved_search_count() >= limit:
            raise ValidationError(
                f"Your plan includes {limit} saved search(es). Remove one, or upgrade "
                "for more."
            )
        with self._store.transaction() as session:
            existing = session.execute(
                select(SavedSearch).where(func.lower(SavedSearch.name) == cleaned.lower())
            ).scalar_one_or_none()
            row = existing or SavedSearch(name=cleaned)
            row.name = cleaned
            row.kind = kind.value
            row.query = query or None
            row.criteria = criteria.model_dump_json() if criteria is not None else None
            row.pinned = pinned
            if existing is None:
                session.add(row)
            session.flush()
            return _saved_search_info(row)

    def saved_searches(self) -> list[SavedSearchInfo]:
        with self._store.session() as session:
            rows = (
                session.execute(
                    select(SavedSearch).order_by(
                        SavedSearch.pinned.desc(), SavedSearch.name
                    )
                )
                .scalars()
                .all()
            )
            return [_saved_search_info(row) for row in rows]

    def saved_search_count(self) -> int:
        with self._store.session() as session:
            return int(
                session.execute(select(func.count()).select_from(SavedSearch)).scalar() or 0
            )

    def delete_saved_search(self, search_id: int) -> None:
        with self._store.transaction() as session:
            row = session.get(SavedSearch, search_id)
            if row is not None:
                session.delete(row)

    def set_pinned(self, search_id: int, pinned: bool) -> None:
        with self._store.transaction() as session:
            row = session.get(SavedSearch, search_id)
            if row is not None:
                row.pinned = pinned

    def record_saved_search_run(self, search_id: int, result_count: int) -> None:
        with self._store.transaction() as session:
            row = session.get(SavedSearch, search_id)
            if row is not None:
                row.run_count += 1
                row.last_run_at = datetime.now(UTC)
                row.last_result_count = result_count

    # -- history -----------------------------------------------------------
    def record_search(
        self,
        query: str,
        kind: SearchKind = SearchKind.BIN,
        *,
        result_count: int = 0,
        elapsed_ms: float = 0.0,
        enabled: bool = True,
        keep: int = 50,
    ) -> None:
        """Record a search locally. Never transmitted anywhere."""
        term = " ".join((query or "").split())
        if not term or not enabled or keep <= 0:
            return
        with self._store.transaction() as session:
            session.execute(
                delete(SearchHistoryEntry).where(
                    SearchHistoryEntry.kind == kind.value,
                    func.lower(SearchHistoryEntry.query) == term.lower(),
                )
            )
            session.add(
                SearchHistoryEntry(
                    kind=kind.value,
                    query=term[:256],
                    result_count=result_count,
                    elapsed_ms=elapsed_ms,
                )
            )
            cap = min(max(keep, 1), HISTORY_HARD_CAP)
            keep_ids = select(SearchHistoryEntry.id).order_by(
                SearchHistoryEntry.searched_at.desc()
            ).limit(cap)
            session.execute(
                delete(SearchHistoryEntry).where(SearchHistoryEntry.id.notin_(keep_ids))
            )

    def history(self, kind: SearchKind | None = None, limit: int = 25) -> list[HistoryEntry]:
        with self._store.session() as session:
            statement = select(SearchHistoryEntry).order_by(
                SearchHistoryEntry.searched_at.desc()
            )
            if kind is not None:
                statement = statement.where(SearchHistoryEntry.kind == kind.value)
            rows = session.execute(statement.limit(limit)).scalars().all()
            return [
                HistoryEntry(
                    id=row.id,
                    kind=_search_kind(row.kind),
                    query=row.query,
                    result_count=row.result_count,
                    searched_at=row.searched_at,
                )
                for row in rows
            ]

    def recent_terms(self, kind: SearchKind | None = None, limit: int = 25) -> list[str]:
        return [entry.query for entry in self.history(kind, limit)]

    def clear_history(self) -> int:
        with self._store.transaction() as session:
            result = session.execute(delete(SearchHistoryEntry))
            return int(result.rowcount or 0)

    # -- favourites --------------------------------------------------------
    def add_favorite(
        self,
        kind: FavoriteKind,
        target_value: str,
        label: str = "",
        subtitle: str = "",
    ) -> FavoriteInfo:
        value = (target_value or "").strip()
        if not value:
            raise ValidationError("Nothing to add to favourites.")
        with self._store.transaction() as session:
            existing = session.execute(
                select(Favorite).where(
                    Favorite.kind == kind.value, Favorite.target_value == value
                )
            ).scalar_one_or_none()
            row = existing or Favorite(kind=kind.value, target_value=value)
            row.label = label or row.label
            row.subtitle = subtitle or row.subtitle
            if existing is None:
                session.add(row)
            session.flush()
            return FavoriteInfo(
                id=row.id,
                kind=kind,
                target_value=row.target_value,
                label=row.label or "",
                subtitle=row.subtitle or "",
                created_at=row.created_at,
            )

    def remove_favorite(self, kind: FavoriteKind, target_value: str) -> bool:
        with self._store.transaction() as session:
            result = session.execute(
                delete(Favorite).where(
                    Favorite.kind == kind.value,
                    Favorite.target_value == (target_value or "").strip(),
                )
            )
            return bool(result.rowcount)

    def toggle_favorite(
        self, kind: FavoriteKind, target_value: str, label: str = "", subtitle: str = ""
    ) -> bool:
        """Add or remove. Returns whether it is now a favourite."""
        if self.is_favorite(kind, target_value):
            self.remove_favorite(kind, target_value)
            return False
        self.add_favorite(kind, target_value, label, subtitle)
        return True

    def is_favorite(self, kind: FavoriteKind, target_value: str) -> bool:
        with self._store.session() as session:
            return (
                session.execute(
                    select(Favorite.id).where(
                        Favorite.kind == kind.value,
                        Favorite.target_value == (target_value or "").strip(),
                    )
                ).scalar()
                is not None
            )

    def favorites(self, kind: FavoriteKind | None = None, limit: int = 200) -> list[FavoriteInfo]:
        with self._store.session() as session:
            statement = select(Favorite).order_by(Favorite.created_at.desc())
            if kind is not None:
                statement = statement.where(Favorite.kind == kind.value)
            rows = session.execute(statement.limit(limit)).scalars().all()
            return [
                FavoriteInfo(
                    id=row.id,
                    kind=FavoriteKind(row.kind),
                    target_value=row.target_value,
                    label=row.label or "",
                    subtitle=row.subtitle or "",
                    created_at=row.created_at,
                )
                for row in rows
            ]

    # -- report templates ---------------------------------------------------
    def save_template(
        self,
        name: str,
        report_type: str,
        output_format: str,
        criteria: str,
        description: str = "",
        *,
        limit: int | None = None,
    ) -> TemplateInfo:
        cleaned = " ".join((name or "").split())[:128]
        if not cleaned:
            raise ValidationError("Give the template a name.")
        if limit is not None and limit >= 0 and self.template_count() >= limit:
            raise ValidationError(
                f"Your plan includes {limit} report template(s). Remove one, or "
                "upgrade for more."
            )
        with self._store.transaction() as session:
            existing = session.execute(
                select(ReportTemplate).where(
                    func.lower(ReportTemplate.name) == cleaned.lower()
                )
            ).scalar_one_or_none()
            row = existing or ReportTemplate(name=cleaned, report_type=report_type)
            row.name = cleaned
            row.description = description or None
            row.report_type = report_type
            row.output_format = output_format
            row.criteria = criteria
            if existing is None:
                session.add(row)
            session.flush()
            return _template_info(row)

    def templates(self) -> list[TemplateInfo]:
        with self._store.session() as session:
            rows = (
                session.execute(select(ReportTemplate).order_by(ReportTemplate.name))
                .scalars()
                .all()
            )
            return [_template_info(row) for row in rows]

    def template_count(self) -> int:
        with self._store.session() as session:
            return int(
                session.execute(select(func.count()).select_from(ReportTemplate)).scalar() or 0
            )

    def delete_template(self, template_id: int) -> None:
        with self._store.transaction() as session:
            row = session.get(ReportTemplate, template_id)
            if row is not None:
                session.delete(row)

    def record_template_use(self, template_id: int) -> None:
        with self._store.transaction() as session:
            row = session.get(ReportTemplate, template_id)
            if row is not None:
                row.use_count += 1
                row.last_used_at = datetime.now(UTC)

    # -- generated reports --------------------------------------------------
    def record_report(
        self,
        title: str,
        report_type: str,
        output_format: str,
        path: str,
        *,
        row_count: int = 0,
        size_bytes: int = 0,
        database_version: str | None = None,
    ) -> None:
        with self._store.transaction() as session:
            session.add(
                GeneratedReport(
                    title=title[:256],
                    report_type=report_type,
                    output_format=output_format,
                    path=path,
                    row_count=row_count,
                    size_bytes=size_bytes,
                    database_version=database_version,
                )
            )

    def recent_reports(self, limit: int = 25) -> list[GeneratedReport]:
        with self._store.session() as session:
            rows = (
                session.execute(
                    select(GeneratedReport)
                    .order_by(GeneratedReport.created_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            for row in rows:
                session.expunge(row)
            return list(rows)

    # -- notifications ------------------------------------------------------
    def notify(
        self,
        title: str,
        body: str = "",
        *,
        kind: str = "info",
        source: str = "app",
        action: str | None = None,
    ) -> None:
        with self._store.transaction() as session:
            session.add(
                Notification(
                    title=title[:256], body=body or None, kind=kind, source=source, action=action
                )
            )

    def notifications(self, limit: int = 50, *, unread_only: bool = False) -> list[NotificationInfo]:
        with self._store.session() as session:
            statement = select(Notification).order_by(Notification.created_at.desc())
            if unread_only:
                statement = statement.where(Notification.read.is_(False))
            rows = session.execute(statement.limit(limit)).scalars().all()
            return [
                NotificationInfo(
                    id=row.id,
                    title=row.title,
                    body=row.body or "",
                    kind=row.kind,
                    source=row.source,
                    action=row.action,
                    read=row.read,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def unread_notifications(self) -> int:
        with self._store.session() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.read.is_(False))
                ).scalar()
                or 0
            )

    def mark_notifications_read(self, ids: Sequence[int] | None = None) -> int:
        with self._store.transaction() as session:
            statement = select(Notification).where(Notification.read.is_(False))
            if ids:
                statement = statement.where(Notification.id.in_(list(ids)))
            rows = session.execute(statement).scalars().all()
            for row in rows:
                row.read = True
            return len(rows)

    def clear_notifications(self) -> int:
        with self._store.transaction() as session:
            result = session.execute(delete(Notification))
            return int(result.rowcount or 0)

    # -- housekeeping -------------------------------------------------------
    def prune(self, *, keep_events_days: int = 180) -> None:
        """Drop notifications and history older than the retention window."""
        cutoff = datetime.now(UTC) - timedelta(days=max(1, keep_events_days))
        with self._store.transaction() as session:
            session.execute(delete(Notification).where(Notification.created_at < cutoff))
            session.execute(
                delete(SearchHistoryEntry).where(SearchHistoryEntry.searched_at < cutoff)
            )


def _saved_search_info(row: SavedSearch) -> SavedSearchInfo:
    criteria: AdvancedQuery | None = None
    if row.criteria:
        try:
            criteria = AdvancedQuery.model_validate(json.loads(row.criteria))
        except (json.JSONDecodeError, ValueError):
            criteria = None
    return SavedSearchInfo(
        id=row.id,
        name=row.name,
        kind=_search_kind(row.kind),
        query=row.query or "",
        criteria=criteria,
        pinned=row.pinned,
        run_count=row.run_count,
        last_run_at=row.last_run_at,
        last_result_count=row.last_result_count,
    )


def _template_info(row: ReportTemplate) -> TemplateInfo:
    return TemplateInfo(
        id=row.id,
        name=row.name,
        description=row.description or "",
        report_type=row.report_type,
        output_format=row.output_format,
        criteria=row.criteria or "",
        use_count=row.use_count,
        last_used_at=row.last_used_at,
    )


def _search_kind(value: str) -> SearchKind:
    try:
        return SearchKind(value)
    except ValueError:  # pragma: no cover - defensive
        return SearchKind.BIN
