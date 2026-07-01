"""Pytest configuration and fixtures."""

from collections.abc import AsyncGenerator
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from executive_copilot.config import AppConfig, DatabaseConfig

# Import tables module to ensure all table classes are registered in SQLModel metadata
from executive_copilot.database import tables as _tables  # noqa: F401
from executive_copilot.database.connection import DatabaseManager
from executive_copilot.models import (
    Email,
    EmailAddress,
    Organization,
    OrganizationType,
    Person,
)
from executive_copilot.models.organization import OrganizationDomains


@pytest.fixture
def app_config(tmp_path) -> AppConfig:
    """Create test application configuration."""
    db_path = tmp_path / "test.db"
    return AppConfig(
        database=DatabaseConfig(
            driver="sqlite+aiosqlite",
            name=str(db_path),
        ),
    )


@pytest.fixture
async def db_manager(app_config: AppConfig) -> AsyncGenerator[DatabaseManager, None]:
    """Create test database manager."""
    manager = DatabaseManager(app_config.database)
    await manager.create_tables()
    yield manager
    await manager.close()


@pytest.fixture
async def db_session(db_manager: DatabaseManager) -> AsyncGenerator[AsyncSession, None]:
    """Create test database session."""
    async with db_manager.session() as session:
        yield session


@pytest.fixture
def sample_organization() -> Organization:
    """Create a sample organization for testing."""
    return Organization(
        id=uuid4(),
        created_at=datetime.utcnow(),
        name="Paycode",
        short_name="PC",
        organization_type=OrganizationType.EMPLOYER,
        domains=OrganizationDomains(
            primary="paycode.com.mx",
            aliases=["paycode.mx"],
        ),
        context_sources=["asana", "documentation", "memory"],
        importance=10,
        tags=["employer", "fintech"],
    )


@pytest.fixture
def sample_person() -> Person:
    """Create a sample person for testing."""
    from executive_copilot.models.person import ContactInfo

    return Person(
        id=uuid4(),
        created_at=datetime.utcnow(),
        first_name="John",
        last_name="Smith",
        contact=ContactInfo(email="john.smith@example.com"),
        title="VP Engineering",
        importance=8,
    )


@pytest.fixture
def sample_email() -> Email:
    """Create a sample email for testing."""
    return Email(
        id=uuid4(),
        created_at=datetime.utcnow(),
        sender=EmailAddress(address="sender@example.com", display_name="Sender"),
        to_recipients=[EmailAddress(address="recipient@test.com")],
        subject="Test Email Subject",
        body_text="This is a test email body.",
        snippet="This is a test email...",
        received_at=datetime.utcnow(),
        is_read=False,
    )
