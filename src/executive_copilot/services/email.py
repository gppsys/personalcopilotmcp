"""Email service for orchestrating email operations."""

from datetime import datetime
from uuid import UUID

from executive_copilot.database.unit_of_work import unit_of_work
from executive_copilot.models import Email, Thread
from executive_copilot.providers.email import EmailProvider, EmailQuery
from executive_copilot.services.base import Service


class EmailService(Service):
    """Service for email operations.

    Orchestrates email providers and handles email-related business logic
    including organization matching and classification.
    """

    async def get_inbox(
        self,
        limit: int = 50,
        unread_only: bool = False,
        provider_name: str | None = None,
    ) -> list[Email]:
        """Get inbox emails from all or specific provider.

        Args:
            limit: Maximum emails to return
            unread_only: Only return unread emails
            provider_name: Specific provider to query

        Returns:
            List of emails from inbox
        """
        emails: list[Email] = []

        # Get from database first for cached emails
        async with unit_of_work(self.container.db_manager) as uow:
            emails = await uow.emails.get_inbox(limit=limit, unread_only=unread_only)

        # If we have enough cached emails, return them
        if len(emails) >= limit:
            return emails[:limit]

        # Otherwise, fetch from providers and classify
        providers = self._get_email_providers(provider_name)
        for provider in providers:
            try:
                provider_emails = await provider.get_inbox(
                    limit=limit - len(emails),
                    unread_only=unread_only,
                )
                classified = await self._classify_emails(provider_emails)
                emails.extend(classified)
            except Exception as e:
                self._logger.error(
                    "Failed to fetch from provider",
                    provider=provider.name,
                    error=str(e),
                )

        return emails[:limit]

    async def get_email(
        self,
        email_id: UUID,
        *,
        provider_name: str | None = None,
        external_id: str | None = None,
    ) -> Email | None:
        """Get a single email by ID.

        Args:
            email_id: Domain email ID
            provider_name: Provider to fetch from if not cached
            external_id: External ID for provider lookup

        Returns:
            Email if found
        """
        # Check database first
        async with unit_of_work(self.container.db_manager) as uow:
            email = await uow.emails.get_by_id(email_id)
            if email:
                return email

        # Fetch from provider if we have the info
        if provider_name and external_id:
            providers = self._get_email_providers(provider_name)
            if providers:
                email = await providers[0].get_email(external_id)
                if email:
                    return (await self._classify_emails([email]))[0]

        return None

    async def search_emails(
        self,
        query: str,
        *,
        from_address: str | None = None,
        to_address: str | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        limit: int = 50,
        provider_name: str | None = None,
    ) -> list[Email]:
        """Search emails across providers.

        Args:
            query: Search query text
            from_address: Filter by sender
            to_address: Filter by recipient
            after: Emails after this date
            before: Emails before this date
            limit: Maximum results
            provider_name: Specific provider to search

        Returns:
            Matching emails
        """
        email_query = EmailQuery(
            query=query,
            from_address=from_address,
            to_address=to_address,
            after=after,
            before=before,
            limit=limit,
        )

        all_emails: list[Email] = []
        providers = self._get_email_providers(provider_name)

        for provider in providers:
            try:
                result = await provider.search_emails(email_query)
                classified = await self._classify_emails(result.emails)
                all_emails.extend(classified)
            except Exception as e:
                self._logger.error(
                    "Search failed for provider",
                    provider=provider.name,
                    error=str(e),
                )

        # Sort by received date and limit
        all_emails.sort(key=lambda e: e.received_at, reverse=True)
        return all_emails[:limit]

    async def get_thread(self, thread_id: UUID) -> Thread | None:
        """Get a thread by ID.

        Args:
            thread_id: Thread ID

        Returns:
            Thread if found
        """
        async with unit_of_work(self.container.db_manager) as uow:
            return await uow.threads.get_by_id(thread_id)

    async def get_thread_emails(self, thread_id: UUID) -> list[Email]:
        """Get all emails in a thread.

        Args:
            thread_id: Thread ID

        Returns:
            Emails in thread ordered by date
        """
        async with unit_of_work(self.container.db_manager) as uow:
            return await uow.emails.get_by_thread(thread_id)

    async def get_unread_count(self) -> int:
        """Get count of unread emails."""
        async with unit_of_work(self.container.db_manager) as uow:
            return await uow.emails.get_unread_count()

    async def mark_read(
        self,
        email_id: UUID,
        read: bool = True,
        provider_name: str | None = None,
        external_id: str | None = None,
    ) -> None:
        """Mark email as read/unread.

        Args:
            email_id: Email to mark
            read: True for read, False for unread
            provider_name: Provider containing email
            external_id: External ID for provider
        """
        # Update in database
        async with unit_of_work(self.container.db_manager) as uow:
            await uow.emails.update_fields(email_id, is_read=read)
            await uow.commit()

        # Update in provider
        if provider_name and external_id:
            providers = self._get_email_providers(provider_name)
            if providers:
                await providers[0].mark_read(external_id, read)

    async def get_emails_for_organization(
        self,
        organization_id: UUID,
        limit: int = 50,
    ) -> list[Email]:
        """Get emails associated with an organization.

        Args:
            organization_id: Organization ID
            limit: Maximum results

        Returns:
            Emails from/to the organization
        """
        async with unit_of_work(self.container.db_manager) as uow:
            return await uow.emails.get_by_organization(organization_id, limit)

    async def get_requiring_response(self) -> list[Email]:
        """Get emails flagged as requiring response.

        Returns:
            Emails needing response, ordered by deadline
        """
        async with unit_of_work(self.container.db_manager) as uow:
            return await uow.emails.get_requiring_response()

    async def _classify_emails(self, emails: list[Email]) -> list[Email]:
        """Classify emails with organization and category info.

        Args:
            emails: Emails to classify

        Returns:
            Classified emails
        """
        classified: list[Email] = []
        async with unit_of_work(self.container.db_manager) as uow:
            for email in emails:
                # Try to match organization by sender domain
                org = await uow.organizations.find_by_domain(email.sender_domain)
                if org:
                    email = email.with_update(organization_id=org.id)
                classified.append(email)
        return classified

    def _get_email_providers(self, provider_name: str | None = None) -> list[EmailProvider]:
        """Get email providers to use.

        Args:
            provider_name: Specific provider name, or None for all

        Returns:
            List of email providers
        """
        providers: list[EmailProvider] = []
        configs = self.container.config.get_enabled_providers("email")

        for config in configs:
            if provider_name and config.provider_name != provider_name:
                continue
            try:
                provider = self.container.provider_registry.get_or_create(config)
                if isinstance(provider, EmailProvider):
                    providers.append(provider)
            except Exception as e:
                self._logger.error(
                    "Failed to get provider",
                    provider=config.provider_name,
                    error=str(e),
                )

        return providers
