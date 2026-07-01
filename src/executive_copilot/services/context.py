"""Context service for determining information sources."""

from uuid import UUID

from executive_copilot.context import (
    ContextEngine,
    ContextRequest,
    ContextResult,
    ContextSource,
)
from executive_copilot.context.models import ContextInputType
from executive_copilot.services.base import Service


class ContextService(Service):
    """Service for context determination.

    Wraps the Context Engine and provides convenient methods for
    determining which information sources to consult.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._engine = ContextEngine(self.container.config)

    async def get_email_context(
        self,
        content: str,
        sender_email: str,
        subject: str | None = None,
    ) -> ContextResult:
        """Determine context sources for an email.

        Args:
            content: Email body
            sender_email: Sender address
            subject: Email subject

        Returns:
            Context result with recommended sources
        """
        return self._engine.analyze_email(content, sender_email, subject)

    async def get_question_context(
        self,
        question: str,
        organization_name: str | None = None,
    ) -> ContextResult:
        """Determine context sources for a question.

        Args:
            question: Question text
            organization_name: Optional organization context

        Returns:
            Context result with recommended sources
        """
        return self._engine.analyze_question(question, organization_name)

    async def get_meeting_context(
        self,
        title: str,
        description: str | None = None,
        attendee_emails: list[str] | None = None,
        organization_id: UUID | None = None,
    ) -> ContextResult:
        """Determine context sources for a meeting.

        Args:
            title: Meeting title
            description: Meeting description
            attendee_emails: Attendee email addresses
            organization_id: Related organization

        Returns:
            Context result with recommended sources
        """
        content = f"{title}\n\n{description or ''}"

        # Try to detect organization from attendees
        org_name = None
        sender_domain = None
        if attendee_emails:
            # Use first external attendee's domain
            for email in attendee_emails:
                domain = email.split("@")[-1] if "@" in email else None
                if domain:
                    org_config = self.container.config.get_organization_by_domain(domain)
                    if org_config:
                        org_name = org_config.name
                        sender_domain = domain
                        break

        request = ContextRequest(
            input_type=ContextInputType.MEETING,
            content=content,
            organization_id=organization_id,
            organization_name=org_name,
            sender_domain=sender_domain,
        )

        return self._engine.analyze(request)

    async def get_task_context(
        self,
        title: str,
        description: str | None = None,
        project_name: str | None = None,
        organization_id: UUID | None = None,
    ) -> ContextResult:
        """Determine context sources for a task.

        Args:
            title: Task title
            description: Task description
            project_name: Project name
            organization_id: Related organization

        Returns:
            Context result with recommended sources
        """
        content = f"{title}\n\n{description or ''}"
        if project_name:
            content = f"[{project_name}] {content}"

        request = ContextRequest(
            input_type=ContextInputType.TASK,
            content=content,
            organization_id=organization_id,
        )

        return self._engine.analyze(request)

    async def gather_context(
        self,
        result: ContextResult,
    ) -> dict[ContextSource, object]:
        """Gather actual context data from recommended sources.

        Args:
            result: Context result with source recommendations

        Returns:
            Dictionary mapping sources to their gathered data
        """
        gathered: dict[ContextSource, object] = {}

        for source in result.ordered_sources:
            try:
                data = await self._gather_from_source(source, result)
                if data:
                    gathered[source] = data
            except Exception as e:
                self._logger.error(
                    "Failed to gather from source",
                    source=source.value,
                    error=str(e),
                )

        return gathered

    async def _gather_from_source(
        self,
        source: ContextSource,
        result: ContextResult,
    ) -> object | None:
        """Gather data from a specific source.

        Args:
            source: Source to gather from
            result: Context result with query hints

        Returns:
            Gathered data or None
        """
        match source:
            case ContextSource.MEMORY:
                if result.detected_organization_id:
                    return await self.container.memory.get_organization_context(
                        result.detected_organization_id
                    )
                return await self.container.memory.get_important_memories()

            case ContextSource.ORGANIZATIONS:
                if result.detected_organization_id:
                    from executive_copilot.database.unit_of_work import unit_of_work

                    async with unit_of_work(self.container.db_manager) as uow:
                        return await uow.organizations.get_by_id(
                            result.detected_organization_id
                        )
                return None

            case ContextSource.PEOPLE:
                if result.detected_organization_id:
                    from executive_copilot.database.unit_of_work import unit_of_work

                    async with unit_of_work(self.container.db_manager) as uow:
                        return await uow.persons.find_by_organization(
                            result.detected_organization_id
                        )
                return None

            case ContextSource.PREVIOUS_THREADS:
                if result.detected_organization_id:
                    from executive_copilot.database.unit_of_work import unit_of_work

                    async with unit_of_work(self.container.db_manager) as uow:
                        return await uow.threads.get_by_organization(
                            result.detected_organization_id
                        )
                return None

            case ContextSource.CALENDAR:
                return await self.container.calendar.get_upcoming_events()

            case ContextSource.TASKS:
                return await self.container.tasks.get_my_tasks()

            case _:
                self._logger.debug("Source not implemented", source=source.value)
                return None
