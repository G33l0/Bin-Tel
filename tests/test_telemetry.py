"""Telemetry: opt-in, the sanitiser, the queue and failed uploads.

The payloads here deliberately contain the sorts of values that must never
leave a machine, to prove the sanitiser drops them. The card-shaped numbers
are made up and are not valid payment cards.
"""

from __future__ import annotations

import pytest

from app.telemetry.events import Counter, Event, bucket, size_bucket
from app.telemetry.service import TelemetryService, sanitise


@pytest.fixture
def telemetry(user_store):
    service = TelemetryService(user_store, enabled=True, endpoint="")
    service.set_context(database_version="2026.01.1", plan="free")
    return service


# -- opt-in -------------------------------------------------------------------


def test_nothing_is_recorded_while_telemetry_is_off(user_store):
    service = TelemetryService(user_store, enabled=False, endpoint="")
    assert not service.record(Event.APP_STARTED, {"first_run": True})
    assert service.queue_size() == 0


def test_turning_telemetry_off_clears_what_was_queued(telemetry):
    telemetry.record(Event.APP_STARTED, {"first_run": True})
    assert telemetry.queue_size() == 1

    telemetry.set_enabled(False)
    assert telemetry.queue_size() == 0
    assert not telemetry.enabled


def test_turning_it_on_again_starts_from_empty(telemetry):
    telemetry.record(Event.APP_STARTED, {"first_run": False})
    telemetry.set_enabled(False)
    telemetry.set_enabled(True)
    assert telemetry.queue_size() == 0


# -- the sanitiser ------------------------------------------------------------


def test_an_allow_listed_payload_survives():
    clean = sanitise(Event.APP_STARTED, {"first_run": True, "startup_ms": 1200})
    assert clean == {"first_run": True, "startup_ms": 1200}


def test_keys_the_event_does_not_declare_are_dropped():
    clean = sanitise(Event.APP_STARTED, {"first_run": True, "hostname": "laptop"})
    assert clean == {"first_run": True}


@pytest.mark.parametrize(
    "payload",
    [
        {"bin": "414720"},
        {"iin": "41472012"},
        {"pan": "4111111111111111"},
        {"card_number": "4111 1111 1111 1111"},
        {"cvv": "123"},
        {"pin": "1234"},
        {"cardholder_name": "A Person"},
        {"query": "Meridian Trust"},
        {"search_term": "Meridian Trust"},
        {"email": "someone@example.invalid"},
        {"path": "/home/someone/bintel.sqlite"},
        {"license_key": "SOME-KEY"},
        {"ip": "203.0.113.4"},
    ],
)
def test_forbidden_keys_never_survive_sanitising(payload):
    for event in Event:
        assert sanitise(event, payload) == {}


def test_a_long_numeric_string_is_refused_even_on_an_allowed_key():
    # 16 digits on an otherwise legitimate key still looks like an account.
    clean = sanitise(Event.DATABASE_UPDATED, {"to_version": "4111111111111111"})
    assert clean == {}


def test_non_scalar_values_are_dropped():
    clean = sanitise(
        Event.DATABASE_UPDATED,
        {"to_version": "2026.02.1", "migrated": {"nested": "object"}},
    )
    assert clean == {"to_version": "2026.02.1"}


def test_an_over_long_string_is_dropped():
    clean = sanitise(Event.FEATURE_USED, {"feature": "x" * 5000})
    assert clean == {}


def test_recorded_events_only_carry_sanitised_payloads(telemetry):
    telemetry.record(
        Event.FEATURE_USED,
        {"feature": "advanced_search", "query": "Meridian Trust", "bin": "414720"},
    )
    (row,) = telemetry.queued_events()
    import json

    payload = json.loads(row.payload or "{}")
    assert payload == {"feature": "advanced_search"}
    assert "414720" not in (row.payload or "")


def test_an_unknown_event_name_is_not_recorded(telemetry):
    assert not telemetry.record("something_invented", {"a": 1})
    assert telemetry.queue_size() == 0


# -- buckets ------------------------------------------------------------------


def test_counts_are_bucketed_rather_than_exact():
    assert bucket(0) != bucket(5000)
    assert bucket(7) == bucket(8)
    assert size_bucket(1024) != size_bucket(5 * 1024 * 1024 * 1024)


# -- counters -----------------------------------------------------------------


def test_counters_aggregate_without_recording_values(telemetry):
    for _ in range(3):
        telemetry.increment(Counter.BIN_LOOKUP_COUNT)
    counters = telemetry.counters()
    assert counters[Counter.BIN_LOOKUP_COUNT.value] == 3


def test_counters_stay_local_while_telemetry_is_off(user_store):
    """Counters drive the user's own usage summary, so they keep counting --
    but with telemetry off there is nothing to upload them with."""
    service = TelemetryService(user_store, enabled=False, endpoint="https://x.invalid")
    service.increment(Counter.BIN_LOOKUP_COUNT)

    assert service.counters()[Counter.BIN_LOOKUP_COUNT.value] == 1
    assert service.queue_size() == 0
    assert service.flush() == 0


# -- uploads ------------------------------------------------------------------


def test_a_failed_upload_leaves_the_events_queued(telemetry):
    telemetry.set_endpoint("https://telemetry.invalid/v1/events")
    telemetry.record(Event.APP_STARTED, {"first_run": True})
    assert telemetry.queue_size() == 1

    sent = telemetry.flush()
    assert sent == 0
    assert telemetry.queue_size() == 1, "an unreachable endpoint must not lose events"


def test_flushing_without_an_endpoint_is_a_no_op(telemetry):
    telemetry.record(Event.APP_STARTED, {"first_run": True})
    assert telemetry.flush() == 0
    assert telemetry.queue_size() == 1


def test_the_queue_is_capped(telemetry):
    from app.telemetry.service import MAX_QUEUED_EVENTS

    for _ in range(MAX_QUEUED_EVENTS + 25):
        telemetry.record(Event.FEATURE_USED, {"feature": "bin_lookup"})
    assert telemetry.queue_size() <= MAX_QUEUED_EVENTS


def test_the_install_id_is_random_and_not_derived_from_the_machine(telemetry, user_store):
    import platform
    import socket

    install_id = telemetry.install_id
    assert install_id
    assert len(install_id) >= 16
    assert socket.gethostname() not in install_id
    assert platform.node() not in install_id


def test_the_disclosure_lists_what_is_and_is_not_collected(telemetry):
    collected = " ".join(telemetry.describe_collection()).lower()
    excluded = " ".join(telemetry.describe_exclusions()).lower()
    assert collected
    assert "card" in excluded
    assert "search" in excluded
