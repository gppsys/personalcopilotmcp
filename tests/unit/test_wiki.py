"""Tests for the BookStack wiki provider, service and MCP tools."""

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from executive_copilot.providers.wiki import bookstack
from executive_copilot.providers.wiki.bookstack import BookStackProvider, html_to_markdown
from executive_copilot.services.wiki import WikiService, read_section, sections, text_to_html

PAGE = {
    "id": 7,
    "name": "Proyecto Alfa",
    "slug": "proyecto-alfa",
    "book_id": 2,
    "book_slug": "proyectos",
    "chapter_id": 0,
    "markdown": "",
    "html": "<h1>Proyecto Alfa</h1><p>Intro con <a href=\"https://x.test\">liga</a>.</p>"
    "<h2>Alcance</h2><ul><li>Uno</li><li>Dos</li></ul><h2>Riesgos</h2><p>Banco &amp; tiempos</p>",
    "updated_at": "2026-11-01T10:00:00.000000Z",
    "updated_by": {"id": 1, "name": "Ana"},
    "tags": [{"name": "estado", "value": "activo"}],
    "comments": {
        "active": [
            {
                "comment": {"id": 11, "local_id": 1, "parent_id": None, "html": "<p>Falta el costo</p>",
                            "created_by": {"name": "Luis"}, "created_at": "2026-11-02T09:00:00.000000Z"},
                "children": [
                    {"comment": {"id": 12, "local_id": 2, "parent_id": 1, "html": "<p>Agregado</p>",
                                 "created_by": {"name": "Ana"}, "created_at": "2026-11-03T09:00:00.000000Z"},
                     "children": []}
                ],
            }
        ],
        "archived": [
            {"comment": {"id": 9, "local_id": 0, "html": "<p>viejo</p>", "created_by": {"name": "Ana"}}, "children": []}
        ],
    },
}


class FakeCredentials:
    def __init__(self, token: str | None = "abc:def") -> None:
        self.token = token

    def get_credential(self, ptype: str, pname: str, account: str) -> dict[str, Any] | None:
        assert (ptype, pname) == ("wiki", "bookstack")
        return {"access_token": self.token} if self.token else None


def _config(**kw: Any) -> SimpleNamespace:
    wiki = {"enabled": True, "base_url": "https://wiki.test/", "account": "default",
            "allow_comments": False, "max_chars": 6000} | kw
    return SimpleNamespace(wiki=SimpleNamespace(**wiki))


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Route the provider's HTTP calls to a fake BookStack."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if request.headers.get("Authorization") != "Token abc:def":
            return httpx.Response(401, json={"error": {"code": 401, "message": "bad token"}})
        if path == "/api/books":
            return httpx.Response(200, json={"data": [
                {"id": 2, "name": "Proyectos", "description": "Todos", "updated_at": "2026-10-01T00:00:00Z"}
            ], "total": 1})
        if path == "/api/books/2":
            return httpx.Response(200, json={"id": 2, "name": "Proyectos", "description": "Todos", "contents": [
                {"type": "chapter", "id": 5, "name": "2026", "pages": [{"id": 7, "name": "Proyecto Alfa"}]},
                {"type": "page", "id": 8, "name": "Índice", "draft": False},
            ]})
        if path == "/api/search":
            return httpx.Response(200, json={"total": 1, "data": [{
                "type": "page", "id": 7, "name": "Proyecto Alfa", "url": "https://wiki.test/books/proyectos/page/proyecto-alfa",
                "preview_html": {"name": "Proyecto <strong>Alfa</strong>", "content": "el <strong>alcance</strong> del&nbsp;proyecto"},
                "book": {"id": 2, "name": "Proyectos"}, "updated_at": "2026-11-01T10:00:00Z",
            }]})
        if path == "/api/pages/7":
            return httpx.Response(200, json=PAGE)
        if path == "/api/comments" and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(200, json={"id": 20, "local_id": 3, "parent_id": body.get("reply_to"),
                                             "html": body["html"], "created_by": {"name": "Sam"},
                                             "created_at": "2026-11-18T12:00:00Z"})
        return httpx.Response(404, json={"error": {"code": 404, "message": "not found"}})

    real = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(bookstack.httpx, "AsyncClient", client)
    return seen


def test_html_to_markdown() -> None:
    md = html_to_markdown(PAGE["html"])  # type: ignore[arg-type]
    assert md.startswith("# Proyecto Alfa\n\nIntro con [liga](https://x.test).")
    assert "## Alcance\n\n- Uno\n- Dos" in md
    assert "Banco & tiempos" in md
    assert html_to_markdown("<script>x()</script><pre><code>a\n  b</code></pre>") == "```\na\n  b\n```"


