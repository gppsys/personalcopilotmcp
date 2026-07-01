"""Provider registry for dependency injection."""

from typing import Any, TypeVar

from executive_copilot.config import ProviderConfig
from executive_copilot.exceptions import ConfigurationError
from executive_copilot.logging import get_logger
from executive_copilot.providers.base.provider import Provider

logger = get_logger(__name__)

T = TypeVar("T", bound=Provider[Any])


class ProviderRegistry:
    """Registry for provider implementations.

    Supports registration of provider classes and creation of
    provider instances based on configuration.

    This is the primary mechanism for dependency injection of providers.
    """

    def __init__(self) -> None:
        self._providers: dict[str, dict[str, type[Provider[Any]]]] = {}
        self._instances: dict[str, Provider[Any]] = {}

    def register(
        self,
        provider_type: str,
        provider_name: str,
        provider_class: type[Provider[Any]],
    ) -> None:
        """Register a provider implementation.

        Args:
            provider_type: Type of provider (email, calendar, etc.)
            provider_name: Implementation name (outlook, gmail, etc.)
            provider_class: Provider class to register
        """
        if provider_type not in self._providers:
            self._providers[provider_type] = {}

        self._providers[provider_type][provider_name] = provider_class
        logger.debug(
            "Registered provider",
            provider_type=provider_type,
            provider_name=provider_name,
            provider_class=provider_class.__name__,
        )

    def get_class(
        self,
        provider_type: str,
        provider_name: str,
    ) -> type[Provider[Any]]:
        """Get a registered provider class.

        Args:
            provider_type: Type of provider
            provider_name: Implementation name

        Returns:
            Provider class

        Raises:
            ConfigurationError: If provider not found
        """
        if provider_type not in self._providers:
            raise ConfigurationError(
                f"No providers registered for type: {provider_type}",
                config_key=f"providers.{provider_type}",
            )

        if provider_name not in self._providers[provider_type]:
            available = list(self._providers[provider_type].keys())
            raise ConfigurationError(
                f"Provider not found: {provider_name}. Available: {available}",
                config_key=f"providers.{provider_type}.{provider_name}",
            )

        return self._providers[provider_type][provider_name]

    def create(
        self,
        config: ProviderConfig,
    ) -> Provider[Any]:
        """Create a provider instance from configuration.

        Args:
            config: Provider configuration

        Returns:
            Configured provider instance
        """
        provider_class = self.get_class(config.provider_type, config.provider_name)
        return provider_class(config)

    def get_or_create(
        self,
        config: ProviderConfig,
        instance_key: str | None = None,
    ) -> Provider[Any]:
        """Get an existing instance or create a new one.

        Args:
            config: Provider configuration
            instance_key: Optional key for instance caching.
                         Defaults to "{provider_type}:{provider_name}:{account_id}"

        Returns:
            Provider instance (may be cached)
        """
        if instance_key is None:
            instance_key = f"{config.provider_type}:{config.provider_name}:{config.account_id}"

        if instance_key not in self._instances:
            self._instances[instance_key] = self.create(config)
            logger.debug("Created provider instance", instance_key=instance_key)

        return self._instances[instance_key]

    def list_providers(self, provider_type: str | None = None) -> list[tuple[str, str]]:
        """List all registered providers.

        Args:
            provider_type: Optional filter by type

        Returns:
            List of (provider_type, provider_name) tuples
        """
        result: list[tuple[str, str]] = []
        for ptype, providers in self._providers.items():
            if provider_type is None or ptype == provider_type:
                for pname in providers:
                    result.append((ptype, pname))
        return result

    async def shutdown(self) -> None:
        """Disconnect and cleanup all cached instances."""
        for key, instance in self._instances.items():
            try:
                await instance.disconnect()
                logger.debug("Disconnected provider", instance_key=key)
            except Exception as e:
                logger.error("Error disconnecting provider", instance_key=key, error=str(e))
        self._instances.clear()


# Global registry instance
_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    """Get the global provider registry instance."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
