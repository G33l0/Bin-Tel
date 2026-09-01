"""Exports must be inert, and stored text must be clean.

Two defects these tests exist to keep dead:

* a bank name beginning ``=``, ``+``, ``@`` (or a tab, or a carriage return)
  is a *formula* to every spreadsheet, so an exported CSV opened in Excel
  would execute it;
* a control character or an unbounded paste reaching the database, where a NUL
  truncates the value anywhere C string handling meets it.

All values here are synthetic.
"""

from __future__ import annotations

import pytest

from app.normalizers.name_normalizer import name_normalizer
from app.normalizers.text import MAX_TEXT_LENGTH, sanitise_text
from app.services.export_service import ExportFormat, ExportService
from app.utils.csv_safety import escape_cell, escape_row, escape_rows

DDE_PAYLOAD = "=cmd|'/c calc'!A1"


# ---------------------------------------------------------------------------
# Spreadsheet formulas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    DDE_PAYLOAD,
    "+SUM(1+1)*cmd",
    "@SUM(A1)",
    "=1+1",
    "\tstarts with a tab",
    "\rstarts with a return",
])
def test_a_formula_is_quoted_so_a_spreadsheet_reads_it_as_text(payload):
    escaped = escape_cell(payload)
    assert escaped.startswith("'")
    assert escaped[1:] == payload, "the value itself is preserved, only prefixed"


@pytest.mark.parametrize("value", ["Cascade Bank", "410000", "", "Bank, N.A.", "a+b"])
def test_ordinary_text_is_left_exactly_as_it_is(value):
    assert escape_cell(value) == value


@pytest.mark.parametrize("number", ["-3", "-2.5", "-0", "-1e6"])
def test_a_negative_number_is_not_quoted(number):
    """Quoting it would turn a figure into a string in every spreadsheet."""
    assert escape_cell(number) == number


def test_a_negative_expression_is_quoted_because_it_is_not_a_number():
    assert escape_cell("-2+3+cmd").startswith("'")


def test_none_becomes_an_empty_cell():
    assert escape_cell(None) == ""


def test_rows_and_row_collections_are_escaped_throughout():
    assert escape_row(["ok", DDE_PAYLOAD]) == ["ok", f"'{DDE_PAYLOAD}"]
    assert escape_rows([["a", "=1"], ["=2", "b"]]) == [["a", "'=1"], ["'=2", "b"]]


# ---------------------------------------------------------------------------
# Through a real export
# ---------------------------------------------------------------------------


def test_a_csv_export_never_emits_an_executable_cell(scenario_manager):
    """The end-to-end property: nothing in the file starts a formula."""
    from app.repositories.bin_repository import BinRepository
    from app.services.lookup_service import LookupService

    lookup = LookupService(BinRepository(scenario_manager))
    record = lookup.lookup("410000").best
    assert record is not None

    text = ExportService().render_record(record, ExportFormat.CSV)
    for line in text.splitlines():
        for cell in line.split(","):
            stripped = cell.strip().strip('"')
            if not stripped or _looks_numeric(stripped):
                continue
            assert not stripped.startswith(("=", "+", "@")), f"executable cell: {cell!r}"


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# What reaches the database
# ---------------------------------------------------------------------------


def test_a_nul_never_reaches_a_stored_name():
    """SQLite accepts it, then it truncates wherever C strings are involved."""
    assert "\x00" not in name_normalizer.clean_display("Bank\x00Name")
    assert name_normalizer.clean_display("Bank\x00Name") == "BankName"


@pytest.mark.parametrize("control", ["\x01", "\x07", "\x1f", "\x7f"])
def test_control_characters_are_removed(control):
    assert control not in name_normalizer.clean_display(f"Bank{control}Name")


def test_a_name_is_bounded_to_the_declared_column_length():
    """SQLite does not enforce String(256), so something has to."""
    assert len(name_normalizer.clean_display("A" * 10_000)) == MAX_TEXT_LENGTH


def test_an_ordinary_name_survives_sanitising_unchanged():
    for name in ("Cascade Federal Bank", "Ünïcøde Bänk", "Bank of the West, N.A."):
        assert name_normalizer.clean_display(name) == name


def test_a_field_of_only_control_characters_becomes_an_honest_absence():
    assert sanitise_text("\x00\x01\x02") is None
    assert sanitise_text("   ") is None
    assert sanitise_text(None) is None
