"""Workspace chat: the server AI answers with live access to the workspace.

Read tools run immediately (tasks, agenda, mail search, Obsidian notes,
e-mail -> calendar review). Anything that changes data is never executed by
the model: ``propose_*`` tools only return an *action card* that the web page
shows with a confirm button, and the page performs the change through the
regular API when the user clicks it.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from executive_copilot.logging import get_logger

if TYPE_CHECKING:
    from executive_copilot.services.workspace import WorkspaceService

logger = get_logger(__name__)

MAX_STEPS = 6
MAX_TOOL_OUTPUT = 8000
MAX_HISTORY = 12

# Models offered in the chat picker, cheapest first, shown only when the
# OpenAI key can use them. "cost" is a relative hint for the UI.
CHAT_MODELS: list[dict[str, str]] = [
    {"id": "gpt-4.1-nano", "label": "GPT-4.1 nano", "cost": "mínimo"},
    {"id": "gpt-5-nano", "label": "GPT-5 nano", "cost": "mínimo"},
    {"id": "gpt-4o-mini", "label": "GPT-4o mini", "cost": "bajo"},
    {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini", "cost": "bajo"},
    {"id": "gpt-5-mini", "label": "GPT-5 mini", "cost": "medio"},
    {"id": "gpt-4.1", "label": "GPT-4.1", "cost": "alto"},
    {"id": "gpt-4o", "label": "GPT-4o", "cost": "alto"},
    {"id": "gpt-5", "label": "GPT-5", "cost": "alto"},
]
_models_cache: dict[str, Any] = {}


async def available_models(ai_service: Any) -> list[dict[str, Any]]:
    """Chat models this OpenAI key can use (cached for an hour).

    Falls back to the configured default/premium models when the account's
    model list cannot be read.
    """
    import time

    cfg = ai_service.container.config.ai
    configured = [m for m in dict.fromkeys([cfg.default_model, cfg.premium_model]) if m]
    cached = _models_cache.get("value")
    if cached is None or time.time() - _models_cache.get("at", 0) > 3600:
        try:
            client = ai_service._get_openai_client()
            listing = await client.models.list()
            cached = {m.id for m in listing.data}
        except Exception as e:
            logger.warning("Could not list OpenAI models", error=str(e))
            cached = set()
        _models_cache.update(value=cached, at=time.time())
    known = {m["id"] for m in CHAT_MODELS}
    models = [m for m in CHAT_MODELS if not cached or m["id"] in cached]
    for model_id in configured:
        if model_id not in known:
            models.append({"id": model_id, "label": model_id, "cost": "configurado"})
    default = cfg.default_model if any(m["id"] == cfg.default_model for m in models) else models[0]["id"]
    return [{**m, "default": m["id"] == default} for m in models]

INSTRUCTIONS = """Eres el copiloto ejecutivo de {user}. Hoy es {today} ({weekday}),
zona horaria {tz}. Respondes en el idioma del usuario (normalmente español),
de forma breve y concreta, con viñetas cuando ayuden.

Tienes herramientas para consultar su agenda, tareas de Asana, seguimientos,
compromisos y delegaciones, buscar correos, buscar y leer su bóveda de
Obsidian (reuniones, personas, proyectos) y revisar correos que requieren algo en el calendario.
Úsalas antes de responder sobre su información; no inventes datos.

