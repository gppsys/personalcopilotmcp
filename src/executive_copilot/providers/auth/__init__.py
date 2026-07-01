"""Authentication provider interfaces."""

from executive_copilot.providers.auth.interface import (
    AuthenticationProvider,
    OAuthCredentials,
    TokenInfo,
)

__all__ = [
    "AuthenticationProvider",
    "OAuthCredentials",
    "TokenInfo",
]
