"""BIN/IIN lookup.

Thin, synchronous and thread-safe: the UI never calls this on the GUI thread,
it hands the call to a worker in :mod:`app.workers`.
"""

from __future__ import annotations

import time

from app.core.logging_config import get_logger
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
    """Resolves a BIN/IIN query to one or more :class:`BinRecord` results."""

    def __init__(self, repository: BinRepository) -> None:
        self._repository = repository

    @property
    def available(self) -> bool:
        return self._repository.is_available

    def lookup(self, query: str) -> BinLookupResult:
        """Exact match first, then prefix, then an allocated range."""
        started = time.perf_counter()
        if is_sensitive_length(query):
            from app.core.errors import ValidationError

            raise ValidationError(_REFUSAL)

        parsed: BinInput = validate_bin(query)
        digits = parsed.digits

        exact = self._repository.find_exact(digits)
        if exact is not None:
            return self._result(query, (exact,), "exact", started)

        prefix_matches = self._repository.find_by_prefix(digits)
        if prefix_matches:
            return self._result(query, tuple(prefix_matches), "prefix", started)

        range_matches = self._repository.find_by_range(digits)
        if range_matches:
            return self._result(query, tuple(range_matches), "range", started)

        return self._result(query, (), "none", started)

    def exists(self, query: str) -> bool:
        return self._repository.exists(validate_bin(query).digits)

    @staticmethod
    def _result(
        query: str, records: tuple[BinRecord, ...], matched_by: str, started: float
    ) -> BinLookupResult:
        elapsed = (time.perf_counter() - started) * 1000
        logger.debug(
            "BIN lookup completed",
            extra={
                "context": {
                    "query_length": len(query),
                    "matched_by": matched_by,
                    "results": len(records),
                    "elapsed_ms": round(elapsed, 2),
                }
            },
        )
        return BinLookupResult(
            query=query, records=records, matched_by=matched_by, elapsed_ms=elapsed
        )
