"""Importer registry — maps a file to the importer that can read it."""

from __future__ import annotations

from pathlib import Path

from app.core.errors import ImportError_
from app.importers.base import BaseImporter, ImportOptions
from app.importers.csv_importer import CSVImporter
from app.importers.json_importer import JSONImporter, JSONLImporter
from app.importers.sqlite_importer import SQLiteImporter

_REGISTRY: list[type[BaseImporter]] = [
    CSVImporter,
    JSONImporter,
    JSONLImporter,
    SQLiteImporter,
]


def register_importer(importer: type[BaseImporter]) -> type[BaseImporter]:
    """Register a new importer (future providers plug in here)."""
    if importer not in _REGISTRY:
        _REGISTRY.append(importer)
    return importer


def available_importers() -> list[type[BaseImporter]]:
    return list(_REGISTRY)


def importer_for(options: ImportOptions, *, name: str | None = None) -> BaseImporter:
    """Build the right importer for ``options.source`` (or an explicit *name*)."""
    if name:
        for importer in _REGISTRY:
            if importer.name == name:
                return importer(options)
        raise ImportError_(
            f"There is no importer named {name!r}.",
            detail=f"Known importers: {', '.join(item.name for item in _REGISTRY)}",
        )

    suffix = Path(options.source).suffix.lower()
    for importer in _REGISTRY:
        if suffix in importer.extensions:
            return importer(options)
    raise ImportError_(
        f"Bin-Tel does not know how to import {suffix or 'that file type'}.",
        detail="Supported formats: CSV, TSV, JSON, JSONL, SQLite",
    )
