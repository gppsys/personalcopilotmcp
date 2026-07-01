"""Communication-related domain models: summaries, reminders, and follow-ups."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from executive_copilot.models.base import DomainModel


class FollowUpStatus(StrEnum):
    """Status of a follow-up item."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SNOOZED = "snoozed"


class FollowUpPriority(StrEnum):
    """Priority of a follow-up item."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ReminderStatus(StrEnum):
    """Status of a reminder."""

    PENDING = "pending"
    TRIGGERED = "triggered"
    DISMISSED = "dismissed"
    SNOOZED = "snoozed"


class CommitmentDirection(StrEnum):
    """Direction of a commitment."""

    I_PROMISED = "i_promised"  # I committed to do something for someone
    THEY_PROMISED = "they_promised"  # Someone committed to do something for me


class CommitmentStatus(StrEnum):
    """Status of a commitment."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    OVERDUE = "overdue"


class ConversationSummary(DomainModel):
    """Summary of a conversation or interaction.

    Stores AI-generated summaries of email threads, meetings,
    or other interactions for quick reference and context.
    """

    # Source reference
    source_type: str = Field(description="Type of source (thread, meeting, chat)")
    source_id: UUID = Field(description="ID of the source entity")

    # Content
    title: str = Field(description="Summary title")
    summary: str = Field(description="Summary text")
    key_points: list[str] = Field(default_factory=list, description="Key points extracted")
    decisions: list[str] = Field(default_factory=list, description="Decisions made")
    action_items: list[str] = Field(default_factory=list, description="Action items identified")
    questions: list[str] = Field(default_factory=list, description="Open questions")

    # Participants
    participant_ids: list[str] = Field(default_factory=list, description="Participant person IDs")

    # Context
    organization_id: UUID | None = Field(default=None, description="Related organization")
    topics: list[str] = Field(default_factory=list, description="Topics discussed")
    sentiment: str | None = Field(default=None, description="Overall sentiment")

    # Timing
    conversation_start: datetime = Field(description="When conversation started")
    conversation_end: datetime | None = Field(default=None, description="When conversation ended")
    summarized_at: datetime = Field(description="When summary was generated")

    # Metadata
    message_count: int = Field(default=0, ge=0, description="Number of messages")
    model_used: str | None = Field(default=None, description="AI model used for summary")
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence in summary accuracy",
    )


class FollowUp(DomainModel):
    """A follow-up item that needs attention.

    Tracks items that require future action, either detected
    automatically from conversations or created manually.
    """

    # Description
    title: str = Field(description="Follow-up title")
    description: str | None = Field(default=None, description="Detailed description")
    context: str | None = Field(default=None, description="Context from source")

    # Status
    status: FollowUpStatus = Field(default=FollowUpStatus.PENDING, description="Current status")
    priority: FollowUpPriority = Field(default=FollowUpPriority.MEDIUM, description="Priority level")

    # Timing
    due_date: datetime | None = Field(default=None, description="When follow-up is due")
    snoozed_until: datetime | None = Field(default=None, description="Snoozed until this time")
    completed_at: datetime | None = Field(default=None, description="When completed")

    # Source
    source_type: str | None = Field(default=None, description="Type of source (email, meeting)")
    source_id: UUID | None = Field(default=None, description="ID of source entity")
    detected_from: str | None = Field(
        default=None,
        description="Text that triggered follow-up detection",
    )

    # People
    with_person_id: str | None = Field(default=None, description="Person to follow up with")
    with_person_name: str | None = Field(default=None, description="Person name")
    with_person_email: str | None = Field(default=None, description="Person email")

    # Organization
    organization_id: UUID | None = Field(default=None, description="Related organization")

    # Task link
    task_id: UUID | None = Field(default=None, description="Linked task if created")

    # Metadata
    auto_detected: bool = Field(default=False, description="Whether auto-detected")
    detection_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence of auto-detection",
    )
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    notes: str | None = Field(default=None, description="Additional notes")

    @property
    def is_overdue(self) -> bool:
        """Check if follow-up is overdue."""
        if self.due_date is None or self.status == FollowUpStatus.COMPLETED:
            return False
        return datetime.utcnow() > self.due_date

    @property
    def is_snoozed(self) -> bool:
        """Check if follow-up is currently snoozed."""
        if self.snoozed_until is None:
            return False
        return datetime.utcnow() < self.snoozed_until

    def mark_completed(self) -> "FollowUp":
        """Create a copy marked as completed."""
        return self.with_update(
            status=FollowUpStatus.COMPLETED,
            completed_at=datetime.utcnow(),
        )

    def snooze_until(self, until: datetime) -> "FollowUp":
        """Create a copy snoozed until specified time."""
        return self.with_update(
            status=FollowUpStatus.SNOOZED,
            snoozed_until=until,
        )


