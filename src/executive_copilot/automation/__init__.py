"""Automation Engine for Executive Copilot.

Handles scheduled jobs for data synchronization, briefings,
and other automated tasks using APScheduler.
"""

from executive_copilot.automation.engine import AutomationEngine
from executive_copilot.automation.jobs import Job, JobResult, JobStatus

__all__ = [
    "AutomationEngine",
    "Job",
    "JobResult",
    "JobStatus",
]
