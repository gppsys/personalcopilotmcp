"""Tests for MCP Activity Tracker."""

from pathlib import Path

import pytest

from executive_copilot.mcp.activity import ActivityTracker, get_tracker


@pytest.fixture
def tracker(tmp_path: Path) -> ActivityTracker:
    """Tracker backed by an isolated temporary database."""
    return ActivityTracker(db_path=tmp_path / "activity.db")


class TestActivityTracker:
    """Tests for ActivityTracker."""

    def test_start_and_complete_activity(self, tracker: ActivityTracker) -> None:
        """Test starting and completing an activity."""
        activity_id = tracker.start_activity("test_tool", {"arg1": "value1"})

        assert activity_id is not None
        assert len(activity_id) == 8

        tracker.complete_activity(activity_id, result_summary="Success")

        activities = tracker.get_recent_activities()
        assert len(activities) == 1
        assert activities[0]["tool_name"] == "test_tool"
        assert activities[0]["status"] == "success"
        assert activities[0]["result_summary"] == "Success"
        assert activities[0]["duration_ms"] is not None

    def test_complete_with_error(self, tracker: ActivityTracker) -> None:
        """Test completing an activity with an error."""
        activity_id = tracker.start_activity("failing_tool", {})

        tracker.complete_activity(activity_id, error_message="Something went wrong")

        activities = tracker.get_recent_activities()
        assert len(activities) == 1
        assert activities[0]["status"] == "error"
        assert activities[0]["error_message"] == "Something went wrong"

    def test_stats_counting(self, tracker: ActivityTracker) -> None:
        """Test that stats are correctly counted."""
        # Success
        id1 = tracker.start_activity("tool1", {})
        tracker.complete_activity(id1, result_summary="OK")

        # Another success
        id2 = tracker.start_activity("tool2", {})
        tracker.complete_activity(id2, result_summary="OK")

        # Failure
        id3 = tracker.start_activity("tool1", {})
        tracker.complete_activity(id3, error_message="Failed")

        stats = tracker.get_stats()
        assert stats["total_calls"] == 3
        assert stats["successful_calls"] == 2
        assert stats["failed_calls"] == 1
        assert stats["success_rate"] == 66.7
        assert stats["tools_used"]["tool1"] == 2
        assert stats["tools_used"]["tool2"] == 1

    def test_pending_count(self, tracker: ActivityTracker) -> None:
        """Test that in-flight activities show as pending."""
        tracker.start_activity("slow_tool", {})

        stats = tracker.get_stats()
        assert stats["pending_count"] == 1
        assert stats["total_calls"] == 1

    def test_clear(self, tracker: ActivityTracker) -> None:
        """Test clearing the tracker."""
        tracker.start_activity("tool1", {})
        tracker.clear()

        activities = tracker.get_recent_activities()
        stats = tracker.get_stats()

        assert len(activities) == 0
        assert stats["total_calls"] == 0

    def test_singleton(self) -> None:
        """Test that the default tracker is a singleton."""
        tracker1 = get_tracker()
        tracker2 = get_tracker()
        tracker3 = ActivityTracker()

        assert tracker1 is tracker2
        assert tracker2 is tracker3

    def test_cross_instance_visibility(self, tmp_path: Path) -> None:
        """Activities written by one instance are visible from another.

        This mirrors the real deployment: the MCP server process writes
        activities and the admin panel process reads them.
        """
        db_path = tmp_path / "activity.db"
        writer = ActivityTracker(db_path=db_path)
        reader = ActivityTracker(db_path=db_path)

        activity_id = writer.start_activity("shared_tool", {})
        writer.complete_activity(activity_id, result_summary="OK")

        activities = reader.get_recent_activities()
        assert len(activities) == 1
        assert activities[0]["tool_name"] == "shared_tool"

    def test_sanitize_sensitive_args(self, tracker: ActivityTracker) -> None:
        """Test that sensitive arguments are sanitized."""
        activity_id = tracker.start_activity(
            "auth_tool",
            {"username": "user", "password": "secret123", "api_key": "key123"},
        )
        tracker.complete_activity(activity_id, result_summary="OK")

        activities = tracker.get_recent_activities()
        args = activities[0]["args"]

        assert args["username"] == "user"
        assert args["password"] == "***"
        assert args["api_key"] == "***"
