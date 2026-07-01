"""Repository for tracking processed items."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from executive_copilot.database.tables import ProcessedItemTable


def _col(name: str) -> InstrumentedAttribute[Any]:
    """Get a typed column attribute from ProcessedItemTable."""
    attr: InstrumentedAttribute[Any] = getattr(ProcessedItemTable, name)
    return attr


class ProcessedItemRepository:
    """Tracks items that have been processed to avoid duplicate work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_processed(
        self,
        item_type: str,
        external_id: str,
        account_id: str,
    ) -> bool:
        """Check if an item has already been processed."""
        stmt = select(ProcessedItemTable).where(
            _col("item_type") == item_type,
            _col("external_id") == external_id,
            _col("account_id") == account_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def mark_processed(
        self,
        item_type: str,
        external_id: str,
        account_id: str,
        job_name: str | None = None,
        result: str | None = None,
    ) -> None:
        """Mark an item as processed."""
        item = ProcessedItemTable(
            id=uuid4(),
            created_at=datetime.utcnow(),
            item_type=item_type,
            external_id=external_id,
            account_id=account_id,
            processed_at=datetime.utcnow(),
            job_name=job_name,
            result=result,
        )
        self._session.add(item)

    async def get_processed_ids(
        self,
        item_type: str,
        account_id: str,
    ) -> set[str]:
        """Get all processed external IDs for a type and account."""
        stmt = select(_col("external_id")).where(
            _col("item_type") == item_type,
            _col("account_id") == account_id,
        )
        result = await self._session.execute(stmt)
        return {row[0] for row in result.fetchall()}
