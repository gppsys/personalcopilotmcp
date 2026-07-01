"""Tests for CommitmentRepository."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from executive_copilot.database.repositories import CommitmentRepository
from executive_copilot.models.communication import (
    Commitment,
    CommitmentDirection,
    CommitmentStatus,
)


@pytest.fixture
def sample_commitment() -> Commitment:
    """Create a sample commitment for testing."""
    return Commitment(
        id=uuid4(),
        created_at=datetime.now(UTC),
        title="Send Q3 report to finance team",
        description="Include updated projections",
        original_text="I'll send you the Q3 report by Monday",
        direction=CommitmentDirection.I_PROMISED,
        promiser_email="gonzalo@paycode.com.mx",
        promisee_name="Sarah",
        promisee_email="sarah@finance.com",
        status=CommitmentStatus.PENDING,
        due_date=datetime.now(UTC) + timedelta(days=3),
        source_type="email",
        auto_detected=True,
        detection_confidence=0.85,
        tags=["from-email", "paycode"],
    )


@pytest.fixture
def sample_they_promised() -> Commitment:
    """Create a sample they_promised commitment."""
    return Commitment(
        id=uuid4(),
        created_at=datetime.now(UTC),
        title="John to review proposal",
        description="Will provide feedback by Friday",
        original_text="I'll review it and get back to you Friday",
        direction=CommitmentDirection.THEY_PROMISED,
        promiser_name="John",
        promiser_email="john@example.com",
        promisee_email="gonzalo@paycode.com.mx",
        status=CommitmentStatus.PENDING,
        due_date=datetime.now(UTC) + timedelta(days=2),
        source_type="email",
        auto_detected=True,
        detection_confidence=0.75,
        tags=["from-email"],
    )


@pytest.mark.asyncio
async def test_create_commitment(db_session, sample_commitment):
    """Test creating a commitment."""
    repo = CommitmentRepository(db_session)

    created = await repo.create(sample_commitment)
    await db_session.commit()

    assert created.id == sample_commitment.id
    assert created.title == sample_commitment.title
    assert created.direction == CommitmentDirection.I_PROMISED


@pytest.mark.asyncio
async def test_get_my_commitments(db_session, sample_commitment, sample_they_promised):
    """Test getting commitments I made to others."""
    repo = CommitmentRepository(db_session)

    await repo.create(sample_commitment)
    await repo.create(sample_they_promised)
    await db_session.commit()

    my_commits = await repo.get_my_commitments()

    assert len(my_commits) == 1
    assert my_commits[0].direction == CommitmentDirection.I_PROMISED
    assert my_commits[0].title == sample_commitment.title


@pytest.mark.asyncio
async def test_get_others_commitments(db_session, sample_commitment, sample_they_promised):
    """Test getting commitments others made to me."""
    repo = CommitmentRepository(db_session)

    await repo.create(sample_commitment)
    await repo.create(sample_they_promised)
    await db_session.commit()

    others = await repo.get_others_commitments()

    assert len(others) == 1
    assert others[0].direction == CommitmentDirection.THEY_PROMISED
    assert others[0].promiser_name == "John"


@pytest.mark.asyncio
async def test_get_overdue(db_session):
    """Test getting overdue commitments."""
    repo = CommitmentRepository(db_session)

    overdue = Commitment(
        id=uuid4(),
        created_at=datetime.now(UTC),
        title="Overdue task",
        direction=CommitmentDirection.I_PROMISED,
        status=CommitmentStatus.PENDING,
        due_date=datetime.now(UTC) - timedelta(days=2),
    )

    not_overdue = Commitment(
        id=uuid4(),
        created_at=datetime.now(UTC),
        title="Future task",
        direction=CommitmentDirection.I_PROMISED,
        status=CommitmentStatus.PENDING,
        due_date=datetime.now(UTC) + timedelta(days=5),
    )

    await repo.create(overdue)
    await repo.create(not_overdue)
    await db_session.commit()

    overdue_list = await repo.get_overdue()

    assert len(overdue_list) == 1
    assert overdue_list[0].title == "Overdue task"


@pytest.mark.asyncio
async def test_exists_similar(db_session, sample_commitment):
    """Test checking for similar commitments."""
    repo = CommitmentRepository(db_session)

    await repo.create(sample_commitment)
    await db_session.commit()

    exists = await repo.exists_similar(
        title=sample_commitment.title,
        source_type="email",
        direction=CommitmentDirection.I_PROMISED,
    )
    assert exists is True

    not_exists = await repo.exists_similar(
        title="Different title",
        source_type="email",
        direction=CommitmentDirection.I_PROMISED,
    )
    assert not_exists is False


@pytest.mark.asyncio
async def test_get_by_person(db_session, sample_commitment, sample_they_promised):
    """Test getting commitments by person email."""
    repo = CommitmentRepository(db_session)

    await repo.create(sample_commitment)
    await repo.create(sample_they_promised)
    await db_session.commit()

    john_commits = await repo.get_by_person("john@example.com")
    assert len(john_commits) == 1
    assert john_commits[0].promiser_name == "John"


@pytest.mark.asyncio
async def test_search(db_session, sample_commitment, sample_they_promised):
    """Test searching commitments."""
    repo = CommitmentRepository(db_session)

    await repo.create(sample_commitment)
    await repo.create(sample_they_promised)
    await db_session.commit()

    results = await repo.search("Q3 report")
    assert len(results) == 1
    assert "Q3 report" in results[0].title

    results = await repo.search("proposal")
    assert len(results) == 1
    assert "proposal" in results[0].title
