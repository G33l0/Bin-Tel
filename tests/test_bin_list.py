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
    """The BIN is the one column nothing can stand in for."""
    path = write_list(tmp_path, "Cascade Bank,visa\n", header="bank,network")
    with pytest.raises(ImportError_) as excinfo:
        read_bin_list(path)
    assert "bin" in (excinfo.value.detail or "")


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


def test_a_row_naming_no_institution_is_kept_with_the_issuer_unknown(tmp_path):
    """Losing the row would answer "not found" to a BIN the list contains.

    A scheme and a country are real facts about a BIN. Discarding them because
    the bank is unknown trades a partial answer for a wrong one.
    """
    path = write_list(tmp_path, "410000,,US,visa,credit\n")
    report = read_bin_list(path)
    assert report.accepted == 1
    assert report.unnamed_issuers == 1
    assert report.records[0].issuer is None
    assert report.records[0].country == "US"


def test_restating_the_same_fact_is_a_correction_and_the_later_row_wins(tmp_path):
    """Same BIN, same bank, same relationship and period — one assertion."""
    path = write_list(
        tmp_path,
        "410000,Cascade Bank,US,visa,credit\n410000,Cascade Bank,US,visa,debit\n",
    )
    report = read_bin_list(path)
    assert report.accepted == 1
    assert report.duplicates == 1
    assert report.records[0].card_type == "debit"


def test_two_banks_on_one_bin_are_both_kept(tmp_path):
    """Collapsing them would assert something the list does not say."""
    path = write_list(
        tmp_path,
        "520001,Harbor Mutual,US,mastercard,debit\n"
        "520001,Pacific Savings,US,mastercard,debit\n",
    )
    report = read_bin_list(path)
    assert report.accepted == 2
    assert report.distinct_bins == 1
    assert report.shared_bins == 1
    assert report.duplicates == 0
    assert {record.issuer for record in report.records} == {
        "Harbor Mutual",
        "Pacific Savings",
    }


def test_a_predecessor_and_its_successor_are_both_kept(tmp_path):
    path = write_list(
        tmp_path,
        "530001,Cascade Bank,former_issuer,2019-01-01,2024-06-30\n"
        "530001,Meridian Trust,,2024-07-01,\n",
        header="bin,bank,relationship,effective_from,effective_to",
    )
    report = read_bin_list(path)
    assert report.accepted == 2
    assert report.distinct_bins == 1
    assert report.duplicates == 0


def test_the_same_bank_over_two_different_periods_is_two_facts(tmp_path):
    path = write_list(
        tmp_path,
        "530001,Cascade Bank,former_issuer,2010-01-01,2015-01-01\n"
        "530001,Cascade Bank,,2024-07-01,\n",
        header="bin,bank,relationship,effective_from,effective_to",
    )
    report = read_bin_list(path)
    assert report.accepted == 2
    assert report.duplicates == 0


def test_the_same_bank_written_differently_is_still_one_fact(tmp_path):
    path = write_list(
        tmp_path,
        "410000,  cascade   bank ,US,visa,credit\n410000,Cascade Bank,US,visa,debit\n",
    )
    report = read_bin_list(path)
    assert report.accepted == 1
    assert report.duplicates == 1


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


# ---------------------------------------------------------------------------
# Real lists: several vocabularies, several files, spreadsheet damage
#
# The shapes below are the shapes three real datasets actually arrived in.
# Every BIN and institution is synthetic; only the column names are real.
# ---------------------------------------------------------------------------


def write_named(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_tab_separated_file_is_read(tmp_path):
    path = write_named(
        tmp_path, "bin-list.csv", "bin\tbank\tcountry\n410000\tCascade Bank\tUS\n"
    )
    report = read_bin_list(path)
    assert report.accepted == 1
    assert report.records[0].issuer == "Cascade Bank"


def test_a_french_header_is_understood(tmp_path):
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "BIN\tPays\tEmetteur\tMarque\tType\tNiveau\n"
        "410000\tFRANCE\tCascade Bank\tVISA\tDEBIT\tGOLD\n",
    )
    record = read_bin_list(path).records[0]
    assert record.issuer == "Cascade Bank"
    assert record.network == "VISA"
    assert record.card_type == "DEBIT"
    assert record.card_level == "GOLD"
    assert record.country == "FRANCE"


