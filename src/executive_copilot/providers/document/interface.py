"""Document provider interface."""

from abc import abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from pydantic import BaseModel, Field

from executive_copilot.models import Document, DocumentType
from executive_copilot.providers.base import Provider


class DocumentQuery(BaseModel):
    """Query parameters for document search."""

    # Content search
    query: str | None = Field(default=None, description="Full-text search query")
    name_contains: str | None = Field(default=None, description="Name contains")

    # Location
    folder_id: str | None = Field(default=None, description="Search within folder")
    recursive: bool = Field(default=True, description="Search subfolders")

    # Type filters
    document_types: list[DocumentType] = Field(
        default_factory=list,
        description="Filter by document type",
    )
    mime_types: list[str] = Field(default_factory=list, description="Filter by MIME type")

    # Ownership and sharing
    owner_email: str | None = Field(default=None, description="Filter by owner")
    shared_with_me: bool | None = Field(default=None, description="Only shared documents")

    # Date filters
    modified_after: datetime | None = Field(default=None, description="Modified after")
    modified_before: datetime | None = Field(default=None, description="Modified before")
    created_after: datetime | None = Field(default=None, description="Created after")
    created_before: datetime | None = Field(default=None, description="Created before")

    # Status
    include_trashed: bool = Field(default=False, description="Include trashed")
    starred_only: bool = Field(default=False, description="Only starred")

    # Pagination
    limit: int = Field(default=50, ge=1, le=1000, description="Maximum results")
    page_token: str | None = Field(default=None, description="Pagination token")


class UploadRequest(BaseModel):
    """Request to upload a document."""

    name: str = Field(description="File name")
    content: bytes | None = Field(default=None, description="File content")
    file_path: str | None = Field(default=None, description="Local file path")
    mime_type: str | None = Field(default=None, description="MIME type")
    parent_folder_id: str | None = Field(default=None, description="Parent folder ID")
    description: str | None = Field(default=None, description="File description")


class DocumentProvider(Provider[Document]):
    """Interface for document storage providers.

    Implementations must map their specific document formats to the
    Document domain model.
    """

    # --- Reading ---

    @abstractmethod
    async def get_document(self, document_id: str) -> Document | None:
        """Get a document by ID.

        Args:
            document_id: Document ID

        Returns:
            Document if found
        """
        ...

    @abstractmethod
    async def search_documents(
        self,
        query: DocumentQuery,
    ) -> tuple[list[Document], str | None]:
        """Search documents with criteria.

        Args:
            query: Search parameters

        Returns:
            Tuple of (documents, next_page_token)
        """
        ...

    @abstractmethod
    async def stream_documents(
        self,
        query: DocumentQuery,
    ) -> AsyncIterator[Document]:
        """Stream documents for large result sets.

        Args:
            query: Search parameters

        Yields:
            Document objects
        """
        ...

    @abstractmethod
    async def list_folder(
        self,
        folder_id: str | None = None,
    ) -> list[Document]:
        """List contents of a folder.

        Args:
            folder_id: Folder ID (None = root)

        Returns:
            List of documents and subfolders
        """
        ...

    @abstractmethod
    async def get_recent_documents(self, limit: int = 20) -> list[Document]:
        """Get recently accessed documents.

        Args:
            limit: Maximum documents to return

        Returns:
            List of recently accessed documents
        """
        ...

    @abstractmethod
    async def get_shared_with_me(self, limit: int = 50) -> list[Document]:
        """Get documents shared with the user.

        Args:
            limit: Maximum documents to return

        Returns:
            List of shared documents
        """
        ...

    # --- Content Access ---

    @abstractmethod
    async def download_content(self, document_id: str) -> bytes:
        """Download document content.

        Args:
            document_id: Document to download

        Returns:
            Document content as bytes
        """
        ...

    @abstractmethod
    async def download_to_file(
        self,
        document_id: str,
        destination: str,
    ) -> Document:
        """Download document to local file.

        Args:
            document_id: Document to download
            destination: Local file path

        Returns:
            Document with storage_path set
        """
        ...

    @abstractmethod
    async def get_export_link(
        self,
        document_id: str,
        export_format: str,
    ) -> str:
        """Get export link for a document.

        Args:
            document_id: Document to export
            export_format: Target format (pdf, docx, etc.)

        Returns:
            Export download URL
        """
        ...

    # --- Upload ---

    @abstractmethod
    async def upload_document(self, request: UploadRequest) -> Document:
        """Upload a new document.

        Args:
            request: Upload request with content

        Returns:
            Created document
        """
        ...

    @abstractmethod
    async def update_content(
        self,
        document_id: str,
        content: bytes,
    ) -> Document:
        """Update document content.

        Args:
            document_id: Document to update
            content: New content

        Returns:
            Updated document
        """
        ...

    # --- Folder Operations ---

    @abstractmethod
    async def create_folder(
        self,
        name: str,
        parent_id: str | None = None,
    ) -> Document:
        """Create a new folder.

        Args:
            name: Folder name
            parent_id: Parent folder ID

        Returns:
            Created folder
        """
        ...

    # --- Metadata Operations ---

    @abstractmethod
    async def rename_document(
        self,
        document_id: str,
        new_name: str,
    ) -> Document:
        """Rename a document.

        Args:
            document_id: Document to rename
            new_name: New name

        Returns:
            Updated document
        """
        ...

    @abstractmethod
    async def move_document(
        self,
        document_id: str,
        destination_folder_id: str,
    ) -> Document:
        """Move a document to a different folder.

        Args:
            document_id: Document to move
            destination_folder_id: Target folder

        Returns:
            Updated document
        """
        ...

    @abstractmethod
    async def star_document(
        self,
        document_id: str,
        starred: bool = True,
    ) -> Document:
        """Star or unstar a document.

        Args:
            document_id: Document to modify
            starred: True to star, False to unstar

        Returns:
            Updated document
        """
        ...

    @abstractmethod
    async def trash_document(self, document_id: str) -> Document:
        """Move document to trash.

        Args:
            document_id: Document to trash

        Returns:
            Trashed document
        """
        ...

    @abstractmethod
    async def delete_document(
        self,
        document_id: str,
        permanent: bool = False,
    ) -> None:
        """Delete a document.

        Args:
            document_id: Document to delete
            permanent: If True, permanently delete
        """
        ...

    # --- Sharing ---

    @abstractmethod
    async def share_document(
        self,
        document_id: str,
        email: str,
        permission: str = "reader",
        notify: bool = True,
        message: str | None = None,
    ) -> dict[str, object]:
        """Share document with a user.

        Args:
            document_id: Document to share
            email: Email to share with
            permission: Permission level
            notify: Send notification email
            message: Optional message

        Returns:
            Permission info
        """
        ...

    @abstractmethod
    async def get_permissions(
        self,
        document_id: str,
    ) -> list[dict[str, object]]:
        """Get sharing permissions for a document.

        Args:
            document_id: Document to check

        Returns:
            List of permission entries
        """
        ...

    @abstractmethod
    async def remove_permission(
        self,
        document_id: str,
        permission_id: str,
    ) -> None:
        """Remove a sharing permission.

        Args:
            document_id: Document to modify
            permission_id: Permission to remove
        """
        ...

    # --- Sync ---

    @abstractmethod
    async def get_changes_since(
        self,
        sync_token: str | None = None,
    ) -> tuple[list[Document], str]:
        """Get documents changed since last sync.

        Args:
            sync_token: Token from previous sync

        Returns:
            Tuple of (changed documents, new sync token)
        """
        ...
