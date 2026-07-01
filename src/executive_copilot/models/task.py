"""Task domain model."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from executive_copilot.models.base import DomainModel, ProviderReference


class TaskStatus(StrEnum):
    """Status of a task."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    """Priority level of a task."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskDependency(DomainModel):
    """A dependency relationship between tasks."""

    task_id: UUID = Field(description="ID of the task with the dependency")
    depends_on_id: UUID = Field(description="ID of the task this depends on")
    dependency_type: str = Field(
        default="finish_to_start",
        description="Type of dependency (finish_to_start, start_to_start, etc.)",
    )


class Task(DomainModel):
    """A task or action item.

    Represents tasks from any provider (Asana, Todoist, etc.).
    The provider layer maps provider-specific task formats into
    this canonical representation.
    """

    # Basic info
    title: str = Field(description="Task title")
    description: str | None = Field(default=None, description="Task description")
    status: TaskStatus = Field(default=TaskStatus.NOT_STARTED, description="Task status")
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM, description="Task priority")

    # Timing
    due_date: datetime | None = Field(default=None, description="Due date")
    start_date: datetime | None = Field(default=None, description="Start date")
    completed_at: datetime | None = Field(default=None, description="Completion timestamp")

    # Hierarchy
    project_id: str | None = Field(default=None, description="Project this task belongs to")
    project_name: str | None = Field(default=None, description="Project name")
    section_id: str | None = Field(default=None, description="Section within project")
    section_name: str | None = Field(default=None, description="Section name")
    parent_task_id: UUID | None = Field(default=None, description="Parent task for subtasks")
    subtask_ids: list[UUID] = Field(default_factory=list, description="Child subtask IDs")

    # Assignment
    assignee_id: str | None = Field(default=None, description="Assigned user ID")
    assignee_email: str | None = Field(default=None, description="Assigned user email")
    assignee_name: str | None = Field(default=None, description="Assigned user name")
    creator_id: str | None = Field(default=None, description="Creator user ID")
    followers: list[str] = Field(default_factory=list, description="Follower user IDs")

    # Organization
    tags: list[str] = Field(default_factory=list, description="Task tags")
    custom_fields: dict[str, object] = Field(
        default_factory=dict,
        description="Custom fields from provider",
    )

    # Effort tracking
    estimated_hours: float | None = Field(default=None, ge=0, description="Estimated hours")
    actual_hours: float | None = Field(default=None, ge=0, description="Actual hours spent")
    story_points: int | None = Field(default=None, ge=0, description="Story points")

    # Dependencies
    blocked_by: list[UUID] = Field(default_factory=list, description="Tasks blocking this one")
    blocks: list[UUID] = Field(default_factory=list, description="Tasks blocked by this one")

    # Related entities
    organization_id: UUID | None = Field(default=None, description="Related organization")
    email_ids: list[UUID] = Field(default_factory=list, description="Related email IDs")
    event_ids: list[UUID] = Field(default_factory=list, description="Related event IDs")

    # Attachments
    attachment_ids: list[UUID] = Field(default_factory=list, description="Attachment IDs")
    has_attachments: bool = Field(default=False, description="Whether task has attachments")

    # Comments
    comment_count: int = Field(default=0, ge=0, description="Number of comments")
    last_comment_at: datetime | None = Field(default=None, description="Last comment timestamp")

    # Provider tracking
    provider_ref: ProviderReference | None = Field(
        default=None,
        description="Reference to task in source provider",
    )

    @property
    def is_completed(self) -> bool:
        """Check if task is completed."""
        return self.status == TaskStatus.COMPLETED

    @property
    def is_blocked(self) -> bool:
        """Check if task is blocked."""
        return self.status == TaskStatus.BLOCKED or len(self.blocked_by) > 0

    @property
    def is_overdue(self) -> bool:
        """Check if task is overdue."""
        if self.due_date is None or self.is_completed:
            return False
        return datetime.utcnow() > self.due_date

    @property
    def has_subtasks(self) -> bool:
        """Check if task has subtasks."""
        return len(self.subtask_ids) > 0

    @property
    def is_subtask(self) -> bool:
        """Check if this is a subtask."""
        return self.parent_task_id is not None

    @property
    def is_assigned(self) -> bool:
        """Check if task is assigned to someone."""
        return self.assignee_id is not None or self.assignee_email is not None

    @property
    def days_until_due(self) -> int | None:
        """Get days until due date (negative if overdue)."""
        if self.due_date is None:
            return None
        delta = self.due_date - datetime.utcnow()
        return delta.days

    def mark_completed(self) -> "Task":
        """Create a new task marked as completed."""
        return self.with_update(
            status=TaskStatus.COMPLETED,
            completed_at=datetime.utcnow(),
        )

    def mark_in_progress(self) -> "Task":
        """Create a new task marked as in progress."""
        return self.with_update(status=TaskStatus.IN_PROGRESS)
