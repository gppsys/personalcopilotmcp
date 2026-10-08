"""Tests for Gmail provider retries and failure reporting."""

from typing import Any

import httpx
import pytest

from executive_copilot.providers.email import gmail
from executive_copilot.providers.email.gmail import GmailProvider

RATE_LIMIT_BODY = '{"error": {"code": 403, "errors": [{"reason": "rateLimitExceeded"}]}}'
SCOPE_BODY = '{"error": {"code": 403, "errors": [{"reason": "insufficientPermissions"}]}}'


class FakeCredentials:
    def get_credential(self, *args: Any) -> dict[str, str]:
        return {"access_token": "token"}


def _provider(monkeypatch: pytest.MonkeyPatch, handler: Any) -> GmailProvider:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(gmail.httpx, "AsyncClient", lambda: real_client(transport=transport))
    monkeypatch.setattr(gmail, "RETRY_DELAYS", (0.0, 0.0))
    return GmailProvider(FakeCredentials(), "me@example.com")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_messages_retries_rate_limited_403(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, text=RATE_LIMIT_BODY)
        return httpx.Response(200, json={"messages": [{"id": "a"}]})

    provider = _provider(monkeypatch, handler)
    assert await provider.list_messages(query="x") == [{"id": "a"}]
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_scope_403_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text=SCOPE_BODY)

    provider = _provider(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.list_messages(query="x")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_get_emails_raises_when_every_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "a"}, {"id": "b"}]})
        return httpx.Response(403, text=SCOPE_BODY)

    provider = _provider(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="any of 2 Gmail messages"):
        await provider.get_emails(query="x")


@pytest.mark.asyncio
async def test_get_emails_empty_mailbox_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch, lambda _request: httpx.Response(200, json={}))
    assert await provider.get_emails(query="x") == []
