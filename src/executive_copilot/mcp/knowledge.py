"""Additive, authorized personal knowledge MCP tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier

from executive_copilot.admin.knowledge_auth import authorize_knowledge
from executive_copilot.services.knowledge.service import execute_knowledge


class WorkspaceTokenVerifier(TokenVerifier):
    """Authenticate all MCP HTTP tools using the configured workspace key."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            authorize_knowledge(token)
        except (ValueError, PermissionError):
            return None
        return AccessToken(token=token, client_id="workspace", scopes=["knowledge"])


def register_knowledge_tools(mcp: FastMCP, config: Any, credentials: Any) -> None:
    async def run(access_token: str, action: str, **kwargs: Any) -> dict[str, Any]:
        try:
            authorize_knowledge(access_token)
            result = await execute_knowledge(config, credentials, action, **kwargs)
            return result if isinstance(result, dict) else {"results": result}
        except PermissionError:
            return {"error": "Knowledge access denied", "code": "unauthorized"}
        except ValueError as exc:
            return {"error": str(exc), "code": "invalid_operation"}
        except Exception:
            return {
                "error": "Knowledge operation unavailable; pending changes preserved",
                "code": "unavailable",
            }

    @mcp.tool()
    async def knowledge_search(
        access_token: str,
        query: str = "",
        limit: int = 10,
        folder: str = "",
        tag: str = "",
        category: str = "",
    ) -> dict[str, Any]:
        """Textual knowledge search by content, title, tags and metadata; never semantic."""
        return await run(
            access_token,
            "search",
            query=query,
            limit=limit,
            folder=folder,
            tag=tag,
            category=category,
        )

    @mcp.tool()
    async def knowledge_get(
        access_token: str, path: str, revision: str | None = None
    ) -> dict[str, Any]:
        """Read a Markdown document and its revision hash, or a historical commit version."""
        return await run(
            access_token,
            "version" if revision else "get",
            path=path,
            **({"revision": revision} if revision else {}),
        )

    @mcp.tool()
    async def knowledge_create(access_token: str, path: str, content: str) -> dict[str, Any]:
        """Propose an Obsidian note. Show the diff and obtain user confirmation before knowledge_confirm."""
        return await run(access_token, "propose", operation="create", path=path, content=content)

    @mcp.tool()
    async def knowledge_update(
        access_token: str, path: str, content: str, expected_hash: str
    ) -> dict[str, Any]:
        """Propose a revision preserving unknown YAML; requires the hash from knowledge_get."""
        return await run(
            access_token,
            "propose",
            operation="update",
            path=path,
            content=content,
            expected_hash=expected_hash,
        )

    @mcp.tool()
    async def knowledge_delete(access_token: str, path: str, expected_hash: str) -> dict[str, Any]:
        """Propose deletion; original content remains recoverable. Requires explicit user confirmation."""
        return await run(
            access_token, "propose", operation="delete", path=path, expected_hash=expected_hash
        )

    @mcp.tool()
    async def knowledge_confirm(access_token: str, proposal_id: str) -> dict[str, Any]:
        """Apply an exact previously reviewed proposal only after explicit user approval."""
        return await run(access_token, "confirm", proposal_id=proposal_id)

    @mcp.tool()
    async def knowledge_related(access_token: str, path: str) -> dict[str, Any]:
        """Get backlinks, outgoing references, tags and unresolved relationships."""
        return await run(access_token, "related", path=path)

    @mcp.tool()
    async def knowledge_graph(
        access_token: str, path: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Bounded graph or document neighborhood, with explicit/inferred origins."""
        return await run(access_token, "graph", path=path, limit=limit)

    @mcp.tool()
    async def knowledge_history(access_token: str, path: str) -> dict[str, Any]:
        """List the latest thirty GitHub versions for an authorized Markdown path."""
        return await run(access_token, "history", path=path)

    @mcp.tool()
    async def knowledge_sync(access_token: str) -> dict[str, Any]:
        """Fetch remote notes and publish only confirmed changes; conflicts preserve both versions."""
        return await run(access_token, "sync")

    @mcp.tool()
    async def knowledge_sync_status(access_token: str) -> dict[str, Any]:
        """Get last commit, pending proposals, conflicts and synchronization errors."""
        return await run(access_token, "sync_status")

    @mcp.tool()
    async def knowledge_conflict(access_token: str, conflict_id: str) -> dict[str, Any]:
        """Inspect preserved base, local and remote versions of a conflict."""
        return await run(access_token, "conflict", conflict_id=conflict_id)

    @mcp.tool()
    async def knowledge_resolve(
        access_token: str, conflict_id: str, content: str | None, expected_hash: str | None = None
    ) -> dict[str, Any]:
        """Propose a conflict resolution for review; null content proposes deletion."""
        return await run(
            access_token,
            "resolve",
            conflict_id=conflict_id,
            content=content,
            expected_hash=expected_hash,
        )
