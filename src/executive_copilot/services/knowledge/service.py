"""Review-first knowledge operations and conflict-preserving synchronization."""

from __future__ import annotations

import asyncio
import difflib
import fcntl
import json
import os
import re
import unicodedata
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from executive_copilot.logging import get_logger
from executive_copilot.providers.code.knowledge_github import KnowledgeGitHub, UnsupportedDocument
from executive_copilot.services.knowledge.documents import (
    Document,
    digest,
    parse_document,
    preserve_metadata,
    strings,
    validate_path,
)
from executive_copilot.services.knowledge.locking import KnowledgeBusy
from executive_copilot.services.knowledge.repository import KnowledgeRepository
from executive_copilot.services.vault_index import best_snippet, redact

if TYPE_CHECKING:
    from executive_copilot.config.models import KnowledgeConfig

logger = get_logger(__name__)

# Only these touch the vault or the remote; reads run without the shared lock.
WRITE_ACTIONS = {"confirm", "sync"}


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest_or_none(content: str | None) -> str | None:
    return digest(content) if content is not None else None


def fold(path: str) -> str:
    """Comparison key for exclusions and collisions on case-insensitive, NFD file systems."""
    return unicodedata.normalize("NFC", path).casefold()


def sensitive(content: str) -> bool:
    return redact(content) != content


