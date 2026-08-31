"""Presentation helpers.

Everything the user reads passes through here, which keeps ``Unknown`` (rather
than an empty cell, ``None`` or a guess) as the single answer for missing data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.constants import UNKNOWN_DISPLAY

_EMPTY_TOKENS = {"", "-", "--", "n/a", "na", "null", "none", "unknown", "?"}


def is_empty(value: Any) -> bool:
    """Whether a value should be rendered as ``Unknown``."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _EMPTY_TOKENS
    return False


def display(value: Any, fallback: str = UNKNOWN_DISPLAY) -> str:
    """Render *value* for the UI, never inventing information."""
    if is_empty(value):
        return fallback
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value.strip()
    return str(value)


def display_optional_bool(value: bool | None) -> str:
    """Tri-state boolean: ``Yes`` / ``No`` / ``Unknown``."""
    if value is None:
        return UNKNOWN_DISPLAY
    return "Yes" if value else "No"


def format_number(value: int | float | None) -> str:
    """Thousands-separated integer, or ``Unknown``."""
    if value is None:
        return UNKNOWN_DISPLAY
    return f"{int(value):,}"


def format_bytes(num_bytes: float | None) -> str:
    if num_bytes is None or num_bytes < 0:
        return UNKNOWN_DISPLAY
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_speed(bytes_per_second: float | None) -> str:
    if not bytes_per_second or bytes_per_second <= 0:
        return "—"
    return f"{format_bytes(bytes_per_second)}/s"


def format_duration(seconds: float | None) -> str:
    """Compact remaining-time string: ``45s``, ``3m 20s``, ``1h 05m``."""
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    hours, remainder = divmod(seconds, 3600)
    return f"{hours}h {remainder // 60:02d}m"


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def format_datetime(value: datetime | None, *, with_time: bool = True) -> str:
    if value is None:
        return UNKNOWN_DISPLAY
    local = _as_aware(value).astimezone()
    return local.strftime("%d %b %Y, %H:%M" if with_time else "%d %b %Y")


def format_relative(value: datetime | None) -> str:
    """``just now`` / ``3 hours ago`` / ``in 2 days`` — never a bare timestamp."""
    if value is None:
        return "Never"
    now = datetime.now(UTC)
    delta = now - _as_aware(value)
    future = delta < timedelta(0)
    seconds = abs(delta.total_seconds())

    if seconds < 45:
        return "in a moment" if future else "just now"
    units = (
        (86400 * 365, "year"),
        (86400 * 30, "month"),
        (86400 * 7, "week"),
        (86400, "day"),
        (3600, "hour"),
        (60, "minute"),
    )
    for size, name in units:
        if seconds >= size:
            count = int(seconds // size)
            plural = "" if count == 1 else "s"
            return f"in {count} {name}{plural}" if future else f"{count} {name}{plural} ago"
    return "in a moment" if future else "just now"


def format_datetime_with_relative(value: datetime | None) -> str:
    if value is None:
        return "Never"
    return f"{format_datetime(value)} ({format_relative(value)})"


def format_bin(value: str | None) -> str:
    """Render a BIN for display.

    Six digits and under read best ungrouped; longer prefixes are split after
    the first four so an 8-digit IIN scans as ``4147 2012``.
    """
    if is_empty(value):
        return UNKNOWN_DISPLAY
    digits = str(value).strip()
    if len(digits) <= 6:
        return digits
    return f"{digits[:4]} {digits[4:]}" if len(digits) <= 8 else " ".join(
        digits[index : index + 4] for index in range(0, len(digits), 4)
    )


def format_location(
    city: str | None,
    region: str | None,
    postal_code: str | None,
    country: str | None,
) -> str:
    """Compose a postal-style location block, omitting missing components."""
    line = ", ".join(part.strip() for part in (city, region) if not is_empty(part))
    if not is_empty(postal_code):
        line = f"{line} {str(postal_code).strip()}".strip()
    parts = [part for part in (line, display(country, "") if not is_empty(country) else "") if part]
    return "\n".join(parts) if parts else UNKNOWN_DISPLAY


def truncate(value: str, limit: int = 60) -> str:
    text = (value or "").strip()
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def pluralise(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count:,} {singular if count == 1 else (plural or singular + 's')}"


def title_case(value: str | None) -> str:
    """Title-case that preserves acronyms and common banking suffixes."""
    if is_empty(value):
        return UNKNOWN_DISPLAY
    keep_upper = {"NA", "N.A.", "PLC", "LLC", "SA", "AG", "BV", "NV", "USA", "UK", "ATM", "PSP"}
    words = []
    for word in str(value).split():
        stripped = word.strip(",.").upper()
        words.append(word.upper() if stripped in keep_upper else word.capitalize())
    return " ".join(words)
