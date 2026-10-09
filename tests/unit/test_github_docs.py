"""Tests for the GitHub docs mirror and its indexing with the vault."""

import base64
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest

from executive_copilot.providers.code import github as github_provider
from executive_copilot.providers.code.github import GitHubProvider, RepoInfo, TreeEntry
from executive_copilot.services import github_docs
from executive_copilot.services.github_docs import (
    GitHubDocsSync,
    clean_summary,
    is_doc_path,
    local_name,
    render_card,
    summary_context,
)
from executive_copilot.services.vault_index import GITHUB_CARD, VaultEditError, VaultIndex


def _repo(name: str, pushed: str = "2026-10-01T00:00:00+00:00", **kw: Any) -> RepoInfo:
    return RepoInfo(
        owner="Acme", name=name, description=kw.get("description", f"{name} service"),
        default_branch="main", archived=kw.get("archived", False), private=True,
        pushed_at=datetime.fromisoformat(pushed), language="Rust", topics=(),
        url=f"https://github.com/Acme/{name}", size_kb=10,
    )


def test_is_doc_path() -> None:
    assert is_doc_path("README.md", 100, 300)
    assert is_doc_path("docs/api/endpoints.mdx", 100, 300)
    assert not is_doc_path("src/main.rs", 100, 300)
    assert not is_doc_path("public/assets/plugins/jsgrid/README.md", 100, 300)
    assert not is_doc_path("node_modules/x/README.md", 100, 300)
    assert not is_doc_path(".github/PULL_REQUEST_TEMPLATE.md", 100, 300)
    assert not is_doc_path("CHANGELOG.md", 100, 300)
    assert not is_doc_path("docs/huge.md", 400 * 1024, 300)
    assert not is_doc_path("docs/generated/x.md", 10, 300, ["docs/generated/*"])


def test_local_name_and_card() -> None:
    assert local_name("docs/intro.mdx") == "docs/intro.md"
    card = render_card(_repo("pay"), {"Rust": 900, "Shell": 100}, ["README.md"], "**Qué hace:** cobra.", ["src/", "README.md"])
    assert card.startswith("# Repositorio Acme/pay\n")
    assert "- Lenguajes: Rust 90%, Shell 10%" in card
    assert "## Resumen\n**Qué hace:** cobra." in card
    assert card.rstrip().endswith("- README.md")


class FakeGitHub:
    """Stands in for GitHubProvider inside GitHubDocsSync."""

    repos: ClassVar[list[RepoInfo]] = []
    trees: ClassVar[dict[str, list[TreeEntry]]] = {}
    blobs: ClassVar[dict[str, str]] = {}
    downloads: ClassVar[list[str]] = []

    def __init__(self, *args: Any) -> None:
        pass

    async def list_repos(self, org: str) -> list[RepoInfo]:
        return self.repos

    async def get_tree(self, repo: RepoInfo) -> list[TreeEntry]:
        return self.trees[repo.name]

    async def get_text(self, repo: RepoInfo, sha: str) -> str:
        self.downloads.append(sha)
        return self.blobs[sha]

    async def get_languages(self, repo: RepoInfo) -> dict[str, int]:
        return {"Rust": 1}


class FakeAI:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_text(self, request: Any) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(text="```markdown\n**Qué hace:** procesa pagos.\n```")


