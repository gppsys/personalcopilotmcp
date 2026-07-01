"""Domain models for Executive Copilot.

All models in this module represent the canonical domain objects.
Provider-specific representations must be mapped to these models.
"""

from executive_copilot.models.attachment import Attachment, AttachmentType
from executive_copilot.models.calendar import (
    Attendee,
    AttendeeResponse,
    CalendarEvent,
    EventLocation,
    EventRecurrence,
    EventStatus,
    RecurrenceFrequency,
)
from executive_copilot.models.communication import (
    ConversationSummary,
    FollowUp,
    FollowUpPriority,
    FollowUpStatus,
    Reminder,
    ReminderStatus,
)
from executive_copilot.models.document import Document, DocumentType
from executive_copilot.models.email import Email, EmailAddress, EmailPriority, Thread
from executive_copilot.models.meeting import Meeting, MeetingType
from executive_copilot.models.news import NewsArticle, NewsCategory, NewsSource
from executive_copilot.models.organization import Customer, Organization, OrganizationType
from executive_copilot.models.person import Person, PersonRole
from executive_copilot.models.task import Task, TaskPriority, TaskStatus

__all__ = [
    # Attachment
    "Attachment",
    "AttachmentType",
    # Calendar
    "Attendee",
    "AttendeeResponse",
    "CalendarEvent",
    "EventLocation",
    "EventRecurrence",
    "EventStatus",
    "RecurrenceFrequency",
    # Communication
    "ConversationSummary",
    "FollowUp",
    "FollowUpPriority",
    "FollowUpStatus",
    "Reminder",
    "ReminderStatus",
    # Document
    "Document",
    "DocumentType",
    # Email
    "Email",
    "EmailAddress",
    "EmailPriority",
    "Thread",
    # Meeting
    "Meeting",
    "MeetingType",
    # News
    "NewsArticle",
    "NewsCategory",
    "NewsSource",
    # Organization
    "Customer",
    "Organization",
    "OrganizationType",
    # Person
    "Person",
    "PersonRole",
    # Task
    "Task",
    "TaskPriority",
    "TaskStatus",
]
