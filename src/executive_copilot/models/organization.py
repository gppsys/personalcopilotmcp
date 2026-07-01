"""Organization and Customer domain models."""

from enum import StrEnum

from pydantic import Field, HttpUrl

from executive_copilot.models.base import DomainModel, ProviderReference, ValueObject


class OrganizationType(StrEnum):
    """Type of organization."""

    EMPLOYER = "employer"
    CLIENT = "client"
    VENDOR = "vendor"
    PARTNER = "partner"
    INVESTOR = "investor"
    SCHOOL = "school"
    GOVERNMENT = "government"
    NONPROFIT = "nonprofit"
    PERSONAL = "personal"
    OTHER = "other"


class OrganizationDomains(ValueObject):
    """Email domains associated with an organization."""

    primary: str = Field(description="Primary email domain")
    aliases: list[str] = Field(default_factory=list, description="Additional email domains")

    def matches(self, email: str) -> bool:
        """Check if an email address belongs to this organization."""
        email_domain = email.rsplit("@", maxsplit=1)[-1].lower() if "@" in email else ""
        all_domains = [self.primary.lower()] + [d.lower() for d in self.aliases]
        return email_domain in all_domains


class Organization(DomainModel):
    """An organization the executive interacts with.

    Organizations provide context for emails, meetings, and tasks.
    The context engine uses organization configuration to determine
    which information sources to consult.
    """

    # Identity
    name: str = Field(description="Organization name")
    short_name: str | None = Field(default=None, description="Abbreviated name")
    organization_type: OrganizationType = Field(description="Type of organization")

    # Domains for email matching
    domains: OrganizationDomains | None = Field(
        default=None,
        description="Email domains for matching incoming correspondence",
    )

    # External references
    website: HttpUrl | None = Field(default=None, description="Organization website")
    crm_id: str | None = Field(default=None, description="ID in CRM system")

    # Context configuration
    context_sources: list[str] = Field(
        default_factory=list,
        description="Information sources to consult for this organization",
    )
    default_project_id: str | None = Field(
        default=None,
        description="Default project ID for task creation",
    )

    # Metadata
    industry: str | None = Field(default=None, description="Industry sector")
    notes: str | None = Field(default=None, description="Notes about the organization")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    importance: int = Field(default=5, ge=1, le=10, description="Importance level (1-10)")

    # Provider tracking
    provider_refs: list[ProviderReference] = Field(
        default_factory=list,
        description="References to this organization in external providers",
    )

    @property
    def display_name(self) -> str:
        """Get the display name (short name if available)."""
        return self.short_name or self.name

    def email_belongs_to(self, email: str) -> bool:
        """Check if an email address belongs to this organization."""
        if self.domains is None:
            return False
        return self.domains.matches(email)


class Customer(DomainModel):
    """A customer relationship within an organization.

    Extends organization with customer-specific attributes like
    contract details, account status, and relationship history.
    """

    organization_id: str = Field(description="Reference to the parent organization")

    # Customer details
    account_status: str = Field(default="active", description="Account status")
    contract_type: str | None = Field(default=None, description="Type of contract")
    contract_start: str | None = Field(default=None, description="Contract start date")
    contract_end: str | None = Field(default=None, description="Contract end date")

    # Financial
    annual_value: float | None = Field(default=None, description="Annual contract value")
    currency: str = Field(default="USD", description="Currency code")

    # Relationship
    primary_contact_id: str | None = Field(default=None, description="Primary contact person ID")
    account_manager_id: str | None = Field(default=None, description="Account manager person ID")

    # Metadata
    notes: str | None = Field(default=None, description="Customer-specific notes")
    risk_level: str | None = Field(default=None, description="Risk assessment")
    satisfaction_score: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Customer satisfaction score",
    )

    # Provider tracking
    provider_refs: list[ProviderReference] = Field(
        default_factory=list,
        description="References to this customer in external providers",
    )
