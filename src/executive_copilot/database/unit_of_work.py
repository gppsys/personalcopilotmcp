"""Unit of Work pattern implementation."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from executive_copilot.database.connection import DatabaseManager
from executive_copilot.database.repositories.email import EmailRepository, ThreadRepository
from executive_copilot.database.repositories.organization import OrganizationRepository
from executive_copilot.database.repositories.person import PersonRepository
from executive_copilot.logging import get_logger

logger = get_logger(__name__)


class UnitOfWork:
    """Unit of Work for coordinating repository operations.

    Ensures all repository operations within a unit of work
    share the same database session and transaction boundary.

    Usage:
        async with UnitOfWork(db_manager) as uow:
            person = await uow.persons.get_by_id(id)
            person = person.with_update(name="New Name")
            await uow.persons.update(person)
            await uow.commit()
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._committed = False

        # Initialize repositories
        self._persons: PersonRepository | None = None
        self._organizations: OrganizationRepository | None = None
        self._emails: EmailRepository | None = None
        self._threads: ThreadRepository | None = None

    @property
    def persons(self) -> PersonRepository:
        """Get person repository."""
        if self._persons is None:
            self._persons = PersonRepository(self._session)
        return self._persons

    @property
    def organizations(self) -> OrganizationRepository:
        """Get organization repository."""
        if self._organizations is None:
            self._organizations = OrganizationRepository(self._session)
        return self._organizations

    @property
    def emails(self) -> EmailRepository:
        """Get email repository."""
        if self._emails is None:
            self._emails = EmailRepository(self._session)
        return self._emails

    @property
    def threads(self) -> ThreadRepository:
        """Get thread repository."""
        if self._threads is None:
            self._threads = ThreadRepository(self._session)
        return self._threads

    async def commit(self) -> None:
        """Commit the current transaction."""
        if self._committed:
            raise RuntimeError("Unit of work already committed")
        await self._session.commit()
        self._committed = True
        logger.debug("Unit of work committed")

    async def rollback(self) -> None:
        """Rollback the current transaction."""
        await self._session.rollback()
        logger.debug("Unit of work rolled back")

    async def flush(self) -> None:
        """Flush pending changes without committing."""
        await self._session.flush()


@asynccontextmanager
async def unit_of_work(db_manager: DatabaseManager) -> AsyncGenerator[UnitOfWork, None]:
    """Context manager for unit of work.

    Args:
        db_manager: Database manager instance

    Yields:
        UnitOfWork instance

    Usage:
        async with unit_of_work(db_manager) as uow:
            # Use uow.persons, uow.organizations, etc.
            await uow.commit()
    """
    async with db_manager.session() as session:
        uow = UnitOfWork(session)
        try:
            yield uow
        except Exception:
            await uow.rollback()
            raise
        finally:
            if not uow._committed:
                await uow.rollback()
