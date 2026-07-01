"""Outlook email provider using Microsoft Graph API."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.admin.oauth import OAuthManager
from executive_copilot.logging import get_logger
from executive_copilot.models.base import ProviderReference
from executive_copilot.models.email import Email, EmailAddress, EmailPriority

logger = get_logger(__name__)

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"


class OutlookProvider:
    """Outlook/Microsoft 365 email provider using Graph API."""

    def __init__(self, credential_manager: CredentialManager, account_id: str) -> None:
        self.credential_manager = credential_manager
        self.account_id = account_id
        self._access_token: str | None = None

    def _get_access_token(self) -> str:
        """Get access token from credentials."""
        if self._access_token:
            return self._access_token

        cred = self.credential_manager.get_credential("email", "outlook", self.account_id)
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
        """Refresh the cached access token if a refresh token is available."""
        cred = self.credential_manager.get_credential("email", "outlook", self.account_id)
        if not cred or not cred.get("client_id") or not cred.get("refresh_token"):
            return False

        oauth_manager = OAuthManager(self.credential_manager)
        new_token = await oauth_manager.refresh_microsoft_token(
            account_id=self.account_id,
            client_id=cred["client_id"],
            client_secret=cred.get("client_secret", ""),
            tenant=cred.get("tenant_id", "common"),
        )
        if not new_token:
            return False

        self._access_token = new_token
        return True

    async def _api_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make GET request to Graph API."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GRAPH_API_BASE}{path}",
                headers=self._headers(),
                params=params,
            )
            if response.status_code == 401:
                if await self._refresh_access_token():
                    response = await client.get(
                        f"{GRAPH_API_BASE}{path}",
                        headers=self._headers(),
                        params=params,
                    )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result

    async def list_messages(
        self,
        max_results: int = 20,
        filter_query: str | None = None,
        search_query: str | None = None,
        folder: str | None = "inbox",
    ) -> list[dict[str, Any]]:
        """List messages from Outlook.

        Args:
            max_results: Maximum number of messages to return
            filter_query: OData filter (e.g., "isRead eq false")
            search_query: Search query
            folder: Mail folder (inbox, sentitems, drafts, etc.)

        Returns:
            List of message metadata
        """
        select_fields = (
            "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
            "isRead,importance,hasAttachments,bodyPreview,flag"
        )
        params: dict[str, Any] = {
            "$top": max_results,
            "$select": select_fields,
        }

        if search_query:
            # $search requires /me/messages endpoint (not folder-specific)
            # and cannot be combined with $orderby or $filter
            params["$search"] = f'"{search_query}"'
            path = "/me/messages"
        else:
            params["$orderby"] = "receivedDateTime desc"
            if filter_query:
                params["$filter"] = filter_query
            path = "/me/messages" if folder is None else f"/me/mailFolders/{folder}/messages"

        data = await self._api_get(path, params)
        result: list[dict[str, Any]] = data.get("value", [])
        return result

    async def get_message(self, message_id: str) -> Email:
        """Get a full message by ID.

        Args:
            message_id: Outlook message ID

        Returns:
            Email domain model
        """
        data = await self._api_get(f"/me/messages/{message_id}")
        return self._parse_message(data)

    async def get_emails(
        self,
        max_results: int = 20,
        search_query: str | None = None,
        unread_only: bool = False,
        folder: str = "inbox",
        query: str | None = None,
    ) -> list[Email]:
        """Get emails with full details.

        Args:
            max_results: Maximum number of emails to return
            search_query: Search query
            unread_only: Only return unread emails
            folder: Mail folder
            query: Alias for search_query (for compatibility with Gmail)

        Returns:
            List of Email domain models
        """
        effective_query = query or search_query
        filter_query = "isRead eq false" if unread_only else None
        messages = await self.list_messages(
            max_results=max_results,
            filter_query=filter_query,
            search_query=effective_query,
            folder=folder,
        )

        emails = []
        for msg in messages:
            try:
                email = self._parse_message(msg)
                emails.append(email)
            except Exception as e:
                logger.warning("Failed to parse message", message_id=msg.get("id"), error=str(e))

        return emails

    async def get_inbox(self, max_results: int = 20) -> list[Email]:
        """Get inbox emails."""
        return await self.get_emails(max_results=max_results, folder="inbox")

    async def get_unread(self, max_results: int = 20) -> list[Email]:
        """Get unread emails."""
        return await self.get_emails(max_results=max_results, unread_only=True)

    async def search(self, query: str, max_results: int = 20) -> list[Email]:
        """Search emails."""
        return await self.get_emails(max_results=max_results, search_query=query)

    async def get_from_sender(self, sender: str, max_results: int = 20) -> list[Email]:
        """Get emails from a specific sender."""
        filter_query = f"from/emailAddress/address eq '{sender}'"
        messages = await self.list_messages(
            max_results=max_results,
            filter_query=filter_query,
        )
        return [self._parse_message(msg) for msg in messages]

    def _parse_message(self, data: dict[str, Any]) -> Email:
        """Parse Graph API message into Email domain model."""
        from_data = data.get("from", {}).get("emailAddress", {})
        sender = EmailAddress(
            address=from_data.get("address", ""),
            display_name=from_data.get("name"),
        )

        to_recipients = [
            EmailAddress(
                address=r.get("emailAddress", {}).get("address", ""),
                display_name=r.get("emailAddress", {}).get("name"),
            )
            for r in data.get("toRecipients", [])
        ]

        cc_recipients = [
            EmailAddress(
                address=r.get("emailAddress", {}).get("address", ""),
                display_name=r.get("emailAddress", {}).get("name"),
            )
            for r in data.get("ccRecipients", [])
        ]

        received_str = data.get("receivedDateTime", "")
        if received_str:
            received_at = datetime.fromisoformat(received_str.replace("Z", "+00:00"))
        else:
            received_at = datetime.now(UTC)

        importance_map = {
            "low": EmailPriority.LOW,
            "normal": EmailPriority.NORMAL,
            "high": EmailPriority.HIGH,
        }
        priority = importance_map.get(data.get("importance", "normal"), EmailPriority.NORMAL)

        body_data = data.get("body", {})
        body_text = None
        body_html = None
        if body_data.get("contentType") == "text":
            body_text = body_data.get("content")
        elif body_data.get("contentType") == "html":
            body_html = body_data.get("content")

        is_flagged = data.get("flag", {}).get("flagStatus") == "flagged"

        return Email(
            id=uuid4(),
            created_at=received_at,
            updated_at=None,
            sender=sender,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            bcc_recipients=[],
            subject=data.get("subject", "(No Subject)"),
            body_text=body_text,
            body_html=body_html,
            snippet=data.get("bodyPreview", ""),
            received_at=received_at,
            is_read=data.get("isRead", False),
            is_starred=is_flagged,
            is_spam=False,
            is_trash=False,
            is_draft=data.get("isDraft", False),
            is_sent=False,
            has_attachments=data.get("hasAttachments", False),
            priority=priority,
            labels=[],
            provider_ref=ProviderReference(
                provider_name="outlook",
                external_id=data["id"],
                thread_id=data.get("conversationId"),
                raw_data={"account": self.account_id},
            ),
        )


    async def get_thread(self, conversation_id: str) -> list[Email]:
        """Get all emails in a conversation thread.

        Args:
            conversation_id: Outlook conversation ID

        Returns:
            List of Email domain models in chronological order
        """
        params = {
            "$filter": f"conversationId eq '{conversation_id}'",
            "$orderby": "receivedDateTime asc",
            "$top": 100,
        }
        data = await self._api_get("/me/messages", params)

        emails = []
        for msg in data.get("value", []):
            emails.append(self._parse_message(msg))

        return emails

    async def send_email(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        reply_to_message_id: str | None = None,
    ) -> Email:
        """Send an email.

        Args:
            to: List of recipient email addresses
            subject: Email subject
            body: Email body (plain text)
            cc: CC recipients
            bcc: BCC recipients
            reply_to_message_id: Message ID to reply to

        Returns:
            Sent Email domain model
        """
        message: dict[str, Any] = {
            "subject": subject,
            "body": {
                "contentType": "text",
                "content": body,
            },
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in to
            ],
        }

        if cc:
            message["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc
            ]
        if bcc:
            message["bccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in bcc
            ]

        async with httpx.AsyncClient() as client:
            if reply_to_message_id:
                # Reply to existing message
                response = await client.post(
                    f"{GRAPH_API_BASE}/me/messages/{reply_to_message_id}/reply",
                    headers=self._headers(),
                    json={"message": message, "comment": body},
                )
            else:
                # Send new message
                response = await client.post(
                    f"{GRAPH_API_BASE}/me/sendMail",
                    headers=self._headers(),
                    json={"message": message, "saveToSentItems": True},
                )

            if response.status_code == 401:
                if await self._refresh_access_token():
                    if reply_to_message_id:
                        response = await client.post(
                            f"{GRAPH_API_BASE}/me/messages/{reply_to_message_id}/reply",
                            headers=self._headers(),
                            json={"message": message, "comment": body},
                        )
                    else:
                        response = await client.post(
                            f"{GRAPH_API_BASE}/me/sendMail",
                            headers=self._headers(),
                            json={"message": message, "saveToSentItems": True},
                        )
                if response.status_code == 401:
                    raise ValueError("Access token expired. Please re-authorize.")

            response.raise_for_status()

            # Fetch the sent message from sent items
            sent_messages = await self.list_messages(max_results=1, folder="sentitems")
            if sent_messages:
                return self._parse_message(sent_messages[0])

            # Return a minimal email if we can't fetch it
            now = datetime.now(UTC)
            return Email(
                id=uuid4(),
                created_at=now,
                updated_at=None,
                sender=EmailAddress(address=self.account_id),
                to_recipients=[EmailAddress(address=addr) for addr in to],
                cc_recipients=[EmailAddress(address=addr) for addr in (cc or [])],
                bcc_recipients=[],
                subject=subject,
                body_text=body,
                snippet=body[:100],
                received_at=now,
                is_sent=True,
            )

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
            thread_id: Conversation ID (for compatibility, uses reply_to_message_id if set)

        Returns:
            Draft info with draft ID
        """
        # Use thread_id as fallback for reply_to_message_id
        effective_reply_id = reply_to_message_id or thread_id
        message: dict[str, Any] = {
            "subject": subject,
            "body": {
                "contentType": "text",
                "content": body,
            },
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in to
            ],
        }

        if cc:
            message["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc
            ]

        async with httpx.AsyncClient() as client:
            if effective_reply_id:
                response = await client.post(
                    f"{GRAPH_API_BASE}/me/messages/{effective_reply_id}/createReply",
                    headers=self._headers(),
                )
                if response.status_code == 401:
                    if await self._refresh_access_token():
                        response = await client.post(
                            f"{GRAPH_API_BASE}/me/messages/{effective_reply_id}/createReply",
                            headers=self._headers(),
                        )
                response.raise_for_status()
                draft_id = response.json()["id"]

                # Update the draft with our content
                await client.patch(
                    f"{GRAPH_API_BASE}/me/messages/{draft_id}",
                    headers=self._headers(),
                    json=message,
                )
                result: dict[str, Any] = response.json()
                return result
            else:
                response = await client.post(
                    f"{GRAPH_API_BASE}/me/messages",
                    headers=self._headers(),
                    json=message,
                )
                if response.status_code == 401:
                    if await self._refresh_access_token():
                        response = await client.post(
                            f"{GRAPH_API_BASE}/me/messages",
                            headers=self._headers(),
                            json=message,
                        )
                response.raise_for_status()
                result = response.json()
                return result


async def test_outlook_connection(
    credential_manager: CredentialManager, account_id: str
) -> dict[str, Any]:
    """Test Outlook connection and return account info."""
    provider = OutlookProvider(credential_manager, account_id)

    try:
        await provider.list_messages(max_results=1)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GRAPH_API_BASE}/me",
                headers=provider._headers(),
            )
            response.raise_for_status()
            profile = response.json()

        return {
            "success": True,
            "account_id": account_id,
            "email": profile.get("mail") or profile.get("userPrincipalName"),
            "display_name": profile.get("displayName"),
        }

    except Exception as e:
        return {
            "success": False,
            "account_id": account_id,
            "error": str(e),
        }
