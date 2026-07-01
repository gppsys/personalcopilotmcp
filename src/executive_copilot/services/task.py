"""Task service for orchestrating task operations."""

from datetime import datetime

from executive_copilot.models import Task, TaskStatus
from executive_copilot.providers.task import NewTask, TaskProvider, TaskQuery
from executive_copilot.services.base import Service


class TaskService(Service):
    """Service for task operations.

    Orchestrates task providers and handles task-related business logic.
    """

    async def get_my_tasks(
        self,
        include_completed: bool = False,
        provider_name: str | None = None,
    ) -> list[Task]:
        """Get tasks assigned to the current user.

        Args:
            include_completed: Include completed tasks
            provider_name: Specific provider to query

        Returns:
            User's tasks
        """
        tasks: list[Task] = []
        providers = self._get_task_providers(provider_name)

        for provider in providers:
            try:
                provider_tasks = await provider.get_my_tasks(
                    include_completed=include_completed
                )
                tasks.extend(provider_tasks)
            except Exception as e:
                self._logger.error(
                    "Failed to get my tasks",
                    provider=provider.name,
                    error=str(e),
                )

        # Sort by due date, with None at end
        tasks.sort(key=lambda t: (t.due_date is None, t.due_date or datetime.max))
        return tasks

    async def get_overdue_tasks(
        self,
        provider_name: str | None = None,
    ) -> list[Task]:
        """Get all overdue tasks.

        Args:
            provider_name: Specific provider to query

        Returns:
            Overdue tasks sorted by due date
        """
        tasks: list[Task] = []
        providers = self._get_task_providers(provider_name)

        for provider in providers:
            try:
                provider_tasks = await provider.get_overdue_tasks()
                tasks.extend(provider_tasks)
            except Exception as e:
                self._logger.error(
                    "Failed to get overdue tasks",
                    provider=provider.name,
                    error=str(e),
                )

        tasks.sort(key=lambda t: t.due_date or datetime.min)
        return tasks

    async def get_due_today(
        self,
        provider_name: str | None = None,
    ) -> list[Task]:
        """Get tasks due today.

        Args:
            provider_name: Specific provider to query

        Returns:
            Tasks due today
        """
        tasks: list[Task] = []
        providers = self._get_task_providers(provider_name)

        for provider in providers:
            try:
                provider_tasks = await provider.get_due_today()
                tasks.extend(provider_tasks)
            except Exception as e:
                self._logger.error(
                    "Failed to get tasks due today",
                    provider=provider.name,
                    error=str(e),
                )

        return tasks

    async def search_tasks(
        self,
        query: str | None = None,
        *,
        project_id: str | None = None,
        status: TaskStatus | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
        limit: int = 50,
        provider_name: str | None = None,
    ) -> list[Task]:
        """Search tasks across providers.

        Args:
            query: Text search query
            project_id: Filter by project
            status: Filter by status
            due_before: Due before date
            due_after: Due after date
            limit: Maximum results
            provider_name: Specific provider to search

        Returns:
            Matching tasks
        """
        task_query = TaskQuery(
            query=query,
            project_id=project_id,
            status=status,
            due_before=due_before,
            due_after=due_after,
            limit=limit,
        )

        tasks: list[Task] = []
        providers = self._get_task_providers(provider_name)

        for provider in providers:
            try:
                provider_tasks = await provider.get_tasks(task_query)
                tasks.extend(provider_tasks)
            except Exception as e:
                self._logger.error(
                    "Task search failed",
                    provider=provider.name,
                    error=str(e),
                )

        return tasks[:limit]

    async def get_task(
        self,
        task_id: str,
        provider_name: str | None = None,
    ) -> Task | None:
        """Get a single task.

        Args:
            task_id: Task ID
            provider_name: Provider to query

        Returns:
            Task if found
        """
        providers = self._get_task_providers(provider_name)

        for provider in providers:
            try:
                task = await provider.get_task(task_id)
                if task:
                    return task
            except Exception as e:
                self._logger.error(
                    "Failed to get task",
                    provider=provider.name,
                    task_id=task_id,
                    error=str(e),
                )

        return None

    async def create_task(
        self,
        title: str,
        *,
        description: str | None = None,
        project_id: str | None = None,
        due_date: datetime | None = None,
        assignee_email: str | None = None,
        tags: list[str] | None = None,
        provider_name: str | None = None,
    ) -> Task | None:
        """Create a new task.

        Args:
            title: Task title
            description: Task description
            project_id: Project to add to
            due_date: Due date
            assignee_email: Assignee email
            tags: Tags to apply
            provider_name: Provider to create in

        Returns:
            Created task
        """
        new_task = NewTask(
            title=title,
            description=description,
            project_id=project_id,
            due_date=due_date,
            assignee_email=assignee_email,
            tags=tags or [],
        )

        providers = self._get_task_providers(provider_name)
        if not providers:
            self._logger.error("No task providers available")
            return None

        try:
            return await providers[0].create_task(new_task)
        except Exception as e:
            self._logger.error(
                "Failed to create task",
                provider=providers[0].name,
                error=str(e),
            )
            return None

    async def complete_task(
        self,
        task_id: str,
        provider_name: str | None = None,
    ) -> Task | None:
        """Mark a task as complete.

        Args:
            task_id: Task to complete
            provider_name: Provider containing task

        Returns:
            Updated task
        """
        providers = self._get_task_providers(provider_name)

        for provider in providers:
            try:
                return await provider.complete_task(task_id)
            except Exception as e:
                self._logger.error(
                    "Failed to complete task",
                    provider=provider.name,
                    task_id=task_id,
                    error=str(e),
                )

        return None

    async def get_task_summary(self) -> dict[str, object]:
        """Get summary of task status.

        Returns:
            Summary with counts by status
        """
        my_tasks = await self.get_my_tasks(include_completed=False)
        overdue = await self.get_overdue_tasks()
        due_today = await self.get_due_today()

        # Count by status
        status_counts: dict[str, int] = {}
        for task in my_tasks:
            status = task.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

        return {
            "total_open": len(my_tasks),
            "overdue": len(overdue),
            "due_today": len(due_today),
            "by_status": status_counts,
            "highest_priority_overdue": overdue[0].title if overdue else None,
        }

    def _get_task_providers(
        self,
        provider_name: str | None = None,
    ) -> list[TaskProvider]:
        """Get task providers to use.

        Args:
            provider_name: Specific provider name, or None for all

        Returns:
            List of task providers
        """
        providers: list[TaskProvider] = []
        configs = self.container.config.get_enabled_providers("task")

        for config in configs:
            if provider_name and config.provider_name != provider_name:
                continue
            try:
                provider = self.container.provider_registry.get_or_create(config)
                if isinstance(provider, TaskProvider):
                    providers.append(provider)
            except Exception as e:
                self._logger.error(
                    "Failed to get provider",
                    provider=config.provider_name,
                    error=str(e),
                )

        return providers
