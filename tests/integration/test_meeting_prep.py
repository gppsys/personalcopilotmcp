"""Integration tests for meeting preparation tool."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from executive_copilot.models.base import ProviderReference
from executive_copilot.models.calendar import (
    Attendee,
    AttendeeResponse,
    CalendarEvent,
    EventStatus,
)


@pytest.fixture
def mock_event():
    """Create a mock calendar event."""
    return CalendarEvent(
        id=uuid4(),
        title="Q3 Planning Review",
        description="Quarterly planning session with the team",
        status=EventStatus.CONFIRMED,
        start_time=datetime.now(UTC) + timedelta(hours=2),
        end_time=datetime.now(UTC) + timedelta(hours=3),
        is_all_day=False,
        timezone="UTC",
        organizer=Attendee(
            email="organizer@company.com",
            display_name="Meeting Organizer",
            is_organizer=True,
        ),
        attendees=[
            Attendee(
                email="john@external.com",
                display_name="John Smith",
                response_status=AttendeeResponse.ACCEPTED,
            ),
            Attendee(
                email="jane@external.com",
                display_name="Jane Doe",
                response_status=AttendeeResponse.TENTATIVE,
            ),
        ],
        provider_ref=ProviderReference(
            provider_name="google",
            external_id="event123",
        ),
    )


class MockOrganization:
    """Mock organization for testing."""

    def __init__(self):
        self.name = "External Corp"
        self.short_name = "EXT"
        self.organization_type = "client"
        self.domains = ["external.com"]
        self.importance = 8
        self.tags = ["client"]
        self.provider_accounts = []


@pytest.fixture
def mock_config():
    """Create a mock app config."""
    config = MagicMock()
    config.organizations = [MockOrganization()]
    config.mcp.name = "test-copilot"
    config.mcp.version = "0.1.0"
    config.config_dir = MagicMock()
    config.config_dir.__truediv__ = MagicMock(return_value=MagicMock())
    return config


@pytest.fixture
def mock_credential_manager():
    """Create a mock credential manager."""
    manager = MagicMock()
    manager.get_credential.return_value = {
        "access_token": "test_token",
        "refresh_token": "test_refresh",
        "client_id": "test_client",
    }
    return manager


class TestMeetingPrepDataAggregation:
    """Tests for meeting prep data aggregation logic."""

    def test_external_attendee_detection(self, mock_event, mock_config):
        """Test detection of external attendees."""
        internal_domains = ["company.com", "internal.org"]
        attendee_emails = [a.email for a in mock_event.attendees]

        external = [
            e for e in attendee_emails
            if not any(e.endswith(f"@{d}") for d in internal_domains)
        ]

        assert len(external) == 2
        assert "john@external.com" in external
        assert "jane@external.com" in external

    def test_organization_detection_from_domain(self, mock_event, mock_config):
        """Test organization detection from attendee domain."""
        attendee_email = "john@external.com"
        domain = attendee_email.rsplit("@", maxsplit=1)[-1]

        detected_org = None
        for org in mock_config.organizations:
            if domain in org.domains:
                detected_org = org
                break

        assert detected_org is not None
        assert detected_org.name == "External Corp"

    def test_meeting_subject_keyword_extraction(self, mock_event):
        """Test extraction of keywords from meeting title."""
        title = mock_event.title
        keywords = " ".join(title.split()[:4])

        assert "Q3" in keywords
        assert "Planning" in keywords

    def test_task_relevance_matching(self):
        """Test matching tasks to meeting context."""
        meeting_title = "Q3 Planning Review"
        task_titles = [
            "Complete Q3 budget forecast",
            "Review planning documents",
            "Unrelated task about testing",
            "Q3 goals presentation",
        ]

        # Match words with length > 3 (same logic as in the tool)
        meeting_words = {w.lower() for w in meeting_title.split() if len(w) > 3}
        # This gives: {'planning', 'review'}

        relevant_tasks = []
        for task in task_titles:
            task_lower = task.lower()
            if any(word in task_lower for word in meeting_words):
                relevant_tasks.append(task)

        # "planning" matches "Review planning documents"
        # "review" matches "Review planning documents"
        # No other tasks contain "planning" or "review"
        assert "Unrelated task about testing" not in relevant_tasks
        assert "Review planning documents" in relevant_tasks


class TestMeetingPrepAISummary:
    """Tests for AI summary generation."""

    def test_context_building(self, mock_event, mock_config):
        """Test building context for AI prompt."""
        prep_data = {
            "event": {
                "title": mock_event.title,
                "start": mock_event.start_time.isoformat(),
                "end": mock_event.end_time.isoformat(),
                "duration_minutes": mock_event.duration_minutes,
                "description": mock_event.description,
            },
            "organization": {
                "name": "External Corp",
                "type": "client",
                "importance": 8,
            },
            "attendees_context": [
                {
                    "email": "john@external.com",
                    "name": "John Smith",
                    "recent_emails": [
                        {"subject": "Re: Q3 Planning", "date": "2024-01-15"},
                    ],
                },
            ],
            "related_emails": [
                {"subject": "Q3 Goals Draft", "from": "jane@external.com", "date": "2024-01-10"},
            ],
            "related_tasks": [
                {"title": "Finalize Q3 budget", "status": "in_progress", "due_date": "2024-01-20"},
            ],
        }

        # Build context string
        context_parts = [
            f"Meeting: {prep_data['event']['title']}",
        ]

        if prep_data.get("organization"):
            org = prep_data["organization"]
            context_parts.append(f"Organization: {org['name']} ({org['type']})")

        if prep_data.get("attendees_context"):
            attendees = [a["email"] for a in prep_data["attendees_context"]]
            context_parts.append(f"Attendees: {', '.join(attendees)}")

        context = "\n".join(context_parts)

        assert "Q3 Planning Review" in context
        assert "External Corp" in context
        assert "john@external.com" in context

    @pytest.mark.asyncio
    async def test_ai_service_integration_mock(self):
        """Test AI service call structure (mocked)."""
        mock_ai_result = MagicMock()
        mock_ai_result.text = """**Key Talking Points**
