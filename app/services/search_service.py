"""Advanced search, wired to entitlements and the user's workspace."""

from __future__ import annotations

import time

from app.core.logging_config import get_logger
from app.licensing.entitlements import EntitlementService
from app.licensing.plans import Feature, Limit
from app.models.schemas import (
    AdvancedQuery,
    AdvancedSearchResult,
    BinRow,
    Page,
    PageRequest,
)
from app.models.user_entities import SearchKind
from app.repositories.search_repository import SearchRepository
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)


class SearchService:
    """Runs advanced searches and records them in the local workspace."""

    def __init__(
        self,
        repository: SearchRepository,
        workspace: WorkspaceService,
        entitlements: EntitlementService,
    ) -> None:
        self._repository = repository
        self._workspace = workspace
        self._entitlements = entitlements

    @property
    def available(self) -> bool:
        return self._repository.is_available

    @property
    def is_entitled(self) -> bool:
        return self._entitlements.has_feature(Feature.ADVANCED_SEARCH)

    def search(
        self,
        query: AdvancedQuery,
        request: PageRequest | None = None,
        *,
        record: bool = True,
        history_enabled: bool = True,
        history_size: int = 25,
    ) -> AdvancedSearchResult:
        started = time.perf_counter()
        request = request or PageRequest()
        page = self._repository.search(query, request)
        elapsed = (time.perf_counter() - started) * 1000

        if record and not query.is_empty:
            self._workspace.record_search(
                query.describe(),
                SearchKind.ADVANCED,
                result_count=page.total,
                elapsed_ms=elapsed,
                enabled=history_enabled,
                keep=history_size,
            )
        logger.debug(
            "Advanced search completed",
            extra={"context": {"results": page.total, "elapsed_ms": round(elapsed, 1)}},
        )
        return AdvancedSearchResult(query=query, page=page, elapsed_ms=elapsed)

    def count(self, query: AdvancedQuery) -> int:
        return self._repository.count(query)

    def export_rows(self, query: AdvancedQuery) -> list[BinRow]:
        """Rows for an export, capped at the plan's export quota."""
        limit = self._entitlements.limit(Limit.EXPORT_ROWS, 500)
        maximum = 250_000 if limit < 0 else limit
        return self._repository.all_rows(query, limit=maximum)

    def export_cap(self) -> int | None:
        """The export row cap, or ``None`` when unlimited."""
        limit = self._entitlements.limit(Limit.EXPORT_ROWS, 500)
        return None if limit < 0 else limit

    def suggest(self, term: str, limit: int = 8) -> list[tuple[str, str, str]]:
        return self._repository.suggest(term, limit)

    def filter_values(self) -> dict[str, list[tuple[str, str]]]:
        return self._repository.filter_values()

    def page(self, query: AdvancedQuery, request: PageRequest) -> Page[BinRow]:
        return self._repository.search(query, request)
