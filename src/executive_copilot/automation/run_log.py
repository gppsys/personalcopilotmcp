"""Persistent log of automation job runs.

The engine keeps its history in memory, which a container restart wipes. Every
run is also written here (next to the MCP activity log) so the status page can
show last runs, success rates and failures across restarts and processes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from executive_copilot.automation.jobs import JobResult

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB_PATH = _PROJECT_ROOT / "activity.db"
_MAX_ROWS = 5000

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_seconds REAL,
    items_processed INTEGER DEFAULT 0,
    error_message TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS job_runs_name_started ON job_runs(job_name, started_at);
"""


def _iso(value: datetime | None) -> str | None:
    """ISO string in UTC; jobs use naive ``utcnow()`` timestamps."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


class JobRunLog:
    """SQLite-backed job run history."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def record(self, result: JobResult) -> None:
        details = json.dumps(result.details, default=str, ensure_ascii=False)[:4000]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO job_runs (job_name, status, started_at, completed_at, duration_seconds,"
                " items_processed, error_message, details) VALUES (?,?,?,?,?,?,?,?)",
                (
                    result.job_name,
                    str(result.status),
                    _iso(result.started_at),
                    _iso(result.completed_at),
                    result.duration_seconds,
                    result.items_processed,
                    (result.error_message or None) and result.error_message[:1000],
                    details,
                ),
            )
            conn.execute(
                "DELETE FROM job_runs WHERE id <= (SELECT max(id) FROM job_runs) - ?", (_MAX_ROWS,)
            )

    def recent(self, job_name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Newest runs first."""
        query = "SELECT * FROM job_runs"
        params: tuple[Any, ...] = ()
        if job_name:
            query += " WHERE job_name = ?"
            params = (job_name,)
        query += " ORDER BY started_at DESC, id DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()
        return [_row(r) for r in rows]

    def per_job(self, history: int = 30) -> dict[str, dict[str, Any]]:
        """For each job: its newest ``history`` runs plus last success / failure."""
        out: dict[str, dict[str, Any]] = {}
        with self._connect() as conn:
            names = [r[0] for r in conn.execute("SELECT DISTINCT job_name FROM job_runs")]
            for name in names:
                runs = conn.execute(
                    "SELECT * FROM job_runs WHERE job_name = ? ORDER BY started_at DESC, id DESC LIMIT ?",
                    (name, history),
                ).fetchall()
                last_ok = conn.execute(
                    "SELECT completed_at FROM job_runs WHERE job_name = ? AND status = 'completed'"
                    " ORDER BY started_at DESC LIMIT 1",
                    (name,),
                ).fetchone()
                last_fail = conn.execute(
                    "SELECT * FROM job_runs WHERE job_name = ? AND status = 'failed'"
                    " ORDER BY started_at DESC LIMIT 1",
                    (name,),
                ).fetchone()
                out[name] = {
                    "runs": [_row(r) for r in runs],
                    "last_success_at": last_ok[0] if last_ok else None,
                    "last_failure": _row(last_fail) if last_fail else None,
                }
        return out


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["details"] = json.loads(data.get("details") or "{}")
    except json.JSONDecodeError:
        data["details"] = {}
    return data


_log: JobRunLog | None = None


def get_run_log() -> JobRunLog:
    """Process-wide run log on the default database."""
    global _log
    if _log is None:
        _log = JobRunLog()
    return _log
