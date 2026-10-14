"""Outlook Calendar provider using Microsoft Graph API."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx

from executive_copilot.admin.oauth import OAuthManager
from executive_copilot.credentials import CredentialManager
from executive_copilot.logging import get_logger
from executive_copilot.models.base import ProviderReference
from executive_copilot.models.calendar import (
    Attendee,
    AttendeeResponse,
    CalendarEvent,
    EventLocation,
    EventRecurrence,
    EventStatus,
    RecurrenceFrequency,
)
from executive_copilot.providers.calendar.interface import (
    CalendarInfo,
    CalendarProvider,
    EventQuery,
    FreeBusyRequest,
    FreeBusySlot,
    NewEvent,
)

logger = get_logger(__name__)

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"


class OutlookCalendarProvider(CalendarProvider):
    """Outlook/Microsoft 365 Calendar provider using Graph API."""

    def __init__(self, credential_manager: CredentialManager, account_id: str) -> None:
        self.credential_manager = credential_manager
        self.account_id = account_id
        self._access_token: str | None = None

    @property
    def provider_name(self) -> str:
        return "outlook"

    @property
    def provider_type(self) -> str:
        return "calendar"

    @property
    def info(self) -> Any:
        from executive_copilot.providers.base import ProviderCapability, ProviderInfo
        return ProviderInfo(
            name="outlook_calendar",
            display_name="Outlook Calendar",
            provider_type="calendar",
            capabilities=[
                ProviderCapability.READ_EVENTS,
                ProviderCapability.CREATE_EVENTS,
                ProviderCapability.UPDATE_EVENTS,
                ProviderCapability.DELETE_EVENTS,
                ProviderCapability.RESPOND_TO_EVENTS,
            ],
            requires_auth=True,
            auth_type="oauth2",
        )

    async def connect(self) -> None:
        """Establish connection (validates credentials)."""
        self._get_access_token()

    async def disconnect(self) -> None:
        """Close connection."""
        self._access_token = None

    async def health_check(self) -> bool:
        """Check if connection is healthy."""
        try:
            self._get_access_token()
            return True
        except Exception:
            return False

    def _get_access_token(self) -> str:
        """Get access token from credentials."""
        if self._access_token:
            return self._access_token

        cred = self.credential_manager.get_credential("calendar", "outlook", self.account_id)
        if not cred or not cred.get("access_token"):
            raise ValueError(f"No access token for {self.account_id}. Please authorize first.")

        self._access_token = cred["access_token"]
        return self._access_token

    def _headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
            "Prefer": 'outlook.timezone="UTC"',
        }

    async def _refresh_access_token(self) -> bool:
        """Refresh the cached access token.

        The OAuth client and refresh token are resolved across the account's
        sibling credentials (mail / calendar / documents), and the new token is
        stored on all of them.
        """
        oauth_manager = OAuthManager(self.credential_manager)
        new_token = await oauth_manager.refresh_microsoft_token(account_id=self.account_id)
        if not new_token:
            return False

        self._access_token = new_token
        return True

    async def _api_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make GET request to Graph API."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GRAPH_API_BASE}{path}",
                headers=self._headers(),
                params=params,
            )
            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.get(
                        f"{GRAPH_API_BASE}{path}",
                        headers=self._headers(),
                        params=params,
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result

    async def _api_post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        """Make POST request to Graph API."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GRAPH_API_BASE}{path}",
                headers=self._headers(),
                json=data,
            )
            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.post(
                        f"{GRAPH_API_BASE}{path}",
                        headers=self._headers(),
                        json=data,
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result

    async def _api_patch(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        """Make PATCH request to Graph API."""
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{GRAPH_API_BASE}{path}",
                headers=self._headers(),
                json=data,
            )
            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.patch(
                        f"{GRAPH_API_BASE}{path}",
                        headers=self._headers(),
                        json=data,
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result

    async def _api_delete(self, path: str) -> None:
        """Make DELETE request to Graph API."""
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{GRAPH_API_BASE}{path}",
                headers=self._headers(),
            )
            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.delete(
                        f"{GRAPH_API_BASE}{path}",
                        headers=self._headers(),
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")
            response.raise_for_status()

    # --- Calendar Management ---

    async def list_calendars(self) -> list[CalendarInfo]:
        """List available calendars."""
        data = await self._api_get("/me/calendars")
        calendars = []
        for item in data.get("value", []):
            calendars.append(CalendarInfo(
                id=item["id"],
                name=item.get("name", "Untitled"),
                color=item.get("color"),
                is_primary=item.get("isDefaultCalendar", False),
                is_owner=item.get("canEdit", True),
                can_edit=item.get("canEdit", True),
            ))
        return calendars

    async def get_calendar(self, calendar_id: str) -> CalendarInfo | None:
        """Get calendar by ID."""
        try:
            data = await self._api_get(f"/me/calendars/{calendar_id}")
            return CalendarInfo(
                id=data["id"],
                name=data.get("name", "Untitled"),
                color=data.get("color"),
                is_primary=data.get("isDefaultCalendar", False),
                can_edit=data.get("canEdit", True),
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # --- Event Reading ---

    async def get_events(self, query: EventQuery) -> tuple[list[CalendarEvent], str | None]:
        """Get events matching query."""
        params: dict[str, Any] = {
            "$top": min(query.limit, 1000),
            "$orderby": "start/dateTime",
            "$filter": f"start/dateTime ge '{query.start_time.isoformat()}' and end/dateTime le '{query.end_time.isoformat()}'",
        }
        if query.query:
            params["$search"] = f'"{query.query}"'
        if query.page_token:
            params["$skip"] = query.page_token

        calendar_path = "/me/calendar/events" if not query.calendar_ids else f"/me/calendars/{query.calendar_ids[0]}/events"

        all_events: list[CalendarEvent] = []

        if not query.calendar_ids:
            data = await self._api_get("/me/calendar/calendarView", {
                "startDateTime": query.start_time.isoformat(),
                "endDateTime": query.end_time.isoformat(),
                "$top": min(query.limit, 1000),
                "$orderby": "start/dateTime",
            })
            for item in data.get("value", []):
                event = self._parse_event(item, "primary")
                all_events.append(event)
        else:
            for cal_id in query.calendar_ids:
                data = await self._api_get(f"/me/calendars/{cal_id}/calendarView", {
                    "startDateTime": query.start_time.isoformat(),
                    "endDateTime": query.end_time.isoformat(),
                    "$top": min(query.limit, 1000),
                    "$orderby": "start/dateTime",
                })
                for item in data.get("value", []):
                    event = self._parse_event(item, cal_id)
                    all_events.append(event)

        all_events.sort(key=lambda e: e.start_time)
        next_token = data.get("@odata.nextLink")
        return all_events, next_token

    async def get_event(self, event_id: str, calendar_id: str | None = None) -> CalendarEvent | None:
        """Get a single event by ID."""
        try:
            data = await self._api_get(f"/me/events/{event_id}")
            return self._parse_event(data, calendar_id or "primary")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_today_events(self) -> list[CalendarEvent]:
        """Get all events for today."""
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        query = EventQuery(start_time=start, end_time=end)
        events, _ = await self.get_events(query)
        return events

    async def get_upcoming_events(self, hours: int = 24, limit: int = 10) -> list[CalendarEvent]:
        """Get upcoming events within time window."""
        now = datetime.now(UTC)
        end = now + timedelta(hours=hours)
        query = EventQuery(start_time=now, end_time=end, limit=limit)
        events, _ = await self.get_events(query)
        return events

    async def stream_events(self, query: EventQuery) -> AsyncIterator[CalendarEvent]:
        """Stream events for large result sets."""
        skip = 0
        page_size = 100
        while True:
            query_with_skip = EventQuery(
                start_time=query.start_time,
                end_time=query.end_time,
                calendar_ids=query.calendar_ids,
                query=query.query,
                limit=page_size,
                page_token=str(skip) if skip > 0 else None,
            )
            events, _ = await self.get_events(query_with_skip)
            if not events:
                break
            for event in events:
                yield event
            skip += len(events)
            if len(events) < page_size:
                break

    # --- Event Creation ---

    async def create_event(self, event: NewEvent) -> CalendarEvent:
        """Create a new event."""
        body: dict[str, Any] = {
            "subject": event.title,
            "body": {"contentType": "text", "content": event.description or ""},
        }

        if event.is_all_day:
            body["isAllDay"] = True
            body["start"] = {"dateTime": event.start_time.isoformat(), "timeZone": event.timezone}
            body["end"] = {"dateTime": event.end_time.isoformat(), "timeZone": event.timezone}
        else:
            body["start"] = {"dateTime": event.start_time.isoformat(), "timeZone": event.timezone}
            body["end"] = {"dateTime": event.end_time.isoformat(), "timeZone": event.timezone}

        if event.location:
            body["location"] = {"displayName": event.location.display_name}
            if event.location.conference_url:
                body["isOnlineMeeting"] = True
                body["onlineMeetingProvider"] = "teamsForBusiness"

        if event.attendees:
            body["attendees"] = [
                {
                    "emailAddress": {"address": a.email, "name": a.display_name or a.email},
                    "type": "optional" if a.is_optional else "required",
                }
                for a in event.attendees
            ]

        path = f"/me/calendars/{event.calendar_id}/events" if event.calendar_id else "/me/calendar/events"
        data = await self._api_post(path, body)
        return self._parse_event(data, event.calendar_id or "primary")

    # --- Event Modification ---

    async def update_event(
        self,
        event_id: str,
        updates: dict[str, object],
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> CalendarEvent:
        """Update an existing event."""
        body: dict[str, Any] = {}

        if "title" in updates:
            body["subject"] = updates["title"]
        if "description" in updates:
            body["body"] = {"contentType": "text", "content": updates["description"]}
        if "start_time" in updates:
            body["start"] = {"dateTime": updates["start_time"].isoformat(), "timeZone": "UTC"}  # type: ignore
        if "end_time" in updates:
            body["end"] = {"dateTime": updates["end_time"].isoformat(), "timeZone": "UTC"}  # type: ignore
        if "location" in updates:
            body["location"] = {"displayName": updates["location"]}

        data = await self._api_patch(f"/me/events/{event_id}", body)
        return self._parse_event(data, calendar_id or "primary")

    async def delete_event(
        self,
        event_id: str,
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> None:
        """Delete an event."""
        await self._api_delete(f"/me/events/{event_id}")

    # --- Attendee Operations ---

    async def respond_to_event(
        self,
        event_id: str,
        response: AttendeeResponse,
        calendar_id: str | None = None,
        comment: str | None = None,
    ) -> CalendarEvent:
        """Respond to an event invitation."""
        action_map = {
            AttendeeResponse.ACCEPTED: "accept",
            AttendeeResponse.DECLINED: "decline",
            AttendeeResponse.TENTATIVE: "tentativelyAccept",
        }
        action = action_map.get(response, "tentativelyAccept")

        body: dict[str, Any] = {"sendResponse": True}
        if comment:
            body["comment"] = comment

        await self._api_post(f"/me/events/{event_id}/{action}", body)
        event = await self.get_event(event_id, calendar_id)
        if not event:
            raise ValueError(f"Event {event_id} not found after response")
        return event

    async def add_attendees(
        self,
        event_id: str,
        attendees: list[Attendee],
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> CalendarEvent:
        """Add attendees to an event."""
        event = await self.get_event(event_id, calendar_id)
        if not event:
            raise ValueError(f"Event {event_id} not found")

        existing_emails = {a.email.lower() for a in event.attendees}
        all_attendees = [
            {
                "emailAddress": {"address": a.email, "name": a.display_name or a.email},
                "type": "required",
            }
            for a in event.attendees
        ]
        for a in attendees:
            if a.email.lower() not in existing_emails:
                all_attendees.append({
                    "emailAddress": {"address": a.email, "name": a.display_name or a.email},
                    "type": "optional" if a.is_optional else "required",
                })

        body = {"attendees": all_attendees}
        data = await self._api_patch(f"/me/events/{event_id}", body)
        return self._parse_event(data, calendar_id or "primary")

    async def remove_attendee(
        self,
        event_id: str,
        email: str,
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> CalendarEvent:
        """Remove an attendee from an event."""
        event = await self.get_event(event_id, calendar_id)
        if not event:
            raise ValueError(f"Event {event_id} not found")

        remaining = [
            {
                "emailAddress": {"address": a.email, "name": a.display_name or a.email},
                "type": "required",
            }
            for a in event.attendees
            if a.email.lower() != email.lower()
        ]

        body = {"attendees": remaining}
        data = await self._api_patch(f"/me/events/{event_id}", body)
        return self._parse_event(data, calendar_id or "primary")

    # --- Free/Busy ---

    async def get_free_busy(self, request: FreeBusyRequest) -> list[FreeBusySlot]:
        """Get free/busy information for attendees."""
        body = {
            "schedules": request.attendee_emails,
            "startTime": {"dateTime": request.start_time.isoformat(), "timeZone": "UTC"},
            "endTime": {"dateTime": request.end_time.isoformat(), "timeZone": "UTC"},
            "availabilityViewInterval": 30,
        }

        data = await self._api_post("/me/calendar/getSchedule", body)
        slots: list[FreeBusySlot] = []

        for schedule in data.get("value", []):
            email = schedule.get("scheduleId", "")
            for item in schedule.get("scheduleItems", []):
                status = item.get("status", "busy")
                if status in ("busy", "tentative", "oof"):
                    start_data = item.get("start", {})
                    end_data = item.get("end", {})
                    slots.append(FreeBusySlot(
                        email=email,
                        start=datetime.fromisoformat(start_data.get("dateTime", "").replace("Z", "+00:00")),
                        end=datetime.fromisoformat(end_data.get("dateTime", "").replace("Z", "+00:00")),
                        status=status,
                    ))

        return slots

    async def find_available_times(
        self,
        attendee_emails: list[str],
        duration_minutes: int,
        start_time: datetime,
        end_time: datetime,
        working_hours_only: bool = True,
    ) -> list[tuple[datetime, datetime]]:
        """Find available meeting times for attendees."""
        body = {
            "attendees": [{"emailAddress": {"address": email}} for email in attendee_emails],
            "timeConstraint": {
                "timeslots": [{
                    "start": {"dateTime": start_time.isoformat(), "timeZone": "UTC"},
                    "end": {"dateTime": end_time.isoformat(), "timeZone": "UTC"},
                }]
            },
            "meetingDuration": f"PT{duration_minutes}M",
            "returnSuggestionReasons": False,
            "minimumAttendeePercentage": 100,
        }

        data = await self._api_post("/me/findMeetingTimes", body)
        available: list[tuple[datetime, datetime]] = []

        for suggestion in data.get("meetingTimeSuggestions", []):
            slot = suggestion.get("meetingTimeSlot", {})
            start_data = slot.get("start", {})
            end_data = slot.get("end", {})

            slot_start = datetime.fromisoformat(start_data.get("dateTime", "").replace("Z", "+00:00"))
            slot_end = datetime.fromisoformat(end_data.get("dateTime", "").replace("Z", "+00:00"))

            if working_hours_only:
                if 9 <= slot_start.hour < 18 and slot_start.weekday() < 5:
                    available.append((slot_start, slot_end))
            else:
                available.append((slot_start, slot_end))

        return available[:10]

    # --- Sync Operations ---

    async def get_changes_since(
        self,
        sync_token: str | None = None,
        calendar_id: str | None = None,
    ) -> tuple[list[CalendarEvent], str]:
        """Get events changed since last sync."""
        params: dict[str, Any] = {}
        if sync_token:
            params["$deltatoken"] = sync_token
        else:
            start = datetime.now(UTC) - timedelta(days=30)
            end = datetime.now(UTC) + timedelta(days=365)
            params["startDateTime"] = start.isoformat()
            params["endDateTime"] = end.isoformat()

        data = await self._api_get("/me/calendar/calendarView/delta", params)
        events = [self._parse_event(item, calendar_id or "primary") for item in data.get("value", [])]

        delta_link = data.get("@odata.deltaLink", "")
        new_token = delta_link.split("$deltatoken=")[-1] if "$deltatoken=" in delta_link else ""

        return events, new_token

    # --- Parsing ---

    def _parse_event(self, data: dict[str, Any], calendar_id: str) -> CalendarEvent:
        """Parse Graph API event into domain model."""
        start_data = data.get("start", {})
        end_data = data.get("end", {})

        is_all_day = data.get("isAllDay", False)
        start_str = start_data.get("dateTime", "")
        end_str = end_data.get("dateTime", "")

        if start_str:
            if "Z" in start_str:
                start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            elif "+" in start_str or start_str.endswith("-00:00"):
                start_time = datetime.fromisoformat(start_str)
            else:
                start_time = datetime.fromisoformat(start_str).replace(tzinfo=UTC)
        else:
            start_time = datetime.now(UTC)

        if end_str:
            if "Z" in end_str:
                end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            elif "+" in end_str or end_str.endswith("-00:00"):
                end_time = datetime.fromisoformat(end_str)
            else:
                end_time = datetime.fromisoformat(end_str).replace(tzinfo=UTC)
        else:
            end_time = start_time + timedelta(hours=1)

        tz = start_data.get("timeZone", "UTC")

        status_map = {
            "notResponded": EventStatus.TENTATIVE,
            "organizer": EventStatus.CONFIRMED,
            "accepted": EventStatus.CONFIRMED,
            "declined": EventStatus.CANCELLED,
            "tentativelyAccepted": EventStatus.TENTATIVE,
        }
        response_status = data.get("responseStatus", {}).get("response", "notResponded")
        status = status_map.get(response_status, EventStatus.CONFIRMED)
        if data.get("isCancelled"):
            status = EventStatus.CANCELLED

        location = None
        loc_data = data.get("location", {})
        if loc_data.get("displayName"):
            location = EventLocation(display_name=loc_data["displayName"])

        online_meeting = data.get("onlineMeeting")
        if online_meeting and online_meeting.get("joinUrl"):
            if location:
                location = EventLocation(
                    display_name=location.display_name,
                    conference_url=online_meeting["joinUrl"],
                    conference_type="teams",
                )
            else:
                location = EventLocation(
                    display_name="Teams Meeting",
                    conference_url=online_meeting["joinUrl"],
                    conference_type="teams",
                )

        organizer = None
        org_data = data.get("organizer", {}).get("emailAddress", {})
        if org_data:
            organizer = Attendee(
                email=org_data.get("address", ""),
                display_name=org_data.get("name"),
                is_organizer=True,
            )

        attendees: list[Attendee] = []
        response_map = {
            "accepted": AttendeeResponse.ACCEPTED,
            "declined": AttendeeResponse.DECLINED,
            "tentativelyAccepted": AttendeeResponse.TENTATIVE,
            "notResponded": AttendeeResponse.NEEDS_ACTION,
            "none": AttendeeResponse.NEEDS_ACTION,
        }
        for att_data in data.get("attendees", []):
            email_data = att_data.get("emailAddress", {})
            status_data = att_data.get("status", {})
            attendees.append(Attendee(
                email=email_data.get("address", ""),
                display_name=email_data.get("name"),
                response_status=response_map.get(status_data.get("response", ""), AttendeeResponse.NEEDS_ACTION),
                is_optional=att_data.get("type") == "optional",
            ))

        is_recurring = bool(data.get("recurrence"))
        recurrence = None
        if data.get("recurrence"):
            recurrence = self._parse_recurrence(data["recurrence"])

        return CalendarEvent(
            id=uuid4(),
            created_at=start_time,
            updated_at=None,
            title=data.get("subject", "(No Title)"),
            description=data.get("body", {}).get("content"),
            status=status,
            start_time=start_time,
            end_time=end_time,
            is_all_day=is_all_day,
            timezone=tz,
            is_recurring=is_recurring,
            recurrence=recurrence,
            recurring_event_id=None,
            is_exception=bool(data.get("seriesMasterId")),
            location=location,
            organizer=organizer,
            attendees=attendees,
            calendar_id=calendar_id,
            calendar_name=None,
            visibility="private" if data.get("sensitivity") == "private" else "default",
            busy_status=data.get("showAs", "busy"),
            reminders=[],
            is_private=data.get("sensitivity") == "private",
            is_cancelled=status == EventStatus.CANCELLED,
            can_edit=True,
            can_respond=True,
            provider_ref=ProviderReference(
                provider_name="outlook",
                external_id=data["id"],
                raw_data={"account": self.account_id},
            ),
        )

    def _parse_recurrence(self, rec_data: dict[str, Any]) -> EventRecurrence | None:
        """Parse recurrence pattern."""
        pattern = rec_data.get("pattern", {})
        freq_map = {
            "daily": RecurrenceFrequency.DAILY,
            "weekly": RecurrenceFrequency.WEEKLY,
            "absoluteMonthly": RecurrenceFrequency.MONTHLY,
            "relativeMonthly": RecurrenceFrequency.MONTHLY,
            "absoluteYearly": RecurrenceFrequency.YEARLY,
            "relativeYearly": RecurrenceFrequency.YEARLY,
        }
        freq = freq_map.get(pattern.get("type", ""), RecurrenceFrequency.WEEKLY)

        range_data = rec_data.get("range", {})
        count = None
        until = None
        if range_data.get("type") == "numbered":
            count = range_data.get("numberOfOccurrences")
        elif range_data.get("type") == "endDate":
            until_str = range_data.get("endDate")
            if until_str:
                until = datetime.fromisoformat(until_str).replace(tzinfo=UTC)

        return EventRecurrence(
            frequency=freq,
            interval=pattern.get("interval", 1),
            count=count,
            until=until,
            by_day=pattern.get("daysOfWeek", []),
        )


async def test_outlook_calendar_connection(
    credential_manager: CredentialManager, account_id: str
) -> dict[str, Any]:
    """Test Outlook Calendar connection and return account info."""
    provider = OutlookCalendarProvider(credential_manager, account_id)

    try:
        calendars = await provider.list_calendars()
        today_events = await provider.get_today_events()

        return {
            "success": True,
            "account_id": account_id,
            "calendars": len(calendars),
            "primary_calendar": next((c.name for c in calendars if c.is_primary), None),
            "today_events": len(today_events),
        }
    except Exception as e:
        return {
            "success": False,
            "account_id": account_id,
            "error": str(e),
        }
