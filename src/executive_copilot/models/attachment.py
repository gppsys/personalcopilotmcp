"""Attachment domain model."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from executive_copilot.models.base import DomainModel, ProviderReference


class AttachmentType(StrEnum):
    """Type of attachment based on content."""

    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    ARCHIVE = "archive"
    SPREADSHEET = "spreadsheet"
    PRESENTATION = "presentation"
    PDF = "pdf"
    CODE = "code"
    OTHER = "other"


# MIME type to attachment type mapping
MIME_TYPE_MAPPING: dict[str, AttachmentType] = {
    # Documents
    "application/pdf": AttachmentType.PDF,
    "application/msword": AttachmentType.DOCUMENT,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": AttachmentType.DOCUMENT,
    "application/rtf": AttachmentType.DOCUMENT,
    "text/plain": AttachmentType.DOCUMENT,
    # Spreadsheets
    "application/vnd.ms-excel": AttachmentType.SPREADSHEET,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": AttachmentType.SPREADSHEET,
    "text/csv": AttachmentType.SPREADSHEET,
    # Presentations
    "application/vnd.ms-powerpoint": AttachmentType.PRESENTATION,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": AttachmentType.PRESENTATION,
    # Images
    "image/jpeg": AttachmentType.IMAGE,
    "image/png": AttachmentType.IMAGE,
    "image/gif": AttachmentType.IMAGE,
    "image/webp": AttachmentType.IMAGE,
    "image/svg+xml": AttachmentType.IMAGE,
    # Video
    "video/mp4": AttachmentType.VIDEO,
    "video/mpeg": AttachmentType.VIDEO,
    "video/quicktime": AttachmentType.VIDEO,
    # Audio
    "audio/mpeg": AttachmentType.AUDIO,
    "audio/wav": AttachmentType.AUDIO,
    "audio/ogg": AttachmentType.AUDIO,
    # Archives
    "application/zip": AttachmentType.ARCHIVE,
    "application/x-rar-compressed": AttachmentType.ARCHIVE,
    "application/x-tar": AttachmentType.ARCHIVE,
    "application/gzip": AttachmentType.ARCHIVE,
    # Code
    "application/json": AttachmentType.CODE,
    "application/xml": AttachmentType.CODE,
    "text/html": AttachmentType.CODE,
    "text/css": AttachmentType.CODE,
    "text/javascript": AttachmentType.CODE,
    "application/javascript": AttachmentType.CODE,
}


def attachment_type_from_mime(mime_type: str) -> AttachmentType:
    """Determine attachment type from MIME type."""
    # Normalize mime type
    mime_lower = mime_type.lower().split(";")[0].strip()

    if mime_lower in MIME_TYPE_MAPPING:
        return MIME_TYPE_MAPPING[mime_lower]

    # Fallback based on general type
    if mime_lower.startswith("image/"):
        return AttachmentType.IMAGE
    if mime_lower.startswith("video/"):
        return AttachmentType.VIDEO
    if mime_lower.startswith("audio/"):
        return AttachmentType.AUDIO
    if mime_lower.startswith("text/"):
        return AttachmentType.DOCUMENT

    return AttachmentType.OTHER


class Attachment(DomainModel):
    """A file attachment from any source (email, task, document).

    Attachments are stored separately from their parent entities
    to allow for deduplication and independent lifecycle management.
    """

    # Identity
    filename: str = Field(description="Original filename")
    content_type: str = Field(description="MIME type")
    attachment_type: AttachmentType = Field(description="Categorized attachment type")

    # Size
    size_bytes: int = Field(ge=0, description="File size in bytes")

    # Source reference
    source_type: str = Field(description="Source type (email, task, document)")
    source_id: UUID = Field(description="ID of the source entity")

    # Storage
    storage_path: str | None = Field(
        default=None,
        description="Path to local storage if downloaded",
    )
    is_downloaded: bool = Field(default=False, description="Whether file is locally available")
    checksum: str | None = Field(default=None, description="File checksum for integrity")

    # Metadata
    is_inline: bool = Field(default=False, description="Whether attachment is inline (e.g., embedded image)")
    content_id: str | None = Field(default=None, description="Content-ID for inline attachments")
    description: str | None = Field(default=None, description="Description or alt text")

    # Provider tracking
    provider_ref: ProviderReference | None = Field(
        default=None,
        description="Reference to attachment in source provider",
    )

    @property
    def size_kb(self) -> float:
        """Get file size in kilobytes."""
        return self.size_bytes / 1024

    @property
    def size_mb(self) -> float:
        """Get file size in megabytes."""
        return self.size_bytes / (1024 * 1024)

    @property
    def extension(self) -> str | None:
        """Get file extension from filename."""
        if "." in self.filename:
            return self.filename.rsplit(".", 1)[-1].lower()
        return None

    @classmethod
    def from_mime_type(
        cls,
        filename: str,
        mime_type: str,
        size_bytes: int,
        source_type: str,
        source_id: UUID,
        **kwargs: object,
    ) -> "Attachment":
        """Create attachment with auto-detected type from MIME type."""
        return cls(
            filename=filename,
            content_type=mime_type,
            attachment_type=attachment_type_from_mime(mime_type),
            size_bytes=size_bytes,
            source_type=source_type,
            source_id=source_id,
            **kwargs,  # type: ignore[arg-type]
        )
