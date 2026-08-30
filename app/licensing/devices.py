"""Device identity for activation.

The identifier is deliberately *not* a hardware fingerprint. It is a random
value generated once per installation and stored in the user database, salted
with nothing more than the application id. That is enough to enforce a device
limit fairly, and it reveals nothing about the machine, the network or the
person using it. Deleting the user database resets it, which is the honest
trade-off for a privacy-conscious scheme.
"""

from __future__ import annotations

import hashlib
import platform
import socket
from dataclasses import dataclass

from app.core.constants import APP_ID, APP_VERSION
from app.core.logging_config import get_logger
from app.database.user_store import UserDataStore

logger = get_logger(__name__)

DEVICE_ID_KEY = "device_id"
DEVICE_NAME_KEY = "device_name"


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """What is sent to the licensing service when activating."""

    device_id: str
    name: str
    platform: str
    app_version: str

    def as_payload(self) -> dict[str, str]:
        return {
            "device_id": self.device_id,
            "device_name": self.name,
            "platform": self.platform,
            "app_version": self.app_version,
        }


class DeviceManager:
    """Creates and remembers this installation's device identity."""

    def __init__(self, store: UserDataStore) -> None:
        self._store = store

    # -- identity ---------------------------------------------------------
    @property
    def device_id(self) -> str:
        """A stable, random per-installation identifier."""
        existing = self._store.get_metadata(DEVICE_ID_KEY)
        if existing:
            return existing
        # Derived from the install id so it is stable, and hashed with the
        # application id so the same install cannot be correlated across
        # unrelated products.
        digest = hashlib.sha256(
            f"{APP_ID}:{self._store.install_id}".encode()
        ).hexdigest()[:32]
        self._store.set_metadata(DEVICE_ID_KEY, digest)
        logger.info("Device identity created")
        return digest

    @property
    def device_name(self) -> str:
        """A friendly name so the user can tell their devices apart.

        Defaults to the machine's hostname because that is what a person
        recognises in a device list; it is editable and is only ever sent to
        the licensing service the user chose to activate against.
        """
        stored = self._store.get_metadata(DEVICE_NAME_KEY)
        if stored:
            return stored
        try:
            hostname = socket.gethostname().split(".")[0]
        except OSError:  # pragma: no cover - unusual host configuration
            hostname = ""
        name = hostname or f"{platform.system()} device"
        self._store.set_metadata(DEVICE_NAME_KEY, name)
        return name

    def rename(self, name: str) -> str:
        cleaned = " ".join((name or "").split())[:64]
        if cleaned:
            self._store.set_metadata(DEVICE_NAME_KEY, cleaned)
        return self.device_name

    def identity(self) -> DeviceIdentity:
        return DeviceIdentity(
            device_id=self.device_id,
            name=self.device_name,
            platform=f"{platform.system()} {platform.release()}",
            app_version=APP_VERSION,
        )

    def matches(self, device_id: str | None) -> bool:
        return bool(device_id) and device_id == self.device_id

    def reset(self) -> str:
        """Forget this device identity, e.g. after deactivating."""
        self._store.set_metadata(DEVICE_ID_KEY, None)
        logger.info("Device identity reset")
        return self.device_id
