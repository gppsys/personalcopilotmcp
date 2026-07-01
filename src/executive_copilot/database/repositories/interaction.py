"""Interaction repository for relationship tracking."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from executive_copilot.database.repositories.base import Repository
from executive_copilot.database.tables import InteractionTable
from executive_copilot.models.relationship import (
    Interaction,
    InteractionDirection,
    InteractionType,
)


class InteractionRepository(Repository[Interaction, InteractionTable]):
    """Repository for interaction tracking."""

    @property
    def _table_class(self) -> type[InteractionTable]:
        return InteractionTable

    @property
    def _entity_name(self) -> str:
        return "Interaction"

    def _to_model(self, table: InteractionTable) -> Interaction:
        """Convert database row to domain model."""
        return Interaction(
            id=table.id,
            created_at=table.created_at,
            updated_at=None,
            person_email=table.person_email,
            person_name=table.person_name,
            interaction_type=InteractionType(table.interaction_type),
            direction=InteractionDirection(table.direction),
            occurred_at=table.occurred_at,
            subject=table.subject,
            summary=table.summary,
            snippet=table.snippet,
            source_type=table.source_type,
            source_id=table.source_id,
            account_id=table.account_id,
            organization_id=table.organization_id,
            tags=table.tags or [],
        )

    def _to_table(self, model: Interaction) -> InteractionTable:
        """Convert domain model to database row."""
        return InteractionTable(
            id=model.id,
            created_at=model.created_at,
            person_email=model.person_email,
            person_name=model.person_name,
            interaction_type=model.interaction_type.value,
            direction=model.direction.value,
            occurred_at=model.occurred_at,
            subject=model.subject,
            summary=model.summary,
            snippet=model.snippet,
            source_type=model.source_type,
            source_id=model.source_id,
            account_id=model.account_id,
            organization_id=model.organization_id,
            tags=model.tags,
        )

    async def get_person_history(
        self,
        email: str,
        limit: int = 10,
    ) -> list[Interaction]:
        """Get recent interactions with a person.

        Args:
            email: Person's email address
            limit: Maximum results

        Returns:
            Recent interactions, newest first
        """
        stmt = (
            select(self._table_class)
            .where(self._col("person_email") == email.lower())
            .order_by(self._col("occurred_at").desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_last_interaction(self, email: str) -> Interaction | None:
        """Get the most recent interaction with a person.

        Args:
            email: Person's email address

        Returns:
            Most recent interaction or None
        """
        stmt = (
            select(self._table_class)
            .where(self._col("person_email") == email.lower())
            .order_by(self._col("occurred_at").desc())
            .limit(1)
        )

        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return self._to_model(row) if row else None

    async def count_interactions(
        self,
        email: str,
        direction: InteractionDirection | None = None,
    ) -> int:
        """Count interactions with a person.

        Args:
            email: Person's email address
            direction: Optional filter by direction

        Returns:
            Interaction count
        """
        stmt = select(func.count()).select_from(self._table_class).where(
            self._col("person_email") == email.lower()
        )

        if direction:
            stmt = stmt.where(self._col("direction") == direction.value)

        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def exists_for_source(
        self,
        source_type: str,
        source_id: UUID,
        person_email: str,
    ) -> bool:
        """Check if an interaction already exists for a source.

        Used for deduplication when scanning emails/meetings.
        """
        stmt = select(func.count()).select_from(self._table_class).where(
            self._col("source_type") == source_type,
            self._col("source_id") == source_id,
            self._col("person_email") == person_email.lower(),
        )

        result = await self._session.execute(stmt)
        return result.scalar_one() > 0

    async def get_recent_contacts(
        self,
        limit: int = 20,
        days_back: int = 7,
    ) -> list[tuple[str, datetime, str]]:
        """Get recently contacted people.

        Args:
            limit: Maximum results
            days_back: How far back to look

        Returns:
            List of (email, last_contact, contact_type) tuples
        """
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(days=days_back)

        stmt = (
            select(
                self._col("person_email"),
                func.max(self._col("occurred_at")).label("last_contact"),
                self._col("interaction_type"),
            )
            .where(self._col("occurred_at") >= cutoff)
            .group_by(self._col("person_email"), self._col("interaction_type"))
            .order_by(func.max(self._col("occurred_at")).desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        return [(row[0], row[1], row[2]) for row in result.fetchall()]
