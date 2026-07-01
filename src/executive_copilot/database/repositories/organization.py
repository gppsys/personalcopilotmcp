"""Organization repository implementation."""

from pydantic import HttpUrl
from sqlalchemy import or_, select

from executive_copilot.database.repositories.base import Repository
from executive_copilot.database.tables import OrganizationTable
from executive_copilot.models import Organization, OrganizationType
from executive_copilot.models.base import ProviderReference
from executive_copilot.models.organization import OrganizationDomains


class OrganizationRepository(Repository[Organization, OrganizationTable]):
    """Repository for Organization entities."""

    @property
    def _table_class(self) -> type[OrganizationTable]:
        return OrganizationTable

    @property
    def _entity_name(self) -> str:
        return "Organization"

    def _to_model(self, table: OrganizationTable) -> Organization:
        """Convert table row to domain model."""

        domains = None
        if table.domains:
            domains = OrganizationDomains(**table.domains)

        return Organization(
            id=table.id,
            created_at=table.created_at,
            updated_at=table.updated_at,
            name=table.name,
            short_name=table.short_name,
            organization_type=OrganizationType(table.organization_type),
            domains=domains,
            website=HttpUrl(table.website) if table.website else None,
            crm_id=table.crm_id,
            context_sources=table.context_sources,
            default_project_id=table.default_project_id,
            industry=table.industry,
            notes=table.notes,
            tags=table.tags,
            importance=table.importance,
            provider_refs=[ProviderReference(**ref) for ref in table.provider_refs],
        )

    def _to_table(self, model: Organization) -> OrganizationTable:
        """Convert domain model to table row."""
        return OrganizationTable(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            name=model.name,
            short_name=model.short_name,
            organization_type=model.organization_type.value,
            domains=model.domains.model_dump() if model.domains else None,
            website=str(model.website) if model.website else None,
            crm_id=model.crm_id,
            context_sources=model.context_sources,
            default_project_id=model.default_project_id,
            industry=model.industry,
            notes=model.notes,
            tags=model.tags,
            importance=model.importance,
            provider_refs=[ref.model_dump() for ref in model.provider_refs],
        )

    async def find_by_domain(self, domain: str) -> Organization | None:
        """Find organization by email domain.

        Args:
            domain: Email domain to search for

        Returns:
            Organization if found
        """
        domain_lower = domain.lower()

        stmt = select(self._table_class)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        for row in rows:
            org = self._to_model(row)
            if org.email_belongs_to(f"user@{domain_lower}"):
                return org

        return None

    async def find_by_email(self, email: str) -> Organization | None:
        """Find organization that owns an email address.

        Args:
            email: Full email address

        Returns:
            Organization if found
        """
        if "@" not in email:
            return None
        domain = email.rsplit("@", maxsplit=1)[-1]
        return await self.find_by_domain(domain)

    async def search_by_name(
        self,
        name: str,
        limit: int = 20,
    ) -> list[Organization]:
        """Search organizations by name.

        Args:
            name: Name to search for
            limit: Maximum results

        Returns:
            Matching organizations
        """
        pattern = f"%{name}%"
        stmt = (
            select(self._table_class)
            .where(
                or_(
                    self._col("name").ilike(pattern),
                    self._col("short_name").ilike(pattern),
                )
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_by_type(
        self,
        org_type: OrganizationType,
        limit: int = 100,
    ) -> list[Organization]:
        """Get organizations by type.

        Args:
            org_type: Organization type to filter by
            limit: Maximum results

        Returns:
            Organizations of the specified type
        """
        stmt = (
            select(self._table_class)
            .where(self._col("organization_type") == org_type.value)
            .order_by(self._col("importance").desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_model(row) for row in rows]

    async def get_important_organizations(
        self,
        min_importance: int = 7,
        limit: int = 50,
    ) -> list[Organization]:
        """Get high-importance organizations.

        Args:
            min_importance: Minimum importance level
            limit: Maximum results

        Returns:
            Important organizations sorted by importance
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

    async def find_by_crm_id(self, crm_id: str) -> Organization | None:
        """Find organization by CRM ID.

        Args:
            crm_id: CRM system identifier

        Returns:
            Organization if found
        """
        stmt = select(self._table_class).where(self._col("crm_id") == crm_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._to_model(row)
