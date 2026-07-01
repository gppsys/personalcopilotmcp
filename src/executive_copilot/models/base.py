"""Base model classes and common types."""

from datetime import datetime
from typing import Annotated, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

ModelT = TypeVar("ModelT", bound="DomainModel")

# Common field annotations
Id = Annotated[UUID, Field(default_factory=uuid4, description="Unique identifier")]
CreatedAt = Annotated[datetime, Field(default_factory=datetime.utcnow, description="Creation timestamp")]
UpdatedAt = Annotated[datetime | None, Field(default=None, description="Last update timestamp")]


class DomainModel(BaseModel):
    """Base class for all domain models.

    Provides common configuration and utilities shared across all models.
    Domain models are immutable by default to ensure data integrity.
    """

    model_config = ConfigDict(
        frozen=True,
        use_enum_values=False,
        validate_assignment=True,
        str_strip_whitespace=True,
        extra="forbid",
    )

    id: Id
    created_at: CreatedAt
    updated_at: UpdatedAt

    def with_update(self: ModelT, **kwargs: object) -> ModelT:
        """Create a new instance with updated fields.

        Since models are immutable, this creates a copy with modifications.
        Automatically sets the updated_at timestamp.
        """
        if "updated_at" not in kwargs:
            kwargs["updated_at"] = datetime.utcnow()
        return self.model_copy(update=kwargs)


class ValueObject(BaseModel):
    """Base class for value objects.

    Value objects are immutable and compared by their attributes.
    They have no identity and represent descriptive aspects of the domain.
    """

    model_config = ConfigDict(
        frozen=True,
        use_enum_values=False,
        validate_assignment=True,
        str_strip_whitespace=True,
        extra="forbid",
    )


class ProviderReference(ValueObject):
    """Reference to an entity in an external provider.

    Used to maintain mapping between domain models and their source systems.
    """

    provider_name: str = Field(description="Name of the provider (e.g., 'outlook', 'gmail')")
    external_id: str = Field(description="ID in the external system")
    external_url: str | None = Field(default=None, description="URL to the entity in the external system")
    thread_id: str | None = Field(default=None, description="Thread/conversation ID for email providers")
    raw_data: dict[str, object] | None = Field(
        default=None,
        description="Original provider data, stored for debugging and edge cases",
    )
