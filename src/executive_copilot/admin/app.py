"""Admin panel FastAPI application."""

import asyncio  # noqa: TC003 - needed at runtime for Event type
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from executive_copilot.admin.config_manager import ConfigManager
from executive_copilot.admin.oauth import OAuthManager
from executive_copilot.credentials import CredentialManager
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


class DashboardDoneInput(BaseModel):
    key: str
    done: bool = True


class TechTicketInput(BaseModel):
    title: str
    notes: str | None = None
    assignee_id: str | None = None
    project_id: str | None = None
    due_on: str | None = None


class TechTicketsRequest(BaseModel):
    tickets: list[TechTicketInput] = Field(default_factory=list)
    account: str | None = None


def create_admin_app(config_dir: Path | None = None, shutdown_event: "asyncio.Event | None" = None) -> FastAPI:
    """Create the admin panel FastAPI application."""
    import os
    import signal

    app = FastAPI(
        title="Executive Copilot Admin",
        description="Configuration panel for Executive Copilot",
        version="0.1.0",
    )

    from executive_copilot.admin.knowledge_auth import install_workspace_auth

    install_workspace_auth(app, config_dir or Path("config"))

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

    @app.get("/api/dashboard")
    async def get_dashboard(refresh: bool = False) -> dict[str, Any]:
        """Get dashboard data including daily briefing.

        Serves the cached briefing instantly (written whenever a briefing is
        generated — by the daily job or a manual refresh). Pass refresh=true
        to regenerate live from all providers.
        """
        from executive_copilot.config import load_config
        from executive_copilot.database import DatabaseManager
        from executive_copilot.services.briefing import (
            BriefingService,
            load_briefing_cache,
        )
        from executive_copilot.services.dashboard_state import apply_done_marks

        if not refresh:
            cached = load_briefing_cache()
            if cached is not None:
                return {
                    "status": "ok",
                    "briefing": apply_done_marks(cached),
                    "cached": True,
                }

        try:
            app_config = load_config(config_dir)
            db_manager = DatabaseManager(app_config.database)

            service = BriefingService(
                config=app_config,
                credential_manager=credential_manager,
                db_manager=db_manager,
            )

            briefing = await service.generate_briefing()
            await db_manager.close()

            return {
                "status": "ok",
                "briefing": apply_done_marks(briefing),
                "cached": False,
            }
        except Exception as e:
            logger.error("Failed to generate dashboard data", error=str(e))
            return {
                "status": "error",
                "error": str(e),
                "briefing": None,
            }

    @app.post("/api/dashboard/done")
    async def set_dashboard_item_done(payload: DashboardDoneInput) -> dict[str, Any]:
        """Mark or unmark a dashboard item as done (handled outside email/Asana)."""
        from executive_copilot.services.dashboard_state import set_item_done

        set_item_done(payload.key, payload.done)
        return {"status": "ok", "key": payload.key, "done": payload.done}

    @app.get("/api/activity")
    async def get_activity() -> dict[str, Any]:
        """Get MCP activity log."""
        from executive_copilot.mcp.activity import get_tracker

        tracker = get_tracker()
        return {
            "activities": tracker.get_recent_activities(limit=50),
            "stats": tracker.get_stats(),
        }

    @app.post("/api/activity/clear")
    async def clear_activity() -> dict[str, str]:
        """Clear activity log."""
        from executive_copilot.mcp.activity import get_tracker

        tracker = get_tracker()
        tracker.clear()
        return {"status": "ok"}

    # --- HTML Routes ---

    from executive_copilot.admin.workspace_api import create_workspace_router

    app.include_router(create_workspace_router(config_dir, credential_manager))

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """Serve the workspace (agenda, tasks, Obsidian, chat)."""
        return (Path(__file__).parent / "static" / "workspace.html").read_text(encoding="utf-8")

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_panel() -> str:
        """Serve the settings/admin panel."""
        return get_admin_html()

    @app.get("/tech", response_class=HTMLResponse)
    async def tech_dashboard() -> HTMLResponse:
        """Serve the tech dashboard (CTO view) with persisted week history injected."""
        from executive_copilot.services import week_history

        # Never cache the shell so the browser always gets the latest dashboard.
        return HTMLResponse(
            week_history.render_dashboard_html(),
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    # --- Tech dashboard data (live from Asana, server-side) ---

    @app.get("/api/tech/data")
    async def tech_data(account: str | None = None) -> dict[str, Any]:
        """Clients, active roadmap and recent wins — team-wide, live from Asana."""
        from executive_copilot.services import tech_dashboard

        acc = account or tech_dashboard.DEFAULT_ACCOUNT_ID
        try:
            data = await tech_dashboard.get_dashboard_data(credential_manager, acc)
            return {"status": "ok", **data}
        except Exception as e:
            logger.error("tech_data failed", error=str(e))
            return {"status": "error", "error": str(e)}

    @app.get("/api/tech/team")
    async def tech_team(account: str | None = None) -> dict[str, Any]:
        """Asana workspace members for the ticket-assignee picker."""
        from executive_copilot.services import tech_dashboard

        acc = account or tech_dashboard.DEFAULT_ACCOUNT_ID
        try:
            members = await tech_dashboard.get_team_members(credential_manager, acc)
            return {"status": "ok", "members": members}
        except Exception as e:
            logger.error("tech_team failed", error=str(e))
            return {"status": "error", "error": str(e), "members": []}

    @app.get("/api/tech/projects")
    async def tech_projects(account: str | None = None) -> dict[str, Any]:
        """Asana projects for the ticket-project picker."""
        from executive_copilot.services import tech_dashboard

        acc = account or tech_dashboard.DEFAULT_ACCOUNT_ID
        try:
            projects = await tech_dashboard.list_projects(credential_manager, acc)
            return {"status": "ok", "projects": projects}
        except Exception as e:
            logger.error("tech_projects failed", error=str(e))
            return {"status": "error", "error": str(e), "projects": []}

    @app.post("/api/tech/tickets")
    async def tech_create_tickets(payload: TechTicketsRequest) -> dict[str, Any]:
        """Turn selected review agreements into Asana tickets for the team."""
        from executive_copilot.services import tech_dashboard

        acc = payload.account or tech_dashboard.DEFAULT_ACCOUNT_ID
        items = [t.model_dump() for t in payload.tickets]
        try:
            result = await tech_dashboard.create_tickets(credential_manager, items, acc)
            return {"status": "ok", **result}
        except Exception as e:
            logger.error("tech_create_tickets failed", error=str(e))
            return {"status": "error", "error": str(e)}

    # --- OAuth Routes ---

    @app.get("/auth/google/start")
    async def start_google_auth(account: str, request: Request) -> HTMLResponse:
        """Start Google OAuth flow for an account."""
        # Get client credentials from stored config
        cred = credential_manager.get_oauth_client("google", account)
        if not cred:
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

            gmail_cred = credential_manager.get_oauth_client("google", oauth_state.account_id)
            if not gmail_cred:
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
                        <p><a href="/#accounts">Back to the workspace</a></p><script>setTimeout(function(){{ location.href = "/#accounts"; }}, 1500);</script>
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
        cred = credential_manager.get_oauth_client("microsoft", account)
        if not cred:
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

            outlook_cred = credential_manager.get_oauth_client("microsoft", oauth_state.account_id)
            if not outlook_cred:
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
                        <p><a href="/#accounts">Back to the workspace</a></p><script>setTimeout(function(){{ location.href = "/#accounts"; }}, 1500);</script>
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
                has_refresh_token = cred.get("has_refresh_token", False)
                accounts.append({
                    "provider_type": cred["provider_type"],
                    "provider_name": cred["provider_name"],
                    "account_id": cred["account_id"],
                    "account_label": cred.get("account_label", cred["account_id"]),
                    "authorized": has_access_token or has_refresh_token,
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
            <a href="/admin" class="text-blue-600 hover:text-blue-800">← Back to Admin Panel</a>
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
            {"id": "transcript_scan", "name": "Transcript Scanner"},
            {"id": "commitment_detection", "name": "Commitment Detection"},
            {"id": "relationship_scan", "name": "Relationship Scanner"},
            {"id": "delegation_detection", "name": "Delegation Detection"},
            {"id": "email_forward", "name": "Email Auto-Forward"},
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
        @keyframes pulse-slow { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
        @keyframes slide-in { from { transform: translateY(10px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
        @keyframes count-up { from { transform: scale(0.8); } to { transform: scale(1); } }
        @keyframes float { 0%, 100% { transform: translateY(0px); } 50% { transform: translateY(-10px); } }
        @keyframes glow { 0%, 100% { box-shadow: 0 0 20px rgba(99, 102, 241, 0.3); } 50% { box-shadow: 0 0 40px rgba(99, 102, 241, 0.6), 0 0 60px rgba(139, 92, 246, 0.4); } }
        @keyframes orbit { from { transform: rotate(0deg) translateX(60px) rotate(0deg); } to { transform: rotate(360deg) translateX(60px) rotate(-360deg); } }
        @keyframes orbit-reverse { from { transform: rotate(360deg) translateX(45px) rotate(-360deg); } to { transform: rotate(0deg) translateX(45px) rotate(0deg); } }
        @keyframes pulse-ring { 0% { transform: scale(0.8); opacity: 1; } 100% { transform: scale(1.4); opacity: 0; } }
        @keyframes typing { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
        @keyframes wave { 0%, 100% { transform: scaleY(0.5); } 50% { transform: scaleY(1); } }
        @keyframes brain-pulse { 0%, 100% { transform: scale(1); filter: brightness(1); } 50% { transform: scale(1.05); filter: brightness(1.2); } }
        @keyframes data-flow { 0% { stroke-dashoffset: 100; opacity: 0; } 50% { opacity: 1; } 100% { stroke-dashoffset: 0; opacity: 0; } }
        .animate-pulse-slow { animation: pulse-slow 2s ease-in-out infinite; }
        .animate-slide-in { animation: slide-in 0.3s ease-out forwards; }
        .animate-count { animation: count-up 0.4s ease-out; }
        .animate-float { animation: float 3s ease-in-out infinite; }
        .animate-glow { animation: glow 2s ease-in-out infinite; }
        .animate-orbit { animation: orbit 8s linear infinite; }
        .animate-orbit-reverse { animation: orbit-reverse 6s linear infinite; }
        .animate-brain { animation: brain-pulse 2s ease-in-out infinite; }
        .stat-card { transition: all 0.3s ease; }
        .stat-card:hover { transform: translateY(-2px); box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1); }
        .gradient-blue { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        .gradient-green { background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); }
        .gradient-orange { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
        .gradient-red { background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%); }
        .gradient-purple { background: linear-gradient(135deg, #5f2c82 0%, #49a09d 100%); }
        .gradient-cyan { background: linear-gradient(135deg, #00c6fb 0%, #005bea 100%); }
        .glass { background: rgba(255,255,255,0.9); backdrop-filter: blur(10px); }
        .progress-ring { transform: rotate(-90deg); }
        .progress-ring circle { transition: stroke-dashoffset 0.5s ease-in-out; }
        .ai-orb { background: radial-gradient(circle at 30% 30%, #818cf8, #6366f1, #4f46e5, #4338ca); }
        .wave-bar { animation: wave 1s ease-in-out infinite; }
        .wave-bar:nth-child(2) { animation-delay: 0.1s; }
        .wave-bar:nth-child(3) { animation-delay: 0.2s; }
        .wave-bar:nth-child(4) { animation-delay: 0.3s; }
        .wave-bar:nth-child(5) { animation-delay: 0.4s; }
        /* AI Core panel */
        .ai-core-panel {
            background: radial-gradient(ellipse at 30% 0%, #1e1b4b 0%, #020617 55%, #0f172a 100%);
            box-shadow: 0 0 40px rgba(99, 102, 241, 0.25), inset 0 0 60px rgba(2, 6, 23, 0.8);
            border: 1px solid rgba(99, 102, 241, 0.35);
        }
        .ai-core-grid {
            background-image:
                linear-gradient(rgba(99, 102, 241, 0.07) 1px, transparent 1px),
                linear-gradient(90deg, rgba(99, 102, 241, 0.07) 1px, transparent 1px);
            background-size: 28px 28px;
            mask-image: radial-gradient(ellipse at center, black 30%, transparent 85%);
            -webkit-mask-image: radial-gradient(ellipse at center, black 30%, transparent 85%);
        }
        @keyframes scanline { 0% { top: -10%; opacity: 0; } 10% { opacity: 1; } 90% { opacity: 1; } 100% { top: 105%; opacity: 0; } }
        .ai-core-scanline {
            height: 2px;
            background: linear-gradient(90deg, transparent, rgba(103, 232, 249, 0.6), transparent);
            animation: scanline 5s linear infinite;
        }
        @keyframes ai-typing-blink { 0%, 100% { border-color: transparent; } 50% { border-color: rgba(148, 163, 184, 0.8); } }
        .ai-typing { border-right: 6px solid transparent; display: inline-block; animation: ai-typing-blink 1s step-end infinite; }
    </style>
</head>
<body class="bg-gray-100 min-h-screen" x-data="adminApp()" x-init="init()">
    <div class="container mx-auto px-4 py-8 max-w-6xl">
        <!-- Header -->
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6 flex justify-between items-center">
            <div>
                <h1 class="text-2xl font-bold text-gray-800">Executive Copilot</h1>
                <p class="text-gray-600">Your AI-Powered Executive Assistant</p>
            </div>
            <div class="flex gap-3">
                <a href="/" class="px-4 py-2 bg-gray-900 hover:bg-gray-700 text-white rounded-md text-sm font-medium transition-colors">
                    ← Workspace
                </a>
                <a href="/auth" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-sm font-medium transition-colors">
                    Authorize Accounts
                </a>
                <button @click="shutdownServer()"
                        class="px-4 py-2 bg-red-500 hover:bg-red-600 text-white rounded-md text-sm font-medium transition-colors">
                    Stop Server
                </button>
            </div>
        </div>

        <!-- Tabs -->
        <div class="bg-white rounded-lg shadow-sm mb-6">
            <div class="border-b border-gray-200">
                <nav class="flex -mb-px">
                    <button @click="activeTab = 'dashboard'; loadDashboard()"
                            :class="activeTab === 'dashboard' ? 'tab-active' : 'text-gray-500 hover:text-gray-700'"
                            class="px-6 py-4 text-sm font-medium flex items-center gap-2">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"></path></svg>
                        Dashboard
                    </button>
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
                    <button @click="activeTab = 'activity'; loadActivity()"
                            :class="activeTab === 'activity' ? 'tab-active' : 'text-gray-500 hover:text-gray-700'"
                            class="px-6 py-4 text-sm font-medium flex items-center gap-2">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                        Activity
                        <span x-show="activityStats?.pending_count > 0" class="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
                    </button>
                    <a href="/tech" target="_blank"
                       class="px-6 py-4 text-sm font-medium flex items-center gap-2 text-gray-500 hover:text-gray-700">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 17v-2a4 4 0 014-4h6m0 0l-3-3m3 3l-3 3M9 7H5a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4"></path></svg>
                        Tech Dashboard ↗
                    </a>
                </nav>
            </div>

            <div class="p-6">
                <!-- Dashboard Tab -->
                <div x-show="activeTab === 'dashboard'" x-cloak>
                    <!-- Loading State -->
                    <div x-show="dashboardLoading" class="flex items-center justify-center py-12">
                        <div class="text-center">
                            <svg class="animate-spin h-10 w-10 text-blue-600 mx-auto mb-4" fill="none" viewBox="0 0 24 24">
                                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                            </svg>
                            <p class="text-gray-500">Loading your daily briefing...</p>
                        </div>
                    </div>

                    <!-- Dashboard Content -->
                    <div x-show="!dashboardLoading && dashboard" x-cloak>
                        <!-- Header with Date and Summary -->
                        <div class="mb-6">
                            <div class="flex items-center justify-between">
                                <div>
                                    <h2 class="text-2xl font-bold text-gray-800" x-text="dashboard?.briefing?.date || 'Today'"></h2>
                                    <p class="text-gray-600 mt-1" x-text="dashboard?.briefing?.summary_text || 'Loading...'"></p>
                                    <p class="text-xs text-gray-400 mt-1" x-show="dashboard?.briefing?.generated_at"
                                       x-text="'Updated ' + new Date(dashboard?.briefing?.generated_at).toLocaleString() + (dashboard?.cached ? ' (cached)' : '')"></p>
                                </div>
                                <button @click="loadDashboard(true)" :disabled="dashboardRefreshing"
                                        class="flex items-center gap-2 px-4 py-2 bg-gray-100 hover:bg-gray-200 rounded-lg text-sm text-gray-700 transition-colors disabled:opacity-60">
                                    <svg class="w-4 h-4" :class="dashboardRefreshing ? 'animate-spin' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                                    <span x-text="dashboardRefreshing ? 'Refreshing...' : 'Refresh'"></span>
                                </button>
                            </div>
                        </div>

                        <!-- Stats Cards Grid -->
                        <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
                            <!-- Meetings Card -->
                            <div class="stat-card bg-white rounded-xl p-4 shadow-sm border border-gray-100 animate-slide-in" style="animation-delay: 0.1s">
                                <div class="flex items-center gap-3">
                                    <div class="w-10 h-10 rounded-lg gradient-blue flex items-center justify-center">
                                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
                                    </div>
                                    <div>
                                        <p class="text-2xl font-bold text-gray-800 animate-count" x-text="dashboard?.briefing?.summary?.meetings || 0"></p>
                                        <p class="text-xs text-gray-500">Meetings</p>
                                    </div>
                                </div>
                            </div>

                            <!-- Tasks Due Today Card -->
                            <div class="stat-card bg-white rounded-xl p-4 shadow-sm border border-gray-100 animate-slide-in" style="animation-delay: 0.15s">
                                <div class="flex items-center gap-3">
                                    <div class="w-10 h-10 rounded-lg gradient-green flex items-center justify-center">
                                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"></path></svg>
                                    </div>
                                    <div>
                                        <p class="text-2xl font-bold text-gray-800 animate-count" x-text="dashboard?.briefing?.summary?.tasks_due_today || 0"></p>
                                        <p class="text-xs text-gray-500">Tasks Due</p>
                                    </div>
                                </div>
                            </div>

                            <!-- Overdue Tasks Card -->
                            <div class="stat-card bg-white rounded-xl p-4 shadow-sm border border-gray-100 animate-slide-in" style="animation-delay: 0.2s">
                                <div class="flex items-center gap-3">
                                    <div class="w-10 h-10 rounded-lg gradient-red flex items-center justify-center">
                                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                                    </div>
                                    <div>
                                        <p class="text-2xl font-bold text-gray-800 animate-count" x-text="dashboard?.briefing?.summary?.tasks_overdue || 0"></p>
                                        <p class="text-xs text-gray-500">Overdue</p>
                                    </div>
                                </div>
                            </div>

                            <!-- Emails Needing Attention Card -->
                            <div class="stat-card bg-white rounded-xl p-4 shadow-sm border border-gray-100 animate-slide-in" style="animation-delay: 0.25s">
                                <div class="flex items-center gap-3">
                                    <div class="w-10 h-10 rounded-lg gradient-orange flex items-center justify-center">
                                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
                                    </div>
                                    <div>
                                        <p class="text-2xl font-bold text-gray-800 animate-count" x-text="dashboard?.briefing?.summary?.emails_requiring_attention || 0"></p>
                                        <p class="text-xs text-gray-500">Need Reply</p>
                                    </div>
                                </div>
                            </div>

                            <!-- Pending Replies Card -->
                            <div class="stat-card bg-white rounded-xl p-4 shadow-sm border border-gray-100 animate-slide-in" style="animation-delay: 0.3s">
                                <div class="flex items-center gap-3">
                                    <div class="w-10 h-10 rounded-lg gradient-purple flex items-center justify-center">
                                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"></path></svg>
                                    </div>
                                    <div>
                                        <p class="text-2xl font-bold text-gray-800 animate-count" x-text="dashboard?.briefing?.summary?.pending_replies || 0"></p>
                                        <p class="text-xs text-gray-500">Awaiting</p>
                                    </div>
                                </div>
                            </div>

                            <!-- Waiting on Others Card -->
                            <div class="stat-card bg-white rounded-xl p-4 shadow-sm border border-gray-100 animate-slide-in" style="animation-delay: 0.35s">
                                <div class="flex items-center gap-3">
                                    <div class="w-10 h-10 rounded-lg gradient-cyan flex items-center justify-center">
                                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg>
                                    </div>
                                    <div>
                                        <p class="text-2xl font-bold text-gray-800 animate-count" x-text="dashboard?.briefing?.summary?.waiting_on_others || 0"></p>
                                        <p class="text-xs text-gray-500">Delegated</p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Main Content Grid -->
                        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                            <!-- Today's Schedule -->
                            <div class="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                                <div class="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                                    <h3 class="font-semibold text-gray-800 flex items-center gap-2">
                                        <svg class="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
                                        Today's Schedule
                                    </h3>
                                    <span class="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded-full" x-text="(dashboard?.briefing?.calendar?.events?.length || 0) + ' events'"></span>
                                </div>
                                <div class="p-4 max-h-80 overflow-y-auto">
                                    <template x-if="dashboard?.briefing?.calendar?.events?.length > 0">
                                        <div class="space-y-3">
                                            <template x-for="(event, idx) in dashboard?.briefing?.calendar?.events" :key="idx">
                                                <div class="flex gap-3 p-3 bg-gray-50 rounded-lg hover:bg-gray-100 transition-colors">
                                                    <div class="text-center min-w-[50px]">
                                                        <p class="text-sm font-semibold text-blue-600" x-text="event.is_all_day ? 'All Day' : event.start_time"></p>
                                                    </div>
                                                    <div class="flex-1">
                                                        <p class="font-medium text-gray-800" x-text="event.title"></p>
                                                        <p class="text-xs text-gray-500" x-show="event.location" x-text="'📍 ' + event.location"></p>
                                                        <p class="text-xs text-gray-500" x-show="event.conference_url">
                                                            <a :href="event.conference_url" target="_blank" class="text-blue-600 hover:underline">🔗 Join Meeting</a>
                                                        </p>
                                                    </div>
                                                    <div class="text-xs text-gray-400" x-text="event.duration_minutes + ' min'"></div>
                                                </div>
                                            </template>
                                        </div>
                                    </template>
                                    <template x-if="!dashboard?.briefing?.calendar?.events?.length">
                                        <div class="text-center py-8 text-gray-400">
                                            <svg class="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
                                            <p>No meetings scheduled</p>
                                        </div>
                                    </template>
                                </div>
                            </div>

                            <!-- Emails Requiring Attention -->
                            <div class="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                                <div class="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                                    <h3 class="font-semibold text-gray-800 flex items-center gap-2">
                                        <svg class="w-5 h-5 text-orange-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
                                        Emails Needing Attention
                                    </h3>
                                    <span class="text-xs bg-orange-100 text-orange-700 px-2 py-1 rounded-full" x-text="(dashboard?.briefing?.emails_requiring_attention?.emails?.length || 0) + ' emails'"></span>
                                </div>
                                <div class="p-4 max-h-80 overflow-y-auto">
                                    <template x-if="dashboard?.briefing?.emails_requiring_attention?.emails?.length > 0">
                                        <div class="space-y-3">
                                            <template x-for="(email, idx) in dashboard?.briefing?.emails_requiring_attention?.emails" :key="idx">
                                                <div class="p-3 bg-gray-50 rounded-lg hover:bg-gray-100 transition-colors" :class="email.done ? 'opacity-50' : ''">
                                                    <div class="flex items-start justify-between gap-2">
                                                        <button @click="toggleDone(email)" :title="email.done ? 'Mark as pending' : 'Mark as done (handled elsewhere)'"
                                                                class="mt-0.5 w-6 h-6 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-colors"
                                                                :class="email.done ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 text-transparent hover:border-green-400 hover:text-green-400'">
                                                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"></path></svg>
                                                        </button>
                                                        <div class="flex-1 min-w-0">
                                                            <div class="flex items-center gap-2">
                                                                <p class="font-medium text-gray-800 truncate" :class="email.done ? 'line-through' : ''" x-text="email.subject"></p>
                                                                <span x-show="email.is_urgent" class="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">URGENT</span>
                                                            </div>
                                                            <p class="text-sm text-gray-500" x-text="'From: ' + (email.from_name || email.from)"></p>
                                                            <p class="text-xs text-gray-400 mt-1" x-text="email.snippet?.slice(0, 80) + '...'"></p>
                                                        </div>
                                                    </div>
                                                    <div class="mt-2 flex items-center gap-2 flex-wrap">
                                                        <span class="text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded" x-text="email.attention_reason"></span>
                                                        <span x-show="email.days_waiting >= 1" class="text-xs bg-amber-50 text-amber-600 px-2 py-0.5 rounded" x-text="'Waiting ' + email.days_waiting + (email.days_waiting === 1 ? ' day' : ' days')"></span>
                                                    </div>
                                                </div>
                                            </template>
                                        </div>
                                    </template>
                                    <template x-if="!dashboard?.briefing?.emails_requiring_attention?.emails?.length">
                                        <div class="text-center py-8 text-gray-400">
                                            <svg class="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
                                            <p>All caught up!</p>
                                        </div>
                                    </template>
                                </div>
                            </div>

                            <!-- Tasks Section -->
                            <div class="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                                <div class="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                                    <h3 class="font-semibold text-gray-800 flex items-center gap-2">
                                        <svg class="w-5 h-5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"></path></svg>
                                        Tasks
                                    </h3>
                                </div>
                                <div class="p-4 max-h-80 overflow-y-auto">
                                    <!-- Overdue Tasks -->
                                    <template x-if="dashboard?.briefing?.tasks?.overdue?.length > 0">
                                        <div class="mb-4">
                                            <p class="text-xs font-semibold text-red-600 uppercase tracking-wider mb-2">⚠️ Overdue</p>
                                            <div class="space-y-2">
                                                <template x-for="(task, idx) in dashboard?.briefing?.tasks?.overdue" :key="'overdue-' + idx">
                                                    <div class="p-3 bg-red-50 border border-red-100 rounded-lg" :class="task.done ? 'opacity-50' : ''">
                                                        <div class="flex items-start gap-2">
                                                            <button @click="toggleDone(task)" :title="task.done ? 'Mark as pending' : 'Mark as done (handled elsewhere)'"
                                                                    class="mt-0.5 w-6 h-6 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-colors"
                                                                    :class="task.done ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 text-transparent hover:border-green-400 hover:text-green-400'">
                                                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"></path></svg>
                                                            </button>
                                                            <div class="flex-1 min-w-0">
                                                                <p class="font-medium text-gray-800" :class="task.done ? 'line-through' : ''" x-text="task.title"></p>
                                                                <p class="text-xs text-gray-600 mt-1" x-show="task.description" x-text="task.description?.slice(0, 120) + (task.description?.length > 120 ? '…' : '')"></p>
                                                                <div class="flex items-center gap-2 mt-1 flex-wrap">
                                                                    <span class="text-xs text-gray-500" x-text="task.project_name" x-show="task.project_name"></span>
                                                                    <span class="text-xs text-red-600" x-text="'Due: ' + task.due_date?.slice(0, 10)"></span>
                                                                    <span class="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded" x-show="task.attention_reason" x-text="task.attention_reason"></span>
                                                                    <span class="text-xs text-gray-500" x-show="task.assignee_name" x-text="'👤 ' + task.assignee_name"></span>
                                                                </div>
                                                                <a x-show="task.url" :href="task.url" target="_blank" class="text-xs text-blue-600 hover:underline mt-1 inline-block">Open in Asana →</a>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </template>
                                            </div>
                                        </div>
                                    </template>
                                    <!-- Due Today -->
                                    <template x-if="dashboard?.briefing?.tasks?.due_today?.length > 0">
                                        <div>
                                            <p class="text-xs font-semibold text-green-600 uppercase tracking-wider mb-2">📅 Due Today</p>
                                            <div class="space-y-2">
                                                <template x-for="(task, idx) in dashboard?.briefing?.tasks?.due_today" :key="'today-' + idx">
                                                    <div class="p-3 bg-green-50 border border-green-100 rounded-lg" :class="task.done ? 'opacity-50' : ''">
                                                        <div class="flex items-start gap-2">
                                                            <button @click="toggleDone(task)" :title="task.done ? 'Mark as pending' : 'Mark as done (handled elsewhere)'"
                                                                    class="mt-0.5 w-6 h-6 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-colors"
                                                                    :class="task.done ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 text-transparent hover:border-green-400 hover:text-green-400'">
                                                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"></path></svg>
                                                            </button>
                                                            <div class="flex-1 min-w-0">
                                                                <p class="font-medium text-gray-800" :class="task.done ? 'line-through' : ''" x-text="task.title"></p>
                                                                <p class="text-xs text-gray-600 mt-1" x-show="task.description" x-text="task.description?.slice(0, 120) + (task.description?.length > 120 ? '…' : '')"></p>
                                                                <div class="flex items-center gap-2 mt-1 flex-wrap">
                                                                    <span class="text-xs text-gray-500" x-text="task.project_name" x-show="task.project_name"></span>
                                                                    <span class="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded" x-show="task.attention_reason" x-text="task.attention_reason"></span>
                                                                    <span class="text-xs text-gray-500" x-show="task.assignee_name" x-text="'👤 ' + task.assignee_name"></span>
                                                                </div>
                                                                <a x-show="task.url" :href="task.url" target="_blank" class="text-xs text-blue-600 hover:underline mt-1 inline-block">Open in Asana →</a>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </template>
                                            </div>
                                        </div>
                                    </template>
                                    <template x-if="!dashboard?.briefing?.tasks?.overdue?.length && !dashboard?.briefing?.tasks?.due_today?.length">
                                        <div class="text-center py-8 text-gray-400">
                                            <svg class="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                                            <p>No tasks due today</p>
                                        </div>
                                    </template>
                                </div>
                            </div>

                            <!-- Commitments & Follow-ups -->
                            <div class="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                                <div class="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                                    <h3 class="font-semibold text-gray-800 flex items-center gap-2">
                                        <svg class="w-5 h-5 text-purple-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"></path></svg>
                                        Overdue Follow-ups
                                    </h3>
                                </div>
                                <div class="p-4 max-h-80 overflow-y-auto">
                                    <template x-if="dashboard?.briefing?.overdue?.total > 0">
                                        <div class="space-y-3">
                                            <!-- Follow-ups -->
                                            <template x-for="(item, idx) in dashboard?.briefing?.overdue?.follow_ups || []" :key="'fu-' + idx">
                                                <div class="p-3 bg-yellow-50 border border-yellow-100 rounded-lg flex items-start gap-2" :class="item.done ? 'opacity-50' : ''">
                                                    <button @click="toggleDone(item)" :title="item.done ? 'Mark as pending' : 'Mark as done (handled elsewhere)'"
                                                            class="mt-0.5 w-6 h-6 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-colors"
                                                            :class="item.done ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 text-transparent hover:border-green-400 hover:text-green-400'">
                                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"></path></svg>
                                                    </button>
                                                    <div class="flex-1 min-w-0">
                                                    <p class="font-medium text-gray-800" :class="item.done ? 'line-through' : ''" x-text="item.title"></p>
                                                    <p class="text-xs text-gray-500" x-text="'With: ' + item.with_person"></p>
                                                    <span class="text-xs text-yellow-700 mt-1 inline-block" x-text="'Due: ' + item.due_date?.slice(0, 10)"></span>
                                                    </div>
                                                </div>
                                            </template>
                                            <!-- Commitments -->
                                            <template x-for="(item, idx) in dashboard?.briefing?.overdue?.commitments || []" :key="'cm-' + idx">
                                                <div class="p-3 bg-purple-50 border border-purple-100 rounded-lg flex items-start gap-2" :class="item.done ? 'opacity-50' : ''">
                                                    <button @click="toggleDone(item)" :title="item.done ? 'Mark as pending' : 'Mark as done (handled elsewhere)'"
                                                            class="mt-0.5 w-6 h-6 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-colors"
                                                            :class="item.done ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 text-transparent hover:border-green-400 hover:text-green-400'">
                                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"></path></svg>
                                                    </button>
                                                    <div class="flex-1 min-w-0">
                                                    <div class="flex items-center gap-2">
                                                        <span class="text-xs font-semibold" :class="item.direction === 'i_promised' ? 'text-purple-600' : 'text-cyan-600'" x-text="item.direction === 'i_promised' ? 'You promised:' : 'They promised:'"></span>
                                                    </div>
                                                    <p class="font-medium text-gray-800" :class="item.done ? 'line-through' : ''" x-text="item.title"></p>
                                                    <p class="text-xs text-gray-500" x-text="'To: ' + item.person"></p>
                                                    </div>
                                                </div>
                                            </template>
                                            <!-- Delegations -->
                                            <template x-for="(item, idx) in dashboard?.briefing?.overdue?.delegations || []" :key="'dl-' + idx">
                                                <div class="p-3 bg-blue-50 border border-blue-100 rounded-lg flex items-start gap-2" :class="item.done ? 'opacity-50' : ''">
                                                    <button @click="toggleDone(item)" :title="item.done ? 'Mark as pending' : 'Mark as done (handled elsewhere)'"
                                                            class="mt-0.5 w-6 h-6 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-colors"
                                                            :class="item.done ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 text-transparent hover:border-green-400 hover:text-green-400'">
                                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"></path></svg>
                                                    </button>
                                                    <div class="flex-1 min-w-0">
                                                    <p class="font-medium text-gray-800" :class="item.done ? 'line-through' : ''" x-text="item.title"></p>
                                                    <p class="text-xs text-gray-500" x-text="'Assigned to: ' + item.assignee"></p>
                                                    </div>
                                                </div>
                                            </template>
                                        </div>
                                    </template>
                                    <template x-if="!dashboard?.briefing?.overdue?.total">
                                        <div class="text-center py-8 text-gray-400">
                                            <svg class="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                                            <p>Nothing overdue!</p>
                                        </div>
                                    </template>
                                </div>
                            </div>
                        </div>

                        <!-- Errors Section -->
                        <template x-if="dashboard?.briefing?.errors?.length > 0">
                            <div class="mt-6 p-4 bg-red-50 border border-red-200 rounded-lg">
                                <h4 class="font-medium text-red-800 mb-2">⚠️ Some data could not be loaded:</h4>
                                <ul class="text-sm text-red-700 list-disc list-inside">
                                    <template x-for="(error, idx) in dashboard?.briefing?.errors" :key="idx">
                                        <li x-text="error"></li>
                                    </template>
                                </ul>
                            </div>
                        </template>
                    </div>

                    <!-- Error State -->
                    <div x-show="!dashboardLoading && dashboardError" x-cloak class="text-center py-12">
                        <svg class="w-16 h-16 mx-auto mb-4 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>
                        <h3 class="text-lg font-medium text-gray-800 mb-2">Could not load dashboard</h3>
                        <p class="text-gray-500 mb-4" x-text="dashboardError"></p>
                        <button @click="loadDashboard(true)" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">Try Again</button>
                    </div>
                </div>

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

                <!-- Activity Tab -->
                <div x-show="activeTab === 'activity'" x-cloak x-init="$watch('activeTab', val => { if(val === 'activity') startActivityMonitor(); else stopActivityMonitor(); })">
                    <!-- AI Core: neural network canvas, always visible, reacts to live activity -->
                    <div class="mb-6 rounded-2xl relative overflow-hidden ai-core-panel" style="height: 190px;">
                        <canvas x-ref="aiCanvas" class="absolute inset-0 w-full h-full"></canvas>
                        <div class="ai-core-grid absolute inset-0 pointer-events-none"></div>
                        <div class="ai-core-scanline absolute inset-x-0 pointer-events-none"></div>
                        <div class="relative h-full flex items-center justify-between px-8 pointer-events-none">
                            <div>
                                <div class="flex items-center gap-3">
                                    <span class="relative flex h-3 w-3">
                                        <span class="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75"
                                              :class="activityStats?.pending_count > 0 ? 'bg-fuchsia-400' : 'bg-cyan-400'"></span>
                                        <span class="relative inline-flex rounded-full h-3 w-3"
                                              :class="activityStats?.pending_count > 0 ? 'bg-fuchsia-400' : 'bg-cyan-400'"></span>
                                    </span>
                                    <h3 class="text-xl font-bold tracking-[0.3em] text-white font-mono">AI CORE</h3>
                                </div>
                                <p class="mt-2 font-mono text-sm tracking-widest transition-colors duration-500"
                                   :class="activityStats?.pending_count > 0 ? 'text-fuchsia-300' : 'text-cyan-300'"
                                   x-text="activityStats?.pending_count > 0
                                       ? '▸ PROCESSING ' + activityStats.pending_count + ' TOOL' + (activityStats.pending_count > 1 ? 'S' : '')
                                       : '▸ ONLINE — STANDBY'"></p>
                                <p class="mt-1 font-mono text-xs text-slate-400 h-4 ai-typing"
                                   x-show="activityStats?.pending_count > 0" x-cloak
                                   x-text="(activities.find(a => a.status === 'pending')?.tool_name || '') + '()'"></p>
                            </div>
                            <div class="text-right">
                                <div class="flex items-center gap-2 justify-end">
                                    <span class="w-2 h-2 bg-green-400 rounded-full animate-pulse"></span>
                                    <span class="text-xs font-mono tracking-widest text-green-300">LIVE FEED</span>
                                </div>
                                <p class="mt-2 font-mono text-3xl font-bold text-white" x-text="activityStats?.total_calls || 0"></p>
                                <p class="font-mono text-[10px] tracking-[0.25em] text-slate-400">OPERATIONS</p>
                            </div>
                        </div>
                    </div>

                    <!-- Header with Stats -->
                    <div class="flex justify-between items-center mb-6">
                        <div>
                            <h2 class="text-lg font-semibold flex items-center gap-2">
                                MCP Activity Monitor
                                <span x-show="activityAutoRefresh" class="flex items-center gap-1 text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full">
                                    <span class="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse"></span>
                                    Live
                                </span>
                            </h2>
                            <p class="text-sm text-gray-500">Real-time view of AI assistant tool executions</p>
                        </div>
                        <div class="flex gap-3 items-center">
                            <button @click="clearActivity()" class="flex items-center gap-2 px-3 py-2 bg-red-50 hover:bg-red-100 rounded-lg text-sm text-red-700 transition-colors">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
                                Clear
                            </button>
                            <div class="flex items-center gap-2 bg-gray-100 rounded-lg px-3 py-2">
                                <span class="text-sm text-gray-600">Auto-refresh</span>
                                <button @click="activityAutoRefresh = !activityAutoRefresh; toggleAutoRefresh()"
                                        class="relative w-11 h-6 rounded-full transition-colors duration-200"
                                        :class="activityAutoRefresh ? 'bg-green-500' : 'bg-gray-300'">
                                    <span class="absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-200"
                                          :class="activityAutoRefresh ? 'translate-x-5' : ''"></span>
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- Stats Cards -->
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                        <div class="stat-card bg-gradient-to-br from-blue-500 to-blue-600 rounded-xl p-4 text-white">
                            <div class="flex items-center justify-between">
                                <div>
                                    <p class="text-3xl font-bold animate-count" :key="activityStats?.total_calls" x-text="activityStats?.total_calls || 0"></p>
                                    <p class="text-blue-100 text-sm">Total Calls</p>
                                </div>
                                <svg class="w-10 h-10 text-blue-200 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                            </div>
                        </div>
                        <div class="stat-card bg-gradient-to-br from-green-500 to-green-600 rounded-xl p-4 text-white">
                            <div class="flex items-center justify-between">
                                <div>
                                    <p class="text-3xl font-bold animate-count" :key="activityStats?.successful_calls" x-text="activityStats?.successful_calls || 0"></p>
                                    <p class="text-green-100 text-sm">Successful</p>
                                </div>
                                <svg class="w-10 h-10 text-green-200 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                            </div>
                        </div>
                        <div class="stat-card bg-gradient-to-br from-red-500 to-red-600 rounded-xl p-4 text-white">
                            <div class="flex items-center justify-between">
                                <div>
                                    <p class="text-3xl font-bold animate-count" :key="activityStats?.failed_calls" x-text="activityStats?.failed_calls || 0"></p>
                                    <p class="text-red-100 text-sm">Failed</p>
                                </div>
                                <svg class="w-10 h-10 text-red-200 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                            </div>
                        </div>
                        <div class="stat-card bg-gradient-to-br from-purple-500 to-purple-600 rounded-xl p-4 text-white">
                            <div class="flex items-center justify-between">
                                <div>
                                    <p class="text-3xl font-bold animate-count" :key="activityStats?.success_rate" x-text="(activityStats?.success_rate || 0) + '%'"></p>
                                    <p class="text-purple-100 text-sm">Success Rate</p>
                                </div>
                                <svg class="w-10 h-10 text-purple-200 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path></svg>
                            </div>
                        </div>
                    </div>

                    <!-- Main Grid -->
                    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
                        <!-- Tools Used Chart -->
                        <div class="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
                            <h3 class="font-semibold text-gray-800 mb-3 flex items-center gap-2">
                                <svg class="w-5 h-5 text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>
                                Tools Used
                            </h3>
                            <div class="space-y-3 max-h-72 overflow-y-auto">
                                <template x-for="[tool, count] in Object.entries(activityStats?.tools_used || {}).sort((a,b) => b[1] - a[1]).slice(0, 10)" :key="tool">
                                    <div class="group">
                                        <div class="flex justify-between text-sm mb-1">
                                            <span class="font-mono text-gray-700 group-hover:text-indigo-600 transition-colors truncate" x-text="tool"></span>
                                            <span class="text-gray-500 font-medium" x-text="count"></span>
                                        </div>
                                        <div class="w-full bg-gray-100 rounded-full h-2.5 overflow-hidden">
                                            <div class="bg-gradient-to-r from-indigo-500 to-purple-500 h-2.5 rounded-full transition-all duration-700 ease-out"
                                                 :style="'width: ' + (count / Math.max(...Object.values(activityStats?.tools_used || {1:1})) * 100) + '%'"></div>
                                        </div>
                                    </div>
                                </template>
                                <template x-if="Object.keys(activityStats?.tools_used || {}).length === 0">
                                    <div class="text-center py-8 text-gray-400">
                                        <svg class="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path></svg>
                                        <p class="text-sm">No tools used yet</p>
                                    </div>
                                </template>
                            </div>
                        </div>

                        <!-- Activity Feed -->
                        <div class="lg:col-span-2 bg-white rounded-xl border border-gray-200 overflow-hidden shadow-sm">
                            <div class="px-4 py-3 border-b border-gray-100 flex items-center justify-between bg-gray-50">
                                <h3 class="font-semibold text-gray-800 flex items-center gap-2">
                                    <svg class="w-5 h-5 text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"></path></svg>
                                    Activity Feed
                                </h3>
                                <div class="flex items-center gap-2">
                                    <span class="text-xs text-gray-500" x-text="activities.length + ' events'"></span>
                                    <span x-show="activityLoading" class="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"></span>
                                </div>
                            </div>
                            <div class="max-h-96 overflow-y-auto">
                                <template x-if="activities.length > 0">
                                    <div class="divide-y divide-gray-100">
                                        <template x-for="(activity, idx) in activities" :key="activity.id + '-' + activity.status">
                                            <div class="px-4 py-3 hover:bg-indigo-50/50 transition-all duration-200"
                                                 :class="{'bg-yellow-50': activity.status === 'pending', 'animate-slide-in': idx < 3}">
                                                <div class="flex items-start gap-3">
                                                    <!-- Status Icon -->
                                                    <div class="mt-0.5 flex-shrink-0">
                                                        <template x-if="activity.status === 'pending'">
                                                            <div class="w-9 h-9 rounded-full bg-gradient-to-br from-yellow-400 to-orange-400 flex items-center justify-center shadow-lg shadow-yellow-200">
                                                                <svg class="w-5 h-5 text-white animate-spin" fill="none" viewBox="0 0 24 24">
                                                                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                                                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                                                </svg>
                                                            </div>
                                                        </template>
                                                        <template x-if="activity.status === 'success'">
                                                            <div class="w-9 h-9 rounded-full bg-gradient-to-br from-green-400 to-emerald-500 flex items-center justify-center shadow-lg shadow-green-200">
                                                                <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path></svg>
                                                            </div>
                                                        </template>
                                                        <template x-if="activity.status === 'error'">
                                                            <div class="w-9 h-9 rounded-full bg-gradient-to-br from-red-400 to-rose-500 flex items-center justify-center shadow-lg shadow-red-200">
                                                                <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12"></path></svg>
                                                            </div>
                                                        </template>
                                                    </div>
                                                    <!-- Content -->
                                                    <div class="flex-1 min-w-0">
                                                        <div class="flex items-center gap-2 flex-wrap">
                                                            <span class="font-mono text-sm font-bold text-gray-800" x-text="activity.tool_name"></span>
                                                            <span class="text-xs px-2 py-0.5 rounded-full font-medium"
                                                                  :class="{
                                                                      'bg-yellow-100 text-yellow-700 animate-pulse': activity.status === 'pending',
                                                                      'bg-green-100 text-green-700': activity.status === 'success',
                                                                      'bg-red-100 text-red-700': activity.status === 'error'
                                                                  }"
                                                                  x-text="activity.status === 'pending' ? 'processing...' : activity.status"></span>
                                                            <span class="text-xs text-gray-400 font-mono" x-show="activity.duration_ms" x-text="activity.duration_ms + 'ms'"></span>
                                                        </div>
                                                        <p class="text-xs text-gray-400 mt-0.5" x-text="new Date(activity.timestamp).toLocaleTimeString()"></p>
                                                        <!-- Args (collapsible) -->
                                                        <template x-if="Object.keys(activity.args || {}).length > 0">
                                                            <details class="mt-2">
                                                                <summary class="text-xs text-indigo-600 cursor-pointer hover:text-indigo-800">View parameters</summary>
                                                                <div class="mt-1 text-xs text-gray-600 font-mono bg-gray-50 rounded-lg px-3 py-2 overflow-x-auto" x-text="JSON.stringify(activity.args, null, 2)"></div>
                                                            </details>
                                                        </template>
                                                        <!-- Result -->
                                                        <template x-if="activity.result_summary">
                                                            <p class="mt-1.5 text-xs text-emerald-600 flex items-center gap-1">
                                                                <svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path></svg>
                                                                <span x-text="activity.result_summary"></span>
                                                            </p>
                                                        </template>
                                                        <!-- Error -->
                                                        <template x-if="activity.error_message">
                                                            <p class="mt-1.5 text-xs text-red-600 flex items-center gap-1">
                                                                <svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"></path></svg>
                                                                <span x-text="activity.error_message"></span>
                                                            </p>
                                                        </template>
                                                    </div>
                                                </div>
                                            </div>
                                        </template>
                                    </div>
                                </template>
                                <template x-if="activities.length === 0 && !activityLoading">
                                    <div class="text-center py-16 text-gray-400">
                                        <div class="w-20 h-20 mx-auto mb-4 rounded-full bg-gradient-to-br from-gray-100 to-gray-200 flex items-center justify-center">
                                            <svg class="w-10 h-10 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                                        </div>
                                        <p class="font-medium text-gray-500">No activity yet</p>
                                        <p class="text-sm mt-1">Tool executions will appear here in real-time</p>
                                        <p class="text-xs mt-3 text-gray-400">Try using the MCP server from Claude Desktop</p>
                                    </div>
                                </template>
                            </div>
                        </div>
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
                activeTab: 'dashboard',
                settings: {
                    general: {environment: 'development', debug: true, log_level: 'INFO', ai_provider: 'openai', default_model: 'gpt-4o-mini', premium_model: 'gpt-4o', max_tokens: 4096, temperature: 0.7, azure_endpoint: '', azure_deployment: '', local_base_url: ''},
                    database: {driver: 'sqlite+aiosqlite', name: 'executive_copilot.db', host: 'localhost', port: 5432}
                },
                organizations: [],
                contextRules: [],
                credentials: [],
                automationJobs: [],
                options: {aiProviders: [], models: {}, contextSources: [], orgTypes: [], jobTypes: [], providerTypes: []},

                // Dashboard
                dashboard: null,
                dashboardLoading: false,
                dashboardRefreshing: false,
                dashboardError: null,

                // Activity
                activities: [],
                activityStats: null,
                activityLoading: false,
                activityAutoRefresh: false,
                activityRefreshInterval: null,
                aiCanvasNet: null,

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
                        this.loadAutomationJobs(),
                        this.loadDashboard()
                    ]);
                },

                async loadDashboard(forceRefresh = false) {
                    // Only blank the screen on first load; refreshes keep current data visible
                    if (!this.dashboard) this.dashboardLoading = true;
                    this.dashboardRefreshing = forceRefresh;
                    this.dashboardError = null;
                    try {
                        const url = forceRefresh ? '/api/dashboard?refresh=true' : '/api/dashboard';
                        const response = await fetch(url);
                        const data = await response.json();
                        if (data.status === 'error') {
                            this.dashboardError = data.error;
                        } else {
                            this.dashboard = data;
                        }
                    } catch (e) {
                        this.dashboardError = 'Failed to connect to server';
                    } finally {
                        this.dashboardLoading = false;
                        this.dashboardRefreshing = false;
                    }
                },

                async toggleDone(item) {
                    if (!item.item_key) return;
                    const newDone = !item.done;
                    try {
                        const response = await fetch('/api/dashboard/done', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({key: item.item_key, done: newDone})
                        });
                        if (!response.ok) throw new Error('Request failed');
                        item.done = newDone;
                    } catch (e) {
                        this.showToast('Could not update item', 'error');
                    }
                },

                async loadActivity() {
                    this.activityLoading = true;
                    try {
                        const response = await fetch('/api/activity');
                        const data = await response.json();
                        this.activities = data.activities || [];
                        this.activityStats = data.stats || {};
                    } catch (e) {
                        console.error('Failed to load activity:', e);
                    } finally {
                        this.activityLoading = false;
                    }
                },

                async clearActivity() {
                    if (confirm('Clear all activity history?')) {
                        await fetch('/api/activity/clear', {method: 'POST'});
                        await this.loadActivity();
                        this.showToast('Activity cleared', 'success');
                    }
                },

                toggleAutoRefresh() {
                    if (this.activityAutoRefresh) {
                        this.startActivityMonitor();
                    } else {
                        this.stopActivityMonitor();
                    }
                },

                startActivityMonitor() {
                    this.loadActivity();
                    this.activityAutoRefresh = true;
                    if (!this.activityRefreshInterval) {
                        this.activityRefreshInterval = setInterval(() => this.loadActivity(), 1500);
                    }
                    // Canvas is inside an x-show container; wait a tick until it's visible
                    this.$nextTick(() => this.startAICanvas());
                },

                stopActivityMonitor() {
                    if (this.activityRefreshInterval) {
                        clearInterval(this.activityRefreshInterval);
                        this.activityRefreshInterval = null;
                    }
                    this.activityAutoRefresh = false;
                    this.stopAICanvas();
                },

                startAICanvas() {
                    const canvas = this.$refs.aiCanvas;
                    if (!canvas || this.aiCanvasNet) {
                        if (this.aiCanvasNet) this.aiCanvasNet.running = true;
                        if (this.aiCanvasNet && !this.aiCanvasNet.raf) this.aiCanvasNet.tick();
                        return;
                    }
                    const ctx = canvas.getContext('2d');
                    const dpr = window.devicePixelRatio || 1;
                    const N = 26;
                    const net = {
                        running: true, raf: null, energy: 0.15, pulses: [], nodes: [],
                        resize() {
                            canvas.width = canvas.clientWidth * dpr;
                            canvas.height = canvas.clientHeight * dpr;
                        },
                        isActive: () => (this.activityStats?.pending_count || 0) > 0,
                    };
                    net.resize();
                    window.addEventListener('resize', () => net.resize());
                    for (let i = 0; i < N; i++) {
                        net.nodes.push({
                            x: Math.random(), y: Math.random(),
                            vx: (Math.random() - 0.5) * 0.0006, vy: (Math.random() - 0.5) * 0.0006,
                            r: 1.5 + Math.random() * 2, phase: Math.random() * Math.PI * 2,
                        });
                    }
                    net.tick = () => {
                        if (!net.running) { net.raf = null; return; }
                        const W = canvas.width, H = canvas.height;
                        // Ease energy toward target: calm cyan when idle, hot violet when processing
                        const target = net.isActive() ? 1 : 0.15;
                        net.energy += (target - net.energy) * 0.04;
                        const e = net.energy;
                        const hue = 190 + 85 * e;  // cyan -> violet/fuchsia
                        const t = performance.now() / 1000;

                        ctx.clearRect(0, 0, W, H);

                        // Drift nodes
                        const speed = 1 + e * 3;
                        for (const n of net.nodes) {
                            n.x += n.vx * speed; n.y += n.vy * speed;
                            if (n.x < 0.02 || n.x > 0.98) n.vx *= -1;
                            if (n.y < 0.05 || n.y > 0.95) n.vy *= -1;
                        }

                        // Edges between nearby nodes
                        const edges = [];
                        const maxDist = 0.22;
                        for (let i = 0; i < N; i++) {
                            for (let j = i + 1; j < N; j++) {
                                const a = net.nodes[i], b = net.nodes[j];
                                const dx = a.x - b.x, dy = a.y - b.y;
                                const d = Math.sqrt(dx * dx + dy * dy);
                                if (d < maxDist) edges.push([a, b, 1 - d / maxDist]);
                            }
                        }
                        for (const [a, b, s] of edges) {
                            ctx.strokeStyle = `hsla(${hue}, 90%, 65%, ${s * (0.10 + e * 0.25)})`;
                            ctx.lineWidth = dpr;
                            ctx.beginPath();
                            ctx.moveTo(a.x * W, a.y * H);
                            ctx.lineTo(b.x * W, b.y * H);
                            ctx.stroke();
                        }

                        // Spawn pulses that travel along edges (more when active)
                        if (edges.length && Math.random() < 0.04 + e * 0.35) {
                            net.pulses.push({ edge: edges[Math.floor(Math.random() * edges.length)], t: 0 });
                        }
                        net.pulses = net.pulses.filter(p => p.t <= 1);
                        for (const p of net.pulses) {
                            p.t += 0.02 + e * 0.03;
                            const [a, b] = p.edge;
                            const x = (a.x + (b.x - a.x) * p.t) * W;
                            const y = (a.y + (b.y - a.y) * p.t) * H;
                            const g = ctx.createRadialGradient(x, y, 0, x, y, 6 * dpr);
                            g.addColorStop(0, `hsla(${hue}, 100%, 80%, 0.9)`);
                            g.addColorStop(1, `hsla(${hue}, 100%, 80%, 0)`);
                            ctx.fillStyle = g;
                            ctx.beginPath();
                            ctx.arc(x, y, 6 * dpr, 0, Math.PI * 2);
                            ctx.fill();
                        }

                        // Nodes with breathing glow
                        for (const n of net.nodes) {
                            const glow = 0.5 + 0.5 * Math.sin(t * (1 + e * 3) + n.phase);
                            const x = n.x * W, y = n.y * H, r = n.r * dpr * (1 + glow * 0.5);
                            const g = ctx.createRadialGradient(x, y, 0, x, y, r * 4);
                            g.addColorStop(0, `hsla(${hue}, 95%, 72%, ${0.5 + e * 0.4})`);
                            g.addColorStop(1, `hsla(${hue}, 95%, 72%, 0)`);
                            ctx.fillStyle = g;
                            ctx.beginPath();
                            ctx.arc(x, y, r * 4, 0, Math.PI * 2);
                            ctx.fill();
                            ctx.fillStyle = `hsla(${hue}, 100%, 88%, 0.95)`;
                            ctx.beginPath();
                            ctx.arc(x, y, r, 0, Math.PI * 2);
                            ctx.fill();
                        }

                        net.raf = requestAnimationFrame(net.tick);
                    };
                    this.aiCanvasNet = net;
                    net.tick();
                },

                stopAICanvas() {
                    if (this.aiCanvasNet) {
                        this.aiCanvasNet.running = false;
                        if (this.aiCanvasNet.raf) cancelAnimationFrame(this.aiCanvasNet.raf);
                        this.aiCanvasNet.raf = null;
                    }
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
