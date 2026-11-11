"""Back up the Obsidian vault to a private Git repository.

The vault is mirrored into a separate working copy (nested ``.git`` folders,
OS junk and volatile UI state left out) so the vault itself never gets a
``.git`` folder; the mirror is then committed and pushed. Pushing every run
also retries a commit that a previous run could not push.
"""

import base64
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from executive_copilot.logging import get_logger

logger = get_logger(__name__)

# Matched against path parts (any depth) or, with a "/", against the vault-relative path.
DEFAULT_EXCLUDE = [
    ".git",
    ".DS_Store",
    ".trash",
    ".copilot",
    ".obsidian/workspace.json",
    ".obsidian/workspace-mobile.json",
]


class BackupError(RuntimeError):
    """The backup could not be completed."""


def _excluded(rel: Path, exclude: list[str]) -> bool:
    rel_str = rel.as_posix()
    for pattern in exclude:
        if "/" in pattern:
            if rel_str == pattern or rel_str.startswith(pattern.rstrip("/") + "/"):
                return True
        elif pattern in rel.parts:
            return True
    return False


def mirror_tree(source: Path, target: Path, exclude: list[str]) -> int:
    """Make ``target`` match ``source`` (except ``target/.git``). Returns files copied."""
    copied = 0
    wanted: set[Path] = set()
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if _excluded(rel, exclude) or path.is_symlink():
            continue
        wanted.add(rel)
        dest = target / rel
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        st = path.stat()
        if dest.exists():
            dt = dest.stat()
            if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
                continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        copied += 1

    # Remove what disappeared from the vault, deepest first; never touch the mirror's .git.
    for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(target)
        if rel.parts[0] == ".git" or rel in wanted:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    return copied


class ObsidianBackup:
    """Mirror the vault into ``mirror_dir`` and push it to ``remote``."""

    def __init__(
        self,
        vault: Path,
        mirror_dir: Path,
        remote: str,
        token: str | None = None,
        exclude: list[str] | None = None,
        author: str = "copilot",
    ) -> None:
        self.vault = vault
        self.mirror = mirror_dir
        self.remote = remote
        self.token = token
        self.exclude = DEFAULT_EXCLUDE + list(exclude or [])
        self.author = author

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = ["git", "-c", "safe.directory=*", "-C", str(self.mirror)]
        if self.token:
            basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
            cmd += ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        proc = subprocess.run([*cmd, *args], capture_output=True, text=True, env=env, timeout=600,
                              check=False)
        if check and proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            if self.token:
                err = err.replace(self.token, "***")
            raise BackupError(f"git {args[0]} failed: {err[:400]}")
        return proc

    def run(self) -> dict[str, Any]:
        if not self.vault.is_dir() or not any(self.vault.iterdir()):
            raise BackupError(f"Vault not found or unreadable: {self.vault}")
        if shutil.which("git") is None:
            raise BackupError("git is not installed")

        self.mirror.mkdir(parents=True, exist_ok=True)
        if not (self.mirror / ".git").exists():
            self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", self.author)
        self._git("config", "user.email", f"{self.author}@users.noreply.github.com")

        copied = mirror_tree(self.vault, self.mirror, self.exclude)
        self._git("add", "-A")
        changed = [line for line in self._git("diff", "--cached", "--name-only").stdout.splitlines() if line]
        commit = None
        if changed:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            self._git("commit", "-q", "-m", f"Backup {stamp} ({len(changed)} files)")
            commit = self._git("rev-parse", "--short", "HEAD").stdout.strip()

        self._git("push", "-q", self.remote, "HEAD:main")
        files = len(self._git("ls-files").stdout.splitlines())
        logger.info("Vault backed up", changed=len(changed), commit=commit, files=files)
        return {"changed": len(changed), "copied": copied, "commit": commit, "files": files,
                "remote": self.remote.split("@")[-1]}
