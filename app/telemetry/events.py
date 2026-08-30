"""The telemetry vocabulary.

This module is the contract: an event that is not named here is never queued,
and a payload key that is not listed for that event is dropped. That makes the
privacy claim checkable — you can read this file and see everything Bin-Tel is
capable of sending.

Notably absent, by design: BINs, institution names, search terms, file paths,
hostnames, licence keys, and anything else that could identify a person or
reveal what they looked up.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Event(StrEnum):
    """Every product event Bin-Tel may report."""

    APP_STARTED = "app_started"
    APP_CLOSED = "app_closed"
    DATABASE_INITIALIZED = "database_initialized"
    DATABASE_UPDATED = "database_updated"
    DATABASE_UPDATE_FAILED = "database_update_failed"
    DATABASE_VERIFIED = "database_verified"
    DATABASE_BACKUP_CREATED = "database_backup_created"
    DATABASE_RESTORED = "database_restored"
    FEATURE_USED = "feature_used"
    FEATURE_BLOCKED = "feature_blocked"
    UPGRADE_PAGE_OPENED = "upgrade_page_opened"
    LICENSE_ACTIVATED = "license_activated"
    LICENSE_DEACTIVATED = "license_deactivated"
    SUBSCRIPTION_STATE_CHANGED = "subscription_state_changed"
    REPORT_GENERATED = "report_generated"
    EXPORT_COMPLETED = "export_completed"
    WATCHLIST_CREATED = "watchlist_created"
    WATCHLIST_ALERT_RAISED = "watchlist_alert_raised"
    ERROR_OCCURRED = "error_occurred"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


#: Aggregated counters. These are incremented locally and reported as totals —
#: the individual lookups behind them are never recorded.
class Counter(StrEnum):
    BIN_LOOKUP_COUNT = "bin_lookup_count"
    INSTITUTION_LOOKUP_COUNT = "institution_lookup_count"
    ADVANCED_SEARCH_COUNT = "advanced_search_count"
    BATCH_LOOKUP_COUNT = "batch_lookup_count"
    EXPORT_COUNT = "export_count"
    REPORT_COUNT = "report_count"
    WATCHLIST_EVENT_COUNT = "watchlist_event_count"
    SESSION_COUNT = "session_count"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").replace(" count", "").title()


#: Payload keys each event may carry. Anything else is dropped by the
#: sanitiser, so a careless call site cannot leak a value.
ALLOWED_KEYS: Final[dict[Event, frozenset[str]]] = {
    Event.APP_STARTED: frozenset({"first_run", "startup_ms", "theme", "portable"}),
    Event.APP_CLOSED: frozenset({"session_seconds", "pages_visited"}),
    Event.DATABASE_INITIALIZED: frozenset(
        {"database_version", "record_count", "duration_ms", "compression", "edition"}
    ),
    Event.DATABASE_UPDATED: frozenset(
        {
            "from_version",
            "to_version",
            "duration_ms",
            "bytes_downloaded",
            "compression",
            "migrated",
            "used_delta",
        }
    ),
    Event.DATABASE_UPDATE_FAILED: frozenset({"to_version", "stage", "error_type"}),
    Event.DATABASE_VERIFIED: frozenset({"ok", "quick", "duration_ms", "health_score"}),
    Event.DATABASE_BACKUP_CREATED: frozenset({"size_bucket"}),
    Event.DATABASE_RESTORED: frozenset({"ok"}),
    Event.FEATURE_USED: frozenset({"feature", "surface"}),
    Event.FEATURE_BLOCKED: frozenset({"feature", "required_plan"}),
    Event.UPGRADE_PAGE_OPENED: frozenset({"source", "feature"}),
    Event.LICENSE_ACTIVATED: frozenset({"plan", "edition"}),
    Event.LICENSE_DEACTIVATED: frozenset({"plan"}),
    Event.SUBSCRIPTION_STATE_CHANGED: frozenset({"from_state", "to_state", "plan"}),
    Event.REPORT_GENERATED: frozenset({"report_type", "format", "row_bucket"}),
    Event.EXPORT_COMPLETED: frozenset({"format", "row_bucket", "surface"}),
    Event.WATCHLIST_CREATED: frozenset({"item_count_bucket"}),
    Event.WATCHLIST_ALERT_RAISED: frozenset({"change_type", "count_bucket"}),
    Event.ERROR_OCCURRED: frozenset({"error_type", "surface", "recoverable"}),
}

#: Keys that must never appear in a payload under any circumstances. The
#: sanitiser drops them even if an event's allow-list were edited to permit
#: them, so this is a hard floor rather than a convention.
FORBIDDEN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "bin",
        "iin",
        "pan",
        "card_number",
        "cardnumber",
        "cvv",
        "cvc",
        "pin",
        "track1",
        "track2",
        "cardholder",
        "cardholder_name",
        "account",
        "account_number",
        "iban",
        "password",
        "passphrase",
        "secret",
        "token",
        "api_key",
        "license_key",
        "email",
        "name",
        "username",
        "user",
        "query",
        "search",
        "search_term",
        "institution",
        "institution_name",
        "issuer",
        "bank",
        "path",
        "file",
        "filename",
        "directory",
        "hostname",
        "host",
        "ip",
        "ip_address",
        "mac",
        "serial",
        "address",
        "city",
        "postal_code",
        "latitude",
        "longitude",
    }
)

#: Value types a payload may hold. Anything else is dropped rather than
#: coerced, because coercion is how strings sneak in.
ALLOWED_TYPES: Final[tuple[type, ...]] = (bool, int, float, str)

#: Free-text values are capped and must match one of the enumerations above,
#: so an unexpected string can never carry a search term.
MAX_STRING_LENGTH: Final[int] = 48


def bucket(value: int | None) -> str:
    """Coarsen a count into a bucket, so exact volumes are never reported."""
    if value is None or value < 0:
        return "unknown"
    for threshold, label in (
        (1, "0"),
        (10, "1-9"),
        (100, "10-99"),
        (1_000, "100-999"),
        (10_000, "1k-9k"),
        (100_000, "10k-99k"),
        (1_000_000, "100k-999k"),
    ):
        if value < threshold:
            return label
    return "1m+"


def size_bucket(num_bytes: int | None) -> str:
    """Coarsen a byte count the same way."""
    if num_bytes is None or num_bytes < 0:
        return "unknown"
    megabytes = num_bytes / (1024 * 1024)
    for threshold, label in ((1, "<1MB"), (10, "1-10MB"), (100, "10-100MB"), (1024, "100MB-1GB")):
        if megabytes < threshold:
            return label
    return ">1GB"
