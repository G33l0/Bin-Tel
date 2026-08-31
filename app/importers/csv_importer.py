"""Streaming CSV/TSV importer with delimiter sniffing."""

from __future__ import annotations

import csv
import sys
from collections.abc import Iterator
from pathlib import Path

from app.core.errors import ImportError_
from app.importers.base import BaseImporter, ImportOptions
from app.services.ingest_service import RawBinRecord

# Some published BIN exports have very long free-text address fields.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


class CSVImporter(BaseImporter):
    """Reads delimited text one row at a time."""

    name = "csv"
    extensions = (".csv", ".tsv", ".txt")

    def __init__(self, options: ImportOptions) -> None:
        super().__init__(options)
        self._delimiter = options.delimiter or self._sniff(options.source, options.encoding)

    @staticmethod
    def _sniff(path: Path, encoding: str) -> str:
        try:
            with path.open("r", encoding=encoding, errors="replace", newline="") as handle:
                sample = handle.read(8192)
        except OSError as exc:
            raise ImportError_(
                "The CSV file could not be opened.", detail=str(exc)
            ) from exc
        if not sample.strip():
            raise ImportError_("The CSV file is empty.")
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            # Fall back to whichever candidate appears most in the header line.
            header = sample.splitlines()[0] if sample.splitlines() else ""
            return max(",;\t|", key=header.count)

    def estimated_total(self) -> int | None:
        try:
            with self.options.source.open("rb") as handle:
                return max(0, sum(1 for _ in handle) - 1)
        except OSError:  # pragma: no cover - unreadable file
            return None

    def iter_records(self) -> Iterator[RawBinRecord]:
        options = self.options
        try:
            with options.source.open(
                "r", encoding=options.encoding, errors="replace", newline=""
            ) as handle:
                reader = csv.DictReader(handle, delimiter=self._delimiter)
                if not reader.fieldnames:
                    raise ImportError_("The CSV file has no header row.")
                for row in reader:
                    record = self.to_record(self.map_row(row))
                    if record is not None:
                        yield record
        except UnicodeDecodeError as exc:
            raise ImportError_(
                "The CSV file is not valid text in the selected encoding.",
                detail=str(exc),
            ) from exc
        except OSError as exc:
            raise ImportError_("The CSV file could not be read.", detail=str(exc)) from exc
