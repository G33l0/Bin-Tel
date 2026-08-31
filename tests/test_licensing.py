"""Licensing: signatures, plan states, device limits, grace and tampering.

Every licence here is issued by a throwaway key pair created in the test. No
real licence key, customer name or payment detail appears anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.licensing.activation import (
    ActivationRejected,
    ActivationService,
    DeviceLimitReached,
    LocalLicenseServer,
)
from app.licensing.devices import DeviceManager
from app.licensing.entitlements import EntitlementService
from app.licensing.license_manager import LicenseManager
from app.licensing.models import LicensePayload, LicenseState, LicenseToken
from app.licensing.plans import Feature, Limit, Plan, PlanCatalogue
from app.licensing.signing import generate_secret_key, public_key


@pytest.fixture
def server(tmp_path):
    return LocalLicenseServer(tmp_path / "licensing")


@pytest.fixture
def devices(user_store):
    return DeviceManager(user_store)


@pytest.fixture
def licenses(user_store, server, devices):
    return LicenseManager(user_store, ActivationService(server, devices), devices)


@pytest.fixture
def entitlements(licenses):
    """Wired the way the application context wires it."""
    service = EntitlementService(licenses, PlanCatalogue())
    licenses.set_change_listener(lambda _snapshot: service.notify())
    return service


# -- signing ------------------------------------------------------------------


def test_a_token_only_verifies_against_its_own_key():
    secret = generate_secret_key()
    payload = LicensePayload(
        license_id="TEST-1", plan="pro", issued_at=datetime.now(UTC), device_id="abc"
    )
    token = LicenseToken.issue(payload, secret)

    assert LicenseToken.parse(token.raw, public_key(secret)) is not None
    assert LicenseToken.parse(token.raw, public_key(generate_secret_key())) is None


def test_a_tampered_payload_fails_verification():
    secret = generate_secret_key()
    payload = LicensePayload(
        license_id="TEST-2", plan="free", issued_at=datetime.now(UTC), device_id="abc"
    )
    token = LicenseToken.issue(payload, secret)

    prefix, body, signature = token.raw.split(".")
    forged = LicensePayload(
        license_id="TEST-2",
        plan="enterprise",
        issued_at=payload.issued_at,
        device_id="abc",
    )
    from app.licensing.signing import b64encode

    forged_raw = f"{prefix}.{b64encode(forged.model_dump_json().encode())}.{signature}"
    assert LicenseToken.parse(forged_raw, public_key(secret)) is None


def test_a_malformed_token_is_rejected_rather_than_raising():
    key = public_key(generate_secret_key())
    for value in ("", "nonsense", "bintel.only-two-parts", "a.b.c"):
        assert LicenseToken.parse(value, key) is None


# -- activation ---------------------------------------------------------------


def test_the_free_plan_is_the_starting_point(licenses, entitlements):
    snapshot = licenses.load()
    assert snapshot.plan is Plan.FREE
    assert snapshot.state is LicenseState.FREE
    assert not snapshot.is_activated
    assert not entitlements.has_feature(Feature.ADVANCED_SEARCH)
    assert entitlements.has_feature(Feature.BIN_LOOKUP)


@pytest.mark.parametrize(
    ("key", "plan"),
    [
        ("BINTEL-DEV-PRO", Plan.PRO),
        ("BINTEL-DEV-BUSINESS", Plan.BUSINESS),
        ("BINTEL-DEV-ENTERPRISE", Plan.ENTERPRISE),
    ],
)
def test_each_plan_activates_and_grants_its_features(licenses, entitlements, key, plan):
    snapshot = licenses.activate(key)
    assert snapshot.plan is plan
    assert snapshot.is_activated

    catalogue = PlanCatalogue().get(plan)
    for feature in catalogue.features:
        assert entitlements.has_feature(feature), feature


def test_an_unknown_key_is_declined(licenses):
    with pytest.raises(ActivationRejected):
        licenses.activate("NOT-A-REAL-KEY")
    assert licenses.snapshot.plan is Plan.FREE


def test_deactivation_returns_to_free(licenses, entitlements):
    licenses.activate("BINTEL-DEV-PRO")
    assert entitlements.has_feature(Feature.ADVANCED_SEARCH)

    licenses.deactivate()
    assert licenses.snapshot.plan is Plan.FREE
    assert not entitlements.has_feature(Feature.ADVANCED_SEARCH)


def test_the_device_limit_is_enforced(server, tmp_path):
    """Pro covers three devices; the fourth installation is turned away."""
    from app.database.user_store import UserDataStore

    limit = PlanCatalogue().get(Plan.PRO).limit(Limit.DEVICES, 1)
    assert limit > 0

    stores = []
    try:
        for index in range(limit + 1):
            store = UserDataStore(tmp_path / f"device-{index}.sqlite")
            store.open()
            stores.append(store)
            service = ActivationService(server, DeviceManager(store))
            if index < limit:
                assert service.activate("BINTEL-DEV-PRO").payload.plan == "pro"
            else:
                with pytest.raises(DeviceLimitReached):
                    service.activate("BINTEL-DEV-PRO")
    finally:
        for store in stores:
            store.close()


def test_a_licence_issued_for_another_device_is_invalid(licenses, user_store, devices):
    licenses.activate("BINTEL-DEV-PRO")
    assert licenses.snapshot.plan is Plan.PRO

    # Simulate the licence being copied to a different installation.
    from app.licensing.devices import DEVICE_ID_KEY

    user_store.set_metadata(DEVICE_ID_KEY, "a" * 32)
    reloaded = licenses.load()
    assert reloaded.state is LicenseState.INVALID
    assert licenses.plan is Plan.FREE, "a copied licence grants nothing"


# -- state derivation ---------------------------------------------------------


def _install(user_store, devices, secret, *, last_validated_at=None, **payload_kwargs):
    """Store a signed token directly, so states can be set up precisely."""
    from app.models.user_entities import LicenseRecord

    defaults = dict(
        license_id="TEST",
        plan="pro",
        issued_at=datetime.now(UTC) - timedelta(days=1),
        device_id=devices.device_id,
        grace_days=14,
    )
    defaults.update(payload_kwargs)
    token = LicenseToken.issue(LicensePayload(**defaults), secret)
    _store_record(
        user_store,
        token=token.raw,
        license_key="TEST-KEY",
        plan=token.payload.plan,
        license_id=token.payload.license_id,
        issued_at=token.payload.issued_at,
        expires_at=token.payload.expires_at,
        last_validated_at=last_validated_at or datetime.now(UTC),
    )
    return token


def _store_record(user_store, **fields):
    """Write (or overwrite) the single stored licence row."""
    from sqlalchemy import delete

    from app.models.user_entities import LicenseRecord

    with user_store.transaction() as session:
        session.execute(delete(LicenseRecord))
        session.add(LicenseRecord(**fields))


def _read_record(user_store):
    from sqlalchemy import select

    from app.models.user_entities import LicenseRecord

    with user_store.session() as session:
        record = session.execute(
            select(LicenseRecord).order_by(LicenseRecord.id.desc()).limit(1)
        ).scalar_one()
        session.expunge(record)
        return record


def test_an_expired_licence_falls_back_to_free(user_store, devices):
    secret = generate_secret_key()
    manager = LicenseManager(
        user_store, _service_with_key(secret, devices), devices
    )
    _install(
        user_store,
        devices,
        secret,
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    snapshot = manager.load()
    assert snapshot.state is LicenseState.EXPIRED
    assert manager.plan is Plan.FREE


def test_a_suspended_licence_falls_back_to_free(user_store, devices):
    secret = generate_secret_key()
    manager = LicenseManager(user_store, _service_with_key(secret, devices), devices)
    _install(user_store, devices, secret, status="suspended")

    snapshot = manager.load()
    assert snapshot.state is LicenseState.SUSPENDED
    assert manager.plan is Plan.FREE


def test_a_stale_but_in_window_licence_keeps_working_in_offline_grace(
    user_store, devices
):
    secret = generate_secret_key()
    manager = LicenseManager(user_store, _service_with_key(secret, devices), devices)
    _install(
        user_store,
        devices,
        secret,
        expires_at=datetime.now(UTC) + timedelta(days=200),
        last_validated_at=datetime.now(UTC) - timedelta(days=10),
    )

    snapshot = manager.load()
    assert snapshot.state is LicenseState.OFFLINE_GRACE
    assert manager.plan is Plan.PRO, "the plan must survive a temporary outage"


def test_a_licence_past_its_grace_window_falls_back_to_free(user_store, devices):
    secret = generate_secret_key()
    manager = LicenseManager(user_store, _service_with_key(secret, devices), devices)
    _install(
        user_store,
        devices,
        secret,
        expires_at=datetime.now(UTC) + timedelta(days=200),
        last_validated_at=datetime.now(UTC) - timedelta(days=30),
    )

    snapshot = manager.load()
    assert snapshot.state is LicenseState.EXPIRED
    assert manager.plan is Plan.FREE


def _service_with_key(secret, devices):
    """An activation service that verifies against *secret*'s public key."""

    class _Client:
        available = True
        verifying_key = public_key(secret)

        def activate(self, license_key, device):  # pragma: no cover - unused
            raise ActivationRejected("offline")

        def validate(self, token, device):
            from app.core.errors import OfflineError

            raise OfflineError("no service in this test")

        def deactivate(self, token, device):  # pragma: no cover - unused
            return True

        def devices(self, token):  # pragma: no cover - unused
            return []

    return ActivationService(_Client(), devices)


