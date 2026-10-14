"""Workspace and MCP authorization checks without external services."""

import httpx
import pytest
from fastapi import FastAPI

from executive_copilot.admin.knowledge_auth import install_workspace_auth
from executive_copilot.config.models import AppConfig
from executive_copilot.mcp.knowledge import register_knowledge_tools


@pytest.mark.asyncio
async def test_workspace_login_and_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPILOT_WORKSPACE_ACCESS_TOKEN", "a" * 40)
    app = FastAPI()
    install_workspace_auth(app)

    @app.get("/api/private")
    async def private() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/private")
    async def write() -> dict[str, bool]:
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/private")).status_code == 401
        assert (await client.post("/api/session", json={"token": "wrong"})).status_code == 401
        response = await client.post("/api/session", json={"token": "a" * 40})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert (await client.get("/api/private")).status_code == 200
        assert (
            await client.post("/api/private", headers={"Origin": "http://evil"})
        ).status_code == 403
        assert (
            await client.post("/api/private", headers={"Origin": "http://test"})
        ).status_code == 200
        assert (
            await client.delete("/api/session", headers={"Origin": "http://evil"})
        ).status_code == 403
        assert (await client.delete("/api/session")).status_code == 200
        assert (await client.get("/api/private")).status_code == 401


@pytest.mark.asyncio
async def test_knowledge_requires_configured_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COPILOT_WORKSPACE_ACCESS_TOKEN", raising=False)
    app = FastAPI()
    install_workspace_auth(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/ws/knowledge/dashboard")).status_code == 503


@pytest.mark.asyncio
async def test_mcp_schema_authorization_and_shared_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import FastMCP

    monkeypatch.setenv("COPILOT_WORKSPACE_ACCESS_TOKEN", "a" * 40)
    calls = []

    async def execute(
        config: object, credentials: object, action: str, **kwargs: object
    ) -> dict[str, object]:
        calls.append((action, kwargs))
        return {"mode": "textual", "results": []}

    monkeypatch.setattr("executive_copilot.mcp.knowledge.execute_knowledge", execute)
    mcp = FastMCP("test")
    register_knowledge_tools(mcp, AppConfig(), None)
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} >= {
        "knowledge_search",
        "knowledge_get",
        "knowledge_create",
        "knowledge_update",
        "knowledge_delete",
        "knowledge_related",
        "knowledge_graph",
        "knowledge_history",
        "knowledge_sync",
        "knowledge_sync_status",
    }
    tool = await mcp.get_tool("knowledge_search")
    result = await tool.run({"access_token": "wrong", "query": "secret"})
    assert "unauthorized" in str(result)
    assert not calls
    await tool.run({"access_token": "a" * 40, "query": "reflection"})
    assert calls[0][0] == "search"


@pytest.mark.asyncio
async def test_mcp_transport_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    from executive_copilot.mcp.knowledge import WorkspaceTokenVerifier

    monkeypatch.setenv("COPILOT_WORKSPACE_ACCESS_TOKEN", "a" * 40)
    verifier = WorkspaceTokenVerifier()
    assert await verifier.verify_token("wrong") is None
    assert await verifier.verify_token("á" * 40) is None
    token = await verifier.verify_token("a" * 40)
    assert token is not None and token.client_id == "workspace"


@pytest.mark.asyncio
async def test_session_cookie_is_not_the_key_and_ends_server_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_WORKSPACE_ACCESS_TOKEN", "a" * 40)
    app = FastAPI()
    install_workspace_auth(app)

    @app.get("/api/private")
    async def private() -> dict[str, bool]:
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/session", json={"token": "a" * 40})
        session = client.cookies["copilot_session"]
        assert session != "a" * 40
        assert (await client.get("/api/private")).status_code == 200
        # A copied cookie stops working after logout, and after the key is rotated.
        await client.delete("/api/session")
        client.cookies.set("copilot_session", session)
        assert (await client.get("/api/private")).status_code == 401
        await client.post("/api/session", json={"token": "a" * 40})
        monkeypatch.setenv("COPILOT_WORKSPACE_ACCESS_TOKEN", "b" * 40)
        assert (await client.get("/api/private")).status_code == 401
        headers = {"Authorization": "Bearer " + "b" * 40}
        assert (await client.get("/api/private", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_repeated_failed_logins_are_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPILOT_WORKSPACE_ACCESS_TOKEN", "a" * 40)
    app = FastAPI()
    install_workspace_auth(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(5):
            assert (await client.post("/api/session", json={"token": "x"})).status_code == 401
        assert (await client.post("/api/session", json={"token": "a" * 40})).status_code == 429
