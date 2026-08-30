"""Confidence scoring for normalization, matching and deduplication.

Records are never merged on string similarity alone. A merge requires a score
above :data:`MERGE_THRESHOLD` *and* at least one corroborating signal
(a shared country, a shared BIN, a matching website host, a matching SWIFT/BIC)
— see :class:`MatchEvidence`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum

#: Score at or above which two records may be merged automatically.
MERGE_THRESHOLD = 0.92
#: Score at or above which two records are recorded as a *candidate* pair for
#: review, but are not merged.
REVIEW_THRESHOLD = 0.78


class ConfidenceLevel(StrEnum):
    CERTAIN = "certain"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def from_score(cls, score: float) -> ConfidenceLevel:
        if score >= 0.98:
            return cls.CERTAIN
        if score >= MERGE_THRESHOLD:
            return cls.HIGH
        if score >= REVIEW_THRESHOLD:
            return cls.MEDIUM
        return cls.LOW


def string_similarity(left: str, right: str) -> float:
    """Ratio in ``[0, 1]``. Empty inputs score 0."""
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def token_overlap(left: list[str], right: list[str], *, weights: dict[str, float] | None = None) -> float:
    """Weighted Jaccard overlap of two token lists."""
    if not left or not right:
        return 0.0
    weights = weights or {}
    left_set, right_set = set(left), set(right)

    def weight(token: str) -> float:
        return weights.get(token, 1.0)

    intersection = sum(weight(token) for token in left_set & right_set)
    union = sum(weight(token) for token in left_set | right_set)
    return intersection / union if union else 0.0


@dataclass(slots=True)
class MatchEvidence:
    """Corroborating signals gathered while comparing two records."""

    same_country: bool = False
    shared_bins: int = 0
    same_website_host: bool = False
    same_swift_bic: bool = False
    same_postal_code: bool = False
    alias_match: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def signal_count(self) -> int:
        return sum(
            (
                self.same_country,
                self.shared_bins > 0,
                self.same_website_host,
                self.same_swift_bic,
                self.same_postal_code,
                self.alias_match,
            )
        )

    @property
    def has_corroboration(self) -> bool:
        """A strong identifier, or two weaker signals agreeing."""
        if self.same_swift_bic or self.same_website_host or self.shared_bins > 0:
            return True
        return self.signal_count >= 2


@dataclass(slots=True)
class MatchScore:
    """The result of comparing two candidate records."""

    score: float
    evidence: MatchEvidence
    reason: str = ""

    @property
    def level(self) -> ConfidenceLevel:
        return ConfidenceLevel.from_score(self.score)

    @property
    def can_merge(self) -> bool:
        """Superficial similarity alone is never enough to merge."""
        return self.score >= MERGE_THRESHOLD and self.evidence.has_corroboration

    @property
    def needs_review(self) -> bool:
        return not self.can_merge and self.score >= REVIEW_THRESHOLD


def combine(*scores: tuple[float, float]) -> float:
    """Weighted mean of ``(value, weight)`` pairs, clamped to ``[0, 1]``."""
    total_weight = sum(weight for _, weight in scores)
    if total_weight <= 0:
        return 0.0
    value = sum(score * weight for score, weight in scores) / total_weight
    return max(0.0, min(1.0, value))
