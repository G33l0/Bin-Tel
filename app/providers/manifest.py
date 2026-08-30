"""The database manifest — the lightweight document an update check fetches.

A manifest is a few hundred bytes, so checking for updates never downloads the
full database. Example::

    {
      "version": "2026.08.1",
      "schema_version": 1,
      "release_date": "2026-08-14T00:00:00Z",
      "database_size": 148234240,
      "record_count": 412918,
      "checksum": "sha256:9f2c...",
      "download_url": "https://dist.bintel.org/database/bintel-2026.08.1.sqlite",
      "compression": "none",
      "publisher": "Bin-Tel Project",
      "notes": "August reference refresh",
      "minimum_app_version": "1.0.0"
    }
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.constants import SCHEMA_VERSION
from app.core.errors import ManifestError
from app.utils.hashing import parse_checksum


class ManifestParseError(ManifestError):
    title = "Update information could not be read"


class DatabaseManifest(BaseModel):
    """Validated description of one published database package."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    version: str
    schema_version: int = SCHEMA_VERSION
    release_date: datetime | None = None
    database_size: int = Field(default=0, ge=0)
    record_count: int = Field(default=0, ge=0)
    checksum: str = ""
    download_url: str = ""
    compression: str = "none"
    publisher: str = "Bin-Tel Project"
    notes: str = ""
    minimum_app_version: str = "0.0.0"

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

    def is_newer_than(self, current: str | None) -> bool:
        """Version comparison that degrades to a string compare gracefully."""
        if not current:
            return True
        return compare_versions(self.version, current) > 0

    def supported_by(self, app_version: str, schema_version: int = SCHEMA_VERSION) -> bool:
        if self.schema_version > schema_version:
            return False
        return compare_versions(app_version, self.minimum_app_version) >= 0

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
