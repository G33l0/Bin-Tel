"""The database manifest — the lightweight document an update check fetches.

A manifest is a few hundred bytes, so checking for updates never downloads the
full database. Example::

    {
      "version": "2026.09.01",
      "schema_version": 1,
      "min_schema_version": 1,
      "release_date": "2026-09-01T00:00:00Z",
      "record_count": 1250000,
      "institution_count": 84000,
      "compressed_size": 42000000,
      "database_size": 184000000,
      "sha256": "9f2c…",
      "download_url": "https://dist.bintel.org/db/bintel-2026.09.01.sqlite.xz",
      "compression": "xz",
      "edition": "community",
      "publisher": "Bin-Tel Project",
      "notes": "September reference refresh",
      "minimum_app_version": "1.0.0",
      "deltas": [
        {"from_version": "2026.08.01", "url": "…", "size": 3100000, "sha256": "…"}
      ]
    }

``sha256`` and ``checksum`` are interchangeable; ``deltas`` is read and exposed
but is not applied by this build — see :mod:`app.providers.delta`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.constants import (
    APP_VERSION,
    MAX_SCHEMA_VERSION,
    MIN_SCHEMA_VERSION,
    SCHEMA_VERSION,
)
from app.core.errors import ManifestError
from app.utils.hashing import parse_checksum


class ManifestParseError(ManifestError):
    title = "Update information could not be read"


class Compatibility(BaseModel):
    """The outcome of checking a manifest against this build."""

    model_config = ConfigDict(frozen=True)

    installable: bool
    reason: str = ""
    needs_app_update: bool = False


class DeltaDescriptor(BaseModel):
    """A published incremental update from one version to this manifest's.

    Exposed so the client can *see* that a delta exists and report the saving;
    applying one is the job of a :class:`~app.providers.delta.DeltaApplier`.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    from_version: str
    url: str = ""
    size: int = Field(default=0, ge=0)
    checksum: str = ""
    format: str = "sqlite-diff"

    @property
    def digest(self) -> tuple[str, str]:
        return parse_checksum(self.checksum)


