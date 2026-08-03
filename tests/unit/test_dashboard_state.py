"""Tests for dashboard done-item state."""

from datetime import UTC, datetime, timedelta

from executive_copilot.services import dashboard_state


class TestDoneItemsStore:
    """Tests for the done-items persistence."""

    def test_mark_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            dashboard_state, "_DONE_ITEMS_PATH", tmp_path / "done.json"
        )
        dashboard_state.set_item_done("task:123", True)
        assert "task:123" in dashboard_state.load_done_items()

    def test_unmark(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            dashboard_state, "_DONE_ITEMS_PATH", tmp_path / "done.json"
        )
        dashboard_state.set_item_done("task:123", True)
        dashboard_state.set_item_done("task:123", False)
        assert dashboard_state.load_done_items() == {}

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            dashboard_state, "_DONE_ITEMS_PATH", tmp_path / "missing.json"
        )
        assert dashboard_state.load_done_items() == {}

    def test_expired_items_pruned(self, tmp_path, monkeypatch):
        path = tmp_path / "done.json"
        monkeypatch.setattr(dashboard_state, "_DONE_ITEMS_PATH", path)
        old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        path.write_text(f'{{"task:old": "{old}"}}')
        assert dashboard_state.load_done_items() == {}


class TestApplyDoneMarks:
    """Tests for annotating a briefing with done marks."""

    def _briefing(self):
        return {
            "summary": {
                "tasks_overdue": 2,
                "tasks_due_today": 1,
                "emails_requiring_attention": 1,
                "overdue_items": 1,
                "pending_replies": 1,
                "urgent_emails": 0,
            },
            "tasks": {
                "overdue": [
                    {"id": "a", "provider_id": "asana-1", "title": "T1"},
                    {"id": "b", "provider_id": "asana-2", "title": "T2"},
                ],
                "due_today": [{"id": "c", "provider_id": "asana-3", "title": "T3"}],
                "overdue_count": 2,
                "due_today_count": 1,
                "total_attention_needed": 3,
            },
            "emails_requiring_attention": {
                "emails": [
                    {
                        "subject": "Q",
                        "from": "x@y.com",
                        "thread_id": "th-1",
                        "requires_attention": True,
                        "is_urgent": False,
                    },
                ],
                "total": 1,
                "urgent_count": 0,
                "action_needed_count": 1,
            },
            "overdue": {
                "follow_ups": [{"id": "fu-1", "title": "F1"}],
                "commitments": [],
                "delegations": [],
                "total": 1,
            },
            "pending_replies": {"items": [{"id": "fu-1", "title": "F1"}], "total": 1},
        }

    def test_annotates_item_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            dashboard_state, "_DONE_ITEMS_PATH", tmp_path / "done.json"
        )
        briefing = dashboard_state.apply_done_marks(self._briefing())
        task = briefing["tasks"]["overdue"][0]
        assert task["item_key"] == "task:asana-1"
        assert task["done"] is False
        email = briefing["emails_requiring_attention"]["emails"][0]
        assert email["item_key"] == "email:th-1"

    def test_done_task_adjusts_counts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            dashboard_state, "_DONE_ITEMS_PATH", tmp_path / "done.json"
        )
        dashboard_state.set_item_done("task:asana-1", True)
        briefing = dashboard_state.apply_done_marks(self._briefing())
        assert briefing["tasks"]["overdue"][0]["done"] is True
        assert briefing["tasks"]["overdue_count"] == 1
        assert briefing["summary"]["tasks_overdue"] == 1
        assert briefing["tasks"]["total_attention_needed"] == 2

    def test_done_email_adjusts_counts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            dashboard_state, "_DONE_ITEMS_PATH", tmp_path / "done.json"
        )
        dashboard_state.set_item_done("email:th-1", True)
        briefing = dashboard_state.apply_done_marks(self._briefing())
        attention = briefing["emails_requiring_attention"]
        assert attention["emails"][0]["done"] is True
        assert attention["action_needed_count"] == 0
        assert briefing["summary"]["emails_requiring_attention"] == 0

    def test_done_follow_up_adjusts_counts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            dashboard_state, "_DONE_ITEMS_PATH", tmp_path / "done.json"
        )
        dashboard_state.set_item_done("followup:fu-1", True)
        briefing = dashboard_state.apply_done_marks(self._briefing())
        assert briefing["overdue"]["total"] == 0
        assert briefing["summary"]["overdue_items"] == 0
        assert briefing["pending_replies"]["total"] == 0
        assert briefing["summary"]["pending_replies"] == 0
