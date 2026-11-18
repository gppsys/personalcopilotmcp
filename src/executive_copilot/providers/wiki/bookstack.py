"""BookStack provider: books, pages, search and page comments (REST API)."""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

import httpx

from executive_copilot.logging import get_logger

if TYPE_CHECKING:
    from executive_copilot.credentials import CredentialManager

logger = get_logger(__name__)


@dataclass(frozen=True)
class WikiBook:
    id: int
    name: str
    description: str
    updated_at: str | None


@dataclass(frozen=True)
class WikiNode:
    """A chapter or page inside a book (pages of a chapter are its children)."""

    type: str  # "chapter" | "page"
    id: int
    name: str
    draft: bool = False
    children: tuple[WikiNode, ...] = ()


@dataclass(frozen=True)
class WikiSearchHit:
    type: str  # bookshelf | book | chapter | page
    id: int
    name: str
    url: str
    snippet: str
    book: str | None
    chapter: str | None
    updated_at: str | None


@dataclass(frozen=True)
class WikiComment:
    id: int
    local_id: int | None
    parent_id: int | None
    author: str
    text: str
    created_at: str | None
    archived: bool


@dataclass(frozen=True)
class WikiPage:
    id: int
    name: str
    book_id: int | None
    chapter_id: int | None
    url: str
    markdown: str
    updated_at: str | None
    updated_by: str | None
    tags: tuple[str, ...]
    comments: tuple[WikiComment, ...]


class BookStackProvider:
    """BookStack REST client authenticated with an API token ("token_id:token_secret")."""

    def __init__(self, credential_manager: CredentialManager, base_url: str, account_id: str = "default") -> None:
        self.credential_manager = credential_manager
        self.base_url = (base_url or "").rstrip("/")
        self.account_id = account_id

    def _token(self) -> str:
        cred = self.credential_manager.get_credential("wiki", "bookstack", self.account_id) or {}
        token = str(cred.get("access_token") or "").strip()
        if token.lower().startswith("token "):
            token = token[6:].strip()
        if ":" not in token:
            raise ValueError("No wiki token: add it in Cuentas as token_id:token_secret (BookStack API token).")
        return token

    def _client(self) -> httpx.AsyncClient:
        if not self.base_url:
            raise ValueError("wiki.base_url is not configured")
        return httpx.AsyncClient(
            base_url=f"{self.base_url}/api",
            headers={"Authorization": f"Token {self._token()}", "Accept": "application/json"},
            timeout=httpx.Timeout(10.0, read=30.0),
        )

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get(path, params=params)
            _raise(response)
            data: dict[str, Any] = response.json()
            return data

    async def count_books(self) -> int:
        """Cheap authenticated call (health check)."""
        return int((await self._get("/books", {"count": 1})).get("total") or 0)

    async def list_books(self) -> list[WikiBook]:
        books: list[WikiBook] = []
        offset = 0
        while True:
            data = await self._get("/books", {"count": 500, "offset": offset, "sort": "+name"})
            batch = data.get("data") or []
            books.extend(
                WikiBook(
                    id=int(b["id"]),
                    name=b.get("name") or "",
                    description=(b.get("description") or "").strip(),
                    updated_at=b.get("updated_at"),
                )
                for b in batch
            )
            offset += len(batch)
            if not batch or offset >= int(data.get("total") or 0):
                return books

    async def get_book(self, book_id: int) -> tuple[WikiBook, list[WikiNode]]:
        """A book and its tree of chapters and pages."""
        data = await self._get(f"/books/{book_id}")
        book = WikiBook(
            id=int(data["id"]),
            name=data.get("name") or "",
            description=(data.get("description") or "").strip(),
            updated_at=data.get("updated_at"),
        )
        return book, [_node(item) for item in data.get("contents") or []]

    async def search(self, query: str, count: int = 10) -> tuple[int, list[WikiSearchHit]]:
        data = await self._get("/search", {"query": query, "count": count, "page": 1})
        hits = [
            WikiSearchHit(
                type=item.get("type") or "",
                id=int(item["id"]),
                name=item.get("name") or "",
                url=item.get("url") or "",
                snippet=_plain((item.get("preview_html") or {}).get("content") or ""),
                book=(item.get("book") or {}).get("name"),
                chapter=(item.get("chapter") or {}).get("name"),
                updated_at=item.get("updated_at"),
            )
            for item in data.get("data") or []
        ]
        return int(data.get("total") or len(hits)), hits

    async def get_page(self, page_id: int) -> WikiPage:
        data = await self._get(f"/pages/{page_id}")
        markdown = (data.get("markdown") or "").strip() or html_to_markdown(data.get("html") or "")
        markdown = _EMPTY_ANCHOR.sub("", markdown).strip()
        book_slug, slug = data.get("book_slug"), data.get("slug")
        # Page reads carry no book slug on current versions: fall back to the permalink.
        url = (
            f"{self.base_url}/books/{book_slug}/page/{slug}"
            if book_slug and slug
            else f"{self.base_url}/link/{data['id']}"
        )
        return WikiPage(
            id=int(data["id"]),
            name=data.get("name") or "",
            book_id=data.get("book_id"),
            chapter_id=data.get("chapter_id") or None,
            url=url,
            markdown=markdown,
            updated_at=data.get("updated_at"),
            updated_by=(data.get("updated_by") or {}).get("name") if isinstance(data.get("updated_by"), dict) else None,
            tags=tuple(
                f"{t.get('name')}={t.get('value')}" if t.get("value") else str(t.get("name"))
                for t in data.get("tags") or []
            ),
            comments=tuple(_comments(data.get("comments"))),
        )

    async def create_comment(self, page_id: int, html: str, reply_to: int | None = None) -> WikiComment:
        """Post a comment on a page; reply_to is the local_id of the parent comment."""
        body: dict[str, Any] = {"page_id": page_id, "html": html}
        if reply_to is not None:
            body["reply_to"] = reply_to
        async with self._client() as client:
            response = await client.post("/comments", json=body)
            _raise(response)
            return _comment(response.json(), archived=False)


