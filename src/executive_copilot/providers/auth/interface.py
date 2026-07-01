"""Authentication provider interface."""

from abc import abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, SecretStr

from executive_copilot.providers.base import Provider


class TokenInfo(BaseModel):
    """OAuth token information."""

    access_token: SecretStr = Field(description="Access token")
    refresh_token: SecretStr | None = Field(default=None, description="Refresh token")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_at: datetime | None = Field(default=None, description="Expiration time")
    scope: list[str] = Field(default_factory=list, description="Granted scopes")

    @property
    def is_expired(self) -> bool:
        """Check if token is expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() >= self.expires_at

    @property
    def needs_refresh(self) -> bool:
        """Check if token should be refreshed (5 min buffer)."""
        if self.expires_at is None:
            return False
        from datetime import timedelta

        buffer = timedelta(minutes=5)
        return datetime.utcnow() >= (self.expires_at - buffer)


class OAuthCredentials(BaseModel):
    """OAuth credentials for a provider account."""

    provider_name: str = Field(description="Provider name")
    account_id: str = Field(description="Account identifier")
    token_info: TokenInfo = Field(description="Token information")
    user_info: dict[str, Any] = Field(default_factory=dict, description="User profile info")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class AuthenticationProvider(Provider[OAuthCredentials]):
    """Interface for authentication providers.

    Handles OAuth flows and token management for external services.
    """

    @abstractmethod
    async def get_authorization_url(
        self,
        scopes: list[str] | None = None,
        state: str | None = None,
        redirect_uri: str | None = None,
    ) -> str:
        """Get the OAuth authorization URL.

        Args:
            scopes: Requested scopes (None = use defaults)
            state: State parameter for CSRF protection
            redirect_uri: Override redirect URI

        Returns:
            Authorization URL to redirect user to
        """
        ...

    @abstractmethod
    async def exchange_code(
        self,
        code: str,
        redirect_uri: str | None = None,
    ) -> OAuthCredentials:
        """Exchange authorization code for tokens.

        Args:
            code: Authorization code from OAuth callback
            redirect_uri: Redirect URI used in authorization

        Returns:
            OAuth credentials with tokens
        """
        ...

    @abstractmethod
    async def refresh_token(
        self,
        credentials: OAuthCredentials,
    ) -> OAuthCredentials:
        """Refresh an expired access token.

        Args:
            credentials: Existing credentials with refresh token

        Returns:
            Updated credentials with new tokens
        """
        ...

    @abstractmethod
    async def revoke_token(
        self,
        credentials: OAuthCredentials,
    ) -> None:
        """Revoke OAuth tokens.

        Args:
            credentials: Credentials to revoke
        """
        ...

    @abstractmethod
    async def validate_token(
        self,
        credentials: OAuthCredentials,
    ) -> bool:
        """Validate if token is still valid.

        Args:
            credentials: Credentials to validate

        Returns:
            True if token is valid
        """
        ...

    @abstractmethod
    async def get_user_info(
        self,
        credentials: OAuthCredentials,
    ) -> dict[str, Any]:
        """Get user profile information.

        Args:
            credentials: Credentials to use

        Returns:
            User profile data from provider
        """
        ...

    async def ensure_valid_token(
        self,
        credentials: OAuthCredentials,
    ) -> OAuthCredentials:
        """Ensure credentials have a valid token, refreshing if needed.

        Args:
            credentials: Credentials to check

        Returns:
            Credentials with valid token
        """
        if credentials.token_info.needs_refresh:
            return await self.refresh_token(credentials)
        return credentials
