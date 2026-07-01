"""Service for detecting sent emails awaiting replies."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.config import AppConfig
from executive_copilot.database.connection import DatabaseManager
from executive_copilot.database.repositories import FollowUpRepository, ProcessedItemRepository
from executive_copilot.logging import get_logger
from executive_copilot.models.communication import FollowUp, FollowUpPriority, FollowUpStatus
from executive_copilot.models.email import Email
from executive_copilot.providers.email.gmail import GmailProvider
from executive_copilot.providers.email.outlook import OutlookProvider

logger = get_logger(__name__)


class PendingRepliesService:
    """Scans sent emails and creates follow-ups for unanswered ones."""

    def __init__(
        self,
        config: AppConfig,
        credential_manager: CredentialManager,
        db_manager: DatabaseManager,
    ) -> None:
        self.config = config
        self.credential_manager = credential_manager
        self.db_manager = db_manager

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

    async def sync(
        self,
        days_back: int = 7,
        min_wait_days: int = 2,
    ) -> dict[str, int]:
        """Scan sent emails and create follow-ups for unanswered ones.

        Args:
            days_back: How many days of sent emails to scan
            min_wait_days: Minimum days without reply before flagging

        Returns:
            Summary with counts of processed, created, and updated follow-ups
        """
        accounts = self._get_email_accounts()
        stats = {"processed": 0, "created": 0, "updated": 0, "skipped": 0, "errors": 0}

        for acc in accounts:
            if not self._is_authorized(acc["provider"], acc["account_id"]):
                logger.info("Skipping unauthorized account", account=acc["account_id"])
                continue

            try:
                account_stats = await self._sync_account(
                    acc, days_back=days_back, min_wait_days=min_wait_days
                )
                for key in stats:
                    stats[key] += account_stats.get(key, 0)
            except Exception as e:
                logger.error("Failed to sync account", account=acc["account_id"], error=str(e))
                stats["errors"] += 1

        return stats

    async def _sync_account(
        self,
        account: dict[str, str],
        days_back: int,
        min_wait_days: int,
    ) -> dict[str, int]:
        """Sync a single email account."""
        stats = {"processed": 0, "created": 0, "updated": 0, "skipped": 0}
        provider = self._get_provider(account["provider"], account["account_id"])
        account_id = account["account_id"]

        logger.info("Scanning sent emails", account=account_id, days_back=days_back)

        cutoff_date = datetime.now(UTC) - timedelta(days=days_back)
        min_wait_threshold = datetime.now(UTC) - timedelta(days=min_wait_days)

        sent_emails = await self._fetch_sent_emails(provider, account["provider"], cutoff_date)
        logger.info("Found sent emails", count=len(sent_emails), account=account_id)

        async with self.db_manager.session() as session:
            repo = FollowUpRepository(session)
            processed_repo = ProcessedItemRepository(session)

            # Get already processed email IDs to skip
            processed_ids = await processed_repo.get_processed_ids("sent_email", account_id)
            logger.info("Already processed", count=len(processed_ids), account=account_id)

            for sent_email in sent_emails:
                stats["processed"] += 1

                # Skip if already processed
                external_id = sent_email.provider_ref.external_id if sent_email.provider_ref else None
                if external_id and external_id in processed_ids:
                    stats["skipped"] += 1
                    continue

                result = await self._process_email(
                    sent_email, provider, repo, account, account_id, min_wait_threshold
                )
                stats[result] += 1

                # Mark as processed
                if external_id:
                    await processed_repo.mark_processed(
                        item_type="sent_email",
                        external_id=external_id,
                        account_id=account_id,
                        job_name="follow_up_detection",
                        result=result,
                    )

            await session.commit()

        return stats

    async def _fetch_sent_emails(
        self,
        provider: GmailProvider | OutlookProvider,
        provider_name: str,
        cutoff_date: datetime,
    ) -> list[Email]:
        """Fetch sent emails from provider."""
        if provider_name == "gmail":
            return await provider.get_emails(
                max_results=100,
                query=f"in:sent after:{cutoff_date.strftime('%Y/%m/%d')}",
            )
        return await provider.get_emails(max_results=100, query="folder:sentitems")

    async def _process_email(
        self,
        sent_email: Email,
        provider: GmailProvider | OutlookProvider,
        repo: FollowUpRepository,
        account: dict[str, str],
        account_id: str,
        min_wait_threshold: datetime,
    ) -> str:
        """Process a single sent email. Returns the stats key to increment."""
        # Check if email should be skipped
        if not self._should_process_email(sent_email, min_wait_threshold):
            return "skipped"

        thread_id: str | None = None
        if sent_email.provider_ref:
            thread_id = sent_email.provider_ref.thread_id
            if not thread_id and sent_email.provider_ref.raw_data:
                thread_id = sent_email.provider_ref.raw_data.get("threadId")  # type: ignore[assignment]
        if not thread_id:
            return "skipped"

        # Check thread for replies
        thread_emails = await self._get_thread_safe(provider, thread_id)
        if thread_emails is None:
            return "skipped"

        has_reply = any(
            e.received_at > sent_email.received_at
            and e.sender.address.lower() != account_id.lower()
            for e in thread_emails
        )
        if has_reply:
            return "skipped"

        # Check for existing follow-up or create new one
        external_id = sent_email.provider_ref.external_id if sent_email.provider_ref else None
        existing = await repo.search(f"[{external_id}]") if external_id else []
        existing = [
            f for f in existing
            if f.source_type == "sent_email" and f.status != FollowUpStatus.COMPLETED
        ]

        if existing:
            return "updated"

        await self._create_follow_up(sent_email, repo, account, external_id)
        return "created"

    def _should_process_email(self, sent_email: Email, min_wait_threshold: datetime) -> bool:
        """Check if email should be processed for follow-up."""
        if sent_email.received_at > min_wait_threshold:
            return False
        return bool(sent_email.to_recipients)

    async def _get_thread_safe(self, provider: GmailProvider | OutlookProvider, thread_id: str) -> list[Email] | None:
        """Get thread emails, returning None on error."""
        try:
            return await provider.get_thread(thread_id)
        except Exception as e:
            logger.warning("Failed to get thread", thread_id=thread_id, error=str(e))
            return None

    async def _create_follow_up(
        self,
        sent_email: Email,
        repo: FollowUpRepository,
        account: dict[str, str],
        external_id: str | None,
    ) -> None:
        """Create a follow-up for an unanswered email."""
        recipient = sent_email.to_recipients[0]
        days_waiting = (datetime.now(UTC) - sent_email.received_at).days
        priority = FollowUpPriority.HIGH if days_waiting >= 5 else FollowUpPriority.MEDIUM

        follow_up = FollowUp(
            id=uuid4(),
            created_at=datetime.now(UTC),
            updated_at=None,
            title=f"Awaiting reply: {sent_email.subject}",
            description=f"Sent {days_waiting} days ago to {recipient.address}.",
            context=sent_email.snippet,
            status=FollowUpStatus.PENDING,
            priority=priority,
            due_date=datetime.now(UTC) + timedelta(days=2),
            source_type="sent_email",
            source_id=sent_email.id,
            detected_from=f"[{external_id}] {sent_email.subject[:50]}",
            with_person_email=recipient.address,
            with_person_name=recipient.display_name,
            auto_detected=True,
            detection_confidence=0.9,
            tags=["pending-reply", account["organization"].lower().replace(" ", "-")],
        )

        await repo.create(follow_up)

        logger.info(
            "Created follow-up",
            subject=sent_email.subject[:50],
            recipient=recipient.address,
            days_waiting=days_waiting,
        )
