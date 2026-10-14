"""Async, rebuildable knowledge index; operational state survives reindexing."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Index,
    MetaData,
    String,
    Table,
    Text,
    delete,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

if TYPE_CHECKING:
    from collections.abc import Sequence

    from executive_copilot.services.knowledge.documents import Document

SCHEMA = MetaData()
IDENTITY = String(191).with_variant(String(191, collation="utf8mb4_bin"), "mysql")
DOCUMENTS = Table(
    "knowledge_documents",
    SCHEMA,
    Column("id", IDENTITY, primary_key=True),
    Column("path", String(512), nullable=False),
    Column("title", String(512), nullable=False),
    Column("body", Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column("tags_text", Text, nullable=False),
    Column("metadata_json", JSON, nullable=False),
    Column("categories", JSON, nullable=False),
    Column("hash", String(64), nullable=False),
)
# MySQL FULLTEXT is deliberately not presented as semantic search.
Index(
    "ix_knowledge_fulltext",
    DOCUMENTS.c.title,
    DOCUMENTS.c.body,
    DOCUMENTS.c.tags_text,
    mysql_prefix="FULLTEXT",
).ddl_if(dialect="mysql")
EDGES = Table(
    "knowledge_edges",
    SCHEMA,
    Column("id", String(64), primary_key=True),
    Column("source", IDENTITY, nullable=False, index=True),
    Column("target", String(512), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("origin", String(32), nullable=False),
    Column("explicit", Boolean, nullable=False),
)
# Last synchronized content per path (None: known deleted). remote_sha is the GitHub blob
# holding exactly this content, so an unchanged remote file is not downloaded again.
MANIFEST = Table(
    "knowledge_manifest",
    SCHEMA,
    Column(
        "path",
        String(512).with_variant(String(512, collation="utf8mb4_bin"), "mysql"),
        primary_key=True,
    ),
    Column("content", Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
    Column("digest", String(64), nullable=True),
    Column("remote_sha", String(64), nullable=True),
)
STATE = Table(
    "knowledge_state",
    SCHEMA,
    Column("key", String(191), primary_key=True),
    Column("value", JSON, nullable=False),
)


def _digest(content: str | None) -> str | None:
    from executive_copilot.services.knowledge.documents import digest

    return digest(content) if content is not None else None


def jsonable(value: Any) -> Any:
    """YAML allows non-string keys (dates, numbers); JSON columns do not."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return json.loads(json.dumps(value, default=str))


