"""Tests for MeetingOutcome repository."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from executive_copilot.database.repositories import (
    MeetingDecisionRepository,
    MeetingOutcomeRepository,
)
from executive_copilot.models.meeting import MeetingDecision, MeetingOutcome


@pytest.fixture
def sample_outcome() -> MeetingOutcome:
    """Create a sample meeting outcome."""
    return MeetingOutcome(
        id=uuid4(),
        created_at=datetime.now(UTC),
        event_id="evt_123",
        event_title="Q3 Planning Meeting",
        meeting_date=datetime.now(UTC) - timedelta(hours=2),
        participants=["alice@example.com", "bob@example.com"],
        organizer="alice@example.com",
        summary="Discussed Q3 priorities and resource allocation.",
        decisions=["Prioritize mobile app", "Hire 2 more engineers"],
        next_steps=["Alice to draft job descriptions", "Bob to update roadmap"],
        key_points=["Budget approved", "Timeline is tight"],
        recorded_at=datetime.now(UTC),
        tags=["planning", "q3"],
    )


@pytest.fixture
def sample_decision() -> MeetingDecision:
    """Create a sample meeting decision."""
    return MeetingDecision(
        id=uuid4(),
        created_at=datetime.now(UTC),
        event_id="evt_123",
        event_title="Q3 Planning Meeting",
        meeting_date=datetime.now(UTC) - timedelta(hours=2),
        decision="Prioritize mobile app development over web features",
        context="Mobile users represent 70% of engagement",
        owner="Alice",
        owner_email="alice@example.com",
        participants=["alice@example.com", "bob@example.com"],
        tags=["product", "mobile"],
    )


@pytest.mark.asyncio
async def test_create_outcome(db_session, sample_outcome):
    """Test creating a meeting outcome."""
    repo = MeetingOutcomeRepository(db_session)

    created = await repo.create(sample_outcome)
    await db_session.commit()

    assert created.id == sample_outcome.id
    assert created.event_title == "Q3 Planning Meeting"
    assert created.decision_count == 2
    assert created.next_step_count == 2


@pytest.mark.asyncio
async def test_get_by_event(db_session, sample_outcome):
    """Test getting outcome by event ID."""
    repo = MeetingOutcomeRepository(db_session)

    await repo.create(sample_outcome)
    await db_session.commit()

    outcome = await repo.get_by_event("evt_123")

    assert outcome is not None
    assert outcome.event_title == "Q3 Planning Meeting"


@pytest.mark.asyncio
async def test_create_decision(db_session, sample_decision):
    """Test creating a meeting decision."""
    repo = MeetingDecisionRepository(db_session)

    created = await repo.create(sample_decision)
    await db_session.commit()

    assert created.decision == "Prioritize mobile app development over web features"
    assert created.owner == "Alice"


@pytest.mark.asyncio
async def test_search_decisions(db_session):
    """Test searching decisions."""
    repo = MeetingDecisionRepository(db_session)

    d1 = MeetingDecision(
        id=uuid4(),
        created_at=datetime.now(UTC),
        event_id="evt_1",
        event_title="Product Meeting",
        meeting_date=datetime.now(UTC),
        decision="Launch mobile app by Q4",
    )

    d2 = MeetingDecision(
        id=uuid4(),
        created_at=datetime.now(UTC),
        event_id="evt_2",
        event_title="Budget Meeting",
        meeting_date=datetime.now(UTC),
        decision="Increase marketing budget by 20%",
    )

    await repo.create(d1)
    await repo.create(d2)
    await db_session.commit()

    results = await repo.search("mobile")
    assert len(results) == 1
    assert "mobile" in results[0].decision.lower()

    results = await repo.search("budget")
    assert len(results) == 1
    assert "budget" in results[0].decision.lower()


@pytest.mark.asyncio
async def test_get_decisions_by_event(db_session):
    """Test getting all decisions from a meeting."""
    repo = MeetingDecisionRepository(db_session)

    for i in range(3):
        d = MeetingDecision(
            id=uuid4(),
            created_at=datetime.now(UTC),
            event_id="evt_multi",
            event_title="Multi Decision Meeting",
            meeting_date=datetime.now(UTC),
            decision=f"Decision {i}",
        )
        await repo.create(d)

    await db_session.commit()

    decisions = await repo.get_by_event("evt_multi")
    assert len(decisions) == 3
