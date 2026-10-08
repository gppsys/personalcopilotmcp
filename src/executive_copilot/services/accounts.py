"""Connected accounts: status, health checks and credential changes.

Secrets never leave this module: listings report only whether a client,
token or key is present.
"""

from __future__ import annotations

from typing import Any

from executive_copilot.admin.credentials import OAUTH_FAMILIES, CredentialManager

# provider_name used in organizations.yaml -> OAuth family
PROVIDER_FAMILY = {
    "gmail": "google",
    "google": "google",
    "google_drive": "google",
    "outlook": "microsoft",
    "onedrive": "microsoft",
}
FAMILY_LABEL = {"google": "Google", "microsoft": "Microsoft"}
SERVICE_LABEL = {
    ("email", "gmail"): "Gmail",
    ("calendar", "google"): "Calendar",
    ("document", "google_drive"): "Drive",
    ("email", "outlook"): "Outlook",
    ("calendar", "outlook"): "Calendar",
    ("document", "onedrive"): "OneDrive",
}
# Token/API-key services: kind -> (provider_type, provider_name, field)
TOKEN_SERVICES: dict[str, tuple[str, str, str]] = {
    "asana": ("task", "asana", "access_token"),
    "slack": ("communication", "slack", "access_token"),
    "openai": ("ai", "openai", "api_key"),
}
TOKEN_LABEL = {"asana": "Asana", "slack": "Slack", "openai": "OpenAI (IA del servidor)"}


