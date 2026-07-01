"""Delegation tracking models."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from executive_copilot.models.base import DomainModel, ValueObject


class DelegationStatus(StrEnum):
    """Status of a delegated task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    OVERDUE = "overdue"


class DelegationPriority(StrEnum):
    """Priority of a delegation."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Delegation(DomainModel):
    """A task or request delegated to someone else.

    Tracks what you've asked others to do so you can follow up.
    """

    # What was delegated
    title: str = Field(description="Brief description of the task")
    description: str | None = Field(default=None, description="Detailed description")
    original_text: str | None = Field(default=None, description="Original request text")

    # Who it's assigned to
    assignee_name: str | None = Field(default=None, description="Name of person assigned")
    assignee_email: str = Field(description="Email of person assigned")

    # Status and timing
    status: DelegationStatus = Field(
        default=DelegationStatus.PENDING, description="Current status"
    )
    priority: DelegationPriority = Field(
        default=DelegationPriority.MEDIUM, description="Priority level"
    )
    delegated_at: datetime = Field(description="When the task was delegated")
    due_date: datetime | None = Field(default=None, description="When it's due")
    completed_at: datetime | None = Field(default=None, description="When completed")

    # Source tracking
    source_type: str | None = Field(default=None, description="Source: email, meeting, etc.")
    source_id: UUID | None = Field(default=None, description="ID of source entity")

    # Context
    account_id: str | None = Field(default=None, description="Account used to delegate")
    organization_id: UUID | None = Field(default=None, description="Related organization")

    # Detection metadata
    auto_detected: bool = Field(default=False, description="Whether auto-detected")
    detection_confidence: float | None = Field(default=None, description="Detection confidence")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    notes: str | None = Field(default=None, description="Additional notes")

    @property
    def is_overdue(self) -> bool:
        """Check if delegation is overdue."""
        if self.due_date is None or self.status == DelegationStatus.COMPLETED:
            return False
        due = self.due_date.replace(tzinfo=None) if self.due_date.tzinfo else self.due_date
        return datetime.utcnow() > due

    @property
    def days_waiting(self) -> int:
        """Days since delegation."""
        tz = self.delegated_at.tzinfo
        delegated = self.delegated_at.replace(tzinfo=None) if tz else self.delegated_at
        delta = datetime.utcnow() - delegated
        return delta.days

    @property
    def days_until_due(self) -> int | None:
        """Days until due date (negative if overdue)."""
        if self.due_date is None:
            return None
        due = self.due_date.replace(tzinfo=None) if self.due_date.tzinfo else self.due_date
        delta = due - datetime.utcnow()
        return delta.days

    def mark_completed(self) -> "Delegation":
        """Create a copy marked as completed."""
        return self.with_update(
            status=DelegationStatus.COMPLETED,
            completed_at=datetime.utcnow(),
        )

    def mark_in_progress(self) -> "Delegation":
        """Create a copy marked as in progress."""
        return self.with_update(status=DelegationStatus.IN_PROGRESS)


class DelegationSummary(ValueObject):
    """Summary of delegations to a person."""

    assignee_email: str
    assignee_name: str | None = None
    pending_count: int = 0
    in_progress_count: int = 0
    overdue_count: int = 0
    oldest_delegation_days: int | None = None

    @property
    def total_waiting(self) -> int:
        """Total items waiting on this person."""
        return self.pending_count + self.in_progress_count
