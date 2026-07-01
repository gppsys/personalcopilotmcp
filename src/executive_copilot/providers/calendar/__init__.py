"""Calendar provider interfaces and implementations."""

from executive_copilot.providers.calendar.google import GoogleCalendarProvider
from executive_copilot.providers.calendar.interface import (
    CalendarInfo,
    CalendarProvider,
    EventQuery,
    FreeBusyRequest,
    FreeBusySlot,
    NewEvent,
)
from executive_copilot.providers.calendar.outlook import OutlookCalendarProvider

__all__ = [
    "CalendarInfo",
    "CalendarProvider",
    "EventQuery",
    "FreeBusyRequest",
    "FreeBusySlot",
    "GoogleCalendarProvider",
    "NewEvent",
    "OutlookCalendarProvider",
]