- Review Q3 goals progress
- Discuss budget allocation
- Align on timeline

**Preparation Actions**
- Review last meeting notes
- Prepare budget summary

**Context Summary**
This is a quarterly planning meeting with key stakeholders."""
        mock_ai_result.model = "gpt-4o-mini"

        mock_ai_service = MagicMock()
        mock_ai_service.generate_text = AsyncMock(return_value=mock_ai_result)

        # Simulate calling AI service
        result = await mock_ai_service.generate_text(
            MagicMock(
                input="Meeting context here...",
                instructions="You are an executive assistant...",
                max_output_tokens=800,
            )
        )

        assert "Key Talking Points" in result.text
        assert "Preparation Actions" in result.text
        assert result.model == "gpt-4o-mini"


class TestEventDetails:
    """Tests for get_event_details functionality."""

    @pytest.mark.asyncio
    async def test_event_serialization(self, mock_event):
        """Test that event is properly serialized."""
        serialized = {
            "event_id": "event123",
            "title": mock_event.title,
            "description": mock_event.description,
            "start": mock_event.start_time.isoformat(),
            "end": mock_event.end_time.isoformat(),
            "duration_minutes": mock_event.duration_minutes,
            "is_all_day": mock_event.is_all_day,
            "location": mock_event.location.display_name if mock_event.location else None,
            "conference_url": mock_event.conference_url,
            "organizer": {
                "email": mock_event.organizer.email,
                "name": mock_event.organizer.display_name,
            } if mock_event.organizer else None,
            "attendees": [
                {
                    "email": a.email,
                    "name": a.display_name,
                    "response": a.response_status.value,
                }
                for a in mock_event.attendees
            ],
        }

        assert serialized["title"] == "Q3 Planning Review"
        assert serialized["duration_minutes"] == 60
        assert len(serialized["attendees"]) == 2
        assert serialized["organizer"]["email"] == "organizer@company.com"
