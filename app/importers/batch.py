"""Batch importer — runs every supported file in a directory."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.core.errors import ImportError_
from app.core.logging_config import get_logger
from app.database.engine import DatabaseManager
from app.importers.base import BaseImporter, ImportOptions, ImportSummary
from app.importers.registry import available_importers, importer_for
from app.services.ingest_service import IngestResult, RawBinRecord

logger = get_logger(__name__)


@dataclass(slots=True)
class BatchSummary:
    files: list[tuple[Path, ImportSummary]] = field(default_factory=list)
    failures: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def totals(self) -> IngestResult:
        combined = IngestResult()
        for _, summary in self.files:
            combined.merge(summary.result)
        return combined

    @property
    def summary(self) -> str:
        return (
            f"{len(self.files)} file(s) imported · {self.totals.summary}"
            + (f" · {len(self.failures)} failed" if self.failures else "")
        )


class BatchImporter(BaseImporter):
    """Imports every supported file under a directory, one at a time."""

    name = "batch"
    extensions = ()

    def __init__(self, options: ImportOptions, *, recursive: bool = True) -> None:
        if not options.source.exists():
            raise ImportError_(
                "That folder could not be found.", detail=str(options.source)
            )
        if not options.source.is_dir():
            raise ImportError_("Batch import expects a folder, not a file.")
        self.options = options
        self._recursive = recursive

    def files(self) -> list[Path]:
        suffixes = {
            suffix
            for importer in available_importers()
            for suffix in importer.extensions
        }
        pattern = "**/*" if self._recursive else "*"
        return sorted(
            path
            for path in self.options.source.glob(pattern)
            if path.is_file() and path.suffix.lower() in suffixes
        )

    def iter_records(self) -> Iterator[RawBinRecord]:
        """Chain every file's records — used when a single pass is wanted."""
        for path in self.files():
            child = importer_for(replace(self.options, source=path))
            yield from child.iter_records()

    def run_all(
        self,
        manager: DatabaseManager,
        *,
        progress: Callable[[int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> BatchSummary:
        """Import each file separately so one bad file cannot abort the batch."""
        batch = BatchSummary()
        paths = self.files()
        if not paths:
            raise ImportError_("That folder contains no files Bin-Tel can import.")
        for index, path in enumerate(paths, start=1):
            if cancelled is not None and cancelled():
                break
            options = replace(self.options, source=path)
            if progress is not None:
                progress(index, f"Importing {path.name} ({index}/{len(paths)})…")
            try:
                importer = importer_for(options)
                batch.files.append((path, importer.run(manager, cancelled=cancelled)))
            except Exception as exc:
                logger.warning("Batch import skipped %s: %s", path.name, exc)
                batch.failures.append((path, str(exc)))
        logger.info("Batch import finished", extra={"context": {"summary": batch.summary}})
        return batch
