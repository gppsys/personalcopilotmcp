"""News article domain model."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field, HttpUrl

from executive_copilot.models.base import DomainModel, ProviderReference, ValueObject


class NewsCategory(StrEnum):
    """Category of news article."""

    BUSINESS = "business"
    TECHNOLOGY = "technology"
    FINANCE = "finance"
    INDUSTRY = "industry"
    POLITICS = "politics"
    WORLD = "world"
    SCIENCE = "science"
    HEALTH = "health"
    SPORTS = "sports"
    ENTERTAINMENT = "entertainment"
    OPINION = "opinion"
    OTHER = "other"


class NewsSource(ValueObject):
    """Source of news articles."""

    name: str = Field(description="Source name")
    url: HttpUrl | None = Field(default=None, description="Source website URL")
    category: str | None = Field(default=None, description="Source category/type")
    language: str = Field(default="en", description="Primary language")
    country: str | None = Field(default=None, description="Country of origin")
    reliability_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Reliability score (0-1)",
    )


class NewsArticle(DomainModel):
    """A news article.

    Represents articles from RSS feeds, news APIs, or other sources.
    Used for daily briefings and news digests.
    """

    # Basic info
    title: str = Field(description="Article title")
    description: str | None = Field(default=None, description="Article summary/description")
    content: str | None = Field(default=None, description="Full article content")

    # Source
    source: NewsSource = Field(description="Article source")
    author: str | None = Field(default=None, description="Article author")
    url: HttpUrl = Field(description="URL to the article")

    # Categorization
    category: NewsCategory = Field(default=NewsCategory.OTHER, description="Article category")
    topics: list[str] = Field(default_factory=list, description="Topic tags")
    keywords: list[str] = Field(default_factory=list, description="Extracted keywords")

    # Timing
    published_at: datetime = Field(description="Publication timestamp")
    fetched_at: datetime = Field(description="When article was fetched")

    # Media
    image_url: HttpUrl | None = Field(default=None, description="Main article image")
    thumbnail_url: HttpUrl | None = Field(default=None, description="Thumbnail image")

    # Relevance
    relevance_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Relevance score for the executive (0-1)",
    )
    matched_organizations: list[str] = Field(
        default_factory=list,
        description="Organization IDs mentioned in article",
    )
    matched_topics: list[str] = Field(
        default_factory=list,
        description="Executive's topics matched in article",
    )

    # Engagement
    is_read: bool = Field(default=False, description="Whether article has been read")
    is_saved: bool = Field(default=False, description="Whether article is saved/bookmarked")
    is_shared: bool = Field(default=False, description="Whether article has been shared")
    notes: str | None = Field(default=None, description="Executive's notes on article")

    # AI processing
    summary: str | None = Field(default=None, description="AI-generated summary")
    sentiment: str | None = Field(default=None, description="Sentiment analysis result")
    importance: str | None = Field(default=None, description="Assessed importance level")

    # Provider tracking
    provider_ref: ProviderReference | None = Field(
        default=None,
        description="Reference to article in source provider",
    )

    @property
    def is_recent(self) -> bool:
        """Check if article is from the last 24 hours."""
        age = datetime.utcnow() - self.published_at
        return age.days < 1

    @property
    def age_hours(self) -> float:
        """Get article age in hours."""
        age = datetime.utcnow() - self.published_at
        return age.total_seconds() / 3600

    @property
    def has_content(self) -> bool:
        """Check if full content is available."""
        return self.content is not None and len(self.content) > 0

    @property
    def reading_time_minutes(self) -> int | None:
        """Estimate reading time in minutes (assuming 200 words/minute)."""
        text = self.content or self.description
        if not text:
            return None
        word_count = len(text.split())
        return max(1, round(word_count / 200))
