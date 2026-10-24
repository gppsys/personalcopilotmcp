"""Job definitions for the automation engine."""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, Field

from executive_copilot.logging import get_logger

if TYPE_CHECKING:
    from executive_copilot.services.container import ServiceContainer

logger = get_logger(__name__)


class JobStatus(StrEnum):
    """Status of a job execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobResult(BaseModel):
    """Result of a job execution."""

    job_name: str = Field(description="Job name")
    status: JobStatus = Field(description="Execution status")
    started_at: datetime = Field(description="Start time")
    completed_at: datetime | None = Field(default=None, description="Completion time")
    duration_seconds: float | None = Field(default=None, description="Duration")
    items_processed: int = Field(default=0, description="Items processed")
    error_message: str | None = Field(default=None, description="Error message if failed")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional details")


class Job(ABC):
    """Base class for automation jobs.

    Jobs are scheduled tasks that run periodically to synchronize
    data, generate reports, or perform maintenance.
    """

    # Not implemented yet: the status page shows these apart from real jobs.
    placeholder: ClassVar[bool] = False

    def __init__(
        self,
        name: str,
        container: "ServiceContainer",
        settings: dict[str, Any] | None = None,
    ) -> None:
        """Initialize job.

        Args:
            name: Job name
            container: Service container
            settings: Job-specific settings
        """
        self.name = name
        self._container = container
        self._settings = settings or {}
        self._logger = get_logger(f"job.{name}")

    @property
    def container(self) -> "ServiceContainer":
        """Get service container."""
        return self._container

    @property
    def settings(self) -> dict[str, Any]:
        """Get job settings."""
        return self._settings

    @abstractmethod
    async def execute(self) -> JobResult:
        """Execute the job.

        Returns:
            Job execution result
        """
        ...

    async def run(self) -> JobResult:
        """Run the job with logging and error handling.

        Returns:
            Job execution result
        """
        started_at = datetime.utcnow()
        self._logger.info("Job starting", job=self.name)

        try:
            result = await self.execute()
            completed_at = datetime.utcnow()
            result_dict = result.model_dump(exclude={"completed_at", "duration_seconds"})
            result = JobResult(
                **result_dict,
                completed_at=completed_at,
                duration_seconds=(completed_at - started_at).total_seconds(),
            )
            self._logger.info(
                "Job completed",
                job=self.name,
                status=result.status,
                items=result.items_processed,
                duration=result.duration_seconds,
            )

        except Exception as e:
            self._logger.error("Job failed", job=self.name, error=str(e))
            result = JobResult(
                job_name=self.name,
                status=JobStatus.FAILED,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                duration_seconds=(datetime.utcnow() - started_at).total_seconds(),
                error_message=str(e) or type(e).__name__,
            )

        self._record(result)
        return result

    def _record(self, result: JobResult) -> None:
        """Persist the run for the status page; never fail the job over it."""
        from executive_copilot.automation.run_log import get_run_log

        try:
            get_run_log().record(result)
        except Exception as e:
            self._logger.warning("Could not record job run", error=str(e))


class EmailSyncJob(Job):
    """Job for synchronizing emails from providers."""

    placeholder = True

    async def execute(self) -> JobResult:
        """Sync emails from all providers."""
        started_at = datetime.utcnow()
        items_processed = 0

        # In future sprints, this will:
        # 1. Get sync tokens from database
        # 2. Fetch changes from each provider
        # 3. Update local database
        # 4. Classify new emails
        # 5. Detect follow-ups

        self._logger.info("Email sync placeholder - will be implemented in Sprint 2")

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=items_processed,
            details={"message": "Placeholder - providers not yet implemented"},
        )


class CalendarSyncJob(Job):
    """Job for synchronizing calendar events."""

    placeholder = True

    async def execute(self) -> JobResult:
        """Sync calendar events from all providers."""
        started_at = datetime.utcnow()

        self._logger.info("Calendar sync placeholder - will be implemented in Sprint 6")

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=0,
            details={"message": "Placeholder - providers not yet implemented"},
        )


class TaskSyncJob(Job):
    """Job for synchronizing tasks."""

    placeholder = True

    async def execute(self) -> JobResult:
        """Sync tasks from all providers."""
        started_at = datetime.utcnow()

        self._logger.info("Task sync placeholder - will be implemented in Sprint 7")

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=0,
            details={"message": "Placeholder - providers not yet implemented"},
        )


class NewsSyncJob(Job):
    """Job for fetching news articles."""

    placeholder = True

    async def execute(self) -> JobResult:
        """Fetch news from configured sources."""
        started_at = datetime.utcnow()

        self._logger.info("News sync placeholder - will be implemented in Sprint 10")

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=0,
            details={"message": "Placeholder - providers not yet implemented"},
        )


class DailyBriefingJob(Job):
    """Job for generating and sending daily briefing."""

    async def execute(self) -> JobResult:
        """Generate and optionally send daily briefing."""
        from executive_copilot.services.briefing import BriefingService

        started_at = datetime.utcnow()

        send_email = self._settings.get("send_email", True)
        to_email = self._settings.get("to_email")

        self._logger.info("Generating daily briefing", send_email=send_email)

        service = BriefingService(
            config=self._container.config,
            credential_manager=self._container.credential_manager,
            db_manager=self._container.db_manager,
        )

        briefing = await service.generate_briefing()

        summary = briefing.get("summary", {})
        self._logger.info(
            "Briefing generated",
            meetings=summary.get("meetings", 0),
            unread=summary.get("unread_emails", 0),
            overdue=summary.get("overdue_items", 0),
        )

        email_sent = False
        if send_email:
            result = await service.send_briefing_email(
                briefing=briefing,
                to_email=to_email,
            )
            email_sent = result.get("success", False)
            if not email_sent:
                self._logger.warning(
                    "Failed to send briefing email",
                    error=result.get("error"),
                )

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=1,
            details={
                "summary": briefing.get("summary_text"),
                "meetings": summary.get("meetings", 0),
                "unread_emails": summary.get("unread_emails", 0),
                "overdue_items": summary.get("overdue_items", 0),
                "pending_replies": summary.get("pending_replies", 0),
                "email_sent": email_sent,
            },
        )


class FollowUpDetectionJob(Job):
    """Job for detecting follow-up items from sent emails."""

    async def execute(self) -> JobResult:
        """Scan sent emails for pending replies and create follow-ups."""
        from executive_copilot.services.pending_replies import PendingRepliesService

        started_at = datetime.utcnow()

        days_back = self._settings.get("days_back", 7)
        min_wait_days = self._settings.get("min_wait_days", 2)
        exclude_recipients = self._settings.get("exclude_recipients", [])

        self._logger.info(
            "Starting follow-up detection",
            days_back=days_back,
            min_wait_days=min_wait_days,
            exclude_recipients=exclude_recipients,
        )

        service = PendingRepliesService(
            config=self._container.config,
            credential_manager=self._container.credential_manager,
            db_manager=self._container.db_manager,
        )

        stats = await service.sync(
            days_back=days_back,
            min_wait_days=min_wait_days,
            exclude_recipients=exclude_recipients,
        )

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=stats.get("created", 0),
            details={
                "processed": stats.get("processed", 0),
                "created": stats.get("created", 0),
                "updated": stats.get("updated", 0),
                "skipped": stats.get("skipped", 0),
                "errors": stats.get("errors", 0),
            },
        )


class TranscriptScanJob(Job):
    """Job for scanning meeting transcripts and extracting action items."""

    async def execute(self) -> JobResult:
        """Scan recent meetings for transcripts and extract follow-ups."""
        from executive_copilot.services.transcript_scanner import TranscriptScannerService

        started_at = datetime.utcnow()

        days_back = self._settings.get("days_back", 7)
        use_ai = self._settings.get("use_ai", True)

        self._logger.info("Starting transcript scan", days_back=days_back, use_ai=use_ai)

        ai_service = None
        if use_ai:
            try:
                ai_service = self._container.ai
            except Exception as e:
                self._logger.warning("AI unavailable, using keywords", error=str(e))

        service = TranscriptScannerService(
            config=self._container.config,
            credential_manager=self._container.credential_manager,
            db_manager=self._container.db_manager,
            ai_service=ai_service,
        )

        stats = await service.scan(days_back=days_back)

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=stats.get("follow_ups_created", 0),
            details=stats,
        )


class ObsidianMeetingSyncJob(Job):
    """Write recent Gemini meeting notes into the Obsidian vault."""

    async def execute(self) -> JobResult:
        """Sync meeting notes for the last ``days_back`` days."""
        from executive_copilot.services.obsidian_notes import MeetingNotesSyncService

        started_at = datetime.utcnow()
        days_back = self._settings.get("days_back", 3)

        ai_service = None
        if self._settings.get("use_ai", True):
            try:
                ai_service = self._container.ai
            except Exception as e:
                self._logger.warning("AI unavailable, using Gemini sections", error=str(e))

        service = MeetingNotesSyncService(
            config=self._container.config,
            credential_manager=self._container.credential_manager,
            ai_service=ai_service,
        )
        if self._settings.get("catch_up", True):
            # Reach back to the newest meeting already in the vault, so a stopped
            # container or a failed run never leaves a gap.
            days_back = service.catch_up_days_back(
                minimum=days_back, maximum=self._settings.get("max_days_back", 60)
            )
        self._logger.info("Syncing meeting notes", days_back=days_back)
        result = await service.sync(days_back=days_back)
        if "error" in result:
            return JobResult(
                job_name=self.name,
                status=JobStatus.FAILED,
                started_at=started_at,
                error_message=result["error"],
            )
        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=len(result.get("written", [])),
            details={k: v for k, v in result.items() if k != "written"} | {
                "written": [f"{w['note']} {w['date']}" for w in result.get("written", [])]
            },
        )


class GitHubDocsSyncJob(Job):
    """Mirror GitHub repository docs and index them with the vault."""

    async def execute(self) -> JobResult:
        """Sync changed repositories, then index and embed the new docs."""
        from executive_copilot.services.github_docs import GitHubDocsSync
        from executive_copilot.services.vault_index import build_embedder, build_index

        started_at = datetime.utcnow()
        config = self._container.config
        if not config.github_docs.enabled:
            return JobResult(job_name=self.name, status=JobStatus.SKIPPED, started_at=started_at,
                             details={"reason": "github_docs.enabled is false"})

        ai_service = None
        if self._settings.get("summarize", True):
            try:
                ai_service = self._container.ai
            except Exception as e:
                self._logger.warning("AI unavailable, cards without summary", error=str(e))

        sync = GitHubDocsSync(config, self._container.credential_manager, ai_service)
        stats = await sync.sync(force=self._settings.get("force", False))
        if "error" in stats:
            return JobResult(job_name=self.name, status=JobStatus.FAILED, started_at=started_at,
                             error_message=stats["error"])

        # Index now (and embed) so the first search after a sync is not the slow one.
        index = build_index(config)
        stats["index"] = index.refresh(force=True)
        embedder = build_embedder(config)
        if embedder is not None:
            stats["embedded"] = await index.embed_missing(embedder)
            stats["embedding_tokens"] = embedder.tokens_used

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=stats["files_written"],
            details=stats,
        )


class CommitmentDetectionJob(Job):
    """Job for detecting commitments from emails and transcripts."""

    async def execute(self) -> JobResult:
        """Scan emails for commitments made by/to the user."""
        from executive_copilot.services.commitment_detection import CommitmentDetectionService

        started_at = datetime.utcnow()

        days_back = self._settings.get("days_back", 7)
        use_ai = self._settings.get("use_ai", True)

        self._logger.info("Starting commitment detection", days_back=days_back, use_ai=use_ai)

        ai_service = None
        if use_ai:
            try:
                ai_service = self._container.ai
            except Exception as e:
                self._logger.warning("AI unavailable, using keywords", error=str(e))

        service = CommitmentDetectionService(
            config=self._container.config,
            credential_manager=self._container.credential_manager,
            db_manager=self._container.db_manager,
            ai_service=ai_service,
        )

        stats = await service.scan_emails(days_back=days_back)

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=stats.get("commitments_found", 0),
            details=stats,
        )


class RelationshipScanJob(Job):
    """Job for scanning emails and tracking interactions."""

    async def execute(self) -> JobResult:
        """Scan recent emails to record interactions for relationship tracking."""
        from executive_copilot.services.relationship import RelationshipService

        started_at = datetime.utcnow()

        days_back = self._settings.get("days_back", 7)

        self._logger.info("Starting relationship scan", days_back=days_back)

        service = RelationshipService(
            config=self._container.config,
            credential_manager=self._container.credential_manager,
            db_manager=self._container.db_manager,
        )

        stats = await service.scan_emails_for_interactions(days_back=days_back)

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=stats.get("interactions_created", 0),
            details=stats,
        )


class DelegationDetectionJob(Job):
    """Job for detecting delegations from sent emails."""

    async def execute(self) -> JobResult:
        """Scan sent emails for tasks delegated to others."""
        from executive_copilot.services.delegation_detection import DelegationDetectionService

        started_at = datetime.utcnow()

        days_back = self._settings.get("days_back", 7)
        use_ai = self._settings.get("use_ai", True)

        self._logger.info("Starting delegation detection", days_back=days_back, use_ai=use_ai)

        ai_service = None
        if use_ai:
            try:
                ai_service = self._container.ai
            except Exception as e:
                self._logger.warning("AI unavailable, using keywords", error=str(e))

        service = DelegationDetectionService(
            config=self._container.config,
            credential_manager=self._container.credential_manager,
            db_manager=self._container.db_manager,
            ai_service=ai_service,
        )

        stats = await service.scan_sent_emails(days_back=days_back)

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=stats.get("delegations_found", 0),
            details=stats,
        )


class EmailForwardJob(Job):
    """Job for automatic email forwarding based on rules."""

    async def execute(self) -> JobResult:
        """Process new emails and apply forward rules."""
        from pathlib import Path

        from executive_copilot.services.email_forward import EmailForwardService

        started_at = datetime.utcnow()

        hours_back = self._settings.get("hours_back", 1)

        self._logger.info("Starting email forward processing", hours_back=hours_back)

        service = EmailForwardService(
            config=self._container.config,
            credential_manager=self._container.credential_manager,
            config_dir=Path("config"),
        )

        stats = await service.process_new_emails(hours_back=hours_back)

        status = JobStatus.COMPLETED
        if stats.get("errors"):
            self._logger.warning("Some emails failed to forward", errors=stats["errors"])

        return JobResult(
            job_name=self.name,
            status=status,
            started_at=started_at,
            items_processed=stats.get("emails_forwarded", 0),
            details={
                "emails_checked": stats.get("emails_checked", 0),
                "emails_matched": stats.get("emails_matched", 0),
                "emails_forwarded": stats.get("emails_forwarded", 0),
                "errors": stats.get("errors", []),
                "forwarded_details": stats.get("forwarded_details", []),
            },
        )


class KnowledgeSyncJob(Job):
    """Synchronize reviewed knowledge using the existing scheduler."""

    async def execute(self) -> JobResult:
        from executive_copilot.services.knowledge.locking import KnowledgeBusy
        from executive_copilot.services.knowledge.service import execute_knowledge

        started_at = datetime.utcnow()
        config = self.container.config
        if not config.knowledge.enabled or not config.knowledge.sync_enabled:
            return JobResult(job_name=self.name, status=JobStatus.SKIPPED, started_at=started_at)
        try:
            result = await execute_knowledge(config, self.container.credential_manager, "sync")
        except KnowledgeBusy:
            # Another vault writer (e.g. the meeting-notes sync) holds the lock; next run retries.
            return JobResult(
                job_name=self.name,
                status=JobStatus.SKIPPED,
                started_at=started_at,
                details={"reason": "vault busy"},
            )
        # Open conflicts need review but do not stop other paths from synchronizing.
        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=result.get("published", 0),
            details={
                **{k: result.get(k) for k in ("at", "commit", "published", "documents", "relationships")},
                "conflicts": len(result.get("conflicts") or []),
                "unavailable": len(result.get("unavailable") or []),
                "unreviewed": len(result.get("unreviewed") or []),
            },
        )


class SecurityScanJob(Job):
    """Daily review of advisories for the stack: repo lock files (OSV), watched products (NVD, endoflife)."""

    async def execute(self) -> JobResult:
        from executive_copilot.services.security_scan import SecurityScanService

        started_at = datetime.utcnow()
        config = self._container.config
        if not config.security.enabled:
            return JobResult(job_name=self.name, status=JobStatus.SKIPPED, started_at=started_at,
                             details={"reason": "security.enabled is false"})

        ai_service = None
        if config.security.use_ai and self._settings.get("use_ai", True):
            try:
                ai_service = self._container.ai
            except Exception as e:
                self._logger.warning("AI unavailable, scan without note", error=str(e))

        service = SecurityScanService(config, self._container.credential_manager, ai_service)
        stats = await service.scan()
        if "error" in stats:
            return JobResult(job_name=self.name, status=JobStatus.FAILED, started_at=started_at,
                             error_message=stats["error"], details=stats)
        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=stats.get("findings", 0),
            details=stats,
        )


# Job registry
JOB_TYPES: dict[str, type[Job]] = {
    "email_sync": EmailSyncJob,
    "calendar_sync": CalendarSyncJob,
    "task_sync": TaskSyncJob,
    "news_sync": NewsSyncJob,
    "daily_briefing": DailyBriefingJob,
    "transcript_scan": TranscriptScanJob,
    "follow_up_detection": FollowUpDetectionJob,
    "commitment_detection": CommitmentDetectionJob,
    "relationship_scan": RelationshipScanJob,
    "delegation_detection": DelegationDetectionJob,
    "email_forward": EmailForwardJob,
    "obsidian_meeting_sync": ObsidianMeetingSyncJob,
    "github_docs_sync": GitHubDocsSyncJob,
    "knowledge_sync": KnowledgeSyncJob,
    "security_scan": SecurityScanJob,
}
