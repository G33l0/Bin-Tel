"""Deduplication.

The rule this service is built around: *similar is not the same*. A merge
requires a high name score **and** corroborating evidence (a shared BIN, a
matching SWIFT/BIC, the same website host, or two weaker signals agreeing).
Anything below that is reported as a candidate for review, never merged, and
genuine disagreements are written to ``conflicts`` so no information is lost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.models.entities import (
    Address,
    Bin,
    BinInstitution,
    BinRange,
    Conflict,
    ConflictStatus,
    Institution,
    InstitutionAlias,
)
from app.normalizers.confidence import MatchScore
from app.normalizers.name_normalizer import name_normalizer
from app.normalizers.text import squash

logger = get_logger(__name__)


@dataclass(slots=True)
class MergeCandidate:
    keep_id: int
    merge_id: int
    keep_name: str
    merge_name: str
    score: float
    reason: str

    @property
    def label(self) -> str:
        return f"{self.keep_name} ← {self.merge_name} ({self.score:.2f})"


@dataclass(slots=True)
class DedupeReport:
    """What a deduplication pass found and did."""

    scanned_institutions: int = 0
    merged_institutions: int = 0
    duplicate_aliases_removed: int = 0
    duplicate_addresses_removed: int = 0
    duplicate_links_removed: int = 0
    range_conflicts_recorded: int = 0
    duplicate_bins: int = 0
    review_candidates: list[MergeCandidate] = field(default_factory=list)
    merged: list[MergeCandidate] = field(default_factory=list)
    conflicts_recorded: int = 0

    @property
    def summary(self) -> str:
        return (
            f"{self.scanned_institutions:,} institutions scanned · "
            f"{self.merged_institutions:,} merged · "
            f"{len(self.review_candidates):,} awaiting review · "
            f"{self.duplicate_aliases_removed + self.duplicate_addresses_removed + self.duplicate_links_removed:,} "
            "duplicate rows removed"
        )


class DedupeService:
    """Detects and (where defensible) resolves duplicate records."""

    def __init__(self, session: Session, *, dry_run: bool = False) -> None:
        self._session = session
        self._dry_run = dry_run

    # -- entry point ------------------------------------------------------
    def run(self, *, merge: bool = True) -> DedupeReport:
        report = DedupeReport()
        self.deduplicate_aliases(report)
        self.deduplicate_addresses(report)
        self.deduplicate_links(report)
        self.find_duplicate_bins(report)
        self.find_range_conflicts(report)
        self.deduplicate_institutions(report, merge=merge)
        if not self._dry_run:
            self._session.flush()
        logger.info("Deduplication pass complete", extra={"context": {"summary": report.summary}})
        return report

    # -- exact duplicates --------------------------------------------------
    def deduplicate_aliases(self, report: DedupeReport) -> int:
        """Collapse identical aliases on the same institution."""
        duplicates = self._session.execute(
            select(
                InstitutionAlias.institution_id,
                InstitutionAlias.normalized_alias,
                func.min(InstitutionAlias.id),
                func.count(InstitutionAlias.id),
            )
            .group_by(InstitutionAlias.institution_id, InstitutionAlias.normalized_alias)
            .having(func.count(InstitutionAlias.id) > 1)
        ).all()
        removed = 0
        for institution_id, normalized, keep_id, count in duplicates:
            removed += int(count) - 1
            if not self._dry_run:
                self._session.execute(
                    delete(InstitutionAlias).where(
                        InstitutionAlias.institution_id == institution_id,
                        InstitutionAlias.normalized_alias == normalized,
                        InstitutionAlias.id != keep_id,
                    )
                )
        report.duplicate_aliases_removed = removed
        return removed

    def deduplicate_addresses(self, report: DedupeReport) -> int:
        duplicates = self._session.execute(
            select(
                Address.institution_id,
                Address.fingerprint,
                func.min(Address.id),
                func.count(Address.id),
            )
            .group_by(Address.institution_id, Address.fingerprint)
            .having(func.count(Address.id) > 1)
        ).all()
        removed = 0
        for institution_id, fingerprint, keep_id, count in duplicates:
            removed += int(count) - 1
            if not self._dry_run:
                self._session.execute(
                    delete(Address).where(
                        Address.institution_id == institution_id,
                        Address.fingerprint == fingerprint,
                        Address.id != keep_id,
                    )
                )
        report.duplicate_addresses_removed = removed
        return removed

    def deduplicate_links(self, report: DedupeReport) -> int:
        duplicates = self._session.execute(
            select(
                BinInstitution.bin_id,
                BinInstitution.institution_id,
                BinInstitution.relationship_type,
                func.min(BinInstitution.id),
                func.count(BinInstitution.id),
            )
            .group_by(
                BinInstitution.bin_id,
                BinInstitution.institution_id,
                BinInstitution.relationship_type,
            )
            .having(func.count(BinInstitution.id) > 1)
        ).all()
        removed = 0
        for bin_id, institution_id, relationship, keep_id, count in duplicates:
            removed += int(count) - 1
            if not self._dry_run:
                self._session.execute(
                    delete(BinInstitution).where(
                        BinInstitution.bin_id == bin_id,
                        BinInstitution.institution_id == institution_id,
                        BinInstitution.relationship_type == relationship,
                        BinInstitution.id != keep_id,
                    )
                )
        report.duplicate_links_removed = removed
        return removed

    def find_duplicate_bins(self, report: DedupeReport) -> int:
        """Count allocations recorded more than once.

        The identity of a BIN record is its **prefix and the length that
        prefix was assigned at** — not the digits alone. ``410000`` and
        ``41000012`` share a leading six digits and are two different
        allocations, so grouping on the digits would either miss real
        duplicates or invent them. A unique constraint on ``bins.bin`` keeps
        this at zero in practice; the check exists to catch a package built
        by a pipeline that lost it.
        """
        duplicated = (
            select(Bin.prefix, Bin.prefix_length)
            .group_by(Bin.prefix, Bin.prefix_length)
            .having(func.count(Bin.id) > 1)
            .subquery()
        )
        rows = self._session.execute(
            select(func.count()).select_from(duplicated)
        ).scalar()
        report.duplicate_bins = int(rows or 0)
        return report.duplicate_bins

    def find_range_conflicts(self, report: DedupeReport) -> int:
        """Record spans that two different institutions both claim.

        A unique constraint already stops the *same* institution holding one
        span twice, so an exact duplicate cannot exist. What can exist — and
        what matters — is two issuers claiming the same allocation. That is a
        disagreement, not a duplicate, so it is recorded as a conflict and both
        rows are kept for the lookup engine to report.
        """
        contested = self._session.execute(
            select(
                BinRange.range_low,
                BinRange.range_high,
                func.count(func.distinct(BinRange.institution_id)),
            )
            .where(BinRange.institution_id.is_not(None))
            .group_by(BinRange.range_low, BinRange.range_high)
            .having(func.count(func.distinct(BinRange.institution_id)) > 1)
        ).all()

        recorded = 0
        for low, high, _count in contested:
            claimants = (
                self._session.execute(
                    select(Institution.display_name)
                    .join(BinRange, BinRange.institution_id == Institution.id)
                    .where(BinRange.range_low == low, BinRange.range_high == high)
                    .order_by(Institution.display_name)
                )
                .scalars()
                .all()
            )
            if len(claimants) < 2:
                continue
            if self._record_conflict(
                entity_type="bin_range",
                entity_key=f"{low}-{high}",
                field="institution",
                value_a=str(claimants[0]),
                value_b=", ".join(str(name) for name in claimants[1:]),
            ):
                recorded += 1
        report.range_conflicts_recorded = recorded
        report.conflicts_recorded += recorded
        return recorded

    # -- institution merging ----------------------------------------------
    @staticmethod
    def _blocking_keys(institution: Institution) -> set[str]:
        """Cheap keys that put plausible duplicates in the same bucket.

        Blocking on the exact normalized name alone would never compare
        "Northshore CU" with "Northshore Credit Union". Adding the leading
        token and the acronym brings such pairs together; the evidence check in
        :meth:`_score` still decides whether they may actually be merged.
        """
        normalized = name_normalizer.normalize(institution.display_name)
        keys: set[str] = set()
        core = normalized.core or squash(institution.display_name)
        if core:
            keys.add(f"core:{core}")
        tokens = list(normalized.core_tokens)
        if tokens:
            # The leading word is the most identity-bearing part of a bank name.
            keys.add(f"lead:{tokens[0]}")
        if len(normalized.acronym) >= 3:
            keys.add(f"acronym:{normalized.acronym}")
        return keys

    def deduplicate_institutions(self, report: DedupeReport, *, merge: bool = True) -> DedupeReport:
        """Block institutions into candidate buckets, then score each pair."""
        groups: dict[str, list[Institution]] = defaultdict(list)
        institutions = (
            self._session.execute(select(Institution).order_by(Institution.id)).scalars().all()
        )
        report.scanned_institutions = len(institutions)
        for institution in institutions:
            for key in self._blocking_keys(institution):
                groups[key].append(institution)

        compared: set[tuple[int, int]] = set()
        merged_away: set[int] = set()

        for candidates in groups.values():
            if len(candidates) < 2:
                continue
            for index, anchor in enumerate(candidates):
                if anchor.id in merged_away:
                    continue
                for other in candidates[index + 1 :]:
                    if other.id in merged_away or anchor.id == other.id:
                        continue
                    pair = (min(anchor.id, other.id), max(anchor.id, other.id))
                    if pair in compared:
                        continue
                    compared.add(pair)

                    score = self._score(anchor, other)
                    candidate = MergeCandidate(
                        keep_id=anchor.id,
                        merge_id=other.id,
                        keep_name=anchor.display_name,
                        merge_name=other.display_name,
                        score=score.score,
                        reason=score.reason,
                    )
                    if score.can_merge and merge:
                        self._merge(anchor, other, report)
                        merged_away.add(other.id)
                        report.merged.append(candidate)
                        report.merged_institutions += 1
                    elif score.needs_review or score.can_merge:
                        report.review_candidates.append(candidate)
        return report

    def _score(self, left: Institution, right: Institution) -> MatchScore:
        shared = int(
            self._session.execute(
                select(func.count())
                .select_from(BinInstitution)
                .where(
                    BinInstitution.institution_id == left.id,
                    BinInstitution.bin_id.in_(
                        select(BinInstitution.bin_id).where(
                            BinInstitution.institution_id == right.id
                        )
                    ),
                )
            ).scalar()
            or 0
        )
        alias_match = (
            self._session.execute(
                select(InstitutionAlias.id).where(
                    InstitutionAlias.institution_id == left.id,
                    InstitutionAlias.normalized_alias
                    == name_normalizer.normalized_form(right.display_name),
                )
            ).scalar()
            is not None
        )
        return name_normalizer.match(
            left.display_name,
            right.display_name,
            left_country=left.country.iso2 if left.country else None,
            right_country=right.country.iso2 if right.country else None,
            left_website=left.website,
            right_website=right.website,
            left_swift=left.swift_bic,
            right_swift=right.swift_bic,
            shared_bins=shared,
            alias_match=alias_match,
        )

    def _merge(self, keep: Institution, drop: Institution, report: DedupeReport) -> None:
        """Move everything onto *keep*, preserving lineage and disagreements."""
        if self._dry_run or keep.id == drop.id:
            return

        # Disagreeing scalar fields become conflicts rather than being lost.
        for field_name in ("legal_name", "website", "swift_bic", "country_id"):
            kept_value = getattr(keep, field_name)
            dropped_value = getattr(drop, field_name)
            if dropped_value in (None, ""):
                continue
            if kept_value in (None, ""):
                setattr(keep, field_name, dropped_value)
            elif kept_value != dropped_value:
                self._record_institution_conflict(keep, field_name, kept_value, dropped_value)
                report.conflicts_recorded += 1

        # The dropped name stays findable as an alias.
        normalized = name_normalizer.normalized_form(drop.display_name)
        exists = self._session.execute(
            select(InstitutionAlias.id).where(
                InstitutionAlias.institution_id == keep.id,
                InstitutionAlias.normalized_alias == normalized,
            )
        ).scalar()
        if exists is None and normalized:
            self._session.add(
                InstitutionAlias(
                    institution_id=keep.id,
                    alias=drop.display_name,
                    normalized_alias=normalized,
                    alias_type="variant",
                    confidence=0.9,
                )
            )

        for link in (
            self._session.execute(
                select(BinInstitution).where(BinInstitution.institution_id == drop.id)
            )
            .scalars()
            .all()
        ):
            clash = self._session.execute(
                select(BinInstitution.id).where(
                    BinInstitution.bin_id == link.bin_id,
                    BinInstitution.institution_id == keep.id,
                    BinInstitution.relationship_type == link.relationship_type,
                )
            ).scalar()
            if clash is None:
                link.institution_id = keep.id
            else:
                self._session.delete(link)

        for alias in (
            self._session.execute(
                select(InstitutionAlias).where(InstitutionAlias.institution_id == drop.id)
            )
            .scalars()
            .all()
        ):
            clash = self._session.execute(
                select(InstitutionAlias.id).where(
                    InstitutionAlias.institution_id == keep.id,
                    InstitutionAlias.normalized_alias == alias.normalized_alias,
                )
            ).scalar()
            if clash is None:
                alias.institution_id = keep.id
            else:
                self._session.delete(alias)

        for address in (
            self._session.execute(
                select(Address).where(Address.institution_id == drop.id)
            )
            .scalars()
            .all()
        ):
            clash = self._session.execute(
                select(Address.id).where(
                    Address.institution_id == keep.id,
                    Address.fingerprint == address.fingerprint,
                )
            ).scalar()
            if clash is None:
                address.institution_id = keep.id
                address.is_primary = False
            else:
                self._session.delete(address)

        for child in (
            self._session.execute(select(Institution).where(Institution.parent_id == drop.id))
            .scalars()
            .all()
        ):
            child.parent_id = keep.id

        self._session.flush()
        self._session.delete(drop)
        self._session.flush()

    def _record_conflict(
        self,
        *,
        entity_type: str,
        entity_key: str,
        field: str,
        value_a: object,
        value_b: object,
    ) -> bool:
        """Record a disagreement once. Returns whether it was new."""
        exists = self._session.execute(
            select(Conflict.id).where(
                Conflict.entity_type == entity_type,
                Conflict.entity_key == entity_key,
                Conflict.field == field,
                Conflict.value_a == str(value_a),
                Conflict.value_b == str(value_b),
            )
        ).scalar()
        if exists is not None:
            return False
        self._session.add(
            Conflict(
                entity_type=entity_type,
                entity_key=entity_key,
                field=field,
                value_a=str(value_a),
                value_b=str(value_b),
                status=ConflictStatus.OPEN.value,
                detected_at=datetime.now(UTC),
            )
        )
        return True

    def _record_institution_conflict(
        self, institution: Institution, field_name: str, kept: object, dropped: object
    ) -> None:
        """A field two merged institutions disagreed about."""
        self._record_conflict(
            entity_type="institution",
            entity_key=str(institution.id),
            field=field_name,
            value_a=kept,
            value_b=dropped,
        )
