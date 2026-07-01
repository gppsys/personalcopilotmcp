"""Email provider interfaces and implementations."""

from executive_copilot.providers.email.gmail import GmailProvider
from executive_copilot.providers.email.interface import (
    EmailProvider,
    EmailQuery,
    EmailSearchResult,
)
from executive_copilot.providers.email.outlook import OutlookProvider

__all__ = [
    "EmailProvider",
    "EmailQuery",
    "EmailSearchResult",
    "GmailProvider",
    "OutlookProvider",
]
