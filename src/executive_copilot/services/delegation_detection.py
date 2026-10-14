"""Service for detecting delegations from emails."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from executive_copilot.config import AppConfig
from executive_copilot.credentials import CredentialManager
from executive_copilot.database.connection import DatabaseManager
from executive_copilot.database.repositories import DelegationRepository, ProcessedItemRepository
from executive_copilot.logging import get_logger
from executive_copilot.models.delegation import Delegation, DelegationPriority, DelegationStatus
from executive_copilot.models.email import Email
from executive_copilot.providers.email.gmail import GmailProvider
from executive_copilot.providers.email.outlook import OutlookProvider

logger = get_logger(__name__)

DELEGATION_PROMPT = """Analyze this email and determine if the sender is delegating tasks to others.

A delegation is when someone ASKS or REQUESTS another person to do something.
Look for phrases like:
- "Can you handle..."
- "Please take care of..."
- "Could you..."
- "I need you to..."
- "Would you mind..."
- "Please [action]..."
- "I'd like you to..."

For each delegation found, extract:
- title: Brief task description (max 100 chars)
- description: More detail if available
- assignee_name: Who is being asked (if identifiable from context)
- due_date: When it's due (YYYY-MM-DD format, if mentioned)
- original_text: The exact quote requesting the action
- priority: low, medium, high, or urgent (based on language urgency)

IMPORTANT rules:
- IGNORE quoted reply text (lines starting with ">") and forwarded content:
  only analyze the newly written message.