class KnowledgeRepository:
    # One pooled engine per URL and event loop; pooled connections cannot cross loops.
    _shared: ClassVar[dict[tuple[str, int], KnowledgeRepository]] = {}

    def __init__(self, url: str) -> None:
        if not url.startswith(("mysql+asyncmy://", "sqlite+aiosqlite://")):
            raise ValueError(
                "Knowledge database requires mysql+asyncmy (SQLite is for offline tests)"
            )
        self.engine: AsyncEngine = create_async_engine(
            url, pool_pre_ping=True, hide_parameters=True
        )
        self._initialized = False

    @classmethod
    def shared(cls, url: str) -> KnowledgeRepository:
        key = (url, id(asyncio.get_running_loop()))
        if key not in cls._shared:
            cls._shared[key] = cls(url)
        return cls._shared[key]

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self.engine.begin() as conn:
            await conn.run_sync(SCHEMA.create_all)
            # The baseline used to be one JSON state row; move it once into per-path rows.
            legacy = (
                await conn.execute(select(STATE.c.value).where(STATE.c.key == "manifest"))
            ).first()
            if legacy and not (await conn.execute(select(MANIFEST.c.path).limit(1))).first():
                for path, content in (legacy[0] or {}).items():
                    await conn.execute(
                        insert(MANIFEST).values(
                            path=path, content=content, digest=_digest(content), remote_sha=None
                        )
                    )
            await conn.execute(delete(STATE).where(STATE.c.key.in_(["manifest", "remote_blobs"])))
        self._initialized = True

    async def close(self) -> None:
        await self.engine.dispose()

    async def manifest(self) -> dict[str, str | None]:
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(MANIFEST.c.path, MANIFEST.c.content))
            return {r.path: r.content for r in rows}

    async def manifest_index(self) -> dict[str, tuple[str | None, str | None]]:
        """Digest and remote blob sha per path, without loading contents."""
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                select(MANIFEST.c.path, MANIFEST.c.digest, MANIFEST.c.remote_sha)
            )
            return {r.path: (r.digest, r.remote_sha) for r in rows}

    async def manifest_content(self, path: str) -> str | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(select(MANIFEST.c.content).where(MANIFEST.c.path == path))
            ).first()
            return row[0] if row else None

    async def put_manifest(self, rows: dict[str, tuple[str | None, str | None]]) -> None:
        """Write only the given paths: (content, remote_sha)."""
        if not rows:
            return
        async with self.engine.begin() as conn:
            await conn.execute(delete(MANIFEST).where(MANIFEST.c.path.in_(list(rows))))
            await conn.execute(
                insert(MANIFEST),
                [
                    {"path": p, "content": c, "digest": _digest(c), "remote_sha": sha}
                    for p, (c, sha) in rows.items()
                ],
            )

    async def get_state(self, key: str, default: Any = None) -> Any:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(STATE.c.value).where(STATE.c.key == key))).first()
            return row[0] if row else default

    async def put_state(self, key: str, value: Any) -> None:
        async with self.engine.begin() as conn:
            exists = (await conn.execute(select(STATE.c.key).where(STATE.c.key == key))).first()
            if exists:
                await conn.execute(update(STATE).where(STATE.c.key == key).values(value=value))
            else:
                await conn.execute(insert(STATE).values(key=key, value=value))

    async def states(self, prefix: str) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(STATE).where(STATE.c.key.startswith(prefix)))
            return {r.key: r.value for r in rows}

    async def rebuild(self, documents: Sequence[Document]) -> None:
        await self.refresh(documents, force=True)

    async def refresh(self, documents: Sequence[Document], force: bool = False) -> None:
        from executive_copilot.services.knowledge.documents import digest

        if len({d.id for d in documents}) != len(documents):
            raise ValueError("Duplicate document identities; index unchanged")
        async with self.engine.begin() as conn:
            existing = {
                r.id: (r.hash, r.path)
                for r in (
                    await conn.execute(select(DOCUMENTS.c.id, DOCUMENTS.c.hash, DOCUMENTS.c.path))
                )
            }
            incoming = {d.id: (d.hash, d.path) for d in documents}
            changed = {
                key for key, value in existing.items() if force or incoming.get(key) != value
            } | (set(incoming) - set(existing))
            await conn.execute(delete(EDGES).where(EDGES.c.source.not_in(incoming)))
            if changed:
                await conn.execute(delete(EDGES).where(EDGES.c.source.in_(changed)))
                await conn.execute(delete(DOCUMENTS).where(DOCUMENTS.c.id.in_(changed)))
            for doc in documents:
                if not force and existing.get(doc.id) == (doc.hash, doc.path):
                    continue
                await conn.execute(
                    insert(DOCUMENTS).values(
                        id=doc.id,
                        path=doc.path,
                        title=doc.title,
                        body=doc.body,
                        tags_text=" ".join(doc.tags),
                        metadata_json=jsonable(doc.metadata),
                        categories=list(doc.categories),
                        hash=doc.hash,
                    )
                )
                for rel in doc.relationships:
                    identity = digest(
                        json.dumps([doc.id, rel.target, rel.kind, rel.origin, rel.explicit])
                    )
                    await conn.execute(
                        insert(EDGES).values(
                            id=identity,
                            source=doc.id,
                            target=rel.target,
                            kind=rel.kind,
                            origin=rel.origin,
                            explicit=rel.explicit,
                        )
                    )

    async def documents(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            return [dict(r) for r in (await conn.execute(select(DOCUMENTS))).mappings()]

    async def edges(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            return [dict(r) for r in (await conn.execute(select(EDGES))).mappings()]

    async def fulltext(self, query: str) -> dict[str, float]:
        if self.engine.dialect.name != "mysql":
            return {}
        async with self.engine.connect() as conn:
            return await self._fulltext(conn, query)

    @staticmethod
    async def _fulltext(conn: AsyncConnection, query: str) -> dict[str, float]:
        result = await conn.execute(
            text(
                "SELECT id, MATCH(title, body, tags_text) AGAINST (:query IN NATURAL LANGUAGE MODE) AS score "
                "FROM knowledge_documents WHERE MATCH(title, body, tags_text) "
                "AGAINST (:query IN NATURAL LANGUAGE MODE) > 0"
            ),
            {"query": query},
        )
        return {r.id: float(r.score) for r in result}