class AccountsService:
    """Read and change provider credentials for the workspace."""

    def __init__(self, config: Any, credential_manager: CredentialManager) -> None:
        self.config = config
        self.cm = credential_manager

    # --- listing ------------------------------------------------------------

    def _configured(self) -> dict[tuple[str, str], list[str]]:
        """(family_or_kind, account) -> organizations that use it."""
        used: dict[tuple[str, str], list[str]] = {}
        for org in self.config.organizations:
            for pa in org.provider_accounts:
                family = PROVIDER_FAMILY.get(pa.provider_name)
                kind = family or pa.provider_name
                orgs = used.setdefault((kind, pa.account_id), [])
                if org.name not in orgs:
                    orgs.append(org.name)
        return used

    def list_accounts(self) -> dict[str, Any]:
        configured = self._configured()
        oauth: list[dict[str, Any]] = []
        for family, siblings in OAUTH_FAMILIES.items():
            accounts: set[str] = {acc for (kind, acc) in configured if kind == family}
            for ptype, pname in siblings:
                accounts.update(a["account_id"] for a in self.cm.list_accounts_for_provider(ptype, pname))
            for account in sorted(accounts):
                oauth.append(self._oauth_status(family, account, configured.get((family, account), [])))

        tokens: list[dict[str, Any]] = []
        for kind, (ptype, pname, field) in TOKEN_SERVICES.items():
            accounts = {acc for (k, acc) in configured if k == pname}
            accounts.update(a["account_id"] for a in self.cm.list_accounts_for_provider(ptype, pname))
            if not accounts:
                accounts = {"default"}
            for account in sorted(accounts):
                cred = self.cm.get_credential(ptype, pname, account) or {}
                extra = cred.get("extra") or {}
                has = bool(cred.get(field) or cred.get("api_key") or cred.get("access_token") or extra.get("bot_token"))
                tokens.append({
                    "kind": kind,
                    "label": TOKEN_LABEL[kind],
                    "account_id": account,
                    "status": "connected" if has else "not_connected",
                    "used_by": configured.get((pname, account), []),
                })
        return {"oauth": oauth, "tokens": tokens}

    def _oauth_status(self, family: str, account: str, used_by: list[str]) -> dict[str, Any]:
        services = []
        label = None
        has_access = False
        for ptype, pname in OAUTH_FAMILIES[family]:
            cred = self.cm.get_credential(ptype, pname, account)
            if not cred:
                continue
            label = label or cred.get("account_label")
            if cred.get("access_token"):
                has_access = True
                services.append(SERVICE_LABEL[(ptype, pname)])
        has_client = self.cm.get_oauth_client(family, account) is not None
        has_refresh = self.cm.get_oauth_refresh_token(family, account) is not None
        if not has_client:
            status = "no_client"
        elif not has_access:
            status = "not_connected"
        elif not has_refresh:
            status = "no_refresh"
        else:
            status = "connected"
        return {
            "family": family,
            "provider": FAMILY_LABEL[family],
            "account_id": account,
            "label": label if label and label != account else None,
            "services": services,
            "status": status,
            "used_by": used_by,
        }

    # --- health checks ------------------------------------------------------

    async def test(self, kind: str, account: str) -> dict[str, Any]:
        """Make one cheap real call with the stored credentials."""
        try:
            detail = await self._probe(kind, account)
            return {"ok": True, "detail": detail}
        except Exception as e:
            message = str(e)
            if "expired" in message.lower() or "re-authorize" in message.lower() or "401" in message:
                message = "La sesión expiró: vuelve a conectar la cuenta."
            return {"ok": False, "detail": message[:300]}

    async def _probe(self, kind: str, account: str) -> str:
        if kind == "google":
            if self.cm.get_credential("calendar", "google", account):
                from executive_copilot.providers.calendar.google import GoogleCalendarProvider

                cals = await GoogleCalendarProvider(self.cm, account).list_calendars()
                return f"Calendar OK · {len(cals)} calendarios"
            from executive_copilot.providers.email.gmail import GmailProvider

            await GmailProvider(self.cm, account).list_messages(max_results=1)
            return "Gmail OK"
        if kind == "microsoft":
            if self.cm.get_credential("email", "outlook", account):
                from executive_copilot.providers.email.outlook import OutlookProvider

                await OutlookProvider(self.cm, account).list_messages(max_results=1)
                return "Outlook OK"
            from executive_copilot.providers.calendar.outlook import OutlookCalendarProvider

            cals = await OutlookCalendarProvider(self.cm, account).list_calendars()
            return f"Calendar OK · {len(cals)} calendarios"
        if kind == "asana":
            from executive_copilot.providers.task.asana import AsanaProvider

            me = await AsanaProvider(self.cm, account).get_me()
            return f"Asana OK · {me.get('name') or me.get('email')}"
        if kind == "slack":
            from executive_copilot.providers.communication import SlackProvider

            info = await SlackProvider(self.cm, account).auth_test()
            return f"Slack OK · {info.get('team') or ''}".strip()
        if kind == "openai":
            from openai import AsyncOpenAI

            cred = self.cm.get_credential("ai", "openai", account) or {}
            if not cred.get("api_key"):
                raise ValueError("No hay API key de OpenAI")
            client = AsyncOpenAI(api_key=cred["api_key"], timeout=15)
            await client.models.retrieve(self.config.ai.default_model)
            return f"OpenAI OK · {self.config.ai.default_model}"
        raise ValueError(f"Tipo desconocido: {kind}")

    # --- changes ------------------------------------------------------------

    def set_oauth_client(
        self,
        family: str,
        account: str,
        client_id: str | None,
        client_secret: str | None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Store the OAuth app on the account's main record.

        With no client_id, the client of another account in the same family is
        reused (one Google Cloud / Azure app usually serves every mailbox).
        """
        if family not in OAUTH_FAMILIES:
            raise ValueError(f"Unknown provider family: {family}")
        account = account.strip().lower()
        if not account or "@" not in account:
            raise ValueError("Escribe la dirección de correo de la cuenta")
        if not client_id:
            source = self._any_client(family)
            if source is None:
                raise ValueError("Falta el Client ID: no hay otra cuenta de la que reutilizarlo")
            client_id, client_secret = source["client_id"], source["client_secret"]
            tenant_id = tenant_id or source.get("tenant_id")
        ptype, pname = OAUTH_FAMILIES[family][0]
        self.cm.save_credential(
            provider_type=ptype,
            provider_name=pname,
            account_id=account,
            client_id=client_id,
            client_secret=client_secret or None,
            tenant_id=(tenant_id or None) if family == "microsoft" else None,
        )
        return self._oauth_status(family, account, self._configured().get((family, account), []))

    def _any_client(self, family: str) -> dict[str, str] | None:
        for ptype, pname in OAUTH_FAMILIES[family]:
            for acc in self.cm.list_accounts_for_provider(ptype, pname):
                client = self.cm.get_oauth_client(family, acc["account_id"])
                if client:
                    return client
        return None

    def save_token(self, kind: str, account: str, token: str) -> dict[str, Any]:
        if kind not in TOKEN_SERVICES:
            raise ValueError(f"Unknown service: {kind}")
        if not token.strip():
            raise ValueError("El token está vacío")
        ptype, pname, field = TOKEN_SERVICES[kind]
        value = token.strip()
        if field == "api_key":
            self.cm.save_credential(ptype, pname, account or "default", api_key=value)
        else:
            self.cm.save_credential(ptype, pname, account or "default", access_token=value)
        return {"kind": kind, "account_id": account or "default", "status": "connected"}

    def disconnect(self, family: str, account: str) -> dict[str, Any]:
        """Remove tokens, keep the OAuth client so reconnecting is one click."""
        changed = self.cm.clear_oauth_tokens(family, account)
        return {"account_id": account, "records": changed}

    def remove(self, kind: str, account: str) -> dict[str, Any]:
        """Delete the account everywhere: stored credentials (including the
        OAuth client) and its entries in organizations.yaml, so it no longer
        appears in the workspace or gets queried."""
        removed = 0
        if kind in OAUTH_FAMILIES:
            pairs = OAUTH_FAMILIES[kind]
            names = {pname for _, pname in pairs}
        elif kind in TOKEN_SERVICES:
            ptype, pname, _ = TOKEN_SERVICES[kind]
            pairs = [(ptype, pname)]
            names = {pname}
        else:
            raise ValueError(f"Unknown kind: {kind}")
        for ptype, pname in pairs:
            removed += int(self.cm.delete_credential(ptype, pname, account))
        unlinked = self._remove_from_organizations(names, account)
        return {"account_id": account, "removed": removed, "removed_from_organizations": unlinked}

    def _remove_from_organizations(self, provider_names: set[str], account: str) -> list[str]:
        from executive_copilot.admin.config_manager import ConfigManager

        manager = ConfigManager(self.config.config_dir)
        organizations = manager.load_organizations()
        touched: list[str] = []
        for org in organizations:
            before = org.get("provider_accounts") or []
            after = [
                pa for pa in before
                if not (pa.get("provider_name") in provider_names and pa.get("account_id") == account)
            ]
            if len(after) != len(before):
                org["provider_accounts"] = after
                touched.append(org.get("name", "?"))
        if touched:
            manager.save_organizations(organizations)
        return touched

    # --- calendars ------------------------------------------------------------

    async def calendars(self) -> dict[str, Any]:
        """Every writable calendar of every connected Google/Microsoft account."""
        from executive_copilot.providers.calendar.google import GoogleCalendarProvider
        from executive_copilot.providers.calendar.outlook import OutlookCalendarProvider

        out: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for provider_name, cls in (("google", GoogleCalendarProvider), ("outlook", OutlookCalendarProvider)):
            for acc in self.cm.list_accounts_for_provider("calendar", provider_name):
                account = acc["account_id"]
                cred = self.cm.get_credential("calendar", provider_name, account) or {}
                if not cred.get("access_token"):
                    continue
                try:
                    for cal in await cls(self.cm, account).list_calendars():
                        out.append({
                            "provider": provider_name,
                            "account_id": account,
                            "calendar_id": "primary" if cal.is_primary else cal.id,
                            "name": cal.name,
                            "primary": cal.is_primary,
                            "can_edit": cal.can_edit,
                            "color": cal.color,
                        })
                except Exception as e:
                    errors.append({"account": account, "provider": provider_name, "error": str(e)[:200]})
        out.sort(key=lambda c: (c["account_id"], not c["primary"], c["name"].lower()))
        return {"calendars": out, "errors": errors}
