"""Email provider interface."""

from abc import abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from pydantic import BaseModel, Field

from executive_copilot.models import Attachment, Email, EmailAddress, Thread
from executive_copilot.providers.base import Provider


class EmailQuery(BaseModel):
    """Query parameters for email search."""

    # Text search
    query: str | None = Field(default=None, description="Full-text search query")
    subject: str | None = Field(default=None, description="Subject contains")
    body: str | None = Field(default=None, description="Body contains")

    # Participants
    from_address: str | None = Field(default=None, description="From address")
    to_address: str | None = Field(default=None, description="To address")
    participants: list[str] = Field(default_factory=list, description="Any participant")

    # Date range
    after: datetime | None = Field(default=None, description="Received after")
    before: datetime | None = Field(default=None, description="Received before")

    # Filters
    is_unread: bool | None = Field(default=None, description="Filter by read status")
    is_starred: bool | None = Field(default=None, description="Filter by starred status")
    has_attachment: bool | None = Field(default=None, description="Filter by attachments")
    labels: list[str] = Field(default_factory=list, description="Required labels")
    exclude_labels: list[str] = Field(default_factory=list, description="Excluded labels")

    # Pagination
    limit: int = Field(default=50, ge=1, le=500, description="Maximum results")
    offset: int = Field(default=0, ge=0, description="Results offset")


class EmailSearchResult(BaseModel):
    """Result of an email search."""

    emails: list[Email] = Field(description="Matched emails")
    total_count: int | None = Field(default=None, description="Total matching emails")
    has_more: bool = Field(default=False, description="Whether more results exist")
    next_offset: int | None = Field(default=None, description="Offset for next page")


class DraftEmail(BaseModel):
    """Email draft to be created or sent."""

    to: list[EmailAddress] = Field(description="To recipients")
    cc: list[EmailAddress] = Field(default_factory=list, description="CC recipients")
    bcc: list[EmailAddress] = Field(default_factory=list, description="BCC recipients")
    subject: str = Field(description="Email subject")
    body_text: str | None = Field(default=None, description="Plain text body")
    body_html: str | None = Field(default=None, description="HTML body")
    reply_to_id: str | None = Field(default=None, description="Email ID to reply to")
    thread_id: str | None = Field(default=None, description="Thread to add to")
    attachment_paths: list[str] = Field(default_factory=list, description="Paths to attach")


