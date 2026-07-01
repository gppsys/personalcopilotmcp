"""Follow-up repository."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from executive_copilot.database.repositories.base import Repository
from executive_copilot.database.tables import FollowUpTable
from executive_copilot.models.communication import FollowUp, FollowUpPriority, FollowUpStatus


class FollowUpRepository(Repository[FollowUp, FollowUpTable]):
    """Repository for follow-up items."""

    @property
    def _table_class(self) -> type[FollowUpTable]:
        return FollowUpTable

    @property
    def _entity_name(self) -> str:
        return "FollowUp"

    def _to_model(self, table: FollowUpTable) -> FollowUp:
        """Convert database row to domain model."""
        return FollowUp(
            id=table.id,
            created_at=table.created_at,
            updated_at=table.updated_at,
            title=table.title,
            description=table.description,
            context=table.context,
            status=FollowUpStatus(table.status),
            priority=FollowUpPriority(table.priority),
            due_date=table.due_date,
            snoozed_until=table.snoozed_until,
            completed_at=table.completed_at,
            source_type=table.source_type,
            source_id=table.source_id,
            detected_from=table.detected_from,
            with_person_id=table.with_person_id,
            with_person_name=table.with_person_name,
            with_person_email=table.with_person_email,
            organization_id=table.organization_id,
            task_id=table.task_id,
            auto_detected=table.auto_detected,
            detection_confidence=table.detection_confidence,
            tags=table.tags or [],
            notes=table.notes,
        )

    def _to_table(self, model: FollowUp) -> FollowUpTable:
        """Convert domain model to database row."""
        return FollowUpTable(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            title=model.title,
            description=model.description,
            context=model.context,
            status=model.status.value,
            priority=model.priority.value,
            due_date=model.due_date,
            snoozed_until=model.snoozed_until,
            completed_at=model.completed_at,
            source_type=model.source_type,
            source_id=model.source_id,
            detected_from=model.detected_from,
            with_person_id=model.with_person_id,
            with_person_name=model.with_person_name,
            with_person_email=model.with_person_email,
            organization_id=model.organization_id,
            task_id=model.task_id,
            auto_detected=model.auto_detected,
            detection_confidence=model.detection_confidence,
            tags=model.tags,
            notes=model.notes,
        )

    async def get_pending(
        self,
        limit: int = 50,
        include_snoozed: bool = False,
    ) -> list[FollowUp]:
        """Get pending follow-ups.

        Args:
            limit: Maximum results
            include_snoozed: Include snoozed items

        Returns:
            List of pending follow-ups ordered by due date
        """
        stmt = select(self._table_class).where(
            self._col("status").in_(
                [FollowUpStatus.PENDING.value, FollowUpStatus.IN_PROGRESS.value]
                + ([FollowUpStatus.SNOOZED.value] if include_snoozed else [])
            )
        )
        stmt = stmt.order_by(
            self._col("due_date").asc().nullslast(),
            self._col("priority").desc(),
            self._col("created_at").desc(),
        )
        stmt = stmt.limit(limit)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_overdue(self) -> list[FollowUp]:
        """Get overdue follow-ups."""
        now = datetime.utcnow()
        stmt = select(self._table_class).where(
            self._col("status").in_(
                [FollowUpStatus.PENDING.value, FollowUpStatus.IN_PROGRESS.value]
            ),
            self._col("due_date") < now,
        )
        stmt = stmt.order_by(self._col("due_date").asc())

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_by_person(
        self,
        person_email: str,
        include_completed: bool = False,
    ) -> list[FollowUp]:
        """Get follow-ups with a specific person."""
        stmt = select(self._table_class).where(
            self._col("with_person_email") == person_email
        )
        if not include_completed:
            stmt = stmt.where(
                self._col("status") != FollowUpStatus.COMPLETED.value
            )
        stmt = stmt.order_by(self._col("due_date").asc().nullslast())

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_by_organization(
        self,
        organization_id: UUID,
        include_completed: bool = False,
    ) -> list[FollowUp]:
        """Get follow-ups for an organization."""
        stmt = select(self._table_class).where(
            self._col("organization_id") == organization_id
        )
        if not include_completed:
            stmt = stmt.where(
                self._col("status") != FollowUpStatus.COMPLETED.value
            )
        stmt = stmt.order_by(self._col("due_date").asc().nullslast())

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[FollowUp]:
        """Search follow-ups by title or description."""
        pattern = f"%{query}%"
        stmt = select(self._table_class).where(
            (self._col("title").ilike(pattern))
            | (self._col("description").ilike(pattern))
        )
        stmt = stmt.order_by(self._col("created_at").desc())
        stmt = stmt.limit(limit)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]
