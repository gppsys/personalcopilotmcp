"""Person domain model."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import EmailStr, Field

from executive_copilot.models.base import DomainModel, ProviderReference, ValueObject


class PersonRole(StrEnum):
    """Role a person plays in an organization or relationship."""

    EXECUTIVE = "executive"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    CLIENT = "client"
    VENDOR = "vendor"
    PARTNER = "partner"
    INVESTOR = "investor"
    ADVISOR = "advisor"
    FAMILY = "family"
    FRIEND = "friend"
    ACQUAINTANCE = "acquaintance"
    OTHER = "other"


class ContactInfo(ValueObject):
    """Contact information for a person."""

    email: EmailStr | None = Field(default=None, description="Primary email address")
    secondary_emails: list[EmailStr] = Field(default_factory=list, description="Additional email addresses")
    phone: str | None = Field(default=None, description="Primary phone number")
    secondary_phones: list[str] = Field(default_factory=list, description="Additional phone numbers")
    linkedin_url: str | None = Field(default=None, description="LinkedIn profile URL")
    twitter_handle: str | None = Field(default=None, description="Twitter/X handle")


class Person(DomainModel):
    """A person known to the executive.

    Represents individuals the executive interacts with, including colleagues,
    clients, family members, and other contacts. Used by the memory layer to
    maintain relationship context.
    """

    # Identity
    first_name: str = Field(description="First name")
    last_name: str | None = Field(default=None, description="Last name")
    display_name: str | None = Field(default=None, description="Preferred display name")
    nickname: str | None = Field(default=None, description="Nickname or informal name")

    # Contact
    contact: ContactInfo = Field(default_factory=ContactInfo, description="Contact information")

    # Professional context
    organization_id: UUID | None = Field(default=None, description="Primary organization ID")
    title: str | None = Field(default=None, description="Job title or role")
    department: str | None = Field(default=None, description="Department within organization")

    # Relationship
    roles: list[PersonRole] = Field(default_factory=list, description="Roles in relation to executive")
    importance: int = Field(default=5, ge=1, le=10, description="Importance level (1-10)")
    notes: str | None = Field(default=None, description="Free-form notes about the person")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    # Interaction tracking
    last_contact_at: datetime | None = Field(default=None, description="Last interaction time")
    last_contact_type: str | None = Field(default=None, description="Type of last interaction")
    interaction_count: int = Field(default=0, description="Total interaction count")

    # Provider tracking
    provider_refs: list[ProviderReference] = Field(
        default_factory=list,
        description="References to this person in external providers",
    )

    @property
    def full_name(self) -> str:
        """Get the full name."""
        if self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name

    @property
    def preferred_name(self) -> str:
        """Get the preferred display name."""
        return self.display_name or self.nickname or self.full_name

    @property
    def primary_email(self) -> str | None:
        """Get the primary email address."""
        return self.contact.email

    def has_email(self, email: str) -> bool:
        """Check if this person has a given email address."""
        email_lower = email.lower()
        if self.contact.email and self.contact.email.lower() == email_lower:
            return True
        return any(e.lower() == email_lower for e in self.contact.secondary_emails)

    @property
    def days_since_contact(self) -> int | None:
        """Days since last contact."""
        if not self.last_contact_at:
            return None
        now = datetime.utcnow()
        last = self.last_contact_at
        if last.tzinfo is not None:
            last = last.replace(tzinfo=None)
        delta = now - last
        return delta.days

    def is_stale(self, threshold_days: int = 30) -> bool:
        """Check if relationship is stale (no contact in threshold days)."""
        days = self.days_since_contact
        if days is None:
            return True
        return days >= threshold_days
