"""JSON API for the web workspace (served by the admin app under /api/ws)."""

from __future__ import annotations

import time
from datetime import date
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from executive_copilot.logging import get_logger
from executive_copilot.services.accounts import AccountsService
from executive_copilot.services.security_scan import SecurityScanService
from executive_copilot.services.status import StatusService
from executive_copilot.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path

    from executive_copilot.credentials import CredentialManager

logger = get_logger(__name__)


class TaskInput(BaseModel):
    title: str
    notes: str | None = None
    due_date: str | None = None
    project_id: str | None = None
    account: str | None = None


class EventInput(BaseModel):
    title: str
    start: str
    end: str | None = None
    location: str | None = None
    description: str | None = None
    private: bool = False
    attendees: list[str] = Field(default_factory=list)


class EmailEventInput(BaseModel):
    message_id: str
    account: str
    title: str
    start: str
    end: str | None = None
    location: str | None = None
    notes: str | None = None
    sender: str | None = None
    subject: str | None = None
    web_link: str | None = None


class DismissInput(BaseModel):
    message_id: str
    account: str


class NoteInput(BaseModel):
    content: str


class SyncInput(BaseModel):
    days_back: int = Field(default=7, ge=1, le=60)
    dry_run: bool = True


class OAuthClientInput(BaseModel):
    family: str
    account_id: str
    client_id: str | None = None
    client_secret: str | None = None
    tenant_id: str | None = None


class TokenInput(BaseModel):
    kind: str
    account_id: str = "default"
    token: str


class AccountRef(BaseModel):
    kind: str
    account_id: str


class CalendarRef(BaseModel):
    provider: str
    account_id: str
    calendar_id: str = "primary"


class CalendarSettingsInput(BaseModel):
    default: CalendarRef | None = None
    email_target: CalendarRef | None = None
    use_default_for_email: bool = False
    source_accounts: list[str] | None = None