def _raise(response: httpx.Response) -> None:
    """Surface BookStack's own error message instead of a bare status code."""
    if response.is_success:
        return
    try:
        message = response.json()["error"]["message"]
    except Exception:
        message = response.text[:200]
    raise ValueError(f"BookStack {response.status_code}: {message}")


def _node(item: dict[str, Any]) -> WikiNode:
    return WikiNode(
        type=item.get("type") or "page",
        id=int(item["id"]),
        name=item.get("name") or "",
        draft=bool(item.get("draft")),
        children=tuple(_node({**p, "type": "page"}) for p in item.get("pages") or []),
    )


def _comment(item: dict[str, Any], archived: bool) -> WikiComment:
    author = item.get("created_by")
    return WikiComment(
        id=int(item["id"]),
        local_id=item.get("local_id"),
        parent_id=item.get("parent_id") or None,
        author=(author.get("name") if isinstance(author, dict) else None) or "?",
        text=_plain(item.get("html") or ""),
        created_at=item.get("created_at"),
        archived=bool(item.get("archived", archived)),
    )


def _comments(raw: Any) -> list[WikiComment]:
    """Flatten the comment tree of a page read ({active: [...], archived: [...]})."""
    out: list[WikiComment] = []

    def walk(nodes: Any, archived: bool) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            inner = node.get("comment")
            item: dict[str, Any] = inner if isinstance(inner, dict) else node
            if item.get("id") is not None:
                out.append(_comment(item, archived))
            walk(node.get("children") or node.get("replies"), archived)

    if isinstance(raw, dict):
        walk(raw.get("active"), False)
        walk(raw.get("archived"), True)
    else:
        walk(raw, False)
    return out


def _plain(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


_EMPTY_ANCHOR = re.compile(r"<a\s+(?:id|name)=\"[^\"]*\">\s*</a>\n*")
_BLOCK = {"p", "div", "section", "article", "ul", "ol", "table", "blockquote", "details", "hr"}
_SKIP = {"script", "style", "svg", "head"}


class _MarkdownWriter(HTMLParser):
    """Small HTML -> Markdown converter: headings, lists, links, code and tables as text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.pre = 0
        self.lists: list[list[Any]] = []
        self.href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
            self.parts.append("\n")
        elif tag == "li":
            depth = max(0, len(self.lists) - 1)
            marker = "-"
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                marker = f"{self.lists[-1][1]}."
            self.parts.append("\n" + "  " * depth + marker + " ")
        elif tag == "pre":
            self.pre += 1
            self.parts.append("\n\n```\n")
        elif tag == "code" and not self.pre:
            self.parts.append("`")
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("br", "tr"):
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("| ")
        elif tag == "a":
            self.href = dict(attrs).get("href")
            self.parts.append("[")
        elif tag in _BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self.parts.append("\n")
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.parts.append("\n```\n\n")
        elif tag == "code" and not self.pre:
            self.parts.append("`")
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("td", "th"):
            self.parts.append(" ")
        elif tag == "a":
            href, self.href = self.href, None
            self.parts.append(f"]({href})" if href and not href.startswith("#") else "]")
        elif tag in _BLOCK or re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        self.parts.append(data if self.pre else re.sub(r"\s+", " ", data))


def html_to_markdown(html: str) -> str:
    writer = _MarkdownWriter()
    writer.feed(html)
    writer.close()
    text = "".join(writer.parts)
    text = re.sub(r"\[\s*\]", "", text)  # empty anchors
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+(?=#)", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
