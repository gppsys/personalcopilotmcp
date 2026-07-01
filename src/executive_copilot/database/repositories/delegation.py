"""Delegation repository."""

from datetime import datetime

from sqlalchemy import func, select

from executive_copilot.database.repositories.base import Repository
from executive_copilot.database.tables import DelegationTable
from executive_copilot.models.delegation import (
    Delegation,
    DelegationPriority,
    DelegationStatus,
    DelegationSummary,
)


class DelegationRepository(Repository[Delegation, DelegationTable]):
    """Repository for delegation tracking."""

    @property
    def _table_class(self) -> type[DelegationTable]:
        return DelegationTable

    @property
    def _entity_name(self) -> str:
        return "Delegation"

    def _to_model(self, table: DelegationTable) -> Delegation:
        """Convert database row to domain model."""
        return Delegation(
            id=table.id,
            created_at=table.created_at,
            updated_at=table.updated_at,
            title=table.title,
            description=table.description,
            original_text=table.original_text,
            assignee_name=table.assignee_name,
            assignee_email=table.assignee_email,
            status=DelegationStatus(table.status),
            priority=DelegationPriority(table.priority),
            delegated_at=table.delegated_at,
            due_date=table.due_date,
            completed_at=table.completed_at,
            source_type=table.source_type,
            source_id=table.source_id,
            account_id=table.account_id,
            organization_id=table.organization_id,
            auto_detected=table.auto_detected,
            detection_confidence=table.detection_confidence,
            tags=table.tags or [],
            notes=table.notes,
        )

    def _to_table(self, model: Delegation) -> DelegationTable:
        """Convert domain model to database row."""
        return DelegationTable(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            title=model.title,
            description=model.description,
            original_text=model.original_text,
            assignee_name=model.assignee_name,
            assignee_email=model.assignee_email,
            status=model.status.value,
            priority=model.priority.value,
            delegated_at=model.delegated_at,
            due_date=model.due_date,
            completed_at=model.completed_at,
            source_type=model.source_type,
            source_id=model.source_id,
            account_id=model.account_id,
            organization_id=model.organization_id,
            auto_detected=model.auto_detected,
            detection_confidence=model.detection_confidence,
            tags=model.tags,
            notes=model.notes,
        )

    async def get_pending(
        self,
        include_in_progress: bool = True,
        limit: int = 50,
    ) -> list[Delegation]:
        """Get pending delegations.

        Args:
            include_in_progress: Include in-progress items
            limit: Maximum results

        Returns:
            Pending delegations ordered by due date
        """
        statuses = [DelegationStatus.PENDING.value]
        if include_in_progress:
            statuses.append(DelegationStatus.IN_PROGRESS.value)

        stmt = (
            select(self._table_class)
            .where(self._col("status").in_(statuses))
            .order_by(
                self._col("due_date").asc().nullslast(),
                self._col("priority").desc(),
                self._col("delegated_at").asc(),
            )
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_by_assignee(
        self,
        email: str,
        include_completed: bool = False,
        limit: int = 50,
    ) -> list[Delegation]:
        """Get delegations to a specific person.

        Args:
            email: Assignee email
            include_completed: Include completed items
            limit: Maximum results

        Returns:
            Delegations to this person
        """
        stmt = select(self._table_class).where(
            self._col("assignee_email") == email.lower()
        )

        if not include_completed:
            stmt = stmt.where(
                self._col("status").in_([
                    DelegationStatus.PENDING.value,
                    DelegationStatus.IN_PROGRESS.value,
                ])
            )

        stmt = stmt.order_by(
            self._col("due_date").asc().nullslast(),
            self._col("delegated_at").asc(),
        ).limit(limit)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_overdue(self) -> list[Delegation]:
        """Get overdue delegations."""
        now = datetime.utcnow()
        stmt = (
            select(self._table_class)
            .where(
                self._col("status").in_([
                    DelegationStatus.PENDING.value,
                    DelegationStatus.IN_PROGRESS.value,
                ]),
                self._col("due_date") < now,
            )
            .order_by(self._col("due_date").asc())
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_waiting_summary(self) -> list[DelegationSummary]:
        """Get summary of what you're waiting on from each person.

        Returns:
            List of summaries grouped by assignee
        """
        stmt = (
            select(
                self._col("assignee_email"),
                self._col("assignee_name"),
                self._col("status"),
                func.count().label("count"),
                func.min(self._col("delegated_at")).label("oldest"),
            )
            .where(
                self._col("status").in_([
                    DelegationStatus.PENDING.value,
                    DelegationStatus.IN_PROGRESS.value,
                ])
            )
            .group_by(
                self._col("assignee_email"),
                self._col("assignee_name"),
                self._col("status"),
            )
        )

        result = await self._session.execute(stmt)
        rows = result.fetchall()

        summaries: dict[str, DelegationSummary] = {}
        now = datetime.utcnow()

        for row in rows:
            email = row[0]
            name = row[1]
            status = row[2]
            count = row[3]
            oldest = row[4]

            if email not in summaries:
                summaries[email] = DelegationSummary(
                    assignee_email=email,
                    assignee_name=name,
                )

            if status == DelegationStatus.PENDING.value:
                summaries[email] = DelegationSummary(
                    assignee_email=summaries[email].assignee_email,
                    assignee_name=summaries[email].assignee_name or name,
                    pending_count=count,
                    in_progress_count=summaries[email].in_progress_count,
                    overdue_count=summaries[email].overdue_count,
                    oldest_delegation_days=summaries[email].oldest_delegation_days,
                )
            elif status == DelegationStatus.IN_PROGRESS.value:
                summaries[email] = DelegationSummary(
                    assignee_email=summaries[email].assignee_email,
                    assignee_name=summaries[email].assignee_name or name,
                    pending_count=summaries[email].pending_count,
                    in_progress_count=count,
                    overdue_count=summaries[email].overdue_count,
                    oldest_delegation_days=summaries[email].oldest_delegation_days,
                )

            if oldest:
                oldest_naive = oldest.replace(tzinfo=None) if oldest.tzinfo else oldest
                days = (now - oldest_naive).days
                current = summaries[email].oldest_delegation_days
                if current is None or days > current:
                    summaries[email] = DelegationSummary(
                        assignee_email=summaries[email].assignee_email,
                        assignee_name=summaries[email].assignee_name,
                        pending_count=summaries[email].pending_count,
                        in_progress_count=summaries[email].in_progress_count,
                        overdue_count=summaries[email].overdue_count,
                        oldest_delegation_days=days,
                    )

        overdue = await self.get_overdue()
        for d in overdue:
            email = d.assignee_email
            if email in summaries:
                summaries[email] = DelegationSummary(
                    assignee_email=summaries[email].assignee_email,
                    assignee_name=summaries[email].assignee_name,
                    pending_count=summaries[email].pending_count,
                    in_progress_count=summaries[email].in_progress_count,
                    overdue_count=summaries[email].overdue_count + 1,
                    oldest_delegation_days=summaries[email].oldest_delegation_days,
                )

        return sorted(
            summaries.values(),
            key=lambda s: s.total_waiting,
            reverse=True,
        )

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[Delegation]:
        """Search delegations by title or description."""
        from sqlalchemy import or_

        pattern = f"%{query}%"
        stmt = (
            select(self._table_class)
            .where(
                or_(
                    self._col("title").ilike(pattern),
                    self._col("description").ilike(pattern),
                )
            )
            .order_by(self._col("delegated_at").desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def exists_similar(
        self,
        title: str,
        assignee_email: str,
        source_type: str | None = None,
    ) -> bool:
        """Check if a similar delegation already exists."""
        stmt = select(self._table_class).where(
            self._col("title") == title,
            self._col("assignee_email") == assignee_email.lower(),
            self._col("status").in_([
                DelegationStatus.PENDING.value,
                DelegationStatus.IN_PROGRESS.value,
            ]),
        )

        if source_type:
            stmt = stmt.where(self._col("source_type") == source_type)

        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
