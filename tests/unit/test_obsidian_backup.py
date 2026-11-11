"""Tests for the Obsidian vault backup (real git against a local bare remote)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from executive_copilot.services.obsidian_backup import BackupError, ObsidianBackup, mirror_tree

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / "Paycode" / ".git").mkdir(parents=True)
    (vault / "Paycode" / ".git" / "HEAD").write_text("ref")
    (vault / "Paycode" / "nota.md").write_text("hola")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "app.json").write_text("{}")
    (vault / ".obsidian" / "workspace.json").write_text("{}")
    (vault / ".DS_Store").write_text("x")
    (vault / "raiz.md").write_text("raiz")
    return vault


def _remote_files(remote: Path) -> set[str]:
    out = subprocess.run(["git", "-C", str(remote), "ls-tree", "-r", "--name-only", "main"],
                         capture_output=True, text=True, check=True).stdout
    return set(out.split())


def test_mirror_tree_skips_excluded_and_deletes_removed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    target = tmp_path / "mirror"
    (target / ".git").mkdir(parents=True)
    (target / "viejo.md").write_text("old")

    copied = mirror_tree(vault, target, [".git", ".DS_Store", ".obsidian/workspace.json"])

    assert copied == 3
    assert (target / "Paycode" / "nota.md").exists()
    assert not (target / "Paycode" / ".git").exists()
    assert not (target / ".obsidian" / "workspace.json").exists()
    assert not (target / "viejo.md").exists()
    assert (target / ".git").exists()  # the mirror's own repo is kept
    assert mirror_tree(vault, target, [".git", ".DS_Store", ".obsidian/workspace.json"]) == 0


def test_backup_commits_and_pushes_only_changes(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    backup = ObsidianBackup(vault, tmp_path / "mirror", str(remote), author="tester")

    first = backup.run()
    assert first["commit"] and first["changed"] == 3
    assert _remote_files(remote) == {"Paycode/nota.md", ".obsidian/app.json", "raiz.md"}

    assert backup.run()["commit"] is None  # nothing changed

    (vault / "raiz.md").unlink()
    (vault / "nueva.md").write_text("nueva")
    os.utime(vault / "nueva.md")
    third = backup.run()
    assert third["changed"] == 2
    assert _remote_files(remote) == {"Paycode/nota.md", ".obsidian/app.json", "nueva.md"}


def test_backup_fails_on_unreadable_vault(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BackupError):
        ObsidianBackup(empty, tmp_path / "mirror", str(tmp_path / "r.git")).run()
