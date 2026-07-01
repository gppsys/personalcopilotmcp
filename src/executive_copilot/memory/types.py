"""Memory layer data types."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MemoryType(StrEnum):
    """Types of memory entries."""

    ORGANIZATION = "organization"
    PERSON = "person"
    CUSTOMER = "customer"
    TOPIC = "topic"
    DECISION = "decision"
    CONVERSATION = "conversation"
    FOLLOW_UP = "follow_up"
    FACT = "fact"
    PREFERENCE = "preference"
    RELATIONSHIP = "relationship"


class MemoryEntry(BaseModel):
    """A memory entry in the knowledge base."""

    id: UUID = Field(default_factory=uuid4, description="Entry ID")
    memory_type: MemoryType = Field(description="Type of memory")

    # Content
    title: str = Field(description="Entry title")
    content: str = Field(description="Entry content")
    summary: str | None = Field(default=None, description="Short summary")

    # Classification
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    keywords: list[str] = Field(default_factory=list, description="Extracted keywords")
    topics: list[str] = Field(default_factory=list, description="Related topics")

    # Relations
    organization_id: UUID | None = Field(default=None, description="Related organization")
    person_ids: list[UUID] = Field(default_factory=list, description="Related people")
    related_entries: list[UUID] = Field(default_factory=list, description="Related memories")

    # Source tracking
    source_type: str | None = Field(default=None, description="Source type")
    source_id: str | None = Field(default=None, description="Source entity ID")
    source_url: str | None = Field(default=None, description="Source URL")

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)
    last_accessed_at: datetime | None = Field(default=None)
    valid_from: datetime | None = Field(default=None, description="When info became valid")
    valid_until: datetime | None = Field(default=None, description="When info expires")

    # Importance and confidence
    importance: int = Field(default=5, ge=1, le=10, description="Importance level")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in accuracy",
    )

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    @property
    def is_expired(self) -> bool:
        """Check if memory entry has expired."""
        if self.valid_until is None:
            return False
        return datetime.utcnow() > self.valid_until

    @property
    def age_days(self) -> int:
        """Get age of entry in days."""
        delta = datetime.utcnow() - self.created_at
        return delta.days


class MemoryQuery(BaseModel):
    """Query for searching memory entries."""

    # Text search
    query: str | None = Field(default=None, description="Full-text search query")
    keywords: list[str] = Field(default_factory=list, description="Required keywords")

    # Type filtering
    memory_types: list[MemoryType] = Field(
        default_factory=list,
        description="Filter by memory types",
    )

    # Relation filtering
    organization_id: UUID | None = Field(default=None, description="Filter by organization")
    person_id: UUID | None = Field(default=None, description="Filter by person")
    tags: list[str] = Field(default_factory=list, description="Required tags")
    topics: list[str] = Field(default_factory=list, description="Required topics")

    # Time filtering
    created_after: datetime | None = Field(default=None, description="Created after")
    created_before: datetime | None = Field(default=None, description="Created before")
    include_expired: bool = Field(default=False, description="Include expired entries")

    # Importance filtering
    min_importance: int | None = Field(default=None, ge=1, le=10)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    # Pagination
    limit: int = Field(default=20, ge=1, le=100, description="Maximum results")
    offset: int = Field(default=0, ge=0, description="Results offset")

    # Sorting
    sort_by: str = Field(default="relevance", description="Sort field")
    sort_order: str = Field(default="desc", description="Sort order")


class MemorySearchResult(BaseModel):
    """Result of a memory search."""

    entries: list[MemoryEntry] = Field(description="Matching entries")
    total_count: int = Field(description="Total matching entries")
    has_more: bool = Field(default=False, description="More results available")

    # Search metadata
    query: MemoryQuery = Field(description="Original query")
    search_time_ms: int = Field(default=0, description="Search time in milliseconds")
