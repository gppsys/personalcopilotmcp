"""Service for generating morning briefings."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
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
from executive_copilot.providers.task.asana import AsanaProvider

logger = get_logger(__name__)

# Cache of the last generated briefing, shared across processes so the
# admin dashboard can load instantly instead of hitting every provider.
_BRIEFING_CACHE_PATH = Path(__file__).resolve().parents[3] / "briefing_cache.json"


def save_briefing_cache(briefing: dict[str, Any]) -> None:
    """Persist the last generated briefing to disk (best effort)."""
    try:
        _BRIEFING_CACHE_PATH.write_text(json.dumps(briefing, default=str))
    except OSError as e:
        logger.warning("Failed to write briefing cache", error=str(e))


def load_briefing_cache() -> dict[str, Any] | None:
    """Load the last generated briefing, or None if not available."""
    try:
        return json.loads(_BRIEFING_CACHE_PATH.read_text())  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError):
        return None


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

    def _get_task_accounts(self) -> list[dict[str, str]]:
        """Get all configured task accounts."""
        accounts = []
        seen = set()
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                if pa.provider_type == "task":
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

    def _get_task_provider(self, provider_name: str, account_id: str) -> AsanaProvider:
        """Get task provider instance."""
        if provider_name == "asana":
            return AsanaProvider(self.credential_manager, account_id)
        raise ValueError(f"Unknown task provider: {provider_name}")

    async def generate_briefing(
        self,
        include_emails: bool = True,
        include_calendar: bool = True,
        include_tasks: bool = True,
        max_emails: int = 10,
        max_events: int = 15,
        max_tasks: int = 15,
    ) -> dict[str, Any]:
        """Generate a comprehensive morning briefing.

        Args:
            include_emails: Include email summary
            include_calendar: Include today's events
            include_tasks: Include overdue items, pending work, and Asana tasks
            max_emails: Maximum urgent emails to include
            max_events: Maximum events to include
            max_tasks: Maximum tasks to include

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
            "emails_requiring_attention": None,
            "tasks": None,
            "overdue": None,
            "pending_replies": None,
            "delegations": None,
            "commitments": None,
            "errors": [],
        }

        async_tasks = []

        if include_calendar:
            async_tasks.append(self._get_calendar_briefing(max_events))

        if include_emails:
            async_tasks.append(self._get_email_briefing(max_emails))
            async_tasks.append(self._get_emails_requiring_attention(max_emails))

        if include_tasks:
            async_tasks.append(self._get_task_briefing(max_tasks))
            async_tasks.append(self._get_overdue_items())
            async_tasks.append(self._get_pending_replies())
            async_tasks.append(self._get_delegation_summary())
            async_tasks.append(self._get_commitment_summary())

        results = await asyncio.gather(*async_tasks, return_exceptions=True)

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

            if isinstance(results[idx], Exception):
                briefing["errors"].append(f"Emails requiring attention: {results[idx]}")
            else:
                briefing["emails_requiring_attention"] = results[idx]
            idx += 1

        if include_tasks:
            if isinstance(results[idx], Exception):
                briefing["errors"].append(f"Tasks: {results[idx]}")
            else:
                briefing["tasks"] = results[idx]
            idx += 1

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

        save_briefing_cache(briefing)

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
                    is_urgent = self._is_urgent_email(email) and not self._is_promotional_email(email)
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

    _PROMOTIONAL_LABELS = frozenset({
        "CATEGORY_PROMOTIONS",
        "CATEGORY_SOCIAL",
        "CATEGORY_FORUMS",
    })

    _PROMOTIONAL_SENDER_PREFIXES = (
        "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
        "newsletter", "newsletters", "marketing", "promo", "promociones",
        "promotions", "offers", "ofertas", "deals", "notification",
        "notifications", "notificaciones", "alerts", "avisos", "mailer",
    )

    _UNSUBSCRIBE_MARKERS = (
        "unsubscribe",
        "cancelar suscripción",
        "cancelar suscripcion",
        "darse de baja",
        "date de baja",
        "dejar de recibir",
    )

    def _is_promotional_email(self, email: Email) -> bool:
        """Check if email is promotional/bulk noise that never needs a reply."""
        for label in email.labels:
            if (label.provider_id or label.name) in self._PROMOTIONAL_LABELS:
                return True

        local_part = email.sender.address.split("@")[0].lower()
        if local_part.startswith(self._PROMOTIONAL_SENDER_PREFIXES):
            return True

        content = " ".join(
            part.lower() for part in (email.snippet, email.body_text) if part
        )
        return any(marker in content for marker in self._UNSUBSCRIBE_MARKERS)

    def _email_requires_attention(self, email: Email) -> tuple[bool, str | None]:
        """Check if email requires attention/action.

        Returns (requires_attention, reason).
        """
        subject = (email.subject or "").lower()
        snippet = (email.snippet or "").lower()
        combined = subject + " " + snippet

        action_indicators = [
            ("question", "contains a question"),
            ("can you", "requests your action"),
            ("could you", "requests your action"),
            ("would you", "requests your action"),
            ("please", "requests your action"),
            ("need your", "needs your input"),
            ("waiting for", "waiting for your response"),
            ("following up", "follow-up message"),
            ("remind", "reminder"),
            ("approve", "needs approval"),
            ("approval", "needs approval"),
            ("review", "needs review"),
            ("sign off", "needs sign-off"),
            ("decision", "needs decision"),
            ("feedback", "needs feedback"),
            ("response", "awaiting response"),
            ("reply", "awaiting reply"),
            ("confirm", "needs confirmation"),
            ("schedule", "scheduling request"),
            ("meeting request", "meeting request"),
            ("invitation", "invitation"),
            ("action required", "action required"),
            ("action needed", "action needed"),
            ("your input", "needs your input"),
            ("thoughts on", "requests your opinion"),
            ("what do you think", "requests your opinion"),
            ("?", "contains a question"),
        ]

        for indicator, reason in action_indicators:
            if indicator in combined:
                return True, reason

        return False, None

    async def _get_emails_requiring_attention(self, max_emails: int) -> dict[str, Any]:
        """Get emails that require attention/follow-up."""
        accounts = self._get_email_accounts()
        attention_emails: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for acc in accounts:
            if not self._is_authorized("email", acc["provider"], acc["account_id"]):
                continue

            try:
                provider = self._get_email_provider(acc["provider"], acc["account_id"])
                unread = await provider.get_unread(max_results=50)

                for email in unread:
                    if self._is_promotional_email(email):
                        continue

                    requires_attention, reason = self._email_requires_attention(email)
                    is_urgent = self._is_urgent_email(email)

                    if requires_attention or is_urgent:
                        days_waiting = max(
                            0, (datetime.now(UTC) - email.received_at).days
                        )
                        attention_emails.append({
                            "subject": email.subject,
                            "from": email.sender.address,
                            "from_name": email.sender.display_name,
                            "received_at": email.received_at.isoformat(),
                            "days_waiting": days_waiting,
                            "snippet": email.snippet,
                            "thread_id": email.thread_id,
                            "is_urgent": is_urgent,
                            "requires_attention": requires_attention,
                            "attention_reason": reason,
                            "account": acc["account_id"],
                            "organization": acc["organization"],
                        })

            except Exception as e:
                errors.append({"account": acc["account_id"], "error": str(e)})

        attention_emails.sort(
            key=lambda x: (
                not x["is_urgent"],
                not x["requires_attention"],
                x["received_at"],
            ),
        )
        attention_emails = attention_emails[:max_emails]

        return {
            "total": len(attention_emails),
            "urgent_count": sum(1 for e in attention_emails if e["is_urgent"]),
            "action_needed_count": sum(1 for e in attention_emails if e["requires_attention"]),
            "emails": attention_emails,
            "errors": errors,
        }

    def _task_to_dict(
        self, task: Any, acc: dict[str, str], is_overdue: bool
    ) -> dict[str, Any]:
        """Map a task to briefing dict with context on why it needs attention."""
        now = datetime.now(UTC)
        days_overdue = 0
        if is_overdue and task.due_date:
            days_overdue = max(0, (now - task.due_date).days)

        description = (task.description or "").strip()
        if len(description) > 300:
            description = description[:300] + "…"

        priority = task.priority.value if task.priority else None
        reasons = []
        if is_overdue:
            if days_overdue >= 1:
                reasons.append(
                    f"overdue by {days_overdue} day{'s' if days_overdue != 1 else ''}"
                )
            else:
                reasons.append("past due")
        else:
            reasons.append("due today")
        if priority in ("high", "urgent", "critical"):
            reasons.append(f"{priority} priority")
        if task.is_blocked:
            reasons.append("blocked")
        if task.comment_count:
            reasons.append(f"{task.comment_count} comments")

        return {
            "id": str(task.id),
            "provider_id": task.provider_ref.external_id if task.provider_ref else None,
            "title": task.title,
            "description": description or None,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "days_overdue": days_overdue if is_overdue else 0,
            "project_name": task.project_name,
            "section_name": task.section_name,
            "assignee_name": task.assignee_name,
            "tags": list(task.tags or []),
            "priority": priority,
            "attention_reason": ", ".join(reasons),
            "url": task.provider_ref.external_url if task.provider_ref else None,
            "account": acc["account_id"],
            "organization": acc["organization"],
        }

    async def _get_task_briefing(self, max_tasks: int) -> dict[str, Any]:
        """Get tasks due today and overdue from task providers."""
        accounts = self._get_task_accounts()
        tasks_due_today: list[dict[str, Any]] = []
        tasks_overdue: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        today = datetime.now(UTC).date()

        for acc in accounts:
            if not self._is_authorized("task", acc["provider"], acc["account_id"]):
                continue

            try:
                provider = self._get_task_provider(acc["provider"], acc["account_id"])

                overdue = await provider.get_overdue_tasks()
                for task in overdue[:max_tasks]:
                    tasks_overdue.append(self._task_to_dict(task, acc, is_overdue=True))

                due_today = await provider.get_due_today()
                for task in due_today[:max_tasks]:
                    if task.due_date and task.due_date.date() == today:
                        tasks_due_today.append(
                            self._task_to_dict(task, acc, is_overdue=False)
                        )

            except Exception as e:
                errors.append({"account": acc["account_id"], "error": str(e)})

        tasks_overdue.sort(key=lambda x: x.get("due_date") or "")
        tasks_due_today.sort(key=lambda x: x.get("due_date") or "")

        return {
            "due_today": tasks_due_today[:max_tasks],
            "due_today_count": len(tasks_due_today),
            "overdue": tasks_overdue[:max_tasks],
            "overdue_count": len(tasks_overdue),
            "total_attention_needed": len(tasks_due_today) + len(tasks_overdue),
            "errors": errors,
        }

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
            "emails_requiring_attention": 0,
            "tasks_due_today": 0,
            "tasks_overdue": 0,
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

        if briefing.get("emails_requiring_attention"):
            summary["emails_requiring_attention"] = briefing["emails_requiring_attention"].get(
                "action_needed_count", 0
            )

        if briefing.get("tasks"):
            summary["tasks_due_today"] = briefing["tasks"].get("due_today_count", 0)
            summary["tasks_overdue"] = briefing["tasks"].get("overdue_count", 0)

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

        if summary.get("meetings", 0) > 0:
            parts.append(pluralize(summary["meetings"], "meeting", "meetings"))

        if summary.get("tasks_due_today", 0) > 0:
            parts.append(pluralize(summary["tasks_due_today"], "task due today", "tasks due today"))

        if summary.get("tasks_overdue", 0) > 0:
            parts.append(pluralize(summary["tasks_overdue"], "overdue task", "overdue tasks"))

        if summary.get("emails_requiring_attention", 0) > 0:
            parts.append(pluralize(summary["emails_requiring_attention"], "email needing attention", "emails needing attention"))

        if summary.get("pending_replies", 0) > 0:
            parts.append(pluralize(summary["pending_replies"], "pending reply", "pending replies"))

        if summary.get("overdue_items", 0) > 0:
            parts.append(pluralize(summary["overdue_items"], "overdue follow-up", "overdue follow-ups"))

        if summary.get("urgent_emails", 0) > 0:
            parts.append(pluralize(summary["urgent_emails"], "urgent email", "urgent emails"))

        if summary.get("waiting_on_others", 0) > 0:
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

        def format_task(task: dict[str, Any]) -> list[str]:
            task_lines = []
            project = f" [{task['project_name']}]" if task.get("project_name") else ""
            due = f" (due {task['due_date'][:10]})" if task.get("due_date") else ""
            task_lines.append(f"  {task['title']}{project}{due}")
            if task.get("attention_reason"):
                task_lines.append(f"    Why: {task['attention_reason']}")
            if task.get("description"):
                desc = task["description"].replace("\n", " ").strip()
                if len(desc) > 150:
                    desc = desc[:150] + "…"
                task_lines.append(f"    Details: {desc}")
            if task.get("assignee_name"):
                task_lines.append(f"    Assignee: {task['assignee_name']}")
            if task.get("url"):
                task_lines.append(f"    {task['url']}")
            return task_lines

        if briefing.get("tasks"):
            tasks = briefing["tasks"]
            if tasks.get("overdue"):
                lines.extend(["", "OVERDUE TASKS", "-" * 30])
                for task in tasks["overdue"][:5]:
                    lines.extend(format_task(task))

            if tasks.get("due_today"):
                lines.extend(["", "TASKS DUE TODAY", "-" * 30])
                for task in tasks["due_today"][:5]:
                    lines.extend(format_task(task))

        if briefing.get("emails_requiring_attention"):
            attention = briefing["emails_requiring_attention"]
            if attention.get("emails"):
                lines.extend(["", "EMAILS REQUIRING ATTENTION", "-" * 30])
                for email in attention["emails"][:5]:
                    sender = email.get("from_name") or email["from"]
                    reason = f" ({email['attention_reason']})" if email.get("attention_reason") else ""
                    urgent = " [URGENT]" if email.get("is_urgent") else ""
                    lines.append(f"  From {sender}{urgent}: {email['subject']}{reason}")
                    waiting = email.get("days_waiting", 0)
                    if waiting >= 1:
                        lines.append(f"    Waiting {waiting} day{'s' if waiting != 1 else ''} for your reply")
                    if email.get("snippet"):
                        snippet = email["snippet"].replace("\n", " ").strip()
                        if len(snippet) > 150:
                            snippet = snippet[:150] + "…"
                        lines.append(f"    Preview: {snippet}")

        if briefing.get("overdue") and briefing["overdue"].get("total", 0) > 0:
            lines.extend(["", "OVERDUE FOLLOW-UPS & COMMITMENTS", "-" * 30])
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