Tú NO ejecutas cambios. Para cualquier cambio usa una herramienta propose_*:
eso muestra una tarjeta que el usuario confirma. Después de proponer, di
exactamente "Te dejé una tarjeta para confirmar" y nunca "he marcado",
"he creado", "completé" ni "agendé". Seguimientos, compromisos y delegaciones
se completan con propose_complete_item; solo las tareas de Asana con
propose_complete_task.
El calendario por defecto para eventos nuevos es {calendar}. Las fechas y
horas van en formato local "YYYY-MM-DDTHH:MM" (o "YYYY-MM-DD" si es de día completo).
"""


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


STR = {"type": "string"}
NULLABLE_STR = {"type": ["string", "null"]}

TOOLS: list[dict[str, Any]] = [
    _fn("get_overview", "Agenda de hoy, tareas vencidas/de hoy, seguimientos y compromisos vencidos.", {}),
    _fn(
        "get_events",
        "Eventos de todos los calendarios desde una fecha.",
        {
            "start": {"type": ["string", "null"], "description": "YYYY-MM-DD; hoy si es null"},
            "days": {"type": "integer", "description": "Días a cubrir (1-31)"},
        },
        ["start", "days"],
    ),
    _fn("get_tasks", "Tareas abiertas de Asana asignadas al usuario.", {}),
    _fn("get_tracked_items", "Seguimientos, compromisos (míos y de otros) y delegaciones pendientes.", {}),
    _fn(
        "search_emails",
        "Busca correos en todas las cuentas.",
        {"query": STR, "limit": {"type": "integer", "description": "Máximo por cuenta (1-20)"}},
        ["query", "limit"],
    ),
    _fn("vault_overview", "Mapa de la bóveda de Obsidian (carpetas y notas). Úsalo antes de leer.",
        {"folder": {"type": ["string", "null"], "description": "Carpeta a listar o null"}}, ["folder"]),
    _fn(
        "search_vault",
        "Busca en Obsidian (reuniones, personas, proyectos, arquitectura). Devuelve fragmentos cortos.",
        {"query": STR, "folder": {"type": ["string", "null"], "description": "Filtro de carpeta o null"}},
        ["query", "folder"],
    ),
    _fn(
        "read_vault_note",
        "Lee una nota de Obsidian o solo una sección (las notas largas traen índice de secciones).",
        {"note": STR, "section": {"type": ["string", "null"], "description": "Encabezado a leer o null"}},
        ["note", "section"],
    ),
    _fn("get_person_context", "Nota de una persona, notas que la enlazan y menciones recientes.", {"name": STR}, ["name"]),
    _fn(
        "review_email_calendar_requests",
        "Revisa correos de Outlook que requieren algo en el calendario (tarda ~20 s).",
        {"days_back": {"type": "integer", "description": "1-30"}},
        ["days_back"],
    ),
    _fn(
        "propose_vault_edit",
        "Propone editar una nota de Obsidian (registrar decisión, marcar casilla, agregar o corregir texto). "
        "El usuario confirma. action: check|uncheck|append|replace.",
        {
            "note": STR,
            "action": {"type": "string", "enum": ["check", "uncheck", "append", "replace"]},
            "section": NULLABLE_STR,
            "find": NULLABLE_STR,
            "text": NULLABLE_STR,
            "title": {"type": "string", "description": "Resumen corto del cambio para la tarjeta"},
        },
        ["note", "action", "section", "find", "text", "title"],
    ),
    _fn(
        "propose_create_task",
        "Propone crear una tarea en Asana (el usuario confirma).",
        {"title": STR, "notes": NULLABLE_STR, "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"}},
        ["title", "notes", "due_date"],
    ),
    _fn(
        "propose_complete_item",
        "Propone completar un seguimiento, compromiso o delegación (ids de get_tracked_items). El usuario confirma.",
        {
            "kind": {"type": "string", "enum": ["follow_up", "commitment", "delegation"]},
            "item_id": STR,
            "title": STR,
        },
        ["kind", "item_id", "title"],
    ),
    _fn(
        "propose_complete_task",
        "Propone completar una tarea de ASANA (solo ids numéricos de get_tasks; para seguimientos, "
        "compromisos o delegaciones usa propose_complete_item). El usuario confirma.",
        {"task_id": STR, "title": STR, "account": NULLABLE_STR},
        ["task_id", "title", "account"],
    ),
    _fn(
        "propose_calendar_event",
        "Propone crear un evento en el calendario por defecto (el usuario confirma).",
        {
            "title": STR,
            "start": STR,
            "end": NULLABLE_STR,
            "location": NULLABLE_STR,
            "description": NULLABLE_STR,
        },
        ["title", "start", "end", "location", "description"],
    ),
    _fn(
        "propose_email_event",
        "Propone agregar al calendario un evento detectado en un correo (de review_email_calendar_requests).",
        {
            "message_id": STR,
            "account": STR,
            "title": STR,
            "start": STR,
            "end": NULLABLE_STR,
            "location": NULLABLE_STR,
            "notes": NULLABLE_STR,
            "sender": NULLABLE_STR,
            "subject": NULLABLE_STR,
        },
        ["message_id", "account", "title", "start", "end", "location", "notes", "sender", "subject"],
    ),
]

PROPOSALS = {
    "propose_create_task": "create_task",
    "propose_complete_task": "complete_task",
    "propose_complete_item": "complete_item",
    "propose_calendar_event": "create_event",
    "propose_email_event": "add_email_event",
    "propose_vault_edit": "vault_edit",
}

STEP_LABELS = {
    "get_overview": "Revisé tu día",
    "get_events": "Consulté el calendario",
    "get_tasks": "Revisé Asana",
    "get_tracked_items": "Revisé seguimientos",
    "search_emails": "Busqué correos",
    "vault_overview": "Revisé el mapa de Obsidian",
    "search_vault": "Busqué en Obsidian",
    "read_vault_note": "Leí una nota",
    "get_person_context": "Revisé a una persona",
    "review_email_calendar_requests": "Revisé correos para el calendario",
}


class WorkspaceChat:
    """Tool-using chat over the workspace, backed by the server AI."""

    def __init__(self, workspace: WorkspaceService, ai_service: Any) -> None:
        self.ws = workspace
        self.ai = ai_service

    def _instructions(self) -> str:
        now = datetime.now(self.ws.tz)
        days = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        cfg = self.ws.config
        return INSTRUCTIONS.format(
            user=cfg.user_email or "el usuario",
            today=now.date().isoformat(),
            weekday=days[now.weekday()],
            tz=cfg.calendar_defaults.timezone,
            calendar=cfg.calendar_defaults.account_id or "el calendario principal",
        )

    async def respond(self, messages: list[dict[str, str]], model: str | None = None) -> dict[str, Any]:
        """Answer the last user message given the conversation so far.

        Args:
            messages: Conversation (user/assistant turns)
            model: Model chosen in the picker (default: ai.default_model, the cheap one)
        """
        client = self.ai._get_openai_client()
        model = model or self.ai.choose_model()
        conversation: list[Any] = [
            {"role": m["role"], "content": m["content"]}
            for m in messages[-MAX_HISTORY:]
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        steps: list[dict[str, str]] = []
        actions: list[dict[str, Any]] = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        options: dict[str, Any] = {}
        if self.ai._supports_reasoning(model):
            options["reasoning"] = {"effort": "low"}  # reasoning models reject temperature
        else:
            options["temperature"] = 0.3

        def result(reply: str) -> dict[str, Any]:
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
            return {"reply": reply, "steps": steps, "actions": actions, "model": model, "usage": usage}

        for _ in range(MAX_STEPS):
            response = await client.responses.create(
                model=model,
                instructions=self._instructions(),
                input=conversation,
                tools=TOOLS,
                store=False,
                **options,
            )
            used = getattr(response, "usage", None)
            usage["input_tokens"] += int(getattr(used, "input_tokens", 0) or 0)
            usage["output_tokens"] += int(getattr(used, "output_tokens", 0) or 0)
            calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
            if not calls:
                return result(response.output_text or "")

            for item in response.output:
                conversation.append(item.model_dump(exclude_none=True))
            for call in calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                output = await self._run_tool(call.name, args, steps, actions)
                conversation.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": output,
                })

        return result("No pude terminar en los pasos disponibles; intenta una pregunta más concreta.")

    async def _run_tool(
        self,
        name: str,
        args: dict[str, Any],
        steps: list[dict[str, str]],
        actions: list[dict[str, Any]],
    ) -> str:
        if name in PROPOSALS:
            action = {"type": PROPOSALS[name], **{k: v for k, v in args.items() if v is not None}}
            actions.append(action)
            return json.dumps({"status": "shown_to_user_for_confirmation"})

        detail = args.get("query") or args.get("name") or args.get("note") or args.get("folder") or ""
        steps.append({"tool": name, "label": STEP_LABELS.get(name, name), "detail": str(detail)})
        try:
            result = await self._read(name, args)
        except Exception as e:
            logger.warning("Chat tool failed", tool=name, error=str(e))
            steps[-1]["error"] = str(e)
            result = {"error": str(e)}
        text = json.dumps(result, ensure_ascii=False, default=str)
        return text[:MAX_TOOL_OUTPUT]

    async def _read(self, name: str, args: dict[str, Any]) -> Any:
        ws = self.ws
        if name == "get_overview":
            return await ws.overview()
        if name == "get_events":
            start = date.fromisoformat(args["start"]) if args.get("start") else None
            return await ws.events(days=max(1, min(int(args.get("days") or 1), 31)), start=start)
        if name == "get_tasks":
            return await ws.asana_tasks()
        if name == "get_tracked_items":
            return await ws.tracked_items()
        if name == "search_emails":
            return await ws.search_emails(args["query"], limit=max(1, min(int(args.get("limit") or 8), 20)))
        if name in ("vault_overview", "search_vault", "read_vault_note", "get_person_context"):
            from executive_copilot.services.vault_index import build_index

            index = build_index(ws.config)
            if name == "vault_overview":
                return index.overview(args.get("folder"))
            if name == "search_vault":
                from executive_copilot.services.vault_index import build_embedder

                return await index.hybrid_search(
                    args["query"], build_embedder(ws.config), folder=args.get("folder"), limit=8
                )
            if name == "read_vault_note":
                return index.read(args["note"], section=args.get("section"), max_chars=5000)
            return index.person_context(args["name"])
        if name == "review_email_calendar_requests":
            service = ws.email_calendar()
            try:
                return await service.review(days_back=max(1, min(int(args.get("days_back") or 7), 30)))
            finally:
                if service.db_manager is not None:
                    await service.db_manager.close()
        raise ValueError(f"Unknown tool {name}")
