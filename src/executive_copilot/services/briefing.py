"""Service for generating morning briefings."""

import asyncio
from datetime import UTC, datetime
from typing import Any

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.config import AppConfig
from executive_copilot.database.connection import DatabaseManager
from executive_copilot.database.repositories import (
    CommitmentRepository,
    DelegationRepository,
    FollowUpRepository,
)
from executive_copilot.logging import get_logger
from executive_copilot.models import Email
from executive_copilot.providers.calendar.google import GoogleCalendarProvider
from executive_copilot.providers.calendar.outlook import OutlookCalendarProvider
from executive_copilot.providers.email.gmail import GmailProvider
from executive_copilot.providers.email.outlook import OutlookProvider

logger = get_logger(__name__)


class BriefingService:
    """Service for generating daily briefings."""

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

    def _get_calendar_accounts(self) -> list[dict[str, str]]:
        """Get all configured calendar accounts."""
        accounts = []
        seen = set()
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                if pa.provider_type == "calendar":
                    key = (pa.account_id, pa.provider_name)
                    if key not in seen:
                        seen.add(key)
                        accounts.append({
                            "account_id": pa.account_id,
                            "provider": pa.provider_name,
                            "organization": org.name,
                        })
        return accounts

    def _is_authorized(
        self, provider_type: str, provider_name: str, account_id: str
    ) -> bool:
        """Check if account has valid credentials."""
        cred = self.credential_manager.get_credential(
            provider_type, provider_name, account_id
        )
        return cred is not None and cred.get("access_token") is not None

    def _get_email_provider(self, provider_name: str, account_id: str) -> GmailProvider | OutlookProvider:
        """Get email provider instance."""
        if provider_name == "gmail":
            return GmailProvider(self.credential_manager, account_id)
        elif provider_name == "outlook":
            return OutlookProvider(self.credential_manager, account_id)
        raise ValueError(f"Unknown email provider: {provider_name}")

    def _get_calendar_provider(self, provider_name: str, account_id: str) -> GoogleCalendarProvider | OutlookCalendarProvider:
        """Get calendar provider instance."""
        if provider_name == "google":
            return GoogleCalendarProvider(self.credential_manager, account_id)
        elif provider_name == "outlook":
            return OutlookCalendarProvider(self.credential_manager, account_id)
        raise ValueError(f"Unknown calendar provider: {provider_name}")

    async def generate_briefing(
        self,
        include_emails: bool = True,
        include_calendar: bool = True,
        include_tasks: bool = True,
        max_emails: int = 10,
        max_events: int = 15,
    ) -> dict[str, Any]:
        """Generate a comprehensive morning briefing.

        Args:
            include_emails: Include email summary
            include_calendar: Include today's events
            include_tasks: Include overdue items and pending work
            max_emails: Maximum urgent emails to include
            max_events: Maximum events to include

        Returns:
            Complete briefing data
        """
        now = datetime.now(UTC)
        briefing: dict[str, Any] = {
            "generated_at": now.isoformat(),
            "date": now.strftime("%A, %B %d, %Y"),
            "summary": {},
            "calendar": None,
            "emails": None,
            "overdue": None,
            "pending_replies": None,
            "delegations": None,
            "commitments": None,
            "errors": [],
        }

        tasks = []

        if include_calendar:
            tasks.append(self._get_calendar_briefing(max_events))

        if include_emails:
            tasks.append(self._get_email_briefing(max_emails))

        if include_tasks:
            tasks.append(self._get_overdue_items())
            tasks.append(self._get_pending_replies())
            tasks.append(self._get_delegation_summary())
            tasks.append(self._get_commitment_summary())

        results = await asyncio.gather(*tasks, return_exceptions=True)

        idx = 0
        if include_calendar:
            if isinstance(results[idx], Exception):
                briefing["errors"].append(f"Calendar: {results[idx]}")
            else:
                briefing["calendar"] = results[idx]
            idx += 1

        if include_emails:
            if isinstance(results[idx], Exception):
                briefing["errors"].append(f"Email: {results[idx]}")
            else:
                briefing["emails"] = results[idx]
            idx += 1

        if include_tasks:
            if isinstance(results[idx], Exception):
                briefing["errors"].append(f"Overdue: {results[idx]}")
            else:
                briefing["overdue"] = results[idx]
            idx += 1

            if isinstance(results[idx], Exception):
                briefing["errors"].append(f"Pending replies: {results[idx]}")
            else:
                briefing["pending_replies"] = results[idx]
            idx += 1

            if isinstance(results[idx], Exception):
                briefing["errors"].append(f"Delegations: {results[idx]}")
            else:
                briefing["delegations"] = results[idx]
            idx += 1

            if isinstance(results[idx], Exception):
                briefing["errors"].append(f"Commitments: {results[idx]}")
            else:
                briefing["commitments"] = results[idx]

        briefing["summary"] = self._build_summary(briefing)
        briefing["summary_text"] = self._build_summary_text(briefing["summary"])

        return briefing

    async def _get_calendar_briefing(self, max_events: int) -> dict[str, Any]:
        """Get today's calendar events."""
        accounts = self._get_calendar_accounts()
        events: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for acc in accounts:
            if not self._is_authorized("calendar", acc["provider"], acc["account_id"]):
                continue

            try:
                provider = self._get_calendar_provider(
                    acc["provider"], acc["account_id"]
                )
                today_events = await provider.get_today_events()

                for cal_event in today_events:
                    events.append({
                        "title": cal_event.title,
                        "start": cal_event.start_time.isoformat(),
                        "end": cal_event.end_time.isoformat(),
                        "start_time": cal_event.start_time.strftime("%I:%M %p"),
                        "duration_minutes": cal_event.duration_minutes,
                        "is_all_day": cal_event.is_all_day,
                        "location": cal_event.location.display_name if cal_event.location else None,
                        "conference_url": cal_event.conference_url,
                        "attendee_count": len(cal_event.attendees),
                        "account": acc["account_id"],
                        "organization": acc["organization"],
                    })
            except Exception as e:
                errors.append({"account": acc["account_id"], "error": str(e)})

        events.sort(key=lambda x: x["start"])
        events = events[:max_events]

        next_meeting: dict[str, Any] | None = None
        now = datetime.now(UTC)
        for event in events:
            if not event["is_all_day"]:
                event_start = datetime.fromisoformat(event["start"])
                if event_start > now:
                    next_meeting = event
                    break

        return {
            "total": len(events),
            "events": events,
            "next_meeting": next_meeting,
            "errors": errors,
        }

    async def _get_email_briefing(self, max_emails: int) -> dict[str, Any]:
        """Get email summary with urgent/unread items."""
        accounts = self._get_email_accounts()
        total_unread = 0
        urgent_emails: list[dict[str, Any]] = []
        by_organization: dict[str, int] = {}
        errors = []

        for acc in accounts:
            if not self._is_authorized("email", acc["provider"], acc["account_id"]):
                continue

            try:
                provider = self._get_email_provider(acc["provider"], acc["account_id"])
                unread = await provider.get_unread(max_results=50)

                org = acc["organization"]
                if org not in by_organization:
                    by_organization[org] = 0
                by_organization[org] += len(unread)
                total_unread += len(unread)

                for email in unread[:max_emails]:
                    is_urgent = self._is_urgent_email(email)
                    if is_urgent or len(urgent_emails) < max_emails:
                        urgent_emails.append({
                            "subject": email.subject,
                            "from": email.sender.address,
                            "from_name": email.sender.display_name,
                            "received_at": email.received_at.isoformat(),
                            "snippet": email.snippet,
                            "is_urgent": is_urgent,
                            "account": acc["account_id"],
                            "organization": org,
                        })

            except Exception as e:
                errors.append({"account": acc["account_id"], "error": str(e)})

        urgent_emails.sort(
            key=lambda x: (not x["is_urgent"], x["received_at"]),
            reverse=True,
        )
        urgent_emails = urgent_emails[:max_emails]

        return {
            "total_unread": total_unread,
            "by_organization": by_organization,
            "urgent_count": sum(1 for e in urgent_emails if e["is_urgent"]),
            "emails": urgent_emails,
            "errors": errors,
        }

    def _is_urgent_email(self, email: Email) -> bool:
        """Check if email appears urgent."""
        urgent_keywords = [
            "urgent", "asap", "immediately", "critical", "emergency",
            "time sensitive", "deadline", "eod", "end of day",
        ]
        subject = (email.subject or "").lower()
        snippet = (email.snippet or "").lower()

        return any(keyword in subject or keyword in snippet for keyword in urgent_keywords)

    async def _get_overdue_items(self) -> dict[str, Any]:
        """Get all overdue items."""
        overdue: dict[str, Any] = {
            "follow_ups": [],
            "commitments": [],
            "delegations": [],
            "total": 0,
        }

        async with self.db_manager.session() as session:
            follow_up_repo = FollowUpRepository(session)
            follow_ups = await follow_up_repo.get_overdue()
            for f in follow_ups[:10]:
                overdue["follow_ups"].append({
                    "id": str(f.id),
                    "title": f.title,
                    "due_date": f.due_date.isoformat() if f.due_date else None,
                    "with_person": f.with_person_email,
                    "priority": f.priority.value,
                })

            commitment_repo = CommitmentRepository(session)
            commitments = await commitment_repo.get_overdue()
            for c in commitments[:10]:
                overdue["commitments"].append({
                    "id": str(c.id),
                    "title": c.title,
                    "due_date": c.due_date.isoformat() if c.due_date else None,
                    "direction": c.direction.value,
                    "person": (
                        c.promisee_email
                        if c.direction.value == "i_promised"
                        else c.promiser_email
                    ),
                })

            delegation_repo = DelegationRepository(session)
            delegations = await delegation_repo.get_overdue()
            for d in delegations[:10]:
                overdue["delegations"].append({
                    "id": str(d.id),
                    "title": d.title,
                    "due_date": d.due_date.isoformat() if d.due_date else None,
                    "assignee": d.assignee_email,
                })

        overdue["total"] = (
            len(overdue["follow_ups"])
            + len(overdue["commitments"])
            + len(overdue["delegations"])
        )

        return overdue

    async def _get_pending_replies(self) -> dict[str, Any]:
        """Get pending replies (follow-ups awaiting response)."""
        pending: dict[str, Any] = {
            "items": [],
            "total": 0,
        }

        async with self.db_manager.session() as session:
            repo = FollowUpRepository(session)
            follow_ups = await repo.get_pending(limit=20, include_snoozed=False)

            for f in follow_ups:
                if f.source_type == "pending_reply":
                    pending["items"].append({
                        "id": str(f.id),
                        "title": f.title,
                        "with_person": f.with_person_email,
                        "created_at": f.created_at.isoformat(),
                        "priority": f.priority.value,
                    })

        pending["total"] = len(pending["items"])
        return pending

    async def _get_delegation_summary(self) -> dict[str, Any]:
        """Get delegation summary."""
        async with self.db_manager.session() as session:
            repo = DelegationRepository(session)
            summaries = await repo.get_waiting_summary()

            total_waiting = sum(s.total_waiting for s in summaries)
            total_overdue = sum(s.overdue_count for s in summaries)

            by_person = []
            for s in summaries[:5]:
                name = s.assignee_name or s.assignee_email.split("@")[0]
                by_person.append({
                    "name": name,
                    "email": s.assignee_email,
                    "pending": s.pending_count,
                    "in_progress": s.in_progress_count,
                    "overdue": s.overdue_count,
                    "total": s.total_waiting,
                })

            return {
                "total_waiting": total_waiting,
                "total_overdue": total_overdue,
                "by_person": by_person,
            }

    async def _get_commitment_summary(self) -> dict[str, Any]:
        """Get commitment summary."""
        async with self.db_manager.session() as session:
            repo = CommitmentRepository(session)

            my_commitments = await repo.get_my_commitments(
                include_completed=False, limit=20
            )
            others_commitments = await repo.get_others_commitments(
                include_completed=False, limit=20
            )

            my_overdue = sum(1 for c in my_commitments if c.is_overdue)
            others_overdue = sum(1 for c in others_commitments if c.is_overdue)

            return {
                "i_promised": {
                    "total": len(my_commitments),
                    "overdue": my_overdue,
                },
                "they_promised": {
                    "total": len(others_commitments),
                    "overdue": others_overdue,
                },
            }

    def _build_summary(self, briefing: dict[str, Any]) -> dict[str, int]:
        """Build summary counts."""
        summary = {
            "meetings": 0,
            "unread_emails": 0,
            "urgent_emails": 0,
            "overdue_items": 0,
            "pending_replies": 0,
            "waiting_on_others": 0,
            "my_commitments": 0,
        }

        if briefing.get("calendar"):
            summary["meetings"] = briefing["calendar"].get("total", 0)

        if briefing.get("emails"):
            summary["unread_emails"] = briefing["emails"].get("total_unread", 0)
            summary["urgent_emails"] = briefing["emails"].get("urgent_count", 0)

        if briefing.get("overdue"):
            summary["overdue_items"] = briefing["overdue"].get("total", 0)

        if briefing.get("pending_replies"):
            summary["pending_replies"] = briefing["pending_replies"].get("total", 0)

        if briefing.get("delegations"):
            summary["waiting_on_others"] = briefing["delegations"].get(
                "total_waiting", 0
            )

        if briefing.get("commitments"):
            summary["my_commitments"] = briefing["commitments"].get(
                "i_promised", {}
            ).get("total", 0)

        return summary

    def _build_summary_text(self, summary: dict[str, int]) -> str:
        """Build human-readable summary text."""
        parts = []

        def pluralize(count: int, singular: str, plural: str) -> str:
            return f"{count} {plural if count != 1 else singular}"

        if summary["meetings"] > 0:
            parts.append(pluralize(summary["meetings"], "meeting", "meetings"))

        if summary["pending_replies"] > 0:
            parts.append(pluralize(summary["pending_replies"], "pending reply", "pending replies"))

        if summary["overdue_items"] > 0:
            parts.append(pluralize(summary["overdue_items"], "overdue item", "overdue items"))

        if summary["urgent_emails"] > 0:
            parts.append(pluralize(summary["urgent_emails"], "urgent email", "urgent emails"))

        if summary["waiting_on_others"] > 0:
            n = summary["waiting_on_others"]
            parts.append(f"{n} {'item' if n == 1 else 'items'} waiting on others")

        if not parts:
            return "All clear! No urgent items today."

        return ", ".join(parts)

    async def send_briefing_email(
        self,
        briefing: dict[str, Any],
        to_email: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Send briefing as email digest.

        Args:
            briefing: Briefing data from generate_briefing()
            to_email: Recipient email (defaults to account email)
            account_id: Email account to send from

        Returns:
            Send result
        """
        accounts = self._get_email_accounts()
        if not accounts:
            return {"error": "No email accounts configured"}

        if account_id:
            acc = next((a for a in accounts if a["account_id"] == account_id), None)
        else:
            acc = next(
                (a for a in accounts
                 if self._is_authorized("email", a["provider"], a["account_id"])),
                None
            )

        if not acc:
            return {"error": "No authorized email account available"}

        if not to_email:
            to_email = acc["account_id"]

        subject = f"Morning Briefing - {briefing['date']}"
        body = self._format_briefing_email(briefing)

        try:
            provider = self._get_email_provider(acc["provider"], acc["account_id"])
            await provider.send_email(
                to=[to_email],
                subject=subject,
                body=body,
            )

            return {
                "success": True,
                "sent_to": to_email,
                "sent_from": acc["account_id"],
                "subject": subject,
            }

        except Exception as e:
            logger.error("Failed to send briefing email", error=str(e))
            return {"error": str(e)}

    def _format_briefing_email(self, briefing: dict[str, Any]) -> str:
        """Format briefing as email body."""
        lines = [
            f"Good morning! Here's your briefing for {briefing['date']}.",
            "",
            f"SUMMARY: {briefing.get('summary_text', 'No summary available')}",
            "",
        ]

        summary = briefing.get("summary", {})
        if summary.get("unread_emails", 0) > 0:
            lines.append(f"Unread emails: {summary['unread_emails']}")

        if briefing.get("calendar") and briefing["calendar"].get("events"):
            lines.extend(["", "TODAY'S MEETINGS", "-" * 30])
            for event in briefing["calendar"]["events"][:8]:
                time_str = event.get("start_time", "")
                if event.get("is_all_day"):
                    time_str = "All day"
                lines.append(f"  {time_str} - {event['title']}")
                if event.get("location"):
                    lines.append(f"    Location: {event['location']}")

        if briefing.get("overdue") and briefing["overdue"].get("total", 0) > 0:
            lines.extend(["", "OVERDUE ITEMS", "-" * 30])
            overdue = briefing["overdue"]

            for item in overdue.get("follow_ups", [])[:3]:
                lines.append(f"  Follow-up: {item['title']}")

            for item in overdue.get("commitments", [])[:3]:
                direction = "You promised" if item["direction"] == "i_promised" else "They promised"
                lines.append(f"  {direction}: {item['title']}")

            for item in overdue.get("delegations", [])[:3]:
                lines.append(f"  Waiting on {item['assignee']}: {item['title']}")

        if briefing.get("pending_replies") and briefing["pending_replies"].get("total", 0) > 0:
            lines.extend(["", "PENDING REPLIES", "-" * 30])
            for item in briefing["pending_replies"].get("items", [])[:5]:
                person = item.get("with_person", "someone")
                lines.append(f"  {item['title']} ({person})")

        if briefing.get("delegations") and briefing["delegations"].get("total_waiting", 0) > 0:
            lines.extend(["", "WAITING ON OTHERS", "-" * 30])
            for person in briefing["delegations"].get("by_person", [])[:5]:
                lines.append(f"  {person['name']}: {person['total']} items")

        if briefing.get("emails") and briefing["emails"].get("urgent_count", 0) > 0:
            lines.extend(["", "URGENT EMAILS", "-" * 30])
            for email in briefing["emails"].get("emails", []):
                if email.get("is_urgent"):
                    sender = email["from_name"] or email["from"]
                    lines.append(f"  From {sender}: {email['subject']}")

        lines.extend([
            "",
            "-" * 30,
            "Have a productive day!",
            "",
            "Generated by Executive Copilot",
        ])

        return "\n".join(lines)