def test_the_country_code_beats_the_country_name(tmp_path):
    """A file may spell one country three ways. The code is the least ambiguous."""
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "bin\tissuer\tisoCode2\tisoCode3\tCountryName\n"
        "410000\tCascade Bank\tUS\tUSA\tUNITED STATES\n",
    )
    assert read_bin_list(path).records[0].country == "US"


def test_coordinates_are_recognised_and_never_stored(tmp_path):
    """These hold the country's centroid, not the bank's address."""
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "bin,issuer,latitude,longitude\n410000,Cascade Bank,37.0902,-95.7129\n",
    )
    report = read_bin_list(path)
    assert report.accepted == 1
    assert "latitude" in report.ignored_columns
    assert "longitude" in report.ignored_columns
    assert not hasattr(report.records[0], "latitude")


def test_a_short_bin_is_refused_unless_padding_is_asked_for(tmp_path):
    """42410 and 042410 are different BINs; the file alone cannot say which."""
    body = "bin,bank\n42410,Cascade Bank\n410000,Meridian Trust\n"
    path = write_named(tmp_path, "bin-list.csv", body)

    strict = read_bin_list(path)
    assert strict.accepted == 1
    assert strict.short_bins == 1
    assert strict.padded_bins == 0
    assert "042410" in strict.problems[0].reason

    padded = read_bin_list(path, pad_short_bins=True)
    assert padded.accepted == 2
    assert padded.padded_bins == 1
    assert {record.bin for record in padded.records} == {"042410", "410000"}


def test_a_bin_a_spreadsheet_turned_into_a_float_is_refused(tmp_path):
    path = write_named(
        tmp_path, "bin-list.csv", "bin,bank\n4.1E+05,Cascade Bank\n410000,Meridian\n"
    )
    report = read_bin_list(path)
    assert report.accepted == 1
    assert "scientific notation" in report.problems[0].reason


def test_a_phone_a_spreadsheet_destroyed_is_dropped_but_the_row_is_kept(tmp_path):
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "bin,bank,phone\n410000,Cascade Bank,5.51732E+11\n",
    )
    report = read_bin_list(path)
    assert report.accepted == 1
    assert report.damaged_values == 1
    assert report.records[0].phone is None
    assert report.records[0].issuer == "Cascade Bank"


def test_several_lists_can_live_in_one_file(tmp_path):
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "bin,bank\n410000,Cascade Bank\n"
        "\n"
        "BIN\tEmetteur\tMarque\n530001\tMeridian Trust\tMASTERCARD\n",
    )
    report = read_bin_list(path)
    assert report.accepted == 2
    assert {record.bin for record in report.records} == {"410000", "530001"}


def test_a_dataset_dropped_beside_the_list_is_read_too(tmp_path):
    main = write_named(tmp_path, "bin-list.csv", "bin,bank\n410000,Cascade Bank\n")
    write_named(
        tmp_path,
        "bin-lists/extra.tsv",
        "BIN\tIssuer\tBrand\n530001\tMeridian Trust\tMASTERCARD\n",
    )
    report = read_bin_list(main)
    assert report.accepted == 2
    assert [source.name for source in report.sources] == ["bin-list.csv", "extra.tsv"]


def test_a_scheme_in_a_column_called_brand_still_names_the_scheme(tmp_path):
    path = write_named(
        tmp_path, "bin-list.csv", "bin,issuer,brand\n410000,Cascade Bank,VISA\n"
    )
    record = read_bin_list(path).records[0]
    assert record.brand == "VISA"
    assert record.network == "VISA"


def test_a_prepaid_tier_says_the_card_is_prepaid(tmp_path):
    """The row's own words, not an inference from anything outside it."""
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "bin,issuer,card_type,category\n"
        "410000,Cascade Bank,DEBIT,PREPAID GOLD\n"
        "410001,Cascade Bank,DEBIT,GOLD\n",
    )
    prepaid, plain = read_bin_list(path).records
    assert prepaid.card_level == "PREPAID GOLD"
    assert prepaid.prepaid is True
    assert plain.prepaid is None


