"""Report generation in every format, and the metadata-only guarantee."""

from __future__ import annotations

import csv
import io
import json

import pytest

from app.models.schemas import AdvancedQuery, PageRequest
from app.services.report_service import ReportFormat, ReportService


@pytest.fixture
def reports(tmp_path):
    return ReportService(tmp_path / "reports")


@pytest.fixture
def rows(manager):
    from app.repositories.search_repository import SearchRepository

    page = SearchRepository(manager).search(AdvancedQuery(), PageRequest(page_size=25))
    assert page.items
    return list(page.items)


@pytest.fixture
def content(reports, rows):
    return reports.build_search_report(
        AdvancedQuery(bin_prefix="4"),
        rows,
        title="Test report",
        database_version="2026.01.1",
        total=len(rows) * 3,
    )


def test_a_search_report_carries_its_rows_and_criteria(content, rows):
    assert content.row_count == len(rows)
    assert content.table_columns
    assert content.criteria
    assert any("first" in note for note in content.notes)


@pytest.mark.parametrize(
    "output_format", [ReportFormat.CSV, ReportFormat.JSON, ReportFormat.TXT]
)
def test_the_text_formats_render_and_write(reports, content, output_format):
    result = reports.generate(content, output_format)
    assert result.path.exists()
    assert result.path.suffix == output_format.extension
    assert result.size_bytes > 0


def test_csv_has_a_header_and_one_line_per_row(reports, content):
    payload = reports.render(content, ReportFormat.CSV).decode("utf-8")
    table = list(csv.reader(io.StringIO(payload)))
    header_index = next(
        index for index, line in enumerate(table) if line == content.table_columns
    )
    data = [
        line
        for line in table[header_index + 1 :]
        if len(line) == len(content.table_columns)
    ]
    assert len(data) == content.row_count


def test_json_is_valid_and_structured(reports, content):
    payload = json.loads(reports.render(content, ReportFormat.JSON).decode("utf-8"))
    assert payload["title"] == "Test report"
    assert len(payload["records"]) == content.row_count


def test_txt_is_readable_and_names_the_report(reports, content):
    payload = reports.render(content, ReportFormat.TXT).decode("utf-8")
    assert "Test report" in payload
    assert "2026.01.1" in payload


def test_pdf_renders_a_real_pdf(reports, content):
    pytest.importorskip("reportlab")
    payload = reports.render(content, ReportFormat.PDF)
    assert payload.startswith(b"%PDF-")
    assert len(payload) > 1000


def test_xlsx_renders_a_real_workbook(reports, content):
    pytest.importorskip("openpyxl")
    payload = reports.render(content, ReportFormat.XLSX)
    # XLSX is a zip archive.
    assert payload[:2] == b"PK"

    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(payload))
    assert workbook.sheetnames


def test_a_preview_summarises_without_writing_a_file(reports, content):
    preview = reports.preview(content, limit=5)
    assert "Test report" in preview
    assert list(reports.exports_dir.glob("*")) == [] if reports.exports_dir.exists() else True


def test_a_bin_report_contains_only_issuer_metadata(reports, manager):
    from app.repositories.bin_repository import BinRepository
    from app.services.lookup_service import LookupService
    from sqlalchemy import text

    with manager.session() as session:
        digits = str(session.execute(text("SELECT bin FROM bins LIMIT 1")).scalar())
    record = LookupService(BinRepository(manager)).lookup(digits).best
    assert record is not None

    content = reports.build_bin_report(record, database_version="2026.01.1")
    rendered = reports.render(content, ReportFormat.TXT).decode("utf-8").lower()

    for forbidden in (
        "cvv",
        "card number",
        "cardholder",
        "account number",
        "password",
        "magnetic",
        "track 1",
        "track 2",
    ):
        assert forbidden not in rendered


def test_a_normal_report_does_not_disclose_data_sources(reports, content):
    """Sources and internal notes are for administrators, not report readers."""
    rendered = reports.render(content, ReportFormat.TXT).decode("utf-8").lower()
    for forbidden in ("data source", "source url", "provider", "internal note"):
        assert forbidden not in rendered


def test_a_suggested_filename_is_safe(reports):
    name = ReportService.suggested_filename('BIN 4147/20: "report"', ReportFormat.CSV)
    assert name.endswith(".csv")
    for character in '/\\:"*?<>|':
        assert character not in name
