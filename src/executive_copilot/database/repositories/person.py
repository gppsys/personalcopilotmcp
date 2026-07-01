"""Person repository implementation."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select

from executive_copilot.database.repositories.base import Repository
from executive_copilot.database.tables import PersonTable
from executive_copilot.models import Person, PersonRole
from executive_copilot.models.person import ContactInfo


class PersonRepository(Repository[Person, PersonTable]):
    """Repository for Person entities."""

    @property
    def _table_class(self) -> type[PersonTable]:
        return PersonTable

    @property
    def _entity_name(self) -> str:
        return "Person"

    def _to_model(self, table: PersonTable) -> Person:
        """Convert table row to domain model."""
        from executive_copilot.models.base import ProviderReference

        return Person(
            id=table.id,
            created_at=table.created_at,
            updated_at=table.updated_at,
            first_name=table.first_name,
            last_name=table.last_name,
            display_name=table.display_name,
            nickname=table.nickname,
            contact=ContactInfo(**table.contact) if table.contact else ContactInfo(),
            organization_id=table.organization_id,
            title=table.title,
            department=table.department,
            roles=[PersonRole(r) for r in table.roles],
            importance=table.importance,
            notes=table.notes,
            tags=table.tags,
            last_contact_at=table.last_contact_at,
            last_contact_type=table.last_contact_type,
            interaction_count=table.interaction_count,
            provider_refs=[ProviderReference(**ref) for ref in table.provider_refs],
        )

    def _to_table(self, model: Person) -> PersonTable:
        """Convert domain model to table row."""
        return PersonTable(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            first_name=model.first_name,
            last_name=model.last_name,
            display_name=model.display_name,
            nickname=model.nickname,
            contact=model.contact.model_dump(),
            organization_id=model.organization_id,
            title=model.title,
            department=model.department,
            roles=[r.value for r in model.roles],
            importance=model.importance,
            notes=model.notes,
            tags=model.tags,
            last_contact_at=model.last_contact_at,
            last_contact_type=model.last_contact_type,
            interaction_count=model.interaction_count,
            provider_refs=[ref.model_dump() for ref in model.provider_refs],
        )

    async def find_by_email(self, email: str) -> Person | None:
        """Find person by any of their email addresses.

        Args:
            email: Email address to search for

        Returns:
            Person if found
        """
        email_lower = email.lower()

        stmt = select(self._table_class)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        for row in rows:
            person = self._to_model(row)
            if person.has_email(email_lower):
                return person

        return None

    async def find_by_organization(
        self,
        organization_id: UUID,
        limit: int = 100,
    ) -> list[Person]:
        """Find all persons in an organization.

        Args:
            organization_id: Organization to search
            limit: Maximum results

        Returns:
            List of persons
        """
        stmt = (
            select(self._table_class)
            .where(self._col("organization_id") == organization_id)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def search_by_name(
        self,
        name: str,
        limit: int = 20,
    ) -> list[Person]:
        """Search persons by name.

        Args:
            name: Name to search for
            limit: Maximum results

        Returns:
            Matching persons
        """
        pattern = f"%{name}%"
        stmt = (
            select(self._table_class)
            .where(
                or_(
                    self._col("first_name").ilike(pattern),
                    self._col("last_name").ilike(pattern),
                    self._col("display_name").ilike(pattern),
                    self._col("nickname").ilike(pattern),
                )
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_important_persons(
        self,
        min_importance: int = 7,
        limit: int = 50,
    ) -> list[Person]:
        """Get high-importance persons.

        Args:
            min_importance: Minimum importance level
            limit: Maximum results

        Returns:
            Important persons sorted by importance
        """
        stmt = (
            select(self._table_class)
            .where(self._col("importance") >= min_importance)
            .order_by(self._col("importance").desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def find_by_role(
        self,
        role: PersonRole,
        limit: int = 100,
    ) -> list[Person]:
        """Find persons by role.

        Args:
            role: Role to search for
            limit: Maximum results

        Returns:
            Persons with the specified role
        """
        stmt = select(self._table_class).limit(limit)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return [
            self._to_model(row)
            for row in rows
            if role.value in row.roles
        ]

    async def get_stale_relationships(
        self,
        days_threshold: int = 30,
        min_importance: int = 5,
        limit: int = 50,
    ) -> list[Person]:
        """Get important people we haven't contacted recently.

        Args:
            days_threshold: Days without contact to be considered stale
            min_importance: Minimum importance level to include
            limit: Maximum results

        Returns:
            List of people needing attention, sorted by importance
        """
        cutoff = datetime.utcnow() - timedelta(days=days_threshold)

        stmt = (
            select(self._table_class)
            .where(
                self._col("importance") >= min_importance,
                or_(
                    self._col("last_contact_at").is_(None),
                    self._col("last_contact_at") < cutoff,
                ),
            )
            .order_by(
                self._col("importance").desc(),
                self._col("last_contact_at").asc().nullsfirst(),
            )
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def update_last_contact(
        self,
        email: str,
        contact_at: datetime,
        contact_type: str,
    ) -> bool:
        """Update last contact info for a person.

        Args:
            email: Person's email
            contact_at: When contact occurred
            contact_type: Type of interaction

        Returns:
            True if updated, False if person not found
        """
        person = await self.find_by_email(email)
        if not person:
            return False

        contact_naive = contact_at.replace(tzinfo=None) if contact_at.tzinfo else contact_at
        last_naive = (
            person.last_contact_at.replace(tzinfo=None)
            if person.last_contact_at and person.last_contact_at.tzinfo
            else person.last_contact_at
        )

        should_update = last_naive is None or contact_naive > last_naive

        if should_update:
            updated = person.with_update(
                last_contact_at=contact_at,
                last_contact_type=contact_type,
                interaction_count=person.interaction_count + 1,
                updated_at=datetime.utcnow(),
            )
            await self.update(updated)
            return True

        return False