# -- tamper resistance --------------------------------------------------------


def test_editing_the_stored_plan_cannot_promote_the_licence(user_store, devices):
    secret = generate_secret_key()
    manager = LicenseManager(user_store, _service_with_key(secret, devices), devices)
    _install(user_store, devices, secret, plan="pro")
    assert manager.load().plan is Plan.PRO

    # Rewrite only the unsigned convenience column.
    record = _read_record(user_store)
    _store_record(
        user_store,
        token=record.token,
        license_key=record.license_key,
        plan="enterprise",
        license_id=record.license_id,
        issued_at=record.issued_at,
        expires_at=record.expires_at,
        last_validated_at=record.last_validated_at,
    )

    assert manager.load().plan is Plan.PRO, "the signature decides the plan"


def test_a_corrupted_token_drops_back_to_free(user_store, devices):
    secret = generate_secret_key()
    manager = LicenseManager(user_store, _service_with_key(secret, devices), devices)
    token = _install(user_store, devices, secret)

    record = _read_record(user_store)
    _store_record(
        user_store,
        token=token.raw[:-6] + "AAAAAA",
        license_key=record.license_key,
        plan=record.plan,
        license_id=record.license_id,
        issued_at=record.issued_at,
        expires_at=record.expires_at,
        last_validated_at=record.last_validated_at,
    )

    snapshot = manager.load()
    assert snapshot.plan is Plan.FREE
    assert snapshot.state is LicenseState.INVALID


# -- entitlements -------------------------------------------------------------


def test_limits_come_from_the_plan(licenses, entitlements):
    assert entitlements.limit(Limit.EXPORT_ROWS, 0) == 500
    assert entitlements.limit(Limit.WATCHLISTS, 0) == 0

    licenses.activate("BINTEL-DEV-BUSINESS")
    assert entitlements.limit(Limit.EXPORT_ROWS, 0) != 500


def test_within_limit_respects_unlimited(licenses, entitlements):
    licenses.activate("BINTEL-DEV-ENTERPRISE")
    assert entitlements.within_limit(Limit.WATCHLISTS, 10_000)


def test_an_entitlement_names_the_plan_that_unlocks_it(entitlements):
    entitlement = entitlements.entitlement(Feature.ADVANCED_ANALYTICS)
    assert not entitlement.granted
    assert entitlement.required_plan is not None
    assert entitlement.required_plan is not Plan.FREE


def test_subscribers_hear_about_a_plan_change(licenses, entitlements):
    seen: list[Plan] = []
    entitlements.subscribe(lambda: seen.append(licenses.snapshot.plan))

    licenses.activate("BINTEL-DEV-PRO")
    assert Plan.PRO in seen
