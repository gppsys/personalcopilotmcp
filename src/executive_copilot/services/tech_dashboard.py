"""Tech dashboard data service — feeds the CTO dashboard live from Asana.

The `/tech` dashboard is served over HTTP, so it cannot rely on the Cowork
`window.cowork.callMcpTool` bridge (that only exists inside Claude clients).
This module fetches and shapes the Asana data server-side so the dashboard
works as a plain web page, and turns selected review agreements ("acuerdos")
into real Asana tickets for the whole team.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from executive_copilot.logging import get_logger
from executive_copilot.models import Task, TaskStatus
from executive_copilot.providers.task.asana import AsanaProvider
from executive_copilot.providers.task.interface import NewTask, TaskQuery

if TYPE_CHECKING:
    from executive_copilot.credentials import CredentialManager

logger = get_logger(__name__)

# Default Asana account and projects that back the Paycode tech dashboard.
DEFAULT_ACCOUNT_ID = "gonzalo@paycode.com.mx"
CLIENTS_PROJECT = "1214942074205523"  # Implementación Clientes
ROADMAP_PROJECT = "1214777410141437"  # Engineering Roadmap
SUPPORT_TICKETS_PROJECT = "1216527974783306"  # Tickets Soporte Ingeniería


def _provider(credential_manager: CredentialManager, account_id: str) -> AsanaProvider:
    return AsanaProvider(credential_manager, account_id)


def _serialize_task(task: Task) -> dict[str, Any]:
    """Flatten a domain Task into the fields the dashboard renders."""
    cf = task.custom_fields or {}
    return {
        "title": task.title,
        "notes": task.description or "",
        "status": task.status.value,
        "completed": task.status == TaskStatus.COMPLETED,
        "assignee": task.assignee_name or None,
        "assignee_id": task.assignee_id,
        "section": task.section_name or None,
        "project": task.project_name or None,
        "priority": cf.get("Priority"),
        "work_status": cf.get("Status"),
        "due": task.due_date.date().isoformat() if task.due_date else None,
        "completed_at": task.completed_at.date().isoformat() if task.completed_at else None,
        "url": task.provider_ref.external_url if task.provider_ref else None,
    }


def _due_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    """Order active tasks: dated ones first (soonest first), undated last."""
    due = item.get("due")
    return (0, due) if due else (1, "9999-99-99")


def _week_bounds(now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    """Return (last_monday, this_monday, plan_end) for ISO-week filtering.

    last_monday..this_monday is the previous ISO week (Mon 00:00 → next Mon 00:00),
    which defines "avances de la semana pasada". plan_end is the end of the current
    week + 2 weeks, the horizon for "el plan de esta semana".
    """
    now = now or datetime.now(UTC)
    weekday = now.isocalendar().weekday  # Mon=1 .. Sun=7
    this_monday = (now - timedelta(days=weekday - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    last_monday = this_monday - timedelta(days=7)
    plan_end = this_monday + timedelta(days=21)
    return last_monday, this_monday, plan_end


async def _last_week_done(
    provider: AsanaProvider,
    last_monday: datetime,
    this_monday: datetime,
) -> list[dict[str, Any]]:
    """Tasks completed in the previous ISO week across the whole workspace.

    The team closes work in many boards (roadmap, clients, Kanban, support…),
    so we query Asana's search endpoint filtered by completion date instead of
    listing per project — that returns every close, with no truncation.
    """
    try:
        tasks = await provider.get_completed_between(last_monday, this_monday, limit=100)
    except Exception as e:  # pragma: no cover - network guard
        logger.warning("Failed to fetch weekly completions from Asana", error=str(e))
        return []

    done = [_serialize_task(t) for t in tasks if t.title.strip()]
    done.sort(key=lambda item: item.get("completed_at") or "", reverse=True)
    return done


# Section names (case-insensitive substrings) that mean "blocked / on hold".
BLOCKED_SECTION_PATTERNS = (
    "bloq",
    "pausa",
    "hold",
    "espera",
    "stuck",
    "detenid",
    "blocked",
)


def _is_blocked_section(name: str | None) -> bool:
    low = (name or "").lower()
    return any(p in low for p in BLOCKED_SECTION_PATTERNS)


async def _blocked_tasks(provider: AsanaProvider) -> list[dict[str, Any]]:
    """Open tasks sitting in a 'blocked/paused' section across all projects.

    These are the items to raise with the C-Level (or show they're handled).
    Resolved by section name, so any board that adds a Bloqueado/Pausado column
    is picked up automatically.
    """
    try:
        projects = await provider.list_projects(include_archived=False)
    except Exception as e:  # pragma: no cover - network guard
        logger.warning("Failed to list projects for blocked scan", error=str(e))
        return []

    async def sections_for(project_id: str) -> list[Any]:
        try:
            return await provider.list_sections(project_id)
        except Exception:
            return []

    section_lists = await asyncio.gather(*[sections_for(p.id) for p in projects])
    # Keep the project + section name with each blocked section, so a task that
    # is multi-homed still shows *why* and *where* it is blocked.
    blocked_sections = [
        (s.id, project.name, s.name)
        for project, sections in zip(projects, section_lists, strict=False)
        for s in sections
        if _is_blocked_section(s.name)
    ]
    if not blocked_sections:
        return []

    async def tasks_for(section_id: str) -> list[Task]:
        try:
            return await provider.get_tasks_in_section(section_id)
        except Exception:
            return []

    task_lists = await asyncio.gather(*[tasks_for(sid) for sid, _, _ in blocked_sections])

    blocked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for (_sid, project_name, section_name), tasks in zip(
        blocked_sections, task_lists, strict=False
    ):
        for t in tasks:
            if t.status == TaskStatus.COMPLETED or not t.title.strip():
                continue
            key = t.provider_ref.external_id if t.provider_ref else t.title
            if key in seen:
                continue
            seen.add(key)
            item = _serialize_task(t)
            item["project"] = project_name
            item["section"] = section_name
            blocked.append(item)
    return blocked


async def get_dashboard_data(
    credential_manager: CredentialManager,
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> dict[str, Any]:
    """Live dashboard payload: clients, active roadmap, and recent wins.

    All lists are team-wide (fetched by project, not filtered to the current
    user), so the dashboard reflects the whole tech team's work.
    """
    provider = _provider(credential_manager, account_id)

    last_monday, this_monday, plan_end = _week_bounds()
    plan_end_iso = plan_end.date().isoformat()

    clients_raw, active_raw, done, blocked = await asyncio.gather(
        provider.get_tasks(
            TaskQuery(project_id=CLIENTS_PROJECT, include_completed=False, limit=100)
        ),
        provider.get_tasks(
            TaskQuery(project_id=ROADMAP_PROJECT, include_completed=False, limit=100)
        ),
        _last_week_done(provider, last_monday, this_monday),
        _blocked_tasks(provider),
    )

    clients = [_serialize_task(t) for t in clients_raw if t.title.strip()]

    # "Plan de esta semana": active roadmap tasks. Tasks due within the plan
    # horizon (or already overdue) are flagged so the UI can foreground them,
    # but undated work (epics like MDM) is kept so nothing important vanishes.
    active = []
    for t in active_raw:
        if not t.title.strip():
            continue
        item = _serialize_task(t)
        due = item.get("due")
        item["this_week"] = bool(due and due <= plan_end_iso)
        active.append(item)
    active.sort(key=_due_sort_key)

    # "Riesgos que monitoreo": active work overdue or due within the plan
    # horizon (this week + 2) — the delivery risk the CTO watches. Soonest first.
    today_iso = datetime.now(UTC).date().isoformat()
    at_risk = []
    for item in active:
        due = item.get("due")
        if due and due <= plan_end_iso:
            enriched = dict(item)
            enriched["overdue"] = due < today_iso
            at_risk.append(enriched)
    at_risk.sort(key=_due_sort_key)

    return {
        "clients": clients,
        "roadmap_active": active,
        "roadmap_done": done,
        "roadmap_at_risk": at_risk,
        "blocked": blocked,
        "week": {
            "last_start": last_monday.date().isoformat(),
            "last_end": (this_monday - timedelta(days=1)).date().isoformat(),
            "plan_end": plan_end_iso,
        },
        "counts": {
            "clients": len(clients),
            "roadmap_active": len(active),
            "done": len(done),
            "at_risk": len(at_risk),
            "blocked": len(blocked),
        },
    }


async def get_team_members(
    credential_manager: CredentialManager,
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> list[dict[str, Any]]:
    """Asana workspace members, for the ticket-assignee picker."""
    provider = _provider(credential_manager, account_id)
    workspace = await provider._workspace_id()
    users = await provider._get_all(
        "/users",
        params={"workspace": workspace, "opt_fields": "gid,name,email"},
    )
    members = [
        {"id": u.get("gid"), "name": u.get("name") or u.get("email"), "email": u.get("email")}
        for u in users
        if u.get("gid")
    ]
    members.sort(key=lambda m: (m["name"] or "").lower())
    return members


async def list_projects(
    credential_manager: CredentialManager,
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> list[dict[str, Any]]:
    """Non-archived Asana projects, for the ticket-project picker."""
    provider = _provider(credential_manager, account_id)
    projects = await provider.list_projects(include_archived=False)
    return [{"id": p.id, "name": p.name} for p in projects]


async def create_tickets(
    credential_manager: CredentialManager,
    items: list[dict[str, Any]],
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> dict[str, Any]:
    """Turn selected review agreements into Asana tasks.

    Each item: {title, notes?, assignee_id?, project_id?, due_on?}. Titles
    are required; everything else is optional. Returns per-item results so the
    UI can report exactly which acuerdos became tickets.
    """
    provider = _provider(credential_manager, account_id)
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for item in items:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        due_on = item.get("due_on")
        due_date = None
        if due_on:
            try:
                due_date = datetime.combine(date.fromisoformat(due_on), time.min, tzinfo=UTC)
            except ValueError:
                due_date = None
        try:
            task = await provider.create_task(
                NewTask(
                    title=title,
                    description=item.get("notes") or None,
                    project_id=item.get("project_id") or ROADMAP_PROJECT,
                    assignee_id=item.get("assignee_id") or None,
                    due_date=due_date,
                )
            )
            created.append(
                {
                    "title": task.title,
                    "url": task.provider_ref.external_url if task.provider_ref else None,
                    "assignee": task.assignee_name,
                }
            )
        except Exception as e:  # report failures per item, keep going
            logger.warning("Failed to create Asana ticket", title=title, error=str(e))
            errors.append({"title": title, "error": str(e)})

    return {"created": created, "errors": errors, "count": len(created)}
