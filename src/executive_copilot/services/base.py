"""Base service class."""

from abc import ABC
from typing import TYPE_CHECKING

from executive_copilot.logging import get_logger

if TYPE_CHECKING:
    from executive_copilot.services.container import ServiceContainer


class Service(ABC):
    """Base class for all services.

    Services orchestrate business logic using providers and repositories.
    They should not directly access databases or external APIs.
    """

    def __init__(self, container: "ServiceContainer") -> None:
        """Initialize service with container for dependency access.

        Args:
            container: Service container for accessing other services and providers
        """
        self._container = container
        self._logger = get_logger(f"{self.__class__.__module__}.{self.__class__.__name__}")

    @property
    def container(self) -> "ServiceContainer":
        """Get the service container."""
        return self._container
