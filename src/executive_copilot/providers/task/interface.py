"""Task provider interface."""

from abc import abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from pydantic import BaseModel, Field

from executive_copilot.models import Task, TaskPriority, TaskStatus
from executive_copilot.providers.base import Provider


class ProjectInfo(BaseModel):
    """Information about a project."""

    id: str = Field(description="Project ID")
    name: str = Field(description="Project name")
    description: str | None = Field(default=None, description="Project description")
    color: str | None = Field(default=None, description="Project color")
    is_archived: bool = Field(default=False, description="Whether project is archived")
    is_template: bool = Field(default=False, description="Whether this is a template")
    owner_id: str | None = Field(default=None, description="Project owner ID")
    team_id: str | None = Field(default=None, description="Team ID")
    workspace_id: str | None = Field(default=None, description="Workspace ID")
    task_count: int = Field(default=0, description="Number of tasks")
    custom_fields: dict[str, object] = Field(
        default_factory=dict,
        description="Custom field definitions",
    )


class SectionInfo(BaseModel):
    """Information about a project section."""

    id: str = Field(description="Section ID")
    name: str = Field(description="Section name")
    project_id: str = Field(description="Parent project ID")
    order: int = Field(default=0, description="Sort order")


class TaskQuery(BaseModel):
    """Query parameters for task search."""

    # Filters
    project_id: str | None = Field(default=None, description="Filter by project")
    section_id: str | None = Field(default=None, description="Filter by section")
    assignee_id: str | None = Field(default=None, description="Filter by assignee")
    creator_id: str | None = Field(default=None, description="Filter by creator")
    status: TaskStatus | None = Field(default=None, description="Filter by status")
    priority: TaskPriority | None = Field(default=None, description="Filter by priority")

    # Date filters
    due_before: datetime | None = Field(default=None, description="Due before date")
    due_after: datetime | None = Field(default=None, description="Due after date")
    modified_since: datetime | None = Field(default=None, description="Modified since")

    # Content
    query: str | None = Field(default=None, description="Text search query")
    tags: list[str] = Field(default_factory=list, description="Required tags")

    # Include options
    include_subtasks: bool = Field(default=False, description="Include subtasks")
    include_completed: bool = Field(default=False, description="Include completed tasks")
    include_archived: bool = Field(default=False, description="Include archived")

    # Pagination
    limit: int = Field(default=50, ge=1, le=100, description="Maximum results")
    offset: int = Field(default=0, ge=0, description="Results offset")


class NewTask(BaseModel):
    """Task to be created."""

    title: str = Field(description="Task title")
    description: str | None = Field(default=None, description="Task description")
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM, description="Priority")

    # Location
    project_id: str | None = Field(default=None, description="Project to add to")
    section_id: str | None = Field(default=None, description="Section to add to")
    parent_task_id: str | None = Field(default=None, description="Parent task (for subtask)")

    # Assignment
    assignee_id: str | None = Field(default=None, description="Assignee user ID")
    assignee_email: str | None = Field(default=None, description="Assignee email")
    followers: list[str] = Field(default_factory=list, description="Follower user IDs")

    # Dates
    due_date: datetime | None = Field(default=None, description="Due date")
    start_date: datetime | None = Field(default=None, description="Start date")

    # Metadata
    tags: list[str] = Field(default_factory=list, description="Tags to apply")
    custom_fields: dict[str, object] = Field(
        default_factory=dict,
        description="Custom field values",
    )

    # Effort
    estimated_hours: float | None = Field(default=None, description="Estimated hours")
    story_points: int | None = Field(default=None, description="Story points")


