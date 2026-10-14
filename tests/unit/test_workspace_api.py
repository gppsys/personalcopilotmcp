"""Tests for the web workspace API and chat proposal handling."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from executive_copilot.admin.workspace_api import create_workspace_router
from executive_copilot.credentials import CredentialManager
from executive_copilot.services.chat import PROPOSALS, TOOLS, WorkspaceChat

NOTE = "# PCI\n\n## 2026-08-25\n- Auditoría de proveedores.\n"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    vault = tmp_path / "vault"
    meetings = vault / "Work" / "Meetings"
    meetings.mkdir(parents=True)
    (meetings / "Seguimiento PCI.md").write_text(NOTE, encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "obsidian:\n"
        f"  vault_path: {vault}\n"
        "  meetings_folder: Work/Meetings\n"
        "  people_folder: null\n"
        f"database:\n  name: {tmp_path / 'db.sqlite'}\n"
    )
    app = FastAPI()
    app.include_router(create_workspace_router(config_dir, CredentialManager(tmp_path / "creds")))
    return TestClient(app)


def test_notes_list_read_search(client: TestClient) -> None:
    notes = client.get("/api/ws/notes").json()["notes"]
    assert [n["name"] for n in notes] == ["Seguimiento PCI"]
    assert "Auditoría" in client.get("/api/ws/notes/Seguimiento PCI").json()["content"]
    hits = client.get("/api/ws/notes/search", params={"q": "auditoria"}).json()["results"]
    assert hits[0]["date"] == "2026-08-25"


def test_note_save_only_existing_names(client: TestClient, tmp_path: Path) -> None:
    ok = client.put("/api/ws/notes/Seguimiento PCI", json={"content": NOTE + "- extra\n"})
    assert ok.status_code == 200
    saved = (tmp_path / "vault" / "Work" / "Meetings" / "Seguimiento PCI.md").read_text(encoding="utf-8")
    assert saved.endswith("- extra\n")
    assert client.put("/api/ws/notes/..%2F..%2Fevil", json={"content": "x"}).status_code == 404
    assert client.put("/api/ws/notes/New note", json={"content": "x"}).status_code == 404
    assert not (tmp_path / "evil.md").exists()


def test_unknown_folder_is_rejected(client: TestClient) -> None:
    assert client.get("/api/ws/notes", params={"folder": "../../etc"}).status_code == 400
    assert client.get("/api/ws/notes", params={"folder": "people"}).status_code == 400


def test_complete_item_rejects_unknown_kind(client: TestClient) -> None:
    r = client.post("/api/ws/items/bogus/00000000-0000-0000-0000-000000000000/complete")
    assert r.status_code == 400


def test_every_proposal_tool_is_declared() -> None:
    names = {t["name"] for t in TOOLS}
    assert set(PROPOSALS) <= names
    for tool in TOOLS:
        params = tool["parameters"]
        assert set(params["required"]) == set(params["properties"])  # strict schema


@pytest.mark.asyncio
async def test_proposals_are_not_executed() -> None:
    chat = WorkspaceChat(workspace=None, ai_service=None)  # type: ignore[arg-type]
    steps: list[dict[str, str]] = []
    actions: list[dict[str, Any]] = []
    out = await chat._run_tool(
        "propose_create_task", {"title": "Revisar PCI", "notes": None, "due_date": "2026-10-09"}, steps, actions
    )
    assert "confirmation" in out
    assert actions == [{"type": "create_task", "title": "Revisar PCI", "due_date": "2026-10-09"}]
    assert steps == []


@pytest.mark.asyncio
async def test_complete_item_proposal_maps_to_card() -> None:
    chat = WorkspaceChat(workspace=None, ai_service=None)  # type: ignore[arg-type]
    actions: list[dict[str, Any]] = []
    await chat._run_tool(
        "propose_complete_item", {"kind": "delegation", "item_id": "6427e773", "title": "Educate Dan"}, [], actions
    )
    assert actions == [{"type": "complete_item", "kind": "delegation", "item_id": "6427e773", "title": "Educate Dan"}]


def test_completing_non_asana_id_is_rejected(client: TestClient) -> None:
    r = client.post("/api/ws/tasks/6427e773-77cb-4920-ba7d-81dea2e1ea5e/complete")
    assert r.status_code == 400 and "no es de una tarea de Asana" in r.json()["detail"]
