"""Integration tests for email providers with mocked API responses."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from executive_copilot.models.email import Email
from executive_copilot.providers.email.gmail import GmailProvider
from executive_copilot.providers.email.outlook import OutlookProvider


@pytest.fixture
def mock_credential_manager():
    """Create a mock credential manager."""
    manager = MagicMock()
    manager.get_credential.return_value = {
        "access_token": "test_token",
        "refresh_token": "test_refresh_token",
        "client_id": "test_client_id",
        "client_secret": "test_secret",
    }
    return manager


class TestGmailProvider:
    """Tests for GmailProvider."""

    @pytest.mark.asyncio
    async def test_get_inbox(self, mock_credential_manager):
        """Test getting inbox emails."""
        provider = GmailProvider(mock_credential_manager, "test@gmail.com")

        mock_messages = [
            {"id": "msg1"},
            {"id": "msg2"},
        ]

        mock_message_data = {
            "id": "msg1",
            "internalDate": str(int(datetime.now(UTC).timestamp() * 1000)),
            "snippet": "Test email snippet",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "from", "value": "sender@test.com"},
                    {"name": "to", "value": "test@gmail.com"},
                    {"name": "subject", "value": "Test Subject"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "VGVzdCBib2R5"},  # "Test body" base64
            },
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response_list = MagicMock()
            mock_response_list.status_code = 200
            mock_response_list.json.return_value = {"messages": mock_messages}
            mock_response_list.raise_for_status = MagicMock()

            mock_response_get = MagicMock()
            mock_response_get.status_code = 200
            mock_response_get.json.return_value = mock_message_data
            mock_response_get.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=[mock_response_list, mock_response_get, mock_response_get])
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            emails = await provider.get_inbox(max_results=2)

            assert len(emails) == 2
            assert all(isinstance(e, Email) for e in emails)

    @pytest.mark.asyncio
    async def test_send_email(self, mock_credential_manager):
        """Test sending an email."""
        provider = GmailProvider(mock_credential_manager, "test@gmail.com")

        mock_sent_response = {
            "id": "sent_msg_1",
            "threadId": "thread_1",
        }

        mock_message_data = {
            "id": "sent_msg_1",
            "internalDate": str(int(datetime.now(UTC).timestamp() * 1000)),
            "snippet": "Sent email",
            "labelIds": ["SENT"],
            "payload": {
                "headers": [
                    {"name": "from", "value": "test@gmail.com"},
                    {"name": "to", "value": "recipient@test.com"},
                    {"name": "subject", "value": "Test Send"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "VGVzdCBib2R5"},
            },
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_post_response = MagicMock()
            mock_post_response.status_code = 200
            mock_post_response.json.return_value = mock_sent_response
            mock_post_response.raise_for_status = MagicMock()

            mock_get_response = MagicMock()
            mock_get_response.status_code = 200
            mock_get_response.json.return_value = mock_message_data
            mock_get_response.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_post_response)
            mock_instance.get = AsyncMock(return_value=mock_get_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            sent = await provider.send_email(
                to=["recipient@test.com"],
                subject="Test Send",
                body="Test body",
            )

            assert isinstance(sent, Email)
            assert sent.subject == "Test Send"

    @pytest.mark.asyncio
    async def test_get_thread(self, mock_credential_manager):
        """Test getting an email thread."""
        provider = GmailProvider(mock_credential_manager, "test@gmail.com")

        mock_thread_data = {
            "id": "thread_1",
            "messages": [
                {
                    "id": "msg1",
                    "internalDate": str(int(datetime.now(UTC).timestamp() * 1000 - 3600000)),
                    "snippet": "First message",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "headers": [
                            {"name": "from", "value": "sender@test.com"},
                            {"name": "subject", "value": "Thread Subject"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": "Rmlyc3QgbWVzc2FnZQ=="},
                    },
                },
                {
                    "id": "msg2",
                    "internalDate": str(int(datetime.now(UTC).timestamp() * 1000)),
                    "snippet": "Reply message",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "headers": [
                            {"name": "from", "value": "test@gmail.com"},
                            {"name": "subject", "value": "Re: Thread Subject"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": "UmVwbHkgbWVzc2FnZQ=="},
                    },
                },
            ],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_thread_data
            mock_response.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            emails = await provider.get_thread("thread_1")

            assert len(emails) == 2
            assert emails[0].received_at < emails[1].received_at


class TestOutlookProvider:
    """Tests for OutlookProvider."""

    @pytest.mark.asyncio
    async def test_get_inbox(self, mock_credential_manager):
        """Test getting inbox emails."""
        provider = OutlookProvider(mock_credential_manager, "test@outlook.com")

        mock_messages = {
            "value": [
                {
                    "id": "msg1",
                    "subject": "Test Subject",
                    "from": {"emailAddress": {"address": "sender@test.com", "name": "Sender"}},
                    "toRecipients": [{"emailAddress": {"address": "test@outlook.com"}}],
                    "ccRecipients": [],
                    "receivedDateTime": datetime.now(UTC).isoformat(),
                    "isRead": False,
                    "importance": "normal",
                    "hasAttachments": False,
                    "bodyPreview": "Test preview",
                },
            ],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_messages
            mock_response.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            emails = await provider.get_inbox(max_results=10)

            assert len(emails) == 1
            assert emails[0].subject == "Test Subject"

    @pytest.mark.asyncio
    async def test_send_email(self, mock_credential_manager):
        """Test sending an email via Outlook."""
        provider = OutlookProvider(mock_credential_manager, "test@outlook.com")

        mock_sent_messages = {
            "value": [
                {
                    "id": "sent1",
                    "subject": "Test Send",
                    "from": {"emailAddress": {"address": "test@outlook.com"}},
                    "toRecipients": [{"emailAddress": {"address": "recipient@test.com"}}],
                    "ccRecipients": [],
                    "receivedDateTime": datetime.now(UTC).isoformat(),
                    "isRead": True,
                    "importance": "normal",
                    "hasAttachments": False,
                    "bodyPreview": "Test body",
                },
            ],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_post_response = MagicMock()
            mock_post_response.status_code = 202
            mock_post_response.raise_for_status = MagicMock()

            mock_get_response = MagicMock()
            mock_get_response.status_code = 200
            mock_get_response.json.return_value = mock_sent_messages
            mock_get_response.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_post_response)
            mock_instance.get = AsyncMock(return_value=mock_get_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            sent = await provider.send_email(
                to=["recipient@test.com"],
                subject="Test Send",
                body="Test body",
            )

            assert isinstance(sent, Email)
            assert sent.subject == "Test Send"

    @pytest.mark.asyncio
    async def test_get_thread(self, mock_credential_manager):
        """Test getting an email thread from Outlook."""
        provider = OutlookProvider(mock_credential_manager, "test@outlook.com")

        mock_thread = {
            "value": [
                {
                    "id": "msg1",
                    "subject": "Thread Subject",
                    "from": {"emailAddress": {"address": "sender@test.com"}},
                    "toRecipients": [{"emailAddress": {"address": "test@outlook.com"}}],
                    "ccRecipients": [],
                    "receivedDateTime": "2024-01-01T10:00:00Z",
                    "isRead": True,
                    "importance": "normal",
                    "hasAttachments": False,
                    "bodyPreview": "First message",
                },
                {
                    "id": "msg2",
                    "subject": "Re: Thread Subject",
                    "from": {"emailAddress": {"address": "test@outlook.com"}},
                    "toRecipients": [{"emailAddress": {"address": "sender@test.com"}}],
                    "ccRecipients": [],
                    "receivedDateTime": "2024-01-01T11:00:00Z",
                    "isRead": True,
                    "importance": "normal",
                    "hasAttachments": False,
                    "bodyPreview": "Reply",
                },
            ],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_thread
            mock_response.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            emails = await provider.get_thread("conv_id_123")

            assert len(emails) == 2
