"""Google Calendar provider implementation."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.admin.oauth import OAuthManager
from executive_copilot.logging import get_logger
from executive_copilot.models.base import ProviderReference
from executive_copilot.models.calendar import (
    Attendee,
    AttendeeResponse,
    CalendarEvent,
    EventAttachment,
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

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
DRIVE_EXPORT_TIMEOUT = httpx.Timeout(10.0, read=60.0)


class GoogleCalendarProvider(CalendarProvider):
    """Google Calendar provider using Calendar API."""

    def __init__(self, credential_manager: CredentialManager, account_id: str) -> None:
        self.credential_manager = credential_manager
        self.account_id = account_id
        self._access_token: str | None = None
        # Set when Drive cannot be used at all (missing scope, API disabled),
        # as opposed to one file not being shared with the account.
        self.drive_unavailable: str | None = None

    @property
    def provider_name(self) -> str:
        return "google"

    @property
    def provider_type(self) -> str:
        return "calendar"

    @property
    def info(self) -> Any:
        from executive_copilot.providers.base import ProviderCapability, ProviderInfo
        return ProviderInfo(
            name="google_calendar",
            display_name="Google Calendar",
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

        cred = self.credential_manager.get_credential("calendar", "google", self.account_id)
        if not cred or not cred.get("access_token"):
            raise ValueError(f"No access token for {self.account_id}. Please authorize first.")

        self._access_token = cred["access_token"]
        return self._access_token

    def _headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    async def _refresh_access_token(self) -> bool:
        """Refresh the cached access token.

        The OAuth client and refresh token are resolved across the account's
        sibling credentials (mail / calendar / documents), and the new token is
        stored on all of them.
        """
        oauth_manager = OAuthManager(self.credential_manager)
        new_token = await oauth_manager.refresh_google_token(account_id=self.account_id)
        if not new_token:
            return False

        self._access_token = new_token
        return True

    async def _api_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make GET request to Calendar API."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{CALENDAR_API_BASE}{path}",
                headers=self._headers(),
                params=params,
            )
            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.get(
                        f"{CALENDAR_API_BASE}{path}",
                        headers=self._headers(),
                        params=params,
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result

    async def _api_post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        """Make POST request to Calendar API."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{CALENDAR_API_BASE}{path}",
                headers=self._headers(),
                json=data,
            )
            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.post(
                        f"{CALENDAR_API_BASE}{path}",
                        headers=self._headers(),
                        json=data,
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result

    async def _api_patch(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        """Make PATCH request to Calendar API."""
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{CALENDAR_API_BASE}{path}",
                headers=self._headers(),
                json=data,
            )
            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.patch(
                        f"{CALENDAR_API_BASE}{path}",
                        headers=self._headers(),
                        json=data,
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result

    async def _api_delete(self, path: str) -> None:
        """Make DELETE request to Calendar API."""
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{CALENDAR_API_BASE}{path}",
                headers=self._headers(),
            )
            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.delete(
                        f"{CALENDAR_API_BASE}{path}",
                        headers=self._headers(),
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")
            response.raise_for_status()

    # --- Calendar Management ---

    async def list_calendars(self) -> list[CalendarInfo]:
        """List available calendars."""
        data = await self._api_get("/users/me/calendarList")
        calendars = []
        for item in data.get("items", []):
            calendars.append(CalendarInfo(
                id=item["id"],
                name=item.get("summary", "Untitled"),
                description=item.get("description"),
                color=item.get("backgroundColor"),
                timezone=item.get("timeZone", "UTC"),
                is_primary=item.get("primary", False),
                is_owner=item.get("accessRole") == "owner",
                can_edit=item.get("accessRole") in ("owner", "writer"),
                is_selected=item.get("selected", True),
            ))
        return calendars

    async def get_calendar(self, calendar_id: str) -> CalendarInfo | None:
        """Get calendar by ID."""
        try:
            data = await self._api_get(f"/calendars/{calendar_id}")
            return CalendarInfo(
                id=data["id"],
                name=data.get("summary", "Untitled"),
                description=data.get("description"),
                timezone=data.get("timeZone", "UTC"),
                is_primary=data.get("primary", False),
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # --- Event Reading ---

    async def get_events(self, query: EventQuery) -> tuple[list[CalendarEvent], str | None]:
        """Get events matching query."""
        params: dict[str, Any] = {
            "timeMin": query.start_time.isoformat(),
            "timeMax": query.end_time.isoformat(),
            "maxResults": min(query.limit, 2500),
            "singleEvents": query.single_events,
            "orderBy": "startTime" if query.single_events else "updated",
        }
        if query.query:
            params["q"] = query.query
        if query.show_deleted:
            params["showDeleted"] = True
        if query.page_token:
            params["pageToken"] = query.page_token

        calendar_ids = query.calendar_ids or ["primary"]
        all_events: list[CalendarEvent] = []
        next_token: str | None = None

        for cal_id in calendar_ids:
            data = await self._api_get(f"/calendars/{cal_id}/events", params)
            for item in data.get("items", []):
                event = self._parse_event(item, cal_id)
                all_events.append(event)
            if len(calendar_ids) == 1:
                next_token = data.get("nextPageToken")

        all_events.sort(key=lambda e: e.start_time)
        return all_events, next_token

    async def get_event(self, event_id: str, calendar_id: str | None = None) -> CalendarEvent | None:
        """Get a single event by ID."""
        cal_id = calendar_id or "primary"
        try:
            data = await self._api_get(f"/calendars/{cal_id}/events/{event_id}")
            return self._parse_event(data, cal_id)
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
        page_token: str | None = None
        while True:
            query_with_token = EventQuery(
                start_time=query.start_time,
                end_time=query.end_time,
                calendar_ids=query.calendar_ids,
                query=query.query,
                show_deleted=query.show_deleted,
                single_events=query.single_events,
                limit=min(query.limit, 250),
                page_token=page_token,
            )
            events, next_token = await self.get_events(query_with_token)
            for event in events:
                yield event
            if not next_token:
                break
            page_token = next_token

    # --- Event Creation ---

    async def create_event(self, event: NewEvent) -> CalendarEvent:
        """Create a new event."""
        body: dict[str, Any] = {
            "summary": event.title,
            "description": event.description,
        }

        if event.is_all_day:
            body["start"] = {"date": event.start_time.date().isoformat()}
            body["end"] = {"date": event.end_time.date().isoformat()}
        else:
            body["start"] = {"dateTime": event.start_time.isoformat(), "timeZone": event.timezone}
            body["end"] = {"dateTime": event.end_time.isoformat(), "timeZone": event.timezone}

        if event.location:
            body["location"] = event.location.display_name
            if event.location.conference_url:
                body["conferenceData"] = {
                    "entryPoints": [{"entryPointType": "video", "uri": event.location.conference_url}]
                }

        if event.attendees:
            body["attendees"] = [
                {"email": a.email, "displayName": a.display_name, "optional": a.is_optional}
                for a in event.attendees
            ]

        if event.visibility != "default":
            body["visibility"] = event.visibility

        if event.reminders:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": m} for m in event.reminders],
            }

        params = {"sendUpdates": "all" if event.send_notifications else "none"}
        cal_id = event.calendar_id or "primary"
        data = await self._api_post(
            f"/calendars/{cal_id}/events?{httpx.QueryParams(params)}", body
        )
        return self._parse_event(data, cal_id)

    # --- Event Modification ---

    async def update_event(
        self,
        event_id: str,
        updates: dict[str, object],
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> CalendarEvent:
        """Update an existing event."""
        cal_id = calendar_id or "primary"
        body: dict[str, Any] = {}

        if "title" in updates:
            body["summary"] = updates["title"]
        if "description" in updates:
            body["description"] = updates["description"]
        if "start_time" in updates:
            body["start"] = {"dateTime": updates["start_time"].isoformat()}  # type: ignore
        if "end_time" in updates:
            body["end"] = {"dateTime": updates["end_time"].isoformat()}  # type: ignore
        if "location" in updates:
            body["location"] = updates["location"]

        params = {"sendUpdates": "all" if send_notifications else "none"}
        data = await self._api_patch(f"/calendars/{cal_id}/events/{event_id}?{httpx.QueryParams(params)}", body)
        return self._parse_event(data, cal_id)

    async def delete_event(
        self,
        event_id: str,
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> None:
        """Delete an event."""
        cal_id = calendar_id or "primary"
        params = {"sendUpdates": "all" if send_notifications else "none"}
        await self._api_delete(f"/calendars/{cal_id}/events/{event_id}?{httpx.QueryParams(params)}")

    # --- Attendee Operations ---

    async def respond_to_event(
        self,
        event_id: str,
        response: AttendeeResponse,
        calendar_id: str | None = None,
        comment: str | None = None,
    ) -> CalendarEvent:
        """Respond to an event invitation."""
        cal_id = calendar_id or "primary"
        event = await self.get_event(event_id, cal_id)
        if not event:
            raise ValueError(f"Event {event_id} not found")

        status_map = {
            AttendeeResponse.ACCEPTED: "accepted",
            AttendeeResponse.DECLINED: "declined",
            AttendeeResponse.TENTATIVE: "tentative",
            AttendeeResponse.NEEDS_ACTION: "needsAction",
        }

        body: dict[str, Any] = {"attendees": []}
        for att in event.attendees:
            att_data: dict[str, Any] = {"email": att.email}
            if att.is_self:
                att_data["responseStatus"] = status_map[response]
                if comment:
                    att_data["comment"] = comment
            else:
                att_data["responseStatus"] = status_map[att.response_status]
            body["attendees"].append(att_data)

        data = await self._api_patch(f"/calendars/{cal_id}/events/{event_id}", body)
        return self._parse_event(data, cal_id)

    async def add_attendees(
        self,
        event_id: str,
        attendees: list[Attendee],
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> CalendarEvent:
        """Add attendees to an event."""
        cal_id = calendar_id or "primary"
        event = await self.get_event(event_id, cal_id)
        if not event:
            raise ValueError(f"Event {event_id} not found")

        existing_emails = {a.email for a in event.attendees}
        new_attendees = [
            {"email": a.email, "displayName": a.display_name, "optional": a.is_optional}
            for a in attendees
            if a.email not in existing_emails
        ]

        if not new_attendees:
            return event

        body = {
            "attendees": [
                {"email": a.email, "displayName": a.display_name}
                for a in event.attendees
            ] + new_attendees
        }

        params = {"sendUpdates": "all" if send_notifications else "none"}
        data = await self._api_patch(f"/calendars/{cal_id}/events/{event_id}?{httpx.QueryParams(params)}", body)
        return self._parse_event(data, cal_id)

    async def remove_attendee(
        self,
        event_id: str,
        email: str,
        calendar_id: str | None = None,
        send_notifications: bool = True,
    ) -> CalendarEvent:
        """Remove an attendee from an event."""
        cal_id = calendar_id or "primary"
        event = await self.get_event(event_id, cal_id)
        if not event:
            raise ValueError(f"Event {event_id} not found")

        body = {
            "attendees": [
                {"email": a.email, "displayName": a.display_name}
                for a in event.attendees
                if a.email != email
            ]
        }

        params = {"sendUpdates": "all" if send_notifications else "none"}
        data = await self._api_patch(f"/calendars/{cal_id}/events/{event_id}?{httpx.QueryParams(params)}", body)
        return self._parse_event(data, cal_id)

    # --- Free/Busy ---

    async def get_free_busy(self, request: FreeBusyRequest) -> list[FreeBusySlot]:
        """Get free/busy information for attendees."""
        items: list[dict[str, str]] = [{"id": email} for email in request.attendee_emails]
        if request.calendar_ids:
            items.extend([{"id": cal_id} for cal_id in request.calendar_ids])
        body: dict[str, Any] = {
            "timeMin": request.start_time.isoformat(),
            "timeMax": request.end_time.isoformat(),
            "items": items,
        }

        data = await self._api_post("/freeBusy", body)
        slots: list[FreeBusySlot] = []
        for cal_id, info in data.get("calendars", {}).items():
            for busy in info.get("busy", []):
                slots.append(FreeBusySlot(
                    email=cal_id,
                    start=datetime.fromisoformat(busy["start"].replace("Z", "+00:00")),
                    end=datetime.fromisoformat(busy["end"].replace("Z", "+00:00")),
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
        request = FreeBusyRequest(
            start_time=start_time,
            end_time=end_time,
            attendee_emails=attendee_emails,
        )
        busy_slots = await self.get_free_busy(request)

        busy_intervals: list[tuple[datetime, datetime]] = [
            (slot.start, slot.end) for slot in busy_slots
        ]
        busy_intervals.sort(key=lambda x: x[0])

        merged: list[tuple[datetime, datetime]] = []
        for start, end in busy_intervals:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        available: list[tuple[datetime, datetime]] = []
        current = start_time
        duration = timedelta(minutes=duration_minutes)

        for busy_start, busy_end in merged:
            if current + duration <= busy_start:
                slot_start = current
                slot_end = slot_start + duration
                if working_hours_only:
                    if 9 <= slot_start.hour < 18 and slot_start.weekday() < 5:
                        available.append((slot_start, slot_end))
                else:
                    available.append((slot_start, slot_end))
            current = max(current, busy_end)

        if current + duration <= end_time:
            slot_start = current
            slot_end = slot_start + duration
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
        cal_id = calendar_id or "primary"
        params: dict[str, Any] = {}
        if sync_token:
            params["syncToken"] = sync_token
        else:
            params["timeMin"] = (datetime.now(UTC) - timedelta(days=30)).isoformat()

        data = await self._api_get(f"/calendars/{cal_id}/events", params)
        events = [self._parse_event(item, cal_id) for item in data.get("items", [])]
        new_token = data.get("nextSyncToken", "")
        return events, new_token

    # --- Parsing ---

    def _parse_event(self, data: dict[str, Any], calendar_id: str) -> CalendarEvent:
        """Parse Google Calendar event into domain model."""
        start_data = data.get("start", {})
        end_data = data.get("end", {})

        is_all_day = "date" in start_data
        if is_all_day:
            start_time = datetime.fromisoformat(start_data["date"]).replace(tzinfo=UTC)
            end_time = datetime.fromisoformat(end_data["date"]).replace(tzinfo=UTC)
        else:
            start_str = start_data.get("dateTime", "")
            end_str = end_data.get("dateTime", "")
            start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))

        tz = start_data.get("timeZone", "UTC")

        status_map = {
            "confirmed": EventStatus.CONFIRMED,
            "tentative": EventStatus.TENTATIVE,
            "cancelled": EventStatus.CANCELLED,
        }
        status = status_map.get(data.get("status", "confirmed"), EventStatus.CONFIRMED)

        location = None
        if data.get("location"):
            location = EventLocation(display_name=data["location"])
        if data.get("conferenceData"):
            entry_points = data["conferenceData"].get("entryPoints", [])
            for ep in entry_points:
                if ep.get("entryPointType") == "video":
                    if location:
                        location = EventLocation(
                            display_name=location.display_name,
                            conference_url=ep.get("uri"),
                            conference_type=data["conferenceData"].get("conferenceSolution", {}).get("name"),
                        )
                    else:
                        location = EventLocation(
                            display_name="Video Conference",
                            conference_url=ep.get("uri"),
                        )
                    break

        organizer = None
        if data.get("organizer"):
            org_data = data["organizer"]
            organizer = Attendee(
                email=org_data.get("email", ""),
                display_name=org_data.get("displayName"),
                is_organizer=True,
                is_self=org_data.get("self", False),
            )

        attendees: list[Attendee] = []
        response_map = {
            "accepted": AttendeeResponse.ACCEPTED,
            "declined": AttendeeResponse.DECLINED,
            "tentative": AttendeeResponse.TENTATIVE,
            "needsAction": AttendeeResponse.NEEDS_ACTION,
        }
        for att_data in data.get("attendees", []):
            attendees.append(Attendee(
                email=att_data.get("email", ""),
                display_name=att_data.get("displayName"),
                response_status=response_map.get(att_data.get("responseStatus", ""), AttendeeResponse.NEEDS_ACTION),
                is_optional=att_data.get("optional", False),
                is_organizer=att_data.get("organizer", False),
                is_self=att_data.get("self", False),
                comment=att_data.get("comment"),
            ))

        recurrence = None
        is_recurring = bool(data.get("recurrence"))
        if data.get("recurrence"):
            recurrence = self._parse_recurrence(data["recurrence"])

        attachments: list[EventAttachment] = []
        for att_data in data.get("attachments", []):
            attachments.append(EventAttachment(
                file_id=att_data.get("fileId", ""),
                title=att_data.get("title", "Untitled"),
                mime_type=att_data.get("mimeType"),
                file_url=att_data.get("fileUrl"),
                icon_link=att_data.get("iconLink"),
            ))

        return CalendarEvent(
            id=uuid4(),
            created_at=start_time,
            updated_at=None,
            title=data.get("summary", "(No Title)"),
            description=data.get("description"),
            status=status,
            start_time=start_time,
            end_time=end_time,
            is_all_day=is_all_day,
            timezone=tz,
            is_recurring=is_recurring,
            recurrence=recurrence,
            recurring_event_id=None,
            is_exception=bool(data.get("recurringEventId")),
            location=location,
            organizer=organizer,
            attendees=attendees,
            calendar_id=calendar_id,
            calendar_name=None,
            visibility=data.get("visibility", "default"),
            busy_status="busy" if data.get("transparency") != "transparent" else "free",
            reminders=[],
            is_private=data.get("visibility") == "private",
            is_cancelled=status == EventStatus.CANCELLED,
            attachments=attachments,
            can_edit=True,
            can_respond=True,
            provider_ref=ProviderReference(
                provider_name="google",
                external_id=data["id"],
                external_url=data.get("htmlLink"),
                raw_data={"account": self.account_id},
            ),
        )

    def _parse_recurrence(self, rrules: list[str]) -> EventRecurrence | None:
        """Parse RRULE strings into EventRecurrence."""
        for rule in rrules:
            if rule.startswith("RRULE:"):
                rule = rule[6:]
                parts = dict(p.split("=") for p in rule.split(";") if "=" in p)

                freq_map = {
                    "DAILY": RecurrenceFrequency.DAILY,
                    "WEEKLY": RecurrenceFrequency.WEEKLY,
                    "MONTHLY": RecurrenceFrequency.MONTHLY,
                    "YEARLY": RecurrenceFrequency.YEARLY,
                }
                freq = freq_map.get(parts.get("FREQ", ""), RecurrenceFrequency.WEEKLY)

                return EventRecurrence(
                    frequency=freq,
                    interval=int(parts.get("INTERVAL", 1)),
                    count=int(parts["COUNT"]) if "COUNT" in parts else None,
                    by_day=parts.get("BYDAY", "").split(",") if "BYDAY" in parts else [],
                )
        return None

    async def get_attachment_content(self, file_id: str) -> str | None:
        """Fetch content of a Google Drive attachment (Google Doc as plain text).

        Args:
            file_id: Google Drive file ID

        Returns:
            Text content of the file, or None if not accessible
        """
        drive_api = "https://www.googleapis.com/drive/v3"

        # Exporting a long Doc (notes + transcript) easily takes over httpx's 5s default.
        async with httpx.AsyncClient(timeout=DRIVE_EXPORT_TIMEOUT) as client:
            try:
                response = await client.get(
                    f"{drive_api}/files/{file_id}/export",
                    headers=self._headers(),
                    params={"mimeType": "text/plain"},
                )

                if response.status_code == 401:
                    if await self._refresh_access_token():
                        response = await client.get(
                            f"{drive_api}/files/{file_id}/export",
                            headers=self._headers(),
                            params={"mimeType": "text/plain"},
                        )
            except httpx.TimeoutException:
                logger.warning("Timed out fetching attachment", file_id=file_id)
                return None

            if response.status_code == 200:
                return response.text

            reason = drive_unavailable_reason(response)
            if reason:
                self.drive_unavailable = reason
            logger.warning(
                "Failed to fetch attachment",
                file_id=file_id,
                status=response.status_code,
                reason=reason,
            )
            return None


def drive_unavailable_reason(response: httpx.Response) -> str | None:
    """Why Drive is unusable for every file, or None when only this file failed."""
    if response.status_code != 403:
        return None
    body = response.text
    if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in body or "insufficientPermissions" in body:
        return (
            "the Google token lacks the drive.readonly scope: reconnect the account "
            "from the workspace (Cuentas) to grant Drive access"
        )
    if "SERVICE_DISABLED" in body or "accessNotConfigured" in body:
        return "the Google Drive API is disabled in the OAuth app's Google Cloud project"
    return None


async def test_google_calendar_connection(
    credential_manager: CredentialManager, account_id: str
) -> dict[str, Any]:
    """Test Google Calendar connection and return account info."""
    provider = GoogleCalendarProvider(credential_manager, account_id)

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
