"""News provider interface."""

from abc import abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from pydantic import BaseModel, Field

from executive_copilot.models import NewsArticle, NewsCategory
from executive_copilot.providers.base import Provider


class NewsQuery(BaseModel):
    """Query parameters for news search."""

    # Content filters
    query: str | None = Field(default=None, description="Search query")
    keywords: list[str] = Field(default_factory=list, description="Required keywords")
    exclude_keywords: list[str] = Field(default_factory=list, description="Excluded keywords")

    # Category filters
    categories: list[NewsCategory] = Field(default_factory=list, description="Categories to include")
    sources: list[str] = Field(default_factory=list, description="Sources to include")
    exclude_sources: list[str] = Field(default_factory=list, description="Sources to exclude")

    # Date range
    published_after: datetime | None = Field(default=None, description="Published after")
    published_before: datetime | None = Field(default=None, description="Published before")

    # Language and region
    language: str | None = Field(default=None, description="Language code")
    country: str | None = Field(default=None, description="Country code")

    # Relevance
    min_relevance: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum relevance score",
    )

    # Pagination
    limit: int = Field(default=25, ge=1, le=100, description="Maximum results")
    offset: int = Field(default=0, ge=0, description="Results offset")

    # Sorting
    sort_by: str = Field(default="published_at", description="Sort field")
    sort_order: str = Field(default="desc", description="Sort order (asc/desc)")


class NewsProvider(Provider[NewsArticle]):
    """Interface for news providers.

    Implementations can fetch news from RSS feeds, news APIs,
    or other sources and map to NewsArticle domain model.
    """

    # --- Article Fetching ---

    @abstractmethod
    async def get_headlines(
        self,
        category: NewsCategory | None = None,
        limit: int = 10,
    ) -> list[NewsArticle]:
        """Get top headlines.

        Args:
            category: Optional category filter
            limit: Maximum articles to return

        Returns:
            List of top headline articles
        """
        ...

    @abstractmethod
    async def search_articles(self, query: NewsQuery) -> list[NewsArticle]:
        """Search articles with complex criteria.

        Args:
            query: Search parameters

        Returns:
            List of matching articles
        """
        ...

    @abstractmethod
    async def get_article(self, article_id: str) -> NewsArticle | None:
        """Get a single article by ID.

        Args:
            article_id: Article identifier

        Returns:
            Article if found
        """
        ...

    @abstractmethod
    async def stream_articles(self, query: NewsQuery) -> AsyncIterator[NewsArticle]:
        """Stream articles for large result sets.

        Args:
            query: Search parameters

        Yields:
            NewsArticle objects
        """
        ...

    # --- Feed Management ---

    @abstractmethod
    async def list_sources(self) -> list[dict[str, object]]:
        """List available news sources.

        Returns:
            List of source information dicts
        """
        ...

    @abstractmethod
    async def fetch_feed(
        self,
        source_url: str,
        limit: int = 50,
    ) -> list[NewsArticle]:
        """Fetch articles from a specific feed.

        Args:
            source_url: URL of the feed
            limit: Maximum articles to fetch

        Returns:
            List of articles from feed
        """
        ...

    # --- Relevance Scoring ---

    @abstractmethod
    async def score_relevance(
        self,
        article: NewsArticle,
        topics: list[str],
        organizations: list[str],
    ) -> float:
        """Score article relevance for the executive.

        Args:
            article: Article to score
            topics: Executive's topics of interest
            organizations: Executive's organizations

        Returns:
            Relevance score between 0 and 1
        """
        ...

    # --- Content Extraction ---

    @abstractmethod
    async def fetch_full_content(self, article_url: str) -> str | None:
        """Fetch full article content from URL.

        Args:
            article_url: URL of the article

        Returns:
            Full article text if extractable
        """
        ...

    # --- Sync Operations ---

    @abstractmethod
    async def get_new_articles_since(
        self,
        last_fetch_time: datetime,
        sources: list[str] | None = None,
    ) -> list[NewsArticle]:
        """Get articles published since last fetch.

        Args:
            last_fetch_time: Time of last fetch
            sources: Optional sources to check

        Returns:
            List of new articles
        """
        ...