def test_sections_and_text_to_html() -> None:
    md = "# A\nuno\n```\n# no\n```\n## B\ndos\n## C\ntres\n# D\nfin"
    assert sections(md) == ["A", "B", "C", "D"]
    assert read_section(md, "b") == "## B\ndos"
    assert read_section(md, "A").endswith("tres")
    with pytest.raises(ValueError, match="Section not found"):
        read_section(md, "zzz")
    assert text_to_html("hola <b>\nx\n\nfin") == "<p>hola &lt;b&gt;<br>x</p><p>fin</p>"


@pytest.mark.asyncio
async def test_overview_search_and_read(api: list[httpx.Request]) -> None:
    service = WikiService(_config(), FakeCredentials())  # type: ignore[arg-type]
    books = await service.overview()
    assert books["books"] == [{"id": 2, "name": "Proyectos", "description": "Todos", "updated": "2026-10-01"}]
    tree = await service.overview(2)
    assert tree["contents"][0] == {"type": "chapter", "id": 5, "name": "2026",
                                   "pages": [{"id": 7, "name": "Proyecto Alfa"}]}

    found = await service.search("alcance")
    assert found["results"][0]["snippet"] == "el alcance del proyecto"
    assert found["results"][0]["book"] == "Proyectos"

    page = await service.read_page(7)
    assert page["url"] == "https://wiki.test/books/proyectos/page/proyecto-alfa"
    assert page["updated_by"] == "Ana" and page["tags"] == ["estado=activo"]
    assert page["truncated"] is False and "sections" not in page
    assert [c["text"] for c in page["comments"]] == ["Falta el costo", "Agregado"]  # archived left out
    assert page["comments"][1]["reply_to"] == 1

    part = await service.read_page(7, section="riesgos", comments=False)
    assert part["content"] == "## Riesgos\n\nBanco & tiempos" and "comments" not in part

    short = await service.read_page(7, max_chars=500)
    assert short["truncated"] is False
    assert str(api[0].url).startswith("https://wiki.test/api/books")


@pytest.mark.asyncio
async def test_page_without_book_slug_uses_permalink(api: list[httpx.Request], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PAGE, "markdown", '<a id="x-1"></a>\n\n# Alfa\ntexto')
    monkeypatch.delitem(PAGE, "book_slug")
    page = await WikiService(_config(), FakeCredentials()).read_page(7, comments=False)  # type: ignore[arg-type]
    assert page["content"] == "# Alfa\ntexto"
    assert page["url"] == "https://wiki.test/link/7"


@pytest.mark.asyncio
async def test_comment_is_gated_and_escaped(api: list[httpx.Request]) -> None:
    with pytest.raises(ValueError, match="disabled"):
        await WikiService(_config(), FakeCredentials()).comment(7, "hola")  # type: ignore[arg-type]
    assert api == []

    service = WikiService(_config(allow_comments=True), FakeCredentials())  # type: ignore[arg-type]
    out = await service.comment(7, "Revisar <costos>", reply_to=1)
    assert json.loads(api[-1].content) == {"page_id": 7, "html": "<p>Revisar &lt;costos&gt;</p>", "reply_to": 1}
    assert out["comment"] == {"local_id": 3, "author": "Sam", "date": "2026-11-18",
                              "text": "Revisar <costos>", "reply_to": 1}
    with pytest.raises(ValueError, match="Empty"):
        await service.comment(7, "  ")


@pytest.mark.asyncio
async def test_errors(api: list[httpx.Request]) -> None:
    with pytest.raises(ValueError, match="token_id:token_secret"):
        await BookStackProvider(FakeCredentials(None), "https://wiki.test").count_books()  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="BookStack 401: bad token"):
        await BookStackProvider(FakeCredentials("x:y"), "https://wiki.test").count_books()  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not configured"):
        await WikiService(_config(enabled=False), FakeCredentials()).overview()  # type: ignore[arg-type]
    # "Token id:secret" pasted whole also works
    assert await BookStackProvider(FakeCredentials("Token abc:def"), "https://wiki.test").count_books() == 1  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mcp_tools(api: list[httpx.Request]) -> None:
    from fastmcp import FastMCP

    from executive_copilot.mcp.wiki import register_wiki_tools

    mcp = FastMCP("test")
    register_wiki_tools(mcp, _config(), FakeCredentials())
    names = {t.name for t in await mcp.list_tools()}
    assert {"wiki_overview", "wiki_search", "wiki_read_page", "wiki_comment"} <= names
    tool = await mcp.get_tool("wiki_comment")
    result = await tool.fn(page_id=7, text="hola")
    assert "disabled" in result["error"]
