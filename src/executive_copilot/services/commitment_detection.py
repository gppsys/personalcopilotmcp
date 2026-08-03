"""Service for detecting commitments from emails and transcripts."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.config import AppConfig
from executive_copilot.database.connection import DatabaseManager
from executive_copilot.database.repositories import CommitmentRepository, ProcessedItemRepository
from executive_copilot.logging import get_logger
from executive_copilot.models.communication import Commitment, CommitmentDirection, CommitmentStatus
from executive_copilot.models.email import Email
from executive_copilot.providers.email.gmail import GmailProvider
from executive_copilot.providers.email.outlook import OutlookProvider

logger = get_logger(__name__)

COMMITMENT_PROMPT = """Analyze this text and extract any commitments or promises.

Look for TWO types of commitments:

1. "i_promised" - Commitments made BY the sender:
   - "I'll send you the report"
   - "I will review this by Friday"
   - "I can get that done"
   - "Let me take care of that"
   - "I'll follow up with..."

2. "they_promised" - Commitments made TO the sender (by the recipient/other party):
   - "He said he would send it Monday"
   - "She promised to review it"
   - "They will get back to us"
   - "John mentioned he'll handle this"

For each commitment, extract:
- direction: "i_promised" or "they_promised"
- title: Brief description (max 100 chars)
- description: More detail if available
- promiser_name: Who made the promise (if known)
- promisee_name: Who it was promised to (if known)
- due_date: When it's due (YYYY-MM-DD format, if mentioned)
- original_text: The exact quote

IMPORTANT rules:
- IGNORE quoted reply text (lines starting with ">") and forwarded content:
  only analyze the newly written message.
- IGNORE conditional or hypothetical statements ("only if X, I will...",
  "if that doesn't work, I'll call in") — they are not firm commitments.
- IGNORE boilerplate, signatures, and security disclaimers.
- Only extract commitments that are firm, actionable, and owned by a person.

Return a JSON array. If no commitments found, return [].

Example output:
[
  {{
    "direction": "i_promised",
    "title": "Send Q3 financial report to Sarah",
    "description": "Need to compile and send the Q3 report",
    "promiser_name": null,
    "promisee_name": "Sarah",
    "due_date": "2024-01-15",
    "original_text": "I'll send you the Q3 report by Monday"
  }},
  {{
    "direction": "they_promised",
    "title": "John to review proposal",
    "description": "John will review and provide feedback",
    "promiser_name": "John",
    "promisee_name": null,
    "due_date": "2024-01-12",
    "original_text": "John said he'll have the review done by Friday"
  }}
]

