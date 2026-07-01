"""Base provider interface and types."""

from abc import ABC, abstractmethod
from enum import StrEnum, auto
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from executive_copilot.config import ProviderConfig
from executive_copilot.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class ProviderCapability(StrEnum):
    """Capabilities a provider can support."""

    # Email capabilities
    READ_EMAILS = auto()
    SEND_EMAILS = auto()
    DRAFT_EMAILS = auto()
    SEARCH_EMAILS = auto()
    MANAGE_LABELS = auto()

    # Calendar capabilities
    READ_EVENTS = auto()
    CREATE_EVENTS = auto()
    UPDATE_EVENTS = auto()
    DELETE_EVENTS = auto()
    RESPOND_TO_EVENTS = auto()

    # Task capabilities
    READ_TASKS = auto()
    CREATE_TASKS = auto()
    UPDATE_TASKS = auto()
    DELETE_TASKS = auto()
    MANAGE_PROJECTS = auto()

    # Document capabilities
    READ_DOCUMENTS = auto()
    UPLOAD_DOCUMENTS = auto()
    SEARCH_DOCUMENTS = auto()

    # News capabilities
    FETCH_ARTICLES = auto()
    SEARCH_NEWS = auto()

    # Search capabilities
    FULL_TEXT_SEARCH = auto()
    SEMANTIC_SEARCH = auto()

    # Auth capabilities
    OAUTH2 = auto()
    API_KEY = auto()
    BASIC_AUTH = auto()


class ProviderInfo(BaseModel):
    """Information about a provider."""

    name: str = Field(description="Provider unique name")
    display_name: str = Field(description="Human-readable name")
    provider_type: str = Field(description="Type of provider")
    version: str = Field(default="1.0.0", description="Provider version")
    capabilities: list[ProviderCapability] = Field(
        default_factory=list,
        description="Supported capabilities",
    )
    description: str | None = Field(default=None, description="Provider description")
    requires_auth: bool = Field(default=True, description="Whether auth is required")
    auth_type: str | None = Field(default=None, description="Type of authentication")


class Provider(ABC, Generic[T]):
    """Base class for all providers.

    Providers are responsible for:
    1. Communicating with external systems
    2. Mapping external data to domain models
    3. Handling authentication and connection management

    Providers must NOT contain business logic. They only fetch
    and transform data.
    """

    def __init__(self, config: ProviderConfig) -> None:
        """Initialize provider with configuration.

        Args:
            config: Provider-specific configuration
        """
        self._config = config
        self._logger = get_logger(
            f"{self.__class__.__module__}.{self.__class__.__name__}",
            provider=self.info.name,
        )

    @property
    @abstractmethod
    def info(self) -> ProviderInfo:
        """Get provider information.

        Returns:
            ProviderInfo describing this provider's capabilities.
        """
        ...

    @property
    def name(self) -> str:
        """Get provider name."""
        return self.info.name

    @property
    def config(self) -> ProviderConfig:
        """Get provider configuration."""
        return self._config

    def has_capability(self, capability: ProviderCapability) -> bool:
        """Check if provider supports a capability."""
        return capability in self.info.capabilities

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the external system.

        This method should handle authentication and any
        initialization required before making requests.

        Raises:
            AuthenticationError: If authentication fails
            ProviderError: If connection fails
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the external system.

        Release any resources held by the provider.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider connection is healthy.

        Returns:
            True if connection is healthy, False otherwise.
        """
        ...

    async def __aenter__(self) -> "Provider[T]":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()

    def _translate_exception(self, error: Exception, operation: str) -> Exception:
        """Translate provider-specific exceptions to domain exceptions.

        Subclasses should override this to handle their specific
        exception types. Default implementation wraps in ProviderError.
        """
        from executive_copilot.exceptions import ProviderError

        return ProviderError(
            str(error),
            provider_name=self.name,
            operation=operation,
            cause=error,
        )
