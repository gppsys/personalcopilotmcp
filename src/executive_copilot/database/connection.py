"""Database connection management."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, Pool, QueuePool

from executive_copilot.config import DatabaseConfig
from executive_copilot.logging import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """Manages database connections and sessions.

    Provides async session management with proper connection pooling.
    Designed to support both SQLite and PostgreSQL.
    """

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._engine: AsyncEngine = self._create_engine()
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    def _create_engine(self) -> AsyncEngine:
        """Create async database engine."""
        url = self._config.url

        # Use different pool settings for SQLite vs PostgreSQL
        poolclass: type[Pool]
        if self._config.driver.startswith("sqlite"):
            poolclass = NullPool
            connect_args = {"check_same_thread": False}
            pool_kwargs: dict[str, object] = {}
        else:
            poolclass = QueuePool
            connect_args = {}
            pool_kwargs = {
                "pool_size": self._config.pool_size,
                "max_overflow": self._config.max_overflow,
            }

        return create_async_engine(
            url,
            echo=self._config.echo,
            poolclass=poolclass,
            connect_args=connect_args,
            **pool_kwargs,
        )

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a database session.

        Yields:
            AsyncSession for database operations

        Usage:
            async with db_manager.session() as session:
                # use session
        """
        session: AsyncSession = self._session_factory()
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a session with automatic commit on success.

        Yields:
            AsyncSession that commits on successful exit

        Usage:
            async with db_manager.transaction() as session:
                # changes will be committed
        """
        async with self.session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def create_tables(self) -> None:
        """Create all database tables and add any missing columns.

        ``create_all`` only creates tables that don't exist; it never alters
        existing tables. When a column is added to a table model, existing
        databases silently drift from the model and queries crash with
        "no such column". ``_migrate_missing_columns`` closes that gap by
        issuing ``ALTER TABLE ... ADD COLUMN`` for declared columns missing
        from the live schema.
        """
        # Import the full tables module to ensure all table classes are registered
        from executive_copilot.database import tables

        async with self._engine.begin() as conn:
            await conn.run_sync(tables.SQLModelBase.metadata.create_all)
            await conn.run_sync(self._migrate_missing_columns)
        logger.info("Database tables created")

    @staticmethod
    def _migrate_missing_columns(conn: Any) -> None:
        """Add columns declared in models but missing from the database.

        Only performs additive ``ALTER TABLE ... ADD COLUMN`` operations,
        which are safe on both SQLite and PostgreSQL. Column removals or
        type changes still require a manual migration.
        """
        from sqlalchemy import ColumnDefault, inspect, text

        from executive_copilot.database import tables

        inspector = inspect(conn)
        existing_tables = set(inspector.get_table_names())

        for table in tables.SQLModelBase.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_cols = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_cols:
                    continue
                ddl = (
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                    f"{column.type.compile(conn.dialect)}"
                )
                if isinstance(column.default, ColumnDefault) and column.default.is_scalar:
                    ddl += f" DEFAULT {column.default.arg!r}"
                conn.execute(text(ddl))
                logger.info(
                    "Added missing column",
                    table=table.name,
                    column=column.name,
                )

    async def drop_tables(self) -> None:
        """Drop all database tables."""
        from executive_copilot.database import tables

        async with self._engine.begin() as conn:
            await conn.run_sync(tables.SQLModelBase.metadata.drop_all)
        logger.info("Database tables dropped")

    async def close(self) -> None:
        """Close database connections."""
        await self._engine.dispose()
        logger.info("Database connections closed")


# Global database manager instance
_db_manager: DatabaseManager | None = None


def get_database_manager() -> DatabaseManager:
    """Get the global database manager instance.

    Raises:
        RuntimeError: If database not initialized
    """
    if _db_manager is None:
        raise RuntimeError("Database manager not initialized. Call init_database() first.")
    return _db_manager


def init_database(config: DatabaseConfig) -> DatabaseManager:
    """Initialize the global database manager.

    Args:
        config: Database configuration

    Returns:
        Initialized DatabaseManager
    """
    global _db_manager
    _db_manager = DatabaseManager(config)
    logger.info("Database initialized", driver=config.driver)
    return _db_manager
