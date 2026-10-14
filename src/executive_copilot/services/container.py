"""Dependency injection container for services."""

from typing import TYPE_CHECKING, TypeVar

from executive_copilot.config import AppConfig
from executive_copilot.credentials import CredentialManager
from executive_copilot.database import DatabaseManager
from executive_copilot.logging import get_logger
from executive_copilot.providers.base import ProviderRegistry

if TYPE_CHECKING:
    from executive_copilot.services.ai import AIService
    from executive_copilot.services.calendar import CalendarService
    from executive_copilot.services.context import ContextService
    from executive_copilot.services.email import EmailService
    from executive_copilot.services.memory import MemoryService
    from executive_copilot.services.news import NewsService
    from executive_copilot.services.search import SearchService
    from executive_copilot.services.task import TaskService

logger = get_logger(__name__)

T = TypeVar("T")


class ServiceContainer:
    """Dependency injection container for services.

    Provides lazy initialization of services and manages
    their lifecycle. Services access each other through
    this container to avoid circular dependencies.
    """

    def __init__(
        self,
        config: AppConfig,
        db_manager: DatabaseManager,
        provider_registry: ProviderRegistry,
        credential_manager: CredentialManager | None = None,
    ) -> None:
        """Initialize container with core dependencies.

        Args:
            config: Application configuration
            db_manager: Database manager
            provider_registry: Provider registry
            credential_manager: Credential manager (created if not provided)
        """
        self._config = config
        self._db_manager = db_manager
        self._provider_registry = provider_registry
        self._credential_manager = credential_manager or CredentialManager(
            config.config_dir / ".credentials"
        )

        # Service instances (lazy initialized)
        self._email_service: EmailService | None = None
        self._calendar_service: CalendarService | None = None
        self._task_service: TaskService | None = None
        self._context_service: ContextService | None = None
        self._memory_service: MemoryService | None = None
        self._news_service: NewsService | None = None
        self._search_service: SearchService | None = None
        self._ai_service: AIService | None = None

    @property
    def config(self) -> AppConfig:
        """Get application configuration."""
        return self._config

    @property
    def db_manager(self) -> DatabaseManager:
        """Get database manager."""
        return self._db_manager

    @property
    def provider_registry(self) -> ProviderRegistry:
        """Get provider registry."""
        return self._provider_registry

    @property
    def credential_manager(self) -> CredentialManager:
        """Get credential manager."""
        return self._credential_manager

    @property
    def email(self) -> "EmailService":
        """Get email service."""
        if self._email_service is None:
            from executive_copilot.services.email import EmailService

            self._email_service = EmailService(self)
            logger.debug("Initialized EmailService")
        return self._email_service

    @property
    def calendar(self) -> "CalendarService":
        """Get calendar service."""
        if self._calendar_service is None:
            from executive_copilot.services.calendar import CalendarService

            self._calendar_service = CalendarService(self)
            logger.debug("Initialized CalendarService")
        return self._calendar_service

    @property
    def tasks(self) -> "TaskService":
        """Get task service."""
        if self._task_service is None:
            from executive_copilot.services.task import TaskService

            self._task_service = TaskService(self)
            logger.debug("Initialized TaskService")
        return self._task_service

    @property
    def context(self) -> "ContextService":
        """Get context service."""
        if self._context_service is None:
            from executive_copilot.services.context import ContextService

            self._context_service = ContextService(self)
            logger.debug("Initialized ContextService")
        return self._context_service

    @property
    def memory(self) -> "MemoryService":
        """Get memory service."""
        if self._memory_service is None:
            from executive_copilot.services.memory import MemoryService

            self._memory_service = MemoryService(self)
            logger.debug("Initialized MemoryService")
        return self._memory_service

    @property
    def news(self) -> "NewsService":
        """Get news service."""
        if self._news_service is None:
            from executive_copilot.services.news import NewsService

            self._news_service = NewsService(self)
            logger.debug("Initialized NewsService")
        return self._news_service

    @property
    def search(self) -> "SearchService":
        """Get search service."""
        if self._search_service is None:
            from executive_copilot.services.search import SearchService

            self._search_service = SearchService(self)
            logger.debug("Initialized SearchService")
        return self._search_service

    @property
    def ai(self) -> "AIService":
        """Get AI service."""
        if self._ai_service is None:
            from executive_copilot.services.ai import AIService

            self._ai_service = AIService(self)
            logger.debug("Initialized AIService")
        return self._ai_service

    async def shutdown(self) -> None:
        """Shutdown all services and cleanup resources."""
        logger.info("Shutting down service container")
        await self._provider_registry.shutdown()
        await self._db_manager.close()
        logger.info("Service container shutdown complete")
