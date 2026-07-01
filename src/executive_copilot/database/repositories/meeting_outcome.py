"""Meeting outcome repository."""

from datetime import datetime, timedelta

from sqlalchemy import or_, select

from executive_copilot.database.repositories.base import Repository
from executive_copilot.database.tables import MeetingDecisionTable, MeetingOutcomeTable
from executive_copilot.models.meeting import MeetingDecision, MeetingOutcome


class MeetingOutcomeRepository(Repository[MeetingOutcome, MeetingOutcomeTable]):
    """Repository for meeting outcomes."""

    @property
    def _table_class(self) -> type[MeetingOutcomeTable]:
        return MeetingOutcomeTable

    @property
    def _entity_name(self) -> str:
        return "MeetingOutcome"

    def _to_model(self, table: MeetingOutcomeTable) -> MeetingOutcome:
        """Convert database row to domain model."""
        return MeetingOutcome(
            id=table.id,
            created_at=table.created_at,
            updated_at=table.updated_at,
            event_id=table.event_id,
            event_title=table.event_title,
            meeting_date=table.meeting_date,
            participants=table.participants or [],
            organizer=table.organizer,
            summary=table.summary,
            decisions=table.decisions or [],
            next_steps=table.next_steps or [],
            key_points=table.key_points or [],
            account_id=table.account_id,
            organization_id=table.organization_id,
            recorded_at=table.recorded_at,
            tags=table.tags or [],
            notes=table.notes,
        )

    def _to_table(self, model: MeetingOutcome) -> MeetingOutcomeTable:
        """Convert domain model to database row."""
        return MeetingOutcomeTable(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            event_id=model.event_id,
            event_title=model.event_title,
            meeting_date=model.meeting_date,
            participants=model.participants,
            organizer=model.organizer,
            summary=model.summary,
            decisions=model.decisions,
            next_steps=model.next_steps,
            key_points=model.key_points,
            account_id=model.account_id,
            organization_id=model.organization_id,
            recorded_at=model.recorded_at,
            tags=model.tags,
            notes=model.notes,
        )

    async def get_by_event(self, event_id: str) -> MeetingOutcome | None:
        """Get outcome for a specific event."""
        stmt = select(self._table_class).where(
            self._col("event_id") == event_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return self._to_model(row) if row else None

    async def get_recent(
        self,
        days_back: int = 30,
        limit: int = 50,
    ) -> list[MeetingOutcome]:
        """Get recent meeting outcomes."""
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        stmt = (
            select(self._table_class)
            .where(self._col("meeting_date") >= cutoff)
            .order_by(self._col("meeting_date").desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_with_participant(
        self,
        email: str,
        limit: int = 20,
    ) -> list[MeetingOutcome]:
        """Get outcomes from meetings with a specific participant."""
        stmt = select(self._table_class).limit(limit * 3)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        matching = []
        email_lower = email.lower()
        for row in rows:
            if any(p.lower() == email_lower for p in (row.participants or [])):
                matching.append(self._to_model(row))
                if len(matching) >= limit:
                    break

        return matching


class MeetingDecisionRepository(Repository[MeetingDecision, MeetingDecisionTable]):
    """Repository for searchable meeting decisions."""

    @property
    def _table_class(self) -> type[MeetingDecisionTable]:
        return MeetingDecisionTable

    @property
    def _entity_name(self) -> str:
        return "MeetingDecision"

    def _to_model(self, table: MeetingDecisionTable) -> MeetingDecision:
        """Convert database row to domain model."""
        return MeetingDecision(
            id=table.id,
            created_at=table.created_at,
            updated_at=None,
            event_id=table.event_id,
            event_title=table.event_title,
            meeting_date=table.meeting_date,
            decision=table.decision,
            context=table.context,
            owner=table.owner,
            owner_email=table.owner_email,
            participants=table.participants or [],
            account_id=table.account_id,
            organization_id=table.organization_id,
            tags=table.tags or [],
        )

    def _to_table(self, model: MeetingDecision) -> MeetingDecisionTable:
        """Convert domain model to database row."""
        return MeetingDecisionTable(
            id=model.id,
            created_at=model.created_at,
            event_id=model.event_id,
            event_title=model.event_title,
            meeting_date=model.meeting_date,
            decision=model.decision,
            context=model.context,
            owner=model.owner,
            owner_email=model.owner_email,
            participants=model.participants,
            account_id=model.account_id,
            organization_id=model.organization_id,
            tags=model.tags,
        )

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[MeetingDecision]:
        """Search decisions by text.

        Args:
            query: Search term
            limit: Maximum results

        Returns:
            Matching decisions, newest first
        """
        pattern = f"%{query}%"
        stmt = (
            select(self._table_class)
            .where(
                or_(
                    self._col("decision").ilike(pattern),
                    self._col("context").ilike(pattern),
                    self._col("event_title").ilike(pattern),
                )
            )
            .order_by(self._col("meeting_date").desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_by_event(self, event_id: str) -> list[MeetingDecision]:
        """Get all decisions from a specific meeting."""
        stmt = (
            select(self._table_class)
            .where(self._col("event_id") == event_id)
            .order_by(self._col("created_at"))
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_recent(
        self,
        days_back: int = 30,
        limit: int = 50,
    ) -> list[MeetingDecision]:
        """Get recent decisions."""
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        stmt = (
            select(self._table_class)
            .where(self._col("meeting_date") >= cutoff)
            .order_by(self._col("meeting_date").desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_by_owner(
        self,
        email: str,
        limit: int = 20,
    ) -> list[MeetingDecision]:
        """Get decisions owned by a specific person."""
        stmt = (
            select(self._table_class)
            .where(self._col("owner_email") == email.lower())
            .order_by(self._col("meeting_date").desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]
