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
        assert "1 overdue follow-up" in result

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


class TestEmailRequiresAttention:
    """Tests for email attention detection."""

    def test_question_mark_requires_attention(self):
        """Test detecting questions."""
        service = BriefingService.__new__(BriefingService)

        class MockEmail:
            subject = "Can we discuss the project?"
            snippet = "I have some concerns"

        requires, reason = service._email_requires_attention(MockEmail())
        assert requires is True
        assert reason is not None

    def test_can_you_requires_attention(self):
        """Test detecting action requests."""
        service = BriefingService.__new__(BriefingService)

        class MockEmail:
            subject = "Document review"
            snippet = "Can you review this by Friday"

        requires, reason = service._email_requires_attention(MockEmail())
        assert requires is True
        assert "requests your action" in reason

    def test_approval_requires_attention(self):
        """Test detecting approval requests."""
        service = BriefingService.__new__(BriefingService)

        class MockEmail:
            subject = "Budget approval needed"
            snippet = "Awaiting your approval for the attached"

        requires, reason = service._email_requires_attention(MockEmail())
        assert requires is True
        assert "approval" in reason

    def test_normal_email_no_attention(self):
        """Test that normal emails don't require attention."""
        service = BriefingService.__new__(BriefingService)

        class MockEmail:
            subject = "FYI: Weekly summary"
            snippet = "Here is the summary for this week."

        requires, reason = service._email_requires_attention(MockEmail())
        assert requires is False
        assert reason is None


class TestIsPromotionalEmail:
    """Tests for promotional email detection."""

    def _make_email(self, sender="ana@cliente.com", snippet="", body_text=None, labels=()):
        from types import SimpleNamespace

        return SimpleNamespace(
            sender=SimpleNamespace(address=sender),
            snippet=snippet,
            body_text=body_text,
            labels=[
                SimpleNamespace(name=label, provider_id=label) for label in labels
            ],
        )

    def test_gmail_promotions_category(self):
        """Emails in Gmail's Promotions category are promotional."""
        service = BriefingService.__new__(BriefingService)
        email = self._make_email(labels=["CATEGORY_PROMOTIONS"])
        assert service._is_promotional_email(email) is True

    def test_noreply_sender(self):
        """No-reply senders are promotional."""
        service = BriefingService.__new__(BriefingService)
        email = self._make_email(sender="no-reply@tienda.com")
        assert service._is_promotional_email(email) is True

    def test_newsletter_sender(self):
        """Newsletter senders are promotional."""
        service = BriefingService.__new__(BriefingService)
        email = self._make_email(sender="newsletter@empresa.com")
        assert service._is_promotional_email(email) is True

    def test_unsubscribe_in_body(self):
        """Unsubscribe footers mark an email as promotional."""
        service = BriefingService.__new__(BriefingService)
        email = self._make_email(
            body_text="Great deals this week! Click here to unsubscribe."
        )
        assert service._is_promotional_email(email) is True

    def test_spanish_unsubscribe_marker(self):
        """Spanish unsubscribe footers are detected."""
        service = BriefingService.__new__(BriefingService)
        email = self._make_email(
            snippet="Ofertas exclusivas. Para darse de baja haz clic aquí."
        )
        assert service._is_promotional_email(email) is True

    def test_regular_email_not_promotional(self):
        """A normal email from a person is not promotional."""
        service = BriefingService.__new__(BriefingService)
        email = self._make_email(
            sender="ana@cliente.com",
            snippet="Can you review the contract before Friday?",
            labels=["INBOX", "UNREAD"],
        )
        assert service._is_promotional_email(email) is False


class TestBuildSummaryWithTasks:
    """Tests for summary building with tasks."""

    def test_summary_includes_tasks_due_today(self):
        """Test that summary includes tasks due today."""
        service = BriefingService.__new__(BriefingService)
        summary = {"meetings": 2, "tasks_due_today": 3, "tasks_overdue": 1}
        result = service._build_summary_text(summary)
        assert "3 tasks due today" in result
        assert "1 overdue task" in result

    def test_summary_includes_emails_requiring_attention(self):
        """Test that summary includes emails requiring attention."""
        service = BriefingService.__new__(BriefingService)
        summary = {"meetings": 0, "emails_requiring_attention": 4}
        result = service._build_summary_text(summary)
        assert "4 emails needing attention" in result


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


class TestTaskToDict:
    """Tests for the enriched task mapping."""

    def _make_task(self, **overrides):
        from datetime import UTC, datetime, timedelta
        from uuid import uuid4

        from executive_copilot.models.task import Task, TaskPriority

        defaults = {
            "id": uuid4(),
            "created_at": datetime.now(UTC),
            "updated_at": None,
            "title": "Prepare board deck",
            "description": "Draft the Q3 numbers and send to finance for review.",
            "priority": TaskPriority.HIGH,
            "due_date": datetime.now(UTC) - timedelta(days=3),
            "project_name": "Board Prep",
            "assignee_name": "Gonzalo",
            "comment_count": 4,
        }
        defaults.update(overrides)
        return Task(**defaults)

    def _acc(self):
        return {"account_id": "me@example.com", "organization": "Paycode"}

    def test_overdue_task_includes_context(self):
        service = BriefingService.__new__(BriefingService)
        result = service._task_to_dict(self._make_task(), self._acc(), is_overdue=True)
        assert result["description"].startswith("Draft the Q3 numbers")
        assert result["days_overdue"] == 3
        assert result["assignee_name"] == "Gonzalo"
        assert "overdue by 3 days" in result["attention_reason"]
        assert "high priority" in result["attention_reason"]
        assert "4 comments" in result["attention_reason"]

    def test_due_today_task_reason(self):
        from datetime import UTC, datetime

        service = BriefingService.__new__(BriefingService)
        task = self._make_task(due_date=datetime.now(UTC), comment_count=0)
        result = service._task_to_dict(task, self._acc(), is_overdue=False)
        assert result["days_overdue"] == 0
        assert "due today" in result["attention_reason"]

    def test_long_description_truncated(self):
        service = BriefingService.__new__(BriefingService)
        task = self._make_task(description="x" * 500)
        result = service._task_to_dict(task, self._acc(), is_overdue=True)
        assert len(result["description"]) == 301
        assert result["description"].endswith("…")

    def test_email_format_includes_task_details(self):
        service = BriefingService.__new__(BriefingService)
        briefing = {
            "date": "Monday, June 30, 2026",
            "summary_text": "1 overdue task",
            "summary": {},
            "calendar": None,
            "tasks": {
                "overdue": [{
                    "title": "Prepare board deck",
                    "project_name": "Board Prep",
                    "due_date": "2026-06-27T00:00:00+00:00",
                    "description": "Draft the Q3 numbers.",
                    "assignee_name": "Gonzalo",
                    "attention_reason": "overdue by 3 days, high priority",
                }],
                "due_today": [],
            },
            "overdue": None,
            "pending_replies": None,
            "delegations": None,
            "emails": None,
        }
        result = service._format_briefing_email(briefing)
        assert "Why: overdue by 3 days, high priority" in result
        assert "Details: Draft the Q3 numbers." in result
        assert "Assignee: Gonzalo" in result
