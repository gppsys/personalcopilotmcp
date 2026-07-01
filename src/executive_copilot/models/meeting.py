"""Meeting domain model."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from executive_copilot.models.base import DomainModel, ProviderReference


class MeetingType(StrEnum):
    """Type of meeting."""

    ONE_ON_ONE = "one_on_one"
    TEAM = "team"
    ALL_HANDS = "all_hands"
    BOARD = "board"
    CLIENT = "client"
    INTERVIEW = "interview"
    REVIEW = "review"
    PLANNING = "planning"
    STANDUP = "standup"
    RETROSPECTIVE = "retrospective"
    TRAINING = "training"
    SOCIAL = "social"
    OTHER = "other"


class ActionItem(DomainModel):
    """An action item from a meeting."""

    description: str = Field(description="Action item description")
    assignee_id: str | None = Field(default=None, description="Assigned person ID")
    assignee_name: str | None = Field(default=None, description="Assigned person name")
    due_date: datetime | None = Field(default=None, description="Due date")
    status: str = Field(default="pending", description="Status (pending, completed)")
    task_id: UUID | None = Field(default=None, description="Linked task ID if created")


class Meeting(DomainModel):
    """A meeting with intelligence data.

    Extends calendar events with meeting-specific metadata,
    notes, action items, and analysis. Represents the "intelligence"
    layer on top of raw calendar data.
    """

    # Calendar link
    event_id: UUID = Field(description="Linked calendar event ID")

    # Basic info
    title: str = Field(description="Meeting title")
    meeting_type: MeetingType = Field(default=MeetingType.OTHER, description="Type of meeting")
    description: str | None = Field(default=None, description="Meeting description")

    # Timing
    scheduled_start: datetime = Field(description="Scheduled start time")
    scheduled_end: datetime = Field(description="Scheduled end time")
    actual_start: datetime | None = Field(default=None, description="Actual start time")
    actual_end: datetime | None = Field(default=None, description="Actual end time")

    # Participants
    organizer_id: str | None = Field(default=None, description="Organizer person ID")
    participant_ids: list[str] = Field(default_factory=list, description="Participant person IDs")
    attendance: dict[str, bool] = Field(
        default_factory=dict,
        description="Attendance tracking by participant ID",
    )

    # Context
    organization_id: UUID | None = Field(default=None, description="Related organization")
    project_id: str | None = Field(default=None, description="Related project")
    previous_meeting_id: UUID | None = Field(default=None, description="Previous meeting in series")
    next_meeting_id: UUID | None = Field(default=None, description="Next meeting in series")

    # Content
    agenda: list[str] = Field(default_factory=list, description="Meeting agenda items")
    notes: str | None = Field(default=None, description="Meeting notes")
    transcript: str | None = Field(default=None, description="Meeting transcript")
    recording_url: str | None = Field(default=None, description="Recording URL")

    # Outcomes
    summary: str | None = Field(default=None, description="AI-generated summary")
    key_decisions: list[str] = Field(default_factory=list, description="Key decisions made")
    action_items: list[ActionItem] = Field(default_factory=list, description="Action items")
    follow_ups: list[str] = Field(default_factory=list, description="Follow-up items")

    # Analysis
    sentiment: str | None = Field(default=None, description="Overall meeting sentiment")
    topics_discussed: list[str] = Field(default_factory=list, description="Topics discussed")
    participants_spoken: dict[str, float] = Field(
        default_factory=dict,
        description="Speaking time by participant (percentage)",
    )

    # Preparation
    prep_notes: str | None = Field(default=None, description="Preparation notes")
    context_gathered: list[str] = Field(
        default_factory=list,
        description="Context sources consulted",
    )

    # Status
    is_completed: bool = Field(default=False, description="Whether meeting has occurred")
    is_cancelled: bool = Field(default=False, description="Whether meeting was cancelled")
    is_recurring: bool = Field(default=False, description="Whether this is part of a series")

    # Provider tracking
    provider_ref: ProviderReference | None = Field(
        default=None,
        description="Reference to meeting data in source provider",
    )

    @property
    def duration_minutes(self) -> int:
        """Get scheduled duration in minutes."""
        delta = self.scheduled_end - self.scheduled_start
        return int(delta.total_seconds() / 60)

    @property
    def actual_duration_minutes(self) -> int | None:
        """Get actual duration in minutes if available."""
        if self.actual_start and self.actual_end:
            delta = self.actual_end - self.actual_start
            return int(delta.total_seconds() / 60)
        return None

    @property
    def attendee_count(self) -> int:
        """Get number of participants."""
        return len(self.participant_ids)

    @property
    def pending_action_items(self) -> list[ActionItem]:
        """Get incomplete action items."""
        return [ai for ai in self.action_items if ai.status != "completed"]

    @property
    def has_notes(self) -> bool:
        """Check if meeting has notes."""
        return self.notes is not None and len(self.notes) > 0

    @property
    def has_transcript(self) -> bool:
        """Check if meeting has a transcript."""
        return self.transcript is not None and len(self.transcript) > 0

    @property
    def is_past(self) -> bool:
        """Check if meeting is in the past."""
        return self.scheduled_end < datetime.utcnow()

    @property
    def needs_follow_up(self) -> bool:
        """Check if meeting needs follow-up action."""
        return len(self.pending_action_items) > 0 or len(self.follow_ups) > 0


class MeetingDecision(DomainModel):
    """A decision recorded from a meeting.

    Standalone decision record that links back to a meeting for context.
    Designed for quick capture and searchability.
    """

    # Meeting reference
    event_id: str = Field(description="Calendar event ID")
    event_title: str = Field(description="Meeting title")
    meeting_date: datetime = Field(description="When the meeting occurred")

    # Decision content
    decision: str = Field(description="What was decided")
    context: str | None = Field(default=None, description="Context or rationale")
    owner: str | None = Field(default=None, description="Who owns this decision")
    owner_email: str | None = Field(default=None, description="Owner email")

    # Participants
    participants: list[str] = Field(default_factory=list, description="Meeting attendee emails")

    # Metadata
    account_id: str | None = Field(default=None, description="Calendar account")
    organization_id: UUID | None = Field(default=None, description="Related organization")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")


class MeetingOutcome(DomainModel):
    """Quick capture of meeting outcomes.

    Designed for post-meeting prompts: "What was decided? What's next?"
    Links decisions and next steps back to the meeting.
    """

    # Meeting reference
    event_id: str = Field(description="Calendar event ID")
    event_title: str = Field(description="Meeting title")
    meeting_date: datetime = Field(description="When the meeting occurred")

    # Participants
    participants: list[str] = Field(default_factory=list, description="Attendee emails")
    organizer: str | None = Field(default=None, description="Meeting organizer email")

    # Outcomes
    summary: str | None = Field(default=None, description="Brief meeting summary")
    decisions: list[str] = Field(default_factory=list, description="Decisions made")
    next_steps: list[str] = Field(default_factory=list, description="Action items/next steps")
    key_points: list[str] = Field(default_factory=list, description="Key discussion points")

    # Context
    account_id: str | None = Field(default=None, description="Calendar account")
    organization_id: UUID | None = Field(default=None, description="Related organization")

    # Metadata
    recorded_at: datetime = Field(description="When outcome was recorded")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    notes: str | None = Field(default=None, description="Additional notes")

    @property
    def decision_count(self) -> int:
        """Number of decisions recorded."""
        return len(self.decisions)

    @property
    def next_step_count(self) -> int:
        """Number of next steps recorded."""
        return len(self.next_steps)

    @property
    def has_outcomes(self) -> bool:
        """Check if any outcomes were recorded."""
        return bool(self.decisions or self.next_steps or self.summary)
