"""Memory service for managing executive knowledge."""

from uuid import UUID

from executive_copilot.memory import MemoryEntry, MemoryQuery, MemoryStore, MemoryType
from executive_copilot.services.base import Service


class MemoryService(Service):
    """Service for memory operations.

    Manages the executive's persistent knowledge base including
    organizations, people, decisions, and conversation summaries.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._store = MemoryStore()

    async def remember(
        self,
        title: str,
        content: str,
        memory_type: MemoryType,
        *,
        organization_id: UUID | None = None,
        person_ids: list[UUID] | None = None,
        tags: list[str] | None = None,
        importance: int = 5,
    ) -> MemoryEntry:
        """Store a new memory.

        Args:
            title: Memory title
            content: Memory content
            memory_type: Type of memory
            organization_id: Related organization
            person_ids: Related people
            tags: Tags for categorization
            importance: Importance level (1-10)

        Returns:
            Created memory entry
        """
        entry = MemoryEntry(
            memory_type=memory_type,
            title=title,
            content=content,
            organization_id=organization_id,
            person_ids=person_ids or [],
            tags=tags or [],
            importance=importance,
        )

        return await self._store.add(entry)

    async def recall(
        self,
        query: str,
        *,
        memory_types: list[MemoryType] | None = None,
        organization_id: UUID | None = None,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Recall memories matching a query.

        Args:
            query: Search query
            memory_types: Filter by types
            organization_id: Filter by organization
            limit: Maximum results

        Returns:
            Matching memories
        """
        memory_query = MemoryQuery(
            query=query,
            memory_types=memory_types or [],
            organization_id=organization_id,
            limit=limit,
        )

        result = await self._store.search(memory_query)
        return result.entries

    async def get_organization_context(
        self,
        organization_id: UUID,
    ) -> list[MemoryEntry]:
        """Get all memories related to an organization.

        Args:
            organization_id: Organization ID

        Returns:
            Related memories
        """
        return await self._store.get_by_organization(organization_id)

    async def get_person_context(
        self,
        person_id: UUID,
    ) -> list[MemoryEntry]:
        """Get all memories related to a person.

        Args:
            person_id: Person ID

        Returns:
            Related memories
        """
        return await self._store.get_by_person(person_id)

    async def get_decisions(
        self,
        organization_id: UUID | None = None,
        limit: int = 20,
    ) -> list[MemoryEntry]:
        """Get decision memories.

        Args:
            organization_id: Filter by organization
            limit: Maximum results

        Returns:
            Decision entries
        """
        query = MemoryQuery(
            memory_types=[MemoryType.DECISION],
            organization_id=organization_id,
            limit=limit,
            sort_by="created_at",
        )

        result = await self._store.search(query)
        return result.entries

    async def get_open_follow_ups(self) -> list[MemoryEntry]:
        """Get open follow-up items.

        Returns:
            Open follow-ups
        """
        return await self._store.get_by_type(MemoryType.FOLLOW_UP)

    async def store_conversation_summary(
        self,
        title: str,
        summary: str,
        *,
        organization_id: UUID | None = None,
        person_ids: list[UUID] | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> MemoryEntry:
        """Store a conversation summary.

        Args:
            title: Conversation title
            summary: Summary content
            organization_id: Related organization
            person_ids: Participants
            source_type: Source type (thread, meeting)
            source_id: Source entity ID

        Returns:
            Created memory entry
        """
        entry = MemoryEntry(
            memory_type=MemoryType.CONVERSATION,
            title=title,
            content=summary,
            organization_id=organization_id,
            person_ids=person_ids or [],
            source_type=source_type,
            source_id=source_id,
        )

        return await self._store.add(entry)

    async def update_memory(
        self,
        entry_id: UUID,
        *,
        content: str | None = None,
        importance: int | None = None,
        tags: list[str] | None = None,
    ) -> MemoryEntry | None:
        """Update an existing memory.

        Args:
            entry_id: Entry to update
            content: New content
            importance: New importance
            tags: New tags

        Returns:
            Updated entry
        """
        entry = await self._store.get(entry_id)
        if not entry:
            return None

        updates: dict[str, object] = {}
        if content is not None:
            updates["content"] = content
        if importance is not None:
            updates["importance"] = importance
        if tags is not None:
            updates["tags"] = tags

        updated = MemoryEntry(**{**entry.model_dump(), **updates})
        return await self._store.update(updated)

    async def forget(self, entry_id: UUID) -> bool:
        """Delete a memory.

        Args:
            entry_id: Entry to delete

        Returns:
            True if deleted
        """
        return await self._store.delete(entry_id)

    async def get_important_memories(
        self,
        min_importance: int = 7,
    ) -> list[MemoryEntry]:
        """Get high-importance memories.

        Args:
            min_importance: Minimum importance level

        Returns:
            Important memories
        """
        return await self._store.get_important(min_importance)
