"""Search provider interface."""

from abc import abstractmethod
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from executive_copilot.providers.base import Provider


class SearchEntityType(StrEnum):
    """Types of entities that can be searched."""

    EMAIL = "email"
    THREAD = "thread"
    EVENT = "event"
    TASK = "task"
    DOCUMENT = "document"
    PERSON = "person"
    ORGANIZATION = "organization"
    NEWS = "news"
    MEMORY = "memory"
    ALL = "all"


class SearchQuery(BaseModel):
    """Universal search query parameters."""

    # Search text
    query: str = Field(description="Search query text")

    # Entity filtering
    entity_types: list[SearchEntityType] = Field(
        default_factory=lambda: [SearchEntityType.ALL],
        description="Types to search",
    )

    # Scope
    include_providers: list[str] = Field(
        default_factory=list,
        description="Providers to search (empty = all)",
    )
    exclude_providers: list[str] = Field(
        default_factory=list,
        description="Providers to exclude",
    )

    # Date range
    date_from: datetime | None = Field(default=None, description="Items after this date")
    date_to: datetime | None = Field(default=None, description="Items before this date")

    # Context
    organization_id: str | None = Field(default=None, description="Limit to organization")
    person_id: str | None = Field(default=None, description="Related to person")

    # Search type
    semantic: bool = Field(default=False, description="Use semantic/vector search")
    fuzzy: bool = Field(default=True, description="Allow fuzzy matching")

    # Pagination
    limit: int = Field(default=20, ge=1, le=100, description="Maximum results")
    offset: int = Field(default=0, ge=0, description="Results offset")


class SearchResultItem(BaseModel):
    """A single search result item."""

    # Identity
    entity_type: SearchEntityType = Field(description="Type of result")
    entity_id: str = Field(description="Entity ID")
    provider_name: str = Field(description="Source provider")

    # Display
    title: str = Field(description="Result title")
    snippet: str | None = Field(default=None, description="Relevant excerpt")
    url: str | None = Field(default=None, description="Link to entity")

    # Metadata
    date: datetime | None = Field(default=None, description="Relevant date")
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Relevance score")
    matched_fields: list[str] = Field(
        default_factory=list,
        description="Fields that matched query",
    )

    # Related
    organization_name: str | None = Field(default=None, description="Related organization")
    person_name: str | None = Field(default=None, description="Related person")

    # Raw data for detailed view
    data: dict[str, object] = Field(
        default_factory=dict,
        description="Full entity data",
    )


class SearchResult(BaseModel):
    """Search results container."""

    query: str = Field(description="Original query")
    total_count: int = Field(description="Total matching items")
    items: list[SearchResultItem] = Field(description="Result items")
    has_more: bool = Field(default=False, description="More results available")

    # Facets for filtering
    entity_type_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Count by entity type",
    )
    provider_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Count by provider",
    )

    # Suggestions
    spell_suggestion: str | None = Field(default=None, description="Spelling suggestion")
    related_queries: list[str] = Field(default_factory=list, description="Related queries")


class SearchProvider(Provider[SearchResultItem]):
    """Interface for search providers.

    Search providers aggregate results across multiple data sources
    and provide unified search capabilities.
    """

    @abstractmethod
    async def search(self, query: SearchQuery) -> SearchResult:
        """Perform a search across all indexed data.

        Args:
            query: Search parameters

        Returns:
            Search results
        """
        ...

    @abstractmethod
    async def search_entities(
        self,
        query: str,
        entity_type: SearchEntityType,
        limit: int = 20,
    ) -> list[SearchResultItem]:
        """Search within a specific entity type.

        Args:
            query: Search text
            entity_type: Type to search
            limit: Maximum results

        Returns:
            Matching items
        """
        ...

    @abstractmethod
    async def get_suggestions(
        self,
        partial_query: str,
        limit: int = 5,
    ) -> list[str]:
        """Get autocomplete suggestions.

        Args:
            partial_query: Partial search text
            limit: Maximum suggestions

        Returns:
            List of suggested queries
        """
        ...

    @abstractmethod
    async def find_similar(
        self,
        entity_type: SearchEntityType,
        entity_id: str,
        limit: int = 5,
    ) -> list[SearchResultItem]:
        """Find similar entities.

        Args:
            entity_type: Type of source entity
            entity_id: Entity to find similar to
            limit: Maximum results

        Returns:
            Similar items
        """
        ...

    @abstractmethod
    async def index_entity(
        self,
        entity_type: SearchEntityType,
        entity_id: str,
        provider_name: str,
        data: dict[str, object],
    ) -> None:
        """Index an entity for searching.

        Args:
            entity_type: Type of entity
            entity_id: Entity ID
            provider_name: Source provider
            data: Entity data to index
        """
        ...

    @abstractmethod
    async def remove_from_index(
        self,
        entity_type: SearchEntityType,
        entity_id: str,
    ) -> None:
        """Remove an entity from the search index.

        Args:
            entity_type: Type of entity
            entity_id: Entity to remove
        """
        ...

    @abstractmethod
    async def reindex_all(self, entity_type: SearchEntityType | None = None) -> int:
        """Reindex all entities.

        Args:
            entity_type: Type to reindex (None = all)

        Returns:
            Number of entities indexed
        """
        ...
