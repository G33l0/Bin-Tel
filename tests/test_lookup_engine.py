"""The range-aware lookup engine.

These are the cases the engine exists to get right: an eight-digit assignment
beneath a shared six-digit root, an account range inside a broader one, an
issuer that changed, a prefix that names nobody, and records that disagree.

Every fixture is synthetic. See :mod:`tests.fixtures.scenarios`.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.lookup.evidence import EvidenceLevel, LookupConfidence, score_relationship
from app.lookup.strategy import LookupStrategy, MatchSpecificity
from tests.fixtures.scenarios import (
    CASCADE,
    CASCADE_SUB,
    HARBOR,
    MERIDIAN,
    NORTHSHORE,
    PACIFIC,
)


def issuers(result) -> list[str]:
    """Current issuing institutions named by a result."""
    return [item.display_name for item in result.relationships if item.is_issuing and item.is_current]


# -- specificity --------------------------------------------------------------


def test_exact_8_digit_match(lookup):
    result = lookup.lookup("41000012")
    assert result.strategy == LookupStrategy.EXACT_8.value
    assert issuers(result) == [NORTHSHORE]
    assert result.best is not None
    assert result.best.prefix_length == 8


def test_exact_6_digit_match(lookup):
    result = lookup.lookup("400001")
    assert result.strategy == LookupStrategy.EXACT_6.value
    assert issuers(result) == [MERIDIAN]
    assert result.best is not None
    assert result.best.prefix_length == 6


def test_specific_8_digit_wins_over_the_root_it_sits_under(lookup):
    """The whole point: the root names a different institution."""
    root = lookup.lookup("410000")
    extended = lookup.lookup("41000012")

    assert issuers(root) == [CASCADE]
    assert issuers(extended) == [NORTHSHORE]
    assert issuers(root) != issuers(extended)


def test_two_eight_digit_assignments_under_one_root_stay_separate(lookup):
    assert issuers(lookup.lookup("41000012")) == [NORTHSHORE]
    assert issuers(lookup.lookup("41000034")) == [CASCADE_SUB]


def test_a_six_digit_query_is_never_answered_by_a_child_assignment(lookup):
    """Typing the root must not drag in a more specific allocation."""
    result = lookup.lookup("410000")
    assert result.best is not None
    assert result.best.bin == "410000"
    assert NORTHSHORE not in issuers(result)


def test_a_root_reports_that_more_specific_assignments_exist(lookup):
    result = lookup.lookup("410000")
    assert result.more_specific_count == 2


def test_a_root_with_no_children_reports_none(lookup):
    assert lookup.lookup("400001").more_specific_count == 0


def test_range_match(lookup):
    result = lookup.lookup("450010")
    assert result.strategy == LookupStrategy.BROADER_RANGE.value
    assert issuers(result) == [PACIFIC]


def test_specific_range_beats_a_broader_range(lookup):
    """An account range inside an issuer range answers for its own span."""
    inside = lookup.lookup("45000055")
    outside = lookup.lookup("450010")

    assert inside.strategy == LookupStrategy.ACCOUNT_RANGE.value
    assert issuers(inside) == [HARBOR]
    assert outside.strategy == LookupStrategy.BROADER_RANGE.value
    assert issuers(outside) == [PACIFIC]


def test_the_broader_range_is_still_reported_as_a_candidate(lookup):
    result = lookup.lookup("45000055")
    assert len(result.records) > 1, "the wider allocation is not discarded"


@pytest.mark.parametrize(
    ("higher", "lower"),
    [
        (LookupStrategy.EXACT_8, LookupStrategy.EXACT_6),
        (LookupStrategy.EXACT_8, LookupStrategy.ACCOUNT_RANGE),
        (LookupStrategy.ACCOUNT_RANGE, LookupStrategy.EXACT_6),
        (LookupStrategy.EXACT_6, LookupStrategy.BROADER_RANGE),
        (LookupStrategy.BROADER_RANGE, LookupStrategy.ROOT_PREFIX),
        (LookupStrategy.ROOT_PREFIX, LookupStrategy.WEAK_INFERENCE),
    ],
)
def test_the_specificity_order_is_the_precedence_rule(higher, lower):
    assert higher.specificity > lower.specificity


# -- multiple relationships ---------------------------------------------------


def test_multiple_institution_relationships_are_all_returned(lookup):
    result = lookup.lookup("530001")
    names = {item.display_name for item in result.relationships}
    assert {MERIDIAN, CASCADE} <= names


def test_a_current_issuer_is_never_outranked_by_a_former_one(lookup):
    result = lookup.lookup("530001")
    assert result.best is not None
    assert result.best.issuer_name == MERIDIAN


def test_historical_relationships_are_marked_as_such(lookup):
    result = lookup.lookup("530001")
    historical = [item for item in result.relationships if not item.is_current]
    assert historical
    assert historical[0].display_name == CASCADE
    assert historical[0].standing_label == "Historical"
    assert historical[0].relationship_type == "former_issuer"


def test_an_effective_period_is_reported(lookup):
    result = lookup.lookup("530001")
    former = next(item for item in result.relationships if not item.is_current)
    assert former.effective_period == "2020–2024"


def test_a_timeline_is_not_treated_as_a_conflict(lookup):
    """One issuer succeeding another is a sequence, not a disagreement."""
    result = lookup.lookup("530001")
    assert not result.is_conflicted


# -- conflicts ----------------------------------------------------------------


def test_conflict_detection(lookup):
    result = lookup.lookup("520001")
    assert result.is_conflicted
    assert result.confidence_level == LookupConfidence.CONFLICTED.value


def test_a_conflict_names_the_competing_institution(lookup):
    result = lookup.lookup("520001")
    assert [item.display_name for item in result.conflicting_institutions] == [PACIFIC]


def test_a_conflict_preserves_both_readings(lookup):
    result = lookup.lookup("520001")
    names = {item.display_name for item in result.relationships}
    assert {MERIDIAN, PACIFIC} <= names, "neither claim is discarded"


# -- unknown ------------------------------------------------------------------


def test_unknown_result_for_an_unallocated_prefix(lookup):
    result = lookup.lookup("999999")
    assert not result.found
    assert result.confidence_level == LookupConfidence.UNKNOWN.value
    assert result.relationships == ()


def test_a_prefix_with_no_institution_resolves_to_unknown(lookup):
    """Present in the database, but naming nobody. Never borrow an issuer."""
    result = lookup.lookup("600001")
    assert result.found
    assert not result.resolved
    assert result.confidence_level == LookupConfidence.UNKNOWN.value
    assert result.relationships == ()


def test_a_missing_prefix_never_borrows_an_issuer_from_a_neighbour(lookup):
    """400001 and 400002 exist; 400009 does not. It must resolve to nothing."""
    result = lookup.lookup("400009")
    assert MERIDIAN not in issuers(result)
    assert not result.resolved


@pytest.mark.parametrize("query", ["414721", "414722", "414729"])
def test_numeric_proximity_is_never_evidence(lookup, query):
    """Sequential values are separate allocations. Nothing follows from adjacency."""
    assert not lookup.lookup(query).resolved


# -- confidence ---------------------------------------------------------------


def test_confidence_scoring_follows_the_evidence_hierarchy():
    exact = score_relationship(LookupStrategy.EXACT_8)
    account = score_relationship(LookupStrategy.ACCOUNT_RANGE)
    root = score_relationship(LookupStrategy.ROOT_PREFIX)
    weak = score_relationship(LookupStrategy.WEAK_INFERENCE)

    assert exact.score > account.score > root.score > weak.score
    assert exact.evidence_level is EvidenceLevel.AUTHORITATIVE_RANGE
    assert account.evidence_level is EvidenceLevel.NETWORK_ACCOUNT_RANGE


def test_confidence_carries_its_reasons():
    scored = score_relationship(LookupStrategy.EXACT_8)
    assert scored.reasons
    assert "8-digit" in scored.reasons[0]


def test_independent_agreement_raises_confidence():
    alone = score_relationship(LookupStrategy.EXACT_6)
    agreed = score_relationship(LookupStrategy.EXACT_6, agreeing_records=4)
    assert agreed.score > alone.score
    assert any("agree" in reason for reason in agreed.reasons)


def test_disagreement_produces_conflicted_rather_than_a_lower_score():
    scored = score_relationship(LookupStrategy.EXACT_8, disagreeing_records=1)
    assert scored.level is LookupConfidence.CONFLICTED


def test_no_institution_scores_unknown_not_zero_confidence_in_a_bank():
    scored = score_relationship(LookupStrategy.EXACT_8, has_institution=False)
    assert scored.level is LookupConfidence.UNKNOWN
    assert scored.score == 0.0


def test_an_associative_relationship_scores_below_an_issuing_one():
    issuing = score_relationship(LookupStrategy.EXACT_8)
    associative = score_relationship(LookupStrategy.EXACT_8, relationship_is_issuing=False)
    assert associative.score < issuing.score


def test_a_historical_relationship_scores_below_a_current_one():
    current = score_relationship(LookupStrategy.EXACT_8)
    historical = score_relationship(LookupStrategy.EXACT_8, is_current=False)
    assert historical.score < current.score


def test_nothing_is_ever_reported_as_absolute_certainty():
    scored = score_relationship(LookupStrategy.EXACT_8, agreeing_records=50)
    assert scored.score < 1.0


def test_the_published_confidence_caps_the_result():
    """The engine cannot be more certain than the record it is reading."""
    scored = score_relationship(LookupStrategy.EXACT_8, stored_confidence=0.4)
    assert scored.score <= 0.4


def test_a_lookup_reports_its_confidence_and_match(lookup):
    result = lookup.lookup("41000012")
    assert result.confidence_level == LookupConfidence.VERIFIED.value
    assert result.match_label == MatchSpecificity.EXACT_EXTENDED.label
    assert 0 < result.confidence_percent <= 99


# -- input handling -----------------------------------------------------------


def test_a_card_length_number_is_refused_by_the_engine(lookup):
    with pytest.raises(ValidationError):
        lookup.lookup("4111111111111111")


@pytest.mark.parametrize("query", ["41000012", "4100 0012", "4100-0012"])
def test_formatting_does_not_change_the_answer(lookup, query):
    assert issuers(lookup.lookup(query)) == [NORTHSHORE]


def test_luhn_is_not_used_to_identify_an_issuer(lookup):
    """A check digit says nothing about who issued a card."""
    import subprocess

    engine = subprocess.run(
        ["grep", "-ril", "luhn", "app/lookup", "app/services", "app/repositories"],
        capture_output=True,
        text=True,
    )
    assert engine.stdout.strip() == "", "Luhn must play no part in issuer resolution"
