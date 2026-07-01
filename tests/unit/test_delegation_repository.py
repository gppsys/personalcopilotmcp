"""Tests for DelegationRepository."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from executive_copilot.database.repositories import DelegationRepository
from executive_copilot.models.delegation import (
    Delegation,
    DelegationPriority,
    DelegationStatus,
)


@pytest.fixture
async def delegation_repo(db_session):
    """Create delegation repository."""
    return DelegationRepository(db_session)


def make_delegation(
    title: str = "Test delegation",
    assignee_email: str = "assignee@example.com",
    status: DelegationStatus = DelegationStatus.PENDING,
    priority: DelegationPriority = DelegationPriority.MEDIUM,
    due_date: datetime | None = None,
    delegated_at: datetime | None = None,
    **kwargs,
) -> Delegation:
    """Create a test delegation."""
    return Delegation(
        id=uuid4(),
        created_at=datetime.utcnow(),
        title=title,
        assignee_email=assignee_email,
        status=status,
        priority=priority,
        delegated_at=delegated_at or datetime.utcnow(),
        due_date=due_date,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_create_delegation(delegation_repo, db_session):
    """Test creating a delegation."""
    delegation = make_delegation(title="Review contract")
    created = await delegation_repo.create(delegation)
    await db_session.commit()

    assert created.id == delegation.id
    assert created.title == "Review contract"
    assert created.status == DelegationStatus.PENDING


@pytest.mark.asyncio
async def test_get_pending_delegations(delegation_repo, db_session):
    """Test getting pending delegations."""
    pending = make_delegation(title="Pending task", status=DelegationStatus.PENDING)
    in_progress = make_delegation(title="In progress", status=DelegationStatus.IN_PROGRESS)
    completed = make_delegation(title="Done", status=DelegationStatus.COMPLETED)

    await delegation_repo.create(pending)
    await delegation_repo.create(in_progress)
    await delegation_repo.create(completed)
    await db_session.commit()

    result = await delegation_repo.get_pending(include_in_progress=True)
    assert len(result) == 2
    titles = [d.title for d in result]
    assert "Pending task" in titles
    assert "In progress" in titles
    assert "Done" not in titles


@pytest.mark.asyncio
async def test_get_pending_excludes_in_progress(delegation_repo, db_session):
    """Test get_pending with include_in_progress=False."""
    pending = make_delegation(title="Pending", status=DelegationStatus.PENDING)
    in_progress = make_delegation(title="In progress", status=DelegationStatus.IN_PROGRESS)

    await delegation_repo.create(pending)
    await delegation_repo.create(in_progress)
    await db_session.commit()

    result = await delegation_repo.get_pending(include_in_progress=False)
    assert len(result) == 1
    assert result[0].title == "Pending"


@pytest.mark.asyncio
async def test_get_by_assignee(delegation_repo, db_session):
    """Test getting delegations by assignee."""
    d1 = make_delegation(title="For Sarah", assignee_email="sarah@example.com")
    d2 = make_delegation(title="For Mike", assignee_email="mike@example.com")
    d3 = make_delegation(title="Another for Sarah", assignee_email="sarah@example.com")

    await delegation_repo.create(d1)
    await delegation_repo.create(d2)
    await delegation_repo.create(d3)
    await db_session.commit()

    result = await delegation_repo.get_by_assignee("sarah@example.com")
    assert len(result) == 2
    assert all(d.assignee_email == "sarah@example.com" for d in result)


@pytest.mark.asyncio
async def test_get_by_assignee_case_insensitive(delegation_repo, db_session):
    """Test that assignee lookup is case-insensitive."""
    d = make_delegation(assignee_email="sarah@example.com")
    await delegation_repo.create(d)
    await db_session.commit()

    result = await delegation_repo.get_by_assignee("SARAH@EXAMPLE.COM")
    assert len(result) == 1


@pytest.mark.asyncio
async def test_get_overdue(delegation_repo, db_session):
    """Test getting overdue delegations."""
    yesterday = datetime.utcnow() - timedelta(days=1)
    tomorrow = datetime.utcnow() + timedelta(days=1)

    overdue = make_delegation(title="Overdue", due_date=yesterday)
    future = make_delegation(title="Future", due_date=tomorrow)
    no_date = make_delegation(title="No date", due_date=None)

    await delegation_repo.create(overdue)
    await delegation_repo.create(future)
    await delegation_repo.create(no_date)
    await db_session.commit()

    result = await delegation_repo.get_overdue()
    assert len(result) == 1
    assert result[0].title == "Overdue"


@pytest.mark.asyncio
async def test_get_waiting_summary(delegation_repo, db_session):
    """Test getting waiting summary grouped by assignee."""
    await delegation_repo.create(make_delegation(
        title="Task 1", assignee_email="sarah@example.com", assignee_name="Sarah"
    ))
    await delegation_repo.create(make_delegation(
        title="Task 2", assignee_email="sarah@example.com", assignee_name="Sarah"
    ))
    await delegation_repo.create(make_delegation(
        title="Task 3", assignee_email="sarah@example.com", status=DelegationStatus.IN_PROGRESS
    ))
    await delegation_repo.create(make_delegation(
        title="Task 4", assignee_email="mike@example.com", assignee_name="Mike"
    ))
    await db_session.commit()

    summaries = await delegation_repo.get_waiting_summary()
    assert len(summaries) == 2

    sarah = next(s for s in summaries if s.assignee_email == "sarah@example.com")
    mike = next(s for s in summaries if s.assignee_email == "mike@example.com")

    assert sarah.pending_count == 2
    assert sarah.in_progress_count == 1
    assert sarah.total_waiting == 3

    assert mike.pending_count == 1
    assert mike.total_waiting == 1


@pytest.mark.asyncio
async def test_search_delegations(delegation_repo, db_session):
    """Test searching delegations."""
    await delegation_repo.create(make_delegation(title="Review the contract"))
    await delegation_repo.create(make_delegation(title="Send the report"))
    await delegation_repo.create(make_delegation(
        title="Task", description="Needs contract review"
    ))
    await db_session.commit()

    result = await delegation_repo.search("contract")
    assert len(result) == 2
    assert any("contract" in d.title.lower() for d in result)


@pytest.mark.asyncio
async def test_exists_similar(delegation_repo, db_session):
    """Test checking for similar delegations."""
    await delegation_repo.create(make_delegation(
        title="Review report",
        assignee_email="sarah@example.com",
        source_type="email",
    ))
    await db_session.commit()

    assert await delegation_repo.exists_similar(
        title="Review report",
        assignee_email="sarah@example.com",
        source_type="email",
    )

    assert not await delegation_repo.exists_similar(
        title="Review report",
        assignee_email="mike@example.com",
        source_type="email",
    )

    assert not await delegation_repo.exists_similar(
        title="Different title",
        assignee_email="sarah@example.com",
        source_type="email",
    )


@pytest.mark.asyncio
async def test_delegation_model_is_overdue(delegation_repo, db_session):
    """Test the is_overdue property."""
    yesterday = datetime.utcnow() - timedelta(days=1)
    tomorrow = datetime.utcnow() + timedelta(days=1)

    overdue = make_delegation(due_date=yesterday)
    future = make_delegation(due_date=tomorrow)
    no_date = make_delegation(due_date=None)
    completed = make_delegation(due_date=yesterday, status=DelegationStatus.COMPLETED)

    assert overdue.is_overdue is True
    assert future.is_overdue is False
    assert no_date.is_overdue is False
    assert completed.is_overdue is False


@pytest.mark.asyncio
async def test_delegation_model_days_waiting(delegation_repo, db_session):
    """Test the days_waiting property."""
    three_days_ago = datetime.utcnow() - timedelta(days=3)
    d = make_delegation(delegated_at=three_days_ago)
    assert d.days_waiting == 3


@pytest.mark.asyncio
async def test_delegation_model_days_until_due(delegation_repo, db_session):
    """Test the days_until_due property."""
    in_5_days = datetime.utcnow() + timedelta(days=5)
    ago_2_days = datetime.utcnow() - timedelta(days=2)

    future = make_delegation(due_date=in_5_days)
    overdue = make_delegation(due_date=ago_2_days)
    no_date = make_delegation(due_date=None)

    assert future.days_until_due in (4, 5)
    assert overdue.days_until_due in (-2, -3)
    assert no_date.days_until_due is None


@pytest.mark.asyncio
async def test_mark_completed(delegation_repo, db_session):
    """Test marking a delegation as completed."""
    d = make_delegation(title="Test")
    await delegation_repo.create(d)
    await db_session.commit()

    updated = d.mark_completed()
    assert updated.status == DelegationStatus.COMPLETED
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_mark_in_progress(delegation_repo, db_session):
    """Test marking a delegation as in progress."""
    d = make_delegation(title="Test")
    updated = d.mark_in_progress()
    assert updated.status == DelegationStatus.IN_PROGRESS