def _config(tmp_path: Path, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(github_docs=SimpleNamespace(
        enabled=True, orgs=["Acme"], account="default", include_repos=[], exclude_repos=kw.get("exclude", []),
        include_archived=False, exclude_paths=[], max_file_kb=300, cache_dir=str(tmp_path / "mirror"), summarize=True,
    ))


@pytest.mark.asyncio
async def test_sync_is_incremental(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_docs, "GitHubProvider", FakeGitHub)
    FakeGitHub.repos = [_repo("pay"), _repo("old", archived=True)]
    FakeGitHub.trees = {"pay": [
        TreeEntry("README.md", "a1", 10), TreeEntry("docs/flow.mdx", "b1", 10), TreeEntry("src/main.rs", "c1", 10),
    ]}
    FakeGitHub.blobs = {"a1": "# Pay\nCobros.", "b1": "# Flujo\nPasos.", "a2": "# Pay\nCobros v2."}
    FakeGitHub.downloads = []
    ai = FakeAI()
    sync = GitHubDocsSync(_config(tmp_path), credential_manager=None, ai_service=ai)

    stats = await sync.sync()
    base = tmp_path / "mirror" / "Acme" / "pay"
    assert stats["repos_seen"] == 1 and stats["files_written"] == 2 and stats["summaries"] == 1
    assert (base / "docs" / "flow.md").read_text() == "# Flujo\nPasos."
    card = (base / GITHUB_CARD).read_text()
    assert "**Qué hace:** procesa pagos." in card and "```" not in card

    # Nothing pushed: no tree read, no downloads, no new summary.
    stats = await sync.sync()
    assert stats["repos_changed"] == 0 and ai.calls == 1

    # New push: README changed, docs/flow removed -> one download, one delete, new summary.
    FakeGitHub.repos = [_repo("pay", pushed="2026-10-05T00:00:00+00:00")]
    FakeGitHub.trees["pay"] = [TreeEntry("README.md", "a2", 10)]
    FakeGitHub.downloads = []
    stats = await sync.sync()
    assert FakeGitHub.downloads == ["a2"] and stats["files_removed"] == 1 and ai.calls == 2
    assert not (base / "docs" / "flow.md").exists()
    assert sync.status()["repos"] == 1 and sync.status()["files"] == 1

    # Excluded later: its mirror goes away.
    sync = GitHubDocsSync(_config(tmp_path, exclude=["pay"]), credential_manager=None)
    stats = await sync.sync()
    assert stats["repos_removed"] == 1 and not base.exists()


@pytest.mark.asyncio
async def test_sync_reports_listing_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Denied(FakeGitHub):
        async def list_repos(self, org: str) -> list[RepoInfo]:
            raise ValueError("401 Bad credentials")

    monkeypatch.setattr(github_docs, "GitHubProvider", Denied)
    result = await GitHubDocsSync(_config(tmp_path), credential_manager=None).sync()
    assert "401" in result["error"]
    assert "401" in GitHubDocsSync(_config(tmp_path), credential_manager=None).status()["last_error"]


def test_vault_index_with_github_root(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "Paycode" / "Reuniones").mkdir(parents=True)
    (vault / "Paycode" / "Reuniones" / "Roadmap.md").write_text("# Roadmap\n\n## 2026-10-01\n- Prioridad: tokenización.\n")
    mirror = tmp_path / "mirror" / "Acme" / "pay"
    (mirror / "docs").mkdir(parents=True)
    (mirror / "docs" / "roadmap.md").write_text("# Roadmap técnico\n\n## Fases\nMigrar el vault de llaves a HSM.\n")
    (mirror / GITHUB_CARD).write_text("# Repositorio Acme/pay\n\n## Resumen\nProcesa pagos con tarjeta.\n")

    index = VaultIndex(vault, ["Paycode"], tmp_path / "index.db", extra_roots={"GitHub": tmp_path / "mirror"})
    index.refresh(force=True)

    hits = index.search("vault llaves HSM")
    assert hits and hits[0]["note"] == "GitHub/Acme/pay/docs/roadmap.md"
    assert index.read("Roadmap")["note"] == "Paycode/Reuniones/Roadmap.md"  # vault wins name lookups
    assert index.read("GitHub/Acme/pay/docs/roadmap.md")["title"] == "Roadmap técnico"
    listed = index.overview("GitHub")
    assert [n["note"] for n in listed["notes"]] == [f"GitHub/Acme/pay/{GITHUB_CARD}"]
    with pytest.raises(VaultEditError, match="read-only"):
        index.edit("GitHub/Acme/pay/docs/roadmap.md", "append", text="x")


@pytest.mark.asyncio
async def test_provider_paginates_and_decodes(monkeypatch: pytest.MonkeyPatch) -> None:
    def repo_json(i: int) -> dict[str, Any]:
        return {"owner": {"login": "Acme"}, "name": f"r{i}", "full_name": f"Acme/r{i}", "default_branch": "main",
                "pushed_at": "2026-10-01T00:00:00Z", "archived": False, "private": True}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/orgs/Acme/repos":
            page = int(request.url.params["page"])
            return httpx.Response(200, json=[repo_json(i) for i in range(100 if page == 1 else 3)])
        if path.endswith("/git/trees/main"):
            return httpx.Response(409, json={"message": "Git Repository is empty."})
        if "/git/blobs/" in path:
            return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode("# Hola ñ".encode()).decode()})
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(github_provider.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    creds = SimpleNamespace(get_credential=lambda *_args: {"access_token": "t"})
    provider = GitHubProvider(creds)  # type: ignore[arg-type]

    repos = await provider.list_repos("Acme")
    assert len(repos) == 103 and repos[0].pushed_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert await provider.get_tree(repos[0]) == []
    assert await provider.get_text(repos[0], "abc") == "# Hola ñ"


def test_clean_summary_keeps_card_structure() -> None:
    raw = "```markdown\n# Repositorio \"pay\"\n\n## Qué hace:\nCobra.\n\n### **Integraciones**\n- Banorte\n```"
    assert clean_summary(raw) == "**Qué hace:**\nCobra.\n\n**Integraciones:**\n- Banorte"


def test_summary_context_includes_doc_outline() -> None:
    docs = {"README.md": "# Pay\nIntro", "docs/bancos.md": "# Bancos\n## Banorte\n## STP\ntexto"}
    context = summary_context(_repo("pay"), {"Rust": 1}, ["src/"], docs)
    assert "- docs/bancos.md: Bancos · Banorte · STP" in context
    assert "--- README.md ---\n# Pay\nIntro" in context


def test_summary_context_without_readme_uses_main_top_level_doc() -> None:
    docs = {"AGENTS.md": "# Agents", "PROJECT.md": "# Tokeniza y Cobra\n" + "x" * 500, "docs/a.md": "# A"}
    context = summary_context(_repo("pay"), {}, [], docs)
    assert "--- PROJECT.md ---\n# Tokeniza y Cobra" in context


@pytest.mark.asyncio
async def test_search_collapses_copies_of_the_same_section(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "Paycode" / "SAFE").mkdir(parents=True)
    doc = "# Despliegue en AWS\n\n## Desplegar\nterraform apply en la cuenta de staging.\n"
    (vault / "Paycode" / "SAFE" / "aws.md").write_text(doc)
    (tmp_path / "mirror" / "Acme" / "safe" / "docs").mkdir(parents=True)
    (tmp_path / "mirror" / "Acme" / "safe" / "docs" / "aws.md").write_text(doc)
    index = VaultIndex(vault, ["Paycode"], tmp_path / "index.db", extra_roots={"GitHub": tmp_path / "mirror"})

    for hits in (index.search("terraform staging"), await index.hybrid_search("terraform staging", None)):
        assert len(hits) == 1
        paths = {hits[0]["note"], *hits[0]["also_in"]}
        assert paths == {"Paycode/SAFE/aws.md", "GitHub/Acme/safe/docs/aws.md"}
