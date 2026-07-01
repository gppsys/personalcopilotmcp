"""Admin panel FastAPI application."""

import asyncio  # noqa: TC003 - needed at runtime for Event type
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from executive_copilot.admin.config_manager import ConfigManager
from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.admin.oauth import OAuthManager
from executive_copilot.logging import get_logger

logger = get_logger(__name__)

# Pydantic models for API
class GeneralSettings(BaseModel):
    environment: str = "development"
    debug: bool = True
    log_level: str = "INFO"
    ai_provider: str = "openai"
    default_model: str = "gpt-4o-mini"
    premium_model: str = "gpt-4o"
    max_tokens: int = 4096
    temperature: float = 0.7
    azure_endpoint: str | None = None
    azure_deployment: str | None = None
    local_base_url: str | None = None


class DatabaseSettings(BaseModel):
    driver: str = "sqlite+aiosqlite"
    host: str = "localhost"
    port: int = 5432
    name: str = "executive_copilot.db"
    user: str | None = None
    password: str | None = None


class ProviderAccountInput(BaseModel):
    provider_type: str
    provider_name: str
    account_id: str


class OrganizationInput(BaseModel):
    name: str
    short_name: str | None = None
    organization_type: str
    domains: list[str] = Field(default_factory=list)
    context_sources: list[str] = Field(default_factory=list)
    provider_accounts: list[ProviderAccountInput] = Field(default_factory=list)
    importance: int = 5
    tags: list[str] = Field(default_factory=list)


class ContextRuleInput(BaseModel):
    name: str
    description: str | None = None
    priority: int = 0
    enabled: bool = True
    conditions: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)


class ProviderCredentials(BaseModel):
    provider_type: str
    provider_name: str
    account_id: str = "default"
    account_label: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    api_key: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    tenant_id: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class AutomationJobInput(BaseModel):
    job_name: str
    job_type: str
    enabled: bool = True
    schedule: str
    settings: dict[str, Any] = Field(default_factory=dict)


def create_admin_app(config_dir: Path | None = None, shutdown_event: "asyncio.Event | None" = None) -> FastAPI:
    """Create the admin panel FastAPI application."""
    import os
    import signal

    app = FastAPI(
        title="Executive Copilot Admin",
        description="Configuration panel for Executive Copilot",
        version="0.1.0",
    )

    # Store shutdown event for graceful shutdown
    app.state.shutdown_event = shutdown_event

    # Initialize managers
    config_dir = config_dir or Path("config")
    config_manager = ConfigManager(config_dir)
    credential_manager = CredentialManager(config_dir / ".credentials")
    oauth_manager = OAuthManager(credential_manager)

    # Mount static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # --- Shutdown Routes ---

    @app.post("/api/shutdown")
    async def shutdown_server() -> dict[str, str]:
        """Gracefully shutdown the admin server."""
        logger.info("Shutdown requested via API")

        if app.state.shutdown_event:
            app.state.shutdown_event.set()
        else:
            os.kill(os.getpid(), signal.SIGTERM)

        return {"status": "shutting_down", "message": "Server is shutting down gracefully"}

    @app.get("/api/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy", "service": "executive-copilot-admin"}

    # --- HTML Routes ---

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """Serve the admin panel."""
        return get_admin_html()

    # --- OAuth Routes ---

    @app.get("/auth/google/start")
    async def start_google_auth(account: str, request: Request) -> HTMLResponse:
        """Start Google OAuth flow for an account."""
        # Get client credentials from stored config
        cred = credential_manager.get_credential("email", "gmail", account)
        if not cred or not cred.get("client_id"):
            raise HTTPException(
                status_code=400,
                detail=f"No client_id configured for {account}. Add OAuth credentials first."
            )

        # Build redirect URI
        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/auth/google/callback"

        auth_url = oauth_manager.start_google_auth(
            account_id=account,
            client_id=cred["client_id"],
            redirect_uri=redirect_uri,
        )

        return HTMLResponse(f'''
            <html>
            <head><title>Redirecting to Google...</title></head>
            <body>
                <p>Redirecting to Google for authorization...</p>
                <p>Account: {account}</p>
                <script>window.location.href = "{auth_url}";</script>
                <noscript><a href="{auth_url}">Click here to continue</a></noscript>
            </body>
            </html>
        ''')

    @app.get("/auth/google/callback")
    async def google_oauth_callback(code: str, state: str, request: Request) -> HTMLResponse:
        """Handle Google OAuth callback."""
        try:
            oauth_state = oauth_manager.get_pending_state(state)
            if not oauth_state:
                raise HTTPException(status_code=400, detail="Invalid or expired state token")

            gmail_cred = credential_manager.get_credential("email", "gmail", oauth_state.account_id)
            if not gmail_cred or not gmail_cred.get("client_id"):
                raise HTTPException(
                    status_code=400,
                    detail=f"No Google OAuth credentials found for {oauth_state.account_id}",
                )

            result = await oauth_manager.complete_google_auth(
                code=code,
                state=state,
                client_id=gmail_cred["client_id"],
                client_secret=gmail_cred.get("client_secret", ""),
            )

            return HTMLResponse(f'''
                <html>
                <head>
                    <title>Authorization Complete</title>
                    <style>
                        body {{ font-family: system-ui; padding: 40px; text-align: center; }}
                        .success {{ color: green; font-size: 24px; }}
                        .info {{ color: #666; margin-top: 20px; }}
                    </style>
                </head>
                <body>
                    <div class="success">✓ Google Authorization Complete</div>
                    <div class="info">
                        <p><strong>Account:</strong> {result.get("email", result.get("account_id"))}</p>
                        <p><strong>Name:</strong> {result.get("name", "N/A")}</p>
                        <p>Gmail and Google Calendar access has been granted.</p>
                        <p><a href="/">Return to Admin Panel</a></p>
                    </div>
                </body>
                </html>
            ''')

        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error("Google OAuth callback failed", error=str(e))
            raise HTTPException(status_code=500, detail=f"Authorization failed: {e!s}")

    @app.get("/auth/microsoft/start")
    async def start_microsoft_auth(account: str, request: Request, tenant: str = "common") -> HTMLResponse:
        """Start Microsoft OAuth flow for an account."""
        # Get client credentials
        cred = credential_manager.get_credential("email", "outlook", account)
        if not cred or not cred.get("client_id"):
            raise HTTPException(
                status_code=400,
                detail=f"No client_id configured for {account}. Add OAuth credentials first."
            )

        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/auth/microsoft/callback"

        auth_url = oauth_manager.start_microsoft_auth(
            account_id=account,
            client_id=cred["client_id"],
            redirect_uri=redirect_uri,
            tenant=cred.get("tenant_id", tenant),
        )

        return HTMLResponse(f'''
            <html>
            <head><title>Redirecting to Microsoft...</title></head>
            <body>
                <p>Redirecting to Microsoft for authorization...</p>
                <p>Account: {account}</p>
                <script>window.location.href = "{auth_url}";</script>
                <noscript><a href="{auth_url}">Click here to continue</a></noscript>
            </body>
            </html>
        ''')

    @app.get("/auth/microsoft/callback")
    async def microsoft_oauth_callback(code: str, state: str, request: Request) -> HTMLResponse:
        """Handle Microsoft OAuth callback."""
        try:
            oauth_state = oauth_manager.get_pending_state(state)
            if not oauth_state:
                raise HTTPException(status_code=400, detail="Invalid or expired state token")

            outlook_cred = credential_manager.get_credential(
                "email",
                "outlook",
                oauth_state.account_id,
            )
            if not outlook_cred or not outlook_cred.get("client_id"):
                raise HTTPException(
                    status_code=400,
                    detail=f"No Microsoft OAuth credentials found for {oauth_state.account_id}",
                )

            result = await oauth_manager.complete_microsoft_auth(
                code=code,
                state=state,
                client_id=outlook_cred["client_id"],
                client_secret=outlook_cred.get("client_secret", ""),
                tenant=outlook_cred.get("tenant_id", "common"),
            )

            return HTMLResponse(f'''
                <html>
                <head>
                    <title>Authorization Complete</title>
                    <style>
                        body {{ font-family: system-ui; padding: 40px; text-align: center; }}
                        .success {{ color: green; font-size: 24px; }}
                        .info {{ color: #666; margin-top: 20px; }}
                    </style>
                </head>
                <body>
                    <div class="success">✓ Microsoft Authorization Complete</div>
                    <div class="info">
                        <p><strong>Account:</strong> {result.get("email", result.get("account_id"))}</p>
                        <p><strong>Name:</strong> {result.get("name", "N/A")}</p>
                        <p>Outlook Mail, Calendar and OneDrive access has been granted.</p>
                        <p><a href="/">Return to Admin Panel</a></p>
                    </div>
                </body>
                </html>
            ''')

        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error("Microsoft OAuth callback failed", error=str(e))
            raise HTTPException(status_code=500, detail=f"Authorization failed: {e!s}")

    @app.get("/auth/accounts")
    async def list_accounts_for_auth() -> list[dict[str, Any]]:
        """List accounts that need authorization."""
        creds = credential_manager.list_credentials()
        accounts = []
        seen = set()

        for cred in creds:
            if cred["provider_type"] in ["email", "calendar"]:
                key = f"{cred['provider_name']}:{cred['account_id']}"
                if key in seen:
                    continue
                seen.add(key)

                has_access_token = cred.get("has_access_token", False)
                accounts.append({
                    "provider_type": cred["provider_type"],
                    "provider_name": cred["provider_name"],
                    "account_id": cred["account_id"],
                    "account_label": cred.get("account_label", cred["account_id"]),
                    "authorized": has_access_token,
                    "has_client_id": cred.get("has_client_id", False),
                    "has_client_secret": cred.get("has_client_secret", False),
                    "auth_url": f"/auth/{'google' if cred['provider_name'] in ['gmail', 'google'] else 'microsoft'}/start?account={cred['account_id']}",
                })

        return accounts

    @app.get("/auth", response_class=HTMLResponse)
    async def auth_page() -> str:
        """Show authorization status for all accounts."""
        return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Authorize Accounts - Executive Copilot</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen p-8">
    <div class="max-w-2xl mx-auto">
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
            <h1 class="text-2xl font-bold text-gray-800">Authorize Accounts</h1>
            <p class="text-gray-600 mt-2">Click on an account to authorize access to email and calendar.</p>
            <p class="text-sm text-gray-500 mt-1">You need to have added OAuth client credentials first.</p>
        </div>

        <div id="accounts" class="space-y-4">
            <p class="text-gray-500">Loading accounts...</p>
        </div>

        <div class="mt-6">
            <a href="/" class="text-blue-600 hover:text-blue-800">← Back to Admin Panel</a>
        </div>
    </div>

    <script>
        async function loadAccounts() {
            const response = await fetch('/auth/accounts');
            const accounts = await response.json();

            const container = document.getElementById('accounts');

            if (accounts.length === 0) {
                container.innerHTML = '<p class="text-gray-500">No accounts configured. Add credentials in the Credentials tab first.</p>';
                return;
            }

            // Group by account_id
            const grouped = {};
            accounts.forEach(acc => {
                if (!grouped[acc.account_id]) {
                    grouped[acc.account_id] = acc;
                }
            });

            container.innerHTML = Object.values(grouped).map(acc => `
                <div class="bg-white rounded-lg shadow-sm p-4 flex items-center justify-between">
                    <div>
                        <div class="font-medium">${acc.account_label || acc.account_id}</div>
                        <div class="text-sm text-gray-500">${acc.provider_name} (${acc.provider_type})</div>
                    </div>
                    <div class="flex items-center gap-3">
                        ${acc.authorized ? '<span class="text-green-600 text-sm">✓ Authorized</span>' : ''}
                        ${acc.has_client_id
                            ? `<a href="${acc.auth_url}" class="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700 text-sm">${acc.authorized ? 'Re-authorize' : 'Authorize'}</a>`
                            : '<span class="text-amber-600 text-sm">Add OAuth client credentials</span>'
                        }
                    </div>
                </div>
            `).join('');
        }

        loadAccounts();
    </script>
