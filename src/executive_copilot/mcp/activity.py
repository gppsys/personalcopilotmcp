"""MCP Activity tracking for real-time monitoring.

Activities are persisted to a small SQLite database so they are visible
across processes: the MCP server (spawned by Claude Desktop) writes them,
and the admin panel (a separate process) reads them.
"""

import json
import sqlite3
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

# Both the MCP server and the admin panel run from the project root
# (the MCP entry point chdirs there), but we resolve the path from this
# file so it never depends on the working directory.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB_PATH = _PROJECT_ROOT / "activity.db"

# Keep only the most recent rows; stats are computed over this window.
_MAX_ROWS = 500

# Pending activities older than this are considered stale (e.g. the MCP
# process died mid-call) and are not counted as "executing".
_PENDING_STALE_SECONDS = 300

_SENSITIVE_KEYS = {"password", "secret", "token", "key", "credential", "auth"}


class ActivityType(Enum):
    """Type of MCP activity."""
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"


class ActivityStatus(Enum):
    """Status of an activity."""
    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"


def _sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Sanitize arguments to remove sensitive data."""
    sanitized: dict[str, Any] = {}
    for k, v in args.items():
        if any(s in k.lower() for s in _SENSITIVE_KEYS):
            sanitized[k] = "***"
        elif isinstance(v, str) and len(v) > 100:
            sanitized[k] = v[:100] + "..."
        else:
            sanitized[k] = v
    return sanitized


class ActivityTracker:
    """Tracker for MCP activities backed by a shared SQLite database.

    ``ActivityTracker()`` returns a process-wide singleton pointing at the
    default database. Passing an explicit ``db_path`` returns a dedicated
    instance (used in tests).
    """

    _instance: "ActivityTracker | None" = None

    def __new__(cls, db_path: Path | None = None) -> "ActivityTracker":
        if db_path is not None:
            instance = super().__new__(cls)
            instance._setup(db_path)
            return instance
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._setup(_DEFAULT_DB_PATH)
            cls._instance = instance
        return cls._instance

    def _setup(self, db_path: Path) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_activity (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    activity_type TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms REAL,
                    args TEXT,
                    result_summary TEXT,
                    error_message TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def start_activity(self, tool_name: str, args: dict[str, Any] | None = None) -> str:
        """Start tracking a tool call. Returns activity ID."""
        activity_id = str(uuid4())[:8]
        now = datetime.now(UTC).isoformat()
        args_json = json.dumps(_sanitize_args(args or {}), default=str)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mcp_activity (id, timestamp, activity_type, tool_name, status, args)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (activity_id, now, ActivityType.TOOL_CALL.value, tool_name,
                 ActivityStatus.PENDING.value, args_json),
            )
            self._prune(conn)
        return activity_id

    def complete_activity(
        self,
        activity_id: str,
        result_summary: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Complete a pending activity."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT timestamp FROM mcp_activity WHERE id = ? AND status = ?",
                (activity_id, ActivityStatus.PENDING.value),
            ).fetchone()
            if row is None:
                return

            started = datetime.fromisoformat(row["timestamp"])
            duration = round((datetime.now(UTC) - started).total_seconds() * 1000, 2)
            status = ActivityStatus.ERROR if error_message else ActivityStatus.SUCCESS
            conn.execute(
                "UPDATE mcp_activity SET activity_type = ?, status = ?, duration_ms = ?,"
                " result_summary = ?, error_message = ? WHERE id = ?",
                (ActivityType.TOOL_RESULT.value, status.value, duration,
                 result_summary if not error_message else None, error_message, activity_id),
            )

    def log_error(self, tool_name: str, error_message: str) -> None:
        """Log an error without a prior start_activity call."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mcp_activity (id, timestamp, activity_type, tool_name, status, args, error_message)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid4())[:8], datetime.now(UTC).isoformat(), ActivityType.ERROR.value,
                 tool_name, ActivityStatus.ERROR.value, "{}", error_message),
            )
            self._prune(conn)

    def get_recent_activities(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent activities as dictionaries, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mcp_activity ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "activity_type": row["activity_type"],
                "tool_name": row["tool_name"],
                "status": row["status"],
                "duration_ms": row["duration_ms"],
                "args": json.loads(row["args"] or "{}"),
                "result_summary": row["result_summary"],
                "error_message": row["error_message"],
            }
            for row in rows
        ]

    def get_stats(self) -> dict[str, Any]:
        """Get activity statistics over the retained window."""
        pending_cutoff = datetime.now(UTC).timestamp() - _PENDING_STALE_SECONDS
        cutoff_iso = datetime.fromtimestamp(pending_cutoff, UTC).isoformat()
        with self._connect() as conn:
            counts = conn.execute(
                "SELECT status, COUNT(*) AS n FROM mcp_activity GROUP BY status"
            ).fetchall()
            tools = conn.execute(
                "SELECT tool_name, COUNT(*) AS n FROM mcp_activity GROUP BY tool_name"
            ).fetchall()
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM mcp_activity WHERE status = ? AND timestamp >= ?",
                (ActivityStatus.PENDING.value, cutoff_iso),
            ).fetchone()

        by_status = {row["status"]: row["n"] for row in counts}
        total = sum(by_status.values())
        successful = by_status.get(ActivityStatus.SUCCESS.value, 0)
        return {
            "total_calls": total,
            "successful_calls": successful,
            "failed_calls": by_status.get(ActivityStatus.ERROR.value, 0),
            "tools_used": {row["tool_name"]: row["n"] for row in tools},
            "pending_count": pending["n"],
            "success_rate": round(successful / total * 100, 1) if total > 0 else 0,
        }

    def clear(self) -> None:
        """Clear all activities."""
        with self._connect() as conn:
            conn.execute("DELETE FROM mcp_activity")

    def _prune(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "DELETE FROM mcp_activity WHERE rowid NOT IN"
            " (SELECT rowid FROM mcp_activity ORDER BY rowid DESC LIMIT ?)",
            (_MAX_ROWS,),
        )


def get_tracker() -> ActivityTracker:
    """Get the global activity tracker instance."""
    return ActivityTracker()
