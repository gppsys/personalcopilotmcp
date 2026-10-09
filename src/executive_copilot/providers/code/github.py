"""GitHub provider: repositories, file trees and file contents (read-only REST API)."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from executive_copilot.logging import get_logger

if TYPE_CHECKING:
    from executive_copilot.admin.credentials import CredentialManager

logger = get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"


@dataclass(frozen=True)
class RepoInfo:
    """A repository as listed by the organization."""

    owner: str
    name: str
    description: str | None
    default_branch: str | None
    archived: bool
    private: bool
    pushed_at: datetime | None
    language: str | None
    topics: tuple[str, ...]
    url: str
    size_kb: int

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class TreeEntry:
    """A file in a repository tree."""

    path: str
    sha: str
    size: int


class GitHubProvider:
    """GitHub REST client authenticated with a (fine-grained) personal access token."""

    def __init__(self, credential_manager: CredentialManager, account_id: str = "default") -> None:
        self.credential_manager = credential_manager
        self.account_id = account_id

    def _token(self) -> str:
        cred = self.credential_manager.get_credential("code", "github", self.account_id) or {}
        token = cred.get("access_token")
        if not token:
            raise ValueError("No GitHub token: add one in Cuentas (fine-grained, read-only Contents + Metadata).")
        return str(token)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(10.0, read=60.0),
        )

    async def get_user(self) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get("/user")
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data

    async def list_repos(self, org: str) -> list[RepoInfo]:
        """All repositories of an organization the token can see."""
        repos: list[RepoInfo] = []
        async with self._client() as client:
            page = 1
            while True:
                response = await client.get(
                    f"/orgs/{org}/repos", params={"per_page": 100, "page": page, "type": "all"}
                )
                response.raise_for_status()
                batch = response.json()
                repos.extend(_repo(item) for item in batch)
                if len(batch) < 100:
                    return repos
                page += 1

    async def get_languages(self, repo: RepoInfo) -> dict[str, int]:
        """Bytes of code per language."""
        async with self._client() as client:
            response = await client.get(f"/repos/{repo.full_name}/languages")
            response.raise_for_status()
            data: dict[str, int] = response.json()
            return data

    async def get_tree(self, repo: RepoInfo) -> list[TreeEntry]:
        """Every file of the default branch; empty for an empty repository."""
        if not repo.default_branch:
            return []
        async with self._client() as client:
            response = await client.get(
                f"/repos/{repo.full_name}/git/trees/{repo.default_branch}", params={"recursive": "1"}
            )
            if response.status_code in (404, 409):  # empty repository / branch gone
                return []
            response.raise_for_status()
            data = response.json()
        if data.get("truncated"):
            logger.warning("GitHub tree truncated", repo=repo.full_name)
        return [
            TreeEntry(path=item["path"], sha=item["sha"], size=int(item.get("size") or 0))
            for item in data.get("tree", [])
            if item.get("type") == "blob"
        ]

    async def get_text(self, repo: RepoInfo, sha: str) -> str:
        """Contents of one file (by blob sha) as text."""
        async with self._client() as client:
            response = await client.get(f"/repos/{repo.full_name}/git/blobs/{sha}")
            response.raise_for_status()
            data = response.json()
        raw = base64.b64decode(data.get("content") or "") if data.get("encoding") == "base64" else (data.get("content") or "").encode()
        return raw.decode("utf-8", errors="replace")


def _repo(item: dict[str, Any]) -> RepoInfo:
    pushed = item.get("pushed_at")
    return RepoInfo(
        owner=item["owner"]["login"],
        name=item["name"],
        description=item.get("description"),
        default_branch=item.get("default_branch"),
        archived=bool(item.get("archived")),
        private=bool(item.get("private")),
        pushed_at=datetime.fromisoformat(pushed.replace("Z", "+00:00")) if pushed else None,
        language=item.get("language"),
        topics=tuple(item.get("topics") or ()),
        url=item.get("html_url") or f"https://github.com/{item['full_name']}",
        size_kb=int(item.get("size") or 0),
    )
