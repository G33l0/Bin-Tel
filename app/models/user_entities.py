"""User-owned data: watchlists, saved searches, favorites, templates, telemetry.

This schema deliberately lives in a **separate SQLite file** from the
intelligence database. The intelligence database is replaced wholesale every
time a new package is installed, so anything the user created — a watchlist, a
saved search, a report template, a queued telemetry event — would be destroyed
by the next update if it were stored there.

References into the intelligence database are therefore stored by *value*
(the BIN digits, the institution's stable ``uid``) rather than by foreign key,
because the row ids on the other side are not stable across releases.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UserBase(DeclarativeBase):
    """Declarative base for the user-data database."""


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------


class WatchTargetType(StrEnum):
    BIN = "bin"
    INSTITUTION = "institution"
    COUNTRY = "country"
    SAVED_SEARCH = "saved_search"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class ChangeType(StrEnum):
    """What a database update did to a watched record."""

    BIN_ADDED = "bin_added"
    BIN_REMOVED = "bin_removed"
    INSTITUTION_CHANGED = "institution_changed"
    LOCATION_CHANGED = "location_changed"
    NETWORK_CHANGED = "network_changed"
    CARD_TYPE_CHANGED = "card_type_changed"
    FUNDING_TYPE_CHANGED = "funding_type_changed"
    STATUS_CHANGED = "status_changed"
    COUNTRY_CHANGED = "country_changed"
    INSTITUTION_ADDED = "institution_added"
    INSTITUTION_REMOVED = "institution_removed"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()

    @property
    def severity(self) -> str:
        if self in (ChangeType.BIN_REMOVED, ChangeType.INSTITUTION_REMOVED):
            return "warning"
        if self in (ChangeType.BIN_ADDED, ChangeType.INSTITUTION_ADDED):
            return "info"
        return "info"


class SearchKind(StrEnum):
    BIN = "bin"
    INSTITUTION = "institution"
    ADVANCED = "advanced"
    COUNTRY = "country"

    @property
    def label(self) -> str:
        return {
            SearchKind.BIN: "BIN",
            SearchKind.INSTITUTION: "Institution",
            SearchKind.ADVANCED: "Advanced",
            SearchKind.COUNTRY: "Country",
        }[self]


class FavoriteKind(StrEnum):
    BIN = "bin"
    INSTITUTION = "institution"


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------


class Watchlist(UserBase):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    colour: Mapped[str | None] = mapped_column(String(9))
    notify: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    items: Mapped[list[WatchlistItem]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan"
    )
    events: Mapped[list[WatchlistEvent]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Watchlist {self.name!r}>"


class WatchlistItem(UserBase):
    """One watched target.

    ``target_value`` holds the BIN digits, the institution ``uid`` or the ISO
    country code — never a row id from the intelligence database, whose ids do
    not survive a package replacement.
    """

    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_value: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(String(256))
    #: Snapshot of the watched record's comparable fields, as JSON. Change
    #: detection diffs the new database against this.
    snapshot: Mapped[str | None] = mapped_column(Text)
    snapshot_version: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")
    events: Mapped[list[WatchlistEvent]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "watchlist_id", "target_type", "target_value", name="uq_watchlist_target"
        ),
        Index("ix_watchlist_items_target", "target_type", "target_value"),
    )


class WatchlistEvent(UserBase):
    """A change detected on a watched target after a database update."""

    __tablename__ = "watchlist_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("watchlist_items.id", ondelete="CASCADE")
    )
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_value: Mapped[str] = mapped_column(String(128), nullable=False)
    field: Mapped[str | None] = mapped_column(String(64))
    previous_value: Mapped[str | None] = mapped_column(Text)
    current_value: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    from_version: Mapped[str | None] = mapped_column(String(32))
    to_version: Mapped[str | None] = mapped_column(String(32))
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)

    watchlist: Mapped[Watchlist] = relationship(back_populates="events")
    item: Mapped[WatchlistItem | None] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_watchlist_events_detected", "detected_at"),
        Index("ix_watchlist_events_unread", "acknowledged", "detected_at"),
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SavedSearch(UserBase):
    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(24), default=SearchKind.BIN.value)
    query: Mapped[str | None] = mapped_column(Text)
    #: Serialised :class:`~app.models.schemas.AdvancedQuery`.
    criteria: Mapped[str | None] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_result_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_saved_searches_pinned", "pinned", "name"),)


class SearchHistoryEntry(UserBase):
    """Recent searches. Stored locally and never transmitted anywhere."""

    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), default=SearchKind.BIN.value)
    query: Mapped[str] = mapped_column(String(256), nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[float] = mapped_column(Float, default=0.0)
    searched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_search_history_recent", "searched_at"),
        Index("ix_search_history_query", "kind", "query"),
    )


class Favorite(UserBase):
    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    target_value: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(String(256))
    subtitle: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("kind", "target_value", name="uq_favorite_target"),
        Index("ix_favorites_kind", "kind", "created_at"),
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class ReportTemplate(UserBase):
    __tablename__ = "report_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    output_format: Mapped[str] = mapped_column(String(8), default="pdf")
    #: Serialised :class:`~app.services.report_service.ReportRequest`.
    criteria: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    use_count: Mapped[int] = mapped_column(Integer, default=0)


class GeneratedReport(UserBase):
    """A record of reports produced, so the Report Center can list them."""

    __tablename__ = "generated_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    output_format: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    database_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_generated_reports_created", "created_at"),)


# ---------------------------------------------------------------------------
# Telemetry and notifications
# ---------------------------------------------------------------------------


class TelemetryEvent(UserBase):
    """A queued, aggregated product event awaiting upload.

    Nothing here identifies a person or reveals what was searched for: the
    payload is validated against an allow-list before it is ever queued.
    """

    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text)
    app_version: Mapped[str | None] = mapped_column(String(32))
    database_version: Mapped[str | None] = mapped_column(String(32))
    plan: Mapped[str | None] = mapped_column(String(24))
    platform: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_telemetry_events_created", "created_at"),)


class TelemetryCounter(UserBase):
    """Aggregated counters — how often a feature was used, never with what."""

    __tablename__ = "telemetry_counters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    value: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("name", "period", name="uq_counter_period"),
        Index("ix_telemetry_counters_name", "name", "period"),
    )


class Notification(UserBase):
    """In-app notification, shown in the notification centre."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24), default="info")
    source: Mapped[str] = mapped_column(String(32), default="app")
    action: Mapped[str | None] = mapped_column(String(64))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_notifications_unread", "read", "created_at"),)


# ---------------------------------------------------------------------------
# Licensing (local cache of a server-signed state)
# ---------------------------------------------------------------------------


class LicenseRecord(UserBase):
    """The last server-signed license state, cached for offline use.

    The signed token is the authority; these columns exist so the application
    can display and query the state without re-parsing the token every time.
    """

    __tablename__ = "license_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    license_key: Mapped[str | None] = mapped_column(String(128))
    license_id: Mapped[str | None] = mapped_column(String(64))
    plan: Mapped[str] = mapped_column(String(24), default="free")
    status: Mapped[str] = mapped_column(String(24), default="free")
    #: The server-signed token; verified against the embedded public key.
    token: Mapped[str | None] = mapped_column(Text)
    device_id: Mapped[str | None] = mapped_column(String(64))
    device_limit: Mapped[int] = mapped_column(Integer, default=1)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    grace_until: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UserMetadata(UserBase):
    """Key/value store for the user database's own schema version and state."""

    __tablename__ = "user_metadata"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    SCHEMA_VERSION = "schema_version"
    CREATED_AT = "created_at"
    INSTALL_ID = "install_id"
    LAST_CHANGE_SCAN = "last_change_scan"
