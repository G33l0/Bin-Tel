"""Normalization and deduplication: confidence, evidence and merge safety."""

from __future__ import annotations

import pytest

from app.models.entities import CardType, FundingType
from app.normalizers.card_normalizer import CardNormalizer
from app.normalizers.confidence import MERGE_THRESHOLD, ConfidenceLevel
from app.normalizers.geo_normalizer import GeoNormalizer
from app.normalizers.name_normalizer import NameNormalizer
from app.normalizers.network_normalizer import NetworkNormalizer


@pytest.fixture
def names():
    return NameNormalizer()


# -- names --------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Meridian Trust Bank, N.A.",
        "MERIDIAN TRUST BANK NA",
        "  Meridian   Trust   Bank  ",
        "Meridian Trust Bank Incorporated",
    ],
)
def test_legal_suffixes_and_spacing_normalise_away(names, raw):
    """The legal suffix goes; the name itself, "bank" included, stays."""
    assert names.core_form(raw) == "meridian trust bank"


def test_abbreviations_expand_so_the_same_issuer_matches(names):
    assert names.core_form("Northshore CU") == names.core_form(
        "Northshore Credit Union"
    )
    assert names.core_form("First National FCU") == names.core_form(
        "First National Federal Credit Union"
    )


def test_an_empty_name_normalises_to_nothing(names):
    assert names.normalize("   ").is_empty
    assert names.normalize(None).is_empty


def test_aliases_are_proposed_for_a_legal_name(names):
    aliases = names.candidate_aliases("Meridian Trust Bank, N.A.")
    assert aliases
    assert any("meridian" in alias.lower() for alias in aliases)


def test_a_typo_scores_well_above_an_unrelated_bank(names):
    from app.normalizers.confidence import string_similarity

    typo = names.similarity("Meridian Trust Bank", "Meridien Trust Bank")
    unrelated = names.similarity("Meridian Trust Bank", "Pacific Rim Bank")
    assert typo > unrelated * 2

    # Search-time fuzziness compares the canonical spellings directly, which is
    # a looser test than the evidence-based one dedupe uses.
    assert (
        string_similarity(
            names.core_form("Meridian Trust Bank"),
            names.core_form("Meridien Trust Bank"),
        )
        > 0.9
    )


# -- matching -----------------------------------------------------------------


def test_a_strong_name_match_without_corroboration_will_not_merge(names):
    score = names.match("Meridian Trust Bank", "Meridian Trust Bank")
    assert score.score >= MERGE_THRESHOLD
    assert not score.can_merge, "similarity alone must never be enough"
    assert score.needs_review


def test_a_strong_name_match_with_corroboration_merges(names):
    score = names.match(
        "Meridian Trust Bank",
        "Meridian Trust Bank, N.A.",
        left_country="US",
        right_country="US",
        left_website="meridiantrust.example",
        right_website="https://www.meridiantrust.example/personal",
    )
    assert score.can_merge
    assert score.evidence.same_website_host
    assert score.level in (ConfidenceLevel.HIGH, ConfidenceLevel.CERTAIN)


def test_the_same_name_in_different_countries_does_not_merge(names):
    score = names.match(
        "Northern Bank",
        "Northern Bank",
        left_country="US",
        right_country="JP",
    )
    assert not score.can_merge
    assert "Countries differ" in score.evidence.notes


def test_an_alias_match_still_needs_corroboration(names):
    score = names.match("NTB", "Meridian Trust Bank", alias_match=True)
    assert score.score >= MERGE_THRESHOLD
    assert not score.can_merge


def test_shared_bins_count_as_corroboration(names):
    score = names.match(
        "Meridian Trust Bank",
        "Meridian Trust Bank",
        left_country="US",
        right_country="US",
        shared_bins=4,
    )
    assert score.evidence.shared_bins == 4
    assert score.can_merge


# -- networks -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("VISA", "visa"),
        ("visa credit", "visa"),
        ("MasterCard", "mastercard"),
        ("master card", "mastercard"),
        ("AMEX", "amex"),
        ("American Express", "amex"),
        ("union pay", "unionpay"),
    ],
)
def test_network_spellings_resolve_to_one_code(raw, code):
    assert NetworkNormalizer().code(raw) == code


def test_an_unknown_network_is_reported_as_unknown():
    normalizer = NetworkNormalizer()
    assert normalizer.code("Some Local Scheme") == "unknown"
    assert not normalizer.is_known("Some Local Scheme")


# -- card attributes ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("credit", CardType.CREDIT),
        ("DEBIT", CardType.DEBIT),
        ("pre-paid", CardType.PREPAID),
        ("", CardType.UNKNOWN),
        (None, CardType.UNKNOWN),
    ],
)
def test_card_types_normalise(raw, expected):
    assert CardNormalizer().card_type(raw) is expected


def test_funding_type_falls_back_to_the_card_type():
    normalizer = CardNormalizer()
    assert normalizer.funding_type(None, CardType.DEBIT) is FundingType.DEBIT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("yes", True), ("TRUE", True), ("1", True), ("no", False), ("", None), (None, None)],
)
def test_tri_state_booleans_keep_unknown_distinct_from_false(raw, expected):
    assert CardNormalizer().tri_state(raw) is expected


# -- geography ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("US", "US"),
        ("usa", "US"),
        ("United States", "US"),
        ("u.k.", "GB"),
        ("Deutschland", "DE"),
        ("Türkiye", "TR"),
    ],
)
def test_country_spellings_resolve_to_one_code(raw, code):
    assert GeoNormalizer().country_code(raw) == code


def test_an_unknown_country_is_not_invented():
    assert GeoNormalizer().country_code("Atlantis") is None


def test_a_postal_code_is_normalised_but_not_reformatted_wrongly():
    normalizer = GeoNormalizer()
    assert normalizer.postal_code(" m5h 2n2 ", "CA") == "M5H 2N2"
    assert normalizer.postal_code("10001", "US") == "10001"


# -- dedupe over a real database ---------------------------------------------


def test_a_dedupe_run_over_a_clean_package_finds_nothing_to_merge(manager):
    from app.services.dedupe_service import DedupeService

    with manager.session() as session:
        report = DedupeService(session, dry_run=True).run(merge=False)

    assert report.merged_institutions == 0
    assert report.merged == []
    assert report.duplicate_bins == 0
    assert report.scanned_institutions > 0
