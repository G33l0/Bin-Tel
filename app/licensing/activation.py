"""Talking to a licensing service.

The client is written against an interface, not a URL. In production that
interface is satisfied by :class:`HttpLicenseClient` against the configured
endpoint; in development — and in the test-suite — the same interface is
satisfied by :class:`LocalLicenseServer`, which issues genuinely signed
licences from a locally generated key pair. Nothing pretends a production
backend exists.

Endpoints (POST unless noted)::

    /activate     key + device  → signed licence token
    /validate     token + device → refreshed token
    /deactivate   token + device → confirmation
    /devices      token          → GET the activated device list
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.core.constants import APP_VERSION, MANIFEST_TIMEOUT, USER_AGENT
from app.core.errors import BinTelError, NetworkError, OfflineError
from app.core.logging_config import get_logger, log_event
from app.licensing.devices import DeviceIdentity
from app.licensing.models import DeviceRecord, LicensePayload, LicenseToken
from app.licensing.plans import Plan
from app.licensing.signing import b64encode, generate_secret_key, public_key

logger = get_logger(__name__)


class LicenseError(BinTelError):
    title = "Licence problem"


class ActivationRejected(LicenseError):
    title = "Activation was declined"


class DeviceLimitReached(ActivationRejected):
    title = "Device limit reached"


class LicenseClient(ABC):
    """What the application needs from a licensing service."""

    #: The public key licences from this service are verified against.
    verifying_key: bytes = b""

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def activate(self, license_key: str, device: DeviceIdentity) -> LicenseToken:
        """Exchange a licence key for a signed licence bound to *device*."""

    @abstractmethod
    def validate(self, token: LicenseToken, device: DeviceIdentity) -> LicenseToken:
        """Re-verify a licence and receive a refreshed token."""

    @abstractmethod
    def deactivate(self, token: LicenseToken, device: DeviceIdentity) -> bool:
        """Release this device's activation."""

    @abstractmethod
    def devices(self, token: LicenseToken) -> list[DeviceRecord]:
        """List the devices currently activated against this licence."""


