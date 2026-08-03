"""Persistent weekly snapshots for the tech dashboard.

"Cerrar semana" stores a snapshot of the dashboard's headline metrics
(clients, roadmap, done, urgent) plus an executive note, keyed by ISO
week. Snapshots live on disk so they survive server restarts, and get
injected into the dashboard HTML when it is served as an MCP resource —
the dashboard remembers where last week ended no matter where it renders.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from executive_copilot.logging import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HISTORY_PATH = _REPO_ROOT / "dashboard_week_history.json"
_DASHBOARD_PATH = _REPO_ROOT / "tech_dashboard.html"

# The dashboard's <script> declares `const SERVER_HISTORY = /*__WEEK_HISTORY__*/null;`
# render_dashboard_html() swaps the placeholder for the persisted history.
_HISTORY_PLACEHOLDER = "/*__WEEK_HISTORY__*/null"


def current_iso_week(now: datetime | None = None) -> str:
    """ISO week key like '2026-W27', matching the dashboard's getISOWeek()."""
    dt = now or datetime.now(UTC)
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def load_history() -> dict[str, dict[str, Any]]:
    """Load week snapshots (ISO week -> snapshot)."""
    try:
        data = json.loads(_HISTORY_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: snap for key, snap in data.items() if isinstance(snap, dict)}


def _save_history(history: dict[str, dict[str, Any]]) -> None:
    try:
        _HISTORY_PATH.write_text(
            json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True)
        )
    except OSError as e:
        logger.warning("Failed to write dashboard week history", error=str(e))


def close_week(
    *,
    clients: int,
    roadmap: int,
    done: int,
    urgent: int = 0,
    notes: str = "",
    week: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Save a week snapshot; a locked week is only overwritten with force."""
    history = load_history()
    key = week or current_iso_week()
    existing = history.get(key)
    if existing and existing.get("locked") and not force:
        return {
            "saved": False,
            "week": key,
            "reason": "already_locked",
            "snapshot": existing,
        }

    snapshot: dict[str, Any] = {
        "week": key,
        "date": datetime.now(UTC).date().isoformat(),
        "locked": True,
        "clients": clients,
        "roadmap": roadmap,
        "done": done,
        "urgent": urgent,
        "notes": notes,
    }
    history[key] = snapshot
    _save_history(history)
    return {"saved": True, "week": key, "snapshot": snapshot}


def render_dashboard_html() -> str:
    """The dashboard HTML with the persisted week history injected."""
    html = _DASHBOARD_PATH.read_text()
    history = load_history()
    return html.replace(
        _HISTORY_PLACEHOLDER, json.dumps(history, ensure_ascii=False), 1
    )
