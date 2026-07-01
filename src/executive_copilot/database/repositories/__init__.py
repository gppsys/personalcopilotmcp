"""Repository implementations."""

from executive_copilot.database.repositories.base import Repository
from executive_copilot.database.repositories.commitment import CommitmentRepository
from executive_copilot.database.repositories.delegation import DelegationRepository
from executive_copilot.database.repositories.email import EmailRepository, ThreadRepository
from executive_copilot.database.repositories.follow_up import FollowUpRepository
from executive_copilot.database.repositories.interaction import InteractionRepository
from executive_copilot.database.repositories.meeting_outcome import (
    MeetingDecisionRepository,
    MeetingOutcomeRepository,
)
from executive_copilot.database.repositories.organization import OrganizationRepository
from executive_copilot.database.repositories.person import PersonRepository
from executive_copilot.database.repositories.processed_item import ProcessedItemRepository

__all__ = [
    "CommitmentRepository",
    "DelegationRepository",
    "EmailRepository",
    "FollowUpRepository",
    "InteractionRepository",
    "MeetingDecisionRepository",
    "MeetingOutcomeRepository",
    "OrganizationRepository",
    "PersonRepository",
    "ProcessedItemRepository",
    "Repository",
    "ThreadRepository",
]
