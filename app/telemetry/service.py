"""Opt-in, aggregated product telemetry.

Three rules govern this module:

* **Nothing is collected unless the user turns it on.** The default is off,
  and turning it off also clears whatever is queued.
* **Only the vocabulary in** :mod:`app.telemetry.events` **can be sent.** An
  unknown event, an unlisted key or a forbidden key is dropped by the
  sanitiser, so a careless call site cannot leak a value.
* **Telemetry never affects the application.** Queueing, uploading and failing
  to upload are all invisible to the user; if the endpoint is unreachable the
  events simply age out of the queue.
"""

from __future__ import annotations

import json
import platform
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, func, select

from app.core.constants import APP_VERSION, USER_AGENT
from app.core.logging_config import get_logger
from app.database.user_store import UserDataStore
from app.models.user_entities import TelemetryCounter, TelemetryEvent
from app.telemetry.events import (
    ALLOWED_KEYS,
    ALLOWED_TYPES,
    FORBIDDEN_KEYS,
    MAX_STRING_LENGTH,
    Counter,
    Event,
)

logger = get_logger(__name__)

#: Events older than this are discarded rather than retried forever.
MAX_EVENT_AGE = timedelta(days=30)
#: Hard cap on the queue, so a permanently offline install cannot grow a file.
MAX_QUEUED_EVENTS = 500
#: How many events go in one upload.
BATCH_SIZE = 50
UPLOAD_TIMEOUT = 10.0


