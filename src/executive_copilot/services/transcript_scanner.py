"""Service for scanning meeting transcripts and extracting action items."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.config import AppConfig
from executive_copilot.database.connection import DatabaseManager
from executive_copilot.database.repositories import FollowUpRepository, ProcessedItemRepository
from executive_copilot.logging import get_logger
from executive_copilot.models.calendar import CalendarEvent
from executive_copilot.models.communication import FollowUp, FollowUpPriority, FollowUpStatus
from executive_copilot.providers.calendar.google import GoogleCalendarProvider
from executive_copilot.providers.calendar.interface import EventQuery

logger = get_logger(__name__)

ACTION_ITEM_PROMPT = """Analyze this meeting transcript and extract action items.

For each action item, identify:
- title: A brief summary (max 100 chars)
- description: More detail if available
- assignee: Who is responsible (if mentioned)
- due_date: When it's due (if mentioned, in YYYY-MM-DD format)
- source_text: The exact quote from the transcript

Return a JSON array of action items. If no action items found, return [].

Example output:
[
  {
    "title": "Send Q3 report to finance team",
    "description": "Include updated projections",
    "assignee": "John",
    "due_date": "2024-01-15",
    "source_text": "John said he'll send the Q3 report to finance by Monday"
  }
]

Transcript:
{transcript}
"""


class TranscriptScannerService:
    """Scans meeting transcripts and creates follow-ups for action items."""

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

    def _get_calendar_accounts(self) -> list[dict[str, str]]:
        """Get all configured Google calendar accounts."""
        accounts = []
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                if pa.provider_type == "calendar" and pa.provider_name == "google":
                    accounts.append({
                        "account_id": pa.account_id,
                        "provider": pa.provider_name,
                        "organization": org.name,
                    })
        return accounts

    def _is_authorized(self, account_id: str) -> bool:
        """Check if account has valid credentials."""
        cred = self.credential_manager.get_credential("calendar", "google", account_id)
        return cred is not None and cred.get("access_token") is not None

    async def scan(self, days_back: int = 7) -> dict[str, int]:
        """Scan recent meetings for transcripts and extract action items.

        Args:
            days_back: How many days of past meetings to scan

        Returns:
            Summary stats
        """
        accounts = self._get_calendar_accounts()
        stats = {
            "meetings_scanned": 0,
            "transcripts_found": 0,
            "transcripts_processed": 0,
            "follow_ups_created": 0,
            "errors": 0,
        }

        for acc in accounts:
            if not self._is_authorized(acc["account_id"]):
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

    async def _scan_account(self, account: dict[str, str], days_back: int) -> dict[str, int]:
        """Scan a single calendar account for transcripts."""
        stats = {
            "meetings_scanned": 0,
            "transcripts_found": 0,
            "transcripts_processed": 0,
            "transcripts_skipped": 0,
            "follow_ups_created": 0,
        }

        provider = GoogleCalendarProvider(self.credential_manager, account["account_id"])
        account_id = account["account_id"]

        start_date = datetime.now(UTC) - timedelta(days=days_back)
        end_date = datetime.now(UTC)

        logger.info(
            "Scanning calendar for transcripts",
            account=account_id,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
        )

        query = EventQuery(
            start_time=start_date,
            end_time=end_date,
            limit=100,
        )
        events, _ = await provider.get_events(query)

        async with self.db_manager.session() as session:
            processed_repo = ProcessedItemRepository(session)
            processed_ids = await processed_repo.get_processed_ids("transcript", account_id)
            logger.info("Already processed transcripts", count=len(processed_ids))

            for event in events:
                stats["meetings_scanned"] += 1

                if not event.attachments:
                    continue

                transcript_attachments = event.transcript_attachments
                if not transcript_attachments:
                    continue

                stats["transcripts_found"] += len(transcript_attachments)

                for attachment in transcript_attachments:
                    # Skip if already processed
                    if attachment.file_id in processed_ids:
                        stats["transcripts_skipped"] += 1
                        continue

                    try:
                        content = await provider.get_attachment_content(attachment.file_id)
                        if content:
                            stats["transcripts_processed"] += 1
                            created = await self._process_transcript(
                                event, content, account["organization"]
                            )
                            stats["follow_ups_created"] += created

                            # Mark as processed
                            await processed_repo.mark_processed(
                                item_type="transcript",
                                external_id=attachment.file_id,
                                account_id=account_id,
                                job_name="transcript_scan",
                                result=f"created_{created}",
                            )
                    except Exception as e:
                        logger.warning(
                            "Failed to process transcript",
                            event_title=event.title,
                            attachment_title=attachment.title,
                            error=str(e),
                        )

            await session.commit()

        return stats

    async def _process_transcript(
        self,
        event: CalendarEvent,
        transcript_content: str,
        organization: str,
    ) -> int:
        """Process transcript and create follow-ups for action items.

        Returns number of follow-ups created.
        """
        action_items = await self._extract_action_items(transcript_content)

        if not action_items:
            return 0

        async with self.db_manager.session() as session:
            repo = FollowUpRepository(session)
            created = 0

            for item in action_items:
                follow_up = FollowUp(
                    id=uuid4(),
                    created_at=datetime.now(UTC),
                    updated_at=None,
                    title=item["title"],
                    description=item.get("description"),
                    context=f"From meeting: {event.title} on {event.start_time.date()}",
                    status=FollowUpStatus.PENDING,
                    priority=FollowUpPriority.MEDIUM,
                    due_date=item.get("due_date"),
                    source_type="meeting_transcript",
                    source_id=event.id,
                    detected_from=item.get("source_text", "")[:200],
                    with_person_name=item.get("assignee"),
                    auto_detected=True,
                    detection_confidence=0.7,
                    tags=["from-transcript", organization.lower().replace(" ", "-")],
                )

                await repo.create(follow_up)
                created += 1

            await session.commit()

            logger.info(
                "Created follow-ups from transcript",
                event_title=event.title,
                count=created,
            )

        return created

    async def _extract_action_items(self, transcript: str) -> list[dict[str, Any]]:
        """Extract action items from transcript using AI."""
        if self._ai_service:
            return await self._extract_with_ai(transcript)
        return self._extract_with_keywords(transcript)

    async def _extract_with_ai(self, transcript: str) -> list[dict[str, Any]]:
        """Use AI to extract action items from transcript."""
        from executive_copilot.services.ai import AITextRequest

        truncated = transcript[:15000]

        try:
            prompt = ACTION_ITEM_PROMPT.format(transcript=truncated)
            result = await self._ai_service.generate_text(
                AITextRequest(input=prompt, max_output_tokens=2000)
            )

            text = result.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()

            items = json.loads(text)
            if isinstance(items, list):
                return items[:10]
            return []

        except Exception as e:
            logger.warning("AI extraction failed, falling back to keywords", error=str(e))
            return self._extract_with_keywords(transcript)

    def _extract_with_keywords(self, transcript: str) -> list[dict[str, Any]]:
        """Fallback: extract action items using keyword matching."""
        action_items = []
        lines = transcript.split("\n")

        action_keywords = [
            "action item",
            "todo",
            "to do",
            "will do",
            "i'll",
            "i will",
            "we'll",
            "we will",
            "need to",
            "should",
            "must",
            "follow up",
            "followup",
            "next step",
            "by friday",
            "by monday",
            "by end of",
            "deadline",
        ]

        for line in lines:
            line_lower = line.lower().strip()
            if not line_lower:
                continue

            for keyword in action_keywords:
                if keyword in line_lower:
                    action_items.append({
                        "title": line.strip()[:100],
                        "description": line.strip(),
                        "source_text": line.strip(),
                    })
                    break

        return action_items[:10]
