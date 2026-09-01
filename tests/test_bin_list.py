"""The personal BIN list: what it accepts, what it refuses, and why.

Every BIN and institution here is synthetic. Real payment card data never
appears in a fixture.
"""

from __future__ import annotations

import pytest

from app.core.errors import ImportError_
from app.services.bin_list import (
    KNOWN_COLUMNS,
    normalise_bin,
    read_bin_list,
    resolve_columns,
)

HEADER = "bin,bank,country,network,card_type"


def write_list(tmp_path, body: str, *, header: str = HEADER):
    path = tmp_path / "bin-list.csv"
    path.write_text(f"{header}\n{body}", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The BIN itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("410000", "410000"),
    ("41000012", "41000012"),
    ("4100 0012", "41000012"),
    ("4100-0012", "41000012"),
    (" 530001 ", "530001"),
])
def test_a_bin_is_read_as_six_to_eight_digits(value, expected):
    assert normalise_bin(value) == expected


def test_an_eight_digit_bin_is_never_shortened_to_six():
    """The 8-digit value is the identity, not a longer spelling of a 6."""
    assert normalise_bin("41000012") == "41000012"
    assert len(normalise_bin("41000012")) == 8


@pytest.mark.parametrize("value", ["", "   ", "41A000", "12345", "abcdef"])
def test_an_unusable_bin_is_refused(value):
    with pytest.raises(ValueError):
        normalise_bin(value)


def test_a_card_length_value_is_refused_and_never_echoed():
    """A 16-digit string in this file would be a card number. It is refused."""
    digits = "4" * 16
    with pytest.raises(ValueError) as excinfo:
        normalise_bin(digits)
    message = str(excinfo.value)
    assert "16 digits" in message
    assert digits not in message, "the value must never be repeated back"


def test_a_long_value_is_not_echoed_into_the_problem_report(tmp_path):
    path = write_list(tmp_path, f"410000,Cascade Bank,US,visa,credit\n{'4' * 16},X,US,,\n")
    report = read_bin_list(path)
    assert report.accepted == 1
    assert report.rejected == 1
    problem = report.problems[0]
    assert problem.value == ""
    assert "4" * 16 not in problem.reason


# ---------------------------------------------------------------------------
# The file's shape
# ---------------------------------------------------------------------------


def test_only_bin_and_bank_are_required(tmp_path):
    path = write_list(tmp_path, "410000,Cascade Federal Bank\n", header="bin,bank")
    report = read_bin_list(path)
    assert report.accepted == 1
    assert report.records[0].bin == "410000"
    assert report.records[0].issuer == "Cascade Federal Bank"


def test_a_missing_required_column_stops_the_read(tmp_path):
    path = write_list(tmp_path, "410000,visa\n", header="bin,network")
    with pytest.raises(ImportError_) as excinfo:
        read_bin_list(path)
    assert "bank" in (excinfo.value.detail or "")


def test_an_unknown_column_stops_the_read_rather_than_being_guessed_at(tmp_path):
    """Silently ignoring a column is how a list ends up half-imported."""
    path = write_list(
        tmp_path, "410000,Cascade Bank,whatever\n", header="bin,bank,mystery_field"
    )
    with pytest.raises(ImportError_) as excinfo:
        read_bin_list(path)
    assert "mystery_field" in (excinfo.value.detail or "")


def test_column_aliases_are_accepted(tmp_path):
    path = write_list(tmp_path, "410000,Cascade Bank\n", header="IIN,Issuer")
    assert read_bin_list(path).accepted == 1


def test_columns_may_appear_in_any_order(tmp_path):
    path = write_list(tmp_path, "Cascade Bank,US,410000\n", header="bank,country,bin")
    record = read_bin_list(path).records[0]
    assert record.bin == "410000"
    assert record.issuer == "Cascade Bank"


def test_a_missing_file_says_where_it_was_expected(tmp_path):
    with pytest.raises(ImportError_) as excinfo:
        read_bin_list(tmp_path / "nothing.csv")
    assert "nothing.csv" in (excinfo.value.detail or "")


def test_a_file_with_only_a_header_is_refused(tmp_path):
    path = write_list(tmp_path, "")
    with pytest.raises(ImportError_) as excinfo:
        read_bin_list(path)
    assert "no rows to build from" in excinfo.value.message


def test_an_empty_file_is_refused(tmp_path):
    path = tmp_path / "bin-list.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ImportError_):
        read_bin_list(path)


def test_comments_and_blank_lines_are_ignored(tmp_path):
    path = tmp_path / "bin-list.csv"
    path.write_text(
        "# a note to self\n"
        "\n"
        f"{HEADER}\n"
        "410000,Cascade Bank,US,visa,credit\n"
        "\n"
        "# 999999,Not this one,US,,\n"
        "530001,Meridian Trust,GB,mastercard,credit\n",
        encoding="utf-8",
    )
    report = read_bin_list(path)
    assert report.accepted == 2
    assert [record.bin for record in report.records] == ["410000", "530001"]


def test_a_utf8_bom_does_not_break_the_header(tmp_path):
    path = tmp_path / "bin-list.csv"
    path.write_text(f"{HEADER}\n410000,Cascade Bank,US,visa,credit\n", encoding="utf-8-sig")
    assert read_bin_list(path).accepted == 1


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def test_one_bad_row_never_costs_the_rest_of_the_list(tmp_path):
    path = write_list(
        tmp_path,
        "410000,Cascade Bank,US,visa,credit\n"
        "not-a-bin,Nobody,US,,\n"
        "530001,Meridian Trust,GB,mastercard,credit\n",
    )
    report = read_bin_list(path)
    assert report.accepted == 2
    assert report.rejected == 1
    assert report.problems[0].line == 3


def test_a_row_naming_no_institution_is_skipped(tmp_path):
    path = write_list(tmp_path, "410000,,US,visa,credit\n")
    with pytest.raises(ImportError_):
        read_bin_list(path)


def test_a_later_row_supersedes_an_earlier_one_and_the_collision_is_reported(tmp_path):
    path = write_list(
        tmp_path,
        "410000,First Reading,US,visa,credit\n410000,Second Reading,US,visa,credit\n",
    )
    report = read_bin_list(path)
    assert report.accepted == 1
    assert report.duplicates == 1
    assert report.records[0].issuer == "Second Reading"


def test_a_range_row_carries_both_ends(tmp_path):
    path = write_list(
        tmp_path,
        "450000,Pacific Bank,450099,issuer_range\n",
        header="bin,bank,bin_high,range_type",
    )
    record = read_bin_list(path).records[0]
    assert record.bin == "450000"
    assert record.bin_high == "450099"
    assert record.range_type == "issuer_range"


def test_a_range_with_an_unusable_end_is_skipped_not_silently_narrowed(tmp_path):
    path = write_list(
        tmp_path,
        "410000,Cascade Bank,,visa,credit\n450000,Pacific Bank,nonsense,,\n",
        header="bin,bank,bin_high,network,card_type",
    )
    report = read_bin_list(path)
    assert report.accepted == 1
    assert "range end" in report.problems[0].reason


def test_the_list_is_trusted_but_never_asserted_as_verified(tmp_path):
    path = write_list(tmp_path, "410000,Cascade Bank,US,visa,credit\n")
    assert read_bin_list(path).records[0].confidence < 1.0


def test_every_known_column_resolves(tmp_path):
    header = list(KNOWN_COLUMNS)
    resolved = resolve_columns(header)
    assert set(resolved.values()) == set(header)
