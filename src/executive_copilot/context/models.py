"""Context engine data models."""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ContextInputType(StrEnum):
    """Type of input to analyze for context."""

    EMAIL = "email"
    QUESTION = "question"
    DRAFT_REPLY = "draft_reply"
    TASK = "task"
    MEETING = "meeting"
    GENERAL = "general"


class ContextSource(StrEnum):
    """Information sources that can be consulted."""

    # Provider sources
    ASANA = "asana"
    OUTLOOK = "outlook"
    GMAIL = "gmail"
    GOOGLE_CALENDAR = "google_calendar"
    OUTLOOK_CALENDAR = "outlook_calendar"
    GOOGLE_DRIVE = "google_drive"
    ONEDRIVE = "onedrive"
    SLACK = "slack"
    GITHUB = "github"

    # Memory sources
    MEMORY = "memory"
    ORGANIZATIONS = "organizations"
    PEOPLE = "people"
    CONVERSATIONS = "conversations"
    DECISIONS = "decisions"
    FOLLOW_UPS = "follow_ups"

    # Content sources
    DOCUMENTATION = "documentation"
    PREVIOUS_THREADS = "previous_threads"
    CALENDAR = "calendar"
    TASKS = "tasks"
    NEWS = "news"

    # External sources
    WEB_SEARCH = "web_search"
    WEBSITE = "website"


class ContextRequest(BaseModel):
    """Request for context determination."""

    # Input to analyze
    input_type: ContextInputType = Field(description="Type of input")
    content: str = Field(description="Content to analyze")

    # Known context
    organization_id: UUID | None = Field(default=None, description="Known organization")
    organization_name: str | None = Field(default=None, description="Organization name hint")
    person_id: UUID | None = Field(default=None, description="Known person")
    sender_email: str | None = Field(default=None, description="Email sender address")
    sender_domain: str | None = Field(default=None, description="Email sender domain")

    # Related entities
    thread_id: UUID | None = Field(default=None, description="Related thread")
    task_id: UUID | None = Field(default=None, description="Related task")
    meeting_id: UUID | None = Field(default=None, description="Related meeting")

    # Additional hints
    keywords: list[str] = Field(default_factory=list, description="Extracted keywords")
    topics: list[str] = Field(default_factory=list, description="Detected topics")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class SourceRecommendation(BaseModel):
    """A recommended context source to consult."""

    source: ContextSource = Field(description="Source to consult")
    priority: int = Field(default=0, description="Priority (higher = more important)")
    reason: str = Field(description="Why this source was recommended")
    query_hint: str | None = Field(default=None, description="Suggested query for the source")
    required: bool = Field(default=False, description="Whether this source is required")


class ContextResult(BaseModel):
    """Result of context analysis."""

    # Original request
    request: ContextRequest = Field(description="Original request")

    # Detected context
    detected_organization_id: UUID | None = Field(
        default=None,
        description="Detected organization",
    )
    detected_organization_name: str | None = Field(
        default=None,
        description="Detected organization name",
    )
    detected_topics: list[str] = Field(default_factory=list, description="Detected topics")
    detected_intent: str | None = Field(default=None, description="Detected intent")

    # Recommended sources
    sources: list[SourceRecommendation] = Field(
        default_factory=list,
        description="Recommended sources to consult",
    )

    # Rules matched
    matched_rules: list[str] = Field(
        default_factory=list,
        description="Names of rules that matched",
    )

    # Confidence
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the recommendations",
    )

    @property
    def required_sources(self) -> list[ContextSource]:
        """Get required sources."""
        return [s.source for s in self.sources if s.required]

    @property
    def optional_sources(self) -> list[ContextSource]:
        """Get optional sources."""
        return [s.source for s in self.sources if not s.required]

    @property
    def ordered_sources(self) -> list[ContextSource]:
        """Get sources ordered by priority."""
        sorted_recs = sorted(self.sources, key=lambda s: -s.priority)
        return [s.source for s in sorted_recs]
