"""Conflict resolution.

Two defensible records saying different things about the same BIN is normal in
reference data. What is not acceptable is picking one at random, or deleting
the loser to make the result look tidy.

This resolver decides whether a conflict is *resolvable on structural grounds*
— one record being more specific, more current, or better evidenced than the
other — and says so explicitly when it is not. An unresolved conflict is
preserved and surfaced, never quietly dropped.

The comparisons, in the order they are applied:

1. Specificity — an eight-digit assignment or an account range outranks the
   six-digit root it sits under.
2. Currency — a current record outranks a superseded one.
3. Effective dates — a later effective date supersedes an earlier one.
4. Issuer identity — the same institution under two names is not a conflict.
5. Range breadth — a narrower allocation outranks a wider one.
6. Confidence — a materially better-evidenced record outranks a weaker one.

If none of those separates the two records, the conflict stands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from app.core.logging_config import get_logger
from app.lookup.strategy import MatchSpecificity
from app.models.schemas import InstitutionSummary

logger = get_logger(__name__)

#: How much better-evidenced one record must be before confidence alone
#: decides. Below this the two are treated as equally supported, because a
#: hair's-breadth difference in a stored score is not a reason to pick a bank.
CONFIDENCE_MARGIN = 0.15


class Resolution(StrEnum):
    """Why one record won, or why neither did."""

    MORE_SPECIFIC = "more_specific"
    CURRENT_OVER_HISTORICAL = "current_over_historical"
    LATER_EFFECTIVE_DATE = "later_effective_date"
    SAME_INSTITUTION = "same_institution"
    NARROWER_RANGE = "narrower_range"
    BETTER_EVIDENCED = "better_evidenced"
    UNRESOLVED = "unresolved"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()

    @property
    def is_resolved(self) -> bool:
        return self is not Resolution.UNRESOLVED


@dataclass(slots=True)
class ConflictingClaim:
    """One side of a conflict, with everything needed to rank it."""

    institution: InstitutionSummary
    specificity: MatchSpecificity = MatchSpecificity.EXACT_ROOT
    span: int = 1
    confidence: float = 0.5

    @property
    def is_current(self) -> bool:
        return self.institution.is_current

    @property
    def effective_from(self) -> datetime | None:
        return self.institution.effective_from


@dataclass(slots=True)
class ConflictOutcome:
    """What the resolver concluded."""

    resolution: Resolution = Resolution.UNRESOLVED
    winner: ConflictingClaim | None = None
    #: Always both claims, winner or not. Nothing is discarded.
    claims: list[ConflictingClaim] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def is_resolved(self) -> bool:
        return self.resolution.is_resolved and self.winner is not None

    @property
    def summary(self) -> str:
        if not self.is_resolved:
            return (
                "Records disagree about the issuing institution and the "
                "difference cannot be settled from the data."
            )
        assert self.winner is not None
        return f"{self.winner.institution.display_name} — {self.resolution.label.lower()}."


class ConflictResolver:
    """Settles a conflict on structural grounds, or reports that it cannot."""

    def resolve(self, claims: list[ConflictingClaim]) -> ConflictOutcome:
        """Compare *claims* pairwise and return the outcome."""
        if len(claims) < 2:
            winner = claims[0] if claims else None
            return ConflictOutcome(
                resolution=Resolution.SAME_INSTITUTION if winner else Resolution.UNRESOLVED,
                winner=winner,
                claims=list(claims),
                reasons=["Only one claim; there is nothing to resolve."] if winner else [],
            )

        # The same institution appearing twice is not a disagreement.
        if len({claim.institution.id for claim in claims}) == 1:
            return ConflictOutcome(
                resolution=Resolution.SAME_INSTITUTION,
                winner=claims[0],
                claims=list(claims),
                reasons=["Both records name the same institution."],
            )

        best = claims[0]
        resolution = Resolution.UNRESOLVED
        reasons: list[str] = []
        for challenger in claims[1:]:
            outcome, reason = self._compare(best, challenger)
            if outcome is Resolution.UNRESOLVED:
                logger.debug(
                    "Conflict left unresolved",
                    extra={"context": {"reason": reason}},
                )
                return ConflictOutcome(
                    resolution=Resolution.UNRESOLVED,
                    claims=list(claims),
                    reasons=[*reasons, reason],
                )
            if outcome is not Resolution.SAME_INSTITUTION:
                # ``_compare`` returns the reason from the winner's point of
                # view, and ``best`` is updated to whichever won.
                best = self._winner(best, challenger, outcome)
                resolution = outcome
                reasons.append(reason)

        return ConflictOutcome(
            resolution=resolution,
            winner=best,
            claims=list(claims),
            reasons=reasons,
        )

    # -- pairwise comparison ----------------------------------------------
    def _compare(
        self, left: ConflictingClaim, right: ConflictingClaim
    ) -> tuple[Resolution, str]:
        if left.institution.id == right.institution.id:
            return Resolution.SAME_INSTITUTION, "Both records name the same institution."

        if left.specificity != right.specificity:
            winner = max(left, right, key=lambda claim: int(claim.specificity))
            return (
                Resolution.MORE_SPECIFIC,
                f"{winner.institution.display_name} is named by a "
                f"{winner.specificity.label.lower()} record",
            )

        if left.is_current != right.is_current:
            winner = left if left.is_current else right
            return (
                Resolution.CURRENT_OVER_HISTORICAL,
                f"{winner.institution.display_name} is the current relationship",
            )

        later = self._later_effective(left, right)
        if later is not None:
            return (
                Resolution.LATER_EFFECTIVE_DATE,
                f"{later.institution.display_name} has the later effective date",
            )

        if left.span != right.span:
            winner = min(left, right, key=lambda claim: claim.span)
            return (
                Resolution.NARROWER_RANGE,
                f"{winner.institution.display_name} is named by a narrower allocation",
            )

        if abs(left.confidence - right.confidence) >= CONFIDENCE_MARGIN:
            winner = max(left, right, key=lambda claim: claim.confidence)
            return (
                Resolution.BETTER_EVIDENCED,
                f"{winner.institution.display_name} is materially better evidenced",
            )

        return (
            Resolution.UNRESOLVED,
            "Both records are equally specific, equally current and equally "
            "evidenced; neither supersedes the other",
        )

    @staticmethod
    def _winner(
        left: ConflictingClaim, right: ConflictingClaim, resolution: Resolution
    ) -> ConflictingClaim:
        if resolution is Resolution.MORE_SPECIFIC:
            return max(left, right, key=lambda claim: int(claim.specificity))
        if resolution is Resolution.CURRENT_OVER_HISTORICAL:
            return left if left.is_current else right
        if resolution is Resolution.LATER_EFFECTIVE_DATE:
            return ConflictResolver._later_effective(left, right) or left
        if resolution is Resolution.NARROWER_RANGE:
            return min(left, right, key=lambda claim: claim.span)
        if resolution is Resolution.BETTER_EVIDENCED:
            return max(left, right, key=lambda claim: claim.confidence)
        return left

    @staticmethod
    def _later_effective(
        left: ConflictingClaim, right: ConflictingClaim
    ) -> ConflictingClaim | None:
        """Whichever claim took effect later, when both say and they differ."""
        left_from, right_from = left.effective_from, right.effective_from
        if left_from is None or right_from is None:
            return None
        left_aware = left_from if left_from.tzinfo else left_from.replace(tzinfo=UTC)
        right_aware = right_from if right_from.tzinfo else right_from.replace(tzinfo=UTC)
        if left_aware == right_aware:
            return None
        return left if left_aware > right_aware else right


#: Shared, stateless instance.
conflict_resolver = ConflictResolver()

__all__ = [
    "ConflictOutcome",
    "ConflictResolver",
    "ConflictingClaim",
    "Resolution",
    "conflict_resolver",
]
