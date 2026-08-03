"""Persistent state for dashboard items marked as done.

Lets the user check off briefing items that were resolved outside of
email or Asana (a phone call, a hallway conversation, etc.). Marks are
stored on disk keyed by a stable item key, so they survive briefing
refreshes and server restarts, and expire automatically once the item
would no longer appear in a briefing anyway.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from executive_copilot.logging import get_logger

logger = get_logger(__name__)

_DONE_ITEMS_PATH = Path(__file__).resolve().parents[3] / "dashboard_done_items.json"
_RETENTION_DAYS = 30


def load_done_items() -> dict[str, str]:
    """Load done-item marks (key -> ISO timestamp), pruning expired ones."""
    try:
        data = json.loads(_DONE_ITEMS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    cutoff = datetime.now(UTC) - timedelta(days=_RETENTION_DAYS)
    pruned = {}
    for key, marked_at in data.items():
        try:
            if datetime.fromisoformat(marked_at) >= cutoff:
                pruned[key] = marked_at
        except (TypeError, ValueError):
            continue

    if len(pruned) != len(data):
        _save_done_items(pruned)

    return pruned


def _save_done_items(items: dict[str, str]) -> None:
    try:
        _DONE_ITEMS_PATH.write_text(json.dumps(items))
    except OSError as e:
        logger.warning("Failed to write dashboard done items", error=str(e))


def set_item_done(key: str, done: bool) -> dict[str, str]:
    """Mark or unmark a dashboard item as done. Returns the updated marks."""
    items = load_done_items()
    if done:
        items[key] = datetime.now(UTC).isoformat()
    else:
        items.pop(key, None)
    _save_done_items(items)
    return items


def _email_key(email: dict[str, Any]) -> str:
    thread_id = email.get("thread_id")
    if thread_id:
        return f"email:{thread_id}"
    return f"email:{email.get('from', '')}|{email.get('subject', '')}"


def _task_key(task: dict[str, Any]) -> str:
    return f"task:{task.get('provider_id') or task.get('id')}"


def _annotate(items: list[dict[str, Any]], key_fn: Any, done_keys: set[str]) -> int:
    """Attach item_key/done to each item; return count of done items."""
    done_count = 0
    for item in items:
        key = key_fn(item)
        item["item_key"] = key
        item["done"] = key in done_keys
        done_count += item["done"]
    return done_count


def apply_done_marks(briefing: dict[str, Any]) -> dict[str, Any]:
    """Annotate briefing items with done marks and adjust summary counts."""
    done_keys = set(load_done_items())
    summary = briefing.get("summary") or {}

    attention = briefing.get("emails_requiring_attention")
    if attention and attention.get("emails"):
        done = _annotate(attention["emails"], _email_key, done_keys)
        remaining = [e for e in attention["emails"] if not e["done"]]
        attention["total"] = len(remaining)
        attention["urgent_count"] = sum(1 for e in remaining if e.get("is_urgent"))
        attention["action_needed_count"] = sum(
            1 for e in remaining if e.get("requires_attention")
        )
        if done and "emails_requiring_attention" in summary:
            summary["emails_requiring_attention"] = attention["action_needed_count"]

    emails = briefing.get("emails")
    if emails and emails.get("emails"):
        _annotate(emails["emails"], _email_key, done_keys)
        emails["urgent_count"] = sum(
            1 for e in emails["emails"] if e.get("is_urgent") and not e["done"]
        )
        summary["urgent_emails"] = emails["urgent_count"]

    tasks = briefing.get("tasks")
    if tasks:
        for section, count_key, summary_key in (
            ("due_today", "due_today_count", "tasks_due_today"),
            ("overdue", "overdue_count", "tasks_overdue"),
        ):
            items = tasks.get(section) or []
            done = _annotate(items, _task_key, done_keys)
            if done:
                tasks[count_key] = max(0, tasks.get(count_key, len(items)) - done)
                if summary_key in summary:
                    summary[summary_key] = max(0, summary[summary_key] - done)
        tasks["total_attention_needed"] = (
            tasks.get("due_today_count", 0) + tasks.get("overdue_count", 0)
        )

    overdue = briefing.get("overdue")
    if overdue:
        done_total = 0
        for section, prefix in (
            ("follow_ups", "followup"),
            ("commitments", "commitment"),
            ("delegations", "delegation"),
        ):
            items = overdue.get(section) or []
            done_total += _annotate(
                items, lambda i, p=prefix: f"{p}:{i.get('id')}", done_keys
            )
        if done_total:
            overdue["total"] = max(0, overdue.get("total", 0) - done_total)
            if "overdue_items" in summary:
                summary["overdue_items"] = max(0, summary["overdue_items"] - done_total)

    pending = briefing.get("pending_replies")
    if pending and pending.get("items"):
        done = _annotate(
            pending["items"], lambda i: f"followup:{i.get('id')}", done_keys
        )
        if done:
            pending["total"] = max(0, pending.get("total", 0) - done)
            if "pending_replies" in summary:
                summary["pending_replies"] = max(0, summary["pending_replies"] - done)

    return briefing
