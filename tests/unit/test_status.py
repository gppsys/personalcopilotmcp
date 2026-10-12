"""Tests for the job run log and the status console helpers."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from executive_copilot.automation import run_log
from executive_copilot.automation.jobs import Job, JobResult, JobStatus
from executive_copilot.automation.run_log import JobRunLog
from executive_copilot.services.status import (
    DOWN,
    IDLE,
    OK,
    PENDING,
    WARN,
    StatusService,
    _run_errors,
    describe_cron,
    git_revision,
    percentile,
)


def _result(name: str, status: JobStatus, minutes_ago: int, **kw: Any) -> JobResult:
    started = datetime.utcnow() - timedelta(minutes=minutes_ago)
    return JobResult(
        job_name=name, status=status, started_at=started,
        completed_at=started + timedelta(seconds=2), duration_seconds=2.0, **kw,
    )


def test_run_log_records_and_summarizes(tmp_path: Path) -> None:
    log = JobRunLog(tmp_path / "runs.db")
    log.record(_result("sync", JobStatus.FAILED, 30, error_message="boom"))
    log.record(_result("sync", JobStatus.COMPLETED, 10, items_processed=5, details={"errors": []}))
    log.record(_result("other", JobStatus.COMPLETED, 5))

    summary = log.per_job(history=10)
    assert [r["status"] for r in summary["sync"]["runs"]] == ["completed", "failed"]
    assert summary["sync"]["last_failure"]["error_message"] == "boom"
    assert summary["sync"]["last_success_at"].endswith("+00:00")
    assert [r["job_name"] for r in log.recent(limit=2)] == ["other", "sync"]
    assert log.recent("sync")[0]["details"] == {"errors": []}


class _Boom(Job):
    async def execute(self) -> JobResult:
        raise RuntimeError("")


@pytest.mark.asyncio
async def test_job_run_is_persisted_even_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = JobRunLog(tmp_path / "runs.db")
    monkeypatch.setattr(run_log, "get_run_log", lambda: log)

    result = await _Boom("boom", container=None).run()  # type: ignore[arg-type]
    assert result.status == JobStatus.FAILED
    assert result.error_message == "RuntimeError"  # empty messages still say what happened
    assert log.recent("boom")[0]["status"] == "failed"


def test_describe_cron() -> None:
    assert describe_cron("*/5 * * * *") == "cada 5 min"
    assert describe_cron("30 13,19 * * 1-5") == "L–V 13:30, 19:30"
    assert describe_cron("0 7 * * *") == "diario 07:00"
    assert describe_cron("0 */2 * * *") == "cada 2 h"
    assert describe_cron("0 9 1 * *") == "0 9 1 * *"


def test_percentile() -> None:
    assert percentile([], 95) is None
    assert percentile([10, 20, 30, 40, 1000], 95) == 1000
    assert percentile([10, 20, 30, 40, 1000], 50) == 30


def test_job_health() -> None:
    health = StatusService._job_health
    assert health(False, False, None, False) == (IDLE, None)
    assert health(True, True, None, False) == (IDLE, None)
    assert health(True, False, None, False)[0] == PENDING
    assert health(True, False, {"status": "failed", "error_message": "403"}, False) == (DOWN, "403")
    warn = {"status": "completed", "details": {"errors": ["Account x: 403 Forbidden\nmore"]}}
    assert health(True, False, warn, False) == (WARN, "Completó con errores: Account x: 403 Forbidden")
    assert health(True, False, {"status": "completed", "details": {}}, True)[0] == WARN
    assert health(True, False, {"status": "completed", "details": {}}, False) == (OK, None)


def test_git_revision(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text("904708fabc\n")
    assert git_revision(tmp_path) == "904708f"

    (git / "refs" / "heads" / "main").unlink()
    (git / "packed-refs").write_text("# pack\n1234567890 refs/heads/main\n")
    assert git_revision(tmp_path) == "1234567"
    assert git_revision(tmp_path / "nope") is None


def test_run_errors_accepts_list_or_count() -> None:
    assert _run_errors({"details": {"errors": ["boom\ntrace", {"error": "x"}]}}) == ["boom", "x"]
    assert _run_errors({"details": {"errors": 0}}) == []
    assert _run_errors({"details": {"errors": 3}}) == ["3 error(es)"]
    assert _run_errors({"details": None}) == []
