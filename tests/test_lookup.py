"""BIN lookup: exact hits, range fallbacks, misses and validation."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.errors import ValidationError


@pytest.fixture
def lookup(manager):
    from app.repositories.bin_repository import BinRepository
    from app.services.lookup_service import LookupService

    return LookupService(BinRepository(manager))


def _any_bin(manager) -> str:
    with manager.session() as session:
        return str(session.execute(text("SELECT bin FROM bins LIMIT 1")).scalar())


def test_exact_lookup_resolves_to_an_institution(lookup, manager):
    digits = _any_bin(manager)
    result = lookup.lookup(digits)

    assert result.found
    assert result.matched_by == "exact"
    assert result.best is not None
    assert result.best.bin == digits
    assert result.best.primary_institution is not None
    assert result.best.issuer_name


def test_lookup_is_indifferent_to_spacing(lookup, manager):
    digits = _any_bin(manager)
    spaced = lookup.lookup(f" {digits[:3]} {digits[3:]} ")
    assert spaced.found
    assert spaced.best is not None
    assert spaced.best.bin == digits


def test_unknown_bin_reports_a_miss_rather_than_raising(lookup):
    result = lookup.lookup("999999")
    assert not result.found
    assert result.best is None
    assert result.matched_by == "none"


@pytest.mark.parametrize("value", ["", "   ", "abcdef", "12", "---"])
def test_unusable_input_is_rejected(lookup, value):
    with pytest.raises(ValidationError):
        lookup.lookup(value)


def test_punctuation_is_stripped_rather_than_rejected(lookup, manager):
    digits = _any_bin(manager)
    formatted = f"{digits[:2]}-{digits[2:4]}.{digits[4:]}"
    assert lookup.lookup(formatted).found


def test_a_full_card_length_number_is_refused(lookup):
    """Bin-Tel must never accept something shaped like a card number."""
    with pytest.raises(ValidationError):
        lookup.lookup("4111111111111111")


def test_exists_agrees_with_lookup(lookup, manager):
    digits = _any_bin(manager)
    assert lookup.exists(digits)
    assert not lookup.exists("999999")
