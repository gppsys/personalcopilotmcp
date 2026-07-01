"""Document domain model."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from executive_copilot.models.base import DomainModel, ProviderReference


class DocumentType(StrEnum):
    """Type of document."""

    FILE = "file"
    FOLDER = "folder"
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    PRESENTATION = "presentation"
    PDF = "pdf"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    ARCHIVE = "archive"
    CODE = "code"
    OTHER = "other"


class DocumentPermission(StrEnum):
    """Permission level for a document."""

    OWNER = "owner"
    EDITOR = "editor"
    COMMENTER = "commenter"
    VIEWER = "viewer"


class Document(DomainModel):
    """A document or file from a storage provider.

    Represents files from any cloud storage provider
    (Google Drive, OneDrive, Dropbox, etc.).
    """

    # Basic info
    name: str = Field(description="Document name")
    document_type: DocumentType = Field(description="Type of document")
    mime_type: str | None = Field(default=None, description="MIME type")
    description: str | None = Field(default=None, description="Document description")

    # Location
    path: str | None = Field(default=None, description="Full path in storage")
    parent_id: UUID | None = Field(default=None, description="Parent folder ID")
    parent_path: str | None = Field(default=None, description="Parent folder path")

    # Size
    size_bytes: int | None = Field(default=None, ge=0, description="File size in bytes")

    # Ownership
    owner_id: str | None = Field(default=None, description="Owner user ID")
    owner_email: str | None = Field(default=None, description="Owner email")
    owner_name: str | None = Field(default=None, description="Owner display name")

    # Access
    my_permission: DocumentPermission | None = Field(
        default=None,
        description="Executive's permission level",
    )
    is_shared: bool = Field(default=False, description="Whether document is shared")
    shared_with: list[str] = Field(default_factory=list, description="Emails shared with")
    share_link: str | None = Field(default=None, description="Public share link")
    is_public: bool = Field(default=False, description="Whether publicly accessible")

    # Timing
    modified_at: datetime | None = Field(default=None, description="Last modification time")
    accessed_at: datetime | None = Field(default=None, description="Last access time")
    shared_at: datetime | None = Field(default=None, description="When document was shared")

    # Versioning
    version: str | None = Field(default=None, description="Current version identifier")
    version_count: int = Field(default=1, ge=1, description="Number of versions")

    # Content
    web_url: str | None = Field(default=None, description="URL to view in browser")
    download_url: str | None = Field(default=None, description="Direct download URL")
    thumbnail_url: str | None = Field(default=None, description="Thumbnail image URL")
    preview_url: str | None = Field(default=None, description="Preview URL")

    # Metadata
    tags: list[str] = Field(default_factory=list, description="Document tags")
    starred: bool = Field(default=False, description="Whether document is starred")
    trashed: bool = Field(default=False, description="Whether document is in trash")

    # Related entities
    organization_id: UUID | None = Field(default=None, description="Related organization")

    # Provider tracking
    provider_ref: ProviderReference | None = Field(
        default=None,
        description="Reference to document in source provider",
    )

    @property
    def extension(self) -> str | None:
        """Get file extension from name."""
        if "." in self.name:
            return self.name.rsplit(".", 1)[-1].lower()
        return None

    @property
    def is_folder(self) -> bool:
        """Check if this is a folder."""
        return self.document_type == DocumentType.FOLDER

    @property
    def is_editable(self) -> bool:
        """Check if executive can edit this document."""
        return self.my_permission in (DocumentPermission.OWNER, DocumentPermission.EDITOR)

    @property
    def size_kb(self) -> float | None:
        """Get file size in kilobytes."""
        if self.size_bytes is None:
            return None
        return self.size_bytes / 1024

    @property
    def size_mb(self) -> float | None:
        """Get file size in megabytes."""
        if self.size_bytes is None:
            return None
        return self.size_bytes / (1024 * 1024)
