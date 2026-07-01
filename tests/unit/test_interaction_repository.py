"""Tests for InteractionRepository."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from executive_copilot.database.repositories import InteractionRepository
from executive_copilot.models.relationship import (
    Interaction,
    InteractionDirection,
    InteractionType,
)


@pytest.fixture
def sample_interaction() -> Interaction:
    """Create a sample interaction for testing."""
    return Interaction(
        id=uuid4(),
        created_at=datetime.now(UTC),
        person_email="john@example.com",
        person_name="John Doe",
        interaction_type=InteractionType.EMAIL_SENT,
        direction=InteractionDirection.OUTBOUND,
        occurred_at=datetime.now(UTC) - timedelta(days=2),
        subject="Project Update",
        snippet="Here's the latest update on the project...",
        source_type="email",
        source_id=uuid4(),
        account_id="me@example.com",
    )


@pytest.mark.asyncio
async def test_create_interaction(db_session, sample_interaction):
    """Test creating an interaction."""
    repo = InteractionRepository(db_session)

    created = await repo.create(sample_interaction)
    await db_session.commit()

    assert created.id == sample_interaction.id
    assert created.person_email == "john@example.com"
    assert created.interaction_type == InteractionType.EMAIL_SENT


@pytest.mark.asyncio
async def test_get_person_history(db_session):
    """Test getting interaction history for a person."""
    repo = InteractionRepository(db_session)

    for i in range(5):
        interaction = Interaction(
            id=uuid4(),
            created_at=datetime.now(UTC),
            person_email="sarah@example.com",
            interaction_type=InteractionType.EMAIL_RECEIVED,
            direction=InteractionDirection.INBOUND,
            occurred_at=datetime.now(UTC) - timedelta(days=i),
            subject=f"Email {i}",
        )
        await repo.create(interaction)

    await db_session.commit()

    history = await repo.get_person_history("sarah@example.com", limit=3)

    assert len(history) == 3
    assert history[0].subject == "Email 0"


@pytest.mark.asyncio
async def test_get_last_interaction(db_session):
    """Test getting the most recent interaction."""
    repo = InteractionRepository(db_session)

    old = Interaction(
        id=uuid4(),
        created_at=datetime.now(UTC),
        person_email="mike@example.com",
        interaction_type=InteractionType.EMAIL_SENT,
        direction=InteractionDirection.OUTBOUND,
        occurred_at=datetime.now(UTC) - timedelta(days=10),
        subject="Old email",
    )

    recent = Interaction(
        id=uuid4(),
        created_at=datetime.now(UTC),
        person_email="mike@example.com",
        interaction_type=InteractionType.MEETING,
        direction=InteractionDirection.MUTUAL,
        occurred_at=datetime.now(UTC) - timedelta(days=1),
        subject="Recent meeting",
    )

    await repo.create(old)
    await repo.create(recent)
    await db_session.commit()

    last = await repo.get_last_interaction("mike@example.com")

    assert last is not None
    assert last.subject == "Recent meeting"
    assert last.interaction_type == InteractionType.MEETING


@pytest.mark.asyncio
async def test_count_interactions(db_session):
    """Test counting interactions."""
    repo = InteractionRepository(db_session)

    for i in range(3):
        await repo.create(Interaction(
            id=uuid4(),
            created_at=datetime.now(UTC),
            person_email="count@example.com",
            interaction_type=InteractionType.EMAIL_SENT,
            direction=InteractionDirection.OUTBOUND,
            occurred_at=datetime.now(UTC) - timedelta(days=i),
        ))

    for i in range(2):
        await repo.create(Interaction(
            id=uuid4(),
            created_at=datetime.now(UTC),
            person_email="count@example.com",
            interaction_type=InteractionType.EMAIL_RECEIVED,
            direction=InteractionDirection.INBOUND,
            occurred_at=datetime.now(UTC) - timedelta(days=i),
        ))

    await db_session.commit()

    total = await repo.count_interactions("count@example.com")
    outbound = await repo.count_interactions(
        "count@example.com", InteractionDirection.OUTBOUND
    )
    inbound = await repo.count_interactions(
        "count@example.com", InteractionDirection.INBOUND
    )

    assert total == 5
    assert outbound == 3
    assert inbound == 2


@pytest.mark.asyncio
async def test_exists_for_source(db_session, sample_interaction):
    """Test checking for existing interaction from source."""
    repo = InteractionRepository(db_session)

    await repo.create(sample_interaction)
    await db_session.commit()

    exists = await repo.exists_for_source(
        source_type="email",
        source_id=sample_interaction.source_id,
        person_email=sample_interaction.person_email,
    )
    assert exists is True

    not_exists = await repo.exists_for_source(
        source_type="email",
        source_id=uuid4(),
        person_email=sample_interaction.person_email,
    )
    assert not_exists is False
