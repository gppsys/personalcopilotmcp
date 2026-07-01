"""Email and Thread repository implementations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from executive_copilot.database.repositories.base import Repository
from executive_copilot.database.tables import EmailTable, ThreadTable
from executive_copilot.models import Email, EmailAddress, EmailPriority, Thread
from executive_copilot.models.email import EmailLabel


class EmailRepository(Repository[Email, EmailTable]):
    """Repository for Email entities."""

    @property
    def _table_class(self) -> type[EmailTable]:
        return EmailTable

    @property
    def _entity_name(self) -> str:
        return "Email"

    def _to_model(self, table: EmailTable) -> Email:
        """Convert table row to domain model."""
        from executive_copilot.models.base import ProviderReference

        return Email(
            id=table.id,
            created_at=table.created_at,
            updated_at=table.updated_at,
            thread_id=table.thread_id,
            in_reply_to=table.in_reply_to,
            references=table.references,
            sender=EmailAddress(**table.sender),
            to_recipients=[EmailAddress(**r) for r in table.to_recipients],
            cc_recipients=[EmailAddress(**r) for r in table.cc_recipients],
            bcc_recipients=[EmailAddress(**r) for r in table.bcc_recipients],
            reply_to=EmailAddress(**table.reply_to) if table.reply_to else None,
            subject=table.subject,
            body_text=table.body_text,
            body_html=table.body_html,
            snippet=table.snippet,
            attachment_ids=[UUID(aid) for aid in table.attachment_ids],
            has_attachments=table.has_attachments,
            received_at=table.received_at,
            sent_at=table.sent_at,
            priority=EmailPriority(table.priority),
            labels=[EmailLabel(**lbl) for lbl in table.labels],
            is_read=table.is_read,
            is_starred=table.is_starred,
            is_draft=table.is_draft,
            is_sent=table.is_sent,
            is_spam=table.is_spam,
            is_trash=table.is_trash,
            organization_id=table.organization_id,
            category=table.category,
            requires_response=table.requires_response,
            response_deadline=table.response_deadline,
            provider_ref=ProviderReference(**table.provider_ref) if table.provider_ref else None,
        )

    def _to_table(self, model: Email) -> EmailTable:
        """Convert domain model to table row."""
        return EmailTable(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            thread_id=model.thread_id,
            in_reply_to=model.in_reply_to,
            references=model.references,
            sender=model.sender.model_dump(),
            to_recipients=[r.model_dump() for r in model.to_recipients],
            cc_recipients=[r.model_dump() for r in model.cc_recipients],
            bcc_recipients=[r.model_dump() for r in model.bcc_recipients],
            reply_to=model.reply_to.model_dump() if model.reply_to else None,
            subject=model.subject,
            body_text=model.body_text,
            body_html=model.body_html,
            snippet=model.snippet,
            attachment_ids=[str(aid) for aid in model.attachment_ids],
            has_attachments=model.has_attachments,
            received_at=model.received_at,
            sent_at=model.sent_at,
            priority=model.priority.value,
            labels=[lbl.model_dump() for lbl in model.labels],
            is_read=model.is_read,
            is_starred=model.is_starred,
            is_draft=model.is_draft,
            is_sent=model.is_sent,
            is_spam=model.is_spam,
            is_trash=model.is_trash,
            organization_id=model.organization_id,
            category=model.category,
            requires_response=model.requires_response,
            response_deadline=model.response_deadline,
            provider_ref=model.provider_ref.model_dump() if model.provider_ref else None,
        )

    async def get_inbox(
        self,
        limit: int = 50,
        unread_only: bool = False,
    ) -> list[Email]:
        """Get inbox emails.

        Args:
            limit: Maximum emails to return
            unread_only: Only return unread emails

        Returns:
            List of emails sorted by received date
        """
        stmt = (
            select(self._table_class)
            .where(
                self._col("is_trash") == False,  # noqa: E712
                self._col("is_spam") == False,  # noqa: E712
                self._col("is_draft") == False,  # noqa: E712
            )
            .order_by(self._col("received_at").desc())
            .limit(limit)
        )
        if unread_only:
            stmt = stmt.where(self._col("is_read") == False)  # noqa: E712

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_by_thread(self, thread_id: UUID) -> list[Email]:
        """Get all emails in a thread.

        Args:
            thread_id: Thread ID

        Returns:
            Emails in thread ordered by date
        """
        stmt = (
            select(self._table_class)
            .where(self._col("thread_id") == thread_id)
            .order_by(self._col("received_at").asc())
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_unread_count(self) -> int:
        """Get count of unread emails."""
        from sqlalchemy import func

        stmt = (
            select(func.count())
            .select_from(self._table_class)
            .where(
                self._col("is_read") == False,  # noqa: E712
                self._col("is_trash") == False,  # noqa: E712
                self._col("is_spam") == False,  # noqa: E712
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get_by_organization(
        self,
        organization_id: UUID,
        limit: int = 50,
    ) -> list[Email]:
        """Get emails from an organization.

        Args:
            organization_id: Organization ID
            limit: Maximum results

        Returns:
            Emails from the organization
        """
        stmt = (
            select(self._table_class)
            .where(self._col("organization_id") == organization_id)
            .order_by(self._col("received_at").desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_requiring_response(self) -> list[Email]:
        """Get emails flagged as requiring response."""
        stmt = (
            select(self._table_class)
            .where(
                self._col("requires_response") == True,  # noqa: E712
                self._col("is_trash") == False,  # noqa: E712
            )
            .order_by(self._col("response_deadline").asc().nullslast())
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_received_between(
        self,
        start: datetime,
        end: datetime,
        limit: int = 500,
    ) -> list[Email]:
        """Get emails received in a date range.

        Args:
            start: Start of range
            end: End of range
            limit: Maximum results

        Returns:
            Emails in the range
        """
        stmt = (
            select(self._table_class)
            .where(
                self._col("received_at") >= start,
                self._col("received_at") <= end,
            )
            .order_by(self._col("received_at").desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]


class ThreadRepository(Repository[Thread, ThreadTable]):
    """Repository for Thread entities."""

    @property
    def _table_class(self) -> type[ThreadTable]:
        return ThreadTable

    @property
    def _entity_name(self) -> str:
        return "Thread"

    def _to_model(self, table: ThreadTable) -> Thread:
        """Convert table row to domain model."""
        from executive_copilot.models.base import ProviderReference

        return Thread(
            id=table.id,
            created_at=table.created_at,
            updated_at=table.updated_at,
            subject=table.subject,
            snippet=table.snippet,
            participants=[EmailAddress(**p) for p in table.participants],
            email_ids=[UUID(eid) for eid in table.email_ids],
            email_count=table.email_count,
            started_at=table.started_at,
            last_message_at=table.last_message_at,
            is_read=table.is_read,
            is_starred=table.is_starred,
            has_attachments=table.has_attachments,
            organization_id=table.organization_id,
            category=table.category,
            labels=[EmailLabel(**lbl) for lbl in table.labels],
            summary=table.summary,
            action_items=table.action_items,
            requires_response=table.requires_response,
            provider_ref=ProviderReference(**table.provider_ref) if table.provider_ref else None,
        )

    def _to_table(self, model: Thread) -> ThreadTable:
        """Convert domain model to table row."""
        return ThreadTable(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            subject=model.subject,
            snippet=model.snippet,
            participants=[p.model_dump() for p in model.participants],
            email_ids=[str(eid) for eid in model.email_ids],
            email_count=model.email_count,
            started_at=model.started_at,
            last_message_at=model.last_message_at,
            is_read=model.is_read,
            is_starred=model.is_starred,
            has_attachments=model.has_attachments,
            organization_id=model.organization_id,
            category=model.category,
            labels=[lbl.model_dump() for lbl in model.labels],
            summary=model.summary,
            action_items=model.action_items,
            requires_response=model.requires_response,
            provider_ref=model.provider_ref.model_dump() if model.provider_ref else None,
        )

    async def get_recent_threads(
        self,
        limit: int = 50,
        unread_only: bool = False,
    ) -> list[Thread]:
        """Get recent threads.

        Args:
            limit: Maximum threads to return
            unread_only: Only return unread threads

        Returns:
            Threads sorted by last message date
        """
        stmt = (
            select(self._table_class)
            .order_by(self._col("last_message_at").desc())
            .limit(limit)
        )
        if unread_only:
            stmt = stmt.where(self._col("is_read") == False)  # noqa: E712

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_by_organization(
        self,
        organization_id: UUID,
        limit: int = 50,
    ) -> list[Thread]:
        """Get threads from an organization.

        Args:
            organization_id: Organization ID
            limit: Maximum results

        Returns:
            Threads from the organization
        """
        stmt = (
            select(self._table_class)
            .where(self._col("organization_id") == organization_id)
            .order_by(self._col("last_message_at").desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]
