"""Memory store implementation."""

from datetime import datetime
from uuid import UUID

from executive_copilot.logging import get_logger
from executive_copilot.memory.types import (
    MemoryEntry,
    MemoryQuery,
    MemorySearchResult,
    MemoryType,
)

logger = get_logger(__name__)


class MemoryStore:
    """Persistent memory store for executive knowledge.

    Provides storage and retrieval of memory entries with
    support for full-text search and semantic queries.
    """

    def __init__(self) -> None:
        """Initialize memory store."""
        self._entries: dict[UUID, MemoryEntry] = {}
        logger.info("Memory store initialized")

    async def add(self, entry: MemoryEntry) -> MemoryEntry:
        """Add a new memory entry.

        Args:
            entry: Entry to add

        Returns:
            Added entry
        """
        self._entries[entry.id] = entry
        logger.debug("Added memory entry", entry_id=str(entry.id), type=entry.memory_type)
        return entry

    async def get(self, entry_id: UUID) -> MemoryEntry | None:
        """Get a memory entry by ID.

        Args:
            entry_id: Entry ID

        Returns:
            Entry if found
        """
        entry = self._entries.get(entry_id)
        if entry:
            # Update last accessed time
            updated = MemoryEntry(
                **entry.model_dump(),
                last_accessed_at=datetime.utcnow(),
            )
            self._entries[entry_id] = updated
            return updated
        return None

    async def update(self, entry: MemoryEntry) -> MemoryEntry:
        """Update a memory entry.

        Args:
            entry: Entry with updates

        Returns:
            Updated entry
        """
        entry = MemoryEntry(
            **entry.model_dump(),
            updated_at=datetime.utcnow(),
        )
        self._entries[entry.id] = entry
        logger.debug("Updated memory entry", entry_id=str(entry.id))
        return entry

    async def delete(self, entry_id: UUID) -> bool:
        """Delete a memory entry.

        Args:
            entry_id: Entry to delete

        Returns:
            True if deleted
        """
        if entry_id in self._entries:
            del self._entries[entry_id]
            logger.debug("Deleted memory entry", entry_id=str(entry_id))
            return True
        return False

    async def search(self, query: MemoryQuery) -> MemorySearchResult:
        """Search memory entries.

        Args:
            query: Search query

        Returns:
            Search results
        """
        start_time = datetime.utcnow()
        matches: list[MemoryEntry] = []

        for entry in self._entries.values():
            if self._matches_query(entry, query):
                matches.append(entry)

        # Sort results
        matches = self._sort_results(matches, query)

        # Apply pagination
        total = len(matches)
        paginated = matches[query.offset : query.offset + query.limit]

        search_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        return MemorySearchResult(
            entries=paginated,
            total_count=total,
            has_more=total > query.offset + query.limit,
            query=query,
            search_time_ms=search_time,
        )

    async def get_by_type(
        self,
        memory_type: MemoryType,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        """Get entries by type.

        Args:
            memory_type: Type to filter by
            limit: Maximum results

        Returns:
            Matching entries
        """
        query = MemoryQuery(memory_types=[memory_type], limit=limit)
        result = await self.search(query)
        return result.entries

    async def get_by_organization(
        self,
        organization_id: UUID,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        """Get entries related to an organization.

        Args:
            organization_id: Organization ID
            limit: Maximum results

        Returns:
            Related entries
        """
        query = MemoryQuery(organization_id=organization_id, limit=limit)
        result = await self.search(query)
        return result.entries

    async def get_by_person(
        self,
        person_id: UUID,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        """Get entries related to a person.

        Args:
            person_id: Person ID
            limit: Maximum results

        Returns:
            Related entries
        """
        query = MemoryQuery(person_id=person_id, limit=limit)
        result = await self.search(query)
        return result.entries

    async def get_recent(
        self,
        days: int = 7,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        """Get recently created entries.

        Args:
            days: Days to look back
            limit: Maximum results

        Returns:
            Recent entries
        """
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(days=days)
        query = MemoryQuery(created_after=cutoff, limit=limit, sort_by="created_at")
        result = await self.search(query)
        return result.entries

    async def get_important(
        self,
        min_importance: int = 7,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        """Get high-importance entries.

        Args:
            min_importance: Minimum importance level
            limit: Maximum results

        Returns:
            Important entries
        """
        query = MemoryQuery(
            min_importance=min_importance,
            limit=limit,
            sort_by="importance",
        )
        result = await self.search(query)
        return result.entries

    def _matches_query(self, entry: MemoryEntry, query: MemoryQuery) -> bool:
        """Check if entry matches query criteria."""
        # Check expiration
        if not query.include_expired and entry.is_expired:
            return False

        # Check memory types
        if query.memory_types and entry.memory_type not in query.memory_types:
            return False

        # Check organization
        if query.organization_id and entry.organization_id != query.organization_id:
            return False

        # Check person
        if query.person_id and query.person_id not in entry.person_ids:
            return False

        # Check tags
        if query.tags and not all(tag in entry.tags for tag in query.tags):
            return False

        # Check topics
        if query.topics and not any(topic in entry.topics for topic in query.topics):
            return False

        # Check importance
        if query.min_importance and entry.importance < query.min_importance:
            return False

        # Check confidence
        if query.min_confidence and entry.confidence < query.min_confidence:
            return False

        # Check date range
        if query.created_after and entry.created_at < query.created_after:
            return False
        if query.created_before and entry.created_at > query.created_before:
            return False

        # Check text search
        if query.query:
            query_lower = query.query.lower()
            searchable = f"{entry.title} {entry.content} {' '.join(entry.tags)}".lower()
            if query_lower not in searchable:
                return False

        # Check keywords
        if query.keywords:
            entry_keywords = set(k.lower() for k in entry.keywords)
            query_keywords = set(k.lower() for k in query.keywords)
            if not query_keywords.intersection(entry_keywords):
                return False

        return True

    def _sort_results(
        self,
        entries: list[MemoryEntry],
        query: MemoryQuery,
    ) -> list[MemoryEntry]:
        """Sort search results."""
        reverse = query.sort_order == "desc"

        match query.sort_by:
            case "created_at":
                return sorted(entries, key=lambda e: e.created_at, reverse=reverse)
            case "updated_at":
                return sorted(
                    entries,
                    key=lambda e: e.updated_at or e.created_at,
                    reverse=reverse,
                )
            case "importance":
                return sorted(entries, key=lambda e: e.importance, reverse=reverse)
            case "relevance" | _:
                # For relevance, prioritize importance and recency
                return sorted(
                    entries,
                    key=lambda e: (e.importance, e.created_at),
                    reverse=True,
                )
