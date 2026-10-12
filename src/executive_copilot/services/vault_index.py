"""Token-efficient access to the Obsidian vault: index, search, then read.

Dumping notes into an assistant's context is expensive (the Paycode folder
alone is ~2 MB of text). Instead:

1. Every note in the indexed folders is split into sections (by heading,
   long sections into paragraph-sized pieces) and stored in a local SQLite
   FTS5 index: accent-insensitive, BM25-ranked, no AI or tokens involved.
   The index refreshes incrementally (only files whose mtime/size changed).
2. ``search`` returns a handful of short snippets with their note and
   section, so the assistant sees *where* things are for a few hundred tokens.
3. ``read`` returns one note, or only one of its sections, capped in size;
   long notes come with a table of contents so the next read can be targeted.
4. ``overview`` and ``person_context`` give compact maps of what exists.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from executive_copilot.logging import get_logger
from executive_copilot.services.obsidian_notes import normalize

_GITHUB_REF = re.compile(
    r"^(?:https?://(?:www\.)?github\.com/)?(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)(?P<rel>/.*)?$"
)

logger = get_logger(__name__)

CHUNK_CHARS = 1800
REFRESH_SECONDS = 30
HEADING = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}

# Credentials must never reach an assistant through search snippets.
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                      # AWS access key id
    re.compile(r"\b(?:AC|SK|VA|MG)[0-9a-f]{32}\b"),           # Twilio SIDs / keys
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"),      # OpenAI keys
    re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}\b"),          # Slack tokens
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),            # GitHub tokens
    re.compile(r"\b\d/[A-Za-z0-9_\-]{30,}\b"),                # Asana PATs
    re.compile(
        r"(?i)\b(password|passwd|contrase[nñ]a|secret|token|api[_ -]?key|auth[_ -]?token|client[_ -]?secret)"
        r"(\s*(?:\*\*)?\s*[:=]\s*(?:\*\*)?\s*)([`'\"]?)[^\s`'\"*]{8,}\3"
    ),
]


def redact(text: str) -> str:
    """Replace credential-looking values with [REDACTED]."""
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, mtime REAL, size INTEGER, title TEXT,
    folder TEXT, links TEXT, chars INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    path UNINDEXED, title, heading, body, date UNINDEXED, folder UNINDEXED, ord UNINDEXED,
    tokenize = "unicode61 remove_diacritics 2"
);
CREATE TABLE IF NOT EXISTS vectors (
    chunk_id INTEGER PRIMARY KEY, path TEXT, model TEXT, vec BLOB
);
CREATE INDEX IF NOT EXISTS vectors_path ON vectors(path);
"""
RRF_K = 60
GITHUB_PREFIX = "GitHub"
GITHUB_CARD = "_repositorio.md"


def split_sections(text: str, title: str) -> list[dict[str, Any]]:
    """Split markdown into heading-scoped chunks of at most CHUNK_CHARS."""
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL)  # frontmatter
    sections: list[tuple[str, list[str]]] = []
    path: list[str] = []
    current: list[str] = []
    heading = ""
    for line in text.splitlines():
        match = HEADING.match(line)
        if match and len(match.group(1)) >= 2:
            if current:
                sections.append((heading, current))
            level = len(match.group(1)) - 2
            path = [*path[:level], match.group(2)]
            heading = " > ".join(path)
            current = []
        elif match:  # H1 = title line, not a section boundary
            continue
        else:
            current.append(line)
    if current:
        sections.append((heading, current))

    chunks: list[dict[str, Any]] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        date_match = DATE.search(heading)
        pieces: list[str] = []
        buf = ""
        for para in re.split(r"\n\s*\n", body):
            if buf and len(buf) + len(para) > CHUNK_CHARS:
                pieces.append(buf)
                buf = ""
            while len(para) > CHUNK_CHARS:  # giant paragraph / code block
                pieces.append(para[:CHUNK_CHARS])
                para = para[CHUNK_CHARS:]
            buf = f"{buf}\n\n{para}" if buf else para
        if buf.strip():
            pieces.append(buf)
        for piece in pieces:
            chunks.append({
                "heading": heading or title,
                "body": piece.strip(),
                "date": date_match.group(0) if date_match else None,
            })
    return chunks


