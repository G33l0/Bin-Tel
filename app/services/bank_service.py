"""Financial-institution (bank) lookup and its associated BIN listings."""

from __future__ import annotations

import time

from app.core.logging_config import get_logger
from app.models.schemas import (
    BankLookupResult,
    BinFilters,
    BinRow,
    InstitutionDetail,
    InstitutionStats,
    Page,
    PageRequest,
)
from app.repositories.bin_repository import BinRepository
from app.repositories.institution_repository import InstitutionRepository
from app.utils.validators import validate_search_term

logger = get_logger(__name__)


class BankService:
    """Search institutions, then page through the BINs that belong to them."""

    def __init__(
        self, institutions: InstitutionRepository, bins: BinRepository
    ) -> None:
        self._institutions = institutions
        self._bins = bins

    @property
    def available(self) -> bool:
        return self._institutions.is_available

    def search(self, term: str, *, limit: int = 25, country_code: str | None = None) -> BankLookupResult:
        started = time.perf_counter()
        cleaned = validate_search_term(term)
        matches = self._institutions.search(cleaned, limit=limit, country_code=country_code)
        elapsed = (time.perf_counter() - started) * 1000
        logger.debug(
            "Bank search completed",
            extra={"context": {"results": len(matches), "elapsed_ms": round(elapsed, 2)}},
        )
        return BankLookupResult(query=cleaned, matches=tuple(matches), elapsed_ms=elapsed)

    def get(self, institution_id: int) -> InstitutionDetail | None:
        return self._institutions.get(institution_id)

    def get_by_uid(self, uid: str) -> InstitutionDetail | None:
        """Resolve an institution by its stable uid (used by watchlists)."""
        return self._institutions.get_by_uid(uid)

    def uid_for(self, institution_id: int) -> str | None:
        return self._institutions.uid_for(institution_id)

    def bins_page(
        self,
        institution_id: int,
        request: PageRequest,
        filters: BinFilters | None = None,
    ) -> Page[BinRow]:
        return self._bins.page_for_institution(institution_id, request, filters)

    def all_bins(self, institution_id: int) -> list[BinRow]:
        """Full BIN list for an institution — export paths only."""
        return self._bins.all_bins_for_institution(institution_id)

    def stats(self, institution_id: int) -> InstitutionStats:
        raw = self._bins.institution_bin_stats(institution_id)
        return InstitutionStats(**raw)

    def filter_options(self, institution_id: int | None = None) -> dict[str, list[tuple[str, str]]]:
        return self._bins.filter_options(institution_id)

    def browse(self, request: PageRequest, *, country_code: str | None = None) -> Page[InstitutionDetail]:
        return self._institutions.page(request, country_code=country_code)
