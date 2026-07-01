"""Tests for domain models."""

from datetime import datetime, timedelta
from uuid import uuid4

from executive_copilot.models import (
    CalendarEvent,
    Email,
    EmailAddress,
    Organization,
    OrganizationType,
    Person,
    Task,
    TaskStatus,
)
from executive_copilot.models.organization import OrganizationDomains
from executive_copilot.models.person import ContactInfo


class TestEmailAddress:
    """Tests for EmailAddress value object."""

    def test_str_with_display_name(self) -> None:
        addr = EmailAddress(address="test@example.com", display_name="Test User")
        assert str(addr) == "Test User <test@example.com>"

    def test_str_without_display_name(self) -> None:
        addr = EmailAddress(address="test@example.com")
        assert str(addr) == "test@example.com"

    def test_domain_extraction(self) -> None:
        addr = EmailAddress(address="user@subdomain.example.com")
        assert addr.domain == "subdomain.example.com"


class TestOrganization:
    """Tests for Organization model."""

    def test_email_belongs_to_primary_domain(self) -> None:
        org = Organization(
            id=uuid4(),
            created_at=datetime.utcnow(),
            name="Test Org",
            organization_type=OrganizationType.CLIENT,
            domains=OrganizationDomains(primary="example.com"),
        )
        assert org.email_belongs_to("user@example.com")
        assert not org.email_belongs_to("user@other.com")

    def test_email_belongs_to_alias_domain(self) -> None:
        org = Organization(
            id=uuid4(),
            created_at=datetime.utcnow(),
            name="Test Org",
            organization_type=OrganizationType.CLIENT,
            domains=OrganizationDomains(
                primary="example.com",
                aliases=["example.org", "example.net"],
            ),
        )
        assert org.email_belongs_to("user@example.org")
        assert org.email_belongs_to("user@example.net")

    def test_display_name_uses_short_name(self) -> None:
        org = Organization(
            id=uuid4(),
            created_at=datetime.utcnow(),
            name="Example Organization Inc.",
            short_name="Example",
            organization_type=OrganizationType.CLIENT,
        )
        assert org.display_name == "Example"

    def test_display_name_falls_back_to_name(self) -> None:
        org = Organization(
            id=uuid4(),
            created_at=datetime.utcnow(),
            name="Example Organization",
            organization_type=OrganizationType.CLIENT,
        )
        assert org.display_name == "Example Organization"


class TestPerson:
    """Tests for Person model."""

    def test_full_name(self) -> None:
        person = Person(
            id=uuid4(),
            created_at=datetime.utcnow(),
            first_name="John",
            last_name="Doe",
        )
        assert person.full_name == "John Doe"

    def test_full_name_without_last_name(self) -> None:
        person = Person(
            id=uuid4(),
            created_at=datetime.utcnow(),
            first_name="John",
        )
        assert person.full_name == "John"

    def test_has_email_primary(self) -> None:
        person = Person(
            id=uuid4(),
            created_at=datetime.utcnow(),
            first_name="John",
            contact=ContactInfo(email="john@example.com"),
        )
        assert person.has_email("john@example.com")
        assert person.has_email("JOHN@EXAMPLE.COM")
        assert not person.has_email("jane@example.com")

    def test_has_email_secondary(self) -> None:
        person = Person(
            id=uuid4(),
            created_at=datetime.utcnow(),
            first_name="John",
            contact=ContactInfo(
                email="john@work.com",
                secondary_emails=["john@personal.com"],
            ),
        )
        assert person.has_email("john@personal.com")


class TestEmail:
    """Tests for Email model."""

    def test_all_recipients(self) -> None:
        email = Email(
            id=uuid4(),
            created_at=datetime.utcnow(),
            sender=EmailAddress(address="sender@test.com"),
            to_recipients=[EmailAddress(address="to@test.com")],
            cc_recipients=[EmailAddress(address="cc@test.com")],
            bcc_recipients=[EmailAddress(address="bcc@test.com")],
            subject="Test",
            received_at=datetime.utcnow(),
        )
        assert len(email.all_recipients) == 3

    def test_participant_emails(self) -> None:
        email = Email(
            id=uuid4(),
            created_at=datetime.utcnow(),
            sender=EmailAddress(address="sender@test.com"),
            to_recipients=[EmailAddress(address="to@test.com")],
            subject="Test",
            received_at=datetime.utcnow(),
        )
        emails = email.participant_emails
        assert "sender@test.com" in emails
        assert "to@test.com" in emails


class TestTask:
    """Tests for Task model."""

    def test_is_overdue(self) -> None:
        task = Task(
            id=uuid4(),
            created_at=datetime.utcnow(),
            title="Overdue Task",
            due_date=datetime.utcnow() - timedelta(days=1),
        )
        assert task.is_overdue

    def test_not_overdue_if_completed(self) -> None:
        task = Task(
            id=uuid4(),
            created_at=datetime.utcnow(),
            title="Completed Task",
            status=TaskStatus.COMPLETED,
            due_date=datetime.utcnow() - timedelta(days=1),
        )
        assert not task.is_overdue

    def test_mark_completed(self) -> None:
        task = Task(
            id=uuid4(),
            created_at=datetime.utcnow(),
            title="Task",
        )
        completed = task.mark_completed()
        assert completed.status == TaskStatus.COMPLETED
        assert completed.completed_at is not None


class TestCalendarEvent:
    """Tests for CalendarEvent model."""

    def test_duration(self) -> None:
        start = datetime.utcnow()
        end = start + timedelta(hours=1, minutes=30)
        event = CalendarEvent(
            id=uuid4(),
            created_at=datetime.utcnow(),
            title="Meeting",
            start_time=start,
            end_time=end,
        )
        assert event.duration_minutes == 90

    def test_is_past(self) -> None:
        event = CalendarEvent(
            id=uuid4(),
            created_at=datetime.utcnow(),
            title="Past Meeting",
            start_time=datetime.utcnow() - timedelta(hours=2),
            end_time=datetime.utcnow() - timedelta(hours=1),
        )
        assert event.is_past
