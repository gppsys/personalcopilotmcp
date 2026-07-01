"""Commitment repository."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from executive_copilot.database.repositories.base import Repository
from executive_copilot.database.tables import CommitmentTable
from executive_copilot.models.communication import (
    Commitment,
    CommitmentDirection,
    CommitmentStatus,
)


class CommitmentRepository(Repository[Commitment, CommitmentTable]):
    """Repository for commitment tracking."""

    @property
    def _table_class(self) -> type[CommitmentTable]:
        return CommitmentTable

    @property
    def _entity_name(self) -> str:
        return "Commitment"

    def _to_model(self, table: CommitmentTable) -> Commitment:
        """Convert database row to domain model."""
        return Commitment(
            id=table.id,
            created_at=table.created_at,
            updated_at=table.updated_at,
            title=table.title,
            description=table.description,
            original_text=table.original_text,
            direction=CommitmentDirection(table.direction),
            promiser_name=table.promiser_name,
            promiser_email=table.promiser_email,
            promisee_name=table.promisee_name,
            promisee_email=table.promisee_email,
            status=CommitmentStatus(table.status),
            due_date=table.due_date,
            completed_at=table.completed_at,
            source_type=table.source_type,
            source_id=table.source_id,
            source_date=table.source_date,
            organization_id=table.organization_id,
            auto_detected=table.auto_detected,
            detection_confidence=table.detection_confidence,
            tags=table.tags or [],
            notes=table.notes,
        )

    def _to_table(self, model: Commitment) -> CommitmentTable:
        """Convert domain model to database row."""
        return CommitmentTable(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            title=model.title,
            description=model.description,
            original_text=model.original_text,
            direction=model.direction.value,
            promiser_name=model.promiser_name,
            promiser_email=model.promiser_email,
            promisee_name=model.promisee_name,
            promisee_email=model.promisee_email,
            status=model.status.value,
            due_date=model.due_date,
            completed_at=model.completed_at,
            source_type=model.source_type,
            source_id=model.source_id,
            source_date=model.source_date,
            organization_id=model.organization_id,
            auto_detected=model.auto_detected,
            detection_confidence=model.detection_confidence,
            tags=model.tags,
            notes=model.notes,
        )

    async def get_my_commitments(
        self,
        include_completed: bool = False,
        limit: int = 50,
    ) -> list[Commitment]:
        """Get commitments I made to others."""
        stmt = select(self._table_class).where(
            self._col("direction") == CommitmentDirection.I_PROMISED.value
        )
        if not include_completed:
            stmt = stmt.where(
                self._col("status").in_([
                    CommitmentStatus.PENDING.value,
                    CommitmentStatus.IN_PROGRESS.value,
                ])
            )
        stmt = stmt.order_by(
            self._col("due_date").asc().nullslast(),
            self._col("created_at").desc(),
        )
        stmt = stmt.limit(limit)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_others_commitments(
        self,
        include_completed: bool = False,
        limit: int = 50,
    ) -> list[Commitment]:
        """Get commitments others made to me."""
        stmt = select(self._table_class).where(
            self._col("direction") == CommitmentDirection.THEY_PROMISED.value
        )
        if not include_completed:
            stmt = stmt.where(
                self._col("status").in_([
                    CommitmentStatus.PENDING.value,
                    CommitmentStatus.IN_PROGRESS.value,
                ])
            )
        stmt = stmt.order_by(
            self._col("due_date").asc().nullslast(),
            self._col("created_at").desc(),
        )
        stmt = stmt.limit(limit)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_overdue(self) -> list[Commitment]:
        """Get all overdue commitments (both directions)."""
        now = datetime.utcnow()
        stmt = select(self._table_class).where(
            self._col("status").in_([
                CommitmentStatus.PENDING.value,
                CommitmentStatus.IN_PROGRESS.value,
            ]),
            self._col("due_date") < now,
        )
        stmt = stmt.order_by(self._col("due_date").asc())

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_by_person(
        self,
        email: str,
        include_completed: bool = False,
    ) -> list[Commitment]:
        """Get commitments involving a specific person."""
        stmt = select(self._table_class).where(
            (self._col("promiser_email") == email)
            | (self._col("promisee_email") == email)
        )
        if not include_completed:
            stmt = stmt.where(
                self._col("status") != CommitmentStatus.COMPLETED.value
            )
        stmt = stmt.order_by(self._col("due_date").asc().nullslast())

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_by_source(
        self,
        source_type: str,
        source_id: UUID,
    ) -> list[Commitment]:
        """Get commitments from a specific source (email/transcript)."""
        stmt = select(self._table_class).where(
            self._col("source_type") == source_type,
            self._col("source_id") == source_id,
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[Commitment]:
        """Search commitments by title or description."""
        pattern = f"%{query}%"
        stmt = select(self._table_class).where(
            (self._col("title").ilike(pattern))
            | (self._col("description").ilike(pattern))
            | (self._col("original_text").ilike(pattern))
        )
        stmt = stmt.order_by(self._col("created_at").desc())
        stmt = stmt.limit(limit)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def exists_similar(
        self,
        title: str,
        source_type: str,
        direction: CommitmentDirection,
    ) -> bool:
        """Check if a similar commitment already exists (for deduplication)."""
        stmt = select(self._table_class).where(
            self._col("title") == title,
            self._col("source_type") == source_type,
            self._col("direction") == direction.value,
            self._col("status") != CommitmentStatus.COMPLETED.value,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
