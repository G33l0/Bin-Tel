"""Provider contract."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.providers.manifest import DatabaseManifest


class ProviderStatus(StrEnum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass(slots=True)
class DownloadProgress:
    """Progress of a package download, including derived speed and ETA."""

    received: int = 0
    total: int = 0
    started_at: float = 0.0
    _last_report: float = 0.0

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = time.monotonic()

    @property
    def elapsed(self) -> float:
        return max(1e-6, time.monotonic() - self.started_at)

    @property
    def fraction(self) -> float:
        return min(1.0, self.received / self.total) if self.total else 0.0

    @property
    def percent(self) -> int:
        return int(self.fraction * 100)

    @property
    def bytes_per_second(self) -> float:
        return self.received / self.elapsed

    @property
    def eta_seconds(self) -> float | None:
        speed = self.bytes_per_second
        if not self.total or speed <= 0:
            return None
        remaining = max(0, self.total - self.received)
        return remaining / speed

    def advance(self, chunk: int) -> DownloadProgress:
        self.received += chunk
        return self


#: Signature of the progress callback every provider accepts.
ProgressCallback = Callable[[DownloadProgress], None]
#: Returns True when the caller wants the transfer abandoned.
CancelCheck = Callable[[], bool]


class BaseProvider(ABC):
    """A source of Bin-Tel database packages.

    Implementations must not mutate the active database; they only produce a
    manifest and place a downloaded file somewhere the update service can
    verify it.
    """

    #: Stable identifier used in logs and settings.
    name: str = "base"
    #: Human-readable label for the Updates page.
    label: str = "Provider"
    #: An optional provider is skipped silently when it cannot serve, so its
    #: absence never masks the primary provider's diagnosis.
    optional: bool = False

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.status: ProviderStatus = ProviderStatus.UNKNOWN
        self.last_error: str | None = None

    @abstractmethod
    def fetch_manifest(self) -> DatabaseManifest:
        """Return the latest published package description."""

    @abstractmethod
    def download(
        self,
        manifest: DatabaseManifest,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        """Fetch the package described by *manifest* into *destination*."""

    def is_available(self) -> bool:
        """Cheap reachability probe; providers may override."""
        return self.enabled

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r} status={self.status}>"
