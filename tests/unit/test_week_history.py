"""Tests for the tech dashboard week-history snapshots."""

from datetime import UTC, datetime

import pytest

from executive_copilot.services import week_history


class TestWeekHistoryStore:
    """Tests for the week snapshot persistence."""

    def test_close_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            week_history, "_HISTORY_PATH", tmp_path / "history.json"
        )
        result = week_history.close_week(
            clients=8, roadmap=12, done=6, urgent=3, notes="firmamos cliente X"
        )
        assert result["saved"] is True
        week = result["week"]
        history = week_history.load_history()
        assert history[week]["clients"] == 8
        assert history[week]["notes"] == "firmamos cliente X"
        assert history[week]["locked"] is True

    def test_locked_week_not_overwritten(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            week_history, "_HISTORY_PATH", tmp_path / "history.json"
        )
        week_history.close_week(clients=8, roadmap=12, done=6, week="2026-W27")
        result = week_history.close_week(
            clients=99, roadmap=99, done=99, week="2026-W27"
        )
        assert result["saved"] is False
        assert result["reason"] == "already_locked"
        assert week_history.load_history()["2026-W27"]["clients"] == 8

    def test_force_overwrites_locked_week(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            week_history, "_HISTORY_PATH", tmp_path / "history.json"
        )
        week_history.close_week(clients=8, roadmap=12, done=6, week="2026-W27")
        result = week_history.close_week(
            clients=9, roadmap=11, done=7, week="2026-W27", force=True
        )
        assert result["saved"] is True
        assert week_history.load_history()["2026-W27"]["clients"] == 9

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            week_history, "_HISTORY_PATH", tmp_path / "missing.json"
        )
        assert week_history.load_history() == {}

    def test_corrupt_file(self, tmp_path, monkeypatch):
        path = tmp_path / "history.json"
        monkeypatch.setattr(week_history, "_HISTORY_PATH", path)
        path.write_text("not json")
        assert week_history.load_history() == {}

    def test_current_iso_week_format(self):
        week = week_history.current_iso_week(datetime(2026, 1, 2, tzinfo=UTC))
        # Jan 2 2026 falls in ISO week 2026-W01
        assert week == "2026-W01"


class TestRenderDashboardHtml:
    """Tests for injecting history into the dashboard HTML."""

    def test_injects_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            week_history, "_HISTORY_PATH", tmp_path / "history.json"
        )
        dashboard = tmp_path / "dash.html"
        dashboard.write_text(
            "<script>const SERVER_HISTORY = /*__WEEK_HISTORY__*/null;</script>"
        )
        monkeypatch.setattr(week_history, "_DASHBOARD_PATH", dashboard)

        week_history.close_week(
            clients=8, roadmap=12, done=6, week="2026-W27", notes="nota"
        )
        html = week_history.render_dashboard_html()
        assert "/*__WEEK_HISTORY__*/null" not in html
        assert '"2026-W27"' in html
        assert '"clients": 8' in html

    def test_empty_history_injects_empty_object(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            week_history, "_HISTORY_PATH", tmp_path / "missing.json"
        )
        dashboard = tmp_path / "dash.html"
        dashboard.write_text(
            "<script>const SERVER_HISTORY = /*__WEEK_HISTORY__*/null;</script>"
        )
        monkeypatch.setattr(week_history, "_DASHBOARD_PATH", dashboard)

        html = week_history.render_dashboard_html()
        assert "const SERVER_HISTORY = {};" in html

    @pytest.mark.skipif(
        not week_history._DASHBOARD_PATH.exists(),
        reason="tech_dashboard.html is local-only (gitignored)",
    )
    def test_real_dashboard_has_placeholder(self):
        html = week_history._DASHBOARD_PATH.read_text()
        assert week_history._HISTORY_PLACEHOLDER in html
