"""Base exception classes for Executive Copilot."""

from typing import Any


class CopilotError(Exception):
    """Base exception for all Executive Copilot errors.

    All application-specific exceptions should inherit from this class.
    """

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.cause = cause

    def __str__(self) -> str:
        parts = [self.message]
        if self.details:
            parts.append(f"Details: {self.details}")
        if self.cause:
            parts.append(f"Caused by: {self.cause}")
        return " | ".join(parts)


class ValidationError(CopilotError):
    """Raised when input validation fails."""

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        value: Any = None,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        if field:
            details["field"] = field
        if value is not None:
            details["value"] = str(value)[:100]  # Truncate for safety
        super().__init__(message, details=details, **kwargs)
        self.field = field
        self.value = value


class ConfigurationError(CopilotError):
    """Raised when configuration is invalid or missing."""

    def __init__(
        self,
        message: str,
        *,
        config_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        if config_key:
            details["config_key"] = config_key
        super().__init__(message, details=details, **kwargs)
        self.config_key = config_key


class AuthenticationError(CopilotError):
    """Raised when authentication fails."""

    def __init__(
        self,
        message: str = "Authentication failed",
        *,
        provider: str | None = None,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        if provider:
            details["provider"] = provider
        super().__init__(message, details=details, **kwargs)
        self.provider = provider


class AuthorizationError(CopilotError):
    """Raised when user lacks permission for an operation."""

    def __init__(
        self,
        message: str = "Not authorized",
        *,
        required_permission: str | None = None,
        resource: str | None = None,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        if required_permission:
            details["required_permission"] = required_permission
        if resource:
            details["resource"] = resource
        super().__init__(message, details=details, **kwargs)
        self.required_permission = required_permission
        self.resource = resource


class EntityNotFoundError(CopilotError):
    """Raised when a requested entity does not exist."""

    def __init__(
        self,
        message: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        if entity_type:
            details["entity_type"] = entity_type
        if entity_id:
            details["entity_id"] = entity_id
        super().__init__(message, details=details, **kwargs)
        self.entity_type = entity_type
        self.entity_id = entity_id


class ProviderError(CopilotError):
    """Base class for provider-related errors.

    Providers should translate their specific exceptions into
    subclasses of this exception.
    """

    def __init__(
        self,
        message: str,
        *,
        provider_name: str,
        operation: str | None = None,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        details["provider"] = provider_name
        if operation:
            details["operation"] = operation
        super().__init__(message, details=details, **kwargs)
        self.provider_name = provider_name
        self.operation = operation


class ExternalServiceError(ProviderError):
    """Raised when an external service returns an error."""

    def __init__(
        self,
        message: str,
        *,
        provider_name: str,
        status_code: int | None = None,
        response_body: str | None = None,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        if status_code:
            details["status_code"] = status_code
        if response_body:
            details["response_body"] = response_body[:500]  # Truncate
        super().__init__(message, provider_name=provider_name, details=details, **kwargs)
        self.status_code = status_code
        self.response_body = response_body


class RateLimitError(ProviderError):
    """Raised when a rate limit is exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        provider_name: str,
        retry_after: int | None = None,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        if retry_after:
            details["retry_after_seconds"] = retry_after
        super().__init__(message, provider_name=provider_name, details=details, **kwargs)
        self.retry_after = retry_after


class SynchronizationError(ProviderError):
    """Raised when data synchronization fails."""

    def __init__(
        self,
        message: str,
        *,
        provider_name: str,
        sync_type: str | None = None,
        partial_success: bool = False,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        if sync_type:
            details["sync_type"] = sync_type
        details["partial_success"] = partial_success
        super().__init__(message, provider_name=provider_name, details=details, **kwargs)
        self.sync_type = sync_type
        self.partial_success = partial_success


class ServiceError(CopilotError):
    """Raised when a service operation fails."""

    def __init__(
        self,
        message: str,
        *,
        service_name: str,
        operation: str | None = None,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        details["service"] = service_name
        if operation:
            details["operation"] = operation
        super().__init__(message, details=details, **kwargs)
        self.service_name = service_name
        self.operation = operation


class RepositoryError(CopilotError):
    """Raised when a repository operation fails."""

    def __init__(
        self,
        message: str,
        *,
        repository_name: str,
        operation: str | None = None,
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        details["repository"] = repository_name
        if operation:
            details["operation"] = operation
        super().__init__(message, details=details, **kwargs)
        self.repository_name = repository_name
        self.operation = operation
