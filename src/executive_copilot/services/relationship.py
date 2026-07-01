"""Service for relationship intelligence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.config import AppConfig
from executive_copilot.database.connection import DatabaseManager
from executive_copilot.database.repositories import (
    CommitmentRepository,
    FollowUpRepository,
    InteractionRepository,
    PersonRepository,
)
from executive_copilot.logging import get_logger
from executive_copilot.models.communication import CommitmentStatus, FollowUpStatus
from executive_copilot.models.email import Email
from executive_copilot.models.relationship import (
    Interaction,
    InteractionDirection,
    InteractionType,
    RelationshipSummary,
)
from executive_copilot.providers.email.gmail import GmailProvider
from executive_copilot.providers.email.outlook import OutlookProvider

logger = get_logger(__name__)


class RelationshipService:
    """Provides relationship intelligence and tracking."""

    def __init__(
        self,
        config: AppConfig,
        credential_manager: CredentialManager,
        db_manager: DatabaseManager,
    ) -> None:
        self.config = config
        self.credential_manager = credential_manager
        self.db_manager = db_manager
        self._user_email = config.user_email

    def _get_email_accounts(self) -> list[dict[str, str]]:
        """Get all configured email accounts."""
        accounts = []
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                if pa.provider_type == "email":
                    accounts.append({
                        "account_id": pa.account_id,
                        "provider": pa.provider_name,
                        "organization": org.name,
                    })
        return accounts

    def _is_authorized(self, provider_name: str, account_id: str) -> bool:
        """Check if account has valid credentials."""
        cred = self.credential_manager.get_credential("email", provider_name, account_id)
        return cred is not None and cred.get("access_token") is not None

    def _get_provider(self, provider_name: str, account_id: str) -> GmailProvider | OutlookProvider:
        """Get email provider instance."""
        if provider_name == "gmail":
            return GmailProvider(self.credential_manager, account_id)
        elif provider_name == "outlook":
            return OutlookProvider(self.credential_manager, account_id)
        raise ValueError(f"Unknown email provider: {provider_name}")

    async def get_relationship_summary(
        self,
        email: str,
        stale_threshold_days: int = 30,
    ) -> RelationshipSummary:
        """Get comprehensive relationship summary for a person.

        Args:
            email: Person's email address
            stale_threshold_days: Days without contact to be considered stale

        Returns:
            Relationship summary with contact history and open items
        """
        email_lower = email.lower()

        async with self.db_manager.session() as session:
            person_repo = PersonRepository(session)
            interaction_repo = InteractionRepository(session)
            commitment_repo = CommitmentRepository(session)
            follow_up_repo = FollowUpRepository(session)

            person = await person_repo.find_by_email(email_lower)

            last_interaction = await interaction_repo.get_last_interaction(email_lower)
            total = await interaction_repo.count_interactions(email_lower)
            inbound = await interaction_repo.count_interactions(
                email_lower, InteractionDirection.INBOUND
            )
            outbound = await interaction_repo.count_interactions(
                email_lower, InteractionDirection.OUTBOUND
            )

            commitments = await commitment_repo.get_by_person(email_lower)
            open_commitments = len([
                c for c in commitments
                if c.status in (CommitmentStatus.PENDING, CommitmentStatus.IN_PROGRESS)
            ])

            follow_ups = await follow_up_repo.get_by_person(email_lower)
            open_follow_ups = len([
                f for f in follow_ups
                if f.status in (FollowUpStatus.PENDING, FollowUpStatus.IN_PROGRESS)
            ])

        days_since = None
        is_stale = True
        if last_interaction:
            delta = datetime.now(UTC) - last_interaction.occurred_at.replace(tzinfo=UTC)
            days_since = delta.days
            is_stale = days_since >= stale_threshold_days

        return RelationshipSummary(
            person_email=email_lower,
            person_name=person.preferred_name if person else None,
            importance=person.importance if person else 5,
            last_contact_at=last_interaction.occurred_at if last_interaction else None,
            last_contact_type=last_interaction.interaction_type if last_interaction else None,
            last_contact_subject=last_interaction.subject if last_interaction else None,
            days_since_contact=days_since,
            total_interactions=total,
            inbound_count=inbound,
            outbound_count=outbound,
            open_commitments=open_commitments,
            open_follow_ups=open_follow_ups,
            is_stale=is_stale,
            stale_threshold_days=stale_threshold_days,
        )

    async def get_person_history(
        self,
        email: str,
        limit: int = 5,
    ) -> list[Interaction]:
        """Get recent interaction history with a person.

        Args:
            email: Person's email address
            limit: Maximum interactions to return

        Returns:
            Recent interactions, newest first
        """
        async with self.db_manager.session() as session:
            repo = InteractionRepository(session)
            return await repo.get_person_history(email.lower(), limit=limit)

    async def get_stale_relationships(
        self,
        days_threshold: int = 30,
        min_importance: int = 5,
        limit: int = 20,
    ) -> list[RelationshipSummary]:
        """Get important people we haven't contacted recently.

        Args:
            days_threshold: Days without contact to be considered stale
            min_importance: Minimum importance level
            limit: Maximum results

        Returns:
            Stale relationships needing attention
        """
        async with self.db_manager.session() as session:
            person_repo = PersonRepository(session)
            stale_people = await person_repo.get_stale_relationships(
                days_threshold=days_threshold,
                min_importance=min_importance,
                limit=limit,
            )

        results = []
        for person in stale_people:
            summary = await self.get_relationship_summary(
                person.primary_email or person.contact.email or "",
                stale_threshold_days=days_threshold,
            )
            summary = RelationshipSummary(
                person_email=summary.person_email or person.primary_email or "",
                person_name=person.preferred_name,
                importance=person.importance,
                last_contact_at=person.last_contact_at,
                last_contact_type=(
                    InteractionType(person.last_contact_type)
                    if person.last_contact_type else None
                ),
                days_since_contact=person.days_since_contact,
                total_interactions=person.interaction_count,
                is_stale=True,
                stale_threshold_days=days_threshold,
            )
            results.append(summary)

        return results

    async def record_email_interaction(
        self,
        email: Email,
        account_id: str,
        is_sent: bool,
    ) -> Interaction | None:
        """Record an email as an interaction.

        Args:
            email: Email to record
            account_id: Account that sent/received the email
            is_sent: True if we sent the email, False if we received it

        Returns:
            Created interaction or None if already exists
        """
        if is_sent:
            for recipient in email.to_recipients:
                if recipient.address.lower() != account_id.lower():
                    await self._record_single_interaction(
                        email=email,
                        person_email=recipient.address,
                        person_name=recipient.display_name,
                        direction=InteractionDirection.OUTBOUND,
                        account_id=account_id,
                    )
        elif email.sender.address.lower() != account_id.lower():
            await self._record_single_interaction(
                email=email,
                person_email=email.sender.address,
                person_name=email.sender.display_name,
                direction=InteractionDirection.INBOUND,
                account_id=account_id,
            )

        return None

    async def _record_single_interaction(
        self,
        email: Email,
        person_email: str,
        person_name: str | None,
        direction: InteractionDirection,
        account_id: str,
    ) -> Interaction | None:
        """Record a single interaction from an email."""
        async with self.db_manager.session() as session:
            interaction_repo = InteractionRepository(session)
            person_repo = PersonRepository(session)

            exists = await interaction_repo.exists_for_source(
                source_type="email",
                source_id=email.id,
                person_email=person_email,
            )
            if exists:
                return None

            interaction_type = (
                InteractionType.EMAIL_SENT
                if direction == InteractionDirection.OUTBOUND
                else InteractionType.EMAIL_RECEIVED
            )

            interaction = Interaction(
                id=uuid4(),
                created_at=datetime.now(UTC),
                updated_at=None,
                person_email=person_email.lower(),
                person_name=person_name,
                interaction_type=interaction_type,
                direction=direction,
                occurred_at=email.received_at,
                subject=email.subject,
                snippet=email.snippet,
                source_type="email",
                source_id=email.id,
                account_id=account_id,
            )

            await interaction_repo.create(interaction)

            await person_repo.update_last_contact(
                email=person_email,
                contact_at=email.received_at,
                contact_type=interaction_type.value,
            )

            await session.commit()
            return interaction

    async def scan_emails_for_interactions(
        self,
        days_back: int = 7,
    ) -> dict[str, int]:
        """Scan recent emails and record interactions.

        Args:
            days_back: How many days of emails to scan

        Returns:
            Stats about interactions recorded
        """
        accounts = self._get_email_accounts()
        stats = {
            "emails_scanned": 0,
            "interactions_created": 0,
            "skipped": 0,
            "errors": 0,
        }

        for acc in accounts:
            if not self._is_authorized(acc["provider"], acc["account_id"]):
                logger.info("Skipping unauthorized account", account=acc["account_id"])
                continue

            try:
                account_stats = await self._scan_account(acc, days_back=days_back)
                for key in stats:
                    stats[key] += account_stats.get(key, 0)
            except Exception as e:
                logger.error("Failed to scan account", account=acc["account_id"], error=str(e))
                stats["errors"] += 1

        return stats

    async def _scan_account(
        self,
        account: dict[str, str],
        days_back: int,
    ) -> dict[str, int]:
        """Scan a single email account for interactions."""
        stats = {
            "emails_scanned": 0,
            "interactions_created": 0,
            "skipped": 0,
        }

        provider = self._get_provider(account["provider"], account["account_id"])
        account_id = account["account_id"]

        cutoff_date = datetime.now(UTC) - timedelta(days=days_back)

        logger.info(
            "Scanning emails for interactions",
            account=account_id,
            days_back=days_back,
        )

        if account["provider"] == "gmail":
            emails = await provider.get_emails(
                max_results=200,
                query=f"after:{cutoff_date.strftime('%Y/%m/%d')}",
            )
        else:
            emails = await provider.get_emails(max_results=200)

        for email in emails:
            stats["emails_scanned"] += 1

            is_sent = email.sender.address.lower() == account_id.lower()

            async with self.db_manager.session() as session:
                interaction_repo = InteractionRepository(session)

                already_recorded = await interaction_repo.exists_for_source(
                    source_type="email",
                    source_id=email.id,
                    person_email=(
                        email.to_recipients[0].address if is_sent and email.to_recipients
                        else email.sender.address
                    ),
                )

                if already_recorded:
                    stats["skipped"] += 1
                    continue

            await self.record_email_interaction(email, account_id, is_sent)
            stats["interactions_created"] += 1

        return stats
