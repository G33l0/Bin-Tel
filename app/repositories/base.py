"""Repository base class.

A repository owns a :class:`~app.database.engine.DatabaseManager`, opens a
short-lived session per operation and returns Pydantic DTOs. ORM instances
never escape this layer, so no UI widget can accidentally trigger lazy loading
on a closed session.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.database.engine import DatabaseManager
from app.models.schemas import Page, PageRequest

T = TypeVar("T")


class BaseRepository:
    """Shared session handling and pagination helpers."""

    def __init__(self, manager: DatabaseManager) -> None:
        self._manager = manager

    @property
    def manager(self) -> DatabaseManager:
        return self._manager

    @property
    def is_available(self) -> bool:
        return self._manager.is_open

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._manager.session() as session:
            yield session

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self._manager.transaction() as session:
            yield session

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def count_of(session: Session, statement: Select[Any]) -> int:
        """Total rows a SELECT would return, without fetching them."""
        subquery = statement.order_by(None).limit(None).offset(None).subquery()
        return int(session.execute(select(func.count()).select_from(subquery)).scalar() or 0)

    def paginate(
        self,
        session: Session,
        statement: Select[Any],
        request: PageRequest,
        mapper: Any,
    ) -> Page[Any]:
        """Run *statement* for one page and wrap the rows in a :class:`Page`."""
        total = self.count_of(session, statement)
        rows: Sequence[Any] = (
            session.execute(statement.limit(request.page_size).offset(request.offset))
            .scalars()
            .unique()
            .all()
        )
        return Page(
            items=[mapper(row) for row in rows],
            total=total,
            page=request.page,
            page_size=request.page_size,
        )
