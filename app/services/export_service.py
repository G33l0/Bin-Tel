"""Export of lookup results.

Exports contain BIN/IIN and issuer metadata only. There is no code path here
that can emit cardholder data, and provenance/source fields are excluded from
every format — the same restriction the result UI is held to.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.core.constants import APP_NAME, APP_VERSION
from app.core.errors import ExportError
from app.core.logging_config import get_logger
from app.models.schemas import BinRecord, BinRow
from app.utils.csv_safety import escape_row, escape_rows

logger = get_logger(__name__)


class ExportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    TXT = "txt"

    @property
    def extension(self) -> str:
        return f".{self.value}"

    @property
    def label(self) -> str:
        return {
            ExportFormat.JSON: "JSON (*.json)",
            ExportFormat.CSV: "CSV (*.csv)",
            ExportFormat.TXT: "Plain text (*.txt)",
        }[self]

    @classmethod
    def from_path(cls, path: Path) -> ExportFormat:
        suffix = path.suffix.lower().lstrip(".")
        for member in cls:
            if member.value == suffix:
                return member
        return cls.JSON


#: Column order for tabular exports of a bank result.
ROW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("bin", "BIN"),
    ("length", "BIN Length"),
    ("network", "Network"),
    ("brand", "Card Brand"),
    ("card_type", "Card Type"),
    ("funding_type", "Funding"),
    ("institution", "Issuer"),
    ("relationship_type", "Relationship"),
    ("standing", "Standing"),
    ("country", "Country"),
    ("country_code", "ISO Country Code"),
    ("region", "State / Province"),
    ("city", "City"),
    ("postal_code", "Postal / ZIP Code"),
    ("status", "Status"),
)


class ExportService:
    """Renders results to JSON, CSV or plain text, then writes them out."""

    # -- single BIN result -------------------------------------------------
    def render_record(self, record: BinRecord, fmt: ExportFormat) -> str:
        if fmt is ExportFormat.JSON:
            payload = {
                "generator": f"{APP_NAME} {APP_VERSION}",
                "exported_at": datetime.now(UTC).isoformat(),
                "type": "bin_record",
                "record": record.to_export_dict(),
                # Split by tense as well as listed in full: a consumer reading
                # this file must not have to infer which of these use the BIN
                # now, and must never read a former issuer as a current one.
                "current_issuers": [
                    institution.display_name for institution in record.current_issuers
                ],
                "former_issuers": [
                    {
                        "name": institution.display_name,
                        "effective_from": institution.effective_from.isoformat()
                        if institution.effective_from
                        else None,
                        "effective_to": institution.effective_to.isoformat()
                        if institution.effective_to
                        else None,
                    }
                    for institution in record.former_issuers
                ],
                "institutions": [
                    {
                        "name": institution.display_name,
                        "legal_name": institution.legal_name,
                        "relationship": institution.relationship_label,
                        "is_current_issuer": institution.is_currently_issuing,
                        "effective_from": institution.effective_from.isoformat()
                        if institution.effective_from
                        else None,
                        "effective_to": institution.effective_to.isoformat()
                        if institution.effective_to
                        else None,
                        "country": institution.country.label if institution.country else "Unknown",
                    }
                    for institution in record.institutions
                ],
            }
            return json.dumps(payload, indent=2, ensure_ascii=False)

        pairs = record.to_field_pairs()
        if fmt is ExportFormat.CSV:
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(["Field", "Value"])
            writer.writerows(escape_rows(pairs))
            return buffer.getvalue()

        width = max(len(label) for label, _ in pairs) + 2
        lines = [
            f"{APP_NAME} — BIN Lookup Result",
            f"Exported {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            "-" * (width + 32),
        ]
        lines.extend(f"{label + ':':<{width}}{value}" for label, value in pairs)
        if record.institutions:
            lines.append("")
            lines.append("Associated institutions")
            lines.extend(
                f"  • {item.display_name} ({item.relationship_label})"
                + ("" if item.is_currently_issuing else f" — {item.ended_label}")
                for item in record.institutions
            )
        return "\n".join(lines) + "\n"

    def render_rows(self, rows: Sequence[BinRow], fmt: ExportFormat, title: str = "") -> str:
        if fmt is ExportFormat.JSON:
            payload = {
                "generator": f"{APP_NAME} {APP_VERSION}",
                "exported_at": datetime.now(UTC).isoformat(),
                "type": "bin_table",
                "title": title,
                "count": len(rows),
                "records": [
                    {label: row.cell(key) for key, label in ROW_COLUMNS} for row in rows
                ],
            }
            return json.dumps(payload, indent=2, ensure_ascii=False)

        if fmt is ExportFormat.CSV:
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow([label for _, label in ROW_COLUMNS])
            writer.writerows(
                escape_rows([row.cell(key) for key, _ in ROW_COLUMNS] for row in rows)
            )
            return buffer.getvalue()

        lines = [f"{APP_NAME} — {title or 'BIN records'}", f"{len(rows):,} record(s)", ""]
        lines.extend(
            " | ".join(f"{label}: {row.cell(key)}" for key, label in ROW_COLUMNS) for row in rows
        )
        return "\n".join(lines) + "\n"

    def render_bins(self, bins: Iterable[str]) -> str:
        """Plain newline-separated BIN list, for 'Copy selected BINs'."""
        return "\n".join(str(value) for value in bins)

    # -- writing ----------------------------------------------------------
    def write(self, path: Path, content: str) -> Path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise ExportError(
                "Bin-Tel could not write the export file. Check that the folder exists "
                "and that you have permission to write to it.",
                detail=str(exc),
            ) from exc
        logger.info(
            "Export written", extra={"context": {"path": str(path), "bytes": len(content)}}
        )
        return path

    def export_record(self, record: BinRecord, path: Path, fmt: ExportFormat | None = None) -> Path:
        fmt = fmt or ExportFormat.from_path(path)
        return self.write(path, self.render_record(record, fmt))

    def export_rows(
        self,
        rows: Sequence[BinRow],
        path: Path,
        fmt: ExportFormat | None = None,
        title: str = "",
    ) -> Path:
        fmt = fmt or ExportFormat.from_path(path)
        return self.write(path, self.render_rows(rows, fmt, title))

    @staticmethod
    def suggested_filename(stem: str, fmt: ExportFormat) -> str:
        safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in stem).strip("-")
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return f"bintel-{safe or 'export'}-{stamp}{fmt.extension}"


def summarise_for_clipboard(record: BinRecord) -> str:
    """Compact multi-line summary for the ``Copy Result`` action."""
    pairs = [(label, value) for label, value in record.to_field_pairs() if value != "Unknown"]
    width = max((len(label) for label, _ in pairs), default=0) + 2
    return "\n".join(f"{label + ':':<{width}}{value}" for label, value in pairs)


def _unused(*_: Any) -> None:  # pragma: no cover - keeps linters honest
    return None
