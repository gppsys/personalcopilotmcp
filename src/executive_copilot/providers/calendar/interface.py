"""Calendar provider interface."""

from abc import abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from pydantic import BaseModel, Field

from executive_copilot.models import (
    Attendee,
    AttendeeResponse,
    CalendarEvent,
    EventLocation,
    EventRecurrence,
)
from executive_copilot.providers.base import Provider


class CalendarInfo(BaseModel):
    """Information about a calendar."""

    id: str = Field(description="Calendar ID")
    name: str = Field(description="Calendar name")
    description: str | None = Field(default=None, description="Calendar description")
    color: str | None = Field(default=None, description="Calendar color")
    timezone: str = Field(default="UTC", description="Calendar timezone")
    is_primary: bool = Field(default=False, description="Whether this is the primary calendar")
    is_owner: bool = Field(default=True, description="Whether user owns this calendar")
    can_edit: bool = Field(default=True, description="Whether user can edit events")
    is_selected: bool = Field(default=True, description="Whether calendar is selected/visible")


class EventQuery(BaseModel):
    """Query parameters for event search."""

    # Date range
    start_time: datetime = Field(description="Events starting after this time")
    end_time: datetime = Field(description="Events ending before this time")

    # Filters
    calendar_ids: list[str] = Field(
        default_factory=list,
        description="Calendars to search (empty = all)",
    )
    query: str | None = Field(default=None, description="Text search query")
    show_deleted: bool = Field(default=False, description="Include cancelled events")
    single_events: bool = Field(
        default=True,
        description="Expand recurring events into instances",
    )

    # Pagination
    limit: int = Field(default=100, ge=1, le=2500, description="Maximum results")
    page_token: str | None = Field(default=None, description="Pagination token")


class NewEvent(BaseModel):
    """Event to be created."""

    title: str = Field(description="Event title")
    description: str | None = Field(default=None, description="Event description")

    # Timing
    start_time: datetime = Field(description="Start time")
    end_time: datetime = Field(description="End time")
    is_all_day: bool = Field(default=False, description="All-day event")
    timezone: str = Field(default="UTC", description="Event timezone")

    # Location
    location: EventLocation | None = Field(default=None, description="Event location")

    # Attendees
    attendees: list[Attendee] = Field(default_factory=list, description="Attendees to invite")
    send_notifications: bool = Field(default=True, description="Send invite notifications")

    # Recurrence
    recurrence: EventRecurrence | None = Field(default=None, description="Recurrence rule")

    # Settings
    calendar_id: str | None = Field(default=None, description="Calendar to create in")
    visibility: str = Field(default="default", description="Event visibility")
    busy_status: str = Field(default="busy", description="Show as busy/free")
    reminders: list[int] = Field(default_factory=list, description="Reminder minutes")
    color: str | None = Field(default=None, description="Event color")


class FreeBusyRequest(BaseModel):
    """Request for free/busy information."""

    start_time: datetime = Field(description="Start of time range")
    end_time: datetime = Field(description="End of time range")
    attendee_emails: list[str] = Field(description="Emails to check availability for")
    calendar_ids: list[str] = Field(
        default_factory=list,
        description="Calendars to check (empty = primary)",
    )


class FreeBusySlot(BaseModel):
    """A busy time slot."""

    email: str = Field(description="Attendee email")
    start: datetime = Field(description="Busy period start")
    end: datetime = Field(description="Busy period end")
    status: str = Field(default="busy", description="Status (busy, tentative)")


