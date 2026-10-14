"""Run only against a disposable MySQL database supplied by the test runner."""

import os

import pytest
from sqlalchemy import text

from executive_copilot.services.knowledge.documents import parse_document
from executive_copilot.services.knowledge.repository import KnowledgeRepository


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mysql_fulltext_rebuild_and_operational_state() -> None:
    url = os.environ.get("KNOWLEDGE_TEST_MYSQL_URL")
    if not url:
        pytest.skip("Set KNOWLEDGE_TEST_MYSQL_URL to an isolated empty test database")
    repo = KnowledgeRepository(url)
    try:
        await repo.initialize()
        async with repo.engine.connect() as conn:
            version = (await conn.execute(text("SELECT VERSION()"))).scalar_one()
            assert version
        await repo.put_state("proposal:rebuild-test", {"status": "approved"})
        documents = [
            parse_document(
                "A.md", "---\nid: a\ntags: [filosofia]\n---\nResponsabilidad personal [[B]]"
            ),
            parse_document("B.md", "---\nid: b\ntype: concept\n---\nAutodominio"),
        ]
        await repo.rebuild(documents)
        assert "a" in await repo.fulltext("Responsabilidad")
        assert "a" in await repo.fulltext("filosofia")
        assert len(await repo.edges()) == 1
        await repo.rebuild(documents)
        assert len(await repo.documents()) == 2
        assert (await repo.get_state("proposal:rebuild-test"))["status"] == "approved"
        await repo.rebuild([])
        assert not await repo.documents()
        await repo.rebuild(documents)
        assert "b" in await repo.fulltext("Autodominio")
    finally:
        await repo.close()
