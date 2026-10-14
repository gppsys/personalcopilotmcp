"""Private note repository transport with optimistic Git reference updates."""

from __future__ import annotations

import base64
import os
import re
from typing import Any
from urllib.parse import quote

from executive_copilot.providers.code.github import GitHubProvider


class UnsupportedDocument(ValueError):
    """A remote file that cannot be synchronized (encoding, size); other paths continue."""


class KnowledgeGitHub(GitHubProvider):
    def _token(self) -> str:
        token = os.environ.get("COPILOT_KNOWLEDGE_GITHUB_TOKEN")
        if token:
            return token
        try:
            return super()._token()
        except ValueError:
            raise ValueError(
                "Configure a dedicated GitHub knowledge token with Contents read/write"
            ) from None

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with self._client() as client:
            response = await client.request(method, path, **kwargs)
            if response.status_code >= 400:
                # GitHub error bodies can echo submitted document content.
                raise ValueError(f"GitHub request failed ({response.status_code})")
            return response.json()

    async def verify_private(self, repository: str) -> None:
        data = await self.request("GET", f"/repos/{repository}")
        # "internal" repositories are also flagged private but are visible org-wide.
        visibility = data.get("visibility", "private" if data.get("private") else "public")
        if not data.get("private") or visibility != "private" or data.get("archived"):
            raise ValueError("Knowledge repository must be private and writable")

    async def snapshot(
        self, repository: str, branch: str
    ) -> tuple[str | None, dict[str, dict[str, Any]]]:
        await self.verify_private(repository)
        async with self._client() as client:
            response = await client.get(
                f"/repos/{repository}/git/ref/heads/{quote(branch, safe='/')}"
            )
            if response.status_code == 409:
                return None, {}
            if response.status_code >= 400:
                raise ValueError(f"GitHub branch unavailable ({response.status_code})")
            head = str(response.json()["object"]["sha"])
        commit = await self.request("GET", f"/repos/{repository}/git/commits/{head}")
        tree = await self.request(
            "GET",
            f"/repos/{repository}/git/trees/{commit['tree']['sha']}",
            params={"recursive": "1"},
        )
        if tree.get("truncated"):
            raise ValueError("Incomplete GitHub tree; synchronization stopped")
        return head, {str(i["path"]): i for i in tree["tree"] if i["type"] == "blob"}

    async def blob(self, repository: str, sha: str, max_bytes: int) -> str:
        data = await self.request("GET", f"/repos/{repository}/git/blobs/{sha}")
        if data.get("encoding") != "base64" or int(data.get("size", 0)) > max_bytes:
            raise UnsupportedDocument("Unsupported or oversized GitHub document")
        try:
            return base64.b64decode(data["content"], validate=False).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise UnsupportedDocument("Invalid UTF-8 GitHub document") from exc

    async def publish(
        self,
        repository: str,
        branch: str,
        head: str | None,
        changes: dict[str, str | None],
    ) -> str:
        await self.verify_private(repository)
        if not changes:
            raise ValueError("No confirmed changes to publish")
        if head is None:
            # GitHub's Git Data API cannot create trees in an empty repository.
            path = next((p for p, content in changes.items() if content is not None), None)
            if path is None:
                raise ValueError("Cannot initialize a repository with deletions")
            data = await self.request(
                "PUT",
                f"/repos/{repository}/contents/{quote(path, safe='/')}",
                json={
                    "message": "knowledge: initialize reviewed notes",
                    "branch": branch,
                    "content": base64.b64encode(str(changes[path]).encode()).decode(),
                },
            )
            head = data["commit"]["sha"]
            changes = {p: c for p, c in changes.items() if p != path}
            if not changes:
                return str(head)
        commit = await self.request("GET", f"/repos/{repository}/git/commits/{head}")
        entries = []
        for path, content in changes.items():
            sha = None
            if content is not None:
                blob = await self.request(
                    "POST",
                    f"/repos/{repository}/git/blobs",
                    json={"content": content, "encoding": "utf-8"},
                )
                sha = blob["sha"]
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
        tree = await self.request(
            "POST",
            f"/repos/{repository}/git/trees",
            json={"base_tree": commit["tree"]["sha"], "tree": entries},
        )
        created = await self.request(
            "POST",
            f"/repos/{repository}/git/commits",
            json={
                "message": f"knowledge: publish {len(changes)} reviewed note changes",
                "tree": tree["sha"],
                "parents": [head],
            },
        )
        await self.request(
            "PATCH",
            f"/repos/{repository}/git/refs/heads/{quote(branch, safe='/')}",
            json={"sha": created["sha"], "force": False},
        )
        return str(created["sha"])

    async def history(self, repository: str, branch: str, path: str) -> list[dict[str, Any]]:
        data = await self.request(
            "GET",
            f"/repos/{repository}/commits",
            params={"sha": branch, "path": path, "per_page": 30},
        )
        return [
            {
                "sha": c["sha"],
                "date": c["commit"]["committer"]["date"],
                "message": c["commit"]["message"],
                "url": c["html_url"],
            }
            for c in data
        ]

    async def version(self, repository: str, path: str, revision: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Expected a complete commit SHA")
        await self.verify_private(repository)
        data = await self.request(
            "GET", f"/repos/{repository}/contents/{quote(path, safe='/')}", params={"ref": revision}
        )
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            raise ValueError("Unsupported historical document")
        return base64.b64decode(data["content"]).decode("utf-8")
