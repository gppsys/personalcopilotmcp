"""Email domain models."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import EmailStr, Field

from executive_copilot.models.base import DomainModel, ProviderReference, ValueObject


class EmailPriority(StrEnum):
    """Email priority level."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class EmailAddress(ValueObject):
    """An email address with optional display name."""

    address: EmailStr = Field(description="Email address")
    display_name: str | None = Field(default=None, description="Display name")

    def __str__(self) -> str:
        """Format as 'Display Name <address>' or just address."""
        if self.display_name:
            return f"{self.display_name} <{self.address}>"
        return self.address

    @property
    def domain(self) -> str:
        """Extract the domain from the email address."""
        return self.address.split("@")[-1].lower()


class EmailLabel(ValueObject):
    """A label or folder applied to an email."""

    name: str = Field(description="Label name")
    provider_id: str | None = Field(default=None, description="ID in the provider system")
    color: str | None = Field(default=None, description="Label color")
    is_system: bool = Field(default=False, description="Whether this is a system label")


class Email(DomainModel):
    """An email message.

    Represents a single email from any provider (Outlook, Gmail, etc.).
    The provider layer is responsible for mapping provider-specific
    representations into this canonical model.
    """

    # Threading
    thread_id: UUID | None = Field(default=None, description="Thread this email belongs to")
    in_reply_to: str | None = Field(default=None, description="Message-ID of parent email")
    references: list[str] = Field(default_factory=list, description="Message-IDs in thread")

    # Participants
    sender: EmailAddress = Field(description="Sender address")
    to_recipients: list[EmailAddress] = Field(default_factory=list, description="To recipients")
    cc_recipients: list[EmailAddress] = Field(default_factory=list, description="CC recipients")
    bcc_recipients: list[EmailAddress] = Field(default_factory=list, description="BCC recipients")
    reply_to: EmailAddress | None = Field(default=None, description="Reply-To address")

    # Content
    subject: str = Field(description="Email subject")
    body_text: str | None = Field(default=None, description="Plain text body")
    body_html: str | None = Field(default=None, description="HTML body")
    snippet: str | None = Field(default=None, description="Short preview text")

    # Attachments (IDs reference Attachment model)
    attachment_ids: list[UUID] = Field(default_factory=list, description="Attachment IDs")
    has_attachments: bool = Field(default=False, description="Whether email has attachments")

    # Metadata
    received_at: datetime = Field(description="When the email was received")
    sent_at: datetime | None = Field(default=None, description="When the email was sent")
    priority: EmailPriority = Field(default=EmailPriority.NORMAL, description="Priority level")
    labels: list[EmailLabel] = Field(default_factory=list, description="Applied labels")

    # Flags
    is_read: bool = Field(default=False, description="Whether email has been read")
    is_starred: bool = Field(default=False, description="Whether email is starred/flagged")
    is_draft: bool = Field(default=False, description="Whether email is a draft")
    is_sent: bool = Field(default=False, description="Whether email was sent by user")
    is_spam: bool = Field(default=False, description="Whether email is marked as spam")
    is_trash: bool = Field(default=False, description="Whether email is in trash")

    # Classification (populated by services)
    organization_id: UUID | None = Field(default=None, description="Matched organization")
    category: str | None = Field(default=None, description="Auto-classified category")
    requires_response: bool | None = Field(default=None, description="Whether response needed")
    response_deadline: datetime | None = Field(default=None, description="Response deadline")

    # Provider tracking
    provider_ref: ProviderReference | None = Field(
        default=None,
        description="Reference to the email in the source provider",
    )

    @property
    def all_recipients(self) -> list[EmailAddress]:
        """Get all recipients (to, cc, bcc)."""
        return self.to_recipients + self.cc_recipients + self.bcc_recipients

    @property
    def participant_emails(self) -> set[str]:
        """Get all participant email addresses."""
        emails = {self.sender.address}
        for recipient in self.all_recipients:
            emails.add(recipient.address)
        return emails

    @property
    def sender_domain(self) -> str:
        """Get the sender's email domain."""
        return self.sender.domain


class Thread(DomainModel):
    """An email conversation thread.

    Groups related emails together, providing conversation-level
    metadata and summary information.
    """

    # Content
    subject: str = Field(description="Thread subject (from first email)")
    snippet: str | None = Field(default=None, description="Preview of latest message")

    # Participants
    participants: list[EmailAddress] = Field(
        default_factory=list,
        description="All participants in the thread",
    )

    # Emails in thread (ordered by date)
    email_ids: list[UUID] = Field(default_factory=list, description="Email IDs in thread")
    email_count: int = Field(default=0, ge=0, description="Number of emails in thread")

    # Timing
    started_at: datetime = Field(description="When thread started")
    last_message_at: datetime = Field(description="When last message was received")

    # Flags
    is_read: bool = Field(default=False, description="Whether all emails are read")
    is_starred: bool = Field(default=False, description="Whether thread is starred")
    has_attachments: bool = Field(default=False, description="Whether thread has attachments")

    # Classification
    organization_id: UUID | None = Field(default=None, description="Matched organization")
    category: str | None = Field(default=None, description="Thread category")
    labels: list[EmailLabel] = Field(default_factory=list, description="Applied labels")

    # Context (populated by services)
    summary: str | None = Field(default=None, description="AI-generated thread summary")
    action_items: list[str] = Field(default_factory=list, description="Extracted action items")
    requires_response: bool | None = Field(default=None, description="Whether response needed")

    # Provider tracking
    provider_ref: ProviderReference | None = Field(
        default=None,
        description="Reference to the thread in the source provider",
    )

    @property
    def participant_domains(self) -> set[str]:
        """Get all unique participant domains."""
        return {p.domain for p in self.participants}
