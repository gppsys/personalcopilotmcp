"""OAuth2 authorization flows for Google and Microsoft."""

import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.logging import get_logger

logger = get_logger(__name__)

# Google OAuth2 endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Google scopes for Gmail and Calendar
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# Microsoft OAuth2 endpoints
MICROSOFT_AUTH_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
MICROSOFT_USERINFO_URL = "https://graph.microsoft.com/v1.0/me"

# Microsoft scopes for Outlook Mail, Calendar, OneDrive
MICROSOFT_SCOPES = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Calendars.Read",
    "https://graph.microsoft.com/Calendars.ReadWrite",
    "https://graph.microsoft.com/Files.Read",
    "https://graph.microsoft.com/Files.ReadWrite",
    "https://graph.microsoft.com/User.Read",
    "offline_access",
]


@dataclass
class OAuthState:
    """State for OAuth flow."""
    provider: str  # google or microsoft
    account_id: str
    state_token: str
    redirect_uri: str


class OAuthManager:
    """Manages OAuth2 flows for Google and Microsoft."""

    def __init__(self, credential_manager: CredentialManager) -> None:
        self.credential_manager = credential_manager
        self._pending_states: dict[str, OAuthState] = {}

    def get_pending_state(self, state: str) -> OAuthState | None:
        """Get pending OAuth state without consuming it."""
        return self._pending_states.get(state)

    def start_google_auth(
        self,
        account_id: str,
        client_id: str,
        redirect_uri: str,
    ) -> str:
        """Start Google OAuth2 authorization flow.

        Args:
            account_id: Account identifier (email address)
            client_id: Google OAuth client ID
            redirect_uri: Callback URL

        Returns:
            Authorization URL to redirect user to
        """
        state_token = secrets.token_urlsafe(32)

        self._pending_states[state_token] = OAuthState(
            provider="google",
            account_id=account_id,
            state_token=state_token,
            redirect_uri=redirect_uri,
        )

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state_token,
            "login_hint": account_id,
        }

        auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
        logger.info("Started Google OAuth flow", account_id=account_id)
        return auth_url

    async def complete_google_auth(
        self,
        code: str,
        state: str,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        """Complete Google OAuth2 flow by exchanging code for tokens.

        Args:
            code: Authorization code from Google
            state: State token to verify
            client_id: Google OAuth client ID
            client_secret: Google OAuth client secret

        Returns:
            Dict with account info and tokens
        """
        if state not in self._pending_states:
            raise ValueError("Invalid or expired state token")

        oauth_state = self._pending_states.pop(state)

        async with httpx.AsyncClient() as client:
            # Exchange code for tokens
            token_response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": oauth_state.redirect_uri,
                },
            )
            token_response.raise_for_status()
            tokens = token_response.json()

            # Get user info to verify email
            userinfo_response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            userinfo_response.raise_for_status()
            userinfo = userinfo_response.json()

        actual_email = userinfo.get("email", oauth_state.account_id)

        # Save tokens to credentials
        self.credential_manager.save_credential(
            provider_type="email",
            provider_name="gmail",
            account_id=actual_email,
            account_label=userinfo.get("name", actual_email),
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
        )

        # Also save for calendar
        self.credential_manager.save_credential(
            provider_type="calendar",
            provider_name="google",
            account_id=actual_email,
            account_label=userinfo.get("name", actual_email),
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
        )

        logger.info("Completed Google OAuth flow", account_id=actual_email)

        return {
            "provider": "google",
            "account_id": actual_email,
            "name": userinfo.get("name"),
            "email": userinfo.get("email"),
            "picture": userinfo.get("picture"),
        }

    def start_microsoft_auth(
        self,
        account_id: str,
        client_id: str,
        redirect_uri: str,
        tenant: str = "common",
    ) -> str:
        """Start Microsoft OAuth2 authorization flow.

        Args:
            account_id: Account identifier (email address)
            client_id: Microsoft OAuth client ID
            redirect_uri: Callback URL
            tenant: Microsoft tenant (common, organizations, or tenant ID)

        Returns:
            Authorization URL to redirect user to
        """
        state_token = secrets.token_urlsafe(32)

        self._pending_states[state_token] = OAuthState(
            provider="microsoft",
            account_id=account_id,
            state_token=state_token,
            redirect_uri=redirect_uri,
        )

        auth_url = MICROSOFT_AUTH_URL.format(tenant=tenant)

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(MICROSOFT_SCOPES),
            "state": state_token,
            "login_hint": account_id,
            "prompt": "consent",
        }

        full_url = f"{auth_url}?{urlencode(params)}"
        logger.info("Started Microsoft OAuth flow", account_id=account_id, tenant=tenant)
        return full_url

    async def complete_microsoft_auth(
        self,
        code: str,
        state: str,
        client_id: str,
        client_secret: str,
        tenant: str = "common",
    ) -> dict[str, Any]:
        """Complete Microsoft OAuth2 flow by exchanging code for tokens.

        Args:
            code: Authorization code from Microsoft
            state: State token to verify
            client_id: Microsoft OAuth client ID
            client_secret: Microsoft OAuth client secret
            tenant: Microsoft tenant

        Returns:
            Dict with account info and tokens
        """
        if state not in self._pending_states:
            raise ValueError("Invalid or expired state token")

        oauth_state = self._pending_states.pop(state)
        token_url = MICROSOFT_TOKEN_URL.format(tenant=tenant)

        async with httpx.AsyncClient() as client:
            # Exchange code for tokens
            token_response = await client.post(
                token_url,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": oauth_state.redirect_uri,
                },
            )
            token_response.raise_for_status()
            tokens = token_response.json()

            # Get user info
            userinfo_response = await client.get(
                MICROSOFT_USERINFO_URL,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            userinfo_response.raise_for_status()
            userinfo = userinfo_response.json()

        actual_email = userinfo.get("mail") or userinfo.get("userPrincipalName", oauth_state.account_id)

        # Save tokens for email
        self.credential_manager.save_credential(
            provider_type="email",
            provider_name="outlook",
            account_id=actual_email,
            account_label=userinfo.get("displayName", actual_email),
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
        )

        # Save tokens for calendar
        self.credential_manager.save_credential(
            provider_type="calendar",
            provider_name="outlook",
            account_id=actual_email,
            account_label=userinfo.get("displayName", actual_email),
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
        )

        # Save tokens for OneDrive
        self.credential_manager.save_credential(
            provider_type="document",
            provider_name="onedrive",
            account_id=actual_email,
            account_label=userinfo.get("displayName", actual_email),
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
        )

        logger.info("Completed Microsoft OAuth flow", account_id=actual_email)

        return {
            "provider": "microsoft",
            "account_id": actual_email,
            "name": userinfo.get("displayName"),
            "email": actual_email,
        }

    async def refresh_google_token(
        self,
        account_id: str,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> str | None:
        """Refresh a Google access token and store it on every sibling credential.

        The OAuth client and refresh token are resolved across the account's
        Gmail / Calendar / Drive credential records, so refresh works no matter
        which record the OAuth client was entered on.
        """
        return await self._refresh_token(
            family="google",
            token_url=GOOGLE_TOKEN_URL,
            account_id=account_id,
            client_id=client_id,
            client_secret=client_secret,
        )

    async def refresh_microsoft_token(
        self,
        account_id: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant: str | None = None,
    ) -> str | None:
        """Refresh a Microsoft access token and store it on every sibling credential.

        Microsoft rotates refresh tokens, so the new refresh token is written to
        the Outlook mail, Outlook calendar and OneDrive records alike.
        """
        client = self.credential_manager.get_oauth_client("microsoft", account_id) or {}
        effective_tenant = tenant or client.get("tenant_id") or "common"
        return await self._refresh_token(
            family="microsoft",
            token_url=MICROSOFT_TOKEN_URL.format(tenant=effective_tenant),
            account_id=account_id,
            client_id=client_id,
            client_secret=client_secret,
        )

    async def _refresh_token(
        self,
        family: str,
        token_url: str,
        account_id: str,
        client_id: str | None,
        client_secret: str | None,
    ) -> str | None:
        """Shared refresh-token grant for Google and Microsoft."""
        client = self.credential_manager.get_oauth_client(family, account_id) or {}
        effective_client_id = client_id or client.get("client_id")
        effective_secret = client_secret or client.get("client_secret", "")
        refresh_token = self.credential_manager.get_oauth_refresh_token(family, account_id)

        if not effective_client_id or not refresh_token:
            logger.warning(
                "Cannot refresh token: missing OAuth client or refresh token",
                family=family,
                account_id=account_id,
                has_client_id=bool(effective_client_id),
                has_refresh_token=bool(refresh_token),
            )
            return None

        async with httpx.AsyncClient() as http:
            response = await http.post(
                token_url,
                data={
                    "client_id": effective_client_id,
                    "client_secret": effective_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )

        if response.status_code != 200:
            error_code = None
            try:
                error_code = response.json().get("error")
            except ValueError:
                pass
            logger.error(
                "Failed to refresh OAuth token; re-authorize the account in the admin panel",
                family=family,
                account_id=account_id,
                status_code=response.status_code,
                error=error_code,
            )
            return None

        tokens = response.json()
        new_access_token: str = tokens["access_token"]
        self.credential_manager.save_oauth_tokens(
            family,
            account_id,
            access_token=new_access_token,
            refresh_token=tokens.get("refresh_token") or refresh_token,
        )
        logger.info("Refreshed OAuth token", family=family, account_id=account_id)
        return new_access_token