def test_a_mistyped_header_names_the_column_it_did_not_understand(tmp_path):
    """Worth keeping apart from a bad row: one is a typo, the other is a file."""
    path = write_named(
        tmp_path, "bin-list.csv", "bin,bank,contry\n410000,Cascade Bank,US\n"
    )
    with pytest.raises(ImportError_) as excinfo:
        read_bin_list(path)
    assert "contry" in (excinfo.value.detail or "")


def test_a_bad_bin_is_one_skipped_row_not_the_end_of_the_file(tmp_path):
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "bin,bank,country\n"
        "410000,Cascade Bank,US\n"
        "not-a-bin,Nobody,US\n"
        "530001,Meridian Trust,GB\n",
    )
    report = read_bin_list(path)
    assert report.accepted == 2
    assert report.rejected == 1


def test_padding_restores_a_seven_digit_value_to_eight(tmp_path):
    """Nothing is assigned at seven digits, so seven is an eight missing a zero."""
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "bin,bank\n4141234,Cascade Bank\n42410,Meridian Trust\n414123,Harbor Mutual\n",
    )
    report = read_bin_list(path, pad_short_bins=True)
    assert {record.bin for record in report.records} == {
        "04141234",  # seven digits, restored to an eight-digit assignment
        "042410",  # five digits, restored to a six-digit one
        "414123",  # already six; left exactly as it is
    }
    assert report.padded_bins == 2


def test_a_seven_digit_value_is_untouched_without_padding(tmp_path):
    """Only a file declared damaged gets repaired."""
    path = write_named(tmp_path, "bin-list.csv", "bin,bank\n4141234,Cascade Bank\n")
    report = read_bin_list(path)
    assert report.records[0].bin == "4141234"
    assert report.padded_bins == 0


def test_a_licence_beside_a_dataset_is_not_read_as_one(tmp_path):
    """A redistributed dataset arrives with its notices. They are not lists.

    Guessing by content would mean silently skipping a real list whose header
    had a typo, so the exclusion is by name — and it has to survive the way
    licences are actually named beside the file they cover.
    """
    main = write_named(tmp_path, "bin-list.csv", "bin,bank\n410000,Cascade Bank\n")
    write_named(
        tmp_path,
        "bin-lists/dataset.csv",
        "bin,issuer\n530001,Meridian Trust\n",
    )
    for name in (
        "LICENSE.txt",
        "dataset.LICENSE.txt",
        "README.txt",
        "ATTRIBUTION.txt",
        "NOTICE.txt",
    ):
        write_named(tmp_path, f"bin-lists/{name}", "Attribution 4.0 International\n\nText.\n")

    report = read_bin_list(main)
    assert [source.name for source in report.sources] == ["bin-list.csv", "dataset.csv"]
    assert report.accepted == 2


def test_a_list_whose_name_merely_contains_a_notice_word_is_still_read(tmp_path):
    """`licenses-by-bank.csv` is a list; the match is on whole name parts."""
    main = write_named(tmp_path, "bin-list.csv", "bin,bank\n410000,Cascade Bank\n")
    write_named(
        tmp_path, "bin-lists/licenses-by-bank.csv", "bin,issuer\n530001,Meridian\n"
    )
    report = read_bin_list(main)
    assert "licenses-by-bank.csv" in [source.name for source in report.sources]
    assert report.accepted == 2


# ---------------------------------------------------------------------------
# A file saying what its own columns mean
# ---------------------------------------------------------------------------


def test_a_file_can_declare_what_one_of_its_columns_means(tmp_path):
    """A column name does not carry its meaning; only the file knows it.

    `Pays` is French for country, and in one real list it holds the country a
    card is *accepted* in. Read as the issuing country it attributed
    Russian-issued BINs to Afghanistan.
    """
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "# bintel: Pays = accepted_in\n"
        "BIN\tPays\tEmetteur\n"
        "404059\tAFGHANISTAN\tCascade Bank\n",
    )
    report = read_bin_list(path)
    record = report.records[0]
    assert record.issuer == "Cascade Bank"
    assert record.country is None  # never asserted from an acceptance column
    assert "accepted_in" in report.ignored_columns


def test_without_the_declaration_the_alias_table_still_applies(tmp_path):
    """The directive is an override, not a new requirement."""
    path = write_named(
        tmp_path, "bin-list.csv", "BIN\tPays\tEmetteur\n404059\tFRANCE\tCascade Bank\n"
    )
    assert read_bin_list(path).records[0].country == "FRANCE"


