"""Tests for Person model relationship tracking features."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from executive_copilot.database.repositories import PersonRepository
from executive_copilot.models import Person, PersonRole
from executive_copilot.models.person import ContactInfo


@pytest.fixture
def sample_person_with_contact() -> Person:
    """Create a person with recent contact."""
    return Person(
        id=uuid4(),
        created_at=datetime.now(UTC),
        first_name="Jane",
        last_name="Client",
        contact=ContactInfo(email="jane@client.com"),
        roles=[PersonRole.CLIENT],
        importance=8,
        last_contact_at=datetime.now(UTC) - timedelta(days=5),
        last_contact_type="email_sent",
        interaction_count=10,
    )


@pytest.fixture
def stale_person() -> Person:
    """Create a person with stale contact."""
    return Person(
        id=uuid4(),
        created_at=datetime.now(UTC),
        first_name="Old",
        last_name="Contact",
        contact=ContactInfo(email="old@contact.com"),
        roles=[PersonRole.PARTNER],
        importance=7,
        last_contact_at=datetime.now(UTC) - timedelta(days=45),
        last_contact_type="email_received",
        interaction_count=5,
    )


def test_days_since_contact(sample_person_with_contact):
    """Test days_since_contact property."""
    days = sample_person_with_contact.days_since_contact
    assert days is not None
    assert 4 <= days <= 6


def test_days_since_contact_none():
    """Test days_since_contact when no contact recorded."""
    person = Person(
        id=uuid4(),
        created_at=datetime.now(UTC),
        first_name="New",
        last_name="Person",
    )
    assert person.days_since_contact is None


def test_is_stale_false(sample_person_with_contact):
    """Test is_stale returns False for recent contact."""
    assert sample_person_with_contact.is_stale(threshold_days=30) is False


def test_is_stale_true(stale_person):
    """Test is_stale returns True for old contact."""
    assert stale_person.is_stale(threshold_days=30) is True


def test_is_stale_no_contact():
    """Test is_stale returns True when no contact recorded."""
    person = Person(
        id=uuid4(),
        created_at=datetime.now(UTC),
        first_name="Never",
        last_name="Contacted",
    )
    assert person.is_stale(threshold_days=30) is True


@pytest.mark.asyncio
async def test_get_stale_relationships(db_session, sample_person_with_contact, stale_person):
    """Test getting stale relationships from database."""
    repo = PersonRepository(db_session)

    await repo.create(sample_person_with_contact)
    await repo.create(stale_person)
    await db_session.commit()

    stale = await repo.get_stale_relationships(
        days_threshold=30,
        min_importance=5,
    )

    assert len(stale) == 1
    assert stale[0].first_name == "Old"


@pytest.mark.asyncio
async def test_update_last_contact(db_session, stale_person):
    """Test updating last contact for a person."""
    repo = PersonRepository(db_session)

    await repo.create(stale_person)
    await db_session.commit()

    new_contact_time = datetime.now(UTC)
    updated = await repo.update_last_contact(
        email="old@contact.com",
        contact_at=new_contact_time,
        contact_type="meeting",
    )

    assert updated is True

    person = await repo.find_by_email("old@contact.com")
    assert person is not None
    assert person.last_contact_type == "meeting"
    assert person.interaction_count == 6
