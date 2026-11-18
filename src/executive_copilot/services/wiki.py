"""Company wiki (BookStack): browse, search, read pages and comment on them.

Read live from the wiki's API; outputs are compact because they go to a model.
"""

from __future__ import annotations

import html as html_lib
import re
from typing import TYPE_CHECKING, Any

from executive_copilot.providers.wiki import BookStackProvider, WikiComment, WikiNode

if TYPE_CHECKING:
    from executive_copilot.credentials import CredentialManager

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
MAX_COMMENT_CHARS = 4000


class WikiService:
    def __init__(self, config: Any, credential_manager: CredentialManager) -> None:
        self.settings = config.wiki
        self.cm = credential_manager

    def _provider(self) -> BookStackProvider:
        if not self.settings.enabled or not self.settings.base_url:
            raise ValueError("The wiki is not configured (wiki.enabled / wiki.base_url)")
        return BookStackProvider(self.cm, self.settings.base_url, self.settings.account)

    async def overview(self, book_id: int | None = None) -> dict[str, Any]:
        """Every book, or the chapters and pages of one book."""
        provider = self._provider()
        if book_id is None:
            books = await provider.list_books()
            return {
                "total": len(books),
                "books": [
                    {"id": b.id, "name": b.name, "description": b.description[:140], "updated": _day(b.updated_at)}
                    for b in books
                ],
            }
        book, nodes = await provider.get_book(book_id)
        return {
            "book": {"id": book.id, "name": book.name, "description": book.description[:300]},
            "contents": [_tree(n) for n in nodes],
        }

    async def search(self, query: str, limit: int = 8) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("Empty query")
        total, hits = await self._provider().search(query, count=max(1, min(limit, 20)))
        return {
            "query": query,
            "total": total,
            "results": [
                {
                    "type": h.type,
                    "id": h.id,
                    "name": h.name,
                    **({"book": h.book} if h.book else {}),
                    **({"chapter": h.chapter} if h.chapter else {}),
                    "updated": _day(h.updated_at),
                    "snippet": h.snippet[:240],
                    "url": h.url,
                }
                for h in hits
            ],
        }

    async def read_page(
        self, page_id: int, section: str | None = None, max_chars: int | None = None, comments: bool = True
    ) -> dict[str, Any]:
        """A page as Markdown (or one section of it) with its comments."""
        page = await self._provider().get_page(page_id)
        cap = max(500, min(max_chars or self.settings.max_chars, 40000))
        headings = sections(page.markdown)
        content = page.markdown
        if section:
            content = read_section(page.markdown, section)
        truncated = len(content) > cap
        out: dict[str, Any] = {
            "id": page.id,
            "name": page.name,
            "url": page.url,
            "updated": _day(page.updated_at),
            **({"updated_by": page.updated_by} if page.updated_by else {}),
            **({"tags": list(page.tags)} if page.tags else {}),
            **({"section": section} if section else {}),
            "content": content[:cap],
            "truncated": truncated,
        }
        if truncated and not section and headings:
            out["sections"] = headings[:60]
        if comments and page.comments:
            out["comments"] = [_comment(c) for c in page.comments if not c.archived][-30:]
        return out

    async def comment(self, page_id: int, text: str, reply_to: int | None = None) -> dict[str, Any]:
        """Post a comment on a page (plain text, one paragraph per blank line)."""
        if not self.settings.allow_comments:
            raise ValueError("Commenting on the wiki is disabled (set wiki.allow_comments: true)")
        text = text.strip()
        if not text:
            raise ValueError("Empty comment")
        if len(text) > MAX_COMMENT_CHARS:
            raise ValueError(f"Comment too long (max {MAX_COMMENT_CHARS} characters)")
        provider = self._provider()
        created = await provider.create_comment(page_id, text_to_html(text), reply_to)
        return {"ok": True, "page_id": page_id, "comment": _comment(created)}


def sections(markdown: str) -> list[str]:
    """Headings of a Markdown document, outside code fences."""
    return [title for _, title, _ in _headings(markdown)]


def read_section(markdown: str, wanted: str) -> str:
    """Text under the first heading matching ``wanted``, up to the next heading of its level."""
    found = _headings(markdown)
    key = wanted.strip().lower()
    match = next((h for h in found if h[1].lower() == key), None) or next(
        (h for h in found if key in h[1].lower()), None
    )
    if match is None:
        raise ValueError(f"Section not found: {wanted}. Sections: {', '.join(t for _, t, _ in found[:40])}")
    lines = markdown.splitlines()
    level, _, start = match
    end = next((line for lvl, _, line in found if line > start and lvl <= level), len(lines))
    return "\n".join(lines[start:end]).strip()


def text_to_html(text: str) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "".join(f"<p>{html_lib.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)


def _headings(markdown: str) -> list[tuple[int, str, int]]:
    out: list[tuple[int, str, int]] = []
    fenced = False
    for number, line in enumerate(markdown.splitlines()):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        found = None if fenced else _HEADING.match(line)
        if found:
            out.append((len(found.group(1)), found.group(2), number))
    return out


def _tree(node: WikiNode) -> dict[str, Any]:
    out: dict[str, Any] = {"type": node.type, "id": node.id, "name": node.name}
    if node.draft:
        out["draft"] = True
    if node.children:
        out["pages"] = [{"id": c.id, "name": c.name} for c in node.children]
    return out


def _comment(c: WikiComment) -> dict[str, Any]:
    out: dict[str, Any] = {"local_id": c.local_id, "author": c.author, "date": _day(c.created_at), "text": c.text[:600]}
    if c.parent_id:
        out["reply_to"] = c.parent_id
    return out


def _day(value: str | None) -> str | None:
    return value[:10] if value else None