class HttpLicenseClient(LicenseClient):
    """Production adapter for a hosted licensing API."""

    def __init__(self, base_url: str, verifying_key: bytes, *, timeout: float = MANIFEST_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.verifying_key = verifying_key
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.base_url) and bool(self.verifying_key)

    # -- operations -------------------------------------------------------
    def activate(self, license_key: str, device: DeviceIdentity) -> LicenseToken:
        payload = {"license_key": license_key, **device.as_payload()}
        data = self._post("/activate", payload)
        return self._token_from(data)

    def validate(self, token: LicenseToken, device: DeviceIdentity) -> LicenseToken:
        data = self._post("/validate", {"token": token.raw, **device.as_payload()})
        return self._token_from(data)

    def deactivate(self, token: LicenseToken, device: DeviceIdentity) -> bool:
        data = self._post("/deactivate", {"token": token.raw, **device.as_payload()})
        return bool(data.get("deactivated", True))

    def devices(self, token: LicenseToken) -> list[DeviceRecord]:
        data = self._get("/status", {"token": token.raw})
        entries = data.get("devices", [])
        records: list[DeviceRecord] = []
        for entry in entries if isinstance(entries, list) else []:
            try:
                records.append(DeviceRecord.model_validate(entry))
            except Exception:  # noqa: BLE001 - skip a malformed row
                continue
        return records

    # -- transport --------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"User-Agent": USER_AGENT, "Content-Type": "application/json"}

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json_body=payload)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self._timeout, headers=self._headers()) as client:
                response = client.request(method, url, json=json_body, params=params)
        except httpx.TransportError as exc:
            raise OfflineError(
                "Bin-Tel could not reach the licensing service. Your licence keeps "
                "working during its offline grace period.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        if response.status_code in (401, 403):
            raise ActivationRejected(
                "That licence key was not accepted.",
                detail=f"HTTP {response.status_code}",
            )
        if response.status_code == 409:
            raise DeviceLimitReached(
                "This licence has already been activated on the maximum number of "
                "devices. Deactivate another device and try again.",
                detail="HTTP 409",
            )
        if response.status_code >= 400:
            raise NetworkError(
                "The licensing service reported a problem.",
                detail=f"HTTP {response.status_code}",
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise NetworkError(
                "The licensing service returned an unreadable response.",
                detail=str(exc),
            ) from exc
        return data if isinstance(data, dict) else {}

    def _token_from(self, data: dict[str, Any]) -> LicenseToken:
        raw = str(data.get("token", ""))
        token = LicenseToken.parse(raw, self.verifying_key)
        if token is None:
            raise LicenseError(
                "The licence returned by the service could not be verified.",
                detail="Signature verification failed",
            )
        return token


class LocalLicenseServer(LicenseClient):
    """A real, signing licensing service that runs on this machine.

    It exists so licensing can be developed, demonstrated and tested without a
    hosted backend — it issues genuinely signed licences from a key pair kept
    beside the application data, and enforces the same device limits. It is
    never used unless it is explicitly selected.
    """

    #: Keys recognised by the development server, mapped to the plan they grant.
    DEMO_KEYS: dict[str, Plan] = {
        "BINTEL-DEV-PRO": Plan.PRO,
        "BINTEL-DEV-BUSINESS": Plan.BUSINESS,
        "BINTEL-DEV-ENTERPRISE": Plan.ENTERPRISE,
    }

    def __init__(self, state_dir: Path, *, validity_days: int = 365) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._key_path = self._state_dir / "dev-license-key.json"
        self._registry_path = self._state_dir / "dev-license-registry.json"
        self._validity_days = validity_days
        self._secret_key = self._load_or_create_key()
        self.verifying_key = public_key(self._secret_key)

    # -- key material ------------------------------------------------------
    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            try:
                data = json.loads(self._key_path.read_text(encoding="utf-8"))
                from app.licensing.signing import b64decode

                return b64decode(str(data["secret_key"]))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                logger.warning("Development licence key unreadable; generating a new one")
        secret = generate_secret_key()
        self._key_path.write_text(
            json.dumps(
                {
                    "secret_key": b64encode(secret),
                    "public_key": b64encode(public_key(secret)),
                    "note": "Development licence signing key. Never used in production.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("Development licence signing key created")
        return secret

    @property
    def public_key_b64(self) -> str:
        return b64encode(self.verifying_key)

    # -- registry ----------------------------------------------------------
    def _registry(self) -> dict[str, Any]:
        if not self._registry_path.exists():
            return {}
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_registry(self, registry: dict[str, Any]) -> None:
        self._registry_path.write_text(json.dumps(registry, indent=2, default=str), encoding="utf-8")

    # -- operations --------------------------------------------------------
    def activate(self, license_key: str, device: DeviceIdentity) -> LicenseToken:
        key = (license_key or "").strip().upper()
        plan = self.DEMO_KEYS.get(key)
        if plan is None:
            raise ActivationRejected(
                "That licence key was not recognised by the development licensing "
                "service. Try BINTEL-DEV-PRO, BINTEL-DEV-BUSINESS or "
                "BINTEL-DEV-ENTERPRISE.",
                detail=f"Unknown development key: {key[:12]}…",
            )

        from app.licensing.plans import Limit, PlanCatalogue

        definition = PlanCatalogue().get(plan)
        limit = definition.limit(Limit.DEVICES, 1)

        registry = self._registry()
        record = registry.setdefault(key, {"devices": {}})
        devices: dict[str, Any] = record["devices"]
        if device.device_id not in devices and 0 <= limit <= len(devices):
            raise DeviceLimitReached(
                f"This licence allows {limit} device(s) and they are all in use. "
                "Deactivate one and try again.",
                detail=f"{len(devices)} of {limit} devices active",
            )
        devices[device.device_id] = {
            "name": device.name,
            "platform": device.platform,
            "app_version": device.app_version,
            "activated_at": datetime.now(UTC).isoformat(),
            "last_seen_at": datetime.now(UTC).isoformat(),
        }
        self._save_registry(registry)

        log_event(logger, "Development licence activated", plan=plan.value, devices=len(devices))
        return self._issue(key, plan, device)

    def validate(self, token: LicenseToken, device: DeviceIdentity) -> LicenseToken:
        key = token.payload.license_id.replace("DEV-", "", 1)
        plan = self.DEMO_KEYS.get(key)
        if plan is None:
            raise ActivationRejected("This licence is no longer recognised.")
        registry = self._registry()
        devices = registry.get(key, {}).get("devices", {})
        if device.device_id not in devices:
            raise ActivationRejected(
                "This device is no longer activated for that licence.",
                detail="Device not in registry",
            )
        devices[device.device_id]["last_seen_at"] = datetime.now(UTC).isoformat()
        self._save_registry(registry)
        return self._issue(key, plan, device)

    def deactivate(self, token: LicenseToken, device: DeviceIdentity) -> bool:
        key = token.payload.license_id.replace("DEV-", "", 1)
        registry = self._registry()
        devices = registry.get(key, {}).get("devices", {})
        removed = devices.pop(device.device_id, None) is not None
        self._save_registry(registry)
        log_event(logger, "Development licence deactivated", removed=removed)
        return removed

    def devices(self, token: LicenseToken) -> list[DeviceRecord]:
        key = token.payload.license_id.replace("DEV-", "", 1)
        registry = self._registry()
        entries = registry.get(key, {}).get("devices", {})
        records: list[DeviceRecord] = []
        for device_id, entry in entries.items():
            records.append(
                DeviceRecord(
                    device_id=device_id,
                    name=str(entry.get("name", "")),
                    platform=str(entry.get("platform", "")),
                    app_version=str(entry.get("app_version", "")),
                    activated_at=_parse(entry.get("activated_at")),
                    last_seen_at=_parse(entry.get("last_seen_at")),
                    current=device_id == token.payload.device_id,
                )
            )
        return sorted(records, key=lambda item: item.activated_at or datetime.now(UTC))

    def _issue(self, key: str, plan: Plan, device: DeviceIdentity) -> LicenseToken:
        from app.licensing.plans import Limit, PlanCatalogue

        definition = PlanCatalogue().get(plan)
        now = datetime.now(UTC)
        payload = LicensePayload(
            license_id=f"DEV-{key}",
            plan=plan.value,
            status="active",
            subject="Development licence",
            issued_at=now,
            expires_at=now + timedelta(days=self._validity_days),
            device_id=device.device_id,
            device_limit=max(1, definition.limit(Limit.DEVICES, 1)),
            grace_days=14,
            edition=definition.database_edition,
            issuer="bintel-dev",
        )
        return LicenseToken.issue(payload, self._secret_key)


class ActivationService:
    """Orchestrates activation, validation and deactivation.

    Kept separate from :class:`~app.licensing.license_manager.LicenseManager`
    so that *talking to the service* and *deciding what the application is
    entitled to* remain independently testable.
    """

    def __init__(self, client: LicenseClient, device_manager: Any) -> None:
        self._client = client
        self._devices = device_manager

    @property
    def client(self) -> LicenseClient:
        return self._client

    def set_client(self, client: LicenseClient) -> None:
        self._client = client

    @property
    def available(self) -> bool:
        return self._client.available

    @property
    def verifying_key(self) -> bytes:
        return self._client.verifying_key

    def activate(self, license_key: str) -> LicenseToken:
        key = (license_key or "").strip()
        if not key:
            raise ActivationRejected("Enter your licence key to activate.")
        identity = self._devices.identity()
        log_event(logger, "Licence activation requested", app_version=APP_VERSION)
        return self._client.activate(key, identity)

    def validate(self, token: LicenseToken) -> LicenseToken:
        return self._client.validate(token, self._devices.identity())

    def deactivate(self, token: LicenseToken) -> bool:
        return self._client.deactivate(token, self._devices.identity())

    def devices(self, token: LicenseToken) -> list[DeviceRecord]:
        return self._client.devices(token)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
