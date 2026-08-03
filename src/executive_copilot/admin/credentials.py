"""Secure credential management for the admin panel.

Supports multiple accounts per provider, identified by account_id.
For example:
- email/outlook/user@work.com
- email/outlook/personal@gmail.com
- email/gmail/user@example.com
"""

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from executive_copilot.logging import get_logger

logger = get_logger(__name__)


class CredentialManager:
    """Manages encrypted storage of provider credentials.

    Credentials are stored with a three-part key:
    - provider_type: email, calendar, task, ai, etc.
    - provider_name: outlook, gmail, asana, openai, etc.
    - account_id: email address, workspace name, or "default"

    Examples:
    - email/outlook/user@example.com
    - email/outlook/user@secondary.com
    - calendar/google/user@example.com
    - ai/openai/default
    - ai/anthropic/default
    """

    def __init__(self, credentials_dir: Path) -> None:
        self.credentials_dir = Path(credentials_dir)
        self.credentials_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = self._get_fernet()

    def _get_fernet(self) -> Fernet:
        """Get or create encryption key."""
        key_file = self.credentials_dir / ".key"

        if key_file.exists():
            with open(key_file, "rb") as f:
                key = f.read()
        else:
            machine_id = self._get_machine_id()
            salt = os.urandom(16)

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=480000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))

            with open(key_file, "wb") as f:
                f.write(salt + key)

            os.chmod(key_file, 0o600)
            key = salt + key

        salt = key[:16]
        actual_key = key[16:]
        return Fernet(actual_key)

    def _get_machine_id(self) -> str:
        """Get a machine-specific identifier."""
        import socket
        hostname = socket.gethostname()
        user = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
        return f"{hostname}:{user}"

    def _credential_key(
        self,
        provider_type: str,
        provider_name: str,
        account_id: str,
    ) -> str:
        """Generate a safe filename for the credential."""
        safe_account = account_id.replace("@", "_at_").replace(".", "_")
        return f"{provider_type}__{provider_name}__{safe_account}"

    def _parse_credential_key(self, key: str) -> tuple[str, str, str] | None:
        """Parse a credential key back into components."""
        parts = key.split("__")
        if len(parts) != 3:
            return None
        provider_type, provider_name, safe_account = parts
        account_id = safe_account.replace("_at_", "@").replace("_", ".")
        return provider_type, provider_name, account_id

    def save_credential(
        self,
        provider_type: str,
        provider_name: str,
        account_id: str = "default",
        account_label: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        api_key: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        tenant_id: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> None:
        """Save encrypted credentials.

        Args:
            provider_type: Type of provider (email, calendar, task, ai, etc.)
            provider_name: Provider implementation (outlook, gmail, openai, etc.)
            account_id: Account identifier (email, workspace name, or "default")
            account_label: Human-readable label (e.g., "Work - primary workspace", "Personal")
            client_id: OAuth client ID
            client_secret: OAuth client secret
            api_key: API key for API-key auth
            access_token: OAuth access token
            refresh_token: OAuth refresh token
            tenant_id: Microsoft tenant ID (for Azure/Microsoft providers)
            extra: Additional provider-specific credentials
        """
        credential_data = self.get_credential(provider_type, provider_name, account_id) or {}
        credential_data.update({
            "provider_type": provider_type,
            "provider_name": provider_name,
            "account_id": account_id,
        })

        if account_label:
            credential_data["account_label"] = account_label
        if client_id:
            credential_data["client_id"] = client_id
        if client_secret:
            credential_data["client_secret"] = client_secret
        if api_key:
            credential_data["api_key"] = api_key
        if access_token:
            credential_data["access_token"] = access_token
        if refresh_token:
            credential_data["refresh_token"] = refresh_token
        if tenant_id:
            credential_data["tenant_id"] = tenant_id
        if extra:
            credential_data["extra"] = extra

        encrypted = self._fernet.encrypt(json.dumps(credential_data).encode())

        key = self._credential_key(provider_type, provider_name, account_id)
        filepath = self.credentials_dir / f"{key}.enc"

        with open(filepath, "wb") as f:
            f.write(encrypted)

        os.chmod(filepath, 0o600)
        logger.info(
            "Saved credential",
            provider_type=provider_type,
            provider_name=provider_name,
            account_id=account_id,
        )

    def get_credential(
        self,
        provider_type: str,
        provider_name: str,
        account_id: str = "default",
    ) -> dict[str, Any] | None:
        """Get decrypted credentials for a specific account."""
        key = self._credential_key(provider_type, provider_name, account_id)
        filepath = self.credentials_dir / f"{key}.enc"

        if not filepath.exists():
            return None

        try:
            with open(filepath, "rb") as f:
                encrypted = f.read()

            decrypted = self._fernet.decrypt(encrypted)
            result: dict[str, Any] = json.loads(decrypted.decode())
            return result
        except Exception as e:
            logger.error("Failed to decrypt credential", error=str(e))
            return None

    def get_credentials_for_provider(
        self,
        provider_type: str,
        provider_name: str,
    ) -> list[dict[str, Any]]:
        """Get all credentials for a provider (all accounts)."""
        credentials = []
        prefix = f"{provider_type}__{provider_name}__"

        for filepath in self.credentials_dir.glob("*.enc"):
            if filepath.stem.startswith(prefix):
                parsed = self._parse_credential_key(filepath.stem)
                if parsed:
                    _, _, account_id = parsed
                    cred = self.get_credential(provider_type, provider_name, account_id)
                    if cred:
                        credentials.append(cred)

        return credentials

    def delete_credential(
        self,
        provider_type: str,
        provider_name: str,
        account_id: str = "default",
    ) -> bool:
        """Delete credentials for a specific account."""
        key = self._credential_key(provider_type, provider_name, account_id)
        filepath = self.credentials_dir / f"{key}.enc"

        if filepath.exists():
            filepath.unlink()
            logger.info(
                "Deleted credential",
                provider_type=provider_type,
                provider_name=provider_name,
                account_id=account_id,
            )
            return True
        return False

    def list_credentials(self) -> list[dict[str, Any]]:
        """List all configured credentials (without secrets)."""
        credentials: list[dict[str, Any]] = []

        for filepath in self.credentials_dir.glob("*.enc"):
            parsed = self._parse_credential_key(filepath.stem)
            if parsed:
                provider_type, provider_name, account_id = parsed
                cred = self.get_credential(provider_type, provider_name, account_id)
                if cred:
                    credentials.append({
                        "provider_type": provider_type,
                        "provider_name": provider_name,
                        "account_id": account_id,
                        "account_label": cred.get("account_label", account_id),
                        "has_client_id": "client_id" in cred,
                        "has_client_secret": "client_secret" in cred,
                        "has_api_key": "api_key" in cred,
                        "has_access_token": "access_token" in cred,
                        "has_refresh_token": "refresh_token" in cred,
                        "has_tenant_id": "tenant_id" in cred,
                    })

        return credentials

    def list_accounts_for_provider(
        self,
        provider_type: str,
        provider_name: str,
    ) -> list[dict[str, str]]:
        """List all account IDs for a specific provider."""
        accounts = []
        prefix = f"{provider_type}__{provider_name}__"

        for filepath in self.credentials_dir.glob("*.enc"):
            if filepath.stem.startswith(prefix):
                parsed = self._parse_credential_key(filepath.stem)
                if parsed:
                    _, _, account_id = parsed
                    cred = self.get_credential(provider_type, provider_name, account_id)
                    accounts.append({
                        "account_id": account_id,
                        "account_label": cred.get("account_label", account_id) if cred else account_id,
                    })

        return accounts

    def export_to_env(self) -> dict[str, str]:
        """Export credentials as environment variables format."""
        env_vars: dict[str, str] = {}

        for filepath in self.credentials_dir.glob("*.enc"):
            parsed = self._parse_credential_key(filepath.stem)
            if parsed:
                provider_type, provider_name, account_id = parsed
                cred = self.get_credential(provider_type, provider_name, account_id)
                if cred:
                    safe_account = account_id.replace("@", "_").replace(".", "_").upper()
                    prefix = f"{provider_type.upper()}_{provider_name.upper()}_{safe_account}"

                    if cred.get("client_id"):
                        env_vars[f"{prefix}_CLIENT_ID"] = cred["client_id"]
                    if cred.get("client_secret"):
                        env_vars[f"{prefix}_CLIENT_SECRET"] = cred["client_secret"]
                    if cred.get("api_key"):
                        env_vars[f"{prefix}_API_KEY"] = cred["api_key"]
                    if cred.get("access_token"):
                        env_vars[f"{prefix}_ACCESS_TOKEN"] = cred["access_token"]
                    if cred.get("tenant_id"):
                        env_vars[f"{prefix}_TENANT_ID"] = cred["tenant_id"]

        return env_vars
