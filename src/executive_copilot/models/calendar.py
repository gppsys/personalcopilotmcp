"""Calendar and event domain models."""

from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from executive_copilot.models.base import DomainModel, ProviderReference, ValueObject


class EventStatus(StrEnum):
    """Status of a calendar event."""

    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class AttendeeResponse(StrEnum):
    """Response status of an attendee."""

    NEEDS_ACTION = "needs_action"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TENTATIVE = "tentative"


class RecurrenceFrequency(StrEnum):
    """Frequency of recurring events."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class Attendee(ValueObject):
    """An attendee of a calendar event."""

    email: str = Field(description="Attendee email address")
    display_name: str | None = Field(default=None, description="Display name")
    response_status: AttendeeResponse = Field(
        default=AttendeeResponse.NEEDS_ACTION,
        description="Response to the invitation",
    )
    is_optional: bool = Field(default=False, description="Whether attendance is optional")
    is_organizer: bool = Field(default=False, description="Whether this attendee is the organizer")
    is_self: bool = Field(default=False, description="Whether this is the executive's account")
    comment: str | None = Field(default=None, description="Response comment")

    @property
    def has_responded(self) -> bool:
        """Check if attendee has responded."""
        return self.response_status != AttendeeResponse.NEEDS_ACTION

    @property
    def is_attending(self) -> bool:
        """Check if attendee is attending (accepted or tentative)."""
        return self.response_status in (AttendeeResponse.ACCEPTED, AttendeeResponse.TENTATIVE)


class EventRecurrence(ValueObject):
    """Recurrence rule for repeating events."""

    frequency: RecurrenceFrequency = Field(description="How often the event repeats")
    interval: int = Field(default=1, ge=1, description="Interval between occurrences")
    count: int | None = Field(default=None, ge=1, description="Number of occurrences")
    until: datetime | None = Field(default=None, description="End date for recurrence")
    by_day: list[str] = Field(
        default_factory=list,
        description="Days of week (MO, TU, WE, TH, FR, SA, SU)",
    )
    by_month_day: list[int] = Field(
        default_factory=list,
        description="Days of month (1-31)",
    )
    by_month: list[int] = Field(
        default_factory=list,
        description="Months (1-12)",
    )

    @model_validator(mode="after")
    def validate_end_condition(self) -> "EventRecurrence":
        """Validate that only one end condition is specified."""
        if self.count is not None and self.until is not None:
            raise ValueError("Cannot specify both count and until for recurrence")
        return self


class EventAttachment(ValueObject):
    """An attachment on a calendar event."""

    file_id: str = Field(description="File ID in the provider")
    title: str = Field(description="Attachment title/filename")
    mime_type: str | None = Field(default=None, description="MIME type")
    file_url: str | None = Field(default=None, description="URL to access the file")
    icon_link: str | None = Field(default=None, description="URL to icon")

    @property
    def is_transcript(self) -> bool:
        """Check if this looks like a meeting transcript."""
        title_lower = self.title.lower()
        # Gemini attaches "Notes by Gemini" / "Notas de Gemini" docs; Meet adds
        # "Transcript" / "Transcripción" docs. Spanish workspaces were missed.
        keywords = ["transcript", "transcripci", "recording", "notes", "notas", "gemini"]
        return any(kw in title_lower for kw in keywords)

    @property
    def is_document(self) -> bool:
        """Check if this is a document (Google Doc, PDF, etc.)."""
        if self.mime_type:
            return self.mime_type in [
                "application/vnd.google-apps.document",
                "application/pdf",
                "text/plain",
            ]
        return False


class EventLocation(ValueObject):
    """Location of a calendar event."""

    display_name: str = Field(description="Location display name")
    address: str | None = Field(default=None, description="Physical address")
    latitude: float | None = Field(default=None, description="Latitude coordinate")
    longitude: float | None = Field(default=None, description="Longitude coordinate")
    conference_url: str | None = Field(default=None, description="Video conference URL")
    conference_type: str | None = Field(
        default=None,
        description="Conference type (zoom, teams, meet, etc.)",
    )
    phone_number: str | None = Field(default=None, description="Dial-in phone number")
    pin: str | None = Field(default=None, description="Conference PIN")


class CalendarEvent(DomainModel):
    """A calendar event.

    Represents appointments, meetings, and other scheduled items
    from any calendar provider (Outlook, Google Calendar, etc.).
    """

    # Basic info
    title: str = Field(description="Event title")
    description: str | None = Field(default=None, description="Event description")
    status: EventStatus = Field(default=EventStatus.CONFIRMED, description="Event status")

    # Timing
    start_time: datetime = Field(description="Event start time")
    end_time: datetime = Field(description="Event end time")
    is_all_day: bool = Field(default=False, description="Whether this is an all-day event")
    timezone: str = Field(default="UTC", description="Timezone for the event")

    # Recurrence
    is_recurring: bool = Field(default=False, description="Whether event is recurring")
    recurrence: EventRecurrence | None = Field(default=None, description="Recurrence rule")
    recurring_event_id: UUID | None = Field(
        default=None,
        description="ID of the master recurring event",
    )
    is_exception: bool = Field(
        default=False,
        description="Whether this is a modified instance of a recurring event",
    )

    # Location
    location: EventLocation | None = Field(default=None, description="Event location")

    # Participants
    organizer: Attendee | None = Field(default=None, description="Event organizer")
    attendees: list[Attendee] = Field(default_factory=list, description="Event attendees")

    # Calendar info
    calendar_id: str | None = Field(default=None, description="ID of the containing calendar")
    calendar_name: str | None = Field(default=None, description="Name of the containing calendar")

    # Metadata
    visibility: str = Field(default="default", description="Event visibility")
    busy_status: str = Field(default="busy", description="Show as busy/free/tentative")
    reminders: list[int] = Field(
        default_factory=list,
        description="Reminder times in minutes before event",
    )
    color: str | None = Field(default=None, description="Event color")

    # Flags
    is_private: bool = Field(default=False, description="Whether event is private")
    is_cancelled: bool = Field(default=False, description="Whether event is cancelled")
    can_edit: bool = Field(default=True, description="Whether user can edit this event")
    can_respond: bool = Field(default=True, description="Whether user can respond to invitation")

    # Attachments
    attachments: list[EventAttachment] = Field(
        default_factory=list,
        description="Files attached to the event (transcripts, notes, etc.)",
    )

    # Related entities
    meeting_id: UUID | None = Field(default=None, description="Related meeting entity ID")
    task_ids: list[UUID] = Field(default_factory=list, description="Related task IDs")

    # Provider tracking
    provider_ref: ProviderReference | None = Field(
        default=None,
        description="Reference to event in source provider",
    )

    @property
    def duration(self) -> timedelta:
        """Get event duration."""
        return self.end_time - self.start_time

    @property
    def duration_minutes(self) -> int:
        """Get event duration in minutes."""
        return int(self.duration.total_seconds() / 60)

    @property
    def is_past(self) -> bool:
        """Check if event is in the past."""
        return self.end_time < datetime.utcnow()

    @property
    def is_happening_now(self) -> bool:
        """Check if event is currently happening."""
        now = datetime.utcnow()
        return self.start_time <= now <= self.end_time

    @property
    def required_attendees(self) -> list[Attendee]:
        """Get required attendees."""
        return [a for a in self.attendees if not a.is_optional]

    @property
    def optional_attendees(self) -> list[Attendee]:
        """Get optional attendees."""
        return [a for a in self.attendees if a.is_optional]

    @property
    def accepted_count(self) -> int:
        """Count of attendees who accepted."""
        return sum(1 for a in self.attendees if a.response_status == AttendeeResponse.ACCEPTED)

    @property
    def declined_count(self) -> int:
        """Count of attendees who declined."""
        return sum(1 for a in self.attendees if a.response_status == AttendeeResponse.DECLINED)

    @property
    def pending_count(self) -> int:
        """Count of attendees who haven't responded."""
        return sum(1 for a in self.attendees if a.response_status == AttendeeResponse.NEEDS_ACTION)

    @property
    def has_video_conference(self) -> bool:
        """Check if event has a video conference link."""
        return self.location is not None and self.location.conference_url is not None

    @property
    def conference_url(self) -> str | None:
        """Get the video conference URL if available."""
        if self.location:
            return self.location.conference_url
        return None

    @property
    def has_transcript(self) -> bool:
        """Check if event has a transcript attachment."""
        return any(a.is_transcript for a in self.attachments)

    @property
    def transcript_attachments(self) -> list[EventAttachment]:
        """Get transcript attachments."""
        return [a for a in self.attachments if a.is_transcript]
