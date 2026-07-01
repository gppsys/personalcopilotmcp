"""SQLModel table definitions for persistence.

These tables are internal to the database layer.
Domain models are mapped to/from these tables by repositories.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, Index, Text
from sqlmodel import Field, SQLModel


class SQLModelBase(SQLModel):
    """Base class for all database tables."""

    pass


class PersonTable(SQLModelBase, table=True):
    """Person storage table."""

    __tablename__ = "persons"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    first_name: str
    last_name: str | None = None
    display_name: str | None = None
    nickname: str | None = None

    # Contact info stored as JSON
    contact: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    organization_id: UUID | None = Field(default=None, index=True)
    title: str | None = None
    department: str | None = None

    roles: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    importance: int = Field(default=5)
    notes: str | None = Field(default=None, sa_column=Column(Text))
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Relationship tracking
    last_contact_at: datetime | None = Field(default=None, index=True)
    last_contact_type: str | None = None
    interaction_count: int = Field(default=0)

    provider_refs: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    __table_args__ = (
        Index("ix_persons_first_name", "first_name"),
        Index("ix_persons_last_name", "last_name"),
    )


class OrganizationTable(SQLModelBase, table=True):
    """Organization storage table."""

    __tablename__ = "organizations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    name: str = Field(index=True)
    short_name: str | None = None
    organization_type: str

    # Domains stored as JSON
    domains: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    website: str | None = None
    crm_id: str | None = Field(default=None, index=True)

    context_sources: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    default_project_id: str | None = None

    industry: str | None = None
    notes: str | None = Field(default=None, sa_column=Column(Text))
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    importance: int = Field(default=5)

    provider_refs: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))


class EmailTable(SQLModelBase, table=True):
    """Email storage table."""

    __tablename__ = "emails"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    thread_id: UUID | None = Field(default=None, index=True)
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Participants stored as JSON
    sender: dict[str, Any] = Field(sa_column=Column(JSON))
    to_recipients: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    cc_recipients: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    bcc_recipients: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    reply_to: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    subject: str = Field(index=True)
    body_text: str | None = Field(default=None, sa_column=Column(Text))
    body_html: str | None = Field(default=None, sa_column=Column(Text))
    snippet: str | None = None

    attachment_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    has_attachments: bool = Field(default=False)

    received_at: datetime = Field(index=True)
    sent_at: datetime | None = None
    priority: str = Field(default="normal")
    labels: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    is_read: bool = Field(default=False, index=True)
    is_starred: bool = Field(default=False, index=True)
    is_draft: bool = Field(default=False)
    is_sent: bool = Field(default=False)
    is_spam: bool = Field(default=False)
    is_trash: bool = Field(default=False)

    organization_id: UUID | None = Field(default=None, index=True)
    category: str | None = Field(default=None, index=True)
    requires_response: bool | None = None
    response_deadline: datetime | None = None

    provider_ref: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    __table_args__ = (
        Index("ix_emails_received_at_desc", "received_at", postgresql_ops={"received_at": "DESC"}),
    )


class ThreadTable(SQLModelBase, table=True):
    """Thread storage table."""

    __tablename__ = "threads"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    subject: str = Field(index=True)
    snippet: str | None = None

    participants: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    email_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    email_count: int = Field(default=0)

    started_at: datetime
    last_message_at: datetime = Field(index=True)

    is_read: bool = Field(default=False, index=True)
    is_starred: bool = Field(default=False)
    has_attachments: bool = Field(default=False)

    organization_id: UUID | None = Field(default=None, index=True)
    category: str | None = None
    labels: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    summary: str | None = Field(default=None, sa_column=Column(Text))
    action_items: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    requires_response: bool | None = None

    provider_ref: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class CalendarEventTable(SQLModelBase, table=True):
    """Calendar event storage table."""

    __tablename__ = "calendar_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    title: str = Field(index=True)
    description: str | None = Field(default=None, sa_column=Column(Text))
    status: str = Field(default="confirmed")

    start_time: datetime = Field(index=True)
    end_time: datetime = Field(index=True)
    is_all_day: bool = Field(default=False)
    timezone: str = Field(default="UTC")

    is_recurring: bool = Field(default=False)
    recurrence: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    recurring_event_id: UUID | None = Field(default=None, index=True)
    is_exception: bool = Field(default=False)

    location: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    organizer: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    attendees: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    calendar_id: str | None = Field(default=None, index=True)
    calendar_name: str | None = None

    visibility: str = Field(default="default")
    busy_status: str = Field(default="busy")
    reminders: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    color: str | None = None

    is_private: bool = Field(default=False)
    is_cancelled: bool = Field(default=False)
    can_edit: bool = Field(default=True)
    can_respond: bool = Field(default=True)

    meeting_id: UUID | None = Field(default=None, index=True)
    task_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    provider_ref: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class TaskTable(SQLModelBase, table=True):
    """Task storage table."""

    __tablename__ = "tasks"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    title: str = Field(index=True)
    description: str | None = Field(default=None, sa_column=Column(Text))
    status: str = Field(default="not_started", index=True)
    priority: str = Field(default="medium", index=True)

    due_date: datetime | None = Field(default=None, index=True)
    start_date: datetime | None = None
    completed_at: datetime | None = None

    project_id: str | None = Field(default=None, index=True)
    project_name: str | None = None
    section_id: str | None = None
    section_name: str | None = None
    parent_task_id: UUID | None = Field(default=None, index=True)
    subtask_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    assignee_id: str | None = Field(default=None, index=True)
    assignee_email: str | None = None
    assignee_name: str | None = None
    creator_id: str | None = None
    followers: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    custom_fields: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    estimated_hours: float | None = None
    actual_hours: float | None = None
    story_points: int | None = None

    blocked_by: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    blocks: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    organization_id: UUID | None = Field(default=None, index=True)
    email_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    event_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    attachment_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    has_attachments: bool = Field(default=False)

    comment_count: int = Field(default=0)
    last_comment_at: datetime | None = None

    provider_ref: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class FollowUpTable(SQLModelBase, table=True):
    """Follow-up storage table."""

    __tablename__ = "follow_ups"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    title: str = Field(index=True)
    description: str | None = Field(default=None, sa_column=Column(Text))
    context: str | None = Field(default=None, sa_column=Column(Text))

    status: str = Field(default="pending", index=True)
    priority: str = Field(default="medium", index=True)

    due_date: datetime | None = Field(default=None, index=True)
    snoozed_until: datetime | None = None
    completed_at: datetime | None = None

    source_type: str | None = None
    source_id: UUID | None = Field(default=None, index=True)
    detected_from: str | None = Field(default=None, sa_column=Column(Text))

    with_person_id: str | None = Field(default=None, index=True)
    with_person_name: str | None = None
    with_person_email: str | None = None

    organization_id: UUID | None = Field(default=None, index=True)
    task_id: UUID | None = Field(default=None, index=True)

    auto_detected: bool = Field(default=False)
    detection_confidence: float | None = None
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    notes: str | None = Field(default=None, sa_column=Column(Text))


class ConversationSummaryTable(SQLModelBase, table=True):
    """Conversation summary storage table."""

    __tablename__ = "conversation_summaries"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    source_type: str = Field(index=True)
    source_id: UUID = Field(index=True)

    title: str
    summary: str = Field(sa_column=Column(Text))
    key_points: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    decisions: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    action_items: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    questions: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    participant_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    organization_id: UUID | None = Field(default=None, index=True)
    topics: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    sentiment: str | None = None

    conversation_start: datetime
    conversation_end: datetime | None = None
    summarized_at: datetime

    message_count: int = Field(default=0)
    model_used: str | None = None
    confidence: float | None = None


class SyncStateTable(SQLModelBase, table=True):
    """Synchronization state tracking."""

    __tablename__ = "sync_states"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    provider_name: str = Field(index=True)
    provider_type: str = Field(index=True)
    account_id: str | None = Field(default=None, index=True)

    sync_token: str | None = None
    last_sync_at: datetime | None = None
    next_sync_at: datetime | None = None

    status: str = Field(default="idle")
    error_message: str | None = None
    error_count: int = Field(default=0)

    sync_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    __table_args__ = (
        Index("ix_sync_states_provider", "provider_name", "provider_type", "account_id", unique=True),
    )


class CredentialTable(SQLModelBase, table=True):
    """Secure credential storage."""

    __tablename__ = "credentials"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    provider_name: str = Field(index=True)
    account_id: str = Field(index=True)

    # Encrypted token data
    encrypted_data: str = Field(sa_column=Column(Text))
    encryption_version: int = Field(default=1)

    expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    user_info: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    credential_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    __table_args__ = (
        Index("ix_credentials_provider_account", "provider_name", "account_id", unique=True),
    )


class ProcessedItemTable(SQLModelBase, table=True):
    """Tracks items that have been processed by automation jobs.

    Prevents re-processing the same emails/transcripts on every run.
    """

    __tablename__ = "processed_items"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    item_type: str = Field(index=True, description="Type: sent_email, transcript, etc.")
    external_id: str = Field(index=True, description="Provider-specific ID")
    account_id: str = Field(index=True, description="Account that owns this item")

    processed_at: datetime = Field(default_factory=datetime.utcnow)
    job_name: str | None = Field(default=None, description="Job that processed this")
    result: str | None = Field(default=None, description="Processing result")

    __table_args__ = (
        Index("ix_processed_items_lookup", "item_type", "external_id", "account_id", unique=True),
    )


class DelegationTable(SQLModelBase, table=True):
    """Delegation tracking table."""

    __tablename__ = "delegations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    title: str = Field(index=True)
    description: str | None = Field(default=None, sa_column=Column(Text))
    original_text: str | None = Field(default=None, sa_column=Column(Text))

    assignee_name: str | None = None
    assignee_email: str = Field(index=True)

    status: str = Field(default="pending", index=True)
    priority: str = Field(default="medium", index=True)
    delegated_at: datetime = Field(index=True)
    due_date: datetime | None = Field(default=None, index=True)
    completed_at: datetime | None = None

    source_type: str | None = None
    source_id: UUID | None = Field(default=None, index=True)

    account_id: str | None = Field(default=None, index=True)
    organization_id: UUID | None = Field(default=None, index=True)

    auto_detected: bool = Field(default=False)
    detection_confidence: float | None = None
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    notes: str | None = Field(default=None, sa_column=Column(Text))

    __table_args__ = (
        Index("ix_delegations_assignee_status", "assignee_email", "status"),
    )


class MeetingOutcomeTable(SQLModelBase, table=True):
    """Meeting outcome storage for post-meeting capture."""

    __tablename__ = "meeting_outcomes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    event_id: str = Field(index=True)
    event_title: str = Field(index=True)
    meeting_date: datetime = Field(index=True)

    participants: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    organizer: str | None = None

    summary: str | None = Field(default=None, sa_column=Column(Text))
    decisions: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    next_steps: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    key_points: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    account_id: str | None = Field(default=None, index=True)
    organization_id: UUID | None = Field(default=None, index=True)

    recorded_at: datetime = Field(default_factory=datetime.utcnow)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    notes: str | None = Field(default=None, sa_column=Column(Text))


class MeetingDecisionTable(SQLModelBase, table=True):
    """Individual decision records for searchability."""

    __tablename__ = "meeting_decisions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    event_id: str = Field(index=True)
    event_title: str
    meeting_date: datetime = Field(index=True)

    decision: str = Field(sa_column=Column(Text))
    context: str | None = Field(default=None, sa_column=Column(Text))
    owner: str | None = None
    owner_email: str | None = Field(default=None, index=True)

    participants: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    account_id: str | None = Field(default=None, index=True)
    organization_id: UUID | None = Field(default=None, index=True)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    __table_args__ = (
        Index("ix_meeting_decisions_search", "decision"),
    )


class InteractionTable(SQLModelBase, table=True):
    """Interaction tracking table for relationship intelligence."""

    __tablename__ = "interactions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    person_email: str = Field(index=True)
    person_name: str | None = None

    interaction_type: str = Field(index=True)
    direction: str = Field(index=True)

    occurred_at: datetime = Field(index=True)

    subject: str | None = None
    summary: str | None = Field(default=None, sa_column=Column(Text))
    snippet: str | None = Field(default=None, sa_column=Column(Text))

    source_type: str | None = None
    source_id: UUID | None = Field(default=None, index=True)

    account_id: str | None = Field(default=None, index=True)
    organization_id: UUID | None = Field(default=None, index=True)

    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    __table_args__ = (
        Index("ix_interactions_person_time", "person_email", "occurred_at"),
    )


class CommitmentTable(SQLModelBase, table=True):
    """Commitment storage table.

    Tracks promises made by the user to others (i_promised) and
    promises made by others to the user (they_promised).
    """

    __tablename__ = "commitments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default=None)

    title: str = Field(index=True)
    description: str | None = Field(default=None, sa_column=Column(Text))
    original_text: str | None = Field(default=None, sa_column=Column(Text))

    direction: str = Field(index=True)  # i_promised or they_promised
    promiser_name: str | None = None
    promiser_email: str | None = Field(default=None, index=True)
    promisee_name: str | None = None
    promisee_email: str | None = Field(default=None, index=True)

    status: str = Field(default="pending", index=True)
    due_date: datetime | None = Field(default=None, index=True)
    completed_at: datetime | None = None

    source_type: str | None = None
    source_id: UUID | None = Field(default=None, index=True)
    source_date: datetime | None = None

    organization_id: UUID | None = Field(default=None, index=True)

    auto_detected: bool = Field(default=False)
    detection_confidence: float | None = None
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    notes: str | None = Field(default=None, sa_column=Column(Text))
