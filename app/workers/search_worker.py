"""Workers for lookups and paginated table queries."""

from __future__ import annotations

from app.models.schemas import (
    BankLookupResult,
    BinFilters,
    BinLookupResult,
    BinRow,
    InstitutionStats,
    Page,
    PageRequest,
)
from app.services.bank_service import BankService
from app.services.lookup_service import LookupService
from app.workers.base import Worker


class BinSearchWorker(Worker[BinLookupResult]):
    """Runs a BIN/IIN lookup off the GUI thread."""

    def __init__(self, service: LookupService, query: str) -> None:
        super().__init__(service.lookup, query)
        self.query = query


class BankSearchWorker(Worker[BankLookupResult]):
    """Runs an institution-name search off the GUI thread."""

    def __init__(
        self,
        service: BankService,
        query: str,
        *,
        limit: int = 25,
        country_code: str | None = None,
    ) -> None:
        super().__init__(service.search, query, limit=limit, country_code=country_code)
        self.query = query


class BinPageWorker(Worker[Page[BinRow]]):
    """Fetches one page of an institution's BIN table."""

    def __init__(
        self,
        service: BankService,
        institution_id: int,
        request: PageRequest,
        filters: BinFilters | None = None,
    ) -> None:
        super().__init__(service.bins_page, institution_id, request, filters)
        self.institution_id = institution_id
        self.request = request


class InstitutionStatsWorker(Worker[InstitutionStats]):
    """Computes the summary counters shown above a bank result."""

    def __init__(self, service: BankService, institution_id: int) -> None:
        super().__init__(service.stats, institution_id)
        self.institution_id = institution_id


class FilterOptionsWorker(Worker[dict]):
    """Loads the distinct values that populate the table filter bar."""

    def __init__(self, service: BankService, institution_id: int | None = None) -> None:
        super().__init__(service.filter_options, institution_id)


class AllBinsWorker(Worker[list[BinRow]]):
    """Loads every BIN for an institution — export paths only."""

    def __init__(self, service: BankService, institution_id: int) -> None:
        super().__init__(service.all_bins, institution_id)
        self.institution_id = institution_id
