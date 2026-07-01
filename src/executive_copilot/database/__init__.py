"""Database layer with repository pattern.

No service should directly access SQLModel or database sessions.
All persistence operations go through repositories.
"""

from executive_copilot.database.connection import DatabaseManager, get_database_manager
from executive_copilot.database.repositories.base import Repository
from executive_copilot.database.unit_of_work import UnitOfWork

__all__ = [
    "DatabaseManager",
    "Repository",
    "UnitOfWork",
    "get_database_manager",
]
