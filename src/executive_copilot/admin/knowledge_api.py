"""Knowledge HTTP endpoints using the same service as MCP and jobs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from executive_copilot.admin.knowledge_auth import COOKIE, authorize_knowledge
from executive_copilot.services.knowledge.service import execute_knowledge


class KnowledgeChange(BaseModel):
    operation: str
    path: str
    content: str | None = None
    expected_hash: str | None = None


class ConflictResolution(BaseModel):
    content: str | None = None
    expected_hash: str | None = None


def knowledge_router(config_dir: Any, credentials: Any) -> APIRouter:
    router = APIRouter(prefix="/knowledge", tags=["knowledge"])

    async def run(request: Request, action: str, **kwargs: Any) -> Any:
        from executive_copilot.config import load_config

        token = request.headers.get("authorization", "").removeprefix(
            "Bearer "
        ) or request.cookies.get(COOKIE)
        try:
            authorize_knowledge(token)
            return await execute_knowledge(load_config(config_dir), credentials, action, **kwargs)
        except PermissionError:
            raise HTTPException(401, "Knowledge access denied") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except Exception:
            # Driver/network errors may contain URLs or document parameters.
            raise HTTPException(
                503, "Knowledge operation unavailable; pending changes preserved"
            ) from None

    @router.get("/dashboard")
    async def dashboard(request: Request) -> Any:
        return await run(request, "dashboard")

    @router.get("/search")
    async def search(
        request: Request,
        query: str = "",
        limit: int = 20,
        folder: str = "",
        tag: str = "",
        category: str = "",
    ) -> Any:
        return await run(
            request, "search", query=query, limit=limit, folder=folder, tag=tag, category=category
        )

    @router.get("/document")
    async def document(request: Request, path: str, revision: str | None = None) -> Any:
        if revision:
            return await run(request, "version", path=path, revision=revision)
        return await run(request, "get", path=path)

    @router.get("/related")
    async def related(request: Request, path: str) -> Any:
        return await run(request, "related", path=path)

    @router.get("/graph")
    async def graph(request: Request, path: str | None = None, limit: int = 100) -> Any:
        return await run(request, "graph", path=path, limit=limit)

    @router.get("/history")
    async def history(request: Request, path: str) -> Any:
        return {"results": await run(request, "history", path=path)}

    @router.post("/proposals")
    async def propose(request: Request, payload: KnowledgeChange) -> Any:
        return await run(request, "propose", **payload.model_dump())

    @router.get("/proposals/{proposal_id}")
    async def proposal(request: Request, proposal_id: str) -> Any:
        return await run(request, "proposal", proposal_id=proposal_id)

    @router.post("/proposals/{proposal_id}/confirm")
    async def confirm(request: Request, proposal_id: str) -> Any:
        return await run(request, "confirm", proposal_id=proposal_id)

    @router.get("/sync")
    async def status(request: Request) -> Any:
        return await run(request, "sync_status")

    @router.post("/sync")
    async def sync(request: Request) -> Any:
        return await run(request, "sync")

    @router.post("/reindex")
    async def reindex(request: Request) -> Any:
        return await run(request, "index", force=True)

    @router.get("/conflicts/{conflict_id}")
    async def conflict(request: Request, conflict_id: str) -> Any:
        return await run(request, "conflict", conflict_id=conflict_id)

    @router.post("/conflicts/{conflict_id}/resolve")
    async def resolve(request: Request, conflict_id: str, payload: ConflictResolution) -> Any:
        return await run(request, "resolve", conflict_id=conflict_id, **payload.model_dump())

    return router
