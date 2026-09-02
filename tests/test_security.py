"""The guarantees that matter most: nothing sensitive is stored, shown or logged.

Every card-shaped number in this file is invented and is not a valid payment
card. They exist only to prove Bin-Tel refuses and redacts them.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import text

from app.core.errors import ValidationError
from app.core.logging_config import RedactionFilter, redact
from app.utils.validators import validate_bin

# -- input refusal ------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "4111111111111111",
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "5500000000000004",
        "340000000000009",
        "36000000000008",
    ],
)
def test_a_card_length_number_is_never_accepted(value):
    with pytest.raises(ValidationError):
        validate_bin(value)


@pytest.mark.parametrize("value", ["414720", "41472012", "4147 20"])
def test_a_real_bin_length_is_accepted(value):
    assert validate_bin(value).digits.isdigit()


def test_the_lookup_service_refuses_card_length_input(manager):
    from app.repositories.bin_repository import BinRepository
    from app.services.lookup_service import LookupService

    service = LookupService(BinRepository(manager))
    with pytest.raises(ValidationError) as excinfo:
        service.lookup("4111111111111111")
    assert "never a full card number" in str(excinfo.value.message).lower()


# -- redaction ----------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "card 4111111111111111 declined",
        "pan=4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "number 378282246310005 seen",
    ],
)
def test_card_length_runs_are_masked_in_logs(message):
    cleaned = redact(message)
    assert "4111111111111111" not in cleaned
    assert "4111 1111 1111 1111" not in cleaned
    assert "378282246310005" not in cleaned


@pytest.mark.parametrize("bin_value", ["414720", "41472012", "12345678"])
def test_bins_survive_redaction(bin_value):
    assert bin_value in redact(f"Looking up {bin_value} now")


@pytest.mark.parametrize(
    "message",
    [
        "password=hunter2",
        "token: abcdef123456",
        "api_key = 'sk-not-a-real-key'",
        "authorization: Bearer abcdefghijklmnop",
    ],
)
def test_secrets_are_masked_in_logs(message):
    cleaned = redact(message)
    for secret in ("hunter2", "abcdef123456", "sk-not-a-real-key", "abcdefghijklmnop"):
        assert secret not in cleaned


def test_the_redaction_filter_cleans_a_real_log_record():
    filter_ = RedactionFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="processing %s",
        args=("4111111111111111",),
        exc_info=None,
    )
    assert filter_.filter(record)
    assert "4111111111111111" not in record.getMessage()


def test_redaction_leaves_ordinary_text_alone():
    message = "Installed database 2026.01.1 with 5,000 records in 812 ms"
    assert redact(message) == message


# -- the database holds no sensitive columns ----------------------------------

FORBIDDEN_COLUMN_FRAGMENTS = (
    "pan",
    "card_number",
    "cardnumber",
    "cvv",
    "cvc",
    "pin",
    "track",
    "magstripe",
    "cardholder",
    "account_number",
    "password",
    "secret",
    "credential",
)


def test_the_intelligence_schema_has_no_sensitive_columns(manager):
    with manager.session() as session:
        tables = [
            str(name)
            for (name,) in session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        ]
        for table in tables:
            columns = [
                str(row[1])
                for row in session.execute(text(f"PRAGMA table_info('{table}')"))
            ]
            for column in columns:
                assert not _is_sensitive(column), f"{table}.{column}"


def _is_sensitive(column: str) -> bool:
    """Match on whole name segments, so ``pinned`` is not read as ``pin``."""
    lowered = column.lower()
    segments = set(lowered.split("_"))
    if segments & set(FORBIDDEN_COLUMN_FRAGMENTS):
        return True
    return any(
        fragment in lowered
        for fragment in ("card_number", "cardnumber", "cardholder", "account_number")
    )


def test_the_user_data_schema_has_no_sensitive_columns(user_store):
    with user_store.session() as session:
        for table in user_store.tables():
            columns = [
                str(row[1])
                for row in session.execute(text(f"PRAGMA table_info('{table}')"))
            ]
            for column in columns:
                assert not _is_sensitive(column), f"{table}.{column}"


# -- exports ------------------------------------------------------------------


def test_an_export_contains_only_issuer_metadata(manager, tmp_path):
    from app.models.schemas import AdvancedQuery, PageRequest
    from app.repositories.search_repository import SearchRepository
    from app.services.export_service import ExportFormat, ExportService

    page = SearchRepository(manager).search(AdvancedQuery(), PageRequest(page_size=25))
    payload = ExportService().render_rows(list(page.items), ExportFormat.CSV)
    lowered = payload.lower()

    for fragment in FORBIDDEN_COLUMN_FRAGMENTS:
        assert fragment not in lowered


def test_the_result_card_never_offers_a_sources_or_notes_section():
    """The specification forbids these sections on the normal result views."""
    from app.models.schemas import BinRecord

    labels = {
        label.lower()
        for label, _ in BinRecord(id=1, bin="414720").to_field_pairs()
    }
    for forbidden in ("sources", "data sources", "notes", "source"):
        assert forbidden not in labels


# -- Luhn is a format check, never an issuer signal ---------------------------


def test_luhn_is_available_as_a_format_check_only():
    from app.utils.validators import luhn_check_digit, passes_luhn

    # An invented number that satisfies the check digit.
    assert passes_luhn("4111111111111111")
    assert not passes_luhn("4111111111111112")
    assert luhn_check_digit("411111111111111") == 1


@pytest.mark.parametrize("value", ["", "x", "1"])
def test_luhn_declines_unusable_input(value):
    from app.utils.validators import passes_luhn

    assert not passes_luhn(value)


def test_luhn_plays_no_part_in_issuer_resolution():
    """A check digit says nothing about who issued a card."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "luhn" in path.read_text(encoding="utf-8").lower()
        and path.name != "validators.py"
    ]
    assert offenders == [], "Luhn must stay out of the lookup path"