</body>
</html>
        '''

    # --- Test Endpoints ---

    @app.get("/api/test/gmail/{account_id:path}")
    async def test_gmail_account(account_id: str) -> dict[str, Any]:
        """Test Gmail connection for an account."""
        from executive_copilot.providers.email.gmail import test_gmail_connection
        result = await test_gmail_connection(credential_manager, account_id)
        return result

    @app.get("/api/test/gmail/{account_id:path}/inbox")
    async def get_gmail_inbox(account_id: str, max_results: int = 10) -> dict[str, Any]:
        """Get inbox emails for a Gmail account."""
        from executive_copilot.providers.email.gmail import GmailProvider

        provider = GmailProvider(credential_manager, account_id)
        emails = await provider.get_inbox(max_results=max_results)

        return {
            "account_id": account_id,
            "count": len(emails),
            "emails": [
                {
                    "id": str(e.id),
                    "subject": e.subject,
                    "from": e.sender.address,
                    "from_name": e.sender.display_name,
                    "snippet": e.snippet,
                    "received_at": e.received_at.isoformat(),
                    "is_read": e.is_read,
                    "is_starred": e.is_starred,
                    "has_attachments": e.has_attachments,
                }
                for e in emails
            ],
        }

    @app.get("/api/test/gmail/{account_id:path}/unread")
    async def get_gmail_unread(account_id: str, max_results: int = 10) -> dict[str, Any]:
        """Get unread emails for a Gmail account."""
        from executive_copilot.providers.email.gmail import GmailProvider

        provider = GmailProvider(credential_manager, account_id)
        emails = await provider.get_unread(max_results=max_results)

        return {
            "account_id": account_id,
            "count": len(emails),
            "emails": [
                {
                    "id": str(e.id),
                    "subject": e.subject,
                    "from": e.sender.address,
                    "from_name": e.sender.display_name,
                    "snippet": e.snippet,
                    "received_at": e.received_at.isoformat(),
                }
                for e in emails
            ],
        }

    @app.get("/api/test/asana/{account_id:path}")
    async def test_asana_account(account_id: str) -> dict[str, Any]:
        """Test Asana connection for an account."""
        from executive_copilot.providers.task.asana import test_asana_connection

        return await test_asana_connection(credential_manager, account_id)

    # --- General Settings ---

    @app.get("/api/settings/general")
    async def get_general_settings() -> GeneralSettings:
        """Get general settings."""
        config = config_manager.load_config()
        ai_config = config.get("ai", {})
        legacy_model_routing = config.get("model_routing", {})
        return GeneralSettings(
            environment=config.get("environment", "development"),
            debug=config.get("debug", True),
            log_level=config.get("logging", {}).get("level", "INFO"),
            ai_provider=ai_config.get("provider", "openai"),
            default_model=ai_config.get(
                "default_model",
                legacy_model_routing.get("default_model", "gpt-4o-mini"),
            ),
            premium_model=ai_config.get("premium_model", "gpt-4o"),
            max_tokens=ai_config.get(
                "max_output_tokens",
                legacy_model_routing.get("max_tokens", 4096),
            ),
            temperature=ai_config.get(
                "temperature",
                legacy_model_routing.get("temperature", 0.7),
            ),
            azure_endpoint=ai_config.get("azure_endpoint"),
            azure_deployment=ai_config.get("azure_deployment"),
            local_base_url=ai_config.get("local_base_url"),
        )

    @app.put("/api/settings/general")
    async def update_general_settings(settings: GeneralSettings) -> dict[str, str]:
        """Update general settings."""
        ai_config: dict[str, Any] = {
            "provider": settings.ai_provider,
            "default_model": settings.default_model,
            "premium_model": settings.premium_model,
            "max_output_tokens": settings.max_tokens,
            "temperature": settings.temperature,
        }
        if settings.azure_endpoint:
            ai_config["azure_endpoint"] = settings.azure_endpoint
        if settings.azure_deployment:
            ai_config["azure_deployment"] = settings.azure_deployment
        if settings.local_base_url:
            ai_config["local_base_url"] = settings.local_base_url

        config_manager.update_config({
            "environment": settings.environment,
            "debug": settings.debug,
            "logging": {"level": settings.log_level},
            "ai": ai_config,
        })
        return {"status": "ok"}

    # --- Database Settings ---

    @app.get("/api/settings/database")
    async def get_database_settings() -> DatabaseSettings:
        """Get database settings."""
        config = config_manager.load_config()
        db = config.get("database", {})
        return DatabaseSettings(
            driver=db.get("driver", "sqlite+aiosqlite"),
            host=db.get("host", "localhost"),
            port=db.get("port", 5432),
            name=db.get("name", "executive_copilot.db"),
            user=db.get("user"),
            password=None,  # Don't expose password
        )

    @app.put("/api/settings/database")
    async def update_database_settings(settings: DatabaseSettings) -> dict[str, str]:
        """Update database settings."""
        db_config: dict[str, Any] = {
            "driver": settings.driver,
            "name": settings.name,
        }
        if settings.driver.startswith("postgresql"):
            db_config["host"] = settings.host
            db_config["port"] = settings.port
            if settings.user:
                db_config["user"] = settings.user
            if settings.password:
                db_config["password"] = settings.password

        config_manager.update_config({"database": db_config})
        return {"status": "ok"}

    # --- Organizations ---

    @app.get("/api/organizations")
    async def list_organizations() -> list[dict[str, Any]]:
        """List all organizations."""
        return config_manager.load_organizations()

    @app.post("/api/organizations")
    async def create_organization(org: OrganizationInput) -> dict[str, str]:
        """Create a new organization."""
        orgs = config_manager.load_organizations()
        orgs.append(org.model_dump())
        config_manager.save_organizations(orgs)
        return {"status": "ok", "name": org.name}

    @app.put("/api/organizations/{name}")
    async def update_organization(name: str, org: OrganizationInput) -> dict[str, str]:
        """Update an organization."""
        orgs = config_manager.load_organizations()
        for i, existing in enumerate(orgs):
            if existing.get("name") == name:
                orgs[i] = org.model_dump()
                config_manager.save_organizations(orgs)
                return {"status": "ok"}
        raise HTTPException(status_code=404, detail="Organization not found")

    @app.delete("/api/organizations/{name}")
    async def delete_organization(name: str) -> dict[str, str]:
        """Delete an organization."""
        orgs = config_manager.load_organizations()
        orgs = [o for o in orgs if o.get("name") != name]
        config_manager.save_organizations(orgs)
        return {"status": "ok"}

    # --- Context Rules ---

    @app.get("/api/context-rules")
    async def list_context_rules() -> list[dict[str, Any]]:
        """List all context rules."""
        return config_manager.load_context_rules()

    @app.post("/api/context-rules")
    async def create_context_rule(rule: ContextRuleInput) -> dict[str, str]:
        """Create a new context rule."""
        rules = config_manager.load_context_rules()
        rules.append(rule.model_dump())
        config_manager.save_context_rules(rules)
        return {"status": "ok", "name": rule.name}

    @app.put("/api/context-rules/{name}")
    async def update_context_rule(name: str, rule: ContextRuleInput) -> dict[str, str]:
        """Update a context rule."""
        rules = config_manager.load_context_rules()
        for i, existing in enumerate(rules):
            if existing.get("name") == name:
                rules[i] = rule.model_dump()
                config_manager.save_context_rules(rules)
                return {"status": "ok"}
        raise HTTPException(status_code=404, detail="Rule not found")

    @app.delete("/api/context-rules/{name}")
    async def delete_context_rule(name: str) -> dict[str, str]:
        """Delete a context rule."""
        rules = config_manager.load_context_rules()
        rules = [r for r in rules if r.get("name") != name]
        config_manager.save_context_rules(rules)
        return {"status": "ok"}

    # --- Automation Jobs ---

    @app.get("/api/automation")
    async def list_automation_jobs() -> list[dict[str, Any]]:
        """List all automation jobs."""
        return config_manager.load_automation_jobs()

    @app.post("/api/automation")
    async def create_automation_job(job: AutomationJobInput) -> dict[str, str]:
        """Create a new automation job."""
        jobs = config_manager.load_automation_jobs()
        jobs.append(job.model_dump())
        config_manager.save_automation_jobs(jobs)
        return {"status": "ok", "name": job.job_name}

    @app.put("/api/automation/{name}")
    async def update_automation_job(name: str, job: AutomationJobInput) -> dict[str, str]:
        """Update an automation job."""
        jobs = config_manager.load_automation_jobs()
        for i, existing in enumerate(jobs):
            if existing.get("job_name") == name:
                jobs[i] = job.model_dump()
                config_manager.save_automation_jobs(jobs)
                return {"status": "ok"}
        raise HTTPException(status_code=404, detail="Job not found")

    @app.delete("/api/automation/{name}")
    async def delete_automation_job(name: str) -> dict[str, str]:
        """Delete an automation job."""
        jobs = config_manager.load_automation_jobs()
        jobs = [j for j in jobs if j.get("job_name") != name]
        config_manager.save_automation_jobs(jobs)
        return {"status": "ok"}

    # --- Credentials ---

    @app.get("/api/credentials")
    async def list_credentials() -> list[dict[str, Any]]:
        """List configured credentials (without secrets)."""
        return credential_manager.list_credentials()

    @app.post("/api/credentials")
    async def save_credentials(creds: ProviderCredentials) -> dict[str, str]:
        """Save provider credentials."""
        credential_manager.save_credential(
            provider_type=creds.provider_type,
            provider_name=creds.provider_name,
            account_id=creds.account_id,
            account_label=creds.account_label,
            client_id=creds.client_id,
            client_secret=creds.client_secret,
            api_key=creds.api_key,
            access_token=creds.access_token,
            refresh_token=creds.refresh_token,
            tenant_id=creds.tenant_id,
            extra=creds.extra,
        )
        return {"status": "ok"}

    @app.delete("/api/credentials/{provider_type}/{provider_name}/{account_id:path}")
    async def delete_credentials(provider_type: str, provider_name: str, account_id: str) -> dict[str, str]:
        """Delete provider credentials for a specific account."""
        credential_manager.delete_credential(provider_type, provider_name, account_id)
        return {"status": "ok"}

    @app.get("/api/credentials/{provider_type}/{provider_name}")
    async def list_provider_accounts(provider_type: str, provider_name: str) -> list[dict[str, str]]:
        """List all accounts for a specific provider."""
        return credential_manager.list_accounts_for_provider(provider_type, provider_name)

    # --- Available Options ---

    @app.get("/api/options/ai-providers")
    async def get_ai_providers() -> list[dict[str, str]]:
        """Get available AI providers."""
        return [
            {"id": "openai", "name": "OpenAI"},
            {"id": "anthropic", "name": "Anthropic"},
            {"id": "azure_openai", "name": "Azure OpenAI"},
            {"id": "google", "name": "Google (Gemini)"},
            {"id": "local", "name": "Local (Ollama/LM Studio)"},
        ]

    @app.get("/api/options/models")
    async def get_available_models() -> dict[str, list[dict[str, str]]]:
        """Get available AI models by provider."""
        return {
            "openai": [
                {"id": "gpt-4o", "name": "GPT-4o (Most capable)"},
                {"id": "gpt-4o-mini", "name": "GPT-4o Mini (Recommended)"},
                {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
                {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo (Fastest)"},
                {"id": "o1", "name": "o1 (Reasoning)"},
                {"id": "o1-mini", "name": "o1-mini (Fast reasoning)"},
            ],
            "anthropic": [
                {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4 (Recommended)"},
                {"id": "claude-opus-4-20250514", "name": "Claude Opus 4 (Most capable)"},
                {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet"},
                {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku (Fastest)"},
            ],
            "azure_openai": [
                {"id": "gpt-4o", "name": "GPT-4o (deployment name)"},
                {"id": "gpt-4o-mini", "name": "GPT-4o Mini (deployment name)"},
                {"id": "gpt-4-turbo", "name": "GPT-4 Turbo (deployment name)"},
            ],
            "google": [
                {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro"},
                {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash (Fast)"},
                {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
            ],
            "local": [
                {"id": "llama3.2", "name": "Llama 3.2"},
                {"id": "llama3.1", "name": "Llama 3.1"},
                {"id": "mistral", "name": "Mistral"},
                {"id": "mixtral", "name": "Mixtral"},
                {"id": "codellama", "name": "Code Llama"},
                {"id": "phi3", "name": "Phi-3"},
                {"id": "custom", "name": "Custom (enter model name)"},
            ],
        }

    @app.get("/api/options/context-sources")
    async def get_context_sources() -> list[dict[str, str]]:
        """Get available context sources."""
        return [
            {"id": "asana", "name": "Asana", "category": "tasks"},
            {"id": "outlook", "name": "Outlook Email", "category": "email"},
            {"id": "gmail", "name": "Gmail", "category": "email"},
            {"id": "google_calendar", "name": "Google Calendar", "category": "calendar"},
            {"id": "outlook_calendar", "name": "Outlook Calendar", "category": "calendar"},
            {"id": "google_drive", "name": "Google Drive", "category": "documents"},
            {"id": "onedrive", "name": "OneDrive", "category": "documents"},
            {"id": "slack", "name": "Slack", "category": "communication"},
            {"id": "github", "name": "GitHub", "category": "development"},
            {"id": "memory", "name": "Memory", "category": "internal"},
            {"id": "organizations", "name": "Organizations", "category": "internal"},
            {"id": "people", "name": "People", "category": "internal"},
            {"id": "conversations", "name": "Conversations", "category": "internal"},
            {"id": "decisions", "name": "Decisions", "category": "internal"},
            {"id": "follow_ups", "name": "Follow-ups", "category": "internal"},
            {"id": "documentation", "name": "Documentation", "category": "content"},
            {"id": "previous_threads", "name": "Previous Threads", "category": "email"},
            {"id": "calendar", "name": "Calendar", "category": "calendar"},
            {"id": "tasks", "name": "Tasks", "category": "tasks"},
            {"id": "news", "name": "News", "category": "content"},
        ]

    @app.get("/api/options/organization-types")
    async def get_organization_types() -> list[dict[str, str]]:
        """Get available organization types."""
        return [
            {"id": "employer", "name": "Employer"},
            {"id": "client", "name": "Client"},
            {"id": "vendor", "name": "Vendor"},
            {"id": "partner", "name": "Partner"},
            {"id": "investor", "name": "Investor"},
            {"id": "school", "name": "School"},
            {"id": "government", "name": "Government"},
            {"id": "nonprofit", "name": "Nonprofit"},
            {"id": "personal", "name": "Personal"},
            {"id": "other", "name": "Other"},
        ]

    @app.get("/api/options/job-types")
    async def get_job_types() -> list[dict[str, str]]:
        """Get available automation job types."""
        return [
            {"id": "email_sync", "name": "Email Synchronization"},
            {"id": "calendar_sync", "name": "Calendar Synchronization"},
            {"id": "task_sync", "name": "Task Synchronization"},
            {"id": "news_sync", "name": "News Synchronization"},
            {"id": "daily_briefing", "name": "Daily Briefing"},
            {"id": "follow_up_detection", "name": "Follow-up Detection"},
        ]

    @app.get("/api/options/provider-types")
    async def get_provider_types() -> list[dict[str, Any]]:
        """Get available provider types with their credential requirements."""
        return [
            {
                "type": "ai",
                "providers": [
                    {"id": "openai", "name": "OpenAI", "auth": "api_key", "fields": ["api_key"]},
                    {"id": "anthropic", "name": "Anthropic", "auth": "api_key", "fields": ["api_key"]},
                    {"id": "azure_openai", "name": "Azure OpenAI", "auth": "api_key", "fields": ["api_key"]},
                    {"id": "google", "name": "Google AI", "auth": "api_key", "fields": ["api_key"]},
                ]
            },
            {
                "type": "email",
                "providers": [
                    {"id": "outlook", "name": "Microsoft Outlook", "auth": "oauth", "fields": ["client_id", "client_secret"]},
                    {"id": "gmail", "name": "Gmail", "auth": "oauth", "fields": ["client_id", "client_secret"]},
                ]
            },
            {
                "type": "calendar",
                "providers": [
                    {"id": "outlook", "name": "Outlook Calendar", "auth": "oauth", "fields": ["client_id", "client_secret"]},
                    {"id": "google", "name": "Google Calendar", "auth": "oauth", "fields": ["client_id", "client_secret"]},
                ]
            },
            {
                "type": "task",
                "providers": [
                    {"id": "asana", "name": "Asana", "auth": "token", "fields": ["access_token"]},
                    {"id": "todoist", "name": "Todoist", "auth": "token", "fields": ["api_key"]},
                ]
            },
            {
                "type": "document",
                "providers": [
                    {"id": "google_drive", "name": "Google Drive", "auth": "oauth", "fields": ["client_id", "client_secret"]},
                    {"id": "onedrive", "name": "OneDrive", "auth": "oauth", "fields": ["client_id", "client_secret"]},
                ]
            },
            {
                "type": "communication",
                "providers": [
                    {"id": "slack", "name": "Slack", "auth": "token", "fields": ["bot_token", "app_token"]},
                ]
            },
            {
                "type": "development",
                "providers": [
                    {"id": "github", "name": "GitHub", "auth": "token", "fields": ["access_token"]},
                ]
            },
        ]

    return app


def get_admin_html() -> str:
    """Generate the admin panel HTML."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Executive Copilot Admin</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
    <style>
        [x-cloak] { display: none !important; }
        .tab-active { border-bottom: 2px solid #3b82f6; color: #3b82f6; }
    </style>
</head>
<body class="bg-gray-100 min-h-screen" x-data="adminApp()" x-init="init()">
    <div class="container mx-auto px-4 py-8 max-w-6xl">
        <!-- Header -->
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6 flex justify-between items-center">
            <div>
                <h1 class="text-2xl font-bold text-gray-800">Executive Copilot</h1>
                <p class="text-gray-600">Configuration Panel</p>
            </div>
            <button @click="shutdownServer()"
                    class="px-4 py-2 bg-red-500 hover:bg-red-600 text-white rounded-md text-sm font-medium transition-colors">
                Stop Server
            </button>
        </div>

        <!-- Tabs -->
        <div class="bg-white rounded-lg shadow-sm mb-6">
            <div class="border-b border-gray-200">
                <nav class="flex -mb-px">
                    <button @click="activeTab = 'general'"
                            :class="activeTab === 'general' ? 'tab-active' : 'text-gray-500 hover:text-gray-700'"
                            class="px-6 py-4 text-sm font-medium">
                        General
                    </button>
                    <button @click="activeTab = 'organizations'"
                            :class="activeTab === 'organizations' ? 'tab-active' : 'text-gray-500 hover:text-gray-700'"
                            class="px-6 py-4 text-sm font-medium">
                        Organizations
                    </button>
                    <button @click="activeTab = 'context'"
                            :class="activeTab === 'context' ? 'tab-active' : 'text-gray-500 hover:text-gray-700'"
                            class="px-6 py-4 text-sm font-medium">
                        Context Rules
                    </button>
                    <button @click="activeTab = 'credentials'"
                            :class="activeTab === 'credentials' ? 'tab-active' : 'text-gray-500 hover:text-gray-700'"
                            class="px-6 py-4 text-sm font-medium">
                        Credentials
                    </button>
                    <button @click="activeTab = 'automation'"
                            :class="activeTab === 'automation' ? 'tab-active' : 'text-gray-500 hover:text-gray-700'"
                            class="px-6 py-4 text-sm font-medium">
                        Automation
                    </button>
                </nav>
            </div>

            <div class="p-6">
                <!-- General Settings Tab -->
                <div x-show="activeTab === 'general'" x-cloak>
                    <h2 class="text-lg font-semibold mb-4">General Settings</h2>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Environment</label>
                            <select x-model="settings.general.environment"
                                    class="w-full border border-gray-300 rounded-md px-3 py-2">
                                <option value="development">Development</option>
                                <option value="staging">Staging</option>
                                <option value="production">Production</option>
                            </select>
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Log Level</label>
                            <select x-model="settings.general.log_level"
                                    class="w-full border border-gray-300 rounded-md px-3 py-2">
                                <option value="DEBUG">Debug</option>
                                <option value="INFO">Info</option>
                                <option value="WARNING">Warning</option>
                                <option value="ERROR">Error</option>
                            </select>
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">AI Provider</label>
                            <select x-model="settings.general.ai_provider"
                                    class="w-full border border-gray-300 rounded-md px-3 py-2">
                                <template x-for="provider in options.aiProviders" :key="provider.id">
                                    <option :value="provider.id" x-text="provider.name"></option>
                                </template>
                            </select>
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Default Model</label>
                            <select x-model="settings.general.default_model"
                                    class="w-full border border-gray-300 rounded-md px-3 py-2">
                                <template x-for="model in getModelsForProvider(settings.general.ai_provider)" :key="model.id">
                                    <option :value="model.id" x-text="model.name"></option>
                                </template>
                            </select>
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Premium Model</label>
                            <select x-model="settings.general.premium_model"
                                    class="w-full border border-gray-300 rounded-md px-3 py-2">
                                <template x-for="model in getModelsForProvider(settings.general.ai_provider)" :key="model.id">
                                    <option :value="model.id" x-text="model.name"></option>
                                </template>
                            </select>
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Max Tokens</label>
                            <input type="number" x-model.number="settings.general.max_tokens"
                                   class="w-full border border-gray-300 rounded-md px-3 py-2">
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Temperature</label>
                            <input type="range" min="0" max="2" step="0.1" x-model.number="settings.general.temperature"
                                   class="w-full">
                            <span class="text-sm text-gray-500" x-text="settings.general.temperature"></span>
                        </div>

                        <div class="flex items-center">
                            <input type="checkbox" x-model="settings.general.debug"
                                   class="h-4 w-4 text-blue-600 border-gray-300 rounded">
                            <label class="ml-2 text-sm text-gray-700">Debug Mode</label>
                        </div>
                    </div>

                    <!-- Provider-specific settings -->
                    <div x-show="settings.general.ai_provider === 'azure_openai'" class="mt-6 p-4 bg-gray-50 rounded-lg">
                        <h3 class="text-md font-medium mb-3">Azure OpenAI Settings</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-1">Endpoint URL</label>
                                <input type="text" x-model="settings.general.azure_endpoint"
                                       placeholder="https://your-resource.openai.azure.com"
                                       class="w-full border border-gray-300 rounded-md px-3 py-2 text-sm">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-1">Deployment Name</label>
                                <input type="text" x-model="settings.general.azure_deployment"
                                       placeholder="gpt-4o-deployment"
                                       class="w-full border border-gray-300 rounded-md px-3 py-2 text-sm">
                            </div>
                        </div>
                    </div>

                    <div x-show="settings.general.ai_provider === 'local'" class="mt-6 p-4 bg-gray-50 rounded-lg">
                        <h3 class="text-md font-medium mb-3">Local Model Settings</h3>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">API Base URL</label>
                            <input type="text" x-model="settings.general.local_base_url"
                                   placeholder="http://localhost:11434 (Ollama) or http://localhost:1234/v1 (LM Studio)"
                                   class="w-full border border-gray-300 rounded-md px-3 py-2 text-sm">
                        </div>
                    </div>

                    <h3 class="text-lg font-semibold mt-8 mb-4">Database Settings</h3>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Driver</label>
                            <select x-model="settings.database.driver"
                                    class="w-full border border-gray-300 rounded-md px-3 py-2">
                                <option value="sqlite+aiosqlite">SQLite (Development)</option>
                                <option value="postgresql+asyncpg">PostgreSQL (Production)</option>
                            </select>
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Database Name</label>
                            <input type="text" x-model="settings.database.name"
                                   class="w-full border border-gray-300 rounded-md px-3 py-2">
                        </div>

                        <template x-if="settings.database.driver.includes('postgresql')">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-2">Host</label>
                                <input type="text" x-model="settings.database.host"
                                       class="w-full border border-gray-300 rounded-md px-3 py-2">
                            </div>
                        </template>

                        <template x-if="settings.database.driver.includes('postgresql')">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-2">Port</label>
                                <input type="number" x-model.number="settings.database.port"
                                       class="w-full border border-gray-300 rounded-md px-3 py-2">
                            </div>
                        </template>
                    </div>

                    <div class="mt-6">
                        <button @click="saveGeneralSettings()"
                                class="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700">
                            Save Settings
                        </button>
                    </div>
                </div>

                <!-- Organizations Tab -->
                <div x-show="activeTab === 'organizations'" x-cloak>
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-lg font-semibold">Organizations</h2>
                        <button @click="showOrgModal = true; editingOrg = null; orgForm = {name: '', short_name: '', organization_type: 'client', domains: [], context_sources: [], provider_accounts: [], importance: 5, tags: []}"
                                class="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700 text-sm">
                            + Add Organization
                        </button>
                    </div>

                    <div class="space-y-4">
                        <template x-for="org in organizations" :key="org.name">
                            <div class="border border-gray-200 rounded-lg p-4">
                                <div class="flex justify-between items-start">
                                    <div class="flex-1">
                                        <h3 class="font-medium" x-text="org.name"></h3>
                                        <p class="text-sm text-gray-500" x-text="org.organization_type + ' • ' + org.domains.join(', ')"></p>
                                        <template x-if="org.provider_accounts && org.provider_accounts.length > 0">
                                            <div class="mt-2">
                                                <p class="text-xs text-gray-400 mb-1">Provider Accounts:</p>
                                                <div class="flex flex-wrap gap-1">
                                                    <template x-for="pa in org.provider_accounts" :key="pa.provider_type + pa.account_id">
                                                        <span class="text-xs bg-blue-100 text-blue-800 px-2 py-0.5 rounded"
                                                              x-text="pa.provider_type + ': ' + pa.account_id"></span>
                                                    </template>
                                                </div>
                                            </div>
                                        </template>
                                    </div>
                                    <div class="flex gap-2">
                                        <button @click="editOrganization(org)"
                                                class="text-blue-600 hover:text-blue-800 text-sm">Edit</button>
                                        <button @click="deleteOrganization(org.name)"
                                                class="text-red-600 hover:text-red-800 text-sm">Delete</button>
                                    </div>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>

                <!-- Context Rules Tab -->
                <div x-show="activeTab === 'context'" x-cloak>
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-lg font-semibold">Context Rules</h2>
                        <button @click="showRuleModal = true; editingRule = null; ruleForm = {name: '', description: '', priority: 0, enabled: true, conditions: {}, sources: []}"
                                class="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700 text-sm">
                            + Add Rule
                        </button>
                    </div>

                    <div class="space-y-4">
                        <template x-for="rule in contextRules" :key="rule.name">
                            <div class="border border-gray-200 rounded-lg p-4" :class="{'opacity-50': !rule.enabled}">
                                <div class="flex justify-between items-start">
                                    <div>
                                        <h3 class="font-medium" x-text="rule.name"></h3>
                                        <p class="text-sm text-gray-500" x-text="rule.description"></p>
                                        <p class="text-sm text-gray-500">Priority: <span x-text="rule.priority"></span></p>
                                        <p class="text-sm text-gray-500">Sources: <span x-text="rule.sources.join(', ')"></span></p>
                                    </div>
                                    <div class="flex gap-2">
                                        <button @click="editRule(rule)"
                                                class="text-blue-600 hover:text-blue-800 text-sm">Edit</button>
                                        <button @click="deleteRule(rule.name)"
                                                class="text-red-600 hover:text-red-800 text-sm">Delete</button>
                                    </div>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>

                <!-- Credentials Tab -->
                <div x-show="activeTab === 'credentials'" x-cloak>
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-lg font-semibold">Provider Credentials</h2>
                        <p class="text-sm text-gray-500">Configure credentials for each account/workspace</p>
                    </div>

                    <!-- Configured Accounts -->
                    <div class="mb-6" x-show="credentials.length > 0">
                        <h3 class="font-medium mb-3">Configured Accounts</h3>
                        <div class="space-y-2">
                            <template x-for="cred in credentials" :key="cred.provider_type + '/' + cred.provider_name + '/' + cred.account_id">
                                <div class="flex items-center justify-between border border-gray-200 rounded-lg p-3">
                                    <div>
                                        <span class="font-medium" x-text="cred.account_label || cred.account_id"></span>
                                        <span class="text-sm text-gray-500 ml-2" x-text="'(' + cred.provider_type + '/' + cred.provider_name + ')'"></span>
                                    </div>
                                    <div class="flex gap-2">
                                        <span class="text-xs bg-green-100 text-green-800 px-2 py-1 rounded">Configured</span>
                                        <button @click="deleteCredential(cred.provider_type, cred.provider_name, cred.account_id)"
                                                class="text-red-600 hover:text-red-800 text-sm">Remove</button>
                                    </div>
                                </div>
                            </template>
                        </div>
                    </div>

                    <!-- Add New Credentials -->
                    <h3 class="font-medium mb-3">Add New Account</h3>
                    <div class="space-y-6">
                        <template x-for="providerType in options.providerTypes" :key="providerType.type">
                            <div class="border border-gray-200 rounded-lg p-4">
                                <h3 class="font-medium mb-3 capitalize" x-text="providerType.type"></h3>
                                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <template x-for="provider in providerType.providers" :key="provider.id">
                                        <div class="border border-gray-100 rounded p-3">
                                            <div class="flex justify-between items-center mb-2">
                                                <span class="font-medium text-sm" x-text="provider.name"></span>
                                                <span class="text-xs text-gray-500" x-text="getAccountCount(providerType.type, provider.id) + ' account(s)'"></span>
                                            </div>
                                            <button @click="openCredentialModal(providerType.type, provider)"
                                                    class="text-blue-600 hover:text-blue-800 text-sm">
                                                + Add Account
                                            </button>
                                        </div>
                                    </template>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>

                <!-- Automation Tab -->
                <div x-show="activeTab === 'automation'" x-cloak>
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-lg font-semibold">Automation Jobs</h2>
                        <button @click="showJobModal = true; editingJob = null; jobForm = {job_name: '', job_type: 'email_sync', enabled: true, schedule: '*/5 * * * *', settings: {}}"
                                class="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700 text-sm">
                            + Add Job
                        </button>
                    </div>

                    <div class="space-y-4">
                        <template x-for="job in automationJobs" :key="job.job_name">
                            <div class="border border-gray-200 rounded-lg p-4" :class="{'opacity-50': !job.enabled}">
                                <div class="flex justify-between items-start">
                                    <div>
                                        <h3 class="font-medium" x-text="job.job_name"></h3>
                                        <p class="text-sm text-gray-500">Type: <span x-text="job.job_type"></span></p>
                                        <p class="text-sm text-gray-500">Schedule: <span class="font-mono" x-text="job.schedule"></span></p>
                                    </div>
                                    <div class="flex gap-2">
                                        <button @click="editJob(job)"
                                                class="text-blue-600 hover:text-blue-800 text-sm">Edit</button>
                                        <button @click="deleteJob(job.job_name)"
                                                class="text-red-600 hover:text-red-800 text-sm">Delete</button>
                                    </div>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>
            </div>
        </div>

        <!-- Status Toast -->
        <div x-show="toast.show" x-cloak
             x-transition:enter="transition ease-out duration-300"
             x-transition:leave="transition ease-in duration-200"
             class="fixed bottom-4 right-4 px-4 py-2 rounded-lg shadow-lg"
             :class="toast.type === 'success' ? 'bg-green-500 text-white' : 'bg-red-500 text-white'">
            <span x-text="toast.message"></span>
        </div>

        <!-- Organization Modal -->
        <div x-show="showOrgModal" x-cloak class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div class="bg-white rounded-lg p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto">
                <h2 class="text-lg font-semibold mb-4" x-text="editingOrg ? 'Edit Organization' : 'Add Organization'"></h2>

                <div class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Name *</label>
                        <input type="text" x-model="orgForm.name" class="w-full border border-gray-300 rounded-md px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Short Name</label>
                        <input type="text" x-model="orgForm.short_name" class="w-full border border-gray-300 rounded-md px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Type</label>
                        <select x-model="orgForm.organization_type" class="w-full border border-gray-300 rounded-md px-3 py-2">
                            <template x-for="type in options.orgTypes" :key="type.id">
                                <option :value="type.id" x-text="type.name"></option>
                            </template>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Email Domains (comma-separated)</label>
                        <input type="text" :value="orgForm.domains.join(', ')"
                               @change="orgForm.domains = $event.target.value.split(',').map(d => d.trim()).filter(d => d)"
                               class="w-full border border-gray-300 rounded-md px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Context Sources</label>
                        <div class="grid grid-cols-2 gap-2 max-h-40 overflow-y-auto border border-gray-200 rounded-md p-2">
                            <template x-for="source in options.contextSources" :key="source.id">
                                <label class="flex items-center text-sm">
                                    <input type="checkbox" :value="source.id"
                                           :checked="orgForm.context_sources.includes(source.id)"
                                           @change="toggleArrayItem(orgForm.context_sources, source.id)"
                                           class="mr-2">
                                    <span x-text="source.name"></span>
                                </label>
                            </template>
                        </div>
                    </div>
                    <!-- Provider Accounts Section -->
                    <div class="col-span-2 border-t pt-4 mt-2">
                        <div class="flex justify-between items-center mb-2">
                            <label class="block text-sm font-medium text-gray-700">Provider Accounts</label>
                            <button type="button" @click="orgForm.provider_accounts.push({provider_type: 'email', provider_name: 'outlook', account_id: ''})"
                                    class="text-blue-600 hover:text-blue-800 text-sm">+ Add Account</button>
                        </div>
                        <p class="text-xs text-gray-500 mb-2">Link specific email/calendar accounts to this organization</p>
                        <div class="space-y-2">
                            <template x-for="(pa, idx) in orgForm.provider_accounts" :key="idx">
                                <div class="flex gap-2 items-center bg-gray-50 p-2 rounded">
                                    <select x-model="pa.provider_type" class="border border-gray-300 rounded px-2 py-1 text-sm">
                                        <option value="email">Email</option>
                                        <option value="calendar">Calendar</option>
                                        <option value="task">Task</option>
                                    </select>
                                    <select x-model="pa.provider_name" class="border border-gray-300 rounded px-2 py-1 text-sm">
                                        <option value="outlook">Outlook</option>
                                        <option value="gmail">Gmail</option>
                                        <option value="google">Google</option>
                                        <option value="asana">Asana</option>
                                    </select>
                                    <input type="text" x-model="pa.account_id" placeholder="email@company.com"
                                           class="flex-1 border border-gray-300 rounded px-2 py-1 text-sm">
                                    <button type="button" @click="orgForm.provider_accounts.splice(idx, 1)"
                                            class="text-red-500 hover:text-red-700 text-sm">×</button>
                                </div>
                            </template>
                        </div>
                    </div>

                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Importance (1-10)</label>
                        <input type="range" min="1" max="10" x-model.number="orgForm.importance" class="w-full">
                        <span class="text-sm text-gray-500" x-text="orgForm.importance"></span>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Tags (comma-separated)</label>
                        <input type="text" x-ref="tagsInput"
                               :value="orgForm.tags.join(', ')"
                               @change="orgForm.tags = $event.target.value.split(',').map(t => t.trim()).filter(t => t)"
                               class="w-full border border-gray-300 rounded-md px-3 py-2">
                    </div>
                </div>

                <div class="flex justify-end gap-2 mt-6">
                    <button @click="showOrgModal = false" class="px-4 py-2 text-gray-600 hover:text-gray-800">Cancel</button>
                    <button @click="saveOrganization()" class="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700">Save</button>
                </div>
            </div>
        </div>

        <!-- Credential Modal -->
        <div x-show="showCredModal" x-cloak class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div class="bg-white rounded-lg p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto">
                <h2 class="text-lg font-semibold mb-4" x-text="'Add ' + (credForm.provider_display_name || '') + ' Account'"></h2>

                <div class="space-y-4">
                    <!-- Account identification -->
                    <div class="bg-blue-50 p-3 rounded-lg">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Account ID *</label>
                            <input type="text" x-model="credForm.account_id"
                                   :placeholder="credForm.auth === 'oauth' ? 'your-email@company.com' : 'default'"
                                   class="w-full border border-gray-300 rounded-md px-3 py-2">
                            <p class="text-xs text-gray-500 mt-1">For email/calendar: use your email address. For API keys: use "default" or a label.</p>
                        </div>
                        <div class="mt-3">
                            <label class="block text-sm font-medium text-gray-700 mb-1">Display Label</label>
                            <input type="text" x-model="credForm.account_label"
                                   placeholder="e.g., Work - Primary, Personal"
                                   class="w-full border border-gray-300 rounded-md px-3 py-2">
                            <p class="text-xs text-gray-500 mt-1">Human-friendly name to identify this account.</p>
                        </div>
                    </div>

                    <!-- Microsoft tenant ID for Outlook -->
                    <template x-if="credForm.provider_name === 'outlook'">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Microsoft Tenant ID</label>
                            <input type="text" x-model="credForm.tenant_id"
                                   placeholder="common, organizations, or your-tenant-id"
                                   class="w-full border border-gray-300 rounded-md px-3 py-2">
                            <p class="text-xs text-gray-500 mt-1">Use "common" for personal accounts, "organizations" for work/school, or your specific tenant ID.</p>
                        </div>
                    </template>

                    <template x-if="credForm.fields && credForm.fields.includes('client_id')">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Client ID</label>
                            <input type="text" x-model="credForm.client_id" class="w-full border border-gray-300 rounded-md px-3 py-2">
                        </div>
                    </template>
                    <template x-if="credForm.fields && credForm.fields.includes('client_secret')">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Client Secret</label>
                            <input type="password" x-model="credForm.client_secret" class="w-full border border-gray-300 rounded-md px-3 py-2">
                        </div>
                    </template>
                    <template x-if="credForm.fields && credForm.fields.includes('api_key')">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">API Key</label>
                            <input type="password" x-model="credForm.api_key" class="w-full border border-gray-300 rounded-md px-3 py-2">
                        </div>
                    </template>
                    <template x-if="credForm.fields && credForm.fields.includes('access_token')">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Access Token</label>
                            <input type="password" x-model="credForm.access_token" class="w-full border border-gray-300 rounded-md px-3 py-2">
                        </div>
                    </template>
                    <template x-if="credForm.fields && credForm.fields.includes('bot_token')">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Bot Token</label>
                            <input type="password" x-model="credForm.extra.bot_token" class="w-full border border-gray-300 rounded-md px-3 py-2">
                        </div>
                    </template>
                    <template x-if="credForm.fields && credForm.fields.includes('app_token')">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">App Token</label>
                            <input type="password" x-model="credForm.extra.app_token" class="w-full border border-gray-300 rounded-md px-3 py-2">
                        </div>
                    </template>
                </div>

                <div class="flex justify-end gap-2 mt-6">
                    <button @click="showCredModal = false" class="px-4 py-2 text-gray-600 hover:text-gray-800">Cancel</button>
                    <button @click="saveCredential()" class="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700">Save</button>
                </div>
            </div>
        </div>

        <!-- Rule Modal -->
        <div x-show="showRuleModal" x-cloak class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div class="bg-white rounded-lg p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto">
                <h2 class="text-lg font-semibold mb-4" x-text="editingRule ? 'Edit Rule' : 'Add Rule'"></h2>

                <div class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Name *</label>
                        <input type="text" x-model="ruleForm.name" class="w-full border border-gray-300 rounded-md px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Description</label>
                        <input type="text" x-model="ruleForm.description" class="w-full border border-gray-300 rounded-md px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Priority</label>
                        <input type="number" x-model.number="ruleForm.priority" class="w-full border border-gray-300 rounded-md px-3 py-2">
                    </div>
                    <div class="flex items-center">
                        <input type="checkbox" x-model="ruleForm.enabled" class="mr-2">
                        <label class="text-sm text-gray-700">Enabled</label>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Conditions</label>
                        <div class="space-y-2">
                            <div class="flex gap-2">
                                <input type="text" x-model="ruleForm.conditions.domain" placeholder="Domain (e.g., example.com)"
                                       class="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm">
                            </div>
                            <div class="flex gap-2">
                                <input type="text" x-model="ruleForm.conditions.organization" placeholder="Organization name"
                                       class="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm">
                            </div>
                            <div class="flex gap-2">
                                <input type="text" x-model="ruleForm.conditions.content_contains" placeholder="Content contains"
                                       class="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm">
                            </div>
                        </div>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Sources</label>
                        <div class="grid grid-cols-2 gap-2 max-h-40 overflow-y-auto border border-gray-200 rounded-md p-2">
                            <template x-for="source in options.contextSources" :key="source.id">
                                <label class="flex items-center text-sm">
                                    <input type="checkbox" :value="source.id"
                                           :checked="ruleForm.sources.includes(source.id)"
                                           @change="toggleArrayItem(ruleForm.sources, source.id)"
                                           class="mr-2">
                                    <span x-text="source.name"></span>
                                </label>
                            </template>
                        </div>
                    </div>
                </div>

                <div class="flex justify-end gap-2 mt-6">
                    <button @click="showRuleModal = false" class="px-4 py-2 text-gray-600 hover:text-gray-800">Cancel</button>
                    <button @click="saveRule()" class="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700">Save</button>
                </div>
            </div>
        </div>

        <!-- Job Modal -->
        <div x-show="showJobModal" x-cloak class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div class="bg-white rounded-lg p-6 w-full max-w-lg">
                <h2 class="text-lg font-semibold mb-4" x-text="editingJob ? 'Edit Job' : 'Add Job'"></h2>

                <div class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Job Name *</label>
                        <input type="text" x-model="jobForm.job_name" class="w-full border border-gray-300 rounded-md px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Job Type</label>
                        <select x-model="jobForm.job_type" class="w-full border border-gray-300 rounded-md px-3 py-2">
                            <template x-for="type in options.jobTypes" :key="type.id">
                                <option :value="type.id" x-text="type.name"></option>
                            </template>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Schedule (cron)</label>
                        <input type="text" x-model="jobForm.schedule" class="w-full border border-gray-300 rounded-md px-3 py-2 font-mono">
                        <p class="text-xs text-gray-500 mt-1">Examples: */5 * * * * (every 5 min), 0 7 * * * (7 AM daily)</p>
                    </div>
                    <div class="flex items-center">
                        <input type="checkbox" x-model="jobForm.enabled" class="mr-2">
                        <label class="text-sm text-gray-700">Enabled</label>
                    </div>
                </div>

                <div class="flex justify-end gap-2 mt-6">
                    <button @click="showJobModal = false" class="px-4 py-2 text-gray-600 hover:text-gray-800">Cancel</button>
                    <button @click="saveJob()" class="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700">Save</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        function adminApp() {
            return {
                activeTab: 'general',
                settings: {
                    general: {environment: 'development', debug: true, log_level: 'INFO', ai_provider: 'openai', default_model: 'gpt-4o-mini', premium_model: 'gpt-4o', max_tokens: 4096, temperature: 0.7, azure_endpoint: '', azure_deployment: '', local_base_url: ''},
                    database: {driver: 'sqlite+aiosqlite', name: 'executive_copilot.db', host: 'localhost', port: 5432}
                },
                organizations: [],
                contextRules: [],
                credentials: [],
                automationJobs: [],
                options: {aiProviders: [], models: {}, contextSources: [], orgTypes: [], jobTypes: [], providerTypes: []},

                // Modals
                showOrgModal: false,
                showRuleModal: false,
                showCredModal: false,
                showJobModal: false,

                // Forms
                orgForm: {name: '', short_name: '', organization_type: 'client', domains: [], context_sources: [], provider_accounts: [], importance: 5, tags: []},
                ruleForm: {name: '', description: '', priority: 0, enabled: true, conditions: {}, sources: []},
                credForm: {provider_type: '', provider_name: '', provider_display_name: '', auth: '', fields: [], account_id: '', account_label: '', client_id: '', client_secret: '', api_key: '', access_token: '', tenant_id: '', extra: {}},
                jobForm: {job_name: '', job_type: 'email_sync', enabled: true, schedule: '*/5 * * * *', settings: {}},

                editingOrg: null,
                editingRule: null,
                editingJob: null,

                toast: {show: false, message: '', type: 'success'},

                async init() {
                    await Promise.all([
                        this.loadOptions(),
                        this.loadSettings(),
                        this.loadOrganizations(),
                        this.loadContextRules(),
                        this.loadCredentials(),
                        this.loadAutomationJobs()
                    ]);
                },

                async shutdownServer() {
                    if (confirm('Are you sure you want to stop the admin server?')) {
                        try {
                            await fetch('/api/shutdown', {method: 'POST'});
                            this.showToast('Server is shutting down...', 'success');
                            setTimeout(() => {
                                document.body.innerHTML = '<div style="display:flex;justify-content:center;align-items:center;height:100vh;font-family:system-ui;"><div style="text-align:center;"><h1 style="font-size:24px;margin-bottom:16px;">Server Stopped</h1><p style="color:#666;">The admin server has been shut down.</p><p style="color:#666;margin-top:8px;">Run <code>uv run copilot admin</code> to start again.</p></div></div>';
                            }, 1000);
                        } catch (e) {
                            this.showToast('Server shutdown initiated', 'success');
                        }
                    }
                },

                async loadOptions() {
                    const [aiProviders, models, contextSources, orgTypes, jobTypes, providerTypes] = await Promise.all([
                        fetch('/api/options/ai-providers').then(r => r.json()),
                        fetch('/api/options/models').then(r => r.json()),
                        fetch('/api/options/context-sources').then(r => r.json()),
                        fetch('/api/options/organization-types').then(r => r.json()),
                        fetch('/api/options/job-types').then(r => r.json()),
                        fetch('/api/options/provider-types').then(r => r.json())
                    ]);
                    this.options = {aiProviders, models, contextSources, orgTypes, jobTypes, providerTypes};
                },

                getModelsForProvider(provider) {
                    return this.options.models[provider] || [];
                },

                async loadSettings() {
                    const [general, database] = await Promise.all([
                        fetch('/api/settings/general').then(r => r.json()),
                        fetch('/api/settings/database').then(r => r.json())
                    ]);
                    this.settings.general = general;
                    this.settings.database = database;
                },

                async loadOrganizations() {
                    this.organizations = await fetch('/api/organizations').then(r => r.json());
                },

                async loadContextRules() {
                    this.contextRules = await fetch('/api/context-rules').then(r => r.json());
                },

                async loadCredentials() {
                    this.credentials = await fetch('/api/credentials').then(r => r.json());
                },

                async loadAutomationJobs() {
                    this.automationJobs = await fetch('/api/automation').then(r => r.json());
                },

                async saveGeneralSettings() {
                    await fetch('/api/settings/general', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(this.settings.general)});
                    await fetch('/api/settings/database', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(this.settings.database)});
                    this.showToast('Settings saved', 'success');
                },

                editOrganization(org) {
                    this.editingOrg = org.name;
                    this.orgForm = {
                        ...org,
                        domains: [...(org.domains || [])],
                        context_sources: [...(org.context_sources || [])],
                        provider_accounts: (org.provider_accounts || []).map(pa => ({...pa})),
                        tags: [...(org.tags || [])]
                    };
                    this.showOrgModal = true;
                },

                async saveOrganization() {
                    const method = this.editingOrg ? 'PUT' : 'POST';
                    const url = this.editingOrg ? `/api/organizations/${this.editingOrg}` : '/api/organizations';
                    await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(this.orgForm)});
                    await this.loadOrganizations();
                    this.showOrgModal = false;
                    this.showToast('Organization saved', 'success');
                },

                async deleteOrganization(name) {
                    if (confirm(`Delete organization "${name}"?`)) {
                        await fetch(`/api/organizations/${name}`, {method: 'DELETE'});
                        await this.loadOrganizations();
                        this.showToast('Organization deleted', 'success');
                    }
                },

                editRule(rule) {
                    this.editingRule = rule.name;
                    this.ruleForm = {...rule, conditions: {...(rule.conditions || {})}, sources: [...(rule.sources || [])]};
                    this.showRuleModal = true;
                },

                async saveRule() {
                    // Clean empty conditions
                    const conditions = {};
                    Object.entries(this.ruleForm.conditions).forEach(([k, v]) => { if (v) conditions[k] = v; });
                    this.ruleForm.conditions = conditions;

                    const method = this.editingRule ? 'PUT' : 'POST';
                    const url = this.editingRule ? `/api/context-rules/${this.editingRule}` : '/api/context-rules';
                    await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(this.ruleForm)});
                    await this.loadContextRules();
                    this.showRuleModal = false;
                    this.showToast('Rule saved', 'success');
                },

                async deleteRule(name) {
                    if (confirm(`Delete rule "${name}"?`)) {
                        await fetch(`/api/context-rules/${name}`, {method: 'DELETE'});
                        await this.loadContextRules();
                        this.showToast('Rule deleted', 'success');
                    }
                },

                getAccountCount(providerType, providerId) {
                    return this.credentials.filter(c => c.provider_type === providerType && c.provider_name === providerId).length;
                },

                openCredentialModal(providerType, provider) {
                    this.credForm = {
                        provider_type: providerType,
                        provider_name: provider.id,
                        provider_display_name: provider.name,
                        auth: provider.auth,
                        fields: provider.fields,
                        account_id: provider.auth === 'api_key' ? 'default' : '',
                        account_label: '',
                        client_id: '',
                        client_secret: '',
                        api_key: '',
                        access_token: '',
                        tenant_id: '',
                        extra: {}
                    };
                    this.showCredModal = true;
                },

                async saveCredential() {
                    if (!this.credForm.account_id) {
                        this.showToast('Account ID is required', 'error');
                        return;
                    }
                    await fetch('/api/credentials', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(this.credForm)});
                    await this.loadCredentials();
                    this.showCredModal = false;
                    this.showToast('Credentials saved', 'success');
                },

                async deleteCredential(providerType, providerName, accountId) {
                    if (confirm(`Delete credentials for ${accountId}?`)) {
                        await fetch(`/api/credentials/${providerType}/${providerName}/${encodeURIComponent(accountId)}`, {method: 'DELETE'});
                        await this.loadCredentials();
                        this.showToast('Credentials deleted', 'success');
                    }
                },

                editJob(job) {
                    this.editingJob = job.job_name;
                    this.jobForm = {...job, settings: {...(job.settings || {})}};
                    this.showJobModal = true;
                },

                async saveJob() {
                    const method = this.editingJob ? 'PUT' : 'POST';
                    const url = this.editingJob ? `/api/automation/${this.editingJob}` : '/api/automation';
                    await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(this.jobForm)});
                    await this.loadAutomationJobs();
                    this.showJobModal = false;
                    this.showToast('Job saved', 'success');
                },

                async deleteJob(name) {
                    if (confirm(`Delete job "${name}"?`)) {
                        await fetch(`/api/automation/${name}`, {method: 'DELETE'});
                        await this.loadAutomationJobs();
                        this.showToast('Job deleted', 'success');
                    }
                },

                toggleArrayItem(arr, item) {
                    const idx = arr.indexOf(item);
                    if (idx === -1) arr.push(item);
                    else arr.splice(idx, 1);
                },

                showToast(message, type = 'success') {
                    this.toast = {show: true, message, type};
                    setTimeout(() => this.toast.show = false, 3000);
                }
            };
        }
    </script>
</body>
</html>'''
