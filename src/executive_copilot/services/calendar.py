"""Calendar service for orchestrating calendar operations."""

from datetime import datetime, timedelta

from executive_copilot.models import CalendarEvent
from executive_copilot.providers.calendar import CalendarProvider, EventQuery
from executive_copilot.services.base import Service


class CalendarService(Service):
    """Service for calendar operations.

    Orchestrates calendar providers and handles calendar-related
    business logic.
    """

    async def get_today_events(self) -> list[CalendarEvent]:
        """Get all events for today.

        Returns:
            Today's events ordered by start time
        """
        events: list[CalendarEvent] = []
        providers = self._get_calendar_providers()

        for provider in providers:
            try:
                provider_events = await provider.get_today_events()
                events.extend(provider_events)
            except Exception as e:
                self._logger.error(
                    "Failed to get today's events",
                    provider=provider.name,
                    error=str(e),
                )

        # Sort by start time
        events.sort(key=lambda e: e.start_time)
        return events

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
        events: list[CalendarEvent] = []
        providers = self._get_calendar_providers()

        for provider in providers:
            try:
                provider_events = await provider.get_upcoming_events(
                    hours=hours,
                    limit=limit,
                )
                events.extend(provider_events)
            except Exception as e:
                self._logger.error(
                    "Failed to get upcoming events",
                    provider=provider.name,
                    error=str(e),
                )

        # Sort by start time and limit
        events.sort(key=lambda e: e.start_time)
        return events[:limit]

    async def get_events(
        self,
        start_time: datetime,
        end_time: datetime,
        *,
        calendar_ids: list[str] | None = None,
        query: str | None = None,
        provider_name: str | None = None,
    ) -> list[CalendarEvent]:
        """Get events in a time range.

        Args:
            start_time: Range start
            end_time: Range end
            calendar_ids: Specific calendars to query
            query: Optional text search
            provider_name: Specific provider to query

        Returns:
            Events in the range
        """
        event_query = EventQuery(
            start_time=start_time,
            end_time=end_time,
            calendar_ids=calendar_ids or [],
            query=query,
        )

        events: list[CalendarEvent] = []
        providers = self._get_calendar_providers(provider_name)

        for provider in providers:
            try:
                provider_events, _ = await provider.get_events(event_query)
                events.extend(provider_events)
            except Exception as e:
                self._logger.error(
                    "Failed to get events",
                    provider=provider.name,
                    error=str(e),
                )

        events.sort(key=lambda e: e.start_time)
        return events

    async def get_event(
        self,
        event_id: str,
        *,
        calendar_id: str | None = None,
        provider_name: str | None = None,
    ) -> CalendarEvent | None:
        """Get a single event.

        Args:
            event_id: Event ID
            calendar_id: Calendar containing event
            provider_name: Provider to query

        Returns:
            Event if found
        """
        providers = self._get_calendar_providers(provider_name)

        for provider in providers:
            try:
                event = await provider.get_event(event_id, calendar_id)
                if event:
                    return event
            except Exception as e:
                self._logger.error(
                    "Failed to get event",
                    provider=provider.name,
                    event_id=event_id,
                    error=str(e),
                )

        return None

    async def get_schedule_summary(
        self,
        date: datetime | None = None,
    ) -> dict[str, object]:
        """Get a summary of the day's schedule.

        Args:
            date: Date to summarize (defaults to today)

        Returns:
            Schedule summary with event count and busy time
        """
        if date is None:
            date = datetime.utcnow()

        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)

        events = await self.get_events(start, end)

        # Calculate metrics
        total_events = len(events)
        meeting_minutes = sum(e.duration_minutes for e in events if not e.is_all_day)
        has_conflicts = self._detect_conflicts(events)

        return {
            "date": date.date().isoformat(),
            "total_events": total_events,
            "meeting_minutes": meeting_minutes,
            "meeting_hours": round(meeting_minutes / 60, 1),
            "has_conflicts": has_conflicts,
            "first_event": events[0].title if events else None,
            "first_event_time": events[0].start_time.isoformat() if events else None,
        }

    async def find_free_time(
        self,
        date: datetime,
        duration_minutes: int,
        working_hours_start: int = 9,
        working_hours_end: int = 17,
    ) -> list[tuple[datetime, datetime]]:
        """Find free time slots on a given date.

        Args:
            date: Date to check
            duration_minutes: Required duration
            working_hours_start: Start of working day (hour)
            working_hours_end: End of working day (hour)

        Returns:
            List of (start, end) tuples for available slots
        """
        start = date.replace(hour=working_hours_start, minute=0, second=0, microsecond=0)
        end = date.replace(hour=working_hours_end, minute=0, second=0, microsecond=0)

        events = await self.get_events(start, end)

        # Find gaps between events
        free_slots: list[tuple[datetime, datetime]] = []
        current = start

        for event in sorted(events, key=lambda e: e.start_time):
            if event.is_all_day:
                continue
            if event.start_time > current:
                gap_minutes = (event.start_time - current).total_seconds() / 60
                if gap_minutes >= duration_minutes:
                    free_slots.append((current, event.start_time))
            current = max(current, event.end_time)

        # Check final gap
        if current < end:
            gap_minutes = (end - current).total_seconds() / 60
            if gap_minutes >= duration_minutes:
                free_slots.append((current, end))

        return free_slots

    def _detect_conflicts(self, events: list[CalendarEvent]) -> bool:
        """Detect if any events overlap.

        Args:
            events: Events to check

        Returns:
            True if conflicts exist
        """
        non_all_day = [e for e in events if not e.is_all_day]
        sorted_events = sorted(non_all_day, key=lambda e: e.start_time)

        for i in range(len(sorted_events) - 1):
            if sorted_events[i].end_time > sorted_events[i + 1].start_time:
                return True

        return False

    def _get_calendar_providers(
        self,
        provider_name: str | None = None,
    ) -> list[CalendarProvider]:
        """Get calendar providers to use.

        Args:
            provider_name: Specific provider name, or None for all

        Returns:
            List of calendar providers
        """
        providers: list[CalendarProvider] = []
        configs = self.container.config.get_enabled_providers("calendar")

        for config in configs:
            if provider_name and config.provider_name != provider_name:
                continue
            try:
                provider = self.container.provider_registry.get_or_create(config)
                if isinstance(provider, CalendarProvider):
                    providers.append(provider)
            except Exception as e:
                self._logger.error(
                    "Failed to get provider",
                    provider=config.provider_name,
                    error=str(e),
                )

        return providers
