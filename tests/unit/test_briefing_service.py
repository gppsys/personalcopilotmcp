"""Tests for BriefingService."""


from executive_copilot.services.briefing import BriefingService


class TestBuildSummaryText:
    """Tests for the summary text builder."""

    def test_summary_with_meetings(self):
        """Test summary with meetings."""
        service = BriefingService.__new__(BriefingService)
        summary = {"meetings": 3, "pending_replies": 0, "overdue_items": 0,
                   "urgent_emails": 0, "waiting_on_others": 0, "unread_emails": 0,
                   "my_commitments": 0}
        result = service._build_summary_text(summary)
        assert "3 meetings" in result

    def test_summary_with_single_meeting(self):
        """Test singular form."""
        service = BriefingService.__new__(BriefingService)
        summary = {"meetings": 1, "pending_replies": 0, "overdue_items": 0,
                   "urgent_emails": 0, "waiting_on_others": 0, "unread_emails": 0,
                   "my_commitments": 0}
        result = service._build_summary_text(summary)
        assert "1 meeting" in result
        assert "meetings" not in result

    def test_summary_with_multiple_items(self):
        """Test summary with multiple categories."""
        service = BriefingService.__new__(BriefingService)
        summary = {"meetings": 3, "pending_replies": 2, "overdue_items": 1,
                   "urgent_emails": 0, "waiting_on_others": 0, "unread_emails": 5,
                   "my_commitments": 0}
        result = service._build_summary_text(summary)
        assert "3 meetings" in result
        assert "2 pending replies" in result
        assert "1 overdue item" in result

    def test_summary_all_clear(self):
        """Test empty summary."""
        service = BriefingService.__new__(BriefingService)
        summary = {"meetings": 0, "pending_replies": 0, "overdue_items": 0,
                   "urgent_emails": 0, "waiting_on_others": 0, "unread_emails": 0,
                   "my_commitments": 0}
        result = service._build_summary_text(summary)
        assert "All clear" in result

    def test_summary_with_waiting_on_others(self):
        """Test waiting on others."""
        service = BriefingService.__new__(BriefingService)
        summary = {"meetings": 0, "pending_replies": 0, "overdue_items": 0,
                   "urgent_emails": 0, "waiting_on_others": 5, "unread_emails": 0,
                   "my_commitments": 0}
        result = service._build_summary_text(summary)
        assert "5 items waiting on others" in result


class TestBuildSummary:
    """Tests for the summary builder."""

    def test_build_summary_from_briefing(self):
        """Test building summary from briefing data."""
        service = BriefingService.__new__(BriefingService)
        briefing = {
            "calendar": {"total": 5},
            "emails": {"total_unread": 10, "urgent_count": 2},
            "overdue": {"total": 3},
            "pending_replies": {"total": 4},
            "delegations": {"total_waiting": 6},
            "commitments": {"i_promised": {"total": 2}},
        }
        result = service._build_summary(briefing)
        assert result["meetings"] == 5
        assert result["unread_emails"] == 10
        assert result["urgent_emails"] == 2
        assert result["overdue_items"] == 3
        assert result["pending_replies"] == 4
        assert result["waiting_on_others"] == 6
        assert result["my_commitments"] == 2

    def test_build_summary_with_missing_sections(self):
        """Test handling missing sections gracefully."""
        service = BriefingService.__new__(BriefingService)
        briefing = {"calendar": None, "emails": None}
        result = service._build_summary(briefing)
        assert result["meetings"] == 0
        assert result["unread_emails"] == 0


class TestIsUrgentEmail:
    """Tests for urgent email detection."""

    def test_urgent_in_subject(self):
        """Test detecting urgent in subject."""
        service = BriefingService.__new__(BriefingService)

        class MockEmail:
            subject = "URGENT: Need response"
            snippet = "Please review"

        assert service._is_urgent_email(MockEmail()) is True

    def test_asap_in_snippet(self):
        """Test detecting ASAP in snippet."""
        service = BriefingService.__new__(BriefingService)

        class MockEmail:
            subject = "Project update"
            snippet = "Need this asap please"

        assert service._is_urgent_email(MockEmail()) is True

    def test_not_urgent(self):
        """Test normal email."""
        service = BriefingService.__new__(BriefingService)

        class MockEmail:
            subject = "Weekly update"
            snippet = "Here's the status"

        assert service._is_urgent_email(MockEmail()) is False

    def test_deadline_keyword(self):
        """Test deadline keyword."""
        service = BriefingService.__new__(BriefingService)

        class MockEmail:
            subject = "Deadline tomorrow"
            snippet = "Please submit by EOD"

        assert service._is_urgent_email(MockEmail()) is True


class TestFormatBriefingEmail:
    """Tests for email formatting."""

    def test_format_includes_summary(self):
        """Test that email includes summary text."""
        service = BriefingService.__new__(BriefingService)
        briefing = {
            "date": "Monday, June 30, 2026",
            "summary_text": "3 meetings, 2 pending replies",
            "summary": {},
            "calendar": None,
            "overdue": None,
            "pending_replies": None,
            "delegations": None,
            "emails": None,
        }
        result = service._format_briefing_email(briefing)
        assert "3 meetings, 2 pending replies" in result
        assert "Monday, June 30, 2026" in result

    def test_format_includes_meetings(self):
        """Test that email includes meetings."""
        service = BriefingService.__new__(BriefingService)
        briefing = {
            "date": "Monday, June 30, 2026",
            "summary_text": "2 meetings",
            "summary": {},
            "calendar": {
                "events": [
                    {"title": "Team Standup", "start_time": "9:00 AM", "is_all_day": False},
                    {"title": "Client Call", "start_time": "2:00 PM", "is_all_day": False},
                ]
            },
            "overdue": None,
            "pending_replies": None,
            "delegations": None,
            "emails": None,
        }
        result = service._format_briefing_email(briefing)
        assert "TODAY'S MEETINGS" in result
        assert "Team Standup" in result
        assert "Client Call" in result