class VaultEditInput(BaseModel):
    note: str
    action: str
    section: str | None = None
    find: str | None = None
    text: str | None = None
    dry_run: bool = False


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatInput(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None


class SecurityStateInput(BaseModel):
    state: str
    note: str | None = None


class WatchItemInput(BaseModel):
    product: str
    name: str | None = None
    cpe: str | None = None
    cycles: int | None = Field(default=None, ge=1, le=6)
    lts_only: bool | None = None
    where: list[str] = Field(default_factory=list)


class SecuritySettingsInput(BaseModel):
    enabled: bool | None = None
    cycles: int | None = Field(default=None, ge=1, le=6)
    scan_repos: bool | None = None
    use_ai: bool | None = None
    eol_warning_days: int | None = Field(default=None, ge=0)


def create_workspace_router(config_dir: Path, credential_manager: CredentialManager) -> APIRouter:
    """Routes for the workspace page. Config is re-read per request so edits apply live."""
    router = APIRouter(prefix="/api/ws", tags=["workspace"])
    from executive_copilot.admin.knowledge_api import knowledge_router

    router.include_router(knowledge_router(config_dir, credential_manager))
    state: dict[str, Any] = {}

    def ws() -> WorkspaceService:
        from executive_copilot.config import load_config

        return WorkspaceService(load_config(config_dir), credential_manager)

    async def close(service: Any) -> None:
        if service.db_manager is not None:
            await service.db_manager.close()

    def bad_request(e: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(e))

    # --- overview / calendar ----------------------------------------------

    @router.get("/overview")
    async def overview() -> dict[str, Any]:
        from executive_copilot.services.security_store import SecurityStore

        result = await ws().overview()
        try:
            result["security"] = SecurityStore().summary()
        except Exception as e:  # a broken activity.db must not take Hoy down
            logger.warning("Security summary unavailable", error=str(e))
        result["calendar_review"] = state.get("calendar_review")
        return result

    @router.get("/events")
    async def events(days: int = 7, start: str | None = None) -> dict[str, Any]:
        return await ws().events(days=max(1, min(days, 31)), start=date.fromisoformat(start) if start else None)

    @router.post("/events")
    async def create_event(payload: EventInput) -> dict[str, Any]:
        from executive_copilot.services.email_calendar import EmailCalendarService

        service = ws()
        try:
            event = await EmailCalendarService(service.config, credential_manager).create_event(
                title=payload.title,
                start=payload.start,
                end=payload.end,
                location=payload.location,
                description=payload.description,
                private=payload.private,
                attendees=payload.attendees or None,
            )
        except ValueError as e:
            raise bad_request(e) from e
        return {"created": event}

    # --- e-mail -> calendar -----------------------------------------------

    @router.get("/calendar-requests")
    async def calendar_requests(days_back: int = 7, include_processed: bool = False) -> dict[str, Any]:
        service = ws().email_calendar()
        try:
            result = await service.review(days_back=days_back, include_processed=include_processed)
        finally:
            await close(service)
        state["calendar_review"] = {
            "count": len(result["proposals"]),
            "pending": [p for p in result["proposals"] if not p["already_on_calendar"]],
        }
        return result

    @router.post("/calendar-requests/add")
    async def add_email_event(payload: EmailEventInput) -> dict[str, Any]:
        service = ws().email_calendar(with_ai=False)
        try:
            return await service.add_to_calendar(**payload.model_dump())
        except ValueError as e:
            raise bad_request(e) from e
        finally:
            await close(service)

    @router.post("/calendar-requests/dismiss")
    async def dismiss_email_event(payload: DismissInput) -> dict[str, Any]:
        service = ws().email_calendar(with_ai=False)
        try:
            return await service.dismiss(payload.message_id, payload.account)
        finally:
            await close(service)

    # --- tasks ------------------------------------------------------------

    @router.get("/tasks")
    async def tasks() -> dict[str, Any]:
        service = ws()
        asana = await service.asana_tasks()
        tracked = await service.tracked_items()
        return {**asana, **tracked}

    @router.post("/tasks")
    async def create_task(payload: TaskInput) -> dict[str, Any]:
        try:
            return {"task": await ws().create_asana_task(**payload.model_dump())}
        except ValueError as e:
            raise bad_request(e) from e

    @router.post("/tasks/{task_id}/complete")
    async def complete_task(task_id: str, account: str | None = None) -> dict[str, Any]:
        try:
            return {"task": await ws().complete_asana_task(task_id, account)}
        except ValueError as e:
            raise bad_request(e) from e

    @router.post("/items/{kind}/{item_id}/complete")
    async def complete_item(kind: str, item_id: str) -> dict[str, Any]:
        try:
            return await ws().complete_item(kind, item_id)
        except ValueError as e:
            raise bad_request(e) from e

    # --- Obsidian ---------------------------------------------------------

    def vault(folder: str) -> Any:
        try:
            return ws().vault(folder)
        except ValueError as e:
            raise bad_request(e) from e

    @router.get("/notes")
    async def list_notes(folder: str = "meetings") -> dict[str, Any]:
        v = vault(folder)
        return {"folder": folder, "path": str(v.meetings_dir), "notes": v.list_notes()}

    @router.get("/notes/search")
    async def search_notes(q: str, folder: str = "meetings", since: str | None = None) -> dict[str, Any]:
        v = vault(folder)
        results = v.search(q, limit=40, since=date.fromisoformat(since) if since else None)
        return {"query": q, "results": results}

    @router.get("/notes/{name}")
    async def read_note(name: str, folder: str = "meetings") -> dict[str, Any]:
        v = vault(folder)
        if name not in v.note_names():
            raise HTTPException(status_code=404, detail=f"Note not found: {name}")
        return {"name": name, "folder": folder, "content": v.read(name) or ""}

    @router.put("/notes/{name}")
    async def save_note(name: str, payload: NoteInput, folder: str = "meetings") -> dict[str, Any]:
        v = vault(folder)
        # Only existing notes can be edited: the name must match a file in the
        # folder, which also rules out path traversal.
        if name not in v.note_names():
            raise HTTPException(status_code=404, detail=f"Note not found: {name}")
        v.write(name, payload.content)
        return {"name": name, "saved": True, "chars": len(payload.content)}

    @router.post("/vault/edit")
    async def edit_vault(payload: VaultEditInput) -> dict[str, Any]:
        from pathlib import Path

        from executive_copilot.services.vault_index import VaultEditError, build_index

        service = ws()
        try:
            return build_index(service.config).edit(
                payload.note, payload.action, section=payload.section, find=payload.find,
                text=payload.text, dry_run=payload.dry_run,
                backup_dir=Path(service.config.obsidian.backup_dir),
            )
        except (VaultEditError, ValueError) as e:
            raise bad_request(e) from e

    @router.post("/notes/sync")
    async def sync_notes(payload: SyncInput) -> dict[str, Any]:
        return await ws().notes_service(with_ai=True).sync(days_back=payload.days_back, dry_run=payload.dry_run)

    # --- accounts & credentials --------------------------------------------

    def accounts() -> AccountsService:
        from executive_copilot.config import load_config

        return AccountsService(load_config(config_dir), credential_manager)

    @router.get("/accounts")
    async def list_accounts() -> dict[str, Any]:
        return accounts().list_accounts()

    @router.post("/accounts/test")
    async def test_account(payload: AccountRef) -> dict[str, Any]:
        return await accounts().test(payload.kind, payload.account_id)

    @router.post("/accounts/oauth-client")
    async def set_oauth_client(payload: OAuthClientInput) -> dict[str, Any]:
        try:
            return accounts().set_oauth_client(
                payload.family, payload.account_id, payload.client_id, payload.client_secret, payload.tenant_id
            )
        except ValueError as e:
            raise bad_request(e) from e

    @router.post("/accounts/token")
    async def save_token(payload: TokenInput) -> dict[str, Any]:
        try:
            return accounts().save_token(payload.kind, payload.account_id, payload.token)
        except ValueError as e:
            raise bad_request(e) from e

    @router.post("/accounts/disconnect")
    async def disconnect_account(payload: AccountRef) -> dict[str, Any]:
        return accounts().disconnect(payload.kind, payload.account_id)

    @router.post("/accounts/remove")
    async def remove_account(payload: AccountRef) -> dict[str, Any]:
        try:
            return accounts().remove(payload.kind, payload.account_id)
        except ValueError as e:
            raise bad_request(e) from e

    # --- status console -----------------------------------------------------

    def status() -> StatusService:
        from executive_copilot.config import load_config

        return StatusService(load_config(config_dir), credential_manager)

    @router.get("/status")
    async def status_snapshot() -> dict[str, Any]:
        snapshot = status().snapshot()
        snapshot["checks"] = state.get("checks", {})
        return snapshot

    @router.post("/status/checks")
    async def status_checks(force: bool = False) -> dict[str, Any]:
        """Live API checks per account; cached a minute so refreshes stay cheap."""
        cached_at = state.get("checks_at", 0.0)
        if force or time.monotonic() - cached_at > 60:
            state["checks"] = await status().probe()
            state["checks_at"] = time.monotonic()
        return {"checks": state["checks"]}

    # --- security ---------------------------------------------------------

    def security(ai: bool = False) -> SecurityScanService:
        service = ws()
        return SecurityScanService(service.config, credential_manager, service.ai_service() if ai else None)

    def security_section() -> dict[str, Any]:
        """Current ``security:`` section as saved in config/security.yaml (watchlist + flags)."""
        from executive_copilot.config import load_config
        from executive_copilot.config.loader import load_security_settings

        saved = load_security_settings(config_dir)
        if saved:
            return saved
        # First save: start from the effective config so hand-edited values are kept.
        return load_config(config_dir).security.model_dump(exclude_none=True)

    @router.get("/security")
    async def security_overview(watchlist: bool = True) -> dict[str, Any]:
        return await security().overview(with_watchlist=watchlist)

    @router.post("/security/run")
    async def security_run() -> dict[str, Any]:
        """Run the scan now; recorded in the job run log like a scheduled run."""
        from datetime import datetime

        from executive_copilot.automation.jobs import JobResult, JobStatus
        from executive_copilot.automation.run_log import get_run_log

        service = security(ai=True)
        started_at = datetime.utcnow()
        if not service.settings.enabled:
            raise HTTPException(status_code=400, detail="La revisión de seguridad está desactivada")
        stats = await service.scan()
        failed = "error" in stats
        completed_at = datetime.utcnow()
        get_run_log().record(JobResult(
            job_name="security_scan",
            status=JobStatus.FAILED if failed else JobStatus.COMPLETED,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=(completed_at - started_at).total_seconds(),
            items_processed=stats.get("findings", 0),
            error_message=stats.get("error"),
            details=stats,
        ))
        if failed:
            raise HTTPException(status_code=502, detail=stats["error"])
        return {"status": "completed", "details": stats}

    @router.post("/security/findings/{finding_id:path}")
    async def security_set_state(finding_id: str, payload: SecurityStateInput) -> dict[str, Any]:
        from executive_copilot.services.security_store import SecurityStore

        try:
            row = SecurityStore().set_state(finding_id, payload.state, payload.note)
        except ValueError as e:
            raise bad_request(e) from e
        if row is None:
            raise HTTPException(status_code=404, detail="finding not found")
        return row

    @router.put("/security/settings")
    async def security_settings(payload: SecuritySettingsInput) -> dict[str, Any]:
        from executive_copilot.config.loader import save_security_settings

        section = security_section()
        section.update({k: v for k, v in payload.model_dump().items() if v is not None})
        save_security_settings(config_dir, section)
        return await security().overview(with_watchlist=False)

    @router.post("/security/watchlist")
    async def security_add_watch(payload: WatchItemInput) -> dict[str, Any]:
        """Add or replace a watched product; the slug is validated against endoflife.date."""
        import httpx

        from executive_copilot.config.loader import save_security_settings
        from executive_copilot.services.security_scan import EOL_API

        product = payload.product.strip().lower()
        if not product:
            raise HTTPException(status_code=400, detail="product is required")
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                r = await client.get(f"{EOL_API}/{product}.json")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"endoflife.date no responde: {e}") from e
        if r.status_code == 404:
            raise HTTPException(
                status_code=400,
                detail=f"'{product}' no existe en endoflife.date (usa el slug de la URL, p. ej. 'postgresql')",
            )
        section = security_section()
        items = [i for i in section.get("watchlist", []) if i.get("product") != product]
        item = {k: v for k, v in payload.model_dump().items() if v not in (None, [], "")}
        item["product"] = product
        items.append(item)
        section["watchlist"] = items
        save_security_settings(config_dir, section)
        return await security().overview(with_watchlist=True)

    @router.delete("/security/watchlist/{product}")
    async def security_remove_watch(product: str) -> dict[str, Any]:
        from executive_copilot.config.loader import save_security_settings

        section = security_section()
        section["watchlist"] = [i for i in section.get("watchlist", []) if i.get("product") != product]
        save_security_settings(config_dir, section)
        return await security().overview(with_watchlist=True)

    # --- calendar settings --------------------------------------------------

    @router.get("/calendars")
    async def list_calendars() -> dict[str, Any]:
        return await accounts().calendars()

    @router.get("/settings")
    async def get_settings() -> dict[str, Any]:
        from executive_copilot.services.email_calendar import EmailCalendarService

        service = ws()
        cal = EmailCalendarService(service.config, credential_manager)
        try:
            default = cal.default_calendar()
            target = cal.target_calendar()
        except ValueError:
            default = target = ("google", "", "primary")
        settings = service.config.email_calendar
        return {
            "default_calendar": dict(zip(("provider", "account_id", "calendar_id"), default, strict=True)),
            "email_target": dict(zip(("provider", "account_id", "calendar_id"), target, strict=True)),
            "email_target_is_default": not settings.target_account_id,
            "source_accounts": cal.source_accounts(),
            "outlook_accounts": [
                a["account_id"] for a in credential_manager.list_accounts_for_provider("email", "outlook")
            ],
            "timezone": service.config.calendar_defaults.timezone,
        }

    @router.put("/settings/calendar")
    async def save_calendar_settings(payload: CalendarSettingsInput) -> dict[str, Any]:
        from executive_copilot.config.loader import save_ui_settings

        patch: dict[str, Any] = {}
        if payload.default:
            patch["calendar_defaults"] = payload.default.model_dump()
        email: dict[str, Any] = {}
        if payload.use_default_for_email:
            email.update({"target_provider": None, "target_account_id": None, "target_calendar_id": None})
        elif payload.email_target:
            email.update({
                "target_provider": payload.email_target.provider,
                "target_account_id": payload.email_target.account_id,
                "target_calendar_id": payload.email_target.calendar_id,
            })
        if payload.source_accounts is not None:
            email["source_accounts"] = payload.source_accounts
        if email:
            patch["email_calendar"] = email
        save_ui_settings(config_dir, patch)
        return await get_settings()

    # --- relationship map ---------------------------------------------------

    @router.get("/graph")
    async def graph() -> dict[str, Any]:
        from executive_copilot.services.knowledge_graph import KnowledgeGraphService

        return await KnowledgeGraphService(ws()).build()

    # --- chat -------------------------------------------------------------

    @router.get("/chat/models")
    async def chat_models() -> dict[str, Any]:
        from executive_copilot.services.chat import available_models

        ai = ws().ai_service()
        if ai is None:
            return {"models": [], "error": "Server AI is not configured"}
        return {"models": await available_models(ai)}

    @router.post("/chat")
    async def chat(payload: ChatInput) -> dict[str, Any]:
        from executive_copilot.services.chat import WorkspaceChat

        service = ws()
        ai = service.ai_service()
        if ai is None:
            raise HTTPException(
                status_code=503,
                detail="Server AI is not configured: add an OpenAI API key in Settings → Credentials.",
            )
        if payload.model:
            from executive_copilot.services.chat import available_models

            allowed = {m["id"] for m in await available_models(ai)}
            if payload.model not in allowed:
                raise HTTPException(status_code=400, detail=f"Modelo no disponible: {payload.model}")
        try:
            return await WorkspaceChat(service, ai).respond(
                [m.model_dump() for m in payload.messages], model=payload.model
            )
        except Exception as e:
            logger.error("Chat failed", error=str(e))
            raise HTTPException(status_code=502, detail=f"Chat failed: {e}") from e

    return router
