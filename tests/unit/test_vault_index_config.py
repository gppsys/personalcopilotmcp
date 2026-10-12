"""Tests for building the vault index from configuration."""

from pathlib import Path
from types import SimpleNamespace

from executive_copilot.services.vault_index import build_index


def _config(tmp_path: Path, **obsidian: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "enabled": True,
        "vault_path": str(tmp_path),
        "meetings_folder": "Paycode/Reuniones",
        "people_folder": "Paycode/Personas",
        "index_folders": [],
        "index_exclude": ["Paycode/SAFE/*"],
        "index_path": str(tmp_path / "index.db"),
        "transcripts_folder": "Transcripciones",
        "index_transcripts": False,
    }
    return SimpleNamespace(obsidian=SimpleNamespace(**(base | obsidian)), github_docs=None)


def test_build_index_excludes_transcripts_by_default(tmp_path: Path) -> None:
    index = build_index(_config(tmp_path))
    assert index.exclude == ["Paycode/SAFE/*", "Paycode/Reuniones/Transcripciones/*"]
    assert index.folders == ["Paycode"]


def test_build_index_can_index_transcripts(tmp_path: Path) -> None:
    assert build_index(_config(tmp_path, index_transcripts=True)).exclude == ["Paycode/SAFE/*"]
    assert build_index(_config(tmp_path, transcripts_folder=None)).exclude == ["Paycode/SAFE/*"]
