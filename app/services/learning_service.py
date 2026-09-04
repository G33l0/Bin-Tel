"""Learning: everything Bin-Tel works out or is told, held before it is written.

The database improves from three directions — evidence it already holds, files
you add, and services outside this machine. Only the middle one is a decision
you make row by row. The other two are the dangerous ones, because a pipeline
that writes what it finds is a pipeline that eventually writes something wrong
into a database nobody re-checks, and a wrong BIN is worse than a missing one.

So nothing learned reaches the database directly. It becomes a
:class:`~app.models.entities.LearnedFact`: a proposal that names its source,
that source's licence standing, the value currently held, the value offered,
and the evidence for it. A proposal changes no answer and appears in no
lookup. It waits.

Two separate permissions turn a proposal into a fact, and neither implies the
other:

**The source must be authorized.** Authorization is a name you added to your
settings, never something the code grants itself. An unauthorized source is
not consulted at all — it is not that its answers are ignored, it is that no
request is made.

**The fact must be approved.** Approving is per fact, or in bulk when you have
read them. A source you trust enough to consult is not automatically a source
you trust to overwrite what you curated by hand.

The one shortcut is deliberately narrow: a fact that fills a *blank* from a
source whose licence is settled may apply automatically, and only when you have
turned that on. Contradicting a value you already hold always waits for you,
whatever the source and whatever its licence, because that is the case where
being wrong costs something.

Applying writes provenance beside the value — a claim naming the source and a
normalization event naming the rule — so a value learned this way can always be
traced back to what proposed it and when.

Nothing here is a route around the licensing rules. A source whose terms do not
establish redistribution stays marked as such on every fact it produced, and
that marking travels into the database with the value.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger, log_event
from app.models.entities import (
    Bin,
    BinClaim,
    Conflict,
    ConflictStatus,
    Country,
    Institution,
    LearnedFact,
    LearnedStatus,
    Network,
    NormalizationEvent,
    Source,
)
from app.normalizers.card_normalizer import card_normalizer
from app.normalizers.geo_normalizer import geo_normalizer
from app.normalizers.network_normalizer import network_normalizer
from app.normalizers.text import sanitise_text

logger = get_logger(__name__)

#: Fields a learned fact is allowed to write on a BIN.
#:
#: A whitelist rather than "anything on the row" for a plain reason: these are
#: the descriptive attributes of a card product. The identity columns — the
#: prefix, its length, its type, the numeric span — are what a lookup *matches
#: on*, and letting a learned fact edit them would let a source silently move
#: a BIN rather than describe one.
WRITEABLE_BIN_FIELDS: frozenset[str] = frozenset(
    {"brand", "card_level", "card_type", "funding_type", "currency_code", "network", "country"}
)

#: Fields a learned fact is allowed to write on an institution.
WRITEABLE_INSTITUTION_FIELDS: frozenset[str] = frozenset(
    {"website", "legal_name", "short_name", "swift_bic", "country"}
)

#: How much a proposal from local evidence is worth. Below the 0.9 the personal
#: list carries, because the list is an assertion and this is a deduction.
LOCAL_CONFIDENCE = 0.6


@dataclass(slots=True)
class Proposal:
    """One thing a source says, before anyone has decided what to do about it."""

    subject_type: str
    subject_key: str
    field: str
    proposed_value: str
    source_code: str
    evidence: str
    current_value: str | None = None
    source_reference: str | None = None
    licence: str = "unknown"
    confidence: float = 0.5

    @property
    def fills_a_blank(self) -> bool:
        return not (self.current_value or "").strip()


@dataclass(slots=True)
class LearningReport:
    """What one pass did. Counted, never estimated."""

    proposed: int = 0
    duplicates: int = 0
    superseded: int = 0
    applied: int = 0
    skipped_unauthorized: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.proposed and not self.applied:
            return "nothing new was learned"
        parts: list[str] = []
        if self.proposed:
            parts.append(f"{self.proposed:,} proposal(s) recorded")
        if self.applied:
            parts.append(f"{self.applied:,} applied")
        if self.duplicates:
            parts.append(f"{self.duplicates:,} already known")
        if self.superseded:
            parts.append(f"{self.superseded:,} superseded")
        return " · ".join(parts)


class Authorization:
    """Which sources may be consulted, and what may be written without asking.

    Read from settings and never widened here. A source absent from the list
    is not consulted; there is no default-on source and no implicit one.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        authorized_sources: Sequence[str] = (),
        auto_apply_new_information: bool = False,
        auto_apply_licences: Sequence[str] = ("verified",),
    ) -> None:
        self.enabled = enabled
        self._sources = {code.strip().casefold() for code in authorized_sources if code.strip()}
        self.auto_apply_new_information = auto_apply_new_information
        self._auto_licences = {value.strip().casefold() for value in auto_apply_licences}

    @classmethod
    def from_settings(cls, settings) -> Authorization:
        learning = getattr(settings, "learning", None)
        if learning is None:  # pragma: no cover - settings always carry it
            return cls()
        return cls(
            enabled=learning.enabled,
            authorized_sources=learning.authorized_sources,
            auto_apply_new_information=learning.auto_apply_new_information,
        )

    def is_authorized(self, source_code: str) -> bool:
        """Whether this source may be consulted at all."""
        return self.enabled and source_code.strip().casefold() in self._sources

    def may_auto_apply(self, proposal: Proposal) -> bool:
        """Whether this proposal may be written without being read first.

        Three conditions, all required. It has to fill a blank rather than
        contradict something held; the source's licence has to be settled; and
        automatic application has to be switched on. Anything that overrules a
        curated value waits for a person, always.
        """
        return (
            self.auto_apply_new_information
            and proposal.fills_a_blank
            and proposal.licence.strip().casefold() in self._auto_licences
        )


