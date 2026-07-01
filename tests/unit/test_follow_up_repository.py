"""Unit tests for FollowUpRepository."""

from datetime import datetime, timedelta

from executive_copilot.models.communication import FollowUp, FollowUpPriority, FollowUpStatus


class TestFollowUpModel:
    """Test FollowUp domain model."""

    def test_create_follow_up(self):
        """Test creating a follow-up."""
        follow_up = FollowUp(
            title="Follow up with John",
            description="Discuss Q3 planning",
            status=FollowUpStatus.PENDING,
            priority=FollowUpPriority.HIGH,
            with_person_email="john@example.com",
            with_person_name="John Doe",
        )

        assert follow_up.title == "Follow up with John"
        assert follow_up.status == FollowUpStatus.PENDING
        assert follow_up.priority == FollowUpPriority.HIGH
        assert follow_up.auto_detected is False
        assert follow_up.is_overdue is False

    def test_is_overdue(self):
        """Test overdue detection."""
        past_date = datetime.utcnow() - timedelta(days=1)
        follow_up = FollowUp(
            title="Overdue follow-up",
            status=FollowUpStatus.PENDING,
            due_date=past_date,
        )

        assert follow_up.is_overdue is True

    def test_not_overdue_when_completed(self):
        """Test completed follow-ups are not overdue."""
        past_date = datetime.utcnow() - timedelta(days=1)
        follow_up = FollowUp(
            title="Completed follow-up",
            status=FollowUpStatus.COMPLETED,
            due_date=past_date,
        )

        assert follow_up.is_overdue is False

    def test_not_overdue_without_due_date(self):
        """Test follow-ups without due date are not overdue."""
        follow_up = FollowUp(
            title="No due date",
            status=FollowUpStatus.PENDING,
        )

        assert follow_up.is_overdue is False

    def test_mark_completed(self):
        """Test marking a follow-up as completed."""
        follow_up = FollowUp(
            title="To complete",
            status=FollowUpStatus.PENDING,
        )

        completed = follow_up.mark_completed()

        assert completed.status == FollowUpStatus.COMPLETED
        assert completed.completed_at is not None
        # Original unchanged
        assert follow_up.status == FollowUpStatus.PENDING

    def test_snooze_until(self):
        """Test snoozing a follow-up."""
        follow_up = FollowUp(
            title="To snooze",
            status=FollowUpStatus.PENDING,
        )
        snooze_date = datetime.utcnow() + timedelta(days=7)

        snoozed = follow_up.snooze_until(snooze_date)

        assert snoozed.status == FollowUpStatus.SNOOZED
        assert snoozed.snoozed_until == snooze_date
        assert snoozed.is_snoozed is True

    def test_priority_values(self):
        """Test priority enum values."""
        assert FollowUpPriority.LOW.value == "low"
        assert FollowUpPriority.MEDIUM.value == "medium"
        assert FollowUpPriority.HIGH.value == "high"
        assert FollowUpPriority.URGENT.value == "urgent"

    def test_with_tags(self):
        """Test follow-up with tags."""
        follow_up = FollowUp(
            title="Tagged follow-up",
            tags=["sales", "important", "q3"],
        )

        assert "sales" in follow_up.tags
        assert len(follow_up.tags) == 3