- IGNORE boilerplate, signatures, and security disclaimers (e.g. "If you
  receive an email that seems fraudulent, please check with...") — generic
  advice is not a delegation.
- IGNORE conditional or hypothetical phrasing.
- Only extract requests that are concrete, actionable, and addressed to a
  specific person or team.

Return a JSON array. If no delegations found, return [].

Only include OUTGOING requests (sender asking others), not incoming requests TO the sender.

Email from: {sender}
To: {recipients}
Subject: {subject}

Body:
{body}
"""


class DelegationDetectionService:
    """Detects and tracks delegations from sent emails."""

    def __init__(
        self,
        config: AppConfig,
        credential_manager: CredentialManager,
        db_manager: DatabaseManager,
        ai_service: Any = None,
    ) -> None:
        self.config = config
        self.credential_manager = credential_manager
        self.db_manager = db_manager
        self._ai_service = ai_service

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

    async def scan_sent_emails(self, days_back: int = 7) -> dict[str, int]:
        """Scan sent emails for delegations.

        Args:
            days_back: How many days of sent emails to scan

        Returns:
            Summary stats
        """
        accounts = self._get_email_accounts()
        stats = {
            "emails_scanned": 0,
            "delegations_found": 0,
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
        """Scan a single email account for delegations."""
        stats = {
            "emails_scanned": 0,
            "delegations_found": 0,
            "skipped": 0,
        }

        provider = self._get_provider(account["provider"], account["account_id"])
        account_id = account["account_id"]

        cutoff_date = datetime.now(UTC) - timedelta(days=days_back)

        logger.info(
            "Scanning sent emails for delegations",
            account=account_id,
            days_back=days_back,
        )

        if account["provider"] == "gmail":
            emails = await provider.get_emails(
                max_results=100,
                query=f"in:sent after:{cutoff_date.strftime('%Y/%m/%d')}",
            )
        else:
            emails = await provider.get_emails(max_results=100, query="folder:sentitems")

        async with self.db_manager.session() as session:
            processed_repo = ProcessedItemRepository(session)
            delegation_repo = DelegationRepository(session)

            processed_ids = await processed_repo.get_processed_ids(
                "delegation_scan", account_id
            )
            logger.info("Already processed", count=len(processed_ids))

            for email in emails:
                stats["emails_scanned"] += 1

                external_id = email.provider_ref.external_id if email.provider_ref else None

                if external_id and external_id in processed_ids:
                    stats["skipped"] += 1
                    continue

                if email.sender.address.lower() != account_id.lower():
                    stats["skipped"] += 1
                    continue

                try:
                    created = await self._process_email(
                        email, delegation_repo, account
                    )
                    stats["delegations_found"] += created

                    if external_id:
                        await processed_repo.mark_processed(
                            item_type="delegation_scan",
                            external_id=external_id,
                            account_id=account_id,
                            job_name="delegation_detection",
                            result=f"found_{created}",
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to process email",
                        subject=email.subject[:50] if email.subject else "no subject",
                        error=str(e),
                    )

            await session.commit()

        return stats

    async def _process_email(
        self,
        email: Email,
        repo: DelegationRepository,
        account: dict[str, str],
    ) -> int:
        """Process an email and extract delegations."""
        text = email.body_text or email.snippet or ""
        if not text or len(text) < 20:
            return 0

        if not email.to_recipients:
            return 0

        delegations = await self._extract_delegations(email)
        created = 0

        for item in delegations:
            title = item.get("title", "")[:100]
            assignee_name = item.get("assignee_name")

            assignee_email = None
            if email.to_recipients:
                assignee_email = email.to_recipients[0].address
                if not assignee_name and email.to_recipients[0].display_name:
                    assignee_name = email.to_recipients[0].display_name

            if not assignee_email:
                continue

            exists = await repo.exists_similar(
                title=title,
                assignee_email=assignee_email,
                source_type="email",
            )
            if exists:
                continue

            due_date = None
            if item.get("due_date"):
                import contextlib
                with contextlib.suppress(ValueError):
                    due_date = datetime.strptime(item["due_date"], "%Y-%m-%d")

            priority_str = item.get("priority", "medium").lower()
            try:
                priority = DelegationPriority(priority_str)
            except ValueError:
                priority = DelegationPriority.MEDIUM

            delegation = Delegation(
                id=uuid4(),
                created_at=datetime.now(UTC),
                updated_at=None,
                title=title,
                description=item.get("description"),
                original_text=item.get("original_text", "")[:500],
                assignee_name=assignee_name,
                assignee_email=assignee_email.lower(),
                status=DelegationStatus.PENDING,
                priority=priority,
                delegated_at=email.received_at,
                due_date=due_date,
                source_type="email",
                source_id=email.id,
                account_id=account["account_id"],
                auto_detected=True,
                detection_confidence=0.7,
                tags=["from-email", account["organization"].lower().replace(" ", "-")],
            )

            await repo.create(delegation)
            created += 1

            logger.info(
                "Created delegation",
                title=title[:50],
                assignee=assignee_email,
            )

        return created

    async def _extract_delegations(self, email: Email) -> list[dict[str, Any]]:
        """Extract delegations from email using AI or keywords."""
        if self._ai_service:
            return await self._extract_with_ai(email)
        return self._extract_with_keywords(email)

    async def _extract_with_ai(self, email: Email) -> list[dict[str, Any]]:
        """Use AI to extract delegations."""
        from executive_copilot.services.ai import AITextRequest

        text = email.body_text or email.snippet or ""
        truncated = text[:8000]

        recipients = ", ".join([r.address for r in email.to_recipients[:5]])

        try:
            prompt = DELEGATION_PROMPT.format(
                sender=email.sender.address,
                recipients=recipients,
                subject=email.subject or "(no subject)",
                body=truncated,
            )
            result = await self._ai_service.generate_text(
                AITextRequest(input=prompt, max_output_tokens=1500)
            )

            response_text = result.text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            response_text = response_text.strip()

            items = json.loads(response_text)
            if isinstance(items, list):
                return items[:5]
            return []

        except Exception as e:
            logger.warning("AI extraction failed, falling back to keywords", error=str(e))
            return self._extract_with_keywords(email)

    def _extract_with_keywords(self, email: Email) -> list[dict[str, Any]]:
        """Fallback: extract delegations using keyword matching."""
        text = email.body_text or email.snippet or ""
        delegations = []
        lines = text.split("\n")

        delegation_patterns = [
            "can you",
            "could you",
            "would you",
            "please handle",
            "please take care",
            "i need you to",
            "i'd like you to",
            "i would like you to",
            "can you please",
            "please send",
            "please review",
            "please prepare",
            "please update",
            "please follow up",
            "please check",
            "please confirm",
            "please coordinate",
            "please schedule",
        ]

        for line in lines:
            stripped = line.strip()
            line_lower = stripped.lower()
            if not line_lower or len(line_lower) < 15:
                continue

            # Skip quoted reply text: those lines were written by someone
            # else (or by us in a past email) and must not be re-detected.
            if stripped.startswith(">") or stripped.startswith("|"):
                continue

            # Skip conditional phrasing and boilerplate warnings, e.g.
            # "If you receive an email that seems fraudulent, please check
            # with ..." — hypotheticals are not delegations.
            if line_lower.startswith(("if ", "in case ", "should you ", "en caso ", "si ")):
                continue

            for pattern in delegation_patterns:
                if pattern in line_lower:
                    delegations.append({
                        "title": line.strip()[:100],
                        "original_text": line.strip(),
                        "priority": "medium",
                    })
                    break

        return delegations[:5]
