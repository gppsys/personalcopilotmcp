"""Asana task provider implementation."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time
from typing import Any

import httpx

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.models import Task, TaskStatus
from executive_copilot.models.base import ProviderReference
from executive_copilot.providers.task.interface import (
    NewTask,
    ProjectInfo,
    SectionInfo,
    TaskProvider,
    TaskQuery,
)

ASANA_API_BASE = "https://app.asana.com/api/1.0"

TASK_OPT_FIELDS = ",".join(
    [
        "gid",
        "name",
        "notes",
        "completed",
        "completed_at",
        "created_at",
        "modified_at",
        "due_at",
        "due_on",
        "start_at",
        "start_on",
        "permalink_url",
        "assignee.gid",
        "assignee.name",
        "assignee.email",
        "created_by.gid",
        "followers.gid",
        "projects.gid",
        "projects.name",
        "memberships.project.gid",
        "memberships.project.name",
        "memberships.section.gid",
        "memberships.section.name",
        "tags.name",
        "custom_fields.name",
        "custom_fields.display_value",
        "num_subtasks",
        "num_likes",
    ]
)


class AsanaProvider(TaskProvider):
    """Asana provider using a personal access token."""

    def __init__(self, credential_manager: CredentialManager, account_id: str) -> None:
        self.credential_manager = credential_manager
        self.account_id = account_id
        self._access_token: str | None = None
        self._me: dict[str, Any] | None = None
        self._default_workspace: str | None = None

    @property
    def info(self) -> Any:
        from executive_copilot.providers.base import ProviderCapability, ProviderInfo

        return ProviderInfo(
            name="asana",
            display_name="Asana",
            provider_type="task",
            capabilities=[
                ProviderCapability.READ_TASKS,
                ProviderCapability.CREATE_TASKS,
                ProviderCapability.UPDATE_TASKS,
                ProviderCapability.DELETE_TASKS,
                ProviderCapability.MANAGE_PROJECTS,
            ],
            auth_type="token",
        )

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token

        cred = self.credential_manager.get_credential("task", "asana", self.account_id)
        if not cred or not cred.get("access_token"):
            raise ValueError(f"No Asana access token for {self.account_id}.")

        self._access_token = cred["access_token"]
        return self._access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    async def connect(self) -> None:
        await self.get_me()

    async def disconnect(self) -> None:
        return None

    async def health_check(self) -> bool:
        try:
            await self.get_me()
            return True
        except Exception:
            return False

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                f"{ASANA_API_BASE}{path}",
                headers=self._headers(),
                params=params,
                json=json,
            )
            if response.status_code == 401:
                raise ValueError("Asana token is invalid or expired.")
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

    async def _get_all(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        next_offset: str | None = None
        params = dict(params or {})
        params.setdefault("limit", min(limit or 100, 100))

        while True:
            if next_offset:
                params["offset"] = next_offset
            payload = await self._request("GET", path, params=params)
            page = payload.get("data", [])
            if isinstance(page, list):
                results.extend(item for item in page if isinstance(item, dict))
            if limit is not None and len(results) >= limit:
                return results[:limit]
            next_page = payload.get("next_page") or {}
            next_offset = next_page.get("offset") if isinstance(next_page, dict) else None
            if not next_offset:
                return results

    async def get_me(self) -> dict[str, Any]:
        if self._me:
            return self._me
        data = await self._request(
            "GET",
            "/users/me",
            params={"opt_fields": "gid,name,email,workspaces.gid,workspaces.name"},
        )
        me = data.get("data", {})
        if not isinstance(me, dict):
            raise ValueError("Unexpected Asana user response.")
        self._me = me
        workspaces = me.get("workspaces") or []
        if workspaces and isinstance(workspaces[0], dict):
            self._default_workspace = workspaces[0].get("gid")
        return me

    async def _workspace_id(self) -> str:
        if self._default_workspace:
            return self._default_workspace
        await self.get_me()
        if not self._default_workspace:
            raise ValueError("No Asana workspace available for this token.")
        return self._default_workspace

    async def list_projects(self, include_archived: bool = False) -> list[ProjectInfo]:
        workspace = await self._workspace_id()
        params: dict[str, Any] = {
            "workspace": workspace,
            "archived": str(include_archived).lower(),
            "opt_fields": "gid,name,notes,color,archived,team.gid,workspace.gid,owner.gid",
        }
        projects = await self._get_all("/projects", params=params)
        return [self._parse_project(project) for project in projects]

    async def get_project(self, project_id: str) -> ProjectInfo | None:
        try:
            payload = await self._request(
                "GET",
                f"/projects/{project_id}",
                params={
                    "opt_fields": (
                        "gid,name,notes,color,archived,team.gid,workspace.gid,owner.gid"
                    )
                },
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        data = payload.get("data")
        return self._parse_project(data) if isinstance(data, dict) else None

    async def list_sections(self, project_id: str) -> list[SectionInfo]:
        sections = await self._get_all(
            f"/projects/{project_id}/sections",
            params={"opt_fields": "gid,name,project.gid"},
        )
        return [
            SectionInfo(
                id=section["gid"],
                name=section.get("name", ""),
                project_id=(section.get("project") or {}).get("gid", project_id),
            )
            for section in sections
        ]

    async def get_tasks(self, query: TaskQuery) -> list[Task]:
        params: dict[str, Any] = {
            "completed_since": "now" if not query.include_completed else "1970-01-01T00:00:00Z",
            "opt_fields": TASK_OPT_FIELDS,
        }
        path = "/tasks"

        if query.project_id:
            path = f"/projects/{query.project_id}/tasks"
        else:
            params["workspace"] = await self._workspace_id()
            if query.assignee_id:
                params["assignee"] = query.assignee_id
            else:
                params["assignee"] = "me"

        if query.modified_since:
            params["modified_since"] = query.modified_since.isoformat()

        tasks = await self._get_all(path, params=params, limit=query.limit + query.offset)
        mapped = [self._parse_task(task) for task in tasks]

        if query.status == TaskStatus.COMPLETED:
            mapped = [task for task in mapped if task.status == TaskStatus.COMPLETED]
        elif query.status:
            mapped = [task for task in mapped if task.status == query.status]
        if query.due_before:
            mapped = [
                task for task in mapped if task.due_date and task.due_date <= query.due_before
            ]
        if query.due_after:
            mapped = [task for task in mapped if task.due_date and task.due_date >= query.due_after]
        if query.query:
            needle = query.query.lower()
            mapped = [
                task
                for task in mapped
                if needle in task.title.lower() or needle in (task.description or "").lower()
            ]

        return mapped[query.offset : query.offset + query.limit]

    async def get_task(self, task_id: str) -> Task | None:
        try:
            payload = await self._request(
                "GET",
                f"/tasks/{task_id}",
                params={"opt_fields": TASK_OPT_FIELDS},
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        data = payload.get("data")
        return self._parse_task(data) if isinstance(data, dict) else None

    async def get_my_tasks(self, include_completed: bool = False) -> list[Task]:
        return await self.get_tasks(TaskQuery(include_completed=include_completed, limit=100))

    async def get_overdue_tasks(self) -> list[Task]:
        today_start = datetime.combine(date.today(), time.min, tzinfo=UTC)
        return await self.get_tasks(TaskQuery(due_before=today_start, limit=100))

    async def get_due_today(self) -> list[Task]:
        today = date.today()
        tasks = await self.get_my_tasks(include_completed=False)
        return [task for task in tasks if task.due_date and task.due_date.date() == today]

    async def stream_tasks(self, query: TaskQuery) -> AsyncIterator[Task]:
        for task in await self.get_tasks(query):
            yield task

    async def create_task(self, task: NewTask) -> Task:
        data: dict[str, Any] = {
            "name": task.title,
            "workspace": await self._workspace_id(),
        }
        if task.description:
            data["notes"] = task.description
        if task.project_id:
            data["projects"] = [task.project_id]
        if task.assignee_id:
            data["assignee"] = task.assignee_id
        elif task.assignee_email:
            data["assignee"] = task.assignee_email
        if task.due_date:
            data["due_on"] = task.due_date.date().isoformat()

        payload = await self._request(
            "POST",
            "/tasks",
            json={"data": data},
            params={"opt_fields": TASK_OPT_FIELDS},
        )
        created = payload.get("data")
        if not isinstance(created, dict):
            raise ValueError("Unexpected Asana task creation response.")
        return self._parse_task(created)

    async def create_subtask(self, parent_task_id: str, task: NewTask) -> Task:
        data: dict[str, Any] = {"name": task.title}
        if task.description:
            data["notes"] = task.description
        if task.assignee_id:
            data["assignee"] = task.assignee_id
        elif task.assignee_email:
            data["assignee"] = task.assignee_email
        if task.due_date:
            data["due_on"] = task.due_date.date().isoformat()

        payload = await self._request(
            "POST",
            f"/tasks/{parent_task_id}/subtasks",
            json={"data": data},
            params={"opt_fields": TASK_OPT_FIELDS},
        )
        created = payload.get("data")
        if not isinstance(created, dict):
            raise ValueError("Unexpected Asana subtask creation response.")
        return self._parse_task(created)

    async def update_task(self, task_id: str, updates: dict[str, object]) -> Task:
        payload = await self._request(
            "PUT",
            f"/tasks/{task_id}",
            json={"data": updates},
            params={"opt_fields": TASK_OPT_FIELDS},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("Unexpected Asana task update response.")
        return self._parse_task(data)

    async def complete_task(self, task_id: str) -> Task:
        return await self.update_task(task_id, {"completed": True})

    async def reopen_task(self, task_id: str) -> Task:
        return await self.update_task(task_id, {"completed": False})

    async def delete_task(self, task_id: str) -> None:
        await self._request("DELETE", f"/tasks/{task_id}")

    async def move_task(
        self,
        task_id: str,
        project_id: str | None = None,
        section_id: str | None = None,
    ) -> Task:
        if project_id:
            await self._request(
                "POST",
                f"/tasks/{task_id}/addProject",
                json={"data": {"project": project_id}},
            )
        if section_id:
            await self._request(
                "POST",
                f"/sections/{section_id}/addTask",
                json={"data": {"task": task_id}},
            )
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Asana task not found after move: {task_id}")
        return task

    async def assign_task(
        self,
        task_id: str,
        assignee_id: str | None = None,
        assignee_email: str | None = None,
    ) -> Task:
        assignee = assignee_id or assignee_email
        if not assignee:
            raise ValueError("assignee_id or assignee_email is required")
        return await self.update_task(task_id, {"assignee": assignee})

    async def add_follower(self, task_id: str, user_id: str) -> Task:
        await self._request(
            "POST",
            f"/tasks/{task_id}/addFollowers",
            json={"data": {"followers": [user_id]}},
        )
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Asana task not found after adding follower: {task_id}")
        return task

    async def remove_follower(self, task_id: str, user_id: str) -> Task:
        await self._request(
            "POST",
            f"/tasks/{task_id}/removeFollowers",
            json={"data": {"followers": [user_id]}},
        )
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Asana task not found after removing follower: {task_id}")
        return task

    async def add_comment(self, task_id: str, text: str) -> dict[str, object]:
        payload = await self._request(
            "POST",
            f"/tasks/{task_id}/stories",
            json={"data": {"text": text}},
        )
        data = payload.get("data", {})
        return data if isinstance(data, dict) else {}

    async def get_comments(self, task_id: str, limit: int = 50) -> list[dict[str, object]]:
        return await self._get_all(
            f"/tasks/{task_id}/stories",
            params={"opt_fields": "gid,text,created_at,created_by.name,type"},
            limit=limit,
        )

    async def add_tag(self, task_id: str, tag: str) -> Task:
        await self._request("POST", f"/tasks/{task_id}/addTag", json={"data": {"tag": tag}})
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Asana task not found after adding tag: {task_id}")
        return task

    async def remove_tag(self, task_id: str, tag: str) -> Task:
        await self._request("POST", f"/tasks/{task_id}/removeTag", json={"data": {"tag": tag}})
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Asana task not found after removing tag: {task_id}")
        return task

    async def get_changes_since(
        self,
        sync_token: str | None = None,
        project_id: str | None = None,
    ) -> tuple[list[Task], str]:
        query = TaskQuery(project_id=project_id, include_completed=True, limit=100)
        if sync_token:
            query.modified_since = datetime.fromisoformat(sync_token)
        tasks = await self.get_tasks(query)
        return tasks, datetime.now(UTC).isoformat()

    def _parse_project(self, data: dict[str, Any]) -> ProjectInfo:
        return ProjectInfo(
            id=data["gid"],
            name=data.get("name", ""),
            description=data.get("notes"),
            color=data.get("color"),
            is_archived=bool(data.get("archived")),
            owner_id=(data.get("owner") or {}).get("gid"),
            team_id=(data.get("team") or {}).get("gid"),
            workspace_id=(data.get("workspace") or {}).get("gid"),
        )

    def _parse_task(self, data: dict[str, Any]) -> Task:
        memberships = data.get("memberships") or []
        first_membership = (
            memberships[0] if memberships and isinstance(memberships[0], dict) else {}
        )
        project = first_membership.get("project") or (data.get("projects") or [{}])[0]
        section = first_membership.get("section") or {}
        assignee = data.get("assignee") or {}

        from uuid import NAMESPACE_URL, uuid5
        task_id = uuid5(NAMESPACE_URL, f"asana:task:{data['gid']}")
        return Task(
            id=task_id,
            title=data.get("name") or "(Untitled)",
            description=data.get("notes"),
            status=TaskStatus.COMPLETED if data.get("completed") else TaskStatus.NOT_STARTED,
            due_date=self._parse_asana_datetime(data.get("due_at") or data.get("due_on")),
            start_date=self._parse_asana_datetime(data.get("start_at") or data.get("start_on")),
            completed_at=self._parse_asana_datetime(data.get("completed_at")),
            project_id=project.get("gid") if isinstance(project, dict) else None,
            project_name=project.get("name") if isinstance(project, dict) else None,
            section_id=section.get("gid") if isinstance(section, dict) else None,
            section_name=section.get("name") if isinstance(section, dict) else None,
            assignee_id=assignee.get("gid") if isinstance(assignee, dict) else None,
            assignee_email=assignee.get("email") if isinstance(assignee, dict) else None,
            assignee_name=assignee.get("name") if isinstance(assignee, dict) else None,
            creator_id=(data.get("created_by") or {}).get("gid"),
            followers=[
                follower["gid"]
                for follower in data.get("followers", [])
                if isinstance(follower, dict) and follower.get("gid")
            ],
            tags=[
                tag["name"]
                for tag in data.get("tags", [])
                if isinstance(tag, dict) and tag.get("name")
            ],
            custom_fields=self._parse_custom_fields(data.get("custom_fields", [])),
            has_attachments=bool(data.get("num_likes", 0)),
            provider_ref=ProviderReference(
                provider_name="asana",
                external_id=data["gid"],
                external_url=data.get("permalink_url"),
                raw_data=data,
            ),
            created_at=self._parse_asana_datetime(data.get("created_at")) or datetime.utcnow(),
            updated_at=self._parse_asana_datetime(data.get("modified_at")),
        )

    def _parse_custom_fields(self, fields: list[dict[str, Any]]) -> dict[str, object]:
        return {
            field["name"]: field.get("display_value")
            for field in fields
            if isinstance(field, dict) and field.get("name") and field.get("display_value")
        }

    def _parse_asana_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time.min, tzinfo=UTC)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def test_asana_connection(
    credential_manager: CredentialManager,
    account_id: str,
) -> dict[str, Any]:
    """Test Asana connection for an account."""
    provider = AsanaProvider(credential_manager, account_id)
    me = await provider.get_me()
    projects = await provider.list_projects()
    tasks = await provider.get_my_tasks()
    return {
        "status": "success",
        "account": account_id,
        "user": {
            "gid": me.get("gid"),
            "name": me.get("name"),
            "email": me.get("email"),
        },
        "projects": len(projects),
        "open_tasks": len(tasks),
    }
