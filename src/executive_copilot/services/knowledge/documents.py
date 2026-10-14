"""Lossless Markdown parsing and Obsidian relationship extraction."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

import yaml

CATEGORIES = {"fact", "reflection", "inference", "hypothesis", "decision", "event", "concept"}
RELATIONS = {
    "related_to",
    "supports",
    "contradicts",
    "evolves_from",
    "references",
    "influenced_by",
    "part_of",
}
FRONTMATTER = re.compile(r"\A(?:\ufeff)?---\r?\n(?:(.*?)\r?\n)?---(?:\r?\n|\Z)", re.DOTALL)
WIKILINK = re.compile(r"!?\[\[([^\]\n]+)\]\]")


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_path(path: str) -> str:
    """Allow only portable relative Markdown paths, without hidden components."""
    parts = PurePosixPath(path).parts
    if (
        not parts
        or len(path) > 512
        or path != str(PurePosixPath(path))
        or path.startswith("/")
        or "\\" in path
        or any(p in {".", ".."} or p.startswith(".") for p in parts)
        or any(ord(c) < 32 for c in path)
        or not path.lower().endswith(".md")
        or any(c in path for c in (":", "\x7f"))
    ):
        raise ValueError("Invalid relative Markdown path")
    return path


def strings(value: Any) -> tuple[str, ...]:
    """Scalars become strings; one nested level is flattened (unquoted ``[[X]]`` in YAML)."""
    if value is None:
        return ()
    if isinstance(value, list):
        items = [v for item in value for v in (item if isinstance(item, list) else [item])]
    else:
        items = [value]
    if any(isinstance(v, (dict, list)) for v in items):
        raise ValueError("Expected a string or a list of strings")
    return tuple(str(v) for v in items if v is not None)


def link_target(value: str) -> str:
    value = value.strip().removeprefix("[[").removesuffix("]]")
    return value.split("|", 1)[0].split("#", 1)[0].strip().removesuffix(".md")


@dataclass(frozen=True)
class Relationship:
    target: str
    kind: str
    origin: str
    explicit: bool = True


@dataclass(frozen=True)
class Document:
    id: str
    path: str
    title: str
    content: str
    body: str
    metadata: dict[str, Any]
    categories: tuple[str, ...]
    tags: tuple[str, ...]
    relationships: tuple[Relationship, ...]
    hash: str


def parse_document(path: str, content: str) -> Document:
    validate_path(path)
    match = FRONTMATTER.match(content)
    metadata: dict[str, Any] = {}
    if content.lstrip("\ufeff").startswith(("---\n", "---\r\n")) and not match:
        raise ValueError("Unclosed YAML frontmatter")
    if match:
        try:
            loaded = yaml.safe_load(match.group(1) or "")
        except yaml.YAMLError as exc:
            raise ValueError("Invalid YAML frontmatter") from exc
        if loaded is not None and not isinstance(loaded, dict):
            raise ValueError("Frontmatter must be a mapping")
        metadata = loaded or {}
    body = content[match.end() :] if match else content
    # Other ``type`` values (existing vault conventions) stay as plain metadata.
    categories = tuple(c for c in strings(metadata.get("type")) if c in CATEGORIES)
    tags = strings(metadata.get("tags"))
    title = str(metadata.get("title") or PurePosixPath(path).stem)
    identity = str(metadata.get("id") or "path:" + digest(path))
    if not identity or len(identity) > 191 or len(title) > 512:
        raise ValueError("Document identity or title is too long")
    # Code examples are not document relations.
    prose = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", body)
    prose = re.sub(r"`[^`\n]*`", "", prose)
    relations = [
        Relationship(link_target(v)[:512], "references", "wikilink")
        for v in WIKILINK.findall(prose)
    ]
    relations.extend(
        Relationship(link_target(v)[:512], "related_to", "related")
        for v in strings(metadata.get("related"))
    )
    explicit_relations = metadata.get("relations") or []
    if not isinstance(explicit_relations, list):
        raise ValueError("relations must be a list")
    for item in explicit_relations:
        if (
            not isinstance(item, dict)
            or item.get("type") not in RELATIONS
            or not isinstance(item.get("target"), str)
        ):
            raise ValueError("Invalid relationship")
        origin = item.get("origin", "user")
        if origin not in {"user", "ai"}:
            raise ValueError("Invalid relationship origin")
        explicit = origin != "ai" or item.get("confirmed") is True
        relations.append(
            Relationship(link_target(item["target"])[:512], item["type"], origin, explicit)
        )
    return Document(
        identity,
        path,
        title,
        content,
        body,
        metadata,
        categories,
        tags,
        tuple(dict.fromkeys(r for r in relations if r.target)),
        digest(content),
    )


class SemanticSearchProvider(Protocol):
    """Optional future semantic backend; textual search never implements this."""

    async def index_document(self, document: Document) -> None: ...
    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...
    async def delete_document(self, document_id: str) -> None: ...


def preserve_metadata(path: str, original: str, updated: str) -> str:
    """Keep omitted frontmatter properties using their original YAML fragments.

    Explicit new values win, including null. Missing fields are preserved rather
    than silently removed by a client that submits only a document body.
    """
    previous = parse_document(path, original)
    proposed = parse_document(path, updated)
    old_match = FRONTMATTER.match(original)
    if old_match is None:
        return updated
    new_match = FRONTMATTER.match(updated)
    if new_match is None:
        return original[: old_match.end()] + updated.removeprefix("\ufeff")
    missing = set(previous.metadata) - set(proposed.metadata)
    if not missing:
        return updated
    raw_yaml = old_match.group(1) or ""
    tree = yaml.compose(raw_yaml)
    assert isinstance(tree, yaml.MappingNode)
    fragments = []
    for index, (key, _) in enumerate(tree.value):
        if key.value not in missing:
            continue
        end = (
            tree.value[index + 1][0].start_mark.index
            if index + 1 < len(tree.value)
            else len(raw_yaml)
        )
        fragments.append(raw_yaml[key.start_mark.index : end].rstrip("\r\n"))
    newline = "\r\n" if updated.startswith(("---\r\n", "\ufeff---\r\n")) else "\n"
    offset = updated.index("\n") + 1
    result = updated[:offset] + newline.join(fragments) + newline + updated[offset:]
    parse_document(path, result)
    return result