class KnowledgeService:
    def __init__(
        self,
        settings: KnowledgeConfig,
        vault: Path,
        repository: KnowledgeRepository,
        remote: KnowledgeGitHub,
    ) -> None:
        self.settings = settings
        self.vault = vault.resolve()
        self.repository = repository
        self.remote = remote
        # Paths left out of the last inventory, with the reason; reported, never synchronized.
        self.unavailable: dict[str, str] = {}

    @asynccontextmanager
    async def locked(self) -> Any:
        # Nonblocking, so it runs inline: a cancelled thread could acquire and leak the lock.
        directory = self.settings.state_dir
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(directory / "knowledge.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise KnowledgeBusy("Knowledge operation already running; retry later") from None
        try:
            yield
        finally:
            os.close(fd)

    def allowed(self, path: str) -> bool:
        try:
            validate_path(path)
        except ValueError:
            return False
        forbidden = {"node_modules", "credentials", "secrets", "vault_backups", "knowledge_state"}
        key = fold(path)
        if any(part in forbidden for part in Path(key).parts):
            return False
        # fnmatch's * crosses "/", so a leading "**/" also matches at the vault root.
        return not any(
            fnmatchcase(key, p) or fnmatchcase("/" + key, p)
            for p in (fold(pattern) for pattern in self.settings.exclude)
        )

    def path(self, path: str) -> Path:
        if not self.allowed(path):
            raise ValueError("Document path is not authorized")
        target = self.vault / path
        for part in [target, *target.parents]:
            if part == self.vault:
                break
            if part.is_symlink():
                raise ValueError("Symbolic links are not authorized")
        if not target.resolve().is_relative_to(self.vault):
            raise ValueError("Document path escapes the vault")
        return target

    async def read(self, path: str) -> str | None:
        target = self.path(path)

        def load() -> str | None:
            if not target.exists():
                return None
            if not target.is_file() or target.stat().st_size > self.settings.max_file_bytes:
                raise ValueError("Unsupported or oversized document")
            try:
                with target.open("r", encoding="utf-8", newline="") as stream:
                    return stream.read()
            except UnicodeDecodeError:
                raise ValueError("Document is not valid UTF-8") from None

        content = await asyncio.to_thread(load)
        if content is not None and sensitive(content):
            raise ValueError("Document appears to contain credentials; it is not available")
        return content

    async def write(self, path: str, content: str | None) -> None:
        target = self.path(path)

        def save() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                target.unlink(missing_ok=True)
                return
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                with temporary.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(save)

    async def inventory(self) -> dict[str, tuple[int, int]]:
        """Allowed paths with (mtime_ns, size); cheap enough to run on every request."""

        def scan() -> dict[str, tuple[int, int]]:
            if not self.vault.is_dir():
                raise ValueError("Knowledge vault is unavailable")
            found = {}
            for directory, folders, names in os.walk(self.vault, followlinks=False):
                folders[:] = [
                    f
                    for f in folders
                    if not f.startswith(".") and not (Path(directory) / f).is_symlink()
                ]
                for name in names:
                    full = Path(directory) / name
                    rel = full.relative_to(self.vault).as_posix()
                    if self.allowed(rel):
                        try:
                            stat = full.lstat()
                        except OSError:
                            continue
                        found[rel] = (stat.st_mtime_ns, stat.st_size)
            return dict(sorted(found.items()))

        return await asyncio.to_thread(scan)

    async def local_digests(self) -> dict[str, str]:
        """Content digest per readable local note, re-reading only files whose stat changed."""
        stats = await self.inventory()
        cache: dict[str, list[Any]] = await self.repository.get_state("local_stat", {})
        fresh: dict[str, list[Any]] = {}
        for path, (mtime, size) in stats.items():
            cached = cache.get(path)
            if cached and cached[0] == mtime and cached[1] == size:
                fresh[path] = cached
                continue
            try:
                content = await self.read(path)
            except ValueError as exc:
                fresh[path] = [mtime, size, None, str(exc)]
                continue
            if content is not None:
                fresh[path] = [mtime, size, digest(content), None]
        if fresh != cache:
            await self.repository.put_state("local_stat", fresh)
        self.unavailable = {p: v[3] for p, v in fresh.items() if v[3]}
        return {p: v[2] for p, v in fresh.items() if v[2]}

    async def files(self) -> dict[str, str]:
        paths = list(await self.inventory())
        files = {}
        self.unavailable = {}
        for path in paths:
            # One unreadable or credential-like note must not block every other note.
            try:
                content = await self.read(path)
            except ValueError as exc:
                self.unavailable[path] = str(exc)
                continue
            if content is not None:
                files[path] = content
        return files

    def parse_all(self, files: dict[str, str]) -> list[Document]:
        """Parse each note on its own; invalid or duplicate ones are reported, not indexed."""
        documents: list[Document] = []
        seen: set[str] = set()
        for path, content in files.items():
            try:
                doc = parse_document(path, content)
            except ValueError as exc:
                self.unavailable[path] = str(exc)
                continue
            if doc.id in seen:
                self.unavailable[path] = "Duplicate document identity"
                continue
            seen.add(doc.id)
            documents.append(doc)
        return documents

    async def index(self, force: bool = False) -> dict[str, Any]:
        # Reads call this on every request: when no file stat changed, skip reading the vault.
        inventory = list((await self.inventory()).items())
        stats = digest(json.dumps([inventory, sorted(self.settings.exclude)]))
        summary = await self.repository.get_state("index_summary")
        fingerprint = await self.repository.get_state("index_fingerprint")
        if not force and summary and fingerprint and summary.get("stats") == stats:
            self.unavailable = await self.repository.get_state("unavailable", {})
            return {k: v for k, v in summary.items() if k != "stats"}
        documents = self.parse_all(await self.files())
        new_fingerprint = digest(json.dumps([(d.path, d.hash) for d in documents]))
        if force or new_fingerprint != fingerprint:
            await self.repository.refresh(documents, force=force)
            await self.repository.put_state("index_fingerprint", new_fingerprint)
        await self.repository.put_state("unavailable", self.unavailable)
        result = {
            "documents": len(documents),
            "relationships": sum(len(d.relationships) for d in documents),
            "unavailable": len(self.unavailable),
        }
        await self.repository.put_state("index_summary", {**result, "stats": stats})
        return result

    async def get(self, path: str) -> dict[str, Any]:
        content = await self.read(path)
        if content is None:
            raise ValueError("Document does not exist")
        try:
            doc = parse_document(path, content)
        except ValueError as exc:
            # Still readable, with the hash needed to propose a fix.
            return {
                "path": path,
                "content": content,
                "hash": digest(content),
                "metadata": {},
                "error": str(exc),
            }
        return {
            "path": path,
            "id": doc.id,
            "title": doc.title,
            "content": content,
            "hash": doc.hash,
            "metadata": doc.metadata,
            "related": await self.related(path),
        }

    async def links(
        self, docs: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Resolved edges (including tag edges), unresolved edges and tag nodes, uncapped."""
        aliases: dict[str, list[str]] = {}
        for doc in docs:
            for alias in {
                doc["id"],
                doc["path"].removesuffix(".md"),
                doc["title"],
                Path(doc["path"]).stem,
            }:
                aliases.setdefault(alias.casefold(), []).append(doc["id"])
        edges = []
        unresolved = []
        for edge in await self.repository.edges():
            targets = set(aliases.get(edge["target"].casefold(), []))
            if len(targets) == 1:
                edges.append({**edge, "target": next(iter(targets))})
            else:
                unresolved.append(edge)
        # Tags derive associations without persisting invented document assertions.
        tag_groups: dict[str, list[str]] = {}
        for doc in docs:
            for tag in doc_tags(doc):
                tag_groups.setdefault(tag, []).append(doc["id"])
        tag_nodes = [
            {"id": "tag:" + tag, "title": tag, "path": None, "categories": ["tag"]}
            for tag in tag_groups
        ]
        for tag, tagged_ids in tag_groups.items():
            edges.extend(
                {
                    "id": digest(identity + tag),
                    "source": identity,
                    "target": "tag:" + tag,
                    "kind": "tagged",
                    "origin": "tag",
                    "explicit": True,
                }
                for identity in tagged_ids
            )
        return edges, unresolved, tag_nodes

    async def graph(self, path: str | None = None, limit: int = 100) -> dict[str, Any]:
        docs = await self.repository.documents()
        edges, unresolved, tag_nodes = await self.links(docs)
        selected = {d["id"] for d in docs if d["path"] == path} if path else set()
        if path and not selected:
            raise ValueError("Document not indexed")
        if path:
            selected |= {e["target"] for e in edges if e["source"] in selected} | {
                e["source"] for e in edges if e["target"] in selected
            }
        nodes = [
            {"id": d["id"], "title": d["title"], "path": d["path"], "categories": d["categories"]}
            for d in docs
        ] + tag_nodes
        nodes = [n for n in nodes if not path or n["id"] in selected]
        if path:
            nodes.sort(key=lambda n: n["path"] != path)
        nodes = nodes[: max(1, min(limit, 300))]
        ids = {n["id"] for n in nodes}
        return {
            "nodes": nodes,
            "edges": [e for e in edges if e["source"] in ids and e["target"] in ids],
            "unresolved": [e for e in unresolved if not path or e["source"] in selected],
            "truncated": len(selected if path else docs + tag_nodes) > len(nodes),
        }

    async def related(self, path: str) -> dict[str, Any]:
        try:
            graph = await self.graph(path)
        except ValueError:
            return {"nodes": [], "outgoing": [], "incoming": [], "unresolved": []}
        identity = next(n["id"] for n in graph["nodes"] if n["path"] == path)
        return {
            "nodes": graph["nodes"],
            "outgoing": [e for e in graph["edges"] if e["source"] == identity],
            "incoming": [e for e in graph["edges"] if e["target"] == identity],
            "unresolved": graph["unresolved"],
        }

    async def search(
        self, query: str = "", limit: int = 20, folder: str = "", tag: str = "", category: str = ""
    ) -> dict[str, Any]:
        if len(query) > 500:
            raise ValueError("Query too long")
        terms = query.casefold().split()
        fulltext = await self.repository.fulltext(query) if query.strip() else {}
        docs = await self.repository.documents()
        candidates = []
        for doc in docs:
            meta = doc["metadata_json"]
            tags = doc_tags(doc)
            if (
                (folder and not doc["path"].startswith(folder.rstrip("/") + "/"))
                or (tag and tag not in tags)
                or (category and category not in doc["categories"])
            ):
                continue
            kinds = []
            score = fulltext.get(doc["id"], 0.0)
            for label, value, weight in [
                ("title", doc["title"], 5),
                ("tags", " ".join(tags), 4),
                ("content", doc["body"], 1),
                ("metadata", json.dumps(meta), 1),
            ]:
                if terms and all(t in value.casefold() for t in terms):
                    kinds.append(label)
                    score += weight
            if doc["id"] in fulltext:
                kinds.append("fulltext")
            candidates.append(
                {
                    "path": doc["path"],
                    "id": doc["id"],
                    "title": doc["title"],
                    "categories": doc["categories"],
                    "tags": tags,
                    "score": score,
                    "match_types": kinds,
                    "snippet": redact(best_snippet(doc["body"], terms)),
                    "hash": doc["hash"],
                }
            )
        edges, _, _ = await self.links(docs)
        matches = {d["id"] for d in candidates if d["score"] > 0}
        results = []
        for candidate in candidates:
            relations = [e for e in edges if candidate["id"] in (e["source"], e["target"])]
            # Only explicit direct document relations affect textual ranking.
            related_match = any(
                e["explicit"]
                and e["kind"] != "tagged"
                and (
                    (e["source"] in matches and e["target"] == candidate["id"])
                    or (e["target"] in matches and e["source"] == candidate["id"])
                )
                for e in relations
            )
            if terms and related_match:
                candidate["score"] += 0.5
                candidate["match_types"].append("graph")
            if terms and not candidate["score"]:
                continue
            candidate["relationships"] = relations[:10]
            results.append(candidate)
        results.sort(key=lambda d: (-d["score"], d["path"]))
        return {
            "mode": "textual",
            "results": results[: max(1, min(limit, 100))],
            "total": len(results),
        }

    async def propose(
        self,
        operation: str,
        path: str,
        content: str | None = None,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        if operation not in {"create", "update", "delete"}:
            raise ValueError("Unsupported operation")
        current = await self.read(path)
        if operation == "create" and current is not None:
            raise ValueError("Document already exists")
        if operation != "create" and (current is None or digest(current) != expected_hash):
            raise ValueError("Document revision changed; read it again before proposing")
        if operation == "delete":
            content = None
        else:
            if content is None or len(content.encode()) > self.settings.max_file_bytes:
                raise ValueError("Missing or oversized document")
            if operation == "update" and current is not None:
                content = preserve_metadata(path, current, content)
            if sensitive(content):
                raise ValueError("Document appears to contain credentials; publication blocked")
            doc = parse_document(path, content)
            if operation == "create" and "id" not in doc.metadata:
                # Insert only missing properties; unknown YAML remains byte-for-byte intact.
                import yaml

                additions = {
                    "id": "knowledge-" + uuid4().hex,
                    "title": doc.title,
                    "type": "reflection",
                    "created": now()[:10],
                    "updated": now()[:10],
                    "tags": [],
                    "related": [],
                    "source": "reviewed_proposal",
                    "status": "active",
                }
                header = yaml.safe_dump(
                    {k: v for k, v in additions.items() if k not in doc.metadata},
                    allow_unicode=True,
                    sort_keys=False,
                )
                bom = "﻿" if content.startswith("﻿") else ""
                content = content.removeprefix("﻿")
                if content.startswith("---\n"):
                    content = "---\n" + header + content[4:]
                elif content.startswith("---\r\n"):
                    content = "---\r\n" + header.replace("\n", "\r\n") + content[5:]
                else:
                    content = "---\n" + header + "---\n" + content
                content = bom + content
                parse_document(path, content)
        if content is not None and len(content.encode()) > self.settings.max_file_bytes:
            raise ValueError("Oversized document after metadata preservation")
        proposal: dict[str, Any] = {
            "id": uuid4().hex,
            "operation": operation,
            "path": path,
            "base": current,
            "content": content,
            "status": "proposed",
            "created": now(),
        }
        proposal["before_hash"] = digest_or_none(current)
        proposal["after_hash"] = digest_or_none(content)
        proposal["diff"] = "\n".join(
            difflib.unified_diff(
                (current or "").splitlines(),
                (content or "").splitlines(),
                fromfile=path,
                tofile=path,
                lineterm="",
            )
        )
        published = await self.repository.manifest_content(path)
        proposal["publication_diff"] = "\n".join(
            difflib.unified_diff(
                (published or "").splitlines(),
                (content or "").splitlines(),
                fromfile="GitHub/" + path,
                tofile="Propuesta/" + path,
                lineterm="",
            )
        )
        await self.repository.put_state("proposal:" + proposal["id"], proposal)
        return {k: v for k, v in proposal.items() if k not in {"base", "content"}}

    async def remote_content(self, path: str) -> str | None:
        _, tree = await self.remote.snapshot(self.settings.repository, self.settings.branch)
        entry = tree.get(path)
        if not entry:
            return None
        return await self.remote.blob(
            self.settings.repository, entry["sha"], self.settings.max_file_bytes
        )

    async def confirm(self, proposal_id: str) -> dict[str, Any]:
        proposal = await self.repository.get_state("proposal:" + proposal_id)
        if not proposal:
            raise ValueError("Unknown proposal")
        path = proposal["path"]
        if proposal["status"] == "published" or (
            proposal["status"] == "approved" and proposal.get("applied")
        ):
            return {"id": proposal_id, "status": proposal["status"], "path": path}
        current = await self.read(path)
        if proposal["status"] == "approved":
            # Approval was persisted but the write was interrupted; finish it now.
            if current == proposal["content"]:
                proposal["applied"] = now()
                await self.repository.put_state("proposal:" + proposal_id, proposal)
                return {"id": proposal_id, "path": path, "status": "approved"}
            if current != proposal["base"]:
                raise ValueError("Document changed after approval; generate a new proposal")
            return await self.apply(proposal_id, proposal)
        if proposal["status"] != "proposed":
            raise ValueError("Proposal is no longer applicable")
        if current != proposal["base"]:
            raise ValueError("Document revision changed; generate a new proposal")
        theirs = None
        if proposal.get("conflict_id"):
            conflict = await self.conflict(proposal["conflict_id"])
            if conflict.get("resolved"):
                raise ValueError("Conflict already resolved; generate a new proposal")
            theirs = await self.remote_content(path)
            # Compare with what the user reviewed, not the conflict's latest observation.
            if digest_or_none(theirs) != proposal.get("remote_hash"):
                raise ValueError("Remote revision changed; synchronize before resolving")
        if proposal["content"] is not None:
            await self.index()
            identities = {d["id"] for d in await self.repository.documents() if d["path"] != path}
            if parse_document(path, proposal["content"]).id in identities:
                raise ValueError("Duplicate document identity")
        # Durable approval precedes disk I/O; retries can recover interrupted writes.
        proposal.update(status="approved", confirmed=now())
        await self.repository.put_state("proposal:" + proposal_id, proposal)
        if proposal.get("conflict_id"):
            conflict = await self.conflict(proposal["conflict_id"])
            await self.repository.put_manifest({path: (theirs, None)})
            conflict["resolved"] = now()
            conflict["resolved_by"] = proposal_id
            await self.repository.put_state("conflict:" + proposal["conflict_id"], conflict)
        for key, previous in (await self.repository.states("proposal:")).items():
            if (
                previous["path"] == path
                and previous["id"] != proposal_id
                and previous["status"] == "approved"
            ):
                previous["status"] = "superseded"
                await self.repository.put_state(key, previous)
        return await self.apply(proposal_id, proposal)

    async def apply(self, proposal_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        await self.write(proposal["path"], proposal["content"])
        await self.repository.put_state(
            "audit:" + proposal_id,
            {
                "operation": proposal["operation"],
                "path": proposal["path"],
                "confirmed": proposal["confirmed"],
                "before": digest_or_none(proposal["base"]),
                "after": digest_or_none(proposal["content"]),
            },
        )
        proposal["applied"] = now()
        await self.repository.put_state("proposal:" + proposal_id, proposal)
        await self.index()
        return {"id": proposal_id, "path": proposal["path"], "status": "approved"}

    async def proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = await self.repository.get_state("proposal:" + proposal_id)
        if not proposal:
            raise ValueError("Unknown proposal")
        return {k: v for k, v in proposal.items() if k not in {"base", "content"}}

    async def sync_status(self) -> dict[str, Any]:
        proposals = await self.repository.states("proposal:")
        conflicts = await self.repository.states("conflict:")
        published = {p: d for p, (d, _) in (await self.repository.manifest_index()).items()}
        local = await self.local_digests()
        unavailable = {**await self.repository.get_state("unavailable", {}), **self.unavailable}
        approved_paths = {
            p["path"]
            for p in proposals.values()
            if p["status"] == "approved" and local.get(p["path"]) == p.get("after_hash")
        }
        return {
            "unreviewed": [
                p
                for p in sorted(set(local) | set(published))
                if local.get(p) != published.get(p)
                and p not in approved_paths
                and p not in unavailable
            ],
            "unavailable": [{"path": p, "reason": r} for p, r in sorted(unavailable.items())],
            "repository": self.settings.repository,
            "branch": self.settings.branch,
            "enabled": self.settings.sync_enabled,
            "last_sync": await self.repository.get_state("last_sync"),
            "pending": [
                {k: v for k, v in p.items() if k in {"id", "path", "status", "operation"}}
                for p in proposals.values()
                if p["status"] in {"proposed", "approved"}
            ],
            "conflicts": [
                {"id": k.removeprefix("conflict:"), "path": v["path"], "created": v["created"]}
                for k, v in conflicts.items()
                if not v.get("resolved")
            ],
            "error": await self.repository.get_state("sync_error"),
        }

    async def sync(self) -> dict[str, Any]:
        if not self.settings.sync_enabled:
            raise ValueError("Knowledge synchronization is disabled")
        try:
            result = await self._sync()
        except Exception:
            await self.repository.put_state(
                "sync_error",
                {"at": now(), "message": "Synchronization failed; pending changes preserved"},
            )
            raise
        await self.repository.put_state("sync_error", None)
        return {**result, "error": None}

    async def _sync(self) -> dict[str, Any]:
        repo, branch = self.settings.repository, self.settings.branch
        base = await self.repository.manifest()
        original = dict(base)
        shas = {p: sha for p, (_, sha) in (await self.repository.manifest_index()).items()}
        origin = {"repository": repo, "branch": branch}
        synced = any(v is not None for v in base.values())
        previous_origin = await self.repository.get_state("manifest_origin")
        if synced and previous_origin and previous_origin != origin:
            # Comparing against another remote would read every synced note as deleted there.
            raise ValueError(
                "Knowledge repository or branch changed since the last sync; "
                "synchronization stopped to protect local notes"
            )
        head, tree = await self.remote.snapshot(repo, branch)
        if synced and head is None:
            raise ValueError(
                "Remote branch is empty but notes were synchronized before; synchronization stopped"
            )
        skipped: dict[str, str] = {}
        remote_files: dict[str, str] = {}
        for path, item in tree.items():
            if not self.allowed(path):
                continue
            if (
                item.get("mode") != "100644"
                or int(item.get("size", 0)) > self.settings.max_file_bytes
            ):
                skipped[path] = "Unsupported remote document"
                continue
            known = base.get(path)
            # A blob sha names its content: reuse the baseline instead of downloading it again.
            if known is not None and shas.get(path) == item["sha"]:
                remote_files[path] = known
                continue
            try:
                content = await self.remote.blob(repo, item["sha"], self.settings.max_file_bytes)
            except UnsupportedDocument as exc:
                skipped[path] = str(exc)
                continue
            if sensitive(content):
                skipped[path] = "Remote document appears to contain credentials"
                continue
            remote_files[path] = content
        proposals = await self.repository.states("proposal:")
        approved = {
            p["path"]: (key, p)
            for key, p in sorted(proposals.items(), key=lambda item: item[1].get("confirmed", ""))
            if p["status"] == "approved"
        }
        # Recover a write interrupted after durable confirmation, without overwriting later edits.
        for path, (_, proposal) in approved.items():
            if path in skipped or not self.allowed(path):
                continue
            if proposal.get("conflict_id"):
                conflict = await self.repository.get_state("conflict:" + proposal["conflict_id"])
                if conflict and not conflict.get("resolved"):
                    # Interrupted between approval and resolution: finish only if the remote
                    # is still the version the user reviewed.
                    if digest_or_none(remote_files.get(path)) != proposal.get("remote_hash"):
                        continue
                    base[path] = remote_files.get(path)
                    conflict.update(resolved=now(), resolved_by=proposal["id"])
                    await self.repository.put_manifest({path: (base[path], None)})
                    original[path] = base[path]
                    shas.pop(path, None)
                    await self.repository.put_state("conflict:" + proposal["conflict_id"], conflict)
            try:
                current = await self.read(path)
            except ValueError:
                continue
            if not proposal.get("applied") and current == proposal["base"]:
                await self.write(path, proposal["content"])
        local = await self.files()
        skipped.update(self.unavailable)
        if synced and not local and not any(self.vault.iterdir()):
            # An empty directory is usually a missing bind mount, not deleted notes.
            raise ValueError("Knowledge vault looks empty; check the vault mount before syncing")
        # Two names that differ only by case/normalization are one file on macOS.
        names: dict[str, list[str]] = {}
        for path in set(base) | set(local) | set(remote_files):
            names.setdefault(fold(path), []).append(path)
        for group in names.values():
            if len(group) > 1:
                skipped.update(dict.fromkeys(group, "Path differs from another only by case"))
        changes: dict[str, str | None] = {}
        conflicts = await self.repository.states("conflict:")
        open_conflicts = {c["path"]: key for key, c in conflicts.items() if not c.get("resolved")}
        for path in sorted(set(base) | set(local) | set(remote_files)):
            if not self.allowed(path) or path in skipped:
                continue
            if path in open_conflicts:
                key = open_conflicts[path]
                conflict = conflicts[key]
                if conflict["remote"] != remote_files.get(path) or conflict["local"] != local.get(
                    path
                ):
                    conflict.setdefault("versions", []).append(
                        {"local": conflict["local"], "remote": conflict["remote"], "at": now()}
                    )
                    conflict.update(local=local.get(path), remote=remote_files.get(path))
                    await self.repository.put_state(key, conflict)
                continue
            old, ours, theirs = base.get(path), local.get(path), remote_files.get(path)
            if ours == theirs:
                base[path] = theirs
                continue
            if old not in (ours, theirs):
                await self.repository.put_state(
                    "conflict:" + uuid4().hex,
                    {"path": path, "base": old, "local": ours, "remote": theirs, "created": now()},
                )
                # A new divergence invalidates earlier approvals; resolving it creates a new one.
                if path in approved:
                    key, proposal = approved.pop(path)
                    proposal["status"] = "superseded"
                    await self.repository.put_state(key, proposal)
                continue
            if ours == old:
                await self.write(path, theirs)
                base[path] = theirs
            elif path in approved and ours == approved[path][1]["content"]:
                if ours is not None and sensitive(ours):
                    raise ValueError("Credential-like content blocks publication")
                changes[path] = ours
        if changes:
            head = await self.remote.publish(repo, branch, head, changes)
            base.update(changes)
        # Persist the baseline before marking completed operations; retry recognizes remote equality.
        # Only rows whose content or remote blob changed are written.
        remote_shas = {
            p: tree[p]["sha"]
            for p, c in remote_files.items()
            if p not in changes and base.get(p) == c
        }
        await self.repository.put_manifest(
            {
                p: (c, remote_shas.get(p))
                for p, c in base.items()
                if p not in original or original[p] != c or shas.get(p) != remote_shas.get(p)
            }
        )
        await self.repository.put_state("manifest_origin", origin)
        for path, (key, proposal) in approved.items():
            if path in base and base[path] == proposal["content"]:
                proposal.update(status="published", commit=head)
                await self.repository.put_state(key, proposal)
        indexed = await self.index()
        latest = {
            "at": now(),
            "commit": head,
            "published": len(changes),
            "skipped": len(skipped),
            **indexed,
        }
        await self.repository.put_state("last_sync", latest)
        status = await self.sync_status()
        self.unavailable.update(skipped)
        return {
            **latest,
            **status,
            "unavailable": [{"path": p, "reason": r} for p, r in sorted(skipped.items())],
            "unreviewed": [
                p
                for p in local
                if local[p] != base.get(p) and p not in approved and p not in skipped
            ],
        }

    async def conflict(self, conflict_id: str) -> dict[str, Any]:
        conflict = await self.repository.get_state("conflict:" + conflict_id)
        if not conflict:
            raise ValueError("Unknown conflict")
        return dict(conflict)

    async def resolve(
        self, conflict_id: str, content: str | None, expected_hash: str | None
    ) -> dict[str, Any]:
        conflict = await self.conflict(conflict_id)
        if conflict.get("resolved"):
            raise ValueError("Conflict already resolved")
        path = conflict["path"]
        # Confirmation checks that the remote is still the version shown in this review.
        remote_hash = digest_or_none(conflict["remote"])
        # Resolution is a new reviewable proposal; it does not publish immediately.
        if content is None and await self.read(path) is None:
            proposal = {
                "id": uuid4().hex,
                "operation": "delete",
                "path": path,
                "base": None,
                "content": None,
                "status": "proposed",
                "created": now(),
                "conflict_id": conflict_id,
                "remote_hash": remote_hash,
                "diff": "",
                "publication_diff": "\n".join(
                    difflib.unified_diff(
                        (conflict["remote"] or "").splitlines(),
                        [],
                        fromfile="GitHub/" + path,
                        tofile="Propuesta/" + path,
                        lineterm="",
                    )
                ),
            }
            await self.repository.put_state("proposal:" + proposal["id"], proposal)
            return {k: v for k, v in proposal.items() if k not in {"base", "content"}}
        proposal = await self.propose(
            "delete"
            if content is None
            else ("create" if await self.read(path) is None else "update"),
            path,
            content,
            expected_hash,
        )
        full = await self.repository.get_state("proposal:" + proposal["id"])
        full.update(conflict_id=conflict_id, remote_hash=remote_hash)
        await self.repository.put_state("proposal:" + proposal["id"], full)
        return proposal

    async def dashboard(self) -> dict[str, Any]:
        docs = await self.repository.documents()
        edges, _, _ = await self.links(docs)
        connected = {e[k] for e in edges for k in ("source", "target")}
        recent = sorted(
            docs, key=lambda d: str(d["metadata_json"].get("updated", "")), reverse=True
        )[:10]
        return {
            "documents": len(docs),
            "relationships": len(await self.repository.edges()),
            "recent": [{"path": d["path"], "title": d["title"]} for d in recent],
            "isolated": [
                {"path": d["path"], "title": d["title"]} for d in docs if d["id"] not in connected
            ][:50],
            "sync": await self.sync_status(),
        }


def doc_tags(doc: dict[str, Any]) -> list[str]:
    try:
        return list(strings(doc["metadata_json"].get("tags")))
    except ValueError:
        return []


async def execute_knowledge(config: Any, credentials: Any, action: str, **kwargs: Any) -> Any:
    settings = config.knowledge
    if not settings.enabled:
        raise ValueError("Knowledge is disabled; configure it before use")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", settings.repository):
        raise ValueError("Invalid knowledge repository")
    url = os.environ.get("COPILOT_KNOWLEDGE_DATABASE_URL")
    if not url or not url.startswith("mysql+asyncmy://"):
        raise ValueError("Configure COPILOT_KNOWLEDGE_DATABASE_URL for MySQL")
    if config.obsidian.vault_path is None:
        raise ValueError("Configure an Obsidian vault")
    repository = KnowledgeRepository.shared(url)
    excludes = settings.exclude + config.obsidian.index_exclude
    if config.obsidian.transcripts_folder and not config.obsidian.index_transcripts:
        excludes = [
            *excludes,
            f"{config.obsidian.meetings_folder.strip('/')}/{config.obsidian.transcripts_folder.strip('/')}/*",
        ]
    settings = settings.model_copy(update={"exclude": list(set(excludes))})
    service = KnowledgeService(
        settings,
        config.obsidian.vault_path,
        repository,
        KnowledgeGitHub(credentials, settings.account),
    )
    try:
        if action in WRITE_ACTIONS:
            async with service.locked():
                await repository.initialize()
                return await getattr(service, action)(**kwargs)
        await repository.initialize()
        if action == "history":
            path = kwargs["path"]
            service.path(path)
            await service.remote.verify_private(settings.repository)
            return await service.remote.history(settings.repository, settings.branch, path)
        if action == "version":
            service.path(kwargs["path"])
            return {"content": await service.remote.version(settings.repository, **kwargs)}
        if action not in {
            "index",
            "get",
            "search",
            "graph",
            "related",
            "propose",
            "proposal",
            "sync_status",
            "dashboard",
            "conflict",
            "resolve",
        }:
            raise ValueError("Unknown knowledge operation")
        if action in {"get", "search", "graph", "related", "dashboard"}:
            try:
                await service.index()
            except Exception:
                # A concurrent refresh may win the race; serve the index as it is.
                logger.warning("knowledge_index_refresh_failed", action=action, exc_info=True)
        return await getattr(service, action)(**kwargs)
    except ValueError:
        raise
    except Exception:
        logger.warning("knowledge_operation_failed", action=action, exc_info=True)
        raise ValueError(
            "Knowledge infrastructure unavailable; pending changes preserved"
        ) from None
