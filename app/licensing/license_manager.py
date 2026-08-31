"""The licence state machine.

Responsibilities, and nothing beyond them: hold the current licence, decide
which :class:`~app.licensing.models.LicenseState` it implies, persist it, and
re-validate it when the offline grace window is running out.

Two rules shape the design:

* a licence is only ever believed when its signature verifies, so editing the
  stored row or the token cannot promote a plan;
* losing the network never breaks the application. Lookups are local and stay
  local; a paid licence continues to apply throughout its grace window, and
  when that expires the application falls back to Free rather than locking up.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.errors import NetworkError, OfflineError
from app.core.logging_config import get_logger, log_event
from app.database.user_store import UserDataStore
from app.licensing.activation import ActivationRejected, ActivationService, LicenseError
from app.licensing.devices import DeviceManager
from app.licensing.models import (
    DeviceRecord,
    LicenseSnapshot,
    LicenseState,
    LicenseToken,
    payload_to_snapshot,
    redact_key,
)
from app.licensing.plans import Plan
from app.models.user_entities import LicenseRecord

logger = get_logger(__name__)


class LicenseManager:
    """Owns the licence, its persistence and its state transitions."""

    def __init__(
        self,
        store: UserDataStore,
        activation: ActivationService,
        devices: DeviceManager,
        *,
        on_change: Callable[[LicenseSnapshot], None] | None = None,
    ) -> None:
        self._store = store
        self._activation = activation
        self._devices = devices
        self._on_change = on_change
        self._snapshot: LicenseSnapshot = LicenseSnapshot.free()
        self._loaded = False

    # -- state ------------------------------------------------------------
    @property
    def snapshot(self) -> LicenseSnapshot:
        if not self._loaded:
            self.load()
        return self._snapshot

    @property
    def state(self) -> LicenseState:
        return self.snapshot.state

    @property
    def plan(self) -> Plan:
        """The plan whose entitlements currently apply.

        An expired or suspended licence falls back to Free — the application
        stays completely usable, it simply stops offering paid features.
        """
        snapshot = self.snapshot
        return snapshot.plan if snapshot.state.is_entitled else Plan.FREE

    def set_change_listener(self, listener: Callable[[LicenseSnapshot], None] | None) -> None:
        self._on_change = listener

    # -- persistence ------------------------------------------------------
    def load(self) -> LicenseSnapshot:
        """Read the stored licence and re-derive its state."""
        self._loaded = True
        record = self._read_record()
        if record is None or not record.token:
            self._snapshot = LicenseSnapshot.free()
            return self._snapshot

        token = LicenseToken.parse(record.token, self._activation.verifying_key)
        if token is None:
            # Either the token was altered, or it was issued by a different
            # service than the one now configured. Either way it is not trusted.
            logger.warning("The stored licence could not be verified; falling back to Free")
            self._snapshot = LicenseSnapshot(
                state=LicenseState.INVALID,
                plan=Plan.FREE,
                license_key=record.license_key or "",
                message=(
                    "Your stored licence could not be verified, so Bin-Tel is running "
                    "with the Free plan. Activate again to restore your plan."
                ),
            )
            return self._snapshot

        self._snapshot = self._derive(token, record.last_validated_at, record.license_key or "")
        return self._snapshot

    def _read_record(self) -> LicenseRecord | None:
        with self._store.session() as session:
            record = session.execute(
                select(LicenseRecord).order_by(LicenseRecord.id.desc()).limit(1)
            ).scalar_one_or_none()
            if record is not None:
                session.expunge(record)
            return record

    def _write(self, token: LicenseToken, license_key: str, validated_at: datetime) -> None:
        snapshot = self._derive(token, validated_at, license_key)
        with self._store.transaction() as session:
            record = session.execute(
                select(LicenseRecord).order_by(LicenseRecord.id.desc()).limit(1)
            ).scalar_one_or_none()
            if record is None:
                record = LicenseRecord()
                session.add(record)
            record.license_key = license_key or record.license_key
            record.license_id = token.payload.license_id
            record.plan = token.payload.plan
            record.status = snapshot.state.value
            record.token = token.raw
            record.device_id = token.payload.device_id
            record.device_limit = token.payload.device_limit
            record.issued_at = token.payload.issued_at
            record.expires_at = token.payload.expires_at
            record.last_validated_at = validated_at
            record.grace_until = snapshot.grace_until
        self._snapshot = snapshot
        self._loaded = True
        self._notify()

    def _clear(self) -> None:
        from sqlalchemy import delete

        with self._store.transaction() as session:
            session.execute(delete(LicenseRecord))
        self._snapshot = LicenseSnapshot.free("Bin-Tel is running with the Free plan.")
        self._loaded = True
        self._notify()

    def _notify(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change(self._snapshot)
            except Exception:  # noqa: BLE001 - a listener must not break licensing
                logger.exception("A licence change listener raised")

    # -- state derivation --------------------------------------------------
    def _derive(
        self, token: LicenseToken, last_validated: datetime | None, license_key: str
    ) -> LicenseSnapshot:
        payload = token.payload
        now = datetime.now(UTC)

        if payload.is_suspended:
            return payload_to_snapshot(
                payload,
                state=LicenseState.SUSPENDED,
                license_key=license_key,
                last_validated_at=last_validated,
                message=(
                    "This licence has been suspended. Bin-Tel is running with the Free "
                    "plan; contact support to restore it."
                ),
            )

        if payload.is_expired:
            return payload_to_snapshot(
                payload,
                state=LicenseState.EXPIRED,
                license_key=license_key,
                last_validated_at=last_validated,
                message=(
                    "Your licence has expired. Bin-Tel is running with the Free plan — "
                    "your database and lookups are unaffected."
                ),
            )

        if not self._devices.matches(payload.device_id):
            return payload_to_snapshot(
                payload,
                state=LicenseState.INVALID,
                license_key=license_key,
                last_validated_at=last_validated,
                message=(
                    "This licence was activated for a different device. Activate it "
                    "here to use your plan on this machine."
                ),
            )

        grace_until = payload.grace_until(last_validated)
        if grace_until is not None and now > grace_until:
            return payload_to_snapshot(
                payload,
                state=LicenseState.EXPIRED,
                license_key=license_key,
                last_validated_at=last_validated,
                message=(
                    "Bin-Tel has not been able to verify your licence for a while, so "
                    "it is running with the Free plan. Reconnect to restore your plan."
                ),
            )

        # Verified recently enough to be trusted. When the last successful
        # check is stale but still inside the window, say so plainly.
        state = _state_for(payload.plan_enum)
        if last_validated is not None and grace_until is not None:
            age = now - _aware(last_validated)
            window = grace_until - _aware(last_validated)
            if window.total_seconds() > 0 and age > window / 2:
                return payload_to_snapshot(
                    payload,
                    state=LicenseState.OFFLINE_GRACE,
                    license_key=license_key,
                    last_validated_at=last_validated,
                    message=(
                        "Bin-Tel has not reached the licensing service recently. Your "
                        f"{payload.plan_enum.label} plan stays active until "
                        f"{grace_until:%d %b %Y}."
                    ),
                )
        return payload_to_snapshot(
            payload,
            state=state,
            license_key=license_key,
            last_validated_at=last_validated,
        )

    # -- operations --------------------------------------------------------
    def activate(self, license_key: str) -> LicenseSnapshot:
        """Exchange a key for a signed licence and store it."""
        token = self._activation.activate(license_key)
        if not self._devices.matches(token.payload.device_id):
            raise LicenseError(
                "The licensing service issued a licence for a different device.",
                detail="device_id mismatch in the issued licence",
            )
        self._write(token, license_key.strip(), datetime.now(UTC))
        log_event(
            logger,
            "Licence activated",
            plan=token.payload.plan,
            license_id=token.payload.license_id,
            key=redact_key(license_key),
        )
        return self._snapshot

    def revalidate(self, *, force: bool = False) -> LicenseSnapshot:
        """Re-check the licence with the service.

        Being offline is not a failure: the stored state simply stands until
        the grace window closes.
        """
        snapshot = self.snapshot
        if not snapshot.is_activated:
            return snapshot
        if not force and not snapshot.needs_revalidation:
            return snapshot

        record = self._read_record()
        if record is None or not record.token:
            return snapshot
        token = LicenseToken.parse(record.token, self._activation.verifying_key)
        if token is None:
            return self.load()

        try:
            refreshed = self._activation.validate(token)
        except OfflineError:
            logger.info("Licence revalidation skipped: the service is unreachable")
            return snapshot
        except ActivationRejected as exc:
            logger.warning("Licence revalidation was declined: %s", exc.message)
            self._snapshot = payload_to_snapshot(
                token.payload,
                state=LicenseState.SUSPENDED,
                license_key=record.license_key or "",
                last_validated_at=record.last_validated_at,
                message=exc.message,
            )
            self._notify()
            return self._snapshot
        except NetworkError:
            logger.info("Licence revalidation failed; keeping the stored state")
            return snapshot

        self._write(refreshed, record.license_key or "", datetime.now(UTC))
        log_event(logger, "Licence revalidated", plan=refreshed.payload.plan)
        return self._snapshot

    def deactivate(self) -> LicenseSnapshot:
        """Release this device and return to the Free plan."""
        record = self._read_record()
        if record is not None and record.token:
            token = LicenseToken.parse(record.token, self._activation.verifying_key)
            if token is not None:
                try:
                    self._activation.deactivate(token)
                except (OfflineError, NetworkError):
                    # The local licence is still removed; the service will
                    # reconcile the seat when it next hears from this device.
                    logger.info("Deactivating locally; the service was unreachable")
                except ActivationRejected:
                    logger.info("The service had already released this activation")
        self._clear()
        log_event(logger, "Licence deactivated")
        return self._snapshot

    def devices(self) -> list[DeviceRecord]:
        record = self._read_record()
        if record is None or not record.token:
            return []
        token = LicenseToken.parse(record.token, self._activation.verifying_key)
        if token is None:
            return []
        try:
            return self._activation.devices(token)
        except (OfflineError, NetworkError, LicenseError):
            # Offline, the current device is still known locally.
            return [
                DeviceRecord(
                    device_id=self._devices.device_id,
                    name=self._devices.device_name,
                    platform="",
                    activated_at=record.issued_at,
                    last_seen_at=record.last_validated_at,
                    current=True,
                )
            ]

    def apply_free(self) -> LicenseSnapshot:
        """Explicitly continue on the Free plan."""
        self._clear()
        return self._snapshot


def _state_for(plan: Plan) -> LicenseState:
    return {
        Plan.FREE: LicenseState.FREE,
        Plan.PRO: LicenseState.PRO,
        Plan.BUSINESS: LicenseState.BUSINESS,
        Plan.ENTERPRISE: LicenseState.ENTERPRISE,
    }[plan]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
