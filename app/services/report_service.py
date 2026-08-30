"""The Report Center.

Turns a search, an institution profile, an analytics view or a database health
check into a document. CSV, JSON and plain text are always available; PDF and
Excel are produced when their libraries are installed, and the service says so
plainly rather than offering a format it cannot deliver.

Customer-facing reports carry BIN/IIN and issuer metadata only. Data sources,
provider metadata and internal notes are never rendered — that information
exists in the database for data-quality work, and only the explicitly
requested internal report can reference it.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import APP_NAME, APP_TAGLINE, APP_VERSION, COPYRIGHT
from app.core.errors import ExportError
from app.core.logging_config import get_logger, log_event
from app.models.schemas import AdvancedQuery, BinRecord, BinRow, InstitutionDetail
from app.services.analytics_service import AnalyticsSnapshot
from app.services.health_service import HealthReport
from app.utils.formatting import format_bytes, format_datetime, format_number

logger = get_logger(__name__)


class ReportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    TXT = "txt"
    PDF = "pdf"
    XLSX = "xlsx"

    @property
    def extension(self) -> str:
        return f".{self.value}"

    @property
    def label(self) -> str:
        return {
            ReportFormat.CSV: "CSV (*.csv)",
            ReportFormat.JSON: "JSON (*.json)",
            ReportFormat.TXT: "Plain text (*.txt)",
            ReportFormat.PDF: "PDF document (*.pdf)",
            ReportFormat.XLSX: "Excel workbook (*.xlsx)",
        }[self]

    @property
    def display(self) -> str:
        return {
            ReportFormat.CSV: "CSV",
            ReportFormat.JSON: "JSON",
            ReportFormat.TXT: "Plain text",
            ReportFormat.PDF: "PDF",
            ReportFormat.XLSX: "Excel",
        }[self]

    @property
    def is_binary(self) -> bool:
        return self in (ReportFormat.PDF, ReportFormat.XLSX)


class ReportType(StrEnum):
    BIN_RECORD = "bin_record"
    INSTITUTION_PROFILE = "institution_profile"
    SEARCH_RESULTS = "search_results"
    ANALYTICS = "analytics"
    DATABASE_HEALTH = "database_health"
    WATCHLIST = "watchlist"

    @property
    def label(self) -> str:
        return {
            ReportType.BIN_RECORD: "BIN record",
            ReportType.INSTITUTION_PROFILE: "Institution profile",
            ReportType.SEARCH_RESULTS: "Search results",
            ReportType.ANALYTICS: "Analytics summary",
            ReportType.DATABASE_HEALTH: "Database health",
            ReportType.WATCHLIST: "Watchlist activity",
        }[self]

    @property
    def description(self) -> str:
        return {
            ReportType.BIN_RECORD: "One BIN and everything recorded about its issuer.",
            ReportType.INSTITUTION_PROFILE: "An institution and its complete BIN portfolio.",
            ReportType.SEARCH_RESULTS: "The results of a search, with its criteria.",
            ReportType.ANALYTICS: "Coverage and distribution across the database.",
            ReportType.DATABASE_HEALTH: "Integrity, completeness and health assessment.",
            ReportType.WATCHLIST: "Changes detected on watched records.",
        }[self]


def pdf_available() -> bool:
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


def xlsx_available() -> bool:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return False
    return True


def available_formats() -> list[ReportFormat]:
    """Formats this installation can actually produce."""
    formats = [ReportFormat.CSV, ReportFormat.JSON, ReportFormat.TXT]
    if pdf_available():
        formats.append(ReportFormat.PDF)
    if xlsx_available():
        formats.append(ReportFormat.XLSX)
    return formats


class ReportRequest(BaseModel):
    """Everything needed to produce a report. Serialised into templates."""

    model_config = ConfigDict(frozen=True)

    report_type: ReportType = ReportType.SEARCH_RESULTS
    output_format: ReportFormat = ReportFormat.PDF
    title: str = ""
    subtitle: str = ""
    query: AdvancedQuery | None = None
    institution_id: int | None = None
    bin_value: str | None = None
    watchlist_id: int | None = None
    #: Restrict a search report to the rows the user selected.
    selected_only: bool = False
    include_summary: bool = True
    max_rows: int = Field(default=10_000, ge=1)

    def resolved_title(self) -> str:
        return self.title or self.report_type.label

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True)


@dataclass(slots=True)
class ReportContent:
    """The assembled, format-independent body of a report."""

    title: str
    subtitle: str = ""
    report_type: ReportType = ReportType.SEARCH_RESULTS
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    database_version: str | None = None
    criteria: list[tuple[str, str]] = field(default_factory=list)
    summary: list[tuple[str, str]] = field(default_factory=list)
    #: ``(heading, rows)`` sections of label/value pairs.
    detail_sections: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    table_columns: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.table_rows)


@dataclass(slots=True)
class ReportResult:
    """What was produced."""

    path: Path
    content: ReportContent
    output_format: ReportFormat
    size_bytes: int

    @property
    def row_count(self) -> int:
        return self.content.row_count


#: Column set for tabular reports. Deliberately excludes anything about where
#: the data came from.
TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("bin", "BIN"),
    ("network", "Network"),
    ("brand", "Card Brand"),
    ("card_type", "Card Type"),
    ("funding_type", "Funding"),
    ("institution", "Issuer"),
    ("country", "Country"),
    ("region", "State / Province"),
    ("city", "City"),
    ("postal_code", "Postal / ZIP"),
    ("status", "Status"),
)


class ReportService:
    """Builds report content and renders it in the requested format."""

    def __init__(self, exports_dir: Path) -> None:
        self._exports_dir = Path(exports_dir)

    def set_exports_dir(self, path: Path) -> None:
        self._exports_dir = Path(path)

    @property
    def exports_dir(self) -> Path:
        return self._exports_dir

    # -- content builders --------------------------------------------------
    def build_bin_report(
        self, record: BinRecord, *, database_version: str | None = None
    ) -> ReportContent:
        content = ReportContent(
            title=f"BIN {record.bin}",
            subtitle=record.issuer_name,
            report_type=ReportType.BIN_RECORD,
            database_version=database_version,
        )
        pairs = [(label, value) for label, value in record.to_field_pairs()]
        content.detail_sections.append(("Record", pairs))
        if record.has_multiple_institutions:
            content.detail_sections.append(
                (
                    "Associated institutions",
                    [
                        (item.relationship_label, item.display_name)
                        for item in record.institutions
                    ],
                )
            )
        return content

    def build_institution_report(
        self,
        institution: InstitutionDetail,
        rows: Sequence[BinRow],
        *,
        stats: dict[str, Any] | None = None,
        database_version: str | None = None,
    ) -> ReportContent:
        content = ReportContent(
            title=institution.display_name,
            subtitle=institution.legal_name or "Institution profile",
            report_type=ReportType.INSTITUTION_PROFILE,
            database_version=database_version,
        )
        profile: list[tuple[str, str]] = [
            ("Institution", institution.display_name),
            ("Legal name", institution.legal_name or "Unknown"),
            ("Country", institution.country.display_name if institution.country else "Unknown"),
            ("Website", institution.website or "Unknown"),
            ("Associated BINs", format_number(institution.bin_count)),
        ]
        if institution.has_address and institution.address is not None:
            profile.append(("Address", institution.address.one_line))
        if institution.aliases:
            profile.append(("Also known as", ", ".join(institution.aliases[:8])))
        content.detail_sections.append(("Institution", profile))

        if stats:
            content.summary = [
                ("Total BINs", format_number(stats.get("total_bins", len(rows)))),
                ("Networks", format_number(len(stats.get("by_network", {})))),
                ("Countries", format_number(stats.get("countries", 0))),
                ("Prepaid", format_number(stats.get("prepaid", 0))),
                ("Commercial", format_number(stats.get("commercial", 0))),
            ]
        content.table_columns = [label for _, label in TABLE_COLUMNS]
        content.table_rows = [
            [row.cell(key) for key, _ in TABLE_COLUMNS] for row in rows
        ]
        return content

    def build_search_report(
        self,
        query: AdvancedQuery | None,
        rows: Sequence[BinRow],
        *,
        title: str = "",
        database_version: str | None = None,
        total: int | None = None,
    ) -> ReportContent:
        content = ReportContent(
            title=title or "Search results",
            subtitle=query.describe() if query else "All records",
            report_type=ReportType.SEARCH_RESULTS,
            database_version=database_version,
        )
        if query is not None:
            content.criteria = query.active_criteria
        content.summary = [
            ("Records in this report", format_number(len(rows))),
            ("Total matches", format_number(total if total is not None else len(rows))),
        ]
        content.table_columns = [label for _, label in TABLE_COLUMNS]
        content.table_rows = [[row.cell(key) for key, _ in TABLE_COLUMNS] for row in rows]
        if total is not None and total > len(rows):
            content.notes.append(
                f"This report contains the first {len(rows):,} of {total:,} matching records."
            )
        return content

    def build_analytics_report(
        self, snapshot: AnalyticsSnapshot, *, database_version: str | None = None
    ) -> ReportContent:
        content = ReportContent(
            title="Database analytics",
            subtitle=f"Database {database_version or snapshot.database_version or 'unknown'}",
            report_type=ReportType.ANALYTICS,
            database_version=database_version or snapshot.database_version,
        )
        content.summary = [(label, format_number(value)) for label, value in snapshot.headline]
        for name in ("country", "network", "card_type", "funding_type", "region", "status"):
            distribution = snapshot.distribution(name)
            if distribution.is_empty:
                continue
            content.detail_sections.append(
                (
                    distribution.title,
                    [
                        (
                            item.label,
                            f"{format_number(item.value)}  ({item.share(distribution.total):.1%})",
                        )
                        for item in distribution.top(12)
                    ],
                )
            )
        if snapshot.top_institutions:
            content.detail_sections.append(
                (
                    "Largest institutions by BIN count",
                    [(name, format_number(count)) for name, count in snapshot.top_institutions],
                )
            )
        if snapshot.growth:
            content.table_columns = ["Period", "Records added", "Cumulative"]
            content.table_rows = [
                [point.period, format_number(point.added), format_number(point.cumulative)]
                for point in snapshot.growth
            ]
        return content

    def build_health_report(
        self, report: HealthReport, *, database_version: str | None = None
    ) -> ReportContent:
        content = ReportContent(
            title="Database health",
            subtitle=f"{report.percent}% — {report.grade.label}",
            report_type=ReportType.DATABASE_HEALTH,
            database_version=database_version,
        )
        content.summary = [
            ("Health score", f"{report.percent}%"),
            ("Grade", report.grade.label),
            ("Records", format_number(report.records)),
            ("Institutions", format_number(report.institutions)),
            ("Duplicates", format_number(report.duplicates)),
            ("Orphans", format_number(report.orphans)),
            ("Conflicts", format_number(report.conflicts)),
        ]
        content.table_columns = ["Check", "Grade", "Score", "Detail"]
        content.table_rows = [
            [check.label, check.grade.label, f"{check.score:.2f}", check.detail]
            for check in report.checks
        ]
        if report.missing_fields:
            content.detail_sections.append(
                (
                    "Missing field coverage",
                    [
                        (name.title(), format_number(count))
                        for name, count in report.missing_fields.items()
                    ],
                )
            )
        return content

    def build_watchlist_report(
        self,
        watchlist_name: str,
        alerts: Sequence[Any],
        *,
        database_version: str | None = None,
    ) -> ReportContent:
        content = ReportContent(
            title=f"Watchlist activity — {watchlist_name}",
            subtitle=f"{len(alerts):,} change(s) detected",
            report_type=ReportType.WATCHLIST,
            database_version=database_version,
        )
        content.table_columns = ["Detected", "Target", "Change", "Detail", "From", "To"]
        content.table_rows = [
            [
                format_datetime(alert.detected_at),
                f"{alert.target_type.label}: {alert.target_value}",
                alert.change_type.label,
                alert.summary,
                alert.from_version or "—",
                alert.to_version or "—",
            ]
            for alert in alerts
        ]
        return content

    # -- rendering ---------------------------------------------------------
    def render(self, content: ReportContent, output_format: ReportFormat) -> bytes:
        """Render *content*. Returns bytes for every format."""
        if output_format is ReportFormat.JSON:
            return self._render_json(content).encode("utf-8")
        if output_format is ReportFormat.CSV:
            return self._render_csv(content).encode("utf-8")
        if output_format is ReportFormat.TXT:
            return self._render_text(content).encode("utf-8")
        if output_format is ReportFormat.PDF:
            return self._render_pdf(content)
        if output_format is ReportFormat.XLSX:
            return self._render_xlsx(content)
        raise ExportError(f"Bin-Tel cannot produce {output_format} reports.")

    def generate(
        self, content: ReportContent, output_format: ReportFormat, path: Path | None = None
    ) -> ReportResult:
        """Render and write a report."""
        destination = path or (
            self._exports_dir / self.suggested_filename(content.title, output_format)
        )
        payload = self.render(content, output_format)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        except OSError as exc:
            raise ExportError(
                "Bin-Tel could not write the report. Check that the folder exists and "
                "that you have permission to write to it.",
                detail=str(exc),
            ) from exc
        log_event(
            logger,
            "Report generated",
            report_type=content.report_type.value,
            output_format=output_format.value,
            rows=content.row_count,
            bytes=len(payload),
        )
        return ReportResult(
            path=destination,
            content=content,
            output_format=output_format,
            size_bytes=len(payload),
        )

    def preview(self, content: ReportContent, limit: int = 40) -> str:
        """A plain-text preview shown before exporting."""
        trimmed = ReportContent(
            title=content.title,
            subtitle=content.subtitle,
            report_type=content.report_type,
            generated_at=content.generated_at,
            database_version=content.database_version,
            criteria=content.criteria,
            summary=content.summary,
            detail_sections=content.detail_sections,
            table_columns=content.table_columns,
            table_rows=content.table_rows[:limit],
            notes=list(content.notes),
        )
        if len(content.table_rows) > limit:
            trimmed.notes.append(
                f"Preview shows {limit:,} of {len(content.table_rows):,} rows."
            )
        return self._render_text(trimmed)

    # -- format renderers --------------------------------------------------
    @staticmethod
    def _header(content: ReportContent) -> dict[str, Any]:
        return {
            "generator": f"{APP_NAME} {APP_VERSION}",
            "report_type": content.report_type.value,
            "title": content.title,
            "subtitle": content.subtitle,
            "generated_at": content.generated_at.isoformat(),
            "database_version": content.database_version or "unknown",
        }

    def _render_json(self, content: ReportContent) -> str:
        payload: dict[str, Any] = {
            **self._header(content),
            "criteria": dict(content.criteria),
            "summary": dict(content.summary),
            "sections": {
                heading: dict(rows) for heading, rows in content.detail_sections
            },
            "records": [
                dict(zip(content.table_columns, row, strict=False))
                for row in content.table_rows
            ],
            "notes": content.notes,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def _render_csv(self, content: ReportContent) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow([f"{APP_NAME} — {content.title}"])
        writer.writerow(["Generated", content.generated_at.strftime("%Y-%m-%d %H:%M UTC")])
        writer.writerow(["Database version", content.database_version or "unknown"])
        if content.subtitle:
            writer.writerow(["Subject", content.subtitle])
        if content.criteria:
            writer.writerow([])
            writer.writerow(["Search criteria"])
            writer.writerows(content.criteria)
        if content.summary:
            writer.writerow([])
            writer.writerow(["Summary"])
            writer.writerows(content.summary)
        for heading, rows in content.detail_sections:
            writer.writerow([])
            writer.writerow([heading])
            writer.writerows(rows)
        if content.table_columns:
            writer.writerow([])
            writer.writerow(content.table_columns)
            writer.writerows(content.table_rows)
        for note in content.notes:
            writer.writerow([])
            writer.writerow([note])
        return buffer.getvalue()

    def _render_text(self, content: ReportContent) -> str:
        lines: list[str] = [
            f"{APP_NAME} — {APP_TAGLINE}",
            "=" * 78,
            content.title,
        ]
        if content.subtitle:
            lines.append(content.subtitle)
        lines.extend(
            [
                "-" * 78,
                f"Generated        {content.generated_at.strftime('%d %b %Y, %H:%M UTC')}",
                f"Database version {content.database_version or 'unknown'}",
                f"Report type      {content.report_type.label}",
                "",
            ]
        )

        def block(heading: str, rows: Sequence[tuple[str, str]]) -> None:
            if not rows:
                return
            lines.append(heading.upper())
            width = max(len(label) for label, _ in rows) + 2
            lines.extend(f"  {label + ':':<{width}}{value}" for label, value in rows)
            lines.append("")

        block("Search criteria", content.criteria)
        block("Summary", content.summary)
        for heading, rows in content.detail_sections:
            block(heading, rows)

        if content.table_columns and content.table_rows:
            widths = [
                max(len(column), *(len(row[index]) for row in content.table_rows))
                for index, column in enumerate(content.table_columns)
            ]
            widths = [min(width, 28) for width in widths]
            lines.append("  ".join(column[:w].ljust(w) for column, w in zip(content.table_columns, widths, strict=False)))
            lines.append("  ".join("-" * w for w in widths))
            for row in content.table_rows:
                lines.append(
                    "  ".join(
                        str(value)[:w].ljust(w)
                        for value, w in zip(row, widths, strict=False)
                    )
                )
            lines.append("")

        lines.extend(content.notes)
        lines.append("")
        lines.append(COPYRIGHT)
        return "\n".join(lines) + "\n"

    def _render_pdf(self, content: ReportContent) -> bytes:
        if not pdf_available():
            raise ExportError(
                "PDF reports need the reportlab package, which is not installed in "
                "this build. CSV, JSON and text reports are available.",
                detail="import reportlab failed",
            )
        from app.services.report_pdf import render_pdf

        return render_pdf(content)

    def _render_xlsx(self, content: ReportContent) -> bytes:
        if not xlsx_available():
            raise ExportError(
                "Excel reports need the openpyxl package, which is not installed in "
                "this build. CSV, JSON and text reports are available.",
                detail="import openpyxl failed",
            )
        from app.services.report_xlsx import render_xlsx

        return render_xlsx(content)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def suggested_filename(title: str, output_format: ReportFormat) -> str:
        safe = "".join(
            char if char.isalnum() or char in "-_" else "-" for char in title
        ).strip("-")
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return f"bintel-{(safe or 'report').lower()}-{stamp}{output_format.extension}"

    @staticmethod
    def describe_size(num_bytes: int) -> str:
        return format_bytes(num_bytes)
