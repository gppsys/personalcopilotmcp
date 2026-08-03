"""Slack communication provider implementation."""

from typing import Any

import httpx

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.exceptions import AuthenticationError, ProviderError
from executive_copilot.providers.base import ProviderCapability, ProviderInfo

SLACK_API_BASE = "https://slack.com/api"


class SlackProvider:
    """Slack provider using a bot token stored in encrypted credentials."""

    def __init__(self, credential_manager: CredentialManager, account_id: str = "default") -> None:
        self.credential_manager = credential_manager
        self.account_id = account_id
        self._bot_token: str | None = None
        self._auth_info: dict[str, Any] | None = None

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="slack",
            display_name="Slack",
            provider_type="communication",
            capabilities=[
                ProviderCapability.FULL_TEXT_SEARCH,
            ],
            auth_type="token",
        )

    def _get_bot_token(self) -> str:
        if self._bot_token:
            return self._bot_token

        cred = self.credential_manager.get_credential("communication", "slack", self.account_id)
        if not cred:
            raise AuthenticationError(
                f"No Slack credentials for {self.account_id}.",
                provider="slack",
            )

        extra = cred.get("extra") or {}
        token = cred.get("access_token") or cred.get("api_key") or extra.get("bot_token")
        if not token:
            raise AuthenticationError(
                f"No Slack bot token for {self.account_id}.",
                provider="slack",
            )

        self._bot_token = str(token)
        return self._bot_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_bot_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                f"{SLACK_API_BASE}/{path}",
                headers=self._headers(),
                params=params,
                json=json,
            )

        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            raise ProviderError(
                f"Slack API error: {error}",
                provider_name="slack",
                operation=path,
            )
        return data

    async def connect(self) -> None:
        await self.auth_test()

    async def disconnect(self) -> None:
        return None

    async def health_check(self) -> bool:
        try:
            await self.auth_test()
            return True
        except Exception:
            return False

    async def auth_test(self) -> dict[str, Any]:
        if self._auth_info is None:
            self._auth_info = await self._request("POST", "auth.test")
        return self._auth_info

    async def list_channels(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        exclude_archived: bool = True,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "conversations.list",
            params={
                "types": "public_channel,private_channel,mpim,im",
                "limit": min(limit, 1000),
                "cursor": cursor,
                "exclude_archived": str(exclude_archived).lower(),
            },
        )

    async def channel_history(
        self,
        channel_id: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "conversations.history",
            params={
                "channel": channel_id,
                "limit": min(limit, 200),
                "cursor": cursor,
            },
        )

    async def join_channel(self, channel_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "conversations.join",
            json={"channel": channel_id},
        )

    async def search_messages(
        self,
        query: str,
        *,
        count: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "search.messages",
            params={
                "query": query,
                "count": min(count, 100),
                "page": page,
                "sort": "timestamp",
                "sort_dir": "desc",
            },
        )
