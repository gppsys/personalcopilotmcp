"""Gmail provider implementation using Google Gmail API."""

import base64
import email.mime.text
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any
from uuid import uuid4

import httpx

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.admin.oauth import OAuthManager
from executive_copilot.logging import get_logger
from executive_copilot.models.base import ProviderReference
from executive_copilot.models.email import Email, EmailAddress, EmailLabel, EmailPriority

logger = get_logger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


class GmailProvider:
    """Gmail provider for fetching and sending emails."""

    def __init__(self, credential_manager: CredentialManager, account_id: str) -> None:
        self.credential_manager = credential_manager
        self.account_id = account_id
        self._access_token: str | None = None

    def _get_access_token(self) -> str:
        """Get access token from credentials."""
        if self._access_token:
            return self._access_token

        cred = self.credential_manager.get_credential("email", "gmail", self.account_id)
        if not cred or not cred.get("access_token"):
            raise ValueError(f"No access token for {self.account_id}. Please authorize first.")

        self._access_token = cred["access_token"]
        return self._access_token

    def _headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    async def _refresh_access_token(self) -> bool:
        """Refresh the cached access token.

        The OAuth client and refresh token are resolved across the account's
        sibling credentials (mail / calendar / documents), and the new token is
        stored on all of them.
        """
        oauth_manager = OAuthManager(self.credential_manager)
        new_token = await oauth_manager.refresh_google_token(account_id=self.account_id)
        if not new_token:
            return False

        self._access_token = new_token
        return True

    async def list_messages(
        self,
        max_results: int = 20,
        query: str | None = None,
        label_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List messages from Gmail.

        Args:
            max_results: Maximum number of messages to return
            query: Gmail search query (e.g., "is:unread", "from:someone@example.com")
            label_ids: Filter by label IDs (e.g., ["INBOX", "UNREAD"])

        Returns:
            List of message metadata
        """
        params: dict[str, Any] = {"maxResults": max_results}
        if query:
            params["q"] = query
        if label_ids:
            params["labelIds"] = label_ids

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages",
                headers=self._headers(),
                params=params,
            )

            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.get(
                        f"{GMAIL_API_BASE}/users/me/messages",
                        headers=self._headers(),
                        params=params,
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")

            response.raise_for_status()
            data = response.json()

            result: list[dict[str, Any]] = data.get("messages", [])
            return result

    async def get_message(self, message_id: str) -> Email:
        """Get a full message by ID.

        Args:
            message_id: Gmail message ID

        Returns:
            Email domain model
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages/{message_id}",
                headers=self._headers(),
                params={"format": "full"},
            )

            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.get(
                        f"{GMAIL_API_BASE}/users/me/messages/{message_id}",
                        headers=self._headers(),
                        params={"format": "full"},
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")

            response.raise_for_status()
            data = response.json()

            return self._parse_message(data)

    async def get_emails(
        self,
        max_results: int = 20,
        query: str | None = None,
        unread_only: bool = False,
    ) -> list[Email]:
        """Get emails with full details.

        Args:
            max_results: Maximum number of emails to return
            query: Gmail search query
            unread_only: Only return unread emails

        Returns:
            List of Email domain models
        """
        search_query = query or ""
        if unread_only:
            search_query = f"is:unread {search_query}".strip()

        messages = await self.list_messages(max_results=max_results, query=search_query)

        emails = []
        for msg in messages:
            try:
                email = await self.get_message(msg["id"])
                emails.append(email)
            except Exception as e:
                logger.warning("Failed to fetch message", message_id=msg["id"], error=str(e))

        return emails

    async def get_inbox(self, max_results: int = 20) -> list[Email]:
        """Get inbox emails."""
        return await self.get_emails(max_results=max_results, query="in:inbox")

    async def get_unread(self, max_results: int = 20) -> list[Email]:
        """Get unread emails."""
        return await self.get_emails(max_results=max_results, unread_only=True)

    async def search(self, query: str, max_results: int = 20) -> list[Email]:
        """Search emails."""
        return await self.get_emails(max_results=max_results, query=query)

    def _parse_message(self, data: dict[str, Any]) -> Email:
        """Parse Gmail API message into Email domain model."""
        headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}

        # Parse sender
        sender_raw = headers.get("from", "")
        sender_name, sender_email = parseaddr(sender_raw)
        sender = EmailAddress(
            address=sender_email or sender_raw,
            display_name=sender_name or None,
        )

        # Parse recipients
        to_recipients = self._parse_recipients(headers.get("to", ""))
        cc_recipients = self._parse_recipients(headers.get("cc", ""))

        # Parse date
        internal_date = data.get("internalDate")
        if internal_date:
            received_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC)
        else:
            received_at = datetime.now(UTC)

        # Get body
        body_text, body_html = self._extract_body(data.get("payload", {}))

        # Get snippet
        snippet = data.get("snippet", "")

        # Check labels
        label_ids = data.get("labelIds", [])
        is_read = "UNREAD" not in label_ids
        is_starred = "STARRED" in label_ids
        is_spam = "SPAM" in label_ids
        is_trash = "TRASH" in label_ids
        is_draft = "DRAFT" in label_ids
        is_sent = "SENT" in label_ids

        # Check attachments
        has_attachments = self._has_attachments(data.get("payload", {}))

        return Email(
            id=uuid4(),
            created_at=received_at,
            updated_at=None,
            sender=sender,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            bcc_recipients=[],
            subject=headers.get("subject", "(No Subject)"),
            body_text=body_text,
            body_html=body_html,
            snippet=snippet,
            received_at=received_at,
            is_read=is_read,
            is_starred=is_starred,
            is_spam=is_spam,
            is_trash=is_trash,
            is_draft=is_draft,
            is_sent=is_sent,
            has_attachments=has_attachments,
            priority=EmailPriority.NORMAL,
            labels=[
                EmailLabel(name=label_id, provider_id=label_id, is_system=label_id.isupper())
                for label_id in label_ids
            ],
            provider_ref=ProviderReference(
                provider_name="gmail",
                external_id=data["id"],
                thread_id=data.get("threadId"),
                raw_data={"account": self.account_id},
            ),
        )

    def _parse_recipients(self, header_value: str) -> list[EmailAddress]:
        """Parse recipient header into list of EmailAddress."""
        if not header_value:
            return []

        recipients = []
        for addr in header_value.split(","):
            name, email = parseaddr(addr.strip())
            if email:
                recipients.append(EmailAddress(address=email, display_name=name or None))

        return recipients

    def _extract_body(self, payload: dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract text and HTML body from message payload."""
        body_text = None
        body_html = None

        mime_type = payload.get("mimeType", "")

        if mime_type == "text/plain":
            body_text = self._decode_body(payload.get("body", {}))
        elif mime_type == "text/html":
            body_html = self._decode_body(payload.get("body", {}))
        elif mime_type.startswith("multipart/"):
            for part in payload.get("parts", []):
                part_mime = part.get("mimeType", "")
                if part_mime == "text/plain" and not body_text:
                    body_text = self._decode_body(part.get("body", {}))
                elif part_mime == "text/html" and not body_html:
                    body_html = self._decode_body(part.get("body", {}))
                elif part_mime.startswith("multipart/"):
                    # Nested multipart
                    nested_text, nested_html = self._extract_body(part)
                    if nested_text and not body_text:
                        body_text = nested_text
                    if nested_html and not body_html:
                        body_html = nested_html

        return body_text, body_html

    def _decode_body(self, body: dict[str, Any]) -> str | None:
        """Decode base64url encoded body."""
        data = body.get("data")
        if not data:
            return None

        try:
            # Gmail uses URL-safe base64
            decoded = base64.urlsafe_b64decode(data)
            return decoded.decode("utf-8", errors="replace")
        except Exception:
            return None

    def _has_attachments(self, payload: dict[str, Any]) -> bool:
        """Check if message has attachments."""
        if payload.get("filename"):
            return True

        for part in payload.get("parts", []):
            if part.get("filename"):
                return True
            if self._has_attachments(part):
                return True

        return False


    async def get_thread(self, thread_id: str) -> list[Email]:
        """Get all emails in a thread.

        Args:
            thread_id: Gmail thread ID

        Returns:
            List of Email domain models in chronological order
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/threads/{thread_id}",
                headers=self._headers(),
                params={"format": "full"},
            )

            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.get(
                        f"{GMAIL_API_BASE}/users/me/threads/{thread_id}",
                        headers=self._headers(),
                        params={"format": "full"},
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")

            response.raise_for_status()
            data = response.json()

            emails = []
            for msg in data.get("messages", []):
                emails.append(self._parse_message(msg))

            return sorted(emails, key=lambda e: e.received_at)

    async def send_email(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        reply_to_message_id: str | None = None,
        thread_id: str | None = None,
    ) -> Email:
        """Send an email.

        Args:
            to: List of recipient email addresses
            subject: Email subject
            body: Email body (plain text)
            cc: CC recipients
            bcc: BCC recipients
            reply_to_message_id: Message ID to reply to
            thread_id: Thread ID to add this message to

        Returns:
            Sent Email domain model
        """
        # Build the email message
        msg = email.mime.text.MIMEText(body)
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg["From"] = self.account_id

        if cc:
            msg["Cc"] = ", ".join(cc)
        if bcc:
            msg["Bcc"] = ", ".join(bcc)

        # Add threading headers if replying
        if reply_to_message_id:
            msg["In-Reply-To"] = reply_to_message_id
            msg["References"] = reply_to_message_id

        # Encode to base64url
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

        payload: dict[str, Any] = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/messages/send",
                headers=self._headers(),
                json=payload,
            )

            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.post(
                        f"{GMAIL_API_BASE}/users/me/messages/send",
                        headers=self._headers(),
                        json=payload,
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")

            response.raise_for_status()
            sent_data = response.json()

            # Fetch the full sent message
            return await self.get_message(sent_data["id"])

    async def create_draft(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        reply_to_message_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an email draft.

        Args:
            to: List of recipient email addresses
            subject: Email subject
            body: Email body (plain text)
            cc: CC recipients
            reply_to_message_id: Message ID to reply to
            thread_id: Thread ID to add this draft to

        Returns:
            Draft info with draft ID
        """
        msg = email.mime.text.MIMEText(body)
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg["From"] = self.account_id

        if cc:
            msg["Cc"] = ", ".join(cc)
        if reply_to_message_id:
            msg["In-Reply-To"] = reply_to_message_id
            msg["References"] = reply_to_message_id

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

        message_payload: dict[str, Any] = {"raw": raw}
        if thread_id:
            message_payload["threadId"] = thread_id

        payload = {"message": message_payload}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/drafts",
                headers=self._headers(),
                json=payload,
            )

            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.post(
                        f"{GMAIL_API_BASE}/users/me/drafts",
                        headers=self._headers(),
                        json=payload,
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")

            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result

    async def mark_as_read(self, message_id: str) -> bool:
        """Mark a message as read.

        Args:
            message_id: The message ID

        Returns:
            True if successful
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/messages/{message_id}/modify",
                headers=self._headers(),
                json={"removeLabelIds": ["UNREAD"]},
            )

            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.post(
                        f"{GMAIL_API_BASE}/users/me/messages/{message_id}/modify",
                        headers=self._headers(),
                        json={"removeLabelIds": ["UNREAD"]},
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")

            response.raise_for_status()
            return True

    async def add_label(self, message_id: str, label_name: str) -> bool:
        """Add a label to a message. Creates the label if it doesn't exist.

        Args:
            message_id: The message ID
            label_name: Label name (can be nested like "forwarded/accounting")

        Returns:
            True if successful
        """
        label_id = await self._get_or_create_label(label_name)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/messages/{message_id}/modify",
                headers=self._headers(),
                json={"addLabelIds": [label_id]},
            )

            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.post(
                        f"{GMAIL_API_BASE}/users/me/messages/{message_id}/modify",
                        headers=self._headers(),
                        json={"addLabelIds": [label_id]},
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")

            response.raise_for_status()
            return True

    async def _get_or_create_label(self, label_name: str) -> str:
        """Get label ID, creating it if it doesn't exist."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/labels",
                headers=self._headers(),
            )
            response.raise_for_status()
            labels = response.json().get("labels", [])

            for label in labels:
                if label.get("name", "").lower() == label_name.lower():
                    return str(label["id"])

            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/labels",
                headers=self._headers(),
                json={"name": label_name},
            )
            response.raise_for_status()
            return str(response.json()["id"])


async def test_gmail_connection(
    credential_manager: CredentialManager, account_id: str
) -> dict[str, Any]:
    """Test Gmail connection and return account info."""
    provider = GmailProvider(credential_manager, account_id)

    try:
        # Try to fetch one message to verify connection
        await provider.list_messages(max_results=1)

        async with httpx.AsyncClient() as client:
            # Get profile info
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/profile",
                headers=provider._headers(),
            )
            response.raise_for_status()
            profile = response.json()

        return {
            "success": True,
            "account_id": account_id,
            "email": profile.get("emailAddress"),
            "messages_total": profile.get("messagesTotal"),
            "threads_total": profile.get("threadsTotal"),
        }

    except Exception as e:
        return {
            "success": False,
            "account_id": account_id,
            "error": str(e),
        }
