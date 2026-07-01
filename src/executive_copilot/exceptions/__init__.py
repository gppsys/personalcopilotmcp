"""Exception hierarchy for Executive Copilot.

All exceptions should inherit from CopilotError to allow
catching all application-specific errors uniformly.

Providers should translate their specific exceptions into
these domain exceptions.
"""

from executive_copilot.exceptions.base import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    CopilotError,
    EntityNotFoundError,
    ExternalServiceError,
    ProviderError,
    RateLimitError,
    RepositoryError,
    ServiceError,
    SynchronizationError,
    ValidationError,
)

__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "ConfigurationError",
    "CopilotError",
    "EntityNotFoundError",
    "ExternalServiceError",
    "ProviderError",
    "RateLimitError",
    "RepositoryError",
    "ServiceError",
    "SynchronizationError",
    "ValidationError",
]
