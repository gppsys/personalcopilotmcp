"""Tests for repositories."""

from datetime import datetime
from uuid import uuid4

import pytest

from executive_copilot.database.repositories import OrganizationRepository, PersonRepository
from executive_copilot.models import Organization, OrganizationType, Person
from executive_copilot.models.organization import OrganizationDomains
from executive_copilot.models.person import ContactInfo


@pytest.mark.asyncio
class TestPersonRepository:
    """Tests for PersonRepository."""

    async def test_add_and_get(self, db_session) -> None:
        repo = PersonRepository(db_session)

        person = Person(
            id=uuid4(),
            created_at=datetime.utcnow(),
            first_name="John",
            last_name="Doe",
            contact=ContactInfo(email="john@example.com"),
        )

        added = await repo.add(person)
        assert added.id == person.id

        retrieved = await repo.get_by_id(person.id)
        assert retrieved is not None
        assert retrieved.first_name == "John"
        assert retrieved.last_name == "Doe"

    async def test_search_by_name(self, db_session) -> None:
        repo = PersonRepository(db_session)

        # Add test persons
        person1 = Person(
            id=uuid4(),
            created_at=datetime.utcnow(),
            first_name="John",
            last_name="Smith",
        )
        person2 = Person(
            id=uuid4(),
            created_at=datetime.utcnow(),
            first_name="Jane",
            last_name="Johnson",
        )

        await repo.add(person1)
        await repo.add(person2)
        await db_session.commit()

        results = await repo.search_by_name("John")
        assert len(results) >= 1
        assert any(p.first_name == "John" for p in results)

    async def test_delete(self, db_session) -> None:
        repo = PersonRepository(db_session)

        person = Person(
            id=uuid4(),
            created_at=datetime.utcnow(),
            first_name="ToDelete",
        )

        await repo.add(person)
        await db_session.commit()

        deleted = await repo.delete(person.id)
        assert deleted

        retrieved = await repo.get_by_id(person.id)
        assert retrieved is None


@pytest.mark.asyncio
class TestOrganizationRepository:
    """Tests for OrganizationRepository."""

    async def test_add_and_get(self, db_session) -> None:
        repo = OrganizationRepository(db_session)

        org = Organization(
            id=uuid4(),
            created_at=datetime.utcnow(),
            name="Test Organization",
            organization_type=OrganizationType.CLIENT,
            domains=OrganizationDomains(primary="test.com"),
        )

        added = await repo.add(org)
        assert added.id == org.id

        retrieved = await repo.get_by_id(org.id)
        assert retrieved is not None
        assert retrieved.name == "Test Organization"

    async def test_find_by_domain(self, db_session) -> None:
        repo = OrganizationRepository(db_session)

        org = Organization(
            id=uuid4(),
            created_at=datetime.utcnow(),
            name="Domain Test Org",
            organization_type=OrganizationType.CLIENT,
            domains=OrganizationDomains(
                primary="primary.com",
                aliases=["alias.com"],
            ),
        )

        await repo.add(org)
        await db_session.commit()

        # Find by primary domain
        found = await repo.find_by_domain("primary.com")
        assert found is not None
        assert found.name == "Domain Test Org"

        # Find by alias domain
        found_alias = await repo.find_by_domain("alias.com")
        assert found_alias is not None

    async def test_get_by_type(self, db_session) -> None:
        repo = OrganizationRepository(db_session)

        client_org = Organization(
            id=uuid4(),
            created_at=datetime.utcnow(),
            name="Client Org",
            organization_type=OrganizationType.CLIENT,
        )
        vendor_org = Organization(
            id=uuid4(),
            created_at=datetime.utcnow(),
            name="Vendor Org",
            organization_type=OrganizationType.VENDOR,
        )

        await repo.add(client_org)
        await repo.add(vendor_org)
        await db_session.commit()

        clients = await repo.get_by_type(OrganizationType.CLIENT)
        assert any(o.name == "Client Org" for o in clients)
        assert not any(o.name == "Vendor Org" for o in clients)
