"""Search service for unified search across all sources."""

from datetime import datetime

from executive_copilot.providers.search import SearchEntityType, SearchQuery, SearchResult
from executive_copilot.services.base import Service


class SearchService(Service):
    """Service for unified search operations.

    Provides search across all data sources including emails,
    calendar, tasks, documents, and memory.
    """

    async def search_everything(
        self,
        query: str,
        *,
        limit: int = 20,
        include_types: list[SearchEntityType] | None = None,
    ) -> SearchResult:
        """Search across all sources.

        Args:
            query: Search query
            limit: Maximum results
            include_types: Entity types to include

        Returns:
            Unified search results
        """
        search_query = SearchQuery(
            query=query,
            entity_types=include_types or [SearchEntityType.ALL],
            limit=limit,
        )

        # For now, aggregate from individual services
        # In future sprints, this will use a proper search provider
        results = await self._aggregate_search(search_query)
        return results

    async def search_by_type(
        self,
        query: str,
        entity_type: SearchEntityType,
        limit: int = 20,
    ) -> SearchResult:
        """Search within a specific entity type.

        Args:
            query: Search query
            entity_type: Type to search
            limit: Maximum results

        Returns:
            Search results
        """
        search_query = SearchQuery(
            query=query,
            entity_types=[entity_type],
            limit=limit,
        )

        return await self._aggregate_search(search_query)

    async def search_recent(
        self,
        query: str,
        days: int = 7,
        limit: int = 20,
    ) -> SearchResult:
        """Search recent items.

        Args:
            query: Search query
            days: Days to look back
            limit: Maximum results

        Returns:
            Search results
        """
        from datetime import timedelta

        search_query = SearchQuery(
            query=query,
            date_from=datetime.utcnow() - timedelta(days=days),
            limit=limit,
        )

        return await self._aggregate_search(search_query)

    async def search_organization(
        self,
        query: str,
        organization_id: str,
        limit: int = 20,
    ) -> SearchResult:
        """Search within an organization's context.

        Args:
            query: Search query
            organization_id: Organization to search within
            limit: Maximum results

        Returns:
            Search results
        """
        search_query = SearchQuery(
            query=query,
            organization_id=organization_id,
            limit=limit,
        )

        return await self._aggregate_search(search_query)

    async def _aggregate_search(self, query: SearchQuery) -> SearchResult:
        """Aggregate search results from multiple services.

        Args:
            query: Search query

        Returns:
            Aggregated results
        """
        from executive_copilot.providers.search import SearchResultItem

        items: list[SearchResultItem] = []
        type_counts: dict[str, int] = {}

        # Search emails if type matches
        if self._should_search_type(query, SearchEntityType.EMAIL):
            try:
                emails = await self.container.email.search_emails(
                    query.query,
                    limit=query.limit,
                )
                for email in emails:
                    items.append(
                        SearchResultItem(
                            entity_type=SearchEntityType.EMAIL,
                            entity_id=str(email.id),
                            provider_name="email",
                            title=email.subject,
                            snippet=email.snippet,
                            date=email.received_at,
                            relevance_score=0.8,
                        )
                    )
                type_counts["email"] = len(emails)
            except Exception as e:
                self._logger.error("Email search failed", error=str(e))

        # Search tasks if type matches
        if self._should_search_type(query, SearchEntityType.TASK):
            try:
                tasks = await self.container.tasks.search_tasks(
                    query=query.query,
                    limit=query.limit,
                )
                for task in tasks:
                    items.append(
                        SearchResultItem(
                            entity_type=SearchEntityType.TASK,
                            entity_id=str(task.id),
                            provider_name="task",
                            title=task.title,
                            snippet=task.description,
                            date=task.due_date,
                            relevance_score=0.7,
                        )
                    )
                type_counts["task"] = len(tasks)
            except Exception as e:
                self._logger.error("Task search failed", error=str(e))

        # Search memory if type matches
        if self._should_search_type(query, SearchEntityType.MEMORY):
            try:
                memories = await self.container.memory.recall(
                    query.query or "",
                    limit=query.limit,
                )
                for memory in memories:
                    items.append(
                        SearchResultItem(
                            entity_type=SearchEntityType.MEMORY,
                            entity_id=str(memory.id),
                            provider_name="memory",
                            title=memory.title,
                            snippet=memory.summary or memory.content[:200],
                            date=memory.created_at,
                            relevance_score=0.6,
                        )
                    )
                type_counts["memory"] = len(memories)
            except Exception as e:
                self._logger.error("Memory search failed", error=str(e))

        # Sort by relevance and date
        items.sort(
            key=lambda i: (i.relevance_score, i.date or datetime.min),
            reverse=True,
        )

        return SearchResult(
            query=query.query or "",
            total_count=len(items),
            items=items[:query.limit],
            has_more=len(items) > query.limit,
            entity_type_counts=type_counts,
        )

    def _should_search_type(
        self,
        query: SearchQuery,
        entity_type: SearchEntityType,
    ) -> bool:
        """Check if we should search a given type."""
        if not query.entity_types:
            return True
        if SearchEntityType.ALL in query.entity_types:
            return True
        return entity_type in query.entity_types