class EmailProvider(Provider[Email]):
    """Interface for email providers.

    Implementations must map their specific email formats to the
    Email and Thread domain models.
    """

    # --- Inbox Operations ---

    @abstractmethod
    async def get_inbox(
        self,
        limit: int = 50,
        unread_only: bool = False,
    ) -> list[Email]:
        """Get recent inbox emails.

        Args:
            limit: Maximum number of emails to return
            unread_only: If True, only return unread emails

        Returns:
            List of Email domain models
        """
        ...

    @abstractmethod
    async def get_email(self, email_id: str) -> Email | None:
        """Get a single email by ID.

        Args:
            email_id: Provider-specific email ID

        Returns:
            Email if found, None otherwise
        """
        ...

    @abstractmethod
    async def search_emails(self, query: EmailQuery) -> EmailSearchResult:
        """Search emails with complex criteria.

        Args:
            query: Search parameters

        Returns:
            Search results with emails and pagination info
        """
        ...

    @abstractmethod
    async def stream_emails(
        self,
        query: EmailQuery,
    ) -> AsyncIterator[Email]:
        """Stream emails matching query for large result sets.

        Args:
            query: Search parameters

        Yields:
            Email objects as they are fetched
        """
        ...

    # --- Thread Operations ---

    @abstractmethod
    async def get_thread(self, thread_id: str) -> Thread | None:
        """Get an email thread by ID.

        Args:
            thread_id: Provider-specific thread ID

        Returns:
            Thread if found, None otherwise
        """
        ...

    @abstractmethod
    async def get_thread_emails(self, thread_id: str) -> list[Email]:
        """Get all emails in a thread.

        Args:
            thread_id: Provider-specific thread ID

        Returns:
            List of emails in the thread, ordered by date
        """
        ...

    # --- Send Operations ---

    @abstractmethod
    async def create_draft(self, draft: DraftEmail) -> Email:
        """Create an email draft.

        Args:
            draft: Draft email content

        Returns:
            Created draft as Email
        """
        ...

    @abstractmethod
    async def send_email(self, draft: DraftEmail) -> Email:
        """Send an email immediately.

        Args:
            draft: Email content to send

        Returns:
            Sent email
        """
        ...

    @abstractmethod
    async def send_draft(self, draft_id: str) -> Email:
        """Send an existing draft.

        Args:
            draft_id: ID of draft to send

        Returns:
            Sent email
        """
        ...

    @abstractmethod
    async def reply_to_email(
        self,
        email_id: str,
        body_text: str,
        body_html: str | None = None,
        reply_all: bool = False,
    ) -> Email:
        """Reply to an email.

        Args:
            email_id: ID of email to reply to
            body_text: Reply body text
            body_html: Optional HTML body
            reply_all: If True, reply to all recipients

        Returns:
            Sent reply email
        """
        ...

    # --- Status Operations ---

    @abstractmethod
    async def mark_read(self, email_id: str, read: bool = True) -> None:
        """Mark an email as read or unread.

        Args:
            email_id: Email to mark
            read: True to mark as read, False for unread
        """
        ...

    @abstractmethod
    async def mark_starred(self, email_id: str, starred: bool = True) -> None:
        """Star or unstar an email.

        Args:
            email_id: Email to star/unstar
            starred: True to star, False to unstar
        """
        ...

    @abstractmethod
    async def move_to_folder(self, email_id: str, folder: str) -> None:
        """Move email to a folder/label.

        Args:
            email_id: Email to move
            folder: Target folder/label name
        """
        ...

    @abstractmethod
    async def delete_email(self, email_id: str, permanent: bool = False) -> None:
        """Delete an email.

        Args:
            email_id: Email to delete
            permanent: If True, permanently delete. Otherwise move to trash.
        """
        ...

    # --- Attachment Operations ---

    @abstractmethod
    async def get_attachment(
        self,
        email_id: str,
        attachment_id: str,
    ) -> tuple[Attachment, bytes]:
        """Get attachment metadata and content.

        Args:
            email_id: Email containing attachment
            attachment_id: Attachment to retrieve

        Returns:
            Tuple of (Attachment metadata, file content bytes)
        """
        ...

    @abstractmethod
    async def download_attachment(
        self,
        email_id: str,
        attachment_id: str,
        destination: str,
    ) -> Attachment:
        """Download attachment to local file.

        Args:
            email_id: Email containing attachment
            attachment_id: Attachment to download
            destination: Local path to save file

        Returns:
            Attachment metadata with storage_path set
        """
        ...

    # --- Sync Operations ---

    @abstractmethod
    async def get_changes_since(
        self,
        sync_token: str | None = None,
    ) -> tuple[list[Email], str]:
        """Get emails changed since last sync.

        Args:
            sync_token: Token from previous sync, or None for initial sync

        Returns:
            Tuple of (changed emails, new sync token)
        """
        ...

    # --- Label/Folder Operations ---

    @abstractmethod
    async def list_labels(self) -> list[dict[str, str]]:
        """List available labels/folders.

        Returns:
            List of label info dicts with at least 'id' and 'name'
        """
        ...

    @abstractmethod
    async def add_label(self, email_id: str, label: str) -> None:
        """Add a label to an email.

        Args:
            email_id: Email to label
            label: Label to add
        """
        ...

    @abstractmethod
    async def remove_label(self, email_id: str, label: str) -> None:
        """Remove a label from an email.

        Args:
            email_id: Email to unlabel
            label: Label to remove
        """
        ...