class LearningService:
    """Gathers proposals, records them, and applies the ones that are approved."""

    def __init__(self, session: Session, authorization: Authorization | None = None) -> None:
        self._session = session
        self._auth = authorization or Authorization()

    # -- recording ---------------------------------------------------------
    def record(self, proposals: Iterable[Proposal]) -> LearningReport:
        """Write proposals to the ledger, superseding anything they replace."""
        report = LearningReport()
        for proposal in proposals:
            value = sanitise_text(proposal.proposed_value, limit=512)
            if value is None:
                continue
            if (proposal.current_value or "").strip() == value:
                continue  # nothing to learn; the database already says this

            existing = self._session.execute(
                select(LearnedFact).where(
                    LearnedFact.subject_type == proposal.subject_type,
                    LearnedFact.subject_key == proposal.subject_key,
                    LearnedFact.field == proposal.field,
                    LearnedFact.source_code == proposal.source_code,
                    LearnedFact.proposed_value == value,
                )
            ).scalar_one_or_none()
            if existing is not None:
                # Already raised, and its decision stands. Re-proposing a
                # rejected fact every pass would train a person to click
                # through the ledger without reading it.
                report.duplicates += 1
                continue

            report.superseded += self._supersede_earlier(proposal)

            fact = LearnedFact(
                subject_type=proposal.subject_type,
                subject_key=proposal.subject_key,
                field=proposal.field,
                current_value=proposal.current_value,
                proposed_value=value,
                source_code=proposal.source_code,
                source_reference=proposal.source_reference,
                licence=proposal.licence,
                confidence=proposal.confidence,
                evidence=proposal.evidence,
                status=LearnedStatus.PENDING.value,
            )
            self._session.add(fact)
            report.proposed += 1
            if len(report.examples) < 12:
                report.examples.append(
                    f"{proposal.subject_key}: {proposal.field} → {value} "
                    f"({proposal.source_code})"
                )

            if self._auth.may_auto_apply(proposal):
                self._session.flush()
                fact.status = LearnedStatus.APPROVED.value
                fact.decided_at = datetime.now(UTC)
                fact.decided_reason = "filled a blank from an authorized source"

        self._session.flush()
        return report

    def _supersede_earlier(self, proposal: Proposal) -> int:
        """Mark still-pending proposals about the same field as overtaken."""
        earlier = (
            self._session.execute(
                select(LearnedFact).where(
                    LearnedFact.subject_type == proposal.subject_type,
                    LearnedFact.subject_key == proposal.subject_key,
                    LearnedFact.field == proposal.field,
                    LearnedFact.source_code == proposal.source_code,
                    LearnedFact.status == LearnedStatus.PENDING.value,
                )
            )
            .scalars()
            .all()
        )
        for fact in earlier:
            fact.status = LearnedStatus.SUPERSEDED.value
            fact.decided_at = datetime.now(UTC)
        return len(earlier)

    # -- reading -----------------------------------------------------------
    def pending(self, limit: int = 200) -> list[LearnedFact]:
        return list(
            self._session.execute(
                select(LearnedFact)
                .where(LearnedFact.status == LearnedStatus.PENDING.value)
                .order_by(LearnedFact.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

    def get(self, fact_id: int) -> LearnedFact | None:
        return self._session.get(LearnedFact, fact_id)

    # -- deciding ----------------------------------------------------------
    def approve(self, fact_id: int, reason: str = "") -> LearnedFact | None:
        return self._decide(fact_id, LearnedStatus.APPROVED, reason)

    def reject(self, fact_id: int, reason: str = "") -> LearnedFact | None:
        return self._decide(fact_id, LearnedStatus.REJECTED, reason)

    def _decide(
        self, fact_id: int, status: LearnedStatus, reason: str
    ) -> LearnedFact | None:
        fact = self._session.get(LearnedFact, fact_id)
        if fact is None or fact.status not in (
            LearnedStatus.PENDING.value,
            LearnedStatus.APPROVED.value,
        ):
            return None
        fact.status = status.value
        fact.decided_at = datetime.now(UTC)
        fact.decided_reason = sanitise_text(reason, limit=512)
        self._session.flush()
        return fact

    # -- applying ----------------------------------------------------------
    def apply_approved(self) -> LearningReport:
        """Write every approved fact, with provenance beside each value."""
        report = LearningReport()
        approved = (
            self._session.execute(
                select(LearnedFact).where(
                    LearnedFact.status == LearnedStatus.APPROVED.value
                )
            )
            .scalars()
            .all()
        )
        for fact in approved:
            if self._apply_one(fact):
                fact.status = LearnedStatus.APPLIED.value
                fact.applied_at = datetime.now(UTC)
                report.applied += 1
                if len(report.examples) < 12:
                    report.examples.append(
                        f"{fact.subject_key}: {fact.field} = {fact.proposed_value}"
                    )
        self._session.flush()
        if report.applied:
            log_event(
                logger,
                "Learned facts applied",
                applied=report.applied,
            )
        return report

    def _apply_one(self, fact: LearnedFact) -> bool:
        if fact.subject_type == "bin":
            return self._apply_to_bin(fact)
        if fact.subject_type == "institution":
            return self._apply_to_institution(fact)
        return False

    def _apply_to_bin(self, fact: LearnedFact) -> bool:
        if fact.field not in WRITEABLE_BIN_FIELDS:
            return False
        record = self._session.execute(
            select(Bin).where(Bin.bin == fact.subject_key)
        ).scalar_one_or_none()
        if record is None:
            # The database was rebuilt and this BIN is no longer in it. The
            # proposal is not applied and not lost; it simply has no subject.
            return False

        value = fact.proposed_value or ""
        if fact.field == "network":
            network = self._network(value)
            if network is None:
                return False
            record.network_id = network.id
        elif fact.field == "country":
            country = self._country(value)
            if country is None:
                return False
            record.country_id = country.id
        elif fact.field == "currency_code":
            code = card_normalizer.currency(value)
            if code is None:
                return False
            record.currency_code = code
        elif fact.field == "card_level":
            record.card_level = card_normalizer.card_level(value)
        else:
            setattr(record, fact.field, value)

        record.last_updated = datetime.now(UTC)
        self._record_provenance(fact, "bin", record.bin, record.id)
        return True

    def _apply_to_institution(self, fact: LearnedFact) -> bool:
        if fact.field not in WRITEABLE_INSTITUTION_FIELDS:
            return False
        institution = self._session.execute(
            select(Institution).where(Institution.uid == fact.subject_key)
        ).scalar_one_or_none()
        if institution is None:
            return False

        value = fact.proposed_value or ""
        if fact.field == "country":
            country = self._country(value)
            if country is None:
                return False
            institution.country_id = country.id
        else:
            setattr(institution, fact.field, value)
        self._record_provenance(fact, "institution", institution.uid, None)
        return True

    def _record_provenance(
        self, fact: LearnedFact, entity_type: str, entity_key: str, bin_id: int | None
    ) -> None:
        """Leave behind what proposed this value, and under what licence.

        Both records matter. The event says a rule changed a field; the claim
        says which source asserted it. Together they are what lets a value be
        questioned later without having to guess where it came from.
        """
        self._session.add(
            NormalizationEvent(
                entity_type=entity_type,
                entity_key=entity_key,
                field=fact.field,
                raw_value=fact.current_value,
                normalized_value=fact.proposed_value,
                rule=f"learned:{fact.source_code}",
                confidence=fact.confidence,
            )
        )
        if bin_id is not None:
            source = self._source(fact)
            self._session.add(
                BinClaim(
                    bin_id=bin_id,
                    source_id=source.id if source else None,
                    field=fact.field,
                    value=fact.proposed_value,
                    confidence=fact.confidence,
                )
            )

    # -- gathering: evidence already held ----------------------------------
    def gather_local(self) -> list[Proposal]:
        """Propose from what the database already knows, without a network.

        Two kinds. An unresolved conflict is a field where two defensible
        claims exist and the database picked one — the other is offered here so
        the choice can be revisited deliberately. And a BIN whose country is
        blank while its institution's is known is a gap the enrichment pass
        declines to fill on its own, because the institution's country is where
        it is domiciled and not necessarily where the BIN is issued.
        """
        proposals: list[Proposal] = []
        proposals.extend(self._from_open_conflicts())
        proposals.extend(self._from_institution_countries())
        return proposals

    def _from_open_conflicts(self) -> list[Proposal]:
        conflicts = (
            self._session.execute(
                select(Conflict).where(
                    Conflict.entity_type == "bin",
                    Conflict.status == ConflictStatus.OPEN.value,
                )
            )
            .scalars()
            .all()
        )
        proposals: list[Proposal] = []
        for conflict in conflicts:
            record = self._session.execute(
                select(Bin).where(Bin.bin == conflict.entity_key)
            ).scalar_one_or_none()
            if record is None or conflict.field not in WRITEABLE_BIN_FIELDS:
                continue
            held = self._held_value(record, conflict.field)
            # A conflict on a relationship stores the *row id* that was in
            # dispute, because that is what the merge compared. An id is not
            # something a person can weigh, so it is turned back into the name
            # it stands for before the proposal is written.
            side_a = self._readable(conflict.field, conflict.value_a)
            side_b = self._readable(conflict.field, conflict.value_b)
            # Offer whichever side is *not* currently in force.
            alternative = side_b if (held or "") == (side_a or "") else side_a
            if not alternative or alternative == held:
                continue
            proposals.append(
                Proposal(
                    subject_type="bin",
                    subject_key=record.bin,
                    field=conflict.field,
                    current_value=held,
                    proposed_value=alternative,
                    source_code="local:conflict",
                    licence="verified",
                    confidence=min(conflict.confidence_a, conflict.confidence_b),
                    evidence=(
                        f"Two sources disagree about {conflict.field}. The database "
                        f"holds {held or 'nothing'}; the other claim is {alternative}."
                    ),
                )
            )
        return proposals

    def _from_institution_countries(self) -> list[Proposal]:
        rows = (
            self._session.execute(
                select(Bin, Institution)
                .join(Bin.institution_links)
                .join(Institution)
                .where(Bin.country_id.is_(None), Institution.country_id.is_not(None))
            )
            .unique()
            .all()
        )
        proposals: list[Proposal] = []
        seen: set[str] = set()
        for record, institution in rows:
            if record.bin in seen:
                continue
            seen.add(record.bin)
            country = self._session.get(Country, institution.country_id)
            if country is None:
                continue
            proposals.append(
                Proposal(
                    subject_type="bin",
                    subject_key=record.bin,
                    field="country",
                    current_value=None,
                    proposed_value=country.iso2,
                    source_code="local:institution",
                    licence="verified",
                    confidence=LOCAL_CONFIDENCE,
                    evidence=(
                        f"{institution.display_name} is recorded in {country.name}. "
                        "That is where the institution is, which is not necessarily "
                        "where this BIN is issued."
                    ),
                )
            )
        return proposals

    # -- gathering: a source outside this machine --------------------------
    def gather_external(
        self, provider, bins: Sequence[str], report: LearningReport | None = None
    ) -> list[Proposal]:
        """Ask an authorized provider about some BINs, and propose what differs.

        The authorization check comes before the request, not after the answer:
        an unauthorized source is never contacted at all. Whatever comes back
        is a proposal like any other, and its licence standing travels with it.
        """
        report = report if report is not None else LearningReport()
        code = getattr(provider, "SOURCE_CODE", provider.__class__.__name__.lower())
        if not self._auth.is_authorized(code):
            report.skipped_unauthorized.append(code)
            return []

        licence = str(getattr(provider, "licence", "unknown"))
        reference = str(getattr(provider, "reference", "") or "") or None
        proposals: list[Proposal] = []
        for digits in bins:
            record = self._session.execute(
                select(Bin).where(Bin.bin == digits)
            ).scalar_one_or_none()
            if record is None:
                continue
            reading = provider.lookup(digits)
            if reading is None:
                continue
            for field_name, value in self._reading_fields(reading).items():
                if not value:
                    continue
                held = self._held_value(record, field_name)
                if (held or "").strip().casefold() == value.strip().casefold():
                    continue
                proposals.append(
                    Proposal(
                        subject_type="bin",
                        subject_key=record.bin,
                        field=field_name,
                        current_value=held,
                        proposed_value=value,
                        source_code=code,
                        source_reference=reference,
                        licence=licence,
                        confidence=float(getattr(reading, "confidence", 0.5)),
                        evidence=f"{code} reports {field_name} as {value} for {digits}.",
                    )
                )
        return proposals

    @staticmethod
    def _reading_fields(reading) -> dict[str, str]:
        """The fields of an external reading Bin-Tel has a home for."""
        return {
            "network": (getattr(reading, "scheme", "") or "").strip(),
            "brand": (getattr(reading, "brand", "") or "").strip(),
            "card_type": (getattr(reading, "card_type", "") or "").strip(),
            "country": (getattr(reading, "country_alpha2", "") or "").strip(),
            "currency_code": (getattr(reading, "country_currency", "") or "").strip(),
        }

    def _readable(self, field_name: str, value: str | None) -> str | None:
        """Turn a stored foreign key back into the name it stands for."""
        text = (value or "").strip()
        if not text:
            return None
        if field_name == "network" and text.isdigit():
            network = self._session.get(Network, int(text))
            return network.display_name if network else None
        if field_name == "country" and text.isdigit():
            country = self._session.get(Country, int(text))
            return country.iso2 if country else None
        return text

    # -- small lookups -----------------------------------------------------
    def _held_value(self, record: Bin, field_name: str) -> str | None:
        if field_name == "network":
            network = self._session.get(Network, record.network_id) if record.network_id else None
            return network.display_name if network else None
        if field_name == "country":
            country = self._session.get(Country, record.country_id) if record.country_id else None
            return country.iso2 if country else None
        value = getattr(record, field_name, None)
        return str(value) if value not in (None, "") else None

    def _network(self, value: str) -> Network | None:
        definition = network_normalizer.normalize(value)
        if definition.code == "unknown":
            return None
        return self._session.execute(
            select(Network).where(Network.code == definition.code)
        ).scalar_one_or_none()

    def _country(self, value: str) -> Country | None:
        record = geo_normalizer.country(value)
        if record is None:
            return None
        return self._session.execute(
            select(Country).where(Country.iso2 == record.iso2)
        ).scalar_one_or_none()

    def _source(self, fact: LearnedFact) -> Source | None:
        source = self._session.execute(
            select(Source).where(Source.code == fact.source_code)
        ).scalar_one_or_none()
        if source is not None:
            return source
        source = Source(
            code=fact.source_code,
            name=fact.source_code,
            reference=fact.source_reference,
            licence=fact.licence,
            trust_score=min(max(fact.confidence, 0.0), 1.0),
        )
        self._session.add(source)
        self._session.flush()
        return source


__all__ = [
    "WRITEABLE_BIN_FIELDS",
    "WRITEABLE_INSTITUTION_FIELDS",
    "Authorization",
    "LearningReport",
    "LearningService",
    "Proposal",
]