Text to analyze:
{text}
"""


class CommitmentDetectionService:
    """Detects and tracks commitments from emails and transcripts."""

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

    async def scan_emails(self, days_back: int = 7) -> dict[str, int]:
        """Scan recent emails for commitments.

        Args:
            days_back: How many days of emails to scan

        Returns:
            Summary stats
        """
        accounts = self._get_email_accounts()
        stats = {
            "emails_scanned": 0,
            "commitments_found": 0,
            "i_promised": 0,
            "they_promised": 0,
            "skipped": 0,
            "errors": 0,
        }

        for acc in accounts:
            if not self._is_authorized(acc["provider"], acc["account_id"]):
                logger.info("Skipping unauthorized account", account=acc["account_id"])
                continue

            try:
                account_stats = await self._scan_account_emails(acc, days_back=days_back)
                for key in stats:
                    stats[key] += account_stats.get(key, 0)
            except Exception as e:
                logger.error("Failed to scan account", account=acc["account_id"], error=str(e))
                stats["errors"] += 1

        return stats

    async def _scan_account_emails(
        self,
        account: dict[str, str],
        days_back: int,
    ) -> dict[str, int]:
        """Scan a single email account for commitments."""
        stats = {
            "emails_scanned": 0,
            "commitments_found": 0,
            "i_promised": 0,
            "they_promised": 0,
            "skipped": 0,
        }

        provider = self._get_provider(account["provider"], account["account_id"])
        account_id = account["account_id"]

        cutoff_date = datetime.now(UTC) - timedelta(days=days_back)

        logger.info(
            "Scanning emails for commitments",
            account=account_id,
            days_back=days_back,
        )

        emails = await self._fetch_emails(provider, account["provider"], cutoff_date)
        logger.info("Found emails to scan", count=len(emails), account=account_id)

        async with self.db_manager.session() as session:
            processed_repo = ProcessedItemRepository(session)
            commitment_repo = CommitmentRepository(session)

            processed_ids = await processed_repo.get_processed_ids(
                "commitment_scan", account_id
            )
            logger.info("Already processed", count=len(processed_ids))

            for email in emails:
                stats["emails_scanned"] += 1

                external_id = email.provider_ref.external_id if email.provider_ref else None

                if external_id and external_id in processed_ids:
                    stats["skipped"] += 1
                    continue

                try:
                    created = await self._process_email_for_commitments(
                        email, commitment_repo, account
                    )
                    stats["commitments_found"] += created["total"]
                    stats["i_promised"] += created["i_promised"]
                    stats["they_promised"] += created["they_promised"]

                    if external_id:
                        await processed_repo.mark_processed(
                            item_type="commitment_scan",
                            external_id=external_id,
                            account_id=account_id,
                            job_name="commitment_detection",
                            result=f"found_{created['total']}",
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to process email",
                        subject=email.subject[:50] if email.subject else "no subject",
                        error=str(e),
                    )

            await session.commit()

        return stats

    async def _fetch_emails(
        self,
        provider: GmailProvider | OutlookProvider,
        provider_name: str,
        cutoff_date: datetime,
    ) -> list[Email]:
        """Fetch emails from provider."""
        if provider_name == "gmail":
            return await provider.get_emails(
                max_results=100,
                query=f"after:{cutoff_date.strftime('%Y/%m/%d')}",
            )
        return await provider.get_emails(max_results=100)

    async def _process_email_for_commitments(
        self,
        email: Email,
        repo: CommitmentRepository,
        account: dict[str, str],
    ) -> dict[str, int]:
        """Process an email and extract commitments."""
        result = {"total": 0, "i_promised": 0, "they_promised": 0}

        text = email.body_text or email.snippet or ""
        if not text or len(text) < 20:
            return result

        is_sent_by_me = email.sender.address.lower() == account["account_id"].lower()

        commitments = await self._extract_commitments(text)

        for item in commitments:
            direction_str = item.get("direction", "i_promised")

            if is_sent_by_me and direction_str == "i_promised":
                direction = CommitmentDirection.I_PROMISED
            elif (not is_sent_by_me and direction_str == "they_promised") or (is_sent_by_me and direction_str == "they_promised") or (not is_sent_by_me and direction_str == "i_promised"):
                direction = CommitmentDirection.THEY_PROMISED
            else:
                direction = CommitmentDirection(direction_str)

            title = item.get("title", "")[:100]

            exists = await repo.exists_similar(
                title=title,
                source_type="email",
                direction=direction,
            )
            if exists:
                continue

            due_date = None
            if item.get("due_date"):
                try:
                    due_date = datetime.strptime(item["due_date"], "%Y-%m-%d")
                except ValueError:
                    pass

            if is_sent_by_me:
                promiser_name = None
                promiser_email = account["account_id"]
                promisee_name = item.get("promisee_name")
                promisee_email = (
                    email.to_recipients[0].address if email.to_recipients else None
                )
            else:
                promiser_name = item.get("promiser_name") or email.sender.display_name
                promiser_email = email.sender.address
                promisee_name = None
                promisee_email = account["account_id"]

            commitment = Commitment(
                id=uuid4(),
                created_at=datetime.now(UTC),
                updated_at=None,
                title=title,
                description=item.get("description"),
                original_text=item.get("original_text", "")[:500],
                direction=direction,
                promiser_name=promiser_name,
                promiser_email=promiser_email,
                promisee_name=promisee_name,
                promisee_email=promisee_email,
                status=CommitmentStatus.PENDING,
                due_date=due_date,
                source_type="email",
                source_id=email.id,
                source_date=email.received_at,
                auto_detected=True,
                detection_confidence=0.7,
                tags=["from-email", account["organization"].lower().replace(" ", "-")],
            )

            await repo.create(commitment)
            result["total"] += 1
            if direction == CommitmentDirection.I_PROMISED:
                result["i_promised"] += 1
            else:
                result["they_promised"] += 1

            logger.info(
                "Created commitment",
                title=title[:50],
                direction=direction.value,
                source="email",
            )

        return result

    async def _extract_commitments(self, text: str) -> list[dict[str, Any]]:
        """Extract commitments from text using AI."""
        if self._ai_service:
            return await self._extract_with_ai(text)
        return self._extract_with_keywords(text)

    async def _extract_with_ai(self, text: str) -> list[dict[str, Any]]:
        """Use AI to extract commitments."""
        from executive_copilot.services.ai import AITextRequest

        truncated = text[:10000]

        try:
            prompt = COMMITMENT_PROMPT.format(text=truncated)
            result = await self._ai_service.generate_text(
                AITextRequest(input=prompt, max_output_tokens=2000)
            )

            response_text = result.text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            response_text = response_text.strip()

            items = json.loads(response_text)
            if isinstance(items, list):
                return items[:10]
            return []

        except Exception as e:
            logger.warning("AI extraction failed, falling back to keywords", error=str(e))
            return self._extract_with_keywords(text)

    def _extract_with_keywords(self, text: str) -> list[dict[str, Any]]:
        """Fallback: extract commitments using keyword matching."""
        commitments = []
        lines = text.split("\n")

        i_promised_keywords = [
            "i'll",
            "i will",
            "i can do",
            "i can get",
            "i can send",
            "let me",
            "i'll take care",
            "i will send",
            "i will review",
            "i will follow up",
            "i'll get back",
            "i'll have it",
        ]

        they_promised_keywords = [
            "he will",
            "she will",
            "they will",
            "he'll",
            "she'll",
            "they'll",
            "promised to",
            "said he would",
            "said she would",
            "mentioned he'll",
            "mentioned she'll",
            "will get back to",
            "will send",
        ]

        for line in lines:
            stripped = line.strip()
            line_lower = stripped.lower()
            if not line_lower or len(line_lower) < 10:
                continue

            # Skip quoted reply text — those lines belong to earlier emails
            # (possibly written by someone else) and produce false positives
            # like detecting a quoted bullet as a fresh commitment.
            if stripped.startswith(">") or stripped.startswith("|"):
                continue

            # Skip conditional/hypothetical phrasing: "Only if the above are
            # not feasible - I will call in" is not a firm commitment.
            if line_lower.startswith(
                ("if ", "only if ", "in case ", "unless ", "should ", "si ", "solo si ")
            ):
                continue

            for keyword in i_promised_keywords:
                if keyword in line_lower:
                    commitments.append({
                        "direction": "i_promised",
                        "title": stripped[:100],
                        "original_text": stripped,
                    })
                    break

            for keyword in they_promised_keywords:
                if keyword in line_lower:
                    commitments.append({
                        "direction": "they_promised",
                        "title": line.strip()[:100],
                        "original_text": line.strip(),
                    })
                    break

        return commitments[:10]

    async def extract_from_transcript(
        self,
        transcript: str,
        event_title: str,
        event_id: str | None,
        organization: str,
    ) -> int:
        """Extract commitments from a meeting transcript.

        Returns number of commitments created.
        """
        commitments = await self._extract_commitments(transcript)

        if not commitments:
            return 0

        async with self.db_manager.session() as session:
            repo = CommitmentRepository(session)
            created = 0

            for item in commitments:
                direction = CommitmentDirection(item.get("direction", "i_promised"))
                title = item.get("title", "")[:100]

                exists = await repo.exists_similar(
                    title=title,
                    source_type="transcript",
                    direction=direction,
                )
                if exists:
                    continue

                due_date = None
                if item.get("due_date"):
                    try:
                        due_date = datetime.strptime(item["due_date"], "%Y-%m-%d")
                    except ValueError:
                        pass

                from uuid import NAMESPACE_URL, uuid5
                source_uuid = uuid5(NAMESPACE_URL, f"event:{event_id}") if event_id else None
                commitment = Commitment(
                    id=uuid4(),
                    created_at=datetime.now(UTC),
                    updated_at=None,
                    title=title,
                    description=f"From meeting: {event_title}",
                    original_text=item.get("original_text", "")[:500],
                    direction=direction,
                    promiser_name=item.get("promiser_name"),
                    promisee_name=item.get("promisee_name"),
                    status=CommitmentStatus.PENDING,
                    due_date=due_date,
                    source_type="transcript",
                    source_id=source_uuid,
                    source_date=datetime.now(UTC),
                    auto_detected=True,
                    detection_confidence=0.6,
                    tags=["from-transcript", organization.lower().replace(" ", "-")],
                )

                await repo.create(commitment)
                created += 1

                logger.info(
                    "Created commitment from transcript",
                    title=title[:50],
                    direction=direction.value,
                )

            await session.commit()

        return created
