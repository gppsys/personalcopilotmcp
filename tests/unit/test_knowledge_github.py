"""GitHub protocol checks using only mocked HTTP."""

import base64
from typing import Any

import httpx
import pytest

from executive_copilot.providers.code.knowledge_github import KnowledgeGitHub


@pytest.mark.asyncio
async def test_public_repository_blocks_all_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json={"private": False})

    provider = KnowledgeGitHub(None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        provider,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://api.github.com"
        ),
    )
    with pytest.raises(ValueError, match="private"):
        await provider.publish("owner/notes", "main", "a" * 40, {"A.md": "private content"})
    assert calls == ["GET"]


@pytest.mark.asyncio
async def test_publish_uses_parent_head_and_never_forces_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    provider = KnowledgeGitHub(None)  # type: ignore[arg-type]

    async def request(method: str, path: str, **kwargs: Any) -> Any:
        payload = kwargs.get("json", {})
        calls.append((method, path, payload))
        if path == "/repos/owner/notes":
            return {"private": True}
        if method == "GET":
            return {"tree": {"sha": "base-tree"}}
        return {"sha": "new-sha"}

    monkeypatch.setattr(provider, "request", request)
    await provider.publish("owner/notes", "main", "base-head", {"A.md": "new", "B.md": None})
    commit = next(c[2] for c in calls if c[0] == "POST" and c[1].endswith("/git/commits"))
    assert commit["parents"] == ["base-head"]
    assert calls[-1][2] == {"sha": "new-sha", "force": False}
    tree = next(c[2] for c in calls if c[1].endswith("/git/trees"))
    assert tree["tree"][1]["sha"] is None


@pytest.mark.asyncio
async def test_empty_repository_initializes_only_reviewed_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = KnowledgeGitHub(None)  # type: ignore[arg-type]
    sent = []

    async def request(method: str, path: str, **kwargs: Any) -> Any:
        if method == "GET":
            return {"private": True}
        sent.append(kwargs["json"])
        return {"commit": {"sha": "initial"}}

    monkeypatch.setattr(provider, "request", request)
    assert await provider.publish("owner/notes", "main", None, {"A.md": "reviewed"}) == "initial"
    assert base64.b64decode(sent[0]["content"]) == b"reviewed"
    assert sent[0]["branch"] == "main"


@pytest.mark.asyncio
async def test_truncated_tree_is_not_treated_as_deletions(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/git/ref/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "head"}})
        if "/git/commits/" in request.url.path:
            return httpx.Response(200, json={"tree": {"sha": "tree"}})
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json={"truncated": True, "tree": []})
        return httpx.Response(200, json={"private": True})

    provider = KnowledgeGitHub(None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        provider,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://api.github.com"
        ),
    )
    with pytest.raises(ValueError, match="Incomplete"):
        await provider.snapshot("owner/notes", "main")


@pytest.mark.asyncio
async def test_internal_repository_is_not_private(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = KnowledgeGitHub(None)  # type: ignore[arg-type]

    async def request(method: str, path: str, **kwargs: Any) -> Any:
        return {"private": True, "visibility": "internal"}

    monkeypatch.setattr(provider, "request", request)
    with pytest.raises(ValueError, match="private"):
        await provider.verify_private("owner/notes")


@pytest.mark.asyncio
async def test_branch_with_slash_keeps_ref_path(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode())
        if "/git/ref/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "head"}})
        if "/git/commits/" in request.url.path:
            return httpx.Response(200, json={"tree": {"sha": "tree"}})
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json={"tree": []})
        return httpx.Response(200, json={"private": True, "visibility": "private"})

    provider = KnowledgeGitHub(None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        provider,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://api.github.com"
        ),
    )
    await provider.snapshot("owner/notes", "notes/main")
    assert "/repos/owner/notes/git/ref/heads/notes/main" in paths
