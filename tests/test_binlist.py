"""The binlist.net second opinion: bounded, opt-in, and never authoritative.

No test here touches the network. Every response is scripted through an httpx
mock transport, and the one real call made while building this feature is not
repeated by the suite — five requests an hour is somebody else's allowance to
spend, not a test fixture.

The BINs are synthetic. The bank names are invented.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.errors import NetworkError, ValidationError
from app.providers.binlist import (
    PROVIDER_LICENSE,
    REQUESTS_PER_HOUR,
    BinlistProvider,
    ExternalReading,
    LicenseStatus,
    RateLimited,
    RequestBudget,
    parse_reading,
)

DOCUMENTED_RESPONSE = {
    "number": {"length": 16, "luhn": True},
    "scheme": "visa",
    "type": "debit",
    "brand": "Visa/Dankort",
    "prepaid": False,
    "country": {
        "numeric": "208",
        "alpha2": "DK",
        "name": "Denmark",
        "emoji": "🇩🇰",
        "currency": "DKK",
        "latitude": 56,
        "longitude": 10,
    },
    "bank": {
        "name": "Jyske Bank",
        "url": "www.jyskebank.dk",
        "phone": "+4589893300",
        "city": "Hjørring",
    },
}


def scripted(status: int = 200, payload=None, headers=None, requests=None):
    """A client factory that answers from a script instead of the network."""

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(status, json=payload if payload is not None else {}, headers=headers or {})

    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def budget(tmp_path):
    return RequestBudget(tmp_path / "budget.json")


# ---------------------------------------------------------------------------
# Nothing longer than a BIN leaves the machine
# ---------------------------------------------------------------------------


def test_a_card_length_value_is_truncated_to_a_bin_before_the_request(budget):
    """The safety property: a pasted card number cannot be transmitted."""
    seen: list[httpx.Request] = []
    provider = BinlistProvider(
        budget=budget, client_factory=scripted(200, DOCUMENTED_RESPONSE, requests=seen)
    )
    provider.lookup("4571736012345678")

    assert len(seen) == 1
    path = seen[0].url.path
    assert path == "/45717360"
    assert "4571736012345678" not in str(seen[0].url)


def test_a_query_shorter_than_a_bin_is_refused_without_a_request(budget):
    seen: list[httpx.Request] = []
    provider = BinlistProvider(budget=budget, client_factory=scripted(requests=seen))
    with pytest.raises(ValidationError):
        provider.lookup("4571")
    assert seen == [], "nothing should have been sent"


def test_a_refused_query_does_not_spend_the_allowance(budget):
    provider = BinlistProvider(budget=budget, client_factory=scripted())
    with pytest.raises(ValidationError):
        provider.lookup("12")
    assert provider.remaining() == REQUESTS_PER_HOUR


def test_separators_are_stripped_from_the_query(budget):
    seen: list[httpx.Request] = []
    provider = BinlistProvider(
        budget=budget, client_factory=scripted(200, DOCUMENTED_RESPONSE, requests=seen)
    )
    provider.lookup("4571-7360")
    assert seen[0].url.path == "/45717360"


# ---------------------------------------------------------------------------
# The published allowance, enforced here rather than discovered by being refused
# ---------------------------------------------------------------------------


def test_the_allowance_starts_at_five(budget):
    assert budget.remaining() == REQUESTS_PER_HOUR == 5


def test_the_sixth_request_in_an_hour_is_refused_locally(budget):
    seen: list[httpx.Request] = []
    provider = BinlistProvider(
        budget=budget, client_factory=scripted(200, DOCUMENTED_RESPONSE, requests=seen)
    )
    for _ in range(5):
        provider.lookup("457173")
    assert provider.remaining() == 0

    with pytest.raises(RateLimited):
        provider.lookup("457173")
    assert len(seen) == 5, "the sixth must not reach the service"


def test_the_allowance_survives_a_restart(tmp_path):
    """A limiter that resets with the process is not a limit."""
    path = tmp_path / "budget.json"
    first = RequestBudget(path)
    for _ in range(3):
        first.claim()

    second = RequestBudget(path)
    assert second.remaining() == 2


def test_the_allowance_frees_up_as_the_window_passes(tmp_path):
    budget = RequestBudget(tmp_path / "budget.json", limit=2, window_seconds=100.0)
    budget.claim(now=1_000.0)
    budget.claim(now=1_010.0)
    assert budget.remaining(now=1_020.0) == 0
    assert budget.remaining(now=1_101.0) == 1
    assert budget.remaining(now=1_111.0) == 2


def test_a_corrupt_budget_file_does_not_break_a_lookup(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text("not json at all", encoding="utf-8")
    assert RequestBudget(path).remaining() == REQUESTS_PER_HOUR


def test_the_service_refusing_us_empties_the_local_allowance(budget):
    """A 429 means their accounting wins, so we back off for their window."""
    provider = BinlistProvider(
        budget=budget,
        client_factory=scripted(429, {}, headers={"Retry-After": "1800"}),
    )
    with pytest.raises(RateLimited) as excinfo:
        provider.lookup("457173")
    assert excinfo.value.retry_after_seconds == 1800
    assert provider.remaining() == 0


def test_a_429_without_a_retry_after_backs_off_for_the_whole_window(budget):
    provider = BinlistProvider(budget=budget, client_factory=scripted(429, {}))
    with pytest.raises(RateLimited):
        provider.lookup("457173")
    assert provider.remaining() == 0


# ---------------------------------------------------------------------------
# Reading the documented response
# ---------------------------------------------------------------------------


def test_the_documented_response_is_parsed_field_for_field():
    reading = parse_reading("45717360", DOCUMENTED_RESPONSE)
    assert reading.scheme == "visa"
    assert reading.card_type == "debit"
    assert reading.brand == "Visa/Dankort"
    assert reading.prepaid is False
    assert reading.bank_name == "Jyske Bank"
    assert reading.bank_url == "www.jyskebank.dk"
    assert reading.bank_phone == "+4589893300"
    assert reading.bank_city == "Hjørring"
    assert reading.country_alpha2 == "DK"
    assert reading.country_name == "Denmark"
    assert reading.country_currency == "DKK"


def test_null_fields_stay_absent_rather_than_becoming_answers():
    """Their documentation says any field may be null."""
    reading = parse_reading(
        "457173",
        {"scheme": "visa", "type": None, "bank": {"name": None}, "country": None},
    )
    assert reading.scheme == "visa"
    assert reading.card_type is None
    assert reading.bank_name is None
    assert reading.country_alpha2 is None
    assert not reading.names_an_institution


def test_an_entirely_empty_response_has_no_content():
    assert not parse_reading("457173", {}).has_content


def test_a_404_means_they_have_no_record_not_an_error(budget):
    provider = BinlistProvider(budget=budget, client_factory=scripted(404, {}))
    assert provider.lookup("457173") is None


def test_a_server_error_is_reported_as_retryable(budget):
    provider = BinlistProvider(budget=budget, client_factory=scripted(503, {}))
    with pytest.raises(NetworkError) as excinfo:
        provider.lookup("457173")
    assert excinfo.value.retryable


def test_an_unreachable_service_is_a_network_error_not_a_crash(budget):
    def factory():
        def handler(request):
            raise httpx.ConnectError("no route to host")

        return httpx.Client(transport=httpx.MockTransport(handler))

    provider = BinlistProvider(budget=budget, client_factory=factory)
    with pytest.raises(NetworkError):
        provider.lookup("457173")


def test_a_non_json_response_is_reported_rather_than_guessed_at(budget):
    def factory():
        return httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="<html>maintenance</html>")
            )
        )

    provider = BinlistProvider(budget=budget, client_factory=factory)
    with pytest.raises(NetworkError):
        provider.lookup("457173")


def test_the_documented_api_version_header_is_sent(budget):
    seen: list[httpx.Request] = []
    provider = BinlistProvider(
        budget=budget, client_factory=scripted(200, DOCUMENTED_RESPONSE, requests=seen)
    )
    provider.lookup("457173")
    assert seen[0].headers["Accept-Version"] == "3"


# ---------------------------------------------------------------------------
# A reading is a suggestion, never data
# ---------------------------------------------------------------------------


def test_a_reading_renders_as_a_row_you_could_paste():
    reading = parse_reading("45717360", DOCUMENTED_RESPONSE)
    header, values = reading.as_list_row().splitlines()
    columns = header.split(",")
    assert columns[:2] == ["bin", "bank"]
    assert values.startswith("45717360,Jyske Bank")
    assert "visa" in values


def test_a_reading_naming_no_bank_says_so_in_the_row_rather_than_inventing_one():
    reading = parse_reading("457173", {"scheme": "visa"})
    values = reading.as_list_row().splitlines()[1]
    assert "UNKNOWN" in values


def test_a_reading_lists_where_it_disagrees_with_local_data(monkeypatch):
    class FakeNetwork:
        label = "Visa"

    class FakeCountry:
        iso2 = "US"

    class FakeIssuer:
        display_name = "Cascade Federal Bank"

    class FakeRecord:
        network = FakeNetwork()
        country = FakeCountry()
        card_type = "debit"
        current_issuers = (FakeIssuer(),)

    reading = parse_reading(
        "410000",
        {
            "scheme": "mastercard",
            "type": "credit",
            "country": {"alpha2": "GB"},
            "bank": {"name": "Some Other Bank"},
        },
    )
    differences = reading.differences_from(FakeRecord())
    joined = " ".join(differences)
    assert "Network" in joined and "Visa" in joined and "mastercard" in joined
    assert "Country" in joined
    assert "Issuer" in joined and "Cascade Federal Bank" in joined


def test_agreement_produces_no_differences():
    class FakeNetwork:
        label = "visa"

    class FakeRecord:
        network = FakeNetwork()
        country = None
        card_type = "debit"
        current_issuers = ()

    reading = parse_reading("457173", {"scheme": "visa", "type": "debit"})
    assert reading.differences_from(FakeRecord()) == []


def test_a_missing_local_record_yields_no_differences():
    reading = parse_reading("457173", DOCUMENTED_RESPONSE)
    assert reading.differences_from(None) == []


def test_an_unknown_field_on_either_side_is_not_a_disagreement():
    """Absent is not the same as different."""

    class FakeRecord:
        network = None
        country = None
        card_type = None
        current_issuers = ()

    reading = parse_reading("457173", DOCUMENTED_RESPONSE)
    assert reading.differences_from(FakeRecord()) == []


# ---------------------------------------------------------------------------
# Licensing is recorded honestly
# ---------------------------------------------------------------------------


def test_the_provider_is_marked_as_needing_a_licence_review():
    """No terms of use are published, and binlist/data carries no licence."""
    assert PROVIDER_LICENSE is LicenseStatus.REVIEW_REQUIRED
    assert BinlistProvider.license_status is LicenseStatus.REVIEW_REQUIRED


def test_the_provider_offers_no_bulk_interface():
    """There is no way to spend the allowance on a whole list."""
    public = {name for name in dir(BinlistProvider) if not name.startswith("_")}
    for forbidden in ("lookup_many", "bulk", "enrich", "import_all", "fetch_all"):
        assert forbidden not in public


def test_a_reading_is_not_a_bin_record():
    """Kept a separate type so it cannot be mistaken for Bin-Tel's own data."""
    from app.models.schemas import BinRecord

    assert not issubclass(ExternalReading, BinRecord)


# ---------------------------------------------------------------------------
# Off unless it is switched on
# ---------------------------------------------------------------------------


def test_the_lookup_is_disabled_by_default():
    from app.core.config import Settings

    assert Settings().external.binlist_enabled is False


def test_the_endpoint_must_be_https():
    from app.core.config import ExternalSettings

    with pytest.raises(Exception):
        ExternalSettings(binlist_endpoint="http://lookup.binlist.net")


def test_the_status_line_reports_the_remaining_allowance(budget):
    provider = BinlistProvider(
        budget=budget, client_factory=scripted(200, DOCUMENTED_RESPONSE)
    )
    assert "5 of 5" in provider.status_line()
    provider.lookup("457173")
    assert "4 of 5" in provider.status_line()


def test_the_budget_file_is_written_so_it_can_be_inspected(tmp_path):
    path = tmp_path / "budget.json"
    RequestBudget(path).claim()
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), list)

