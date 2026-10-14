"""Offline knowledge integrity, review, graph and failure recovery tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from executive_copilot.config.models import KnowledgeConfig
from executive_copilot.services.knowledge.documents import digest, parse_document, validate_path
from executive_copilot.services.knowledge.repository import DOCUMENTS, KnowledgeRepository
from executive_copilot.services.knowledge.service import KnowledgeService

if TYPE_CHECKING:
    from pathlib import Path


class FakeRemote:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.commits = 0
        self.fail = False
        self.fail_after_publish = False
        self.private = True
        self.blob_reads = 0
        self.shas: dict[str, str] = {}

    async def snapshot(self, repository: str, branch: str) -> tuple[str, dict[str, Any]]:
        if self.fail:
            raise ConnectionError("network")
        if not self.private:
            raise ValueError("Knowledge repository must be private")
        # Like Git, the sha names the content, so a changed file gets a new sha.
        self.shas = {digest(p + "\0" + c)[:40]: p for p, c in self.files.items()}
        return str(self.commits), {
            p: {"sha": digest(p + "\0" + c)[:40], "mode": "100644", "size": len(c.encode())}
            for p, c in self.files.items()
        }

    async def blob(self, repository: str, sha: str, max_bytes: int) -> str:
        self.blob_reads += 1
        return self.files[self.shas[sha]]

    async def publish(
        self, repository: str, branch: str, head: str | None, changes: dict[str, str | None]
    ) -> str:
        for path, content in changes.items():
            if content is None:
                self.files.pop(path, None)
            else:
                self.files[path] = content
        self.commits += 1
        if self.fail_after_publish:
            self.fail_after_publish = False
            raise ConnectionError("lost response")
        return str(self.commits)


@pytest_asyncio.fixture
async def knowledge(tmp_path: Path) -> Any:
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    repository = KnowledgeRepository(f"sqlite+aiosqlite:///{tmp_path / 'index.db'}")
    await repository.initialize()
    remote = FakeRemote()
    service = KnowledgeService(
        KnowledgeConfig(sync_enabled=True, state_dir=tmp_path / "state"), vault, repository, remote
    )  # type: ignore[arg-type]
    yield service, remote
    await repository.close()


@pytest.mark.parametrize(
    "path",
    [
        "../a.md",
        "/a.md",
        "a/../../b.md",
        "a\\b.md",
        ".git/a.md",
        "a/.secrets.md",
        "a/./b.md",
        "a//b.md",
        "a.txt",
        "a.md\n",
        "C:/a.md",
    ],
)
def test_path_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        validate_path(path)


def test_lossless_frontmatter_and_relations() -> None:
    content = '---\r\nid: note-a\r\ntype: [reflection, hypothesis]\r\ntags: [ética]\r\ncustom: {nested: true}\r\nrelated: ["[[B]]"]\r\nrelations:\r\n - {target: "[[C]]", type: contradicts, origin: ai}\r\n---\r\n[[D#Section|label]]\r\n`[[ignored]]`\r\n'
    doc = parse_document("A.md", content)
    assert doc.content == content
    assert doc.metadata["custom"] == {"nested": True}
    assert doc.categories == ("reflection", "hypothesis")
    assert {(r.target, r.kind) for r in doc.relationships} == {
        ("B", "related_to"),
        ("C", "contradicts"),
        ("D", "references"),
    }
    assert next(r for r in doc.relationships if r.target == "C").explicit is False


@pytest.mark.parametrize(
    "content",
    [
        "---\nbad: [\n---\n",
        "---\n- list\n---\n",
        "---\nid: a",
        "---\nrelations: wrong\n---\n",
    ],
)
def test_invalid_frontmatter(content: str) -> None:
    with pytest.raises(ValueError):
        parse_document("A.md", content)


@pytest.mark.asyncio
async def test_create_requires_review_and_retry_is_idempotent(knowledge: Any) -> None:
    service, remote = knowledge
    proposal = await service.propose("create", "Ideas/A.md", "# Responsabilidad\nUna reflexión")
    assert await service.read("Ideas/A.md") is None
    await service.sync()
    assert not remote.files
    await service.confirm(proposal["id"])
    content = await service.read("Ideas/A.md")
    assert parse_document("Ideas/A.md", content).metadata["type"] == "reflection"
    assert not remote.files
    await service.sync()
    assert remote.files["Ideas/A.md"] == content
    await service.confirm(proposal["id"])
    await service.sync()
    assert remote.commits == 1


@pytest.mark.asyncio
async def test_stale_review_never_overwrites(knowledge: Any) -> None:
    service, _ = knowledge
    await service.write("A.md", "Original")
    proposal = await service.propose("update", "A.md", "Changed", digest("Original"))
    await service.write("A.md", "External edit")
    with pytest.raises(ValueError, match="revision changed"):
        await service.confirm(proposal["id"])
    assert await service.read("A.md") == "External edit"


@pytest.mark.asyncio
async def test_unreviewed_local_changes_are_not_published(knowledge: Any) -> None:
    service, remote = knowledge
    await service.write("A.md", "Local")
    result = await service.sync()
    assert result["unreviewed"] == ["A.md"]
    assert not remote.files


@pytest.mark.asyncio
async def test_conflict_preserves_versions_and_resolution_requires_review(knowledge: Any) -> None:
    service, remote = knowledge
    remote.files["A.md"] = "Original"
    await service.sync()
    proposal = await service.propose("update", "A.md", "Ours", digest("Original"))
    await service.confirm(proposal["id"])
    remote.files["A.md"] = "Theirs"
    result = await service.sync()
    conflict = result["conflicts"][0]
    saved = await service.conflict(conflict["id"])
    assert (saved["base"], saved["local"], saved["remote"]) == ("Original", "Ours", "Theirs")
    assert await service.read("A.md") == "Ours"
    assert remote.files["A.md"] == "Theirs"
    resolution = await service.resolve(conflict["id"], "Merged", digest("Ours"))
    assert await service.read("A.md") == "Ours"
    await service.confirm(resolution["id"])
    await service.sync()
    assert remote.files["A.md"] == "Merged"
    assert not (await service.sync_status())["conflicts"]


@pytest.mark.asyncio
async def test_network_failure_after_commit_recovers_without_duplicate(knowledge: Any) -> None:
    service, remote = knowledge
    proposal = await service.propose("create", "A.md", "Text")
    await service.confirm(proposal["id"])
    remote.fail_after_publish = True
    with pytest.raises(ConnectionError):
        await service.sync()
    assert (await service.sync_status())["error"]
    await service.sync()
    assert remote.commits == 1
    assert not (await service.sync_status())["pending"]


@pytest.mark.asyncio
async def test_rebuild_graph_and_search_preserve_operations(knowledge: Any) -> None:
    service, remote = knowledge
    remote.files = {
        "A.md": "---\nid: a\ntype: reflection\ntags: [ética]\n---\nResponsabilidad [[B]]",
        "B.md": "---\nid: b\ntype: concept\n---\nAutodominio",
    }
    await service.sync()
    await service.repository.put_state(
        "proposal:keep", {"status": "proposed", "id": "keep", "path": "X.md", "operation": "create"}
    )
    from sqlalchemy import delete

    async with service.repository.engine.begin() as conn:
        await conn.execute(delete(DOCUMENTS))
    await service.repository.put_state("index_fingerprint", None)
    await service.index()
    assert len(await service.repository.documents()) == 2
    assert await service.repository.get_state("proposal:keep")
    results = await service.search("Responsabilidad")
    assert results["mode"] == "textual" and results["results"][0]["path"] == "A.md"
    assert (await service.search(tag="ética"))["total"] == 1
    graph = await service.graph("B.md")
    assert any(e["source"] == "a" and e["target"] == "b" for e in graph["edges"])


def test_vault_conventions_parse_leniently() -> None:
    doc = parse_document(
        "A.md", "---\ntype: [personal, reflection]\ntags: [2026]\nrelated: [[B]]\n---\nText"
    )
    assert doc.categories == ("reflection",)
    assert doc.metadata["type"] == ["personal", "reflection"]
    assert doc.tags == ("2026",)
    assert [r.target for r in doc.relationships] == ["B"]
    assert parse_document("A.md", "---\n---\nBody").body == "Body"


@pytest.mark.asyncio
async def test_duplicate_ids_are_reported_and_first_stays_indexed(knowledge: Any) -> None:
    service, _ = knowledge
    await service.write("A.md", "---\nid: a\n---\nA")
    await service.index()
    await service.write("B.md", "---\nid: a\n---\nB")
    await service.index()
    assert [d["path"] for d in await service.repository.documents()] == ["A.md"]
    assert service.unavailable == {"B.md": "Duplicate document identity"}


@pytest.mark.asyncio
async def test_symlink_secret_and_public_repo_blocked(knowledge: Any, tmp_path: Path) -> None:
    service, remote = knowledge
    outside = tmp_path / "outside.md"
    outside.write_text("Private", encoding="utf-8")
    (service.vault / "link.md").symlink_to(outside)
    with pytest.raises(ValueError, match="Symbolic"):
        await service.read("link.md")
    with pytest.raises(ValueError, match="credentials"):
        await service.propose("create", "A.md", "token: ghp_" + "a" * 36)
    remote.private = False
    with pytest.raises(ValueError, match="private"):
        await service.sync()
    assert outside.read_text() == "Private"


@pytest.mark.asyncio
async def test_process_lock_excludes_second_service(knowledge: Any) -> None:
    service, _ = knowledge
    async with service.locked():
        with pytest.raises(ValueError, match="already running"):
            async with service.locked():
                pytest.fail("Concurrent lock acquired")


@pytest.mark.asyncio
async def test_delete_preserves_original_in_durable_proposal(knowledge: Any) -> None:
    service, remote = knowledge
    remote.files["A.md"] = "Keep original"
    await service.sync()
    proposal = await service.propose("delete", "A.md", expected_hash=digest("Keep original"))
    assert await service.read("A.md") == "Keep original"
    await service.confirm(proposal["id"])
    await service.sync()
    assert "A.md" not in remote.files
    assert (await service.repository.get_state("proposal:" + proposal["id"]))[
        "base"
    ] == "Keep original"


@pytest.mark.asyncio
async def test_crlf_unknown_metadata_and_bom_are_preserved(knowledge: Any) -> None:
    service, _ = knowledge
    content = "\ufeff---\r\ncustom: {value: 42}\r\n# preserved comment\r\n---\r\nOriginal\r\n"
    proposal = await service.propose("create", "A.md", content)
    await service.confirm(proposal["id"])
    saved = await service.read("A.md")
    assert saved.startswith("\ufeff---\r\n")
    assert "custom: {value: 42}\r\n# preserved comment\r\n" in saved
    updated = await service.propose(
        "update", "A.md", saved.replace("Original", "Extended"), digest(saved)
    )
    await service.confirm(updated["id"])
    assert await service.read("A.md") == saved.replace("Original", "Extended")


@pytest.mark.asyncio
async def test_reviewing_initial_vault_content_shows_publication_diff(knowledge: Any) -> None:
    service, _ = knowledge
    await service.write("A.md", "Existing personal note")
    proposal = await service.propose(
        "update", "A.md", "Existing personal note", digest("Existing personal note")
    )
    assert not proposal["diff"]
    assert "+Existing personal note" in proposal["publication_diff"]


@pytest.mark.asyncio
async def test_later_local_revert_is_not_overwritten_by_approval_recovery(knowledge: Any) -> None:
    service, remote = knowledge
    remote.files["A.md"] = "Original"
    await service.sync()
    proposal = await service.propose("update", "A.md", "Approved", digest("Original"))
    await service.confirm(proposal["id"])
    await service.write("A.md", "Original")
    await service.sync()
    assert await service.read("A.md") == "Original"
    assert remote.files["A.md"] == "Original"


@pytest.mark.asyncio
async def test_incremental_index_tracks_renames_and_removes_stale_edges(knowledge: Any) -> None:
    service, _ = knowledge
    content = "---\nid: stable\n---\n[[B]]"
    await service.write("A.md", content)
    await service.index()
    await service.write("A.md", None)
    await service.write("Renamed.md", content)
    await service.index()
    assert (await service.repository.documents())[0]["path"] == "Renamed.md"
    await service.write("Renamed.md", content.replace("[[B]]", "No link"))
    await service.index()
    assert not await service.repository.edges()


@pytest.mark.asyncio
async def test_body_only_update_preserves_unknown_yaml(knowledge: Any) -> None:
    service, _ = knowledge
    content = "---\nid: stable\ncustom: {value: 42} # original formatting\nsource: personal\n---\nOriginal"
    await service.write("A.md", content)
    proposal = await service.propose("update", "A.md", "Updated", digest(content))
    await service.confirm(proposal["id"])
    updated = await service.read("A.md")
    assert updated == content.replace("Original", "Updated")
    proposal = await service.propose(
        "update", "A.md", "---\nid: stable\ntitle: New title\n---\nExpanded", digest(updated)
    )
    await service.confirm(proposal["id"])
    saved = await service.read("A.md")
    assert "custom: {value: 42} # original formatting" in saved
    assert parse_document("A.md", saved).metadata["source"] == "personal"


@pytest.mark.asyncio
async def test_deleted_local_conflict_can_confirm_deletion(knowledge: Any) -> None:
    service, remote = knowledge
    remote.files["A.md"] = "Original"
    await service.sync()
    await service.write("A.md", None)
    remote.files["A.md"] = "Remote changed"
    conflict_id = (await service.sync())["conflicts"][0]["id"]
    proposal = await service.resolve(conflict_id, None, None)
    assert "-Remote changed" in proposal["publication_diff"]
    await service.confirm(proposal["id"])
    await service.sync()
    assert "A.md" not in remote.files


@pytest.mark.asyncio
async def test_graph_match_is_identified_as_graph_not_semantic(knowledge: Any) -> None:
    service, _ = knowledge
    await service.write("A.md", "Responsabilidad [[B]]")
    await service.write("B.md", "Concepto relacionado")
    await service.index()
    results = await service.search("Responsabilidad")
    neighbor = next(r for r in results["results"] if r["path"] == "B.md")
    assert neighbor["match_types"] == ["graph"]
    assert neighbor["relationships"]


@pytest.mark.asyncio
async def test_one_invalid_note_does_not_block_the_others(knowledge: Any) -> None:
    service, _ = knowledge
    await service.write("Bad.md", "---\nbad: [\n---\n")
    await service.write("Empty.md", "---\ntags:\n---\nResponsabilidad")
    await service.write("Good.md", "Responsabilidad")
    result = await service.index()
    assert result["documents"] == 2 and "Bad.md" in service.unavailable
    assert (await service.search("Responsabilidad"))["total"] == 2
    bad = await service.get("Bad.md")
    assert bad["error"] and bad["hash"] == digest("---\nbad: [\n---\n")
    proposal = await service.propose("update", "Good.md", "Mejor", digest("Responsabilidad"))
    await service.confirm(proposal["id"])
    assert await service.read("Good.md") == "Mejor"


@pytest.mark.asyncio
async def test_credential_notes_are_not_readable_or_synchronized(knowledge: Any) -> None:
    service, remote = knowledge
    secret = "token: ghp_" + "a" * 36
    await service.write("Keys.md", secret)
    with pytest.raises(ValueError, match="credentials"):
        await service.get("Keys.md")
    remote.files["Remote.md"] = secret
    result = await service.sync()
    assert {u["path"] for u in result["unavailable"]} == {"Keys.md", "Remote.md"}
    assert not (service.vault / "Remote.md").exists()
    assert "Keys.md" not in remote.files
    assert not await service.repository.documents()


@pytest.mark.asyncio
async def test_exclusions_ignore_case_unicode_form_and_match_at_root(knowledge: Any) -> None:
    service, _ = knowledge
    service.settings.exclude = ["Privado/*", "**/Transcripciones/**", "Reflexión/*"]
    assert not service.allowed("privado/banco.md")
    assert not service.allowed("Transcripciones/a.md")
    assert not service.allowed("Reuniones/Transcripciones/a.md")
    assert not service.allowed("Reflexio\u0301n/a.md")
    assert service.allowed("Notas/a.md")


@pytest.mark.asyncio
async def test_case_variant_remote_path_is_skipped_not_overwritten(knowledge: Any) -> None:
    service, remote = knowledge
    await service.write("Nota.md", "Local")
    remote.files["nota.md"] = "Remote"
    result = await service.sync()
    assert {u["path"] for u in result["unavailable"]} >= {"Nota.md", "nota.md"}
    assert await service.read("Nota.md") == "Local"


@pytest.mark.asyncio
async def test_changing_repository_or_emptied_remote_never_deletes_local_notes(
    knowledge: Any,
) -> None:
    service, remote = knowledge
    remote.files["A.md"] = "Synced"
    await service.sync()
    remote.files.clear()
    remote.commits = 0
    original_snapshot = remote.snapshot

    async def empty(repository: str, branch: str) -> tuple[None, dict[str, Any]]:
        return None, {}

    remote.snapshot = empty
    with pytest.raises(ValueError, match="empty"):
        await service.sync()
    remote.snapshot = original_snapshot
    service.settings.branch = "other"
    with pytest.raises(ValueError, match="changed since the last sync"):
        await service.sync()
    assert await service.read("A.md") == "Synced"


@pytest.mark.asyncio
async def test_new_conflict_after_resolution_is_not_auto_resolved(knowledge: Any) -> None:
    service, remote = knowledge
    remote.files["A.md"] = "Original"
    await service.sync()
    proposal = await service.propose("update", "A.md", "Ours", digest("Original"))
    await service.confirm(proposal["id"])
    remote.files["A.md"] = "Theirs"
    first = (await service.sync())["conflicts"][0]["id"]
    resolution = await service.resolve(first, "Merged", digest("Ours"))
    await service.confirm(resolution["id"])
    remote.files["A.md"] = "Theirs again"
    second = (await service.sync())["conflicts"]
    assert len(second) == 1 and second[0]["id"] != first
    await service.sync()
    assert remote.files["A.md"] == "Theirs again"
    assert (await service.sync_status())["conflicts"]
    saved = await service.repository.get_state("proposal:" + resolution["id"])
    assert saved["status"] == "superseded"


@pytest.mark.asyncio
async def test_confirm_rejects_remote_change_after_resolution_review(knowledge: Any) -> None:
    service, remote = knowledge
    remote.files["A.md"] = "Original"
    await service.sync()
    proposal = await service.propose("update", "A.md", "Ours", digest("Original"))
    await service.confirm(proposal["id"])
    remote.files["A.md"] = "Theirs"
    conflict_id = (await service.sync())["conflicts"][0]["id"]
    resolution = await service.resolve(conflict_id, "Merged", digest("Ours"))
    remote.files["A.md"] = "Changed after review"
    await service.sync()
    with pytest.raises(ValueError, match="Remote revision changed"):
        await service.confirm(resolution["id"])
    assert remote.files["A.md"] == "Changed after review"


@pytest.mark.asyncio
async def test_interrupted_confirm_is_finished_by_retry(knowledge: Any) -> None:
    service, _ = knowledge
    await service.write("A.md", "Original")
    proposal = await service.propose("update", "A.md", "Approved", digest("Original"))
    stored = await service.repository.get_state("proposal:" + proposal["id"])
    stored.update(status="approved", confirmed="2026-10-14T00:00:00+00:00")
    await service.repository.put_state("proposal:" + proposal["id"], stored)
    await service.confirm(proposal["id"])
    assert await service.read("A.md") == "Approved"
    assert (await service.repository.get_state("proposal:" + proposal["id"]))["applied"]


@pytest.mark.asyncio
async def test_unchanged_remote_blobs_are_not_downloaded_again(knowledge: Any) -> None:
    service, remote = knowledge
    remote.files = {f"N{i}.md": f"Nota {i}" for i in range(5)}
    await service.sync()
    assert remote.blob_reads == 5
    await service.sync()
    assert remote.blob_reads == 5
    remote.files["N0.md"] = "Cambiada"
    await service.sync()
    assert remote.blob_reads == 6
    assert await service.read("N0.md") == "Cambiada"


@pytest.mark.asyncio
async def test_sync_after_failure_reports_no_stale_error(knowledge: Any) -> None:
    service, remote = knowledge
    remote.fail = True
    with pytest.raises(ConnectionError):
        await service.sync()
    remote.fail = False
    assert (await service.sync())["error"] is None


@pytest.mark.asyncio
async def test_dashboard_connectivity_is_not_capped_by_graph_limit(knowledge: Any) -> None:
    service, _ = knowledge
    for i in range(160):
        await service.write(f"N{i:03}.md", f"[[N{(i + 1) % 160:03}]]")
    await service.index()
    assert not (await service.dashboard())["isolated"]


@pytest.mark.asyncio
async def test_reads_skip_the_vault_when_no_file_changed(
    knowledge: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = knowledge
    for i in range(3):
        await service.write(f"N{i}.md", f"Nota {i}")
    await service.index()
    await service.sync_status()
    reads = []
    original = service.read

    async def counting(path: str) -> str | None:
        reads.append(path)
        return await original(path)

    monkeypatch.setattr(service, "read", counting)
    await service.index()
    await service.sync_status()
    assert reads == []
    await service.write("N1.md", "Nota cambiada")
    # Status checks re-read only the changed note; the index re-reads once to rebuild.
    assert "N1.md" in (await service.sync_status())["unreviewed"]
    assert reads == ["N1.md"]
    assert (await service.index())["documents"] == 3
    assert (await service.search("cambiada"))["total"] == 1


@pytest.mark.asyncio
async def test_sync_writes_only_changed_baseline_rows(
    knowledge: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, remote = knowledge
    remote.files = {f"N{i}.md": f"Nota {i}" for i in range(4)}
    await service.sync()
    written: list[set[str]] = []
    original = service.repository.put_manifest

    async def recording(rows: dict[str, Any]) -> None:
        written.append(set(rows))
        await original(rows)

    monkeypatch.setattr(service.repository, "put_manifest", recording)
    await service.sync()
    remote.files["N2.md"] = "Cambiada"
    await service.sync()
    assert [w for w in written if w] == [{"N2.md"}]
    assert await service.repository.manifest_content("N2.md") == "Cambiada"


@pytest.mark.asyncio
async def test_legacy_json_baseline_moves_to_rows(tmp_path: Path) -> None:
    repository = KnowledgeRepository(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    await repository.initialize()
    await repository.put_state("manifest", {"A.md": "Synced", "Gone.md": None})
    repository._initialized = False
    await repository.initialize()
    assert await repository.manifest() == {"A.md": "Synced", "Gone.md": None}
    assert await repository.get_state("manifest") is None
    await repository.close()


def test_state_dir_is_anchored_to_the_config_root(tmp_path: Path) -> None:
    from executive_copilot.config import load_config

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("knowledge:\n  state_dir: knowledge_state\n")
    config = load_config(tmp_path / "config")
    assert config.knowledge.state_dir == tmp_path.resolve() / "knowledge_state"
