"""Automation engine using APScheduler."""

from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from executive_copilot.automation.jobs import JOB_TYPES, Job, JobResult
from executive_copilot.config import AppConfig, AutomationConfig
from executive_copilot.logging import get_logger
from executive_copilot.services.container import ServiceContainer

logger = get_logger(__name__)


class AutomationEngine:
    """Engine for managing scheduled automation jobs.

    Uses APScheduler to run jobs on configured schedules.
    """

    def __init__(
        self,
        config: AppConfig,
        container: ServiceContainer,
    ) -> None:
        """Initialize automation engine.

        Args:
            config: Application configuration
            container: Service container
        """
        self._config = config
        self._container = container
        self._scheduler = AsyncIOScheduler()
        self._jobs: dict[str, Job] = {}
        self._history: list[JobResult] = []

    async def start(self) -> None:
        """Start the automation engine."""
        logger.info("Starting automation engine")

        # Register configured jobs
        for job_config in self._config.automation_jobs:
            if job_config.enabled:
                self._register_job(job_config)

        # Start scheduler
        self._scheduler.start()
        logger.info(
            "Automation engine started",
            job_count=len(self._jobs),
        )

    async def stop(self) -> None:
        """Stop the automation engine."""
        logger.info("Stopping automation engine")
        self._scheduler.shutdown(wait=True)
        logger.info("Automation engine stopped")

    def _register_job(self, config: AutomationConfig) -> None:
        """Register a job with the scheduler.

        Args:
            config: Job configuration
        """
        job_type = JOB_TYPES.get(config.job_type)
        if job_type is None:
            logger.warning(
                "Unknown job type",
                job_name=config.job_name,
                job_type=config.job_type,
            )
            return

        # Create job instance
        job = job_type(
            name=config.job_name,
            container=self._container,
            settings=config.settings,
        )
        self._jobs[config.job_name] = job

        # Parse cron schedule
        try:
            trigger = CronTrigger.from_crontab(config.schedule)
        except ValueError as e:
            logger.error(
                "Invalid cron schedule",
                job_name=config.job_name,
                schedule=config.schedule,
                error=str(e),
            )
            return

        # Add to scheduler
        self._scheduler.add_job(
            self._run_job,
            trigger=trigger,
            args=[config.job_name],
            id=config.job_name,
            name=config.job_name,
            replace_existing=True,
        )

        logger.info(
            "Registered job",
            job_name=config.job_name,
            job_type=config.job_type,
            schedule=config.schedule,
        )

    async def _run_job(self, job_name: str) -> None:
        """Run a scheduled job.

        Args:
            job_name: Name of job to run
        """
        job = self._jobs.get(job_name)
        if job is None:
            logger.error("Job not found", job_name=job_name)
            return

        result = await job.run()
        self._history.append(result)

        # Keep only last 100 results
        if len(self._history) > 100:
            self._history = self._history[-100:]

    async def run_job_now(self, job_name: str) -> JobResult | None:
        """Run a job immediately.

        Args:
            job_name: Name of job to run

        Returns:
            Job result if job exists
        """
        job = self._jobs.get(job_name)
        if job is None:
            logger.error("Job not found", job_name=job_name)
            return None

        result = await job.run()
        self._history.append(result)
        return result

    def get_job_status(self, job_name: str) -> dict[str, Any] | None:
        """Get status of a job.

        Args:
            job_name: Job name

        Returns:
            Job status info
        """
        if job_name not in self._jobs:
            return None

        # Get scheduler job info
        scheduler_job = self._scheduler.get_job(job_name)
        next_run = None
        if scheduler_job:
            next_run = scheduler_job.next_run_time

        # Get last result
        last_result = None
        for result in reversed(self._history):
            if result.job_name == job_name:
                last_result = result
                break

        return {
            "job_name": job_name,
            "next_run": next_run.isoformat() if next_run else None,
            "last_run": last_result.model_dump() if last_result else None,
        }

    def list_jobs(self) -> list[dict[str, Any]]:
        """List all registered jobs.

        Returns:
            List of job info dicts
        """
        jobs: list[dict[str, Any]] = []
        for job_name in self._jobs:
            status = self.get_job_status(job_name)
            if status:
                jobs.append(status)
        return jobs

    def get_history(
        self,
        job_name: str | None = None,
        limit: int = 20,
    ) -> list[JobResult]:
        """Get job execution history.

        Args:
            job_name: Filter by job name
            limit: Maximum results

        Returns:
            Job results
        """
        results = self._history
        if job_name:
            results = [r for r in results if r.job_name == job_name]
        return list(reversed(results[-limit:]))
