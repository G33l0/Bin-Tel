"""License data model: the signed payload and the state derived from it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.licensing.plans import Plan
from app.licensing.signing import b64decode, b64encode, verify

#: Token envelope: a version tag, the payload, and a detached signature.
TOKEN_PREFIX = "bintel-lic-v1"


class LicenseState(StrEnum):
    """Every state the application can be in with respect to licensing."""

    NOT_ACTIVATED = "not_activated"
    FREE = "free"
    PRO = "pro"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    OFFLINE_GRACE = "offline_grace"
    INVALID = "invalid"

    @property
    def label(self) -> str:
        return {
            LicenseState.NOT_ACTIVATED: "Not activated",
            LicenseState.FREE: "Free",
            LicenseState.PRO: "Pro",
            LicenseState.BUSINESS: "Business",
            LicenseState.ENTERPRISE: "Enterprise",
            LicenseState.EXPIRED: "Expired",
            LicenseState.SUSPENDED: "Suspended",
            LicenseState.OFFLINE_GRACE: "Offline (grace period)",
            LicenseState.INVALID: "Invalid licence",
        }[self]

    @property
    def is_entitled(self) -> bool:
        """Whether paid entitlements currently apply."""
        return self in (
            LicenseState.PRO,
            LicenseState.BUSINESS,
            LicenseState.ENTERPRISE,
            LicenseState.OFFLINE_GRACE,
        )

    @property
    def badge_state(self) -> str:
        """Maps to the interface's status vocabulary."""
        if self in (LicenseState.EXPIRED, LicenseState.SUSPENDED, LicenseState.INVALID):
            return "danger"
        if self is LicenseState.OFFLINE_GRACE:
            return "warning"
        if self.is_entitled:
            return "success"
        return "info"


class LicensePayload(BaseModel):
    """The signed body of a license.

    Everything the client trusts comes from here, and it is only trusted once
    the accompanying signature verifies against the embedded public key.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    license_id: str
    plan: str = Plan.FREE.value
    status: str = "active"
    #: The account or organisation the license belongs to. Never a card number
    #: or anything else that could identify a payment instrument.
    subject: str = ""
    issued_at: datetime
    expires_at: datetime | None = None
    device_id: str = ""
    device_limit: int = Field(default=1, ge=1)
    #: Explicit feature grants. When present these *replace* the plan's set,
    #: which is what makes bespoke enterprise entitlements possible.
    features: tuple[str, ...] = ()
    limits: dict[str, int] = Field(default_factory=dict)
    #: How long the client may keep working after it last reached the server.
    grace_days: int = Field(default=14, ge=0, le=365)
    edition: str = ""
    issuer: str = "bintel"

    @property
    def plan_enum(self) -> Plan:
        return Plan.parse(self.plan)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return _aware(self.expires_at) < datetime.now(UTC)

    @property
    def is_suspended(self) -> bool:
        return self.status.lower() in ("suspended", "revoked", "cancelled")

    def grace_until(self, last_validated: datetime | None) -> datetime | None:
        if last_validated is None or self.grace_days <= 0:
            return None
        return _aware(last_validated) + timedelta(days=self.grace_days)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class LicenseToken(BaseModel):
    """A payload plus its detached signature, as stored and transported."""

    model_config = ConfigDict(frozen=True)

    payload: LicensePayload
    signature: str
    raw: str

    @classmethod
    def parse(cls, token: str, verifying_key: bytes) -> Self | None:
        """Decode and verify a token. Returns ``None`` when it is not genuine."""
        parts = (token or "").strip().split(".")
        if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
            return None
        try:
            body = b64decode(parts[1])
            signature = b64decode(parts[2])
        except (ValueError, TypeError):
            return None
        if not verify(body, signature, verifying_key):
            return None
        try:
            payload = LicensePayload.model_validate_json(body)
        except ValidationError:
            return None
        return cls(payload=payload, signature=parts[2], raw=token)

    @classmethod
    def issue(cls, payload: LicensePayload, secret_key: bytes) -> Self:
        """Sign a payload. Used by the licensing service and its dev adapter."""
        from app.licensing.signing import sign

        body = payload.to_json().encode("utf-8")
        signature = sign(body, secret_key)
        raw = f"{TOKEN_PREFIX}.{b64encode(body)}.{b64encode(signature)}"
        return cls(payload=payload, signature=b64encode(signature), raw=raw)


class DeviceRecord(BaseModel):
    """One activated device, as reported by the licensing service."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    device_id: str
    name: str = ""
    platform: str = ""
    app_version: str = ""
    activated_at: datetime | None = None
    last_seen_at: datetime | None = None
    current: bool = False

    @property
    def display_name(self) -> str:
        return self.name or f"Device {self.device_id[:8]}"