class CalendarProvider(Provider[CalendarEvent]):
    """Interface for calendar providers.

    Implementations must map their specific event formats to the
    CalendarEvent domain model.
    """

    # --- Calendar Management ---

    @abstractmethod
    async def list_calendars(self) -> list[CalendarInfo]:
        """List available calendars.

        Returns:
            List of calendar information
        """
        ...

    @abstractmethod
    async def get_calendar(self, calendar_id: str) -> CalendarInfo | None:
        """Get calendar by ID.

        Args:
            calendar_id: Calendar ID

        Returns:
            Calendar info if found
        """
        ...

    # --- Event Reading ---

    @abstractmethod
    async def get_events(self, query: EventQuery) -> tuple[list[CalendarEvent], str | None]:
        """Get events matching query.

        Args:
            query: Event query parameters

        Returns:
            Tuple of (events, next_page_token)
        """
        ...

    @abstractmethod
    async def get_event(
        self,
        event_id: str,
        calendar_id: str | None = None,
    ) -> CalendarEvent | None:
        """Get a single event by ID.

        Args:
            event_id: Event ID
            calendar_id: Calendar ID (optional for some providers)

        Returns:
            Event if found
        """
        ...

    @abstractmethod
    async def get_today_events(self) -> list[CalendarEvent]:
        """Get all events for today.

        Returns:
            Today's events ordered by start time
        """
        ...

    @abstractmethod
    async def get_upcoming_events(
        self,
        hours: int = 24,
        limit: int = 10,
    ) -> list[CalendarEvent]:
        """Get upcoming events within time window.

        Args:
            hours: Hours to look ahead
            limit: Maximum events to return

        Returns:
            Upcoming events ordered by start time
        """
        ...

    @abstractmethod
    def stream_events(self, query: EventQuery) -> AsyncIterator[CalendarEvent]:
        """Stream events for large result sets.

        Args:
            query: Event query parameters

        Yields:
            CalendarEvent objects
        """
        ...

    # --- Event Creation ---

    @abstractmethod
    async def create_event(self, event: NewEvent) -> CalendarEvent:
        """Create a new event.

        Args:
            event: Event to create

        Returns:
            Created event
        """
        ...

    # --- Event Modification ---

    @abstractmethod
    async def update_event(
        self,
        event_id: str,
        updates: dict[str, object],
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> CalendarEvent:
        """Update an existing event.

        Args:
            event_id: Event to update
            updates: Fields to update
            calendar_id: Calendar containing event
            send_notifications: Whether to notify attendees

        Returns:
            Updated event
        """
        ...

    @abstractmethod
    async def delete_event(
        self,
        event_id: str,
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> None:
        """Delete an event.

        Args:
            event_id: Event to delete
            calendar_id: Calendar containing event
            send_notifications: Whether to notify attendees
        """
        ...

    # --- Attendee Operations ---

    @abstractmethod
    async def respond_to_event(
        self,
        event_id: str,
        response: AttendeeResponse,
        calendar_id: str | None = None,
        comment: str | None = None,
    ) -> CalendarEvent:
        """Respond to an event invitation.

        Args:
            event_id: Event to respond to
            response: Response status
            calendar_id: Calendar containing event
            comment: Optional response comment

        Returns:
            Updated event
        """
        ...

    @abstractmethod
    async def add_attendees(
        self,
        event_id: str,
        attendees: list[Attendee],
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> CalendarEvent:
        """Add attendees to an event.

        Args:
            event_id: Event to modify
            attendees: Attendees to add
            calendar_id: Calendar containing event
            send_notifications: Whether to send invites

        Returns:
            Updated event
        """
        ...

    @abstractmethod
    async def remove_attendee(
        self,
        event_id: str,
        email: str,
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> CalendarEvent:
        """Remove an attendee from an event.

        Args:
            event_id: Event to modify
            email: Attendee email to remove
            calendar_id: Calendar containing event
            send_notifications: Whether to notify

        Returns:
            Updated event
        """
        ...

    # --- Free/Busy ---

    @abstractmethod
    async def get_free_busy(self, request: FreeBusyRequest) -> list[FreeBusySlot]:
        """Get free/busy information for attendees.

        Args:
            request: Free/busy query

        Returns:
            List of busy time slots
        """
        ...

    @abstractmethod
    async def find_available_times(
        self,
        attendee_emails: list[str],
        duration_minutes: int,
        start_time: datetime,
        end_time: datetime,
        working_hours_only: bool = True,
    ) -> list[tuple[datetime, datetime]]:
        """Find available meeting times for attendees.

        Args:
            attendee_emails: Attendees to check
            duration_minutes: Meeting duration
            start_time: Earliest possible start
            end_time: Latest possible end
            working_hours_only: Only suggest business hours

        Returns:
            List of (start, end) tuples for available slots
        """
        ...

    # --- Sync Operations ---

    @abstractmethod
    async def get_changes_since(
        self,
        sync_token: str | None = None,
        calendar_id: str | None = None,
    ) -> tuple[list[CalendarEvent], str]:
        """Get events changed since last sync.

        Args:
            sync_token: Token from previous sync
            calendar_id: Calendar to sync (None = all)

        Returns:
            Tuple of (changed events, new sync token)
        """
        ...