class DatabaseManifest(BaseModel):
    """Validated description of one published database package."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    version: str
    schema_version: int = SCHEMA_VERSION
    #: Oldest schema a client may hold and still install this package.
    min_schema_version: int = 1
    release_date: datetime | None = None
    #: Size of the installed (expanded) database.
    database_size: int = Field(default=0, ge=0)
    #: Size of the file that is actually transferred. Zero when uncompressed.
    compressed_size: int = Field(default=0, ge=0)
    record_count: int = Field(default=0, ge=0)
    institution_count: int = Field(default=0, ge=0)
    checksum: str = ""
    #: Convenience spelling used by the distribution tooling; folded into
    #: ``checksum`` during validation.
    sha256: str = ""
    download_url: str = ""
    compression: str = "none"
    #: Which database edition this package is — community, professional, …
    edition: str = "community"
    publisher: str = "Bin-Tel Project"
    notes: str = ""
    minimum_app_version: str = "0.0.0"
    deltas: tuple[DeltaDescriptor, ...] = ()

    @model_validator(mode="after")
    def _fold_sha256(self) -> DatabaseManifest:
        """Accept ``sha256`` as an alias for ``checksum``."""
        if not self.checksum and self.sha256:
            object.__setattr__(self, "checksum", f"sha256:{self.sha256.strip()}")
        return self

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Manifest is missing a version")
        return value

    @field_validator("download_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        """Absolute URLs are restricted to http(s)/file; relative paths are
        allowed and resolved against the manifest's own location."""
        value = value.strip()
        if not value:
            return value
        scheme = urlparse(value).scheme.lower()
        if scheme and scheme not in ("https", "http", "file"):
            raise ValueError("download_url must be an https, http or file URL")
        return value

    # -- derived ----------------------------------------------------------
    @property
    def checksum_algorithm(self) -> str:
        return parse_checksum(self.checksum)[0]

    @property
    def checksum_digest(self) -> str:
        return parse_checksum(self.checksum)[1]

    @property
    def has_checksum(self) -> bool:
        return bool(self.checksum_digest)

    @property
    def is_compressed(self) -> bool:
        return self.compression.lower() not in ("", "none", "raw")

    @property
    def transfer_size(self) -> int:
        """Bytes actually downloaded, which is what a progress bar measures."""
        if self.is_compressed and self.compressed_size:
            return self.compressed_size
        return self.database_size

    @property
    def required_storage(self) -> int:
        """Peak disk needed: the download, the expanded file, and the backup."""
        expanded = self.database_size or self.transfer_size
        return self.transfer_size + expanded * 2

    @property
    def saving_ratio(self) -> float:
        """How much smaller the transfer is than the installed database."""
        if not self.database_size or not self.is_compressed or not self.compressed_size:
            return 0.0
        return max(0.0, 1.0 - self.compressed_size / self.database_size)

    def delta_from(self, version: str | None) -> DeltaDescriptor | None:
        """The published delta that upgrades *version* to this release."""
        if not version:
            return None
        return next((item for item in self.deltas if item.from_version == version), None)

    @property
    def has_deltas(self) -> bool:
        return bool(self.deltas)

    def is_newer_than(self, current: str | None) -> bool:
        """Version comparison that degrades to a string compare gracefully."""
        if not current:
            return True
        return compare_versions(self.version, current) > 0

    def supported_by(
        self,
        app_version: str = APP_VERSION,
        schema_version: int = MAX_SCHEMA_VERSION,
    ) -> bool:
        """Whether this build may install the package at all."""
        return self.compatibility(app_version, schema_version).installable

    def compatibility(
        self,
        app_version: str = APP_VERSION,
        max_schema: int = MAX_SCHEMA_VERSION,
        min_schema: int = MIN_SCHEMA_VERSION,
    ) -> Compatibility:
        """Explain, in the manifest's own terms, whether it can be installed."""
        if self.schema_version > max_schema:
            return Compatibility(
                installable=False,
                reason=(
                    f"Database {self.version} uses schema {self.schema_version}, which "
                    f"needs a newer version of Bin-Tel (this build supports up to "
                    f"schema {max_schema})."
                ),
                needs_app_update=True,
            )
        if self.schema_version < min_schema:
            return Compatibility(
                installable=False,
                reason=(
                    f"Database {self.version} uses schema {self.schema_version}, which "
                    f"is older than this build supports (minimum {min_schema})."
                ),
            )
        if compare_versions(app_version, self.minimum_app_version) < 0:
            return Compatibility(
                installable=False,
                reason=(
                    f"Database {self.version} requires Bin-Tel "
                    f"{self.minimum_app_version} or newer."
                ),
                needs_app_update=True,
            )
        from app.providers.compression import is_supported

        if not is_supported(self.compression):
            return Compatibility(
                installable=False,
                reason=(
                    f"Database {self.version} is published with {self.compression} "
                    "compression, which this build cannot expand."
                ),
                needs_app_update=True,
            )
        return Compatibility(installable=True, reason="Compatible.")

    @classmethod
    def parse(cls, payload: str | bytes | dict[str, Any]) -> Self:
        """Parse and validate a manifest document."""
        if isinstance(payload, str | bytes):
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ManifestParseError(
                    "The update server returned something Bin-Tel could not read.",
                    detail=f"Invalid JSON: {exc}",
                ) from exc
        else:
            data = payload
        if not isinstance(data, dict):
            raise ManifestParseError(
                "The update server returned an unexpected response.",
                detail=f"Expected a JSON object, got {type(data).__name__}",
            )
        # Accept a wrapper of the shape {"latest": {...}} as well.
        if "version" not in data and isinstance(data.get("latest"), dict):
            data = data["latest"]
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ManifestParseError(
                "The update information from the server was incomplete.",
                detail=str(exc),
            ) from exc

    @classmethod
    def from_file(cls, path: Path) -> Self:
        try:
            return cls.parse(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ManifestParseError(
                "The manifest file could not be read.", detail=str(exc)
            ) from exc

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2)

    @classmethod
    def placeholder(cls) -> Self:
        return cls(version="0.0.0", release_date=datetime.now(UTC))

    def describe(self) -> list[tuple[str, str]]:
        """Label/value pairs for the first-run and Updates screens."""
        from app.utils.formatting import format_bytes, format_datetime, format_number

        rows = [
            ("Database version", self.version),
            ("Release date", format_datetime(self.release_date, with_time=False)),
            ("Records", format_number(self.record_count) if self.record_count else "Unknown"),
            ("Download size", format_bytes(self.transfer_size)),
            ("Installed size", format_bytes(self.database_size or self.transfer_size)),
            ("Required storage", format_bytes(self.required_storage)),
            ("Compression", self.compression if self.is_compressed else "None"),
            ("Edition", self.edition.title()),
            ("Schema version", str(self.schema_version)),
            ("Publisher", self.publisher),
        ]
        return rows


def compare_versions(left: str, right: str) -> int:
    """Compare dotted versions numerically; ``-1``, ``0`` or ``1``."""

    def parts(value: str) -> list[int | str]:
        out: list[int | str] = []
        for chunk in str(value).replace("-", ".").split("."):
            out.append(int(chunk) if chunk.isdigit() else chunk.lower())
        return out

    left_parts, right_parts = parts(left), parts(right)
    for index in range(max(len(left_parts), len(right_parts))):
        a = left_parts[index] if index < len(left_parts) else 0
        b = right_parts[index] if index < len(right_parts) else 0
        if a == b:
            continue
        if isinstance(a, int) and isinstance(b, int):
            return 1 if a > b else -1
        return 1 if str(a) > str(b) else -1
    return 0
