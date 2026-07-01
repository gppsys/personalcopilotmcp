"""Memory Layer for Executive Copilot.

The Memory Layer provides persistent storage and retrieval of:
- Organizations and their context
- People and relationships
- Conversation summaries
- Technical topics and decisions
- Follow-ups and open items

Memory is provider-independent and serves as the executive's
knowledge base.
"""

from executive_copilot.memory.store import MemoryStore
from executive_copilot.memory.types import (
    MemoryEntry,
    MemoryQuery,
    MemoryType,
)

__all__ = [
    "MemoryEntry",
    "MemoryQuery",
    "MemoryStore",
    "MemoryType",
]