def test_a_declaration_naming_an_unknown_column_is_refused(tmp_path):
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "# bintel: Pays = wherever\nBIN\tPays\n404059\tAFGHANISTAN\n",
    )
    with pytest.raises(ImportError_) as excinfo:
        read_bin_list(path)
    assert "wherever" in (excinfo.value.detail or "")


def test_a_declared_column_is_still_kept_in_full(tmp_path):
    """Not asserted is not discarded — the row keeps every cell."""
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "# bintel: Pays = accepted_in\n"
        "BIN\tPays\tEmetteur\n"
        "404059\tAFGHANISTAN\tCascade Bank\n",
    )
    record = read_bin_list(path).records[0]
    assert record.source_row["Pays"] == "AFGHANISTAN"


def test_a_file_can_declare_how_far_it_is_trusted(tmp_path):
    """Which source you trust is knowledge you have; the system lacked it."""
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "# bintel: confidence = 0.5\nbin,bank\n410000,Cascade Bank\n",
    )
    assert read_bin_list(path).records[0].confidence == 0.5


def test_a_file_that_says_nothing_is_trusted_as_before(tmp_path):
    path = write_named(tmp_path, "bin-list.csv", "bin,bank\n410000,Cascade Bank\n")
    assert read_bin_list(path).records[0].confidence == 0.9


def test_settings_can_live_beside_a_file_that_must_not_be_edited(tmp_path):
    """A redistributed dataset stays byte-for-byte as published."""
    main = write_named(tmp_path, "bin-list.csv", "bin,bank\n410000,Cascade Bank\n")
    dataset = write_named(
        tmp_path, "bin-lists/public.csv", "bin,issuer\n530001,Meridian Trust\n"
    )
    write_named(tmp_path, "bin-lists/public.csv.bintel", "# bintel: confidence = 0.4\n")

    report = read_bin_list(main)
    by_bin = {record.bin: record.confidence for record in report.records}
    assert by_bin == {"410000": 0.9, "530001": 0.4}
    # The dataset itself was never touched.
    assert dataset.read_text(encoding="utf-8") == "bin,issuer\n530001,Meridian Trust\n"


@pytest.mark.parametrize("declared", ["nonsense", "1.5", "0", "-0.2"])
def test_an_unusable_confidence_is_refused(tmp_path, declared):
    path = write_named(
        tmp_path,
        "bin-list.csv",
        f"# bintel: confidence = {declared}\nbin,bank\n410000,Cascade Bank\n",
    )
    with pytest.raises(ImportError_):
        read_bin_list(path)


def test_a_file_can_declare_that_its_zeros_were_stripped(tmp_path):
    """Which files are damaged is a property of the files, not of the machine.

    A dataset whose BIN column went through a spreadsheet is still damaged on
    someone else's laptop, and a clean list beside it must not be padded just
    because they share a folder.
    """
    main = write_named(tmp_path, "bin-list.csv", "bin,bank\n410000,Cascade Bank\n")
    write_named(
        tmp_path,
        "bin-lists/damaged.csv",
        "# bintel: pad_short_bins = true\nbin,bank\n42410,Meridian Trust\n",
    )
    write_named(tmp_path, "bin-lists/clean.csv", "bin,bank\n530001,Harbor Mutual\n")

    # No flag passed anywhere: the damaged file still reads correctly.
    report = read_bin_list(main)
    assert report.rejected == 0
    assert {record.bin for record in report.records} == {
        "410000",
        "042410",
        "530001",
    }
    assert report.padded_bins == 1


def test_a_file_can_decline_padding_the_caller_asked_for(tmp_path):
    """A clean file is not damaged by a flag meant for its neighbour."""
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "# bintel: pad_short_bins = false\nbin,bank\n42410,Cascade Bank\n410000,M\n",
    )
    report = read_bin_list(path, pad_short_bins=True)
    assert report.accepted == 1
    assert report.rejected == 1


def test_an_unusable_padding_declaration_is_refused(tmp_path):
    path = write_named(
        tmp_path,
        "bin-list.csv",
        "# bintel: pad_short_bins = maybe\nbin,bank\n410000,Cascade Bank\n",
    )
    with pytest.raises(ImportError_) as excinfo:
        read_bin_list(path)
    assert "maybe" in (excinfo.value.detail or "")
