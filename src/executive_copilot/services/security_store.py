"""Persistent security findings (next to the job run log in activity.db).

A finding is one advisory affecting one component in one place (a package in a
repository, a watched product's release line, or a release line reaching end of
life). The scan upserts findings by id so a daily run can tell what is new,
what is still open and what disappeared (fixed or no longer in the lock file).
The user's review state (reviewed / ignored) survives every run.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB_PATH = _PROJECT_ROOT / "activity.db"

SEVERITIES = ("critical", "high", "medium", "low", "unknown")
STATES = ("open", "reviewed", "ignored", "resolved")

SCHEMA = """
CREATE TABLE IF NOT EXISTS security_findings (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    vuln_id TEXT,
    severity TEXT NOT NULL DEFAULT 'unknown',
    score REAL,
    component TEXT NOT NULL,
    ecosystem TEXT,
    version TEXT,
    fixed_in TEXT,
    repo TEXT,
    title TEXT,
    url TEXT,
    published TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    note TEXT,
    resolved_at TEXT,
    details TEXT,
    dev INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS security_findings_state ON security_findings(state, severity);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def severity_rank(severity: str | None) -> int:
    """Lower is worse; unknown sorts after low."""
    try:
        return SEVERITIES.index((severity or "unknown").lower())
    except ValueError:
        return len(SEVERITIES)


class SecurityStore:
    """SQLite-backed findings with review state."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(security_findings)")}
        if "dev" not in columns:  # table created before dev-only dependencies were tracked
            conn.execute("ALTER TABLE security_findings ADD COLUMN dev INTEGER NOT NULL DEFAULT 0")
        return conn

    # --- writes -------------------------------------------------------------

    def upsert(self, findings: list[dict[str, Any]], ignored_ids: dict[str, str] | None = None) -> dict[str, int]:
        """Record this run's findings. Returns counts of new / reopened / resolved.

        Findings in ``ignored_ids`` (advisory id -> reason, from config) are stored
        as ignored. Open findings not seen this run are marked resolved; a finding
        that comes back later is reopened.
        """
        ignored_ids = ignored_ids or {}
        now = _now()
        seen: set[str] = set()
        stats = {"new": 0, "reopened": 0, "resolved": 0, "updated": 0}
        with self._connect() as conn:
            for f in findings:
                fid = f["id"]
                seen.add(fid)
                row = conn.execute("SELECT state FROM security_findings WHERE id = ?", (fid,)).fetchone()
                details = json.dumps(f.get("details") or {}, ensure_ascii=False, default=str)[:4000]
                reason = ignored_ids.get(f.get("vuln_id") or "")
                if row is None:
                    stats["new"] += 1
                    conn.execute(
                        "INSERT INTO security_findings (id, source, kind, vuln_id, severity, score, component,"
                        " ecosystem, version, fixed_in, repo, title, url, published, first_seen, last_seen,"
                        " state, note, details, dev) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            fid, f["source"], f["kind"], f.get("vuln_id"), f.get("severity") or "unknown",
                            f.get("score"), f["component"], f.get("ecosystem"), f.get("version"),
                            f.get("fixed_in"), f.get("repo"), (f.get("title") or "")[:300], f.get("url"),
                            f.get("published"), now, now, "ignored" if reason else "open",
                            reason, details, int(bool(f.get("dev"))),
                        ),
                    )
                    continue
                state = row["state"]
                if state == "resolved":
                    state = "open"
                    stats["reopened"] += 1
                else:
                    stats["updated"] += 1
                if reason and state == "open":
                    state = "ignored"
                conn.execute(
                    "UPDATE security_findings SET severity = ?, score = ?, version = ?, fixed_in = ?, title = ?,"
                    " url = ?, published = ?, last_seen = ?, state = ?, resolved_at = NULL, details = ?,"
                    " note = COALESCE(note, ?), dev = ? WHERE id = ?",
                    (
                        f.get("severity") or "unknown", f.get("score"), f.get("version"), f.get("fixed_in"),
                        (f.get("title") or "")[:300], f.get("url"), f.get("published"), now, state, details,
                        reason, int(bool(f.get("dev"))), fid,
                    ),
                )
            # Findings not seen anymore: resolved (fixed, removed, or out of the window).
            sources = sorted({f["source"] for f in findings})
            for source in sources:
                rows = conn.execute(
                    "SELECT id FROM security_findings WHERE source = ? AND state != 'resolved'", (source,)
                ).fetchall()
                gone = [r["id"] for r in rows if r["id"] not in seen]
                for fid in gone:
                    conn.execute(
                        "UPDATE security_findings SET state = 'resolved', resolved_at = ? WHERE id = ?", (now, fid)
                    )
                stats["resolved"] += len(gone)
        return stats

    def set_state(self, finding_id: str, state: str, note: str | None = None) -> dict[str, Any] | None:
        if state not in ("open", "reviewed", "ignored"):
            raise ValueError(f"Invalid state: {state}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE security_findings SET state = ?, note = COALESCE(?, note) WHERE id = ?",
                (state, note, finding_id),
            )
            row = conn.execute("SELECT * FROM security_findings WHERE id = ?", (finding_id,)).fetchone()
        return _row(row) if row else None

    # --- reads --------------------------------------------------------------

    def findings(
        self,
        states: tuple[str, ...] = ("open", "reviewed"),
        limit: int = 400,
        since: str | None = None,
        include_dev: bool = True,
    ) -> list[dict[str, Any]]:
        """Worst first, newest first within the same severity."""
        marks = ",".join("?" for _ in states)
        query = f"SELECT * FROM security_findings WHERE state IN ({marks})"
        params: list[Any] = list(states)
        if not include_dev:
            query += " AND dev = 0"
        if since:
            query += " AND first_seen >= ?"
            params.append(since)
        query += (
            " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2"
            " WHEN 'low' THEN 3 ELSE 4 END, first_seen DESC LIMIT ?"
        )
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row(r) for r in rows]

    def summary(self, today: str | None = None, include_dev: bool = False) -> dict[str, Any]:
        """Counts for badges and the Hoy banner (cheap, local).

        Dev-only dependencies (test runners, bundlers) are left out by default so
        the headline numbers talk about what runs in production.
        """
        today = today or datetime.now(UTC).date().isoformat()
        dev = "" if include_dev else " AND dev = 0"
        with self._connect() as conn:
            by_sev = {
                r["severity"]: r["n"]
                for r in conn.execute(
                    f"SELECT severity, count(*) AS n FROM security_findings WHERE state = 'open'{dev} GROUP BY severity"
                )
            }
            new_today = conn.execute(
                f"SELECT count(*) FROM security_findings WHERE state IN ('open','reviewed'){dev}"
                " AND substr(first_seen, 1, 10) = ?",
                (today,),
            ).fetchone()[0]
            kinds = {
                r["kind"]: r["n"]
                for r in conn.execute(
                    f"SELECT kind, count(*) AS n FROM security_findings WHERE state = 'open'{dev} GROUP BY kind"
                )
            }
            ignored = conn.execute("SELECT count(*) FROM security_findings WHERE state = 'ignored'").fetchone()[0]
            reviewed = conn.execute("SELECT count(*) FROM security_findings WHERE state = 'reviewed'").fetchone()[0]
            dev_open = conn.execute("SELECT count(*) FROM security_findings WHERE state = 'open' AND dev = 1").fetchone()[0]
        open_total = sum(by_sev.values())
        return {
            "dev_open": dev_open,
            "open": open_total,
            "critical": by_sev.get("critical", 0),
            "high": by_sev.get("high", 0),
            "medium": by_sev.get("medium", 0),
            "low": by_sev.get("low", 0),
            "unknown": by_sev.get("unknown", 0),
            "new_today": new_today,
            "eol": kinds.get("eol", 0),
            "ignored": ignored,
            "reviewed": reviewed,
        }


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["details"] = json.loads(data.get("details") or "{}")
    except json.JSONDecodeError:
        data["details"] = {}
    return data
