"""Provider interfaces for Executive Copilot.

Providers are responsible for communicating with external systems.
They must never contain business logic - only data fetching and mapping.

All provider-specific data must be mapped to domain models before
returning from provider methods.
"""

from executive_copilot.providers.base.provider import Provider, ProviderCapability, ProviderInfo
from executive_copilot.providers.base.registry import ProviderRegistry

__all__ = [
    "Provider",
    "ProviderCapability",
    "ProviderInfo",
    "ProviderRegistry",
]