class Reminder(DomainModel):
    """A reminder for the executive.

    Time-based notifications that can be linked to other
    entities or standalone.
    """

    # Content
    title: str = Field(description="Reminder title")
    message: str | None = Field(default=None, description="Reminder message")

    # Timing
    remind_at: datetime = Field(description="When to trigger reminder")
    status: ReminderStatus = Field(default=ReminderStatus.PENDING, description="Reminder status")
    triggered_at: datetime | None = Field(default=None, description="When reminder was triggered")
    snoozed_until: datetime | None = Field(default=None, description="Snoozed until this time")

    # Recurrence
    is_recurring: bool = Field(default=False, description="Whether reminder recurs")
    recurrence_rule: str | None = Field(default=None, description="iCal recurrence rule")
    next_occurrence: datetime | None = Field(default=None, description="Next occurrence time")

    # Links
    linked_type: str | None = Field(default=None, description="Type of linked entity")
    linked_id: UUID | None = Field(default=None, description="ID of linked entity")

    # Notification
    notify_via: list[str] = Field(
        default_factory=lambda: ["mcp"],
        description="Notification channels",
    )
    notification_sent: bool = Field(default=False, description="Whether notification was sent")

    # Metadata
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    priority: str = Field(default="normal", description="Priority level")

    @property
    def is_due(self) -> bool:
        """Check if reminder is due."""
        if self.status != ReminderStatus.PENDING:
            return False
        return datetime.utcnow() >= self.remind_at

    @property
    def is_snoozed(self) -> bool:
        """Check if reminder is currently snoozed."""
        if self.snoozed_until is None:
            return False
        return datetime.utcnow() < self.snoozed_until

    def trigger(self) -> "Reminder":
        """Create a copy marked as triggered."""
        return self.with_update(
            status=ReminderStatus.TRIGGERED,
            triggered_at=datetime.utcnow(),
        )

    def dismiss(self) -> "Reminder":
        """Create a copy marked as dismissed."""
        return self.with_update(status=ReminderStatus.DISMISSED)

    def snooze_until(self, until: datetime) -> "Reminder":
        """Create a copy snoozed until specified time."""
        return self.with_update(
            status=ReminderStatus.SNOOZED,
            snoozed_until=until,
        )


class Commitment(DomainModel):
    """A commitment made by or to the user.

    Tracks promises in both directions:
    - I_PROMISED: Something I said I would do for someone else
    - THEY_PROMISED: Something someone else said they would do for me
    """

    # What was promised
    title: str = Field(description="Brief description of the commitment")
    description: str | None = Field(default=None, description="Detailed description")
    original_text: str | None = Field(default=None, description="Exact quote from source")

    # Direction and parties
    direction: CommitmentDirection = Field(description="Who made the commitment")
    promiser_name: str | None = Field(default=None, description="Who made the promise")
    promiser_email: str | None = Field(default=None, description="Promiser's email")
    promisee_name: str | None = Field(default=None, description="Who the promise was made to")
    promisee_email: str | None = Field(default=None, description="Promisee's email")

    # Status and timing
    status: CommitmentStatus = Field(default=CommitmentStatus.PENDING, description="Current status")
    due_date: datetime | None = Field(default=None, description="When the commitment is due")
    completed_at: datetime | None = Field(default=None, description="When marked complete")

    # Source tracking
    source_type: str | None = Field(default=None, description="Source type: email, transcript")
    source_id: UUID | None = Field(default=None, description="ID of source entity")
    source_date: datetime | None = Field(default=None, description="When the commitment was made")

    # Organization context
    organization_id: UUID | None = Field(default=None, description="Related organization")

    # Detection metadata
    auto_detected: bool = Field(default=False, description="Whether auto-detected by AI")
    detection_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence of detection",
    )
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    notes: str | None = Field(default=None, description="Additional notes")

    @property
    def is_overdue(self) -> bool:
        """Check if commitment is overdue."""
        if self.due_date is None or self.status == CommitmentStatus.COMPLETED:
            return False
        return datetime.utcnow() > self.due_date

    @property
    def days_until_due(self) -> int | None:
        """Days until due date (negative if overdue)."""
        if self.due_date is None:
            return None
        delta = self.due_date - datetime.utcnow()
        return delta.days

    def mark_completed(self) -> "Commitment":
        """Create a copy marked as completed."""
        return self.with_update(
            status=CommitmentStatus.COMPLETED,
            completed_at=datetime.utcnow(),
        )

    def mark_cancelled(self) -> "Commitment":
        """Create a copy marked as cancelled."""
        return self.with_update(status=CommitmentStatus.CANCELLED)
