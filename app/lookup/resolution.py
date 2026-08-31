"""Range-aware BIN resolution.

This is the algorithm the whole application depends on being right. Given a
prefix, it finds every allocation that legitimately contains it, ranks them by
how *specific* they are, and reports the relationships the winning allocation
supports — together with anything that disagrees.

What it will not do
-------------------

* It will not treat a six-digit root as the answer when an eight-digit
  assignment or an account range covering the query exists. In eight-digit
  issuance a root can be shared between issuers, so answering from the root is
  how a card gets attributed to the wrong bank.
* It will not expand a six-digit root into a hundred eight-digit BINs. An
  eight-digit value is an assignment only where one is recorded.
* It will not treat numeric proximity as evidence. ``414720`` and ``414721``
  are two allocations that happen to be adjacent; nothing follows from that.
* It will not pick a winner between records that genuinely disagree. It reports
  the conflict and shows both.
* Where nothing supports an answer, it returns no institution rather than the
  nearest thing it found.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.core.logging_config import get_logger
from app.lookup.conflict_resolver import (
    ConflictingClaim,
    ConflictOutcome,
    conflict_resolver,
)
from app.lookup.evidence import (
    LookupConfidence,
    ResultConfidence,
    combine,
    score_relationship,
)
from app.lookup.strategy import LookupStrategy, MatchSpecificity
from app.models.schemas import BinRecord, InstitutionSummary

logger = get_logger(__name__)


@dataclass(slots=True)
class Candidate:
    """One allocation that contains the query, with how it was found."""

    record: BinRecord
    strategy: LookupStrategy
    #: The numeric width of the allocation. Narrower is more specific, and is
    #: the tie-breaker between two candidates of equal strategy.
    span: int = 1
    #: Whether the allocation itself (not the relationship) is current.
    is_current: bool = True
    #: How many independent records back this allocation.
    agreeing_records: int = 1

    @property
    def specificity(self) -> MatchSpecificity:
        return self.strategy.specificity

    @property
    def sort_key(self) -> tuple[int, int, int, str]:
        """Most specific, then narrowest, then current, then stable by value."""
        return (
            -int(self.specificity),
            self.span,
            0 if self.is_current else 1,
            self.record.bin,
        )


@dataclass(slots=True)
class Resolution:
    """What the engine concluded, and why."""

    query: str
    #: The winning allocation, or ``None`` when nothing supported an answer.
    record: BinRecord | None = None
    strategy: LookupStrategy = LookupStrategy.NONE
    confidence: ResultConfidence = field(default_factory=ResultConfidence)
    #: Every relationship the winning allocation supports, current first.
    relationships: tuple[InstitutionSummary, ...] = ()
    #: Allocations that also contained the query but lost on specificity. Kept
    #: so the interface can say "a broader range also covers this".
    other_candidates: tuple[Candidate, ...] = ()
    #: Institutions named by records that disagree with the winner and that
    #: the data could not settle between. Empty when there was no conflict, or
    #: when one record structurally supersedes the other.
    conflicting_institutions: tuple[InstitutionSummary, ...] = ()
    #: The full conflict analysis, including claims that lost. Nothing is
    #: discarded, so a caller can always show both readings.
    conflict: ConflictOutcome | None = None

    @property
    def found(self) -> bool:
        return self.record is not None

    @property
    def resolved(self) -> bool:
        """Whether an institution was actually named."""
        return bool(self.relationships)

    @property
    def is_conflicted(self) -> bool:
        return self.confidence.level is LookupConfidence.CONFLICTED

    @property
    def institution_count(self) -> int:
        return len({item.id for item in self.relationships})

    @property
    def match_label(self) -> str:
        return self.strategy.specificity.label


class RangeResolver:
    """Ranks candidate allocations and derives the answer from the winner."""

    def resolve(self, query: str, candidates: list[Candidate]) -> Resolution:
        """Pick the most specific valid allocation and explain the choice."""
        if not candidates:
            logger.debug("No allocation contains the query", extra={"context": {"query": query}})
            return Resolution(
                query=query,
                confidence=ResultConfidence(
                    level=LookupConfidence.UNKNOWN,
                    reasons=["No allocation in the database contains this value."],
                ),
            )

        ordered = sorted(candidates, key=lambda item: item.sort_key)
        winner = ordered[0]
        losers = tuple(ordered[1:])

        relationships = self._ordered_relationships(winner.record)
        if not relationships:
            # An allocation exists but names nobody. Saying "unknown issuer" is
            # the honest answer; inventing one from a broader range is not.
            return Resolution(
                query=query,
                record=winner.record,
                strategy=winner.strategy,
                confidence=ResultConfidence(
                    level=LookupConfidence.UNKNOWN,
                    reasons=[
                        "An allocation covers this value, but no institution "
                        "relationship is recorded for it."
                    ],
                ),
                other_candidates=losers,
            )

        conflicting = self._disagreements(winner, losers)
        conflict = self._settle(winner, losers, conflicting)
        # A conflict the data settles structurally — one record being more
        # specific, more current or better evidenced — is not a conflict the
        # user needs to arbitrate. One that it cannot settle stays reported.
        if conflict.is_resolved:
            conflicting = ()
        scores = [
            score_relationship(
                winner.strategy,
                specificity=winner.specificity,
                agreeing_records=winner.agreeing_records,
                disagreeing_records=len({item.id for item in conflicting}),
                is_current=item.is_current and winner.is_current,
                stored_confidence=item.confidence,
                relationship_is_issuing=item.is_issuing,
                corroborating_signals=self._corroboration(winner, losers, item),
            )
            for item in relationships
        ]
        confidence = combine(scores)

        # Carry the per-relationship figure back onto each summary, so the
        # interface can show why one relationship is weaker than another.
        annotated = tuple(
            item.model_copy(
                update={
                    "confidence": round(score.score, 4),
                    "confidence_level": score.level.value,
                }
            )
            for item, score in zip(relationships, scores, strict=True)
        )

        return Resolution(
            query=query,
            record=winner.record,
            strategy=winner.strategy,
            confidence=confidence,
            relationships=annotated,
            other_candidates=losers,
            conflicting_institutions=conflicting,
            conflict=conflict,
        )

    def _settle(
        self,
        winner: Candidate,
        losers: tuple[Candidate, ...],
        conflicting: tuple[InstitutionSummary, ...],
    ) -> ConflictOutcome:
        """Ask the conflict resolver whether the disagreement is settleable."""
        if not conflicting:
            return ConflictOutcome()
        claims = [
            ConflictingClaim(
                institution=item,
                specificity=winner.specificity,
                span=winner.span,
                confidence=item.confidence if item.confidence is not None else 0.5,
            )
            for item in self._current_issuers(winner.record)
        ]
        by_id = {claim.institution.id for claim in claims}
        for candidate in losers:
            for item in self._current_issuers(candidate.record):
                if item.id in by_id:
                    continue
                by_id.add(item.id)
                claims.append(
                    ConflictingClaim(
                        institution=item,
                        specificity=candidate.specificity,
                        span=candidate.span,
                        confidence=item.confidence if item.confidence is not None else 0.5,
                    )
                )
        return conflict_resolver.resolve(claims)

    # -- ranking helpers ---------------------------------------------------
    @staticmethod
    def _ordered_relationships(record: BinRecord) -> tuple[InstitutionSummary, ...]:
        """Current issuing relationships first, then the rest.

        Nothing is dropped: a processor association and a former issuer are
        both real facts, and hiding them would be hiding a conflict.
        """
        return tuple(
            sorted(
                record.institutions,
                key=lambda item: (
                    not item.is_current,
                    not item.is_issuing,
                    not item.is_primary,
                    item.display_name,
                ),
            )
        )

    @classmethod
    def _disagreements(
        cls, winner: Candidate, losers: tuple[Candidate, ...]
    ) -> tuple[InstitutionSummary, ...]:
        """Institutions that contradict the winner's account of who issues.

        Two shapes of disagreement count, and both are reported rather than
        resolved:

        * two current issuing claims on the *same* allocation naming different
          institutions — the "BIN → A, BIN → B" case;
        * an equally specific allocation naming a different issuer.

        Neither a broader range nor an associative relationship is a conflict.
        A broader range is a less specific record the winner correctly
        supersedes; a parent or processor link says something different, not
        something contradictory.
        """
        issuers = cls._current_issuers(winner.record)
        rivals: dict[int, InstitutionSummary] = {}

        # Same allocation, several current issuers: everything after the
        # best-supported one is a competing claim on the same fact.
        if len(issuers) > 1:
            for item in issuers[1:]:
                rivals.setdefault(item.id, item)

        winning_ids = {item.id for item in issuers}
        for candidate in losers:
            if candidate.specificity != winner.specificity:
                continue
            for item in cls._current_issuers(candidate.record):
                if item.id not in winning_ids:
                    rivals.setdefault(item.id, item)
        return tuple(rivals.values())

    @staticmethod
    def _current_issuers(record: BinRecord) -> list[InstitutionSummary]:
        """Distinct institutions currently claimed to issue this allocation."""
        seen: dict[int, InstitutionSummary] = {}
        for item in record.institutions:
            if item.is_current and item.is_issuing:
                seen.setdefault(item.id, item)
        return sorted(
            seen.values(), key=lambda item: (not item.is_primary, item.display_name)
        )

    @staticmethod
    def _corroboration(
        winner: Candidate,
        losers: tuple[Candidate, ...],
        relationship: InstitutionSummary,
    ) -> int:
        """How many *other* allocations independently name this institution.

        This is corroboration, not proximity: a broader range that names the
        same institution genuinely supports the answer.
        """
        return sum(
            1
            for candidate in losers
            if any(item.id == relationship.id for item in candidate.record.institutions)
        )


def agreement_counts(candidates: list[Candidate]) -> Counter[int]:
    """How many candidate allocations name each institution."""
    counter: Counter[int] = Counter()
    for candidate in candidates:
        for item in candidate.record.institutions:
            counter[item.id] += 1
    return counter


#: Shared, stateless instance.
range_resolver = RangeResolver()