class LicenseSnapshot(BaseModel):
    """What the application knows about its licence right now."""

    model_config = ConfigDict(frozen=True)

    state: LicenseState = LicenseState.FREE
    plan: Plan = Plan.FREE
    license_id: str = ""
    license_key: str = ""
    subject: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    last_validated_at: datetime | None = None
    grace_until: datetime | None = None
    device_id: str = ""
    device_limit: int = 1
    features: tuple[str, ...] = ()
    limits: dict[str, int] = Field(default_factory=dict)
    edition: str = "community"
    message: str = ""

    @property
    def is_activated(self) -> bool:
        return bool(self.license_id) and self.state is not LicenseState.NOT_ACTIVATED

    @property
    def days_remaining(self) -> int | None:
        if self.expires_at is None:
            return None
        delta = _aware(self.expires_at) - datetime.now(UTC)
        return max(0, delta.days)

    @property
    def grace_days_remaining(self) -> int | None:
        if self.grace_until is None:
            return None
        delta = _aware(self.grace_until) - datetime.now(UTC)
        return max(0, delta.days)

    @property
    def needs_revalidation(self) -> bool:
        """True once the offline grace window is more than half spent."""
        if self.grace_until is None or self.last_validated_at is None:
            return False
        total = _aware(self.grace_until) - _aware(self.last_validated_at)
        elapsed = datetime.now(UTC) - _aware(self.last_validated_at)
        return total.total_seconds() > 0 and elapsed > total / 2

    def describe(self) -> list[tuple[str, str]]:
        """Label/value pairs for the License page."""
        from app.utils.formatting import display, format_datetime

        rows = [
            ("Plan", self.plan.label),
            ("Status", self.state.label),
            ("Licence ID", display(self.license_id)),
            ("Registered to", display(self.subject)),
            ("Issued", format_datetime(self.issued_at, with_time=False)),
            (
                "Expires",
                "Never" if self.expires_at is None and self.is_activated
                else format_datetime(self.expires_at, with_time=False),
            ),
            ("Last verified", format_datetime(self.last_validated_at)),
            ("Devices", f"{self.device_limit}" if self.device_limit > 0 else "Unlimited"),
            ("Database edition", self.edition.title()),
        ]
        return rows

    @classmethod
    def free(cls, message: str = "") -> LicenseSnapshot:
        return cls(state=LicenseState.FREE, plan=Plan.FREE, message=message)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def payload_to_snapshot(
    payload: LicensePayload,
    *,
    state: LicenseState,
    license_key: str = "",
    last_validated_at: datetime | None = None,
    message: str = "",
) -> LicenseSnapshot:
    """Project a verified payload into the snapshot the application uses."""
    return LicenseSnapshot(
        state=state,
        plan=payload.plan_enum,
        license_id=payload.license_id,
        license_key=license_key,
        subject=payload.subject,
        issued_at=payload.issued_at,
        expires_at=payload.expires_at,
        last_validated_at=last_validated_at,
        grace_until=payload.grace_until(last_validated_at),
        device_id=payload.device_id,
        device_limit=payload.device_limit,
        features=payload.features,
        limits=dict(payload.limits),
        edition=payload.edition or _edition_for(payload.plan_enum),
        message=message,
    )


def _edition_for(plan: Plan) -> str:
    return {
        Plan.FREE: "community",
        Plan.PRO: "professional",
        Plan.BUSINESS: "business",
        Plan.ENTERPRISE: "enterprise",
    }[plan]


def redact_key(license_key: str) -> str:
    """Show enough of a key to recognise it, never enough to reuse it."""
    key = (license_key or "").strip()
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}{'•' * max(4, len(key) - 8)}{key[-4:]}"


def _unused(value: Any) -> None:  # pragma: no cover - keeps linters honest
    return None
