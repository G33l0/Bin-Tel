"""Learning: what is proposed, what is authorized, and what is written.

Nothing here contacts a network. The one external source is a stub, which is
the point: the authorization gate has to be provable without a live service.

Every BIN and institution is synthetic.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models.entities import (
    Bin,
    BinClaim,
    LearnedFact,
    LearnedStatus,
    NormalizationEvent,
    SourceRow,
)
from app.services.learning_service import (
    Authorization,
    LearningService,
    Proposal,
)


@pytest.fixture
def session(manager):
    with manager.transaction() as session:
        yield session


@pytest.fixture
def open_authorization():
    return Authorization(
        enabled=True,
        authorized_sources=["stub-source", "binlist.net"],
    )


def a_proposal(**overrides) -> Proposal:
    values = {
        "subject_type": "bin",
        "subject_key": "410000",
        "field": "brand",
        "proposed_value": "Cascade Signature",
        "source_code": "stub-source",
        "evidence": "the stub says so",
        "licence": "verified",
        "confidence": 0.8,
    }
    values.update(overrides)
    return Proposal(**values)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_nothing_is_authorized_by_default():
    """There is no default-on source, and none that adds itself."""
    auth = Authorization.from_settings(Settings())
    assert not auth.enabled
    assert not auth.is_authorized("binlist.net")
    assert not auth.is_authorized("anything-at-all")


def test_a_source_is_authorized_only_by_being_named():
    settings = Settings()
    settings.learning.enabled = True
    settings.learning.authorized_sources = ["binlist.net"]
    auth = Authorization.from_settings(settings)
    assert auth.is_authorized("binlist.net")
    assert not auth.is_authorized("some-other-service")


def test_enabling_learning_without_naming_a_source_authorizes_nothing():
    settings = Settings()
    settings.learning.enabled = True
    auth = Authorization.from_settings(settings)
    assert not auth.is_authorized("binlist.net")


def test_auto_apply_never_covers_a_contradiction():
    """Overruling a curated value waits for a person, whatever the source."""
    auth = Authorization(
        enabled=True,
        authorized_sources=["stub-source"],
        auto_apply_new_information=True,
    )
    assert auth.may_auto_apply(a_proposal())
    assert not auth.may_auto_apply(a_proposal(current_value="Cascade Classic"))


def test_auto_apply_never_covers_an_unsettled_licence():
    auth = Authorization(
        enabled=True,
        authorized_sources=["binlist.net"],
        auto_apply_new_information=True,
    )
    assert not auth.may_auto_apply(a_proposal(licence="review_required"))
    assert not auth.may_auto_apply(a_proposal(licence="unknown"))


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


def test_a_proposal_changes_nothing_until_it_is_applied(session):
    record = session.execute(select(Bin)).scalars().first()
    before = record.brand

    service = LearningService(session)
    service.record([a_proposal(subject_key=record.bin, current_value=before)])

    session.expire_all()
    assert session.execute(select(Bin).where(Bin.bin == record.bin)).scalar_one().brand == before
    fact = session.execute(select(LearnedFact)).scalars().one()
    assert fact.status == LearnedStatus.PENDING.value


def test_a_proposal_matching_what_is_held_is_not_recorded(session):
    service = LearningService(session)
    report = service.record(
        [a_proposal(current_value="Cascade Signature", proposed_value="Cascade Signature")]
    )
    assert report.proposed == 0


def test_a_decided_proposal_is_not_raised_again(session):
    """Re-proposing a rejection every pass trains people to click through."""
    service = LearningService(session)
    service.record([a_proposal()])
    fact = session.execute(select(LearnedFact)).scalars().one()
    service.reject(fact.id, "no")

    again = service.record([a_proposal()])
    assert again.proposed == 0
    assert again.duplicates == 1
    assert session.execute(select(LearnedFact)).scalars().one().status == (
        LearnedStatus.REJECTED.value
    )


def test_a_newer_proposal_supersedes_the_pending_one_it_replaces(session):
    service = LearningService(session)
    service.record([a_proposal(proposed_value="First")])
    report = service.record([a_proposal(proposed_value="Second")])
    assert report.superseded == 1

    statuses = {
        fact.proposed_value: fact.status
        for fact in session.execute(select(LearnedFact)).scalars()
    }
    assert statuses == {
        "First": LearnedStatus.SUPERSEDED.value,
        "Second": LearnedStatus.PENDING.value,
    }


def test_a_fact_knows_whether_it_fills_a_blank_or_contradicts(session):
    service = LearningService(session)
    service.record(
        [
            a_proposal(field="brand", proposed_value="New"),
            a_proposal(field="card_level", proposed_value="Gold", current_value="Classic"),
        ]
    )
    by_field = {
        fact.field: fact.is_new_information
        for fact in session.execute(select(LearnedFact)).scalars()
    }
    assert by_field == {"brand": True, "card_level": False}


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def test_an_approved_fact_is_written_with_provenance(session):
    record = session.execute(select(Bin)).scalars().first()
    service = LearningService(session)
    service.record(
        [a_proposal(subject_key=record.bin, field="card_level", proposed_value="Platinum")]
    )
    fact = session.execute(select(LearnedFact)).scalars().one()
    service.approve(fact.id, "checked against the issuer's own page")
    report = service.apply_approved()

    assert report.applied == 1
    session.expire_all()
    assert session.execute(select(Bin).where(Bin.bin == record.bin)).scalar_one().card_level == (
        "Platinum"
    )

    event = session.execute(
        select(NormalizationEvent).where(NormalizationEvent.field == "card_level")
    ).scalars().first()
    assert event is not None
    assert event.rule == "learned:stub-source"

    claim = session.execute(
        select(BinClaim).where(BinClaim.field == "card_level")
    ).scalars().first()
    assert claim is not None
    assert claim.value == "Platinum"


def test_a_fact_cannot_write_a_field_that_decides_what_a_lookup_matches(session):
    """A source may describe a BIN. It may never move one."""
    record = session.execute(select(Bin)).scalars().first()
    original = record.prefix_length

    service = LearningService(session)
    service.record(
        [a_proposal(subject_key=record.bin, field="prefix_length", proposed_value="8")]
    )
    fact = session.execute(select(LearnedFact)).scalars().one()
    service.approve(fact.id)
    report = service.apply_approved()

    assert report.applied == 0
    session.expire_all()
    assert session.execute(
        select(Bin).where(Bin.bin == record.bin)
    ).scalar_one().prefix_length == original


def test_a_fact_about_a_bin_that_no_longer_exists_is_not_applied(session):
    service = LearningService(session)
    service.record([a_proposal(subject_key="999999", field="brand")])
    fact = session.execute(select(LearnedFact)).scalars().one()
    service.approve(fact.id)
    assert service.apply_approved().applied == 0


# ---------------------------------------------------------------------------
# An external source
# ---------------------------------------------------------------------------


class StubReading:
    scheme = "mastercard"
    brand = "Stub World"
    card_type = "credit"
    country_alpha2 = "DE"
    country_currency = "EUR"
    confidence = 0.5


class StubProvider:
    """Stands in for a service outside this machine. Counts its own calls."""

    SOURCE_CODE = "stub-source"
    licence = "verified"
    reference = "https://example.invalid/"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def lookup(self, digits: str):
        self.calls.append(digits)
        return StubReading()


def test_an_unauthorized_source_is_never_contacted(session):
    """Not "its answer is ignored" — no request is made at all."""
    record = session.execute(select(Bin)).scalars().first()
    provider = StubProvider()
    service = LearningService(session, Authorization(enabled=True, authorized_sources=[]))

    proposals = service.gather_external(provider, [record.bin])

    assert proposals == []
    assert provider.calls == []


def test_an_authorized_source_produces_proposals_and_writes_nothing(
    session, open_authorization
):
    record = session.execute(select(Bin)).scalars().first()
    before = record.brand
    provider = StubProvider()
    service = LearningService(session, open_authorization)

    proposals = service.gather_external(provider, [record.bin])

    assert provider.calls == [record.bin]
    assert proposals
    assert all(item.source_code == "stub-source" for item in proposals)
    session.expire_all()
    assert session.execute(select(Bin).where(Bin.bin == record.bin)).scalar_one().brand == before


def test_a_source_licence_travels_onto_every_fact_it_produced(session):
    """An unsettled licence is recorded against the value, not forgotten."""
    service = LearningService(session)
    service.record([a_proposal(source_code="binlist.net", licence="review_required")])
    fact = session.execute(select(LearnedFact)).scalars().one()
    assert fact.licence == "review_required"


# ---------------------------------------------------------------------------
# Nothing a source said is reduced away
# ---------------------------------------------------------------------------


def test_every_source_column_survives_including_the_ones_not_asserted(tmp_path, manager):
    """The curated columns are narrower than the row. Narrower is not lossy."""
    from app.services.bin_list import read_bin_list
    from app.services.ingest_service import IngestService

    path = tmp_path / "bin-list.csv"
    path.write_text(
        "bin,issuer,alpha_2,alpha_3,country,latitude,longitude\n"
        "410000,Cascade Bank,US,USA,United States,37.0902,-95.7129\n",
        encoding="utf-8",
    )
    report = read_bin_list(path)

    with manager.transaction() as session:
        ingest = IngestService(session, source_code="test", source_name="Test")
        for record in report.records:
            ingest.ingest(record)

    with manager.session() as session:
        row = session.execute(
            select(SourceRow).where(SourceRow.source_file == "bin-list.csv")
        ).scalars().one()
        payload = json.loads(row.payload)

    # The coordinates are never asserted as the bank's address, and are still
    # here in full; so is the country spelled all three ways.
    assert payload["latitude"] == "37.0902"
    assert payload["longitude"] == "-95.7129"
    assert payload["alpha_2"] == "US"
    assert payload["alpha_3"] == "USA"
    assert payload["country"] == "United States"
    assert row.line_number == 2


def test_the_archived_row_keeps_a_value_the_reader_refused(tmp_path, manager):
    """The mangled phone is dropped from the record and kept in the row."""
    from app.services.bin_list import read_bin_list
    from app.services.ingest_service import IngestService

    path = tmp_path / "bin-list.csv"
    path.write_text(
        "bin,issuer,phone\n410000,Cascade Bank,5.51732E+11\n", encoding="utf-8"
    )
    report = read_bin_list(path)
    assert report.records[0].phone is None

    with manager.transaction() as session:
        ingest = IngestService(session, source_code="test", source_name="Test")
        ingest.ingest(report.records[0])

    with manager.session() as session:
        row = session.execute(select(SourceRow)).scalars().one()
        assert json.loads(row.payload)["phone"] == "5.51732E+11"


def test_the_archive_records_a_file_name_never_a_path(tmp_path, manager):
    """A database meant to be portable must not carry a home directory."""
    from app.services.bin_list import read_bin_list
    from app.services.ingest_service import IngestService

    path = tmp_path / "bin-list.csv"
    path.write_text("bin,issuer\n410000,Cascade Bank\n", encoding="utf-8")
    report = read_bin_list(path)

    with manager.transaction() as session:
        IngestService(session, source_code="test", source_name="Test").ingest(
            report.records[0]
        )

    with manager.session() as session:
        row = session.execute(select(SourceRow)).scalars().one()
        assert row.source_file == "bin-list.csv"
        assert str(tmp_path) not in (row.source_file or "")


# ---------------------------------------------------------------------------
# The review surface
# ---------------------------------------------------------------------------


def test_the_review_card_stays_hidden_until_something_is_waiting(qtbot, context):
    from app.ui.pages.database_page import DatabasePage

    page = DatabasePage(context)
    qtbot.addWidget(page)
    page.refresh_learned()
    assert page.learned_card.isHidden()


def test_the_review_card_marks_a_contradiction_apart_from_a_gap(qtbot, context):
    """Filling a blank and overruling a curated value are different decisions."""
    from sqlalchemy import select

    from app.models.entities import Bin
    from app.services.learning_service import LearningService
    from app.ui.pages.database_page import DatabasePage

    with context.manager.transaction() as session:
        digits = session.execute(select(Bin.bin)).scalars().first()
        LearningService(session).record(
            [
                a_proposal(subject_key=digits, field="card_level", proposed_value="Gold"),
                a_proposal(
                    subject_key=digits,
                    field="brand",
                    proposed_value="Something Else",
                    current_value="Held Value",
                ),
            ]
        )

    page = DatabasePage(context)
    qtbot.addWidget(page)
    page.refresh_learned()

    assert not page.learned_card.isHidden()
    text = page.learned_label.text()
    assert "would overrule something you hold" in text
    assert "Held Value → Something Else" in text