STOPWORDS = {"a", "al", "ante", "como", "con", "cual", "cuales", "cuando", "cuanto", "cuanta", "cuantos", "cuantas", "de", "del", "donde", "el", "ella", "ellos", "en", "entre", "era", "es", "esa", "ese", "eso", "esta", "este", "esto", "estos", "fue", "ha", "han", "hay", "la", "las", "le", "les", "lo", "los", "mas", "me", "mi", "mis", "muy", "nos", "nuestra", "nuestro", "nuestras", "nuestros", "o", "para", "pero", "por", "porque", "que", "quien", "quienes", "se", "ser", "si", "sin", "sobre", "son", "su", "sus", "tambien", "te", "tiene", "tienen", "tu", "un", "una", "uno", "unos", "y", "ya", "yo", "vamos", "hacemos", "hace", "hacer", "tendria", "tenemos", "podemos", "puede", "pasa", "paso", "sale", "va", "van", "the", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it", "of", "on", "or", "our", "that", "this", "to", "was", "we", "what", "when", "where", "which", "who", "why", "with", "do", "does", "did", "can", "could", "should", "would", "will", "about", "into"}


def fts_query(query: str, mode: str = "AND") -> str:
    """Build an FTS5 query from free text: prefix-matched content words."""
    words = [w for w in normalize(query).split() if len(w) > 1 and w not in STOPWORDS]
    return f" {mode} ".join(f"{w}*" for w in words)


class VaultIndex:
    """Incremental FTS index over selected folders of an Obsidian vault."""

    def __init__(
        self,
        vault_path: Path,
        folders: list[str],
        db_path: Path,
        exclude: list[str] | None = None,
        people_folder: str | None = None,
        extra_roots: dict[str, Path] | None = None,
    ) -> None:
        self.vault = vault_path
        self.folders = [f.strip("/") for f in folders if f.strip("/")]
        self.db_path = db_path
        self.exclude = exclude or []
        self.people_folder = (people_folder or "").strip("/") or None
        # Read-only sources outside the vault, indexed under "<prefix>/..." (e.g. GitHub docs).
        self.extra_roots = {k.strip("/"): v for k, v in (extra_roots or {}).items()}
        self._last_refresh = 0.0

    def is_external(self, rel: str) -> bool:
        """Whether an indexed path comes from an extra root (read-only, not in the vault)."""
        return rel.split("/", 1)[0] in self.extra_roots

    # --- storage ------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        return conn

    def _files_on_disk(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for folder in self.folders:
            base = self.vault / folder
            if not base.is_dir():
                continue
            for path in base.rglob("*.md"):
                rel = path.relative_to(self.vault).as_posix()
                if any(part in SKIP_DIRS for part in path.parts):
                    continue
                if any(fnmatch(rel, pattern) for pattern in self.exclude):
                    continue
                found[rel] = path
        for prefix, root in self.extra_roots.items():
            if not root.is_dir():
                continue
            for path in root.rglob("*.md"):
                inner = path.relative_to(root).as_posix()
                if any(part.startswith(".") for part in path.relative_to(root).parts):
                    continue
                rel = f"{prefix}/{inner}"
                if any(fnmatch(rel, pattern) for pattern in self.exclude):
                    continue
                found[rel] = path
        return found

    def refresh(self, force: bool = False) -> dict[str, int]:
        """Re-index new/changed files and drop deleted ones."""
        if not force and time.time() - self._last_refresh < REFRESH_SECONDS:
            return {}
        stats = {"indexed": 0, "removed": 0, "unchanged": 0}
        on_disk = self._files_on_disk()
        with self._connect() as conn:
            known = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT path, mtime, size FROM files")}
            for rel in set(known) - set(on_disk):
                conn.execute("DELETE FROM files WHERE path = ?", (rel,))
                conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
                conn.execute("DELETE FROM vectors WHERE path = ?", (rel,))
                stats["removed"] += 1
            for rel, path in on_disk.items():
                st = path.stat()
                if known.get(rel) == (st.st_mtime, st.st_size):
                    stats["unchanged"] += 1
                    continue
                try:
                    text = redact(path.read_text(encoding="utf-8", errors="replace"))
                except OSError as e:
                    logger.warning("Cannot read note", path=rel, error=str(e))
                    continue
                h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
                title = (h1.group(1).strip() if h1 else path.stem)[:200]
                if self.is_external(rel):
                    folder = rel.split("/", 1)[0]
                else:
                    folder = rel.split("/")[1] if rel.count("/") >= 2 else rel.split("/")[0]
                links = sorted({normalize(t.split("/")[-1]) for t in WIKILINK.findall(text)})
                conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
                conn.execute("DELETE FROM vectors WHERE path = ?", (rel,))
                for order, chunk in enumerate(split_sections(text, title)):
                    conn.execute(
                        "INSERT INTO chunks (path, title, heading, body, date, folder, ord) VALUES (?,?,?,?,?,?,?)",
                        (rel, title, chunk["heading"], chunk["body"], chunk["date"], folder, order),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO files (path, mtime, size, title, folder, links, chars) VALUES (?,?,?,?,?,?,?)",
                    (rel, st.st_mtime, st.st_size, title, folder, json.dumps(links), len(text)),
                )
                stats["indexed"] += 1
        self._last_refresh = time.time()
        if stats["indexed"] or stats["removed"]:
            logger.info("Vault index refreshed", **stats)
        return stats

    # --- queries ------------------------------------------------------------

    def search(self, query: str, folder: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        """Best matching sections as short snippets (at most 2 per note)."""
        self.refresh()
        limit = max(1, min(limit, 20))
        results: list[dict[str, Any]] = []
        with self._connect() as conn:
            for mode in ("AND", "OR"):
                match = fts_query(query, mode)
                if not match:
                    return []
                sql = (
                    "SELECT rowid, path, title, heading, date, "
                    "snippet(chunks, 3, '**', '**', ' … ', 28), bm25(chunks, 0, 6.0, 4.0, 1.0) AS rank "
                    "FROM chunks WHERE chunks MATCH ?"
                )
                params: list[Any] = [match]
                if folder:
                    sql += " AND (folder = ? OR path LIKE ?)"
                    params += [folder, f"%{folder}%"]
                sql += " ORDER BY rank LIMIT ?"
                params.append(limit * 4)
                try:
                    rows = conn.execute(sql, params).fetchall()
                except sqlite3.OperationalError as e:
                    logger.warning("Vault search failed", query=query, error=str(e))
                    rows = []
                per_note: dict[str, int] = {}
                seen: dict[tuple[str, str, str], dict[str, Any]] = {}
                for rowid, path, title, heading, date, snippet, _rank in rows:
                    key = section_key(title, heading, snippet)
                    if key in seen:
                        _also_in(seen[key], path)
                        continue
                    if per_note.get(path, 0) >= 2:
                        continue
                    per_note[path] = per_note.get(path, 0) + 1
                    seen[key] = {
                        "id": rowid,
                        "note": path,
                        "title": title,
                        "section": heading,
                        "date": date,
                        "snippet": re.sub(r"\s+", " ", snippet).strip(),
                    }
                    results.append(seen[key])
                    if len(results) >= limit:
                        break
                if results:
                    break
        return results

    # --- semantic search (embeddings stored in the same SQLite file) ---------

    async def embed_missing(self, embedder: Embedder, batch: int = 96) -> int:
        """Embed sections that have no vector yet for the embedder's model."""
        self.refresh()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT c.rowid, c.path, c.title, c.heading, c.body FROM chunks c "
                "LEFT JOIN vectors v ON v.chunk_id = c.rowid AND v.model = ? WHERE v.chunk_id IS NULL",
                (embedder.model,),
            ).fetchall()
        done = 0
        for i in range(0, len(rows), batch):
            part = rows[i : i + batch]
            vectors = await embedder([f"{t}\n{h}\n{b}"[:6000] for _, _, t, h, b in part])
            with self._connect() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO vectors (chunk_id, path, model, vec) VALUES (?,?,?,?)",
                    [(cid, path, embedder.model, vec_blob(v)) for (cid, path, *_), v in zip(part, vectors, strict=True)],
                )
            done += len(part)
        if done:
            logger.info("Embedded vault sections", count=done, model=embedder.model)
        return done

    def _matrix(self, model: str) -> tuple[Any, list[int]]:
        import numpy as np

        with self._connect() as conn:
            rows = conn.execute("SELECT chunk_id, vec FROM vectors WHERE model = ?", (model,)).fetchall()
        if not rows:
            return np.zeros((0, 1), dtype=np.float32), []
        ids = [r[0] for r in rows]
        mat = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
        return mat, ids

    async def _vector_ranking(self, query: str, embedder: Embedder, k: int) -> list[int]:
        import numpy as np

        await self.embed_missing(embedder)
        mat, ids = self._matrix(embedder.model)
        if not ids:
            return []
        q = np.asarray((await embedder([query]))[0], dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-9
        scores = mat @ q
        top = np.argsort(-scores)[:k]
        return [ids[i] for i in top]

    def _fts_ranking(self, query: str, k: int, folder: str | None = None) -> list[int]:
        with self._connect() as conn:
            for mode in ("AND", "OR"):
                match = fts_query(query, mode)
                if not match:
                    return []
                sql = "SELECT rowid FROM chunks WHERE chunks MATCH ?"
                params: list[Any] = [match]
                if folder:
                    sql += " AND (folder = ? OR path LIKE ?)"
                    params += [folder, f"%{folder}%"]
                sql += " ORDER BY bm25(chunks, 0, 6.0, 4.0, 1.0) LIMIT ?"
                params.append(k)
                try:
                    ids = [r[0] for r in conn.execute(sql, params)]
                except sqlite3.OperationalError:
                    ids = []
                if ids:
                    return ids
        return []

    def _results_for(self, chunk_ids: list[int], query: str, limit: int, folder: str | None) -> list[dict[str, Any]]:
        terms = [t for t in normalize(query).split() if len(t) > 2]
        out: list[dict[str, Any]] = []
        per_note: dict[str, int] = {}
        seen: dict[tuple[str, str, str], dict[str, Any]] = {}
        with self._connect() as conn:
            for cid in chunk_ids:
                row = conn.execute(
                    "SELECT path, title, heading, date, body, folder FROM chunks WHERE rowid = ?", (cid,)
                ).fetchone()
                if not row:
                    continue
                path, title, heading, date, body, chunk_folder = row
                if folder and normalize(folder) != normalize(chunk_folder) and normalize(folder) not in normalize(path):
                    continue
                key = section_key(title, heading, body)
                if key in seen:
                    _also_in(seen[key], path)
                    continue
                if per_note.get(path, 0) >= 2:
                    continue
                per_note[path] = per_note.get(path, 0) + 1
                result = {
                    "id": cid, "note": path, "title": title, "section": heading, "date": date,
                    "snippet": best_snippet(body, terms),
                }
                seen[key] = result
                out.append(result)
                if len(out) >= limit:
                    break
        return out

    async def semantic_search(
        self, query: str, embedder: Embedder, folder: str | None = None, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Sections closest in meaning to the query (embeddings only)."""
        ranking = await self._vector_ranking(query, embedder, k=limit * 6)
        return self._results_for(ranking, query, limit, folder)

    async def hybrid_search(
        self, query: str, embedder: Embedder | None, folder: str | None = None, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Words + meaning, fused by reciprocal rank; falls back to words only."""
        limit = max(1, min(limit, 20))
        self.refresh()
        fts = self._fts_ranking(query, k=40, folder=folder)
        vec: list[int] = []
        if embedder is not None:
            try:
                vec = await self._vector_ranking(query, embedder, k=40)
            except Exception as e:  # no key, network, quota: words still work
                logger.warning("Semantic search unavailable", error=str(e))
        # Meaning carries most of the weight (it won clearly on real questions);
        # exact words still lift sections that literally contain the terms.
        scores: dict[int, float] = {}
        for ranking, weight in ((vec, 1.0), (fts, 0.5)):
            for rank, cid in enumerate(ranking):
                scores[cid] = scores.get(cid, 0.0) + weight / (RRF_K + rank + 1)
        fused = sorted(scores, key=lambda c: -scores[c])
        return self._results_for(fused, query, limit, folder)

    def _resolve(self, conn: sqlite3.Connection, note: str) -> str | None:
        rows = conn.execute("SELECT path, title FROM files").fetchall()
        # Vault notes win name lookups over the many README/roadmap files of extra roots.
        rows.sort(key=lambda r: self.is_external(r[0]))
        target = note.strip().removesuffix(".md")
        for path, _ in rows:
            if path == note or path.removesuffix(".md") == target:
                return str(path)
        # A GitHub URL or "owner/repo" means the repository card (or one of its docs).
        m = _GITHUB_REF.match(target)
        if m:
            prefix = f"GitHub/{m.group('owner')}/{m.group('repo')}"
            rel = (m.group("rel") or "").strip("/")
            if rel.startswith(("blob/", "tree/")):
                rel = rel.split("/", 2)[2] if rel.count("/") >= 2 else ""
            wanted = f"{prefix}/{rel.removesuffix('.md')}" if rel else prefix
            exact = [p for p, _ in rows if p.removesuffix(".md").lower() == wanted.lower()]
            if exact:
                return str(exact[0])
            cards = [p for p, _ in rows if p.lower().startswith(prefix.lower() + "/") and p.endswith("_repositorio.md")]
            if cards:
                return str(cards[0])
        norm = normalize(target.split("/")[-1])
        for path, title in rows:
            if normalize(Path(path).stem) == norm or normalize(title) == norm:
                return str(path)
        partial = [p for p, t in rows if norm and (norm in normalize(Path(p).stem) or norm in normalize(t))]
        return min(partial, key=len) if partial else None

    def read(self, note: str, section: str | None = None, max_chars: int = 6000) -> dict[str, Any]:
        """One note, or only the sections whose heading matches ``section``."""
        self.refresh()
        max_chars = max(500, min(max_chars, 30000))
        with self._connect() as conn:
            path = self._resolve(conn, note)
            if not path:
                return {"error": f"No note matches '{note}'. Use search_vault or vault_overview."}
            rows = conn.execute(
                "SELECT heading, body, date FROM chunks WHERE path = ? ORDER BY CAST(ord AS INTEGER)", (path,)
            ).fetchall()
            title = conn.execute("SELECT title FROM files WHERE path = ?", (path,)).fetchone()[0]
        toc = list(dict.fromkeys(h for h, _, _ in rows))
        if section:
            wanted = normalize(section)
            rows = [r for r in rows if wanted in normalize(r[0])]
            if not rows:
                return {"note": path, "title": title, "error": f"No section matches '{section}'", "sections": toc}
        text = ""
        last = None
        for heading, body, _ in rows:
            if heading != last:
                text += f"\n\n## {heading}\n"
                last = heading
            text += body + "\n"
        text = text.strip()
        out: dict[str, Any] = {
            "note": path,
            "title": title,
            "content": text[:max_chars],
            "truncated": len(text) > max_chars,
        }
        if out["truncated"] or (not section and len(toc) > 8):
            out["sections"] = toc[:80]
            out["hint"] = "Pass section=<heading> to read only one part."
        return out

    def edit(
        self,
        note: str,
        action: str,
        section: str | None = None,
        find: str | None = None,
        text: str | None = None,
        dry_run: bool = False,
        backup_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Apply one edit to an indexed note, with backup and a diff of the change."""
        import difflib

        self.refresh()
        with self._connect() as conn:
            rel = self._resolve(conn, note)
        if not rel:
            raise VaultEditError(f"No note matches '{note}'")
        if self.is_external(rel):
            raise VaultEditError(f"'{rel}' is a read-only mirror (not a vault note)")
        path = self.vault / rel
        original = path.read_text(encoding="utf-8")
        updated = apply_edit(original, action, section=section, find=find, new_text=text)
        if updated == original:
            return {"note": rel, "changed": False, "detail": "The note already had that content"}
        diff = "\n".join(
            line for line in difflib.unified_diff(
                original.splitlines(), updated.splitlines(), lineterm="", n=1
            )
            if not line.startswith(("---", "+++"))
        )
        result: dict[str, Any] = {"note": rel, "action": action, "changed": True, "dry_run": dry_run, "diff": diff[:3000]}
        if dry_run:
            return result
        if backup_dir is not None:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            target = backup_dir / stamp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(original, encoding="utf-8")
            result["backup"] = str(target)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(updated, encoding="utf-8")
        tmp.replace(path)
        self.refresh(force=True)
        return result

    def read_chunk(self, chunk_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT path, title, heading, body FROM chunks WHERE rowid = ?", (chunk_id,)).fetchone()
        if not row:
            return {"error": f"No section with id {chunk_id}"}
        return {"note": row[0], "title": row[1], "section": row[2], "content": row[3]}

    def overview(self, folder: str | None = None, limit: int = 60) -> dict[str, Any]:
        """Compact map: folders with counts, or the notes of one folder."""
        self.refresh()
        with self._connect() as conn:
            files = conn.execute("SELECT path, title, folder, mtime, chars FROM files").fetchall()
            chunk_count = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        if folder:
            wanted = normalize(folder)
            notes = [f for f in files if normalize(f[2]) == wanted or wanted in normalize(f[0])]
            if wanted == normalize(GITHUB_PREFIX):
                # One card per repository instead of hundreds of loose docs.
                notes = [f for f in notes if f[0].endswith(f"/{GITHUB_CARD}")]
            notes.sort(key=lambda f: -f[3])
            return {
                "folder": folder,
                "total": len(notes),
                "notes": [
                    {
                        "note": p,
                        "title": t,
                        "updated": datetime.fromtimestamp(m, UTC).date().isoformat(),
                        "size_kb": round(c / 1024, 1),
                    }
                    for p, t, _, m, c in notes[:limit]
                ],
            }
        by_folder: dict[str, list[Any]] = {}
        for f in files:
            by_folder.setdefault(f[2], []).append(f)
        return {
            "indexed_notes": len(files),
            "indexed_sections": chunk_count,
            "folders": [
                {
                    "folder": name,
                    "notes": len(items),
                    "recent": [t for _, t, _, _, _ in sorted(items, key=lambda f: -f[3])[:5]],
                }
                for name, items in sorted(by_folder.items(), key=lambda kv: -len(kv[1]))
            ],
            "hint": "search_vault finds sections; vault_overview(folder=...) lists one folder.",
        }

    def person_context(self, name: str, max_chars: int = 2500) -> dict[str, Any]:
        """Person note + notes linking to them + their latest mentions."""
        self.refresh()
        with self._connect() as conn:
            people = conn.execute(
                "SELECT path FROM files WHERE path LIKE ?", (f"{self.people_folder}/%" if self.people_folder else "%",)
            ).fetchall()
            norm = normalize(name)
            person_path = next(
                (p for (p,) in people if norm and norm in normalize(re.sub(r"\(.*?\)", "", Path(p).stem))),
                None,
            )
            stem = normalize(Path(person_path).stem) if person_path else norm
            backlinks = [
                {"note": p, "title": t}
                for p, t, links in conn.execute("SELECT path, title, links FROM files")
                if stem in json.loads(links or "[]") and p != person_path
            ]
        words = normalize(re.sub(r"\(.*?\)", "", Path(person_path).stem if person_path else name)).split()
        phrase = " ".join(words[:2])
        mentions: list[dict[str, Any]] = []
        if phrase:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT path, heading, date, snippet(chunks, 3, '**', '**', ' … ', 24) "
                    "FROM chunks WHERE chunks MATCH ? ORDER BY date DESC LIMIT 8",
                    (f'"{phrase}"',),
                ).fetchall()
            mentions = [
                {"note": p, "section": h, "date": d, "snippet": re.sub(r"\s+", " ", s)}
                for p, h, d, s in rows
                if p != person_path
            ]
        note = self.read(person_path, max_chars=max_chars) if person_path else None
        return {
            "name": name,
            "person_note": note,
            "linked_from": backlinks[:20],
            "recent_mentions": mentions,
        }


def vec_blob(vector: list[float]) -> bytes:
    import numpy as np

    return np.asarray(vector, dtype=np.float32).tobytes()


def section_key(title: str, heading: str, body: str) -> tuple[str, str, str]:
    """Identity of a section's content, so copies (vault note vs. repo doc) show up once."""
    digest = hashlib.sha1(normalize(body)[:2000].encode()).hexdigest()[:12]
    return normalize(title), normalize(heading), digest


def _also_in(result: dict[str, Any], path: str) -> None:
    if path != result["note"] and path not in result.setdefault("also_in", []):
        result["also_in"].append(path)


def best_snippet(body: str, terms: list[str], width: int = 220) -> str:
    """Window of the section around the first query word (or its start)."""
    flat = re.sub(r"\s+", " ", body).strip()
    norm = normalize(flat)
    pos = min((norm.find(t) for t in terms if norm.find(t) >= 0), default=-1)
    if pos < 0 or len(flat) <= width:
        return flat[:width] + ("…" if len(flat) > width else "")
    start = max(0, pos - width // 3)
    return ("… " if start else "") + flat[start : start + width] + "…"


class Embedder:
    """OpenAI embeddings for the vault (text-embedding-3-small by default)."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        from openai import AsyncOpenAI

        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, timeout=60)
        self.tokens_used = 0

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(model=self.model, input=texts)
        self.tokens_used += getattr(response.usage, "total_tokens", 0) or 0
        return [item.embedding for item in response.data]


def build_embedder(config: Any) -> Embedder | None:
    """Embedder from the stored OpenAI key, or None when not available."""
    from executive_copilot.admin.credentials import CredentialManager

    cred = CredentialManager(Path(config.config_dir) / ".credentials").get_credential(
        config.ai.credential_provider_type, config.ai.effective_credential_name
    )
    key = (cred or {}).get("api_key")
    if not key:
        return None
    return Embedder(key, config.ai.embedding_model)


class VaultEditError(ValueError):
    """An edit could not be applied unambiguously."""


def _section_bounds(lines: list[str], section: str | None) -> tuple[int, int]:
    """[start, end) line range of a section (whole note when section is None)."""
    if not section:
        return 0, len(lines)
    wanted = normalize(section)
    matches = [i for i, line in enumerate(lines) if HEADING.match(line) and wanted in normalize(line)]
    if not matches:
        raise VaultEditError(f"No section matches '{section}'")
    if len(matches) > 1:
        exact = [i for i in matches if normalize(HEADING.match(lines[i]).group(2)) == wanted]  # type: ignore[union-attr]
        if len(exact) != 1:
            names = [lines[i].strip() for i in matches[:6]]
            raise VaultEditError(f"Section '{section}' is ambiguous: {names}")
        matches = exact
    start = matches[0]
    level = len(HEADING.match(lines[start]).group(1))  # type: ignore[union-attr]
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = HEADING.match(lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    return start, end


def apply_edit(
    text: str,
    action: str,
    section: str | None = None,
    find: str | None = None,
    new_text: str | None = None,
) -> str:
    """Pure edit used by VaultIndex.edit (unit tested).

    Actions:
        check / uncheck: toggle the one checkbox in the section whose text contains ``find``.
        append: add ``new_text`` at the end of the section (or note).
        replace: replace the single occurrence of ``find`` in the section with ``new_text``.
    """
    lines = text.split("\n")
    start, end = _section_bounds(lines, section)

    if action in ("check", "uncheck"):
        if not find:
            raise VaultEditError("'find' must name the checkbox to change")
        wanted = normalize(find)
        hits = [
            i for i in range(start, end)
            if re.match(r"^\s*[-*] \[[ xX]\]", lines[i]) and wanted in normalize(lines[i])
        ]
        if len(hits) != 1:
            raise VaultEditError(f"Expected 1 checkbox matching '{find}', found {len(hits)}")
        mark = "[x]" if action == "check" else "[ ]"
        lines[hits[0]] = re.sub(r"\[[ xX]\]", mark, lines[hits[0]], count=1)
        return "\n".join(lines)

    if action == "append":
        if not new_text or not new_text.strip():
            raise VaultEditError("'text' is required to append")
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        block = new_text.rstrip("\n").split("\n")
        lines[insert_at:insert_at] = block + ([""] if insert_at < len(lines) else [])
        return "\n".join(lines)

    if action == "replace":
        if not find or new_text is None:
            raise VaultEditError("'find' and 'text' are required to replace")
        scope = "\n".join(lines[start:end])
        count = scope.count(find)
        if count != 1:
            raise VaultEditError(f"Expected 'find' exactly once in the section, found {count}")
        lines[start:end] = scope.replace(find, new_text).split("\n")
        return "\n".join(lines)

    raise VaultEditError(f"Unknown action '{action}' (use check, uncheck, append or replace)")


def build_index(config: Any) -> VaultIndex:
    """VaultIndex from config; raises ValueError when the vault is not usable."""
    obs = config.obsidian
    if not obs.enabled or not obs.vault_path:
        raise ValueError("Obsidian vault not configured (OBSIDIAN_VAULT_PATH)")
    vault = Path(obs.vault_path)
    if not vault.is_dir():
        raise ValueError(f"Obsidian vault not found at {vault}")
    folders = obs.index_folders or [obs.meetings_folder.split("/")[0]]
    exclude = list(obs.index_exclude)
    transcripts = getattr(obs, "transcripts_folder", None)
    if transcripts and not getattr(obs, "index_transcripts", False):
        # Verbatim transcripts are long and noisy: searchable only when asked for.
        exclude.append(f"{obs.meetings_folder.strip('/')}/{transcripts.strip('/')}/*")
    extra: dict[str, Path] = {}
    github = getattr(config, "github_docs", None)
    if github is not None and github.enabled:
        extra[GITHUB_PREFIX] = Path(github.cache_dir)
    return VaultIndex(
        vault,
        folders,
        Path(obs.index_path),
        exclude=exclude,
        people_folder=obs.people_folder,
        extra_roots=extra,
    )
