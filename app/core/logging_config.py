"""Structured logging.

Two sinks: a rotating human-readable file in the application-data directory,
and a terse console stream. A redaction filter guarantees that anything
resembling a payment card number, CVV, PIN or secret never reaches either sink
— Bin-Tel handles issuer metadata only, and the log must stay that way even if
an unexpected string is passed to a logging call.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.constants import APP_NAME, APP_VERSION
from app.core.paths import AppPaths, get_paths

_CONFIGURED = False

#: 13-19 digit runs (optionally spaced/dashed) — a possible PAN.
_PAN_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
#: Obvious secret-bearing key/value pairs.
_SECRET_RE = re.compile(
    r"(?i)\b(cvv|cvc|cvv2|pin|password|passwd|secret|token|api[_-]?key)"
    r"\s*[:=]\s*\S+"
)
#: An authorization value is taken to the end of the line, because the token
#: usually follows a scheme word ("Bearer abc…") and stopping at the first
#: space would leave the credential itself in the log.
_AUTH_RE = re.compile(r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*.+")
#: ``track1=...`` / ``track2=...`` magnetic-stripe payloads.
_TRACK_RE = re.compile(r"(?i)\btrack\s*[12]\s*[:=]\s*\S+")


def redact(text: str) -> str:
    """Remove anything that could be cardholder or credential data."""
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    text = _AUTH_RE.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    text = _TRACK_RE.sub("track=[redacted]", text)
    return _PAN_RE.sub(_redact_digits, text)


def _redact_digits(match: re.Match[str]) -> str:
    digits = re.sub(r"[ -]", "", match.group(0))
    # A BIN/IIN is 6-8 digits and is the whole point of this application, so
    # only longer runs — which could be a full account number — are masked.
    if len(digits) < 13:
        return match.group(0)
    return f"[redacted:{len(digits)}-digits]"


class RedactionFilter(logging.Filter):
    """Applies :func:`redact` to the rendered message and its arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: redact(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact(value) if isinstance(value, str) else value for value in record.args
                )
        return True


class ContextFormatter(logging.Formatter):
    """Human-readable line format with optional structured ``extra`` context."""

    default_time_format = "%Y-%m-%d %H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        context: dict[str, Any] = getattr(record, "context", {}) or {}
        if context:
            try:
                base = f"{base} | {json.dumps(context, default=str, sort_keys=True)}"
            except (TypeError, ValueError):  # pragma: no cover - defensive
                base = f"{base} | {context!r}"
        return redact(base)


def setup_logging(
    level: str = "INFO",
    paths: AppPaths | None = None,
    *,
    retention_days: int = 14,
    console: bool = True,
) -> Path:
    """Configure the root logger. Returns the active log-file path."""
    global _CONFIGURED
    paths = paths or get_paths()
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = paths.log_file

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    numeric = getattr(logging, str(level).upper(), logging.INFO)
    redaction = RedactionFilter()

    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=max(1, retention_days), encoding="utf-8"
    )
    file_handler.setLevel(numeric)
    file_handler.setFormatter(
        ContextFormatter("%(asctime)s %(levelname)-8s %(name)-32s %(message)s")
    )
    file_handler.addFilter(redaction)
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(max(numeric, logging.INFO))
        stream.setFormatter(ContextFormatter("%(levelname)-8s %(name)-24s %(message)s"))
        stream.addFilter(redaction)
        root.addHandler(stream)

    # Third-party libraries are noisy at DEBUG; keep them at WARNING.
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "PIL", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    logging.getLogger(__name__).info(
        "Logging initialised",
        extra={
            "context": {
                "app": APP_NAME,
                "version": APP_VERSION,
                "level": logging.getLevelName(numeric),
                "log_file": str(log_file),
                "started": datetime.now(UTC).isoformat(),
            }
        },
    )
    return log_file


def set_level(level: str) -> None:
    """Change the active verbosity without rebuilding the handlers."""
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.setLevel(max(numeric, logging.INFO))
        else:
            handler.setLevel(numeric)


def get_logger(name: str) -> logging.Logger:
    """Module-level logger helper (keeps the ``app.`` prefix consistent)."""
    if not _CONFIGURED:
        # Never let an un-configured logger emit "No handlers" warnings.
        logging.getLogger().addHandler(logging.NullHandler())
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, **context: Any) -> None:
    """Emit a structured lifecycle event (startup, update, import, ...)."""
    logger.info(event, extra={"context": context})
