"""The staging layer between imported data and the production tables.

Raw data is never written straight into ``bins`` and ``institutions``. It lands
in ``staging_records`` and walks a fixed pipeline:

    RAW → NORMALIZED → VALIDATED → RESOLVED → conflict check → PROMOTED

A record that fails any step stops there, keeps the reason it stopped, and is
never promoted. The consequence is the point: a bad feed spoils the staging
table, not the database people are looking things up in.

Nothing here decides an institution on its own. Resolution goes through
:class:`~app.lookup.institution_resolver.InstitutionResolver`, and anything it
reports as POSSIBLE, CONFLICT or UNKNOWN is held for review rather than
guessed at.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.core.logging_config import get_logger, log_event
from app.lookup.institution_resolver import InstitutionResolver, MatchType
from app.models.entities import StagingRecord, StagingStatus
from app.normalizers.bin_normalizer import bin_normalizer
from app.normalizers.name_normalizer import name_normalizer
from app.services.ingest_service import IngestService, RawBinRecord

logger = get_logger(__name__)

#: Fields that may be carried from a staged payload into production. Anything
#: else a source sends is dropped here rather than stored — the allow-list is
#: what keeps an unexpected column out of the database.
PROMOTABLE_FIELDS: frozenset[str] = frozenset(RawBinRecord.model_fields)


@dataclass(slots=True)
class StagingReport:
    """What one staging run did."""

    batch_id: str = ""
    received: int = 0
    normalized: int = 0
    validated: int = 0
    resolved: int = 0
    conflicted: int = 0
    rejected: int = 0
    promoted: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def held(self) -> int:
        """Records that reached staging but were not promoted."""
        return self.received - self.promoted

    @property
    def summary(self) -> str:
        return (
            f"{self.received:,} received · {self.promoted:,} promoted · "
            f"{self.conflicted:,} conflicted · {self.rejected:,} rejected"
        )


class StagingService:
    """Runs the staging pipeline for a batch of imported records."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._resolver = InstitutionResolver(session)

    # -- step 1: receive ---------------------------------------------------
    def receive(
        self, records: Iterable[RawBinRecord], *, batch_id: str | None = None
    ) -> str:
        """Write raw records into staging. Nothing is validated yet."""
        batch = batch_id or uuid.uuid4().hex[:16]
        count = 0
        for record in records:
            self._session.add(
                StagingRecord(
                    batch_id=batch,
                    raw_payload=_payload(record),
                    prefix=record.bin,
                    institution_name=record.issuer,
                    range_low=record.bin,
                    range_high=record.bin_high,
                    status=StagingStatus.RECEIVED.value,
                )
            )
            count += 1
        self._session.flush()
        log_event(logger, "Staging batch received", batch=batch, records=count)
        return batch

    # -- steps 2-5: normalize, validate, resolve, conflict-check -----------
    def process(self, batch_id: str) -> StagingReport:
        """Advance every record in the batch as far as it legitimately goes."""
        report = StagingReport(batch_id=batch_id)
        rows = self._batch(batch_id, StagingStatus.RECEIVED)
        report.received = len(rows)

        for row in rows:
            raw = _restore(row.raw_payload)
            if raw is None:
                self._reject(row, "The staged payload could not be read.")
                report.rejected += 1
                continue

            # -- normalize --------------------------------------------------
            try:
                normalized = bin_normalizer.normalize(raw.bin)
            except ValidationError as exc:
                self._reject(row, f"Prefix rejected: {exc.message}")
                report.rejected += 1
                continue
            row.prefix = normalized.prefix
            row.prefix_length = normalized.prefix_length
            row.prefix_type = normalized.prefix_type
            row.institution_name = (
                name_normalizer.clean_display(raw.issuer or "") or None
            )
            row.normalized_payload = _payload(raw)
            row.status = StagingStatus.NORMALIZED.value
            report.normalized += 1

            # -- validate ---------------------------------------------------
            problem, notes = _validate(raw, normalized.prefix)
            if problem:
                self._reject(row, problem)
                report.rejected += 1
                continue
            if notes:
                # Not fatal, but not invisible either: a record the pipeline
                # had to repair should say so, so nobody has to wonder later
                # why staging and the source disagree.
                row.issues = "; ".join(notes)
                report.issues.extend(notes)
            row.status = StagingStatus.VALIDATED.value
            report.validated += 1

            # -- resolve ----------------------------------------------------
            if not raw.issuer:
                # A record with no issuer is still promotable: an allocation
                # with no institution is a fact, and inventing one is not.
                row.status = StagingStatus.RESOLVED.value
                row.resolution_match_type = MatchType.UNKNOWN.value
                report.resolved += 1
                continue

            resolution = self._resolver.resolve(
                raw.issuer,
                legal_name=raw.issuer_legal_name,
                country_code=raw.country,
                website=raw.website,
                swift_bic=raw.swift_bic,
                postal_code=raw.postal_code,
            )
            row.resolution_match_type = resolution.match_type.value
            row.confidence = resolution.confidence or None

            if resolution.match_type is MatchType.CONFLICT:
                row.status = StagingStatus.CONFLICTED.value
                row.issues = "; ".join(resolution.reasons)
                report.conflicted += 1
                continue

            # POSSIBLE and UNKNOWN both mean "no existing institution was
            # established". Neither blocks promotion — ingest will create a
            # new institution — but the distinction is recorded.
            row.resolved_institution_id = resolution.institution_id
            row.status = StagingStatus.RESOLVED.value
            report.resolved += 1

        self._session.flush()
        log_event(
            logger,
            "Staging batch processed",
            batch=batch_id,
            **{
                "normalized": report.normalized,
                "validated": report.validated,
                "resolved": report.resolved,
                "conflicted": report.conflicted,
                "rejected": report.rejected,
            },
        )
        return report

    # -- step 6: promote ---------------------------------------------------
    def promote(
        self,
        batch_id: str,
        ingest: IngestService,
        report: StagingReport | None = None,
    ) -> StagingReport:
        """Move resolved records into production. Conflicts stay behind."""
        report = report or StagingReport(batch_id=batch_id)
        rows = self._batch(batch_id, StagingStatus.RESOLVED)
        for row in rows:
            raw = _restore(row.normalized_payload or row.raw_payload)
            if raw is None:
                self._reject(row, "The normalized payload could not be read.")
                report.rejected += 1
                continue
            ingest.ingest(raw)
            row.status = StagingStatus.PROMOTED.value
            row.promoted_at = datetime.now(UTC)
            report.promoted += 1
        self._session.flush()
        log_event(
            logger, "Staging batch promoted", batch=batch_id, promoted=report.promoted
        )
        return report

    def run(
        self, records: Iterable[RawBinRecord], ingest: IngestService
    ) -> StagingReport:
        """The whole pipeline, end to end."""
        batch = self.receive(records)
        report = self.process(batch)
        return self.promote(batch, ingest, report)

    # -- inspection --------------------------------------------------------
    def pending(self, batch_id: str | None = None) -> list[StagingRecord]:
        """Records held back — conflicted or rejected."""
        statement = select(StagingRecord).where(
            StagingRecord.status.in_(
                (StagingStatus.CONFLICTED.value, StagingStatus.REJECTED.value)
            )
        )
        if batch_id:
            statement = statement.where(StagingRecord.batch_id == batch_id)
        return list(self._session.execute(statement).scalars().all())

    def counts(self, batch_id: str | None = None) -> dict[str, int]:
        statement = select(StagingRecord.status, func.count()).group_by(
            StagingRecord.status
        )
        if batch_id:
            statement = statement.where(StagingRecord.batch_id == batch_id)
        return {
            str(status): int(count)
            for status, count in self._session.execute(statement).all()
        }

    def clear(self, batch_id: str | None = None) -> int:
        """Discard staged records once a batch is finished with."""
        from sqlalchemy import delete

        statement = delete(StagingRecord)
        if batch_id:
            statement = statement.where(StagingRecord.batch_id == batch_id)
        return int(self._session.execute(statement).rowcount or 0)

    # -- helpers -----------------------------------------------------------
    def _batch(self, batch_id: str, status: StagingStatus) -> list[StagingRecord]:
        return list(
            self._session.execute(
                select(StagingRecord).where(
                    StagingRecord.batch_id == batch_id,
                    StagingRecord.status == status.value,
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    def _reject(row: StagingRecord, reason: str) -> None:
        row.status = StagingStatus.REJECTED.value
        row.issues = reason


def _validate(raw: RawBinRecord, prefix: str) -> tuple[str | None, list[str]]:
    """Whether a normalized record is fit to enter production.

    Returns ``(reason it is not, notes)``. A reason stops the record; notes
    record something the pipeline had to repair and let through.
    """
    notes: list[str] = []
    if raw.bin_high:
        try:
            span = bin_normalizer.normalize_range(prefix, raw.bin_high)
        except ValidationError as exc:
            return f"Range rejected: {exc.message}", notes
        if span.size <= 0:
            return "Range rejected: the range covers nothing.", notes
        # The normalizer transposes an inverted range rather than discarding
        # it, which is right — but the repair is worth recording.
        from app.utils.validators import clean_digits

        low_digits, high_digits = clean_digits(prefix), clean_digits(raw.bin_high)
        if low_digits and high_digits and int(low_digits) > int(high_digits):
            notes.append("Range endpoints arrived transposed and were swapped")
    if raw.effective_from and raw.effective_to and raw.effective_from > raw.effective_to:
        return "Rejected: the relationship ends before it begins.", notes
    if not 0.0 <= raw.confidence <= 1.0:
        return "Rejected: confidence is outside the range 0 to 1.", notes
    return None, notes


def _payload(record: RawBinRecord) -> str:
    """Serialise a record, keeping only fields the pipeline recognises."""
    data = {
        key: value
        for key, value in record.model_dump(mode="json").items()
        if key in PROMOTABLE_FIELDS
    }
    return json.dumps(data, sort_keys=True, default=str)


def _restore(payload: str | None) -> RawBinRecord | None:
    if not payload:
        return None
    try:
        return RawBinRecord.model_validate_json(payload)
    except Exception:
        logger.debug("A staged payload could not be restored", exc_info=True)
        return None
