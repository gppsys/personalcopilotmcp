"""Workspace facade used by the web workspace and its chat.

One place that knows how to read and change the executive's working set:
Asana tasks, follow-ups / commitments / delegations (local DB), calendar
events, e-mail search, Obsidian notes and e-mail -> calendar proposals.
Every account-spanning read runs accounts in parallel and reports per-account
errors instead of failing as a whole.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo

from executive_copilot.logging import get_logger
from executive_copilot.services.email_calendar import EmailCalendarService
from executive_copilot.services.obsidian_notes import (
    MeetingNotesSyncService,
    MeetingNotesVault,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)

T = TypeVar("T")
ACCOUNT_TIMEOUT = 25.0


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


class WorkspaceService:
    """Read/write operations over the executive's workspace."""

    def __init__(self, config: Any, credential_manager: Any) -> None:
        self.config = config
        self.credential_manager = credential_manager
        self.tz = ZoneInfo(config.calendar_defaults.timezone)

    # --- infrastructure -----------------------------------------------------

    def _accounts(self, provider_type: str) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        out: list[dict[str, str]] = []
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                key = (pa.account_id, pa.provider_name)
                if pa.provider_type == provider_type and key not in seen:
                    seen.add(key)
                    out.append({
                        "account_id": pa.account_id,
                        "provider": pa.provider_name,
                        "organization": org.name,
                    })
        return out

    def _authorized(self, provider_type: str, provider_name: str, account_id: str) -> bool:
        cred = self.credential_manager.get_credential(provider_type, provider_name, account_id)
        if not cred:
            return False
        return any(cred.get(k) for k in ("access_token", "api_key", "refresh_token"))

    async def _per_account(
        self,
        accounts: list[dict[str, str]],
        provider_type: str,
        fn: Callable[[dict[str, str]], Awaitable[list[T]]],
    ) -> tuple[list[tuple[dict[str, str], T]], list[dict[str, str]]]:
        async def run(acc: dict[str, str]) -> tuple[dict[str, str], list[T], str | None]:
            if self.credential_manager.get_credential(provider_type, acc["provider"], acc["account_id"]) is None:
                return acc, [], None  # never connected: not an error, it's managed in Cuentas
            if not self._authorized(provider_type, acc["provider"], acc["account_id"]):
                return acc, [], "Not authorized"
            try:
                return acc, await asyncio.wait_for(fn(acc), ACCOUNT_TIMEOUT), None
            except TimeoutError:
                return acc, [], "Timed out"
            except Exception as e:
                return acc, [], str(e)

        rows: list[tuple[dict[str, str], T]] = []
        errors: list[dict[str, str]] = []
        for acc, items, err in await asyncio.gather(*(run(a) for a in accounts)):
            if err:
                errors.append({"account": acc["account_id"], "provider": acc["provider"], "error": err})
            rows.extend((acc, item) for item in items)
        return rows, errors

    def _db(self) -> Any:
        from executive_copilot.database import DatabaseManager

        return DatabaseManager(self.config.database)

    def ai_service(self) -> Any | None:
        """Server-side AI (OpenAI) or None when it cannot be built."""
        try:
            from executive_copilot.providers.base import ProviderRegistry
            from executive_copilot.services.ai import AIService
            from executive_copilot.services.container import ServiceContainer

            container = ServiceContainer(
                self.config, self._db(), ProviderRegistry(), self.credential_manager
            )
            ai = AIService(container)
            ai._get_openai_api_key()  # fail fast when no key is stored
            return ai
        except Exception as e:
            logger.warning("Server AI unavailable", error=str(e))
            return None

    # --- calendar -----------------------------------------------------------

    def _calendar_provider(self, provider: str, account: str) -> Any:
        if provider == "google":
            from executive_copilot.providers.calendar.google import GoogleCalendarProvider

            return GoogleCalendarProvider(self.credential_manager, account)
        from executive_copilot.providers.calendar.outlook import OutlookCalendarProvider

        return OutlookCalendarProvider(self.credential_manager, account)

    async def events(self, days: int = 1, start: date | None = None) -> dict[str, Any]:
        """Events from every calendar account between start (default today) and +days."""
        from executive_copilot.providers.calendar.interface import EventQuery

        first = start or datetime.now(self.tz).date()
        lo = datetime.combine(first, datetime.min.time(), tzinfo=self.tz)
        hi = lo + timedelta(days=days)

        async def fetch(acc: dict[str, str]) -> list[Any]:
            provider = self._calendar_provider(acc["provider"], acc["account_id"])
            events, _ = await provider.get_events(EventQuery(start_time=lo, end_time=hi, limit=250))
            return list(events)

        rows, errors = await self._per_account(self._accounts("calendar"), "calendar", fetch)
        events = []
        for acc, ev in rows:
            if getattr(ev.status, "value", ev.status) == "cancelled":
                continue
            location = ev.location
            events.append({
                "id": ev.provider_ref.external_id if ev.provider_ref else str(ev.id),
                "title": ev.title,
                "start": ev.start_time.astimezone(self.tz).isoformat(),
                "end": ev.end_time.astimezone(self.tz).isoformat(),
                "all_day": ev.is_all_day,
                "location": location.display_name if location else None,
                "conference_url": location.conference_url if location else None,
                "account": acc["account_id"],
                "organization": acc["organization"],
                "url": ev.provider_ref.external_url if ev.provider_ref else None,
                "attendees": [
                    {"email": a.email, "name": a.display_name} for a in (ev.attendees or [])[:40]
                ],
            })
        events.sort(key=lambda e: e["start"])
        return {"from": lo.isoformat(), "to": hi.isoformat(), "events": events, "errors": errors}

    def email_calendar(self, with_ai: bool = True) -> EmailCalendarService:
        return EmailCalendarService(
            self.config,
            self.credential_manager,
            db_manager=self._db(),
            ai_service=self.ai_service() if with_ai else None,
        )

    # --- e-mail -------------------------------------------------------------

    def _email_provider(self, provider: str, account: str) -> Any:
        if provider == "gmail":
            from executive_copilot.providers.email.gmail import GmailProvider

            return GmailProvider(self.credential_manager, account)
        from executive_copilot.providers.email.outlook import OutlookProvider

        return OutlookProvider(self.credential_manager, account)

    async def search_emails(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Search every mailbox (Gmail query syntax works on Gmail accounts)."""

        async def fetch(acc: dict[str, str]) -> list[Any]:
            provider = self._email_provider(acc["provider"], acc["account_id"])
            return list(await provider.search(query, max_results=limit))

        rows, errors = await self._per_account(self._accounts("email"), "email", fetch)
        emails = [
            {
                "account": acc["account_id"],
                "from": e.sender.address,
                "from_name": e.sender.display_name,
                "subject": e.subject,
                "received_at": e.received_at.astimezone(self.tz).isoformat(),
                "snippet": (e.snippet or "")[:300],
                "is_read": e.is_read,
            }
            for acc, e in rows
        ]
        emails.sort(key=lambda x: x["received_at"], reverse=True)
        return {"query": query, "emails": emails[: limit * 3], "errors": errors}

    # --- tasks ----------------------------------------------------------------

    def _task_provider(self, account: str) -> Any:
        from executive_copilot.providers.task.asana import AsanaProvider

        return AsanaProvider(self.credential_manager, account)

    def _default_task_account(self, account: str | None) -> str:
        accounts = self._accounts("task")
        if account:
            if not any(a["account_id"] == account for a in accounts):
                raise ValueError(f"Task account {account} not configured")
            return account
        if not accounts:
            raise ValueError("No task accounts configured")
        return accounts[0]["account_id"]

    @staticmethod
    def _task(task: Any, account: str) -> dict[str, Any]:
        return {
            "id": task.provider_ref.external_id if task.provider_ref else str(task.id),
            "title": task.title,
            "notes": (task.description or "")[:500],
            "status": task.status.value,
            "priority": task.priority.value,
            "due_date": task.due_date.date().isoformat() if task.due_date else None,
            "project": task.project_name,
            "section": task.section_name,
            "url": task.provider_ref.external_url if task.provider_ref else None,
            "account": account,
        }

    async def asana_tasks(self) -> dict[str, Any]:
        async def fetch(acc: dict[str, str]) -> list[Any]:
            return list(await self._task_provider(acc["account_id"]).get_my_tasks())

        rows, errors = await self._per_account(self._accounts("task"), "task", fetch)
        tasks = [self._task(t, acc["account_id"]) for acc, t in rows]
        tasks.sort(key=lambda t: (t["due_date"] is None, t["due_date"] or ""))
        return {"tasks": tasks, "errors": errors}

    async def create_asana_task(
        self,
        title: str,
        notes: str | None = None,
        due_date: str | None = None,
        project_id: str | None = None,
        account: str | None = None,
    ) -> dict[str, Any]:
        from executive_copilot.providers.task import NewTask

        acc = self._default_task_account(account)
        due = (
            datetime.combine(date.fromisoformat(due_date), datetime.min.time(), tzinfo=UTC)
            if due_date
            else None
        )
        task = await self._task_provider(acc).create_task(
            NewTask(title=title, description=notes, project_id=project_id, due_date=due)
        )
        return self._task(task, acc)

    async def complete_asana_task(self, task_id: str, account: str | None = None) -> dict[str, Any]:
        if not task_id.isdigit():
            raise ValueError(
                "Ese ID no es de una tarea de Asana (parece un seguimiento, compromiso o delegación)"
            )
        acc = self._default_task_account(account)
        return self._task(await self._task_provider(acc).complete_task(task_id), acc)

    async def tracked_items(self) -> dict[str, Any]:
        """Follow-ups, commitments and delegations from the local database."""
        from executive_copilot.database.repositories import (
            CommitmentRepository,
            DelegationRepository,
            FollowUpRepository,
        )

        db = self._db()
        try:
            async with db.session() as session:
                follow_ups = await FollowUpRepository(session).get_pending(limit=100)
                commitments = CommitmentRepository(session)
                mine = await commitments.get_my_commitments(limit=100)
                theirs = await commitments.get_others_commitments(limit=100)
                delegations = await DelegationRepository(session).get_pending(limit=100)
        finally:
            await db.close()

        return {
            "follow_ups": [
                {
                    "id": str(f.id),
                    "title": f.title,
                    "context": f.context,
                    "person": f.with_person_name or f.with_person_email,
                    "due_date": _iso(f.due_date),
                    "priority": f.priority.value,
                    "is_overdue": f.is_overdue,
                    "source": f.source_type,
                }
                for f in follow_ups
            ],
            "my_commitments": [self._commitment(c) for c in mine],
            "their_commitments": [self._commitment(c) for c in theirs],
            "delegations": [
                {
                    "id": str(d.id),
                    "title": d.title,
                    "person": d.assignee_name or d.assignee_email,
                    "due_date": _iso(d.due_date),
                    "is_overdue": d.is_overdue,
                    "days_waiting": d.days_waiting,
                }
                for d in delegations
            ],
        }

    @staticmethod
    def _commitment(c: Any) -> dict[str, Any]:
        return {
            "id": str(c.id),
            "title": c.title,
            "person": c.promisee_name or c.promiser_name or c.promisee_email or c.promiser_email,
            "due_date": _iso(c.due_date),
            "is_overdue": c.is_overdue,
            "status": c.status.value,
        }

    async def complete_item(self, kind: str, item_id: str) -> dict[str, Any]:
        """Mark a follow-up, commitment or delegation as completed."""
        from executive_copilot.database.repositories import (
            CommitmentRepository,
            DelegationRepository,
            FollowUpRepository,
        )

        repos: dict[str, Any] = {
            "follow_up": FollowUpRepository,
            "commitment": CommitmentRepository,
            "delegation": DelegationRepository,
        }
        if kind not in repos:
            raise ValueError(f"Unknown item kind: {kind}")
        uid = UUID(item_id)
        db = self._db()
        try:
            async with db.session() as session:
                repo = repos[kind](session)
                item = await repo.get_by_id(uid)
                if item is None:
                    raise ValueError(f"{kind} {item_id} not found")
                await repo.update(item.mark_completed())
                await session.commit()
        finally:
            await db.close()
        return {"kind": kind, "id": item_id, "completed": True}

    # --- Obsidian -------------------------------------------------------------

    def notes_service(self, with_ai: bool = False) -> MeetingNotesSyncService:
        return MeetingNotesSyncService(
            self.config, self.credential_manager, ai_service=self.ai_service() if with_ai else None
        )

    def vault(self, folder: str = "meetings") -> MeetingNotesVault:
        """The meetings vault, or the people folder browsed the same way."""
        meetings = self.notes_service().require_vault()
        if folder == "meetings":
            return meetings
        if folder == "people" and self.config.obsidian.people_folder:
            people = MeetingNotesVault(meetings.vault_path, self.config.obsidian.people_folder, None)
            if people.meetings_dir.exists():
                return people
        raise ValueError(f"Unknown or missing folder: {folder}")

    # --- overview -------------------------------------------------------------

    async def overview(self) -> dict[str, Any]:
        """Today at a glance: agenda, tasks due, tracked items."""
        today = datetime.now(self.tz).date().isoformat()
        results: list[Any] = await asyncio.gather(
            self.events(days=1),
            self.asana_tasks(),
            self.tracked_items(),
            return_exceptions=True,
        )
        events, tasks, tracked = results
        out: dict[str, Any] = {"date": today, "errors": []}

        if isinstance(events, BaseException):
            out["events"] = []
            out["errors"].append({"source": "calendar", "error": str(events)})
        else:
            out["events"] = events["events"]
            out["errors"].extend(events["errors"])

        if isinstance(tasks, BaseException):
            open_tasks: list[dict[str, Any]] = []
            out["errors"].append({"source": "asana", "error": str(tasks)})
        else:
            open_tasks = tasks["tasks"]
            out["errors"].extend(tasks["errors"])
        out["tasks_overdue"] = [t for t in open_tasks if t["due_date"] and t["due_date"] < today]
        out["tasks_today"] = [t for t in open_tasks if t["due_date"] == today]
        out["tasks_open"] = len(open_tasks)

        if isinstance(tracked, BaseException):
            out["errors"].append({"source": "database", "error": str(tracked)})
            tracked = {"follow_ups": [], "my_commitments": [], "delegations": []}
        out["follow_ups_overdue"] = [f for f in tracked["follow_ups"] if f["is_overdue"]]
        out["follow_ups_open"] = len(tracked["follow_ups"])
        out["commitments_overdue"] = [c for c in tracked["my_commitments"] if c["is_overdue"]]
        out["delegations_overdue"] = [d for d in tracked["delegations"] if d["is_overdue"]]

        try:
            notes = self.vault().list_notes()[:5]
        except ValueError:
            notes = []
        out["recent_notes"] = notes
        return out
