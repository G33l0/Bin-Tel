"""JSON and JSON Lines importers.

``JSONLImporter`` streams line by line and handles files of any size.
``JSONImporter`` accepts a top-level array, or an object wrapping one under a
common key (``records``, ``data``, ``bins``, ``results``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from app.core.errors import ImportError_
from app.importers.base import BaseImporter
from app.services.ingest_service import RawBinRecord

_ARRAY_KEYS = ("records", "data", "bins", "results", "items", "rows")


class JSONImporter(BaseImporter):
    """Whole-document JSON importer."""

    name = "json"
    extensions = (".json",)

    def _load(self) -> list[Any]:
        try:
            payload = json.loads(
                self.options.source.read_text(encoding=self.options.encoding, errors="replace")
            )
        except json.JSONDecodeError as exc:
            raise ImportError_(
                "The JSON file could not be parsed.",
                detail=f"Line {exc.lineno}, column {exc.colno}: {exc.msg}",
            ) from exc
        except OSError as exc:
            raise ImportError_("The JSON file could not be read.", detail=str(exc)) from exc

        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in _ARRAY_KEYS:
                value = payload.get(key)
                if isinstance(value, list):
                    return value
            # A mapping of BIN -> attributes is also a valid shape.
            if payload and all(isinstance(value, dict) for value in payload.values()):
                return [{"bin": key, **value} for key, value in payload.items()]
            return [payload]
        raise ImportError_("The JSON file does not contain any BIN records.")

    def estimated_total(self) -> int | None:
        try:
            return len(self._load())
        except ImportError_:
            return None

    def iter_records(self) -> Iterator[RawBinRecord]:
        for entry in self._load():
            if not isinstance(entry, dict):
                continue
            record = self.to_record(self.map_nested(entry))
            if record is not None:
                yield record


class JSONLImporter(BaseImporter):
    """Newline-delimited JSON — streamed, so file size is irrelevant."""

    name = "jsonl"
    extensions = (".jsonl", ".ndjson")

    def estimated_total(self) -> int | None:
        try:
            with self.options.source.open("rb") as handle:
                return sum(1 for line in handle if line.strip())
        except OSError:  # pragma: no cover
            return None

    def iter_records(self) -> Iterator[RawBinRecord]:
        try:
            with self.options.source.open(
                "r", encoding=self.options.encoding, errors="replace"
            ) as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        entry = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue  # skip a malformed line, keep the run going
                    if not isinstance(entry, dict):
                        continue
                    record = self.to_record(self.map_nested(entry))
                    if record is not None:
                        yield record
        except OSError as exc:
            raise ImportError_("The JSONL file could not be read.", detail=str(exc)) from exc
