"""News service for managing news feeds."""

from datetime import datetime

from executive_copilot.models import NewsArticle, NewsCategory
from executive_copilot.providers.news import NewsProvider, NewsQuery
from executive_copilot.services.base import Service


class NewsService(Service):
    """Service for news operations.

    Orchestrates news providers and handles news-related business logic.
    """

    async def get_headlines(
        self,
        category: NewsCategory | None = None,
        limit: int = 10,
    ) -> list[NewsArticle]:
        """Get top headlines.

        Args:
            category: Optional category filter
            limit: Maximum articles

        Returns:
            Top headline articles
        """
        articles: list[NewsArticle] = []
        providers = self._get_news_providers()

        for provider in providers:
            try:
                provider_articles = await provider.get_headlines(
                    category=category,
                    limit=limit,
                )
                articles.extend(provider_articles)
            except Exception as e:
                self._logger.error(
                    "Failed to get headlines",
                    provider=provider.name,
                    error=str(e),
                )

        # Sort by published date and limit
        articles.sort(key=lambda a: a.published_at, reverse=True)
        return articles[:limit]

    async def search_news(
        self,
        query: str,
        *,
        categories: list[NewsCategory] | None = None,
        published_after: datetime | None = None,
        limit: int = 25,
    ) -> list[NewsArticle]:
        """Search news articles.

        Args:
            query: Search query
            categories: Category filters
            published_after: Filter by publish date
            limit: Maximum results

        Returns:
            Matching articles
        """
        news_query = NewsQuery(
            query=query,
            categories=categories or [],
            published_after=published_after,
            limit=limit,
        )

        articles: list[NewsArticle] = []
        providers = self._get_news_providers()

        for provider in providers:
            try:
                provider_articles = await provider.search_articles(news_query)
                articles.extend(provider_articles)
            except Exception as e:
                self._logger.error(
                    "News search failed",
                    provider=provider.name,
                    error=str(e),
                )

        articles.sort(key=lambda a: a.published_at, reverse=True)
        return articles[:limit]

    async def get_relevant_news(
        self,
        topics: list[str],
        organizations: list[str],
        limit: int = 20,
    ) -> list[NewsArticle]:
        """Get news relevant to executive's interests.

        Args:
            topics: Topics of interest
            organizations: Organizations to track
            limit: Maximum results

        Returns:
            Relevant articles sorted by relevance
        """
        articles: list[NewsArticle] = []
        providers = self._get_news_providers()

        # Search for each topic/organization
        search_terms = topics + organizations

        for provider in providers:
            for term in search_terms[:5]:  # Limit searches
                try:
                    query = NewsQuery(query=term, limit=10)
                    provider_articles = await provider.search_articles(query)

                    # Score relevance
                    for article in provider_articles:
                        score = await provider.score_relevance(
                            article,
                            topics,
                            organizations,
                        )
                        scored_article = NewsArticle(
                            **article.model_dump(),
                            relevance_score=score,
                            matched_topics=[t for t in topics if t.lower() in article.title.lower()],
                            matched_organizations=[
                                o for o in organizations if o.lower() in article.title.lower()
                            ],
                        )
                        articles.append(scored_article)
                except Exception as e:
                    self._logger.error(
                        "Relevance search failed",
                        provider=provider.name,
                        term=term,
                        error=str(e),
                    )

        # Deduplicate and sort by relevance
        seen_urls: set[str] = set()
        unique_articles: list[NewsArticle] = []
        for article in articles:
            url_str = str(article.url)
            if url_str not in seen_urls:
                seen_urls.add(url_str)
                unique_articles.append(article)

        unique_articles.sort(
            key=lambda a: (a.relevance_score or 0, a.published_at),
            reverse=True,
        )
        return unique_articles[:limit]

    async def get_daily_briefing(self) -> dict[str, object]:
        """Generate a daily news briefing.

        Returns:
            Briefing with categorized news
        """
        briefing: dict[str, object] = {
            "generated_at": datetime.utcnow().isoformat(),
            "sections": {},
        }

        # Get headlines by category
        categories = [
            NewsCategory.BUSINESS,
            NewsCategory.TECHNOLOGY,
            NewsCategory.FINANCE,
        ]

        sections: dict[str, list[NewsArticle]] = {}
        for category in categories:
            articles = await self.get_headlines(category=category, limit=5)
            sections[category.value] = articles

        briefing["sections"] = {
            cat: [a.model_dump() for a in articles]
            for cat, articles in sections.items()
        }

        return briefing

    def _get_news_providers(self) -> list[NewsProvider]:
        """Get news providers to use."""
        providers: list[NewsProvider] = []
        configs = self.container.config.get_enabled_providers("news")

        for config in configs:
            try:
                provider = self.container.provider_registry.get_or_create(config)
                if isinstance(provider, NewsProvider):
                    providers.append(provider)
            except Exception as e:
                self._logger.error(
                    "Failed to get provider",
                    provider=config.provider_name,
                    error=str(e),
                )

        return providers
