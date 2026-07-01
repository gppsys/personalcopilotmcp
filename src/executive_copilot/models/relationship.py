"""Relationship tracking models."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from executive_copilot.models.base import DomainModel, ValueObject


class InteractionType(StrEnum):
    """Type of interaction with a person."""

    EMAIL_SENT = "email_sent"
    EMAIL_RECEIVED = "email_received"
    MEETING = "meeting"
    CALL = "call"
    MESSAGE = "message"
    OTHER = "other"


class InteractionDirection(StrEnum):
    """Direction of the interaction."""

    INBOUND = "inbound"  # They contacted us
    OUTBOUND = "outbound"  # We contacted them
    MUTUAL = "mutual"  # Meeting, call, etc.


class Interaction(DomainModel):
    """A recorded interaction with a person.

    Tracks emails, meetings, calls, and other touchpoints
    for relationship intelligence.
    """

    person_email: str = Field(description="Email of the person interacted with")
    person_name: str | None = Field(default=None, description="Name at time of interaction")

    interaction_type: InteractionType = Field(description="Type of interaction")
    direction: InteractionDirection = Field(description="Direction of interaction")

    occurred_at: datetime = Field(description="When the interaction occurred")

    subject: str | None = Field(default=None, description="Subject/title of interaction")
    summary: str | None = Field(default=None, description="Brief summary")
    snippet: str | None = Field(default=None, description="Text excerpt")

    source_type: str | None = Field(default=None, description="Source: email, calendar, etc.")
    source_id: UUID | None = Field(default=None, description="ID of source entity")

    account_id: str | None = Field(default=None, description="Account used for interaction")
    organization_id: UUID | None = Field(default=None, description="Related organization")

    tags: list[str] = Field(default_factory=list, description="Tags for categorization")


class RelationshipSummary(ValueObject):
    """Summary of relationship with a person."""

    person_email: str
    person_name: str | None = None
    organization_name: str | None = None
    importance: int = 5

    last_contact_at: datetime | None = None
    last_contact_type: InteractionType | None = None
    last_contact_subject: str | None = None

    days_since_contact: int | None = None
    total_interactions: int = 0
    inbound_count: int = 0
    outbound_count: int = 0

    open_commitments: int = 0
    open_follow_ups: int = 0

    is_stale: bool = False
    stale_threshold_days: int = 30

    @property
    def needs_attention(self) -> bool:
        """Check if relationship needs attention."""
        return self.is_stale or self.open_commitments > 0 or self.open_follow_ups > 0