class TaskProvider(Provider[Task]):
    """Interface for task management providers.

    Implementations must map their specific task formats to the
    Task domain model.
    """

    # --- Project Operations ---

    @abstractmethod
    async def list_projects(
        self,
        include_archived: bool = False,
    ) -> list[ProjectInfo]:
        """List available projects.

        Args:
            include_archived: Include archived projects

        Returns:
            List of project information
        """
        ...

    @abstractmethod
    async def get_project(self, project_id: str) -> ProjectInfo | None:
        """Get project by ID.

        Args:
            project_id: Project ID

        Returns:
            Project info if found
        """
        ...

    @abstractmethod
    async def list_sections(self, project_id: str) -> list[SectionInfo]:
        """List sections in a project.

        Args:
            project_id: Project to list sections for

        Returns:
            List of section information
        """
        ...

    # --- Task Reading ---

    @abstractmethod
    async def get_tasks(self, query: TaskQuery) -> list[Task]:
        """Get tasks matching query.

        Args:
            query: Task query parameters

        Returns:
            List of matching tasks
        """
        ...

    @abstractmethod
    async def get_task(self, task_id: str) -> Task | None:
        """Get a single task by ID.

        Args:
            task_id: Task ID

        Returns:
            Task if found
        """
        ...

    @abstractmethod
    async def get_my_tasks(
        self,
        include_completed: bool = False,
    ) -> list[Task]:
        """Get tasks assigned to the authenticated user.

        Args:
            include_completed: Include completed tasks

        Returns:
            List of assigned tasks
        """
        ...

    @abstractmethod
    async def get_overdue_tasks(self) -> list[Task]:
        """Get all overdue tasks.

        Returns:
            List of overdue tasks
        """
        ...

    @abstractmethod
    async def get_due_today(self) -> list[Task]:
        """Get tasks due today.

        Returns:
            List of tasks due today
        """
        ...

    @abstractmethod
    def stream_tasks(self, query: TaskQuery) -> AsyncIterator[Task]:
        """Stream tasks for large result sets.

        Args:
            query: Task query parameters

        Yields:
            Task objects
        """
        ...

    # --- Task Creation ---

    @abstractmethod
    async def create_task(self, task: NewTask) -> Task:
        """Create a new task.

        Args:
            task: Task to create

        Returns:
            Created task
        """
        ...

    @abstractmethod
    async def create_subtask(
        self,
        parent_task_id: str,
        task: NewTask,
    ) -> Task:
        """Create a subtask.

        Args:
            parent_task_id: Parent task ID
            task: Subtask to create

        Returns:
            Created subtask
        """
        ...

    # --- Task Modification ---

    @abstractmethod
    async def update_task(
        self,
        task_id: str,
        updates: dict[str, object],
    ) -> Task:
        """Update a task.

        Args:
            task_id: Task to update
            updates: Fields to update

        Returns:
            Updated task
        """
        ...

    @abstractmethod
    async def complete_task(self, task_id: str) -> Task:
        """Mark a task as completed.

        Args:
            task_id: Task to complete

        Returns:
            Updated task
        """
        ...

    @abstractmethod
    async def reopen_task(self, task_id: str) -> Task:
        """Reopen a completed task.

        Args:
            task_id: Task to reopen

        Returns:
            Updated task
        """
        ...

    @abstractmethod
    async def delete_task(self, task_id: str) -> None:
        """Delete a task.

        Args:
            task_id: Task to delete
        """
        ...

    @abstractmethod
    async def move_task(
        self,
        task_id: str,
        project_id: str | None = None,
        section_id: str | None = None,
    ) -> Task:
        """Move task to a different project or section.

        Args:
            task_id: Task to move
            project_id: Target project (optional)
            section_id: Target section (optional)

        Returns:
            Updated task
        """
        ...

    # --- Assignment ---

    @abstractmethod
    async def assign_task(
        self,
        task_id: str,
        assignee_id: str | None = None,
        assignee_email: str | None = None,
    ) -> Task:
        """Assign a task to a user.

        Args:
            task_id: Task to assign
            assignee_id: User ID to assign to
            assignee_email: Email to assign to (alternative)

        Returns:
            Updated task
        """
        ...

    @abstractmethod
    async def add_follower(self, task_id: str, user_id: str) -> Task:
        """Add a follower to a task.

        Args:
            task_id: Task to modify
            user_id: User to add as follower

        Returns:
            Updated task
        """
        ...

    @abstractmethod
    async def remove_follower(self, task_id: str, user_id: str) -> Task:
        """Remove a follower from a task.

        Args:
            task_id: Task to modify
            user_id: User to remove

        Returns:
            Updated task
        """
        ...

    # --- Comments ---

    @abstractmethod
    async def add_comment(
        self,
        task_id: str,
        text: str,
    ) -> dict[str, object]:
        """Add a comment to a task.

        Args:
            task_id: Task to comment on
            text: Comment text

        Returns:
            Created comment data
        """
        ...

    @abstractmethod
    async def get_comments(
        self,
        task_id: str,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        """Get comments on a task.

        Args:
            task_id: Task to get comments for
            limit: Maximum comments to return

        Returns:
            List of comment data
        """
        ...

    # --- Tags ---

    @abstractmethod
    async def add_tag(self, task_id: str, tag: str) -> Task:
        """Add a tag to a task.

        Args:
            task_id: Task to tag
            tag: Tag to add

        Returns:
            Updated task
        """
        ...

    @abstractmethod
    async def remove_tag(self, task_id: str, tag: str) -> Task:
        """Remove a tag from a task.

        Args:
            task_id: Task to modify
            tag: Tag to remove

        Returns:
            Updated task
        """
        ...

    # --- Sync ---

    @abstractmethod
    async def get_changes_since(
        self,
        sync_token: str | None = None,
        project_id: str | None = None,
    ) -> tuple[list[Task], str]:
        """Get tasks changed since last sync.

        Args:
            sync_token: Token from previous sync
            project_id: Project to sync (None = all)

        Returns:
            Tuple of (changed tasks, new sync token)
        """
        ...
