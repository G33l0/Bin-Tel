"""Confidence, computed from evidence.

A confidence score here is never a constant, a guess, or a number that came
from nowhere. It is derived from *what kind of evidence supports the answer*,
graded on a fixed hierarchy, and it carries the reasons that produced it so a
result can always be explained.

The hierarchy, strongest first:

===== ============================================================
Level Evidence
===== ============================================================
1     An exact authoritative issuer/range relationship
2     A validated network account-range relationship
3     A strong canonical institution relationship
4     A validated third-party database agreement
5     Multiple independent datasets agreeing
6     Name / address / entity-resolution evidence
7     Weak inference
===== ============================================================

Agreement between independent records raises confidence. Disagreement does not
lower it quietly — it produces :attr:`LookupConfidence.CONFLICTED`, and both
readings are preserved for the caller to show.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from app.lookup.strategy import LookupStrategy, MatchSpecificity


class EvidenceLevel(IntEnum):
    """The graded evidence hierarchy. Lower is stronger."""

    AUTHORITATIVE_RANGE = 1
    NETWORK_ACCOUNT_RANGE = 2
    CANONICAL_INSTITUTION = 3
    VALIDATED_THIRD_PARTY = 4
    MULTI_SOURCE_AGREEMENT = 5
    ENTITY_RESOLUTION = 6
    WEAK_INFERENCE = 7

    @property
    def label(self) -> str:
        return {
            EvidenceLevel.AUTHORITATIVE_RANGE: "Authoritative issuer assignment",
            EvidenceLevel.NETWORK_ACCOUNT_RANGE: "Validated account range",
            EvidenceLevel.CANONICAL_INSTITUTION: "Canonical institution relationship",
            EvidenceLevel.VALIDATED_THIRD_PARTY: "Validated reference data",
            EvidenceLevel.MULTI_SOURCE_AGREEMENT: "Independent records agree",
            EvidenceLevel.ENTITY_RESOLUTION: "Entity resolution",
            EvidenceLevel.WEAK_INFERENCE: "Weak inference",
        }[self]

    @property
    def base_score(self) -> float:
        """The score this level starts from, before adjustment."""
        return {
            EvidenceLevel.AUTHORITATIVE_RANGE: 0.97,
            EvidenceLevel.NETWORK_ACCOUNT_RANGE: 0.93,
            EvidenceLevel.CANONICAL_INSTITUTION: 0.85,
            EvidenceLevel.VALIDATED_THIRD_PARTY: 0.76,
            EvidenceLevel.MULTI_SOURCE_AGREEMENT: 0.70,
            EvidenceLevel.ENTITY_RESOLUTION: 0.55,
            EvidenceLevel.WEAK_INFERENCE: 0.30,
        }[self]


class LookupConfidence(StrEnum):
    """How much weight a caller should put on a lookup result."""

    VERIFIED = "verified"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    CONFLICTED = "conflicted"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return self.value.capitalize()

    @property
    def is_actionable(self) -> bool:
        """Whether the answer is strong enough to be relied on unqualified."""
        return self in (LookupConfidence.VERIFIED, LookupConfidence.HIGH)

    @classmethod
    def from_score(cls, score: float) -> LookupConfidence:
        if score >= 0.95:
            return cls.VERIFIED
        if score >= 0.82:
            return cls.HIGH
        if score >= 0.60:
            return cls.MEDIUM
        if score > 0.0:
            return cls.LOW
        return cls.UNKNOWN


#: Which evidence level a lookup strategy establishes on its own.
_STRATEGY_EVIDENCE: dict[LookupStrategy, EvidenceLevel] = {
    LookupStrategy.EXACT_8: EvidenceLevel.AUTHORITATIVE_RANGE,
    LookupStrategy.EXACT_ASSIGNED: EvidenceLevel.AUTHORITATIVE_RANGE,
    LookupStrategy.ACCOUNT_RANGE: EvidenceLevel.NETWORK_ACCOUNT_RANGE,
    LookupStrategy.EXACT_6: EvidenceLevel.AUTHORITATIVE_RANGE,
    LookupStrategy.BROADER_RANGE: EvidenceLevel.VALIDATED_THIRD_PARTY,
    LookupStrategy.ROOT_PREFIX: EvidenceLevel.VALIDATED_THIRD_PARTY,
    LookupStrategy.CANONICAL_INSTITUTION: EvidenceLevel.CANONICAL_INSTITUTION,
    LookupStrategy.WEAK_INFERENCE: EvidenceLevel.WEAK_INFERENCE,
    LookupStrategy.NONE: EvidenceLevel.WEAK_INFERENCE,
}


@dataclass(slots=True)
class ResultConfidence:
    """A confidence figure with the evidence that produced it."""

    score: float = 0.0
    level: LookupConfidence = LookupConfidence.UNKNOWN
    evidence_level: EvidenceLevel | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int:
        return int(round(max(0.0, min(1.0, self.score)) * 100))

    @property
    def summary(self) -> str:
        """One line a person can read, with no source names in it."""
        if self.level is LookupConfidence.CONFLICTED:
            return "Conflicting records — both are shown."
        if self.level is LookupConfidence.UNKNOWN:
            return "Not enough evidence to name an institution."
        head = self.evidence_level.label if self.evidence_level else "Evidence"
        return f"{self.level.label} — {head.lower()}."

    def explain(self) -> str:
        return "; ".join(self.reasons) if self.reasons else self.summary


def score_relationship(
    strategy: LookupStrategy,
    *,
    specificity: MatchSpecificity | None = None,
    agreeing_records: int = 1,
    disagreeing_records: int = 0,
    is_current: bool = True,
    stored_confidence: float | None = None,
    relationship_is_issuing: bool = True,
    has_institution: bool = True,
    corroborating_signals: int = 0,
) -> ResultConfidence:
    """Grade one candidate relationship.

    ``agreeing_records`` counts independent records asserting the *same*
    institution; ``disagreeing_records`` counts records naming a different one.
    Disagreement is never resolved by silently preferring one — it is reported.
    """
    if not has_institution:
        return ResultConfidence(
            score=0.0,
            level=LookupConfidence.UNKNOWN,
            reasons=["No institution relationship is recorded for this prefix."],
        )

    evidence = _STRATEGY_EVIDENCE[strategy]
    reasons: list[str] = []
    specificity = specificity or strategy.specificity
    score = evidence.base_score
    matched = (
        f"Matched an exact {strategy.label}"
        if strategy.is_exact
        else f"Matched on {strategy.label.lower()}"
    )
    reasons.append(matched)

    # Independent agreement is the one thing that can push a result to the top.
    if agreeing_records > 1:
        bonus = min(0.06, 0.02 * (agreeing_records - 1))
        score += bonus
        reasons.append(f"{agreeing_records} independent records agree")
        if evidence > EvidenceLevel.MULTI_SOURCE_AGREEMENT:
            evidence = EvidenceLevel.MULTI_SOURCE_AGREEMENT

    # Corroboration beyond the name itself — a shared website host, a matching
    # SWIFT/BIC, a shared range. Never proximity.
    if corroborating_signals:
        score += min(0.04, 0.02 * corroborating_signals)
        reasons.append("Corroborated by independent identifiers")

    if not relationship_is_issuing:
        # A parent or processor association is real, but it does not say this
        # institution issues the card.
        score -= 0.12
        reasons.append("Relationship is associative rather than issuing")

    if not is_current:
        score -= 0.10
        reasons.append("Historical relationship, superseded by a later record")

    if specificity <= MatchSpecificity.ROOT:
        score -= 0.08
        reasons.append(
            "Answered from a broader allocation than the value searched for"
        )

    if stored_confidence is not None:
        # The published record's own confidence caps the result: the engine
        # cannot be more certain than the data it is reading.
        score = min(score, max(0.05, stored_confidence))
        if stored_confidence < 0.7:
            reasons.append("Source record carries reduced confidence")

    # Nothing is ever reported as absolute certainty: this is reference data
    # about a changing world, and a 100% figure would overstate what any
    # record can support.
    score = max(0.0, min(0.99, score))

    if disagreeing_records:
        return ResultConfidence(
            score=score,
            level=LookupConfidence.CONFLICTED,
            evidence_level=evidence,
            reasons=[
                *reasons,
                f"{disagreeing_records} record(s) name a different institution",
            ],
        )

    return ResultConfidence(
        score=score,
        level=LookupConfidence.from_score(score),
        evidence_level=evidence,
        reasons=reasons,
    )


def combine(results: list[ResultConfidence]) -> ResultConfidence:
    """The confidence of a whole lookup, given its per-relationship figures.

    A lookup is as confident as its best-supported relationship, unless any
    relationship is conflicted — in which case the lookup is conflicted, since
    the caller is being shown two incompatible answers.
    """
    if not results:
        return ResultConfidence(reasons=["No relationships were found."])
    if any(item.level is LookupConfidence.CONFLICTED for item in results):
        best = max(results, key=lambda item: item.score)
        return ResultConfidence(
            score=best.score,
            level=LookupConfidence.CONFLICTED,
            evidence_level=best.evidence_level,
            reasons=["Records disagree about the issuing institution"],
        )
    return max(results, key=lambda item: item.score)
