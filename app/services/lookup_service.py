"""BIN/IIN lookup.

Thin, synchronous and thread-safe: the UI never calls this on the GUI thread,
it hands the call to a worker in :mod:`app.workers`.

The algorithm, in the order it actually runs:

1. Refuse anything card-length outright.
2. Normalize and validate; keep the length the user asked about.
3. Gather every allocation that *contains* the query — an assignment of the
   same length, a shorter root whose span covers it, and any allocated range.
4. Rank them by specificity: an exact eight-digit assignment or an account
   range beats the six-digit root it sits under, always.
5. Resolve the winner's relationships, current issuing ones first.
6. Detect disagreement between equally specific records and report it rather
   than picking a side.
7. Score the answer from the evidence that produced it.
8. Where nothing supports an institution, say so. Never guess one.
"""

from __future__ import annotations

import time

from app.core.logging_config import get_logger
from app.lookup.evidence import LookupConfidence
from app.lookup.resolution import Resolution, range_resolver
from app.lookup.strategy import LookupStrategy
from app.models.schemas import BinLookupResult, BinRecord
from app.repositories.bin_repository import BinRepository
from app.utils.validators import BinInput, is_sensitive_length, validate_bin

logger = get_logger(__name__)

#: A numeric input this long is refused outright rather than truncated — Bin-Tel
#: must never accept, echo or store a full card number.
_REFUSAL = (
    "Bin-Tel looks up issuer identification numbers only. "
    "Enter the first 6 or 8 digits, never a full card number."
)


class LookupService:
    """Resolves a BIN/IIN query to the institutions the evidence supports."""

    def __init__(self, repository: BinRepository) -> None:
        self._repository = repository

    @property
    def available(self) -> bool:
        return self._repository.is_available

    def lookup(self, query: str) -> BinLookupResult:
        """Resolve *query* to every relationship its allocation supports."""
        started = time.perf_counter()
        if is_sensitive_length(query):
            from app.core.errors import ValidationError

            raise ValidationError(_REFUSAL)

        parsed: BinInput = validate_bin(query)
        digits = parsed.digits

        candidates = self._repository.candidates(digits)
        resolution = range_resolver.resolve(digits, candidates)

        # A six-digit query may sit above eight-digit assignments belonging to
        # other issuers. They never answer the query, but the interface should
        # be able to say they exist rather than implying the root is the whole
        # truth.
        more_specific = 0
        if len(digits) < 8:
            more_specific = len(self._repository.children_of(digits, limit=200))

        return self._result(query, resolution, more_specific, started)

    def resolve(self, query: str) -> Resolution:
        """The full internal resolution, for callers that need the reasoning."""
        parsed = validate_bin(query)
        return range_resolver.resolve(
            parsed.digits, self._repository.candidates(parsed.digits)
        )

    def exists(self, query: str) -> bool:
        return self._repository.exists(validate_bin(query).digits)

    # -- assembly ---------------------------------------------------------
    @staticmethod
    def _result(
        query: str,
        resolution: Resolution,
        more_specific: int,
        started: float,
    ) -> BinLookupResult:
        elapsed = (time.perf_counter() - started) * 1000
        records: tuple[BinRecord, ...] = ()
        if resolution.record is not None:
            # The winner first, then the broader allocations that also covered
            # the query, so a caller reading records[0] always gets the most
            # specific answer.
            others = tuple(
                candidate.record
                for candidate in resolution.other_candidates
                if candidate.record.id != resolution.record.id
            )
            winner = resolution.record
            if resolution.relationships:
                winner = winner.model_copy(
                    update={"institutions": resolution.relationships}
                )
            records = (winner, *others)

        logger.debug(
            "BIN lookup completed",
            extra={
                "context": {
                    "query_length": len(query),
                    "strategy": resolution.strategy.value,
                    "confidence": resolution.confidence.level.value,
                    "institutions": resolution.institution_count,
                    "results": len(records),
                    "elapsed_ms": round(elapsed, 2),
                }
            },
        )
        return BinLookupResult(
            query=query,
            records=records,
            matched_by=_legacy_matched_by(resolution.strategy),
            elapsed_ms=elapsed,
            strategy=resolution.strategy.value,
            match_label=resolution.match_label,
            confidence_score=resolution.confidence.score,
            confidence_level=resolution.confidence.level.value,
            confidence_reasons=tuple(resolution.confidence.reasons),
            conflicting_institutions=resolution.conflicting_institutions,
            more_specific_count=more_specific,
        )


def _legacy_matched_by(strategy: LookupStrategy) -> str:
    """The coarse ``exact``/``prefix``/``range``/``none`` shape kept for callers
    written against the original result object."""
    if strategy.is_exact:
        return "exact"
    if strategy in (LookupStrategy.ACCOUNT_RANGE, LookupStrategy.BROADER_RANGE):
        return "range"
    if strategy is LookupStrategy.NONE:
        return "none"
    return "prefix"


__all__ = ["LookupConfidence", "LookupService"]
