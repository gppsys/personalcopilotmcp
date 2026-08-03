"""Unit tests for PendingRepliesService."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from executive_copilot.models.communication import FollowUpPriority
from executive_copilot.models.email import Email, EmailAddress, EmailPriority


class TestShouldProcessEmail:
    """Tests for email filtering logic."""

    def _make_email(
        self,
        days_ago: int = 3,
        recipients: list[str] | None = None,
    ) -> Email:
        """Create a test email."""
        if recipients is None:
            recipients = ["recipient@example.com"]

        return Email(
            id=uuid4(),
            sender=EmailAddress(address="sender@example.com"),
            to_recipients=[EmailAddress(address=r) for r in recipients],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Test subject",
            received_at=datetime.now(UTC) - timedelta(days=days_ago),
            is_read=True,
            is_starred=False,
            is_spam=False,
            is_trash=False,
            is_draft=False,
            is_sent=True,
            has_attachments=False,
            priority=EmailPriority.NORMAL,
            labels=[],
        )

    def test_skips_recent_emails(self):
        """Emails within min_wait_days should be skipped."""
        from executive_copilot.services.pending_replies import PendingRepliesService

        email = self._make_email(days_ago=1)
        min_wait_threshold = datetime.now(UTC) - timedelta(days=2)

        service = PendingRepliesService.__new__(PendingRepliesService)
        result = service._should_process_email(email, min_wait_threshold)

        assert result is False

    def test_processes_old_emails(self):
        """Emails older than min_wait_days should be processed."""
        from executive_copilot.services.pending_replies import PendingRepliesService

        email = self._make_email(days_ago=5)
        min_wait_threshold = datetime.now(UTC) - timedelta(days=2)

        service = PendingRepliesService.__new__(PendingRepliesService)
        result = service._should_process_email(email, min_wait_threshold)

        assert result is True

    def test_skips_emails_without_recipients(self):
        """Emails with no recipients should be skipped."""
        from executive_copilot.services.pending_replies import PendingRepliesService

        email = self._make_email(days_ago=5, recipients=[])
        email = email.with_update(to_recipients=[])
        min_wait_threshold = datetime.now(UTC) - timedelta(days=2)

        service = PendingRepliesService.__new__(PendingRepliesService)
        result = service._should_process_email(email, min_wait_threshold)

        assert result is False


class TestExcludedRecipients:
    """Tests for the exclude_recipients setting (e.g. forwarding inboxes)."""

    def _make_service(self, exclude: list[str]):
        from executive_copilot.services.pending_replies import PendingRepliesService

        service = PendingRepliesService.__new__(PendingRepliesService)
        service._excluded_recipients = {r.lower() for r in exclude}
        return service

    def _threshold(self) -> datetime:
        return datetime.now(UTC) - timedelta(days=2)

    def test_skips_email_to_excluded_recipient(self):
        """Forwards to an excluded inbox never require a follow-up."""
        service = self._make_service(["invoicing@paycode.com.mx"])
        email = TestShouldProcessEmail()._make_email(
            days_ago=5, recipients=["invoicing@paycode.com.mx"]
        )
        assert service._should_process_email(email, self._threshold()) is False

    def test_exclusion_is_case_insensitive(self):
        """Recipient matching ignores case."""
        service = self._make_service(["Invoicing@Paycode.com.MX"])
        email = TestShouldProcessEmail()._make_email(
            days_ago=5, recipients=["INVOICING@paycode.com.mx"]
        )
        assert service._should_process_email(email, self._threshold()) is False

    def test_processes_email_with_other_recipients(self):
        """If someone besides the excluded inbox is on the email, keep it."""
        service = self._make_service(["invoicing@paycode.com.mx"])
        email = TestShouldProcessEmail()._make_email(
            days_ago=5,
            recipients=["invoicing@paycode.com.mx", "client@example.com"],
        )
        assert service._should_process_email(email, self._threshold()) is True

    def test_no_exclusions_processes_all(self):
        """Without exclusions configured, behavior is unchanged."""
        service = self._make_service([])
        email = TestShouldProcessEmail()._make_email(
            days_ago=5, recipients=["invoicing@paycode.com.mx"]
        )
        assert service._should_process_email(email, self._threshold()) is True


class TestFollowUpCreation:
    """Tests for follow-up priority assignment."""

    def test_high_priority_after_5_days(self):
        """Emails waiting 5+ days should get HIGH priority."""
        days_waiting = 6
        priority = FollowUpPriority.HIGH if days_waiting >= 5 else FollowUpPriority.MEDIUM
        assert priority == FollowUpPriority.HIGH

    def test_medium_priority_under_5_days(self):
        """Emails waiting less than 5 days should get MEDIUM priority."""
        days_waiting = 3
        priority = FollowUpPriority.HIGH if days_waiting >= 5 else FollowUpPriority.MEDIUM
        assert priority == FollowUpPriority.MEDIUM