def sanitise(event: Event, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Reduce a payload to what this event is allowed to carry.

    Returns a new mapping containing only allow-listed keys with allow-listed
    value types. Anything else — including anything in
    :data:`~app.telemetry.events.FORBIDDEN_KEYS` — is dropped silently, because
    the caller must not be able to force a value through.
    """
    if not payload:
        return {}
    allowed = ALLOWED_KEYS.get(event, frozenset())
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        name = str(key).strip().lower()
        if name in FORBIDDEN_KEYS or name not in allowed:
            continue
        if not isinstance(value, ALLOWED_TYPES) or isinstance(value, bytes):
            continue
        if isinstance(value, str):
            text = value.strip()
            if not text or len(text) > MAX_STRING_LENGTH:
                continue
            # A value that looks like a number long enough to be an account is
            # refused outright, belt and braces.
            if text.replace(" ", "").replace("-", "").isdigit() and len(text) >= 12:
                continue
            clean[name] = text
        else:
            clean[name] = value
    return clean


class TelemetryService:
    """Queues, aggregates and (when enabled) uploads product events."""

    def __init__(
        self,
        store: UserDataStore,
        *,
        endpoint: str = "",
        enabled: bool = False,
        install_id_provider: Any = None,
    ) -> None:
        self._store = store
        self._endpoint = endpoint
        self._enabled = enabled
        self._install_id_provider = install_id_provider
        self._context: dict[str, str] = {
            "app_version": APP_VERSION,
            "platform": f"{platform.system()} {platform.machine()}",
        }
        self._database_version: str | None = None
        self._plan: str = "free"

    # -- configuration ----------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Turn telemetry on or off. Turning it off clears the queue."""
        changed = enabled != self._enabled
        self._enabled = enabled
        if changed:
            logger.info(
                "Telemetry preference changed",
                extra={"context": {"enabled": enabled}},
            )
        if not enabled:
            self.clear_queue()

    def set_endpoint(self, endpoint: str) -> None:
        self._endpoint = endpoint

    def set_context(self, *, database_version: str | None = None, plan: str | None = None) -> None:
        """Record the non-identifying context attached to every event."""
        if database_version is not None:
            self._database_version = database_version
        if plan is not None:
            self._plan = plan

    @property
    def install_id(self) -> str:
        """A random per-installation id, used only to deduplicate uploads."""
        if self._install_id_provider is not None:
            return str(self._install_id_provider())
        return self._store.install_id

    # -- recording --------------------------------------------------------
    def record(self, event: Event | str, payload: Mapping[str, Any] | None = None) -> bool:
        """Queue an event. Returns ``False`` when nothing was recorded."""
        if not self._enabled:
            return False
        try:
            resolved = Event(str(event))
        except ValueError:
            logger.debug("Ignoring an unknown telemetry event: %s", event)
            return False

        clean = sanitise(resolved, payload)
        try:
            with self._store.transaction() as session:
                session.add(
                    TelemetryEvent(
                        name=resolved.value,
                        payload=json.dumps(clean, sort_keys=True) if clean else None,
                        app_version=self._context["app_version"],
                        database_version=self._database_version,
                        plan=self._plan,
                        platform=self._context["platform"],
                    )
                )
            self._trim()
        except Exception:  # noqa: BLE001 - telemetry must never break the app
            logger.debug("Could not queue a telemetry event", exc_info=True)
            return False
        return True

    def increment(self, counter: Counter | str, amount: int = 1) -> None:
        """Bump an aggregated counter.

        Counters are kept even when telemetry is disabled, because they also
        drive the local usage summary the user can see on the Privacy page.
        They are only ever *uploaded* when telemetry is enabled.
        """
        try:
            name = Counter(str(counter)).value
        except ValueError:
            return
        period = datetime.now(UTC).strftime("%Y-%m-%d")
        try:
            with self._store.transaction() as session:
                row = session.execute(
                    select(TelemetryCounter).where(
                        TelemetryCounter.name == name, TelemetryCounter.period == period
                    )
                ).scalar_one_or_none()
                if row is None:
                    session.add(TelemetryCounter(name=name, period=period, value=max(0, amount)))
                else:
                    row.value += max(0, amount)
        except Exception:  # noqa: BLE001 - never break the caller
            logger.debug("Could not update a telemetry counter", exc_info=True)

    # -- queue ------------------------------------------------------------
    def queue_size(self) -> int:
        with self._store.session() as session:
            return int(
                session.execute(select(func.count()).select_from(TelemetryEvent)).scalar() or 0
            )

    def queued_events(self, limit: int = 100) -> list[TelemetryEvent]:
        """The queue, so the user can see exactly what would be sent."""
        with self._store.session() as session:
            rows = (
                session.execute(
                    select(TelemetryEvent)
                    .order_by(TelemetryEvent.created_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            for row in rows:
                session.expunge(row)
            return list(rows)

    def counters(self, days: int = 30) -> dict[str, int]:
        """Aggregated counters over the last *days*, summed per counter."""
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._store.session() as session:
            rows = session.execute(
                select(TelemetryCounter.name, func.sum(TelemetryCounter.value))
                .where(TelemetryCounter.period >= since)
                .group_by(TelemetryCounter.name)
            ).all()
        return {str(name): int(total or 0) for name, total in rows}

    def clear_queue(self) -> int:
        """Delete every queued event. Counters are kept unless asked."""
        try:
            with self._store.transaction() as session:
                result = session.execute(delete(TelemetryEvent))
            removed = int(result.rowcount or 0)
        except Exception:  # noqa: BLE001
            logger.debug("Could not clear the telemetry queue", exc_info=True)
            return 0
        if removed:
            logger.info("Telemetry queue cleared", extra={"context": {"events": removed}})
        return removed

    def clear_all(self) -> int:
        """Delete queued events *and* the local counters."""
        removed = self.clear_queue()
        try:
            with self._store.transaction() as session:
                result = session.execute(delete(TelemetryCounter))
            removed += int(result.rowcount or 0)
        except Exception:  # noqa: BLE001
            logger.debug("Could not clear telemetry counters", exc_info=True)
        return removed

    def _trim(self) -> None:
        """Drop events that are too old or beyond the queue cap."""
        cutoff = datetime.now(UTC) - MAX_EVENT_AGE
        try:
            with self._store.transaction() as session:
                session.execute(delete(TelemetryEvent).where(TelemetryEvent.created_at < cutoff))
                total = int(
                    session.execute(select(func.count()).select_from(TelemetryEvent)).scalar() or 0
                )
                if total > MAX_QUEUED_EVENTS:
                    keep_ids = select(TelemetryEvent.id).order_by(
                        TelemetryEvent.created_at.desc()
                    ).limit(MAX_QUEUED_EVENTS)
                    session.execute(
                        delete(TelemetryEvent).where(TelemetryEvent.id.notin_(keep_ids))
                    )
        except Exception:  # noqa: BLE001
            logger.debug("Could not trim the telemetry queue", exc_info=True)

    # -- upload -----------------------------------------------------------
    def flush(self, *, limit: int = BATCH_SIZE) -> int:
        """Upload a batch. Returns how many events were accepted.

        A failure is not an error condition: the events stay queued and the
        application carries on exactly as before.
        """
        if not self._enabled or not self._endpoint:
            return 0
        with self._store.session() as session:
            rows = (
                session.execute(
                    select(TelemetryEvent).order_by(TelemetryEvent.created_at).limit(limit)
                )
                .scalars()
                .all()
            )
            batch = [self._as_payload(row) for row in rows]
            identifiers = [row.id for row in rows]
        if not batch:
            return 0

        body = {
            "install_id": self.install_id,
            "app_version": APP_VERSION,
            "sent_at": datetime.now(UTC).isoformat(),
            "events": batch,
            "counters": self.counters(days=1),
        }
        try:
            with httpx.Client(
                timeout=UPLOAD_TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            ) as client:
                response = client.post(self._endpoint, json=body)
            if response.status_code >= 400:
                logger.debug("Telemetry upload rejected with HTTP %s", response.status_code)
                self._mark_attempted(identifiers)
                return 0
        except httpx.HTTPError as exc:
            logger.debug("Telemetry upload failed: %s", type(exc).__name__)
            self._mark_attempted(identifiers)
            return 0

        with self._store.transaction() as session:
            session.execute(delete(TelemetryEvent).where(TelemetryEvent.id.in_(identifiers)))
        logger.info("Telemetry uploaded", extra={"context": {"events": len(batch)}})
        return len(batch)

    def _mark_attempted(self, identifiers: list[int]) -> None:
        if not identifiers:
            return
        try:
            with self._store.transaction() as session:
                for row in (
                    session.execute(
                        select(TelemetryEvent).where(TelemetryEvent.id.in_(identifiers))
                    )
                    .scalars()
                    .all()
                ):
                    row.attempts += 1
                    row.last_attempt_at = datetime.now(UTC)
        except Exception:  # noqa: BLE001
            logger.debug("Could not record a telemetry upload attempt", exc_info=True)

    @staticmethod
    def _as_payload(row: TelemetryEvent) -> dict[str, Any]:
        try:
            payload = json.loads(row.payload) if row.payload else {}
        except json.JSONDecodeError:  # pragma: no cover - defensive
            payload = {}
        return {
            "name": row.name,
            "at": row.created_at.isoformat() if row.created_at else None,
            "app_version": row.app_version,
            "database_version": row.database_version,
            "plan": row.plan,
            "platform": row.platform,
            "properties": payload,
        }

    # -- transparency ------------------------------------------------------
    def describe_collection(self) -> list[str]:
        """Plain-language list of what telemetry would send, for the UI."""
        return [
            "Which version of Bin-Tel and which database version you are running.",
            "Your operating system family and processor architecture.",
            "Which plan you are on, and whether a licence is active.",
            "Aggregated counts of how often features are used — never what you searched for.",
            "Whether database downloads, updates and verifications succeeded or failed.",
            "The type of any error that occurred, without its message or context.",
            "A random installation identifier that is not linked to you.",
        ]

    def describe_exclusions(self) -> list[str]:
        return [
            "Never any BIN, IIN, institution name, or search term.",
            "Never any card number, security code, PIN or cardholder information.",
            "Never your name, email address, licence key, file paths or hostname.",
            "Never the contents of your database, watchlists or reports.",
        ]
