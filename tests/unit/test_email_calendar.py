"""Tests for e-mail -> calendar helpers and OAuth sibling resolution."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from executive_copilot.config import AppConfig
from executive_copilot.config.models import CalendarDefaultsConfig
from executive_copilot.credentials import CredentialManager
from executive_copilot.services.email_calendar import (
    EmailCalendarService,
    graph_event_fields,
    parse_local,
    titles_match,
)

MX = ZoneInfo("America/Mexico_City")


def test_parse_local_all_day_and_timed() -> None:
    start, all_day = parse_local("2026-10-10", MX)
    assert all_day and start.tzinfo == MX and start.hour == 0
    start, all_day = parse_local("2026-10-10T09:30", MX)
    assert not all_day and (start.hour, start.minute) == (9, 30) and start.tzinfo == MX


def test_titles_match() -> None:
    assert titles_match("Y4A&B Elderly Visit", "Elderly Visit (school)")
    assert not titles_match("Dentista", "Junta de consejo")


def test_graph_event_fields_converts_to_local() -> None:
    raw = {
        "@odata.type": "#microsoft.graph.eventMessageRequest",
        "subject": "Sync",
        "meetingMessageType": "meetingRequest",
        "startDateTime": {"dateTime": "2026-10-10T16:00:00.0000000", "timeZone": "UTC"},
        "endDateTime": {"dateTime": "2026-10-10T17:00:00.0000000", "timeZone": "UTC"},
        "location": {"displayName": "Teams"},
    }
    fields = graph_event_fields(raw, MX)
    assert fields is not None
    assert fields["start"] == "2026-10-10T10:00" and fields["end"] == "2026-10-10T11:00"
    assert fields["location"] == "Teams" and fields["needs_calendar"]
    assert graph_event_fields({"@odata.type": "#microsoft.graph.message"}, MX) is None


class FakeCalendar:
    def __init__(self) -> None:
        self.created: Any = None

    async def create_event(self, event: Any) -> Any:
        self.created = event

        class Ref:
            external_id = "evt123"
            external_url = "https://calendar.google.com/event?eid=evt123"

        class Created:
            id = "uuid"
            title = event.title
            start_time = event.start_time
            end_time = event.end_time
            provider_ref = Ref()

        return Created()


def make_service(fake: FakeCalendar) -> EmailCalendarService:
    config = AppConfig(
        calendar_defaults=CalendarDefaultsConfig(account_id="me@work.com"),
        organizations=[
            {
                "name": "Personal",
                "organization_type": "personal",
                "provider_accounts": [
                    {"provider_type": "email", "provider_name": "outlook", "account_id": "me@outlook.com"}
                ],
            }
        ],
    )
    service = EmailCalendarService(config, credential_manager=None)
    service._calendar_provider = lambda *_: fake  # type: ignore[method-assign]
    return service


@pytest.mark.asyncio
async def test_create_event_defaults_to_work_calendar_and_duration() -> None:
    fake = FakeCalendar()
    service = make_service(fake)
    out = await service.create_event(title="Dentista", start="2026-10-10T09:00")
    assert out["id"] == "evt123"
    assert out["calendar"] == {"provider": "google", "account": "me@work.com", "calendar_id": "primary"}
    assert fake.created.end_time - fake.created.start_time == timedelta(minutes=60)
    assert fake.created.timezone == "America/Mexico_City"
    assert fake.created.send_notifications is False


@pytest.mark.asyncio
async def test_create_all_day_event_end_is_exclusive() -> None:
    fake = FakeCalendar()
    await make_service(fake).create_event(title="Kermés", start="2026-10-10", end="2026-10-11")
    assert fake.created.is_all_day
    assert (fake.created.end_time - fake.created.start_time) == timedelta(days=2)


@pytest.mark.asyncio
async def test_add_to_calendar_from_personal_mail_is_private() -> None:
    fake = FakeCalendar()
    out = await make_service(fake).add_to_calendar(
        message_id="m1", account="me@outlook.com", title="Visita escolar",
        start="2026-10-12T08:00", sender="school@x.mx", subject="Invitation",
    )
    assert out["created"]["private"] is True
    assert fake.created.visibility == "private"
    assert "Origen: correo de school@x.mx" in fake.created.description


@pytest.mark.asyncio
async def test_create_event_rejects_end_before_start() -> None:
    with pytest.raises(ValueError):
        await make_service(FakeCalendar()).create_event(
            title="x", start="2026-10-10T10:00", end="2026-10-10T09:00"
        )


def test_oauth_client_resolved_from_sibling_credential(tmp_path: Path) -> None:
    cm = CredentialManager(tmp_path)
    cm.save_credential("email", "outlook", "me@outlook.com", access_token="a", refresh_token="r1")
    cm.save_credential("calendar", "outlook", "me@outlook.com", client_id="cid", client_secret="sec")

    client = cm.get_oauth_client("microsoft", "me@outlook.com")
    assert client == {"client_id": "cid", "client_secret": "sec", "tenant_id": "common"}
    assert cm.get_oauth_refresh_token("microsoft", "me@outlook.com") == "r1"

    cm.save_oauth_tokens("microsoft", "me@outlook.com", access_token="new", refresh_token="r2")
    for provider_type in ("email", "calendar"):
        cred = cm.get_credential(provider_type, "outlook", "me@outlook.com")
        assert cred is not None and cred["access_token"] == "new" and cred["refresh_token"] == "r2"
    assert cm.get_credential("calendar", "outlook", "me@outlook.com")["client_id"] == "cid"  # type: ignore[index]
    assert cm.get_credential("document", "onedrive", "me@outlook.com") is None


def test_config_example_files_are_not_merged(tmp_path: Path) -> None:
    from executive_copilot.config.loader import ConfigLoader

    (tmp_path / "config.yaml").write_text("debug: false\n")
    (tmp_path / "config.local.example.yaml").write_text("debug: true\n")
    assert ConfigLoader(tmp_path).load().debug is False


def test_now_is_timezone_aware() -> None:
    assert datetime.now(MX).tzinfo is not None


def test_filter_drops_past_and_low_confidence() -> None:
    service = make_service(FakeCalendar())
    tomorrow = (datetime.now(MX) + timedelta(days=1)).date().isoformat()
    proposals = [
        {"start": "2020-01-01", "confidence": 0.99},
        {"start": f"{tomorrow}T10:00", "confidence": 0.4},
        {"start": f"{tomorrow}T10:00", "confidence": 0.9},
        {"start": None, "confidence": 0.3},  # undated keyword hit: user decides
    ]
    kept, dropped = service._filter(proposals)
    assert dropped == 2
    assert [p["confidence"] for p in kept] == [0.9, 0.3]
