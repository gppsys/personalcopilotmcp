"""Base repository interface and implementation."""

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from executive_copilot.database.tables import SQLModelBase
from executive_copilot.exceptions import EntityNotFoundError, RepositoryError
from executive_copilot.logging import get_logger
from executive_copilot.models.base import DomainModel

logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=DomainModel)
TableT = TypeVar("TableT", bound=SQLModelBase)


class Repository(ABC, Generic[ModelT, TableT]):
    """Abstract base repository.

    Repositories abstract database access and handle mapping between
    domain models and database tables.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._logger = get_logger(f"{self.__class__.__module__}.{self.__class__.__name__}")

    @property
    @abstractmethod
    def _table_class(self) -> type[TableT]:
        """Get the SQLModel table class for this repository."""
        ...

    @property
    @abstractmethod
    def _entity_name(self) -> str:
        """Get human-readable entity name for error messages."""
        ...

    @abstractmethod
    def _to_model(self, table: TableT) -> ModelT:
        """Convert database table row to domain model."""
        ...

    @abstractmethod
    def _to_table(self, model: ModelT) -> TableT:
        """Convert domain model to database table row."""
        ...

    def _col(self, name: str) -> InstrumentedAttribute[Any]:
        """Get a typed column attribute from the table class."""
        attr: InstrumentedAttribute[Any] = getattr(self._table_class, name)
        return attr

    async def get_by_id(self, entity_id: UUID) -> ModelT | None:
        """Get entity by ID.

        Args:
            entity_id: Entity UUID

        Returns:
            Domain model if found, None otherwise
        """
        try:
            stmt = select(self._table_class).where(self._col("id") == entity_id)
            result = await self._session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_model(row)
        except Exception as e:
            raise RepositoryError(
                f"Failed to get {self._entity_name} by ID",
                repository_name=self.__class__.__name__,
                operation="get_by_id",
                cause=e,
            ) from e

    async def get_by_id_or_raise(self, entity_id: UUID) -> ModelT:
        """Get entity by ID, raising if not found.

        Args:
            entity_id: Entity UUID

        Returns:
            Domain model

        Raises:
            EntityNotFoundError: If entity not found
        """
        entity = await self.get_by_id(entity_id)
        if entity is None:
            raise EntityNotFoundError(
                f"{self._entity_name} not found",
                entity_type=self._entity_name,
                entity_id=str(entity_id),
            )
        return entity

    async def get_all(self, limit: int = 100, offset: int = 0) -> list[ModelT]:
        """Get all entities with pagination.

        Args:
            limit: Maximum results
            offset: Results offset

        Returns:
            List of domain models
        """
        try:
            stmt = select(self._table_class).limit(limit).offset(offset)
            result = await self._session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_model(row) for row in rows]
        except Exception as e:
            raise RepositoryError(
                f"Failed to get all {self._entity_name}s",
                repository_name=self.__class__.__name__,
                operation="get_all",
                cause=e,
            ) from e

    async def count(self) -> int:
        """Count all entities.

        Returns:
            Total entity count
        """
        try:
            stmt = select(func.count()).select_from(self._table_class)
            result = await self._session.execute(stmt)
            return result.scalar_one()
        except Exception as e:
            raise RepositoryError(
                f"Failed to count {self._entity_name}s",
                repository_name=self.__class__.__name__,
                operation="count",
                cause=e,
            ) from e

    async def add(self, model: ModelT) -> ModelT:
        """Add a new entity.

        Args:
            model: Domain model to add

        Returns:
            Added model (may have updated fields)
        """
        try:
            table = self._to_table(model)
            self._session.add(table)
            await self._session.flush()
            await self._session.refresh(table)
            return self._to_model(table)
        except Exception as e:
            raise RepositoryError(
                f"Failed to add {self._entity_name}",
                repository_name=self.__class__.__name__,
                operation="add",
                cause=e,
            ) from e

    async def create(self, model: ModelT) -> ModelT:
        """Create a new entity (alias for add)."""
        return await self.add(model)

    async def add_many(self, models: list[ModelT]) -> list[ModelT]:
        """Add multiple entities.

        Args:
            models: Domain models to add

        Returns:
            Added models
        """
        try:
            tables = [self._to_table(m) for m in models]
            self._session.add_all(tables)
            await self._session.flush()
            for table in tables:
                await self._session.refresh(table)
            return [self._to_model(t) for t in tables]
        except Exception as e:
            raise RepositoryError(
                f"Failed to add {self._entity_name}s",
                repository_name=self.__class__.__name__,
                operation="add_many",
                cause=e,
            ) from e

    async def update(self, model: ModelT) -> ModelT:
        """Update an existing entity.

        Args:
            model: Domain model with updates

        Returns:
            Updated model
        """
        try:
            table = self._to_table(model)
            await self._session.merge(table)
            await self._session.flush()
            return model
        except Exception as e:
            raise RepositoryError(
                f"Failed to update {self._entity_name}",
                repository_name=self.__class__.__name__,
                operation="update",
                cause=e,
            ) from e

    async def delete(self, entity_id: UUID) -> bool:
        """Delete an entity by ID.

        Args:
            entity_id: Entity to delete

        Returns:
            True if deleted, False if not found
        """
        try:
            stmt = delete(self._table_class).where(self._col("id") == entity_id)
            cursor = cast("CursorResult[Any]", await self._session.execute(stmt))
            await self._session.flush()
            return cursor.rowcount > 0
        except Exception as e:
            raise RepositoryError(
                f"Failed to delete {self._entity_name}",
                repository_name=self.__class__.__name__,
                operation="delete",
                cause=e,
            ) from e

    async def exists(self, entity_id: UUID) -> bool:
        """Check if entity exists.

        Args:
            entity_id: Entity ID to check

        Returns:
            True if exists
        """
        try:
            stmt = select(func.count()).select_from(self._table_class).where(
                self._col("id") == entity_id
            )
            result = await self._session.execute(stmt)
            return result.scalar_one() > 0
        except Exception as e:
            raise RepositoryError(
                f"Failed to check {self._entity_name} existence",
                repository_name=self.__class__.__name__,
                operation="exists",
                cause=e,
            ) from e

    async def update_fields(
        self,
        entity_id: UUID,
        **fields: Any,
    ) -> bool:
        """Update specific fields on an entity.

        Args:
            entity_id: Entity to update
            **fields: Fields to update

        Returns:
            True if updated, False if not found
        """
        try:
            stmt = (
                update(self._table_class)
                .where(self._col("id") == entity_id)
                .values(**fields)
            )
            cursor = cast("CursorResult[Any]", await self._session.execute(stmt))
            await self._session.flush()
            return cursor.rowcount > 0
        except Exception as e:
            raise RepositoryError(
                f"Failed to update {self._entity_name} fields",
                repository_name=self.__class__.__name__,
                operation="update_fields",
                cause=e,
            ) from e
