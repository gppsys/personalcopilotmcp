"""Single-user workspace authentication and knowledge authorization."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

COOKIE = "copilot_session"
SESSION_SECONDS = 8 * 3600
# Failed logins per client within the window before further attempts are refused.
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60
LOGIN = """<!doctype html><html lang="es"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Executive Copilot · Acceso</title><body style="font:16px system-ui;max-width:420px;margin:15vh auto;padding:24px">
<h1>Executive Copilot</h1><p>Introduce tu clave de acceso para abrir el espacio de trabajo.</p>
<form id="login"><input id="key" type="password" autocomplete="current-password" required aria-label="Clave de acceso">
<button>Entrar</button></form><p id="error" role="alert"></p><script>
document.getElementById('login').onsubmit = async event => { event.preventDefault();
const result = await fetch('/api/session', {method:'POST', headers:{'Content-Type':'application/json'},
body:JSON.stringify({token:document.getElementById('key').value})});
if(result.ok) location.href='/' + (location.hash || '#today'); else document.getElementById('error').textContent='Acceso rechazado'; };
</script></body></html>"""


def authorize_knowledge(access_token: str | None) -> None:
    expected = os.environ.get("COPILOT_WORKSPACE_ACCESS_TOKEN", "")
    if len(expected) < 32:
        raise ValueError("Configure COPILOT_WORKSPACE_ACCESS_TOKEN with at least 32 characters")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not hmac.compare_digest(access_token.encode(), expected.encode())
    ):
        raise PermissionError("Knowledge access denied")


class Sessions:
    """In-memory browser sessions: the cookie carries a random id, never the access key.

    Sessions end on logout, on expiry, when the key is rotated, and on restart.
    """

    def __init__(self) -> None:
        self._active: dict[str, tuple[float, str]] = {}
        self._failures: dict[str, list[float]] = {}

    @staticmethod
    def _key_id() -> str:
        expected = os.environ.get("COPILOT_WORKSPACE_ACCESS_TOKEN", "")
        return hashlib.sha256(expected.encode()).hexdigest()

    def create(self) -> str:
        now = time.monotonic()
        self._active = {k: v for k, v in self._active.items() if v[0] > now}
        session_id = secrets.token_urlsafe(32)
        self._active[session_id] = (now + SESSION_SECONDS, self._key_id())
        return session_id

    def valid(self, session_id: str) -> bool:
        entry = self._active.get(session_id)
        return bool(entry and entry[0] > time.monotonic() and entry[1] == self._key_id())

    def end(self, session_id: str) -> None:
        self._active.pop(session_id, None)

    def throttled(self, client: str) -> bool:
        cutoff = time.monotonic() - FAILURE_WINDOW
        recent = [t for t in self._failures.get(client, []) if t > cutoff]
        self._failures[client] = recent
        return len(recent) >= MAX_FAILURES

    def failed(self, client: str) -> None:
        self._failures.setdefault(client, []).append(time.monotonic())


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
        raise HTTPException(403, "Cross-origin write denied")


class WorkspaceAuthentication(BaseHTTPMiddleware):
    """Preserve local legacy behavior until a workspace key is configured."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path == "/api/health":
            return await call_next(request)
        if request.url.path == "/login":
            return HTMLResponse(LOGIN, headers={"Cache-Control": "no-store"})
        expected = os.environ.get("COPILOT_WORKSPACE_ACCESS_TOKEN", "")
        required = getattr(request.app.state, "knowledge_auth_required", lambda: False)()
        if (required and not expected) or (expected and len(expected) < 32):
            return JSONResponse(
                {"detail": "Workspace authentication is not configured"}, status_code=503
            )
        if not expected:
            if (
                request.url.path.startswith("/api/ws/knowledge")
                or request.url.path == "/api/session"
            ):
                return JSONResponse(
                    {"detail": "Workspace authentication is not configured"}, status_code=503
                )
            return await call_next(request)
        if request.url.path == "/api/session":
            return await call_next(request)
        sessions: Sessions = request.app.state.workspace_sessions
        authorization = request.headers.get("authorization", "")
        authenticated = sessions.valid(request.cookies.get(COOKIE, ""))
        if not authenticated and authorization.startswith("Bearer "):
            try:
                authorize_knowledge(authorization[7:])
                authenticated = True
            except (PermissionError, ValueError):
                pass
        if not authenticated:
            if request.url.path.startswith(("/api/", "/auth/")):
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            try:
                check_origin(request)
            except HTTPException:
                return JSONResponse({"detail": "Cross-origin write denied"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response


def install_workspace_auth(app: FastAPI, config_dir: Any = None) -> None:
    if config_dir is not None:
        from executive_copilot.config import load_config

        cached: dict[str, Any] = {"at": float("-inf"), "enabled": False}

        def required() -> bool:
            # Every request asks; re-reading all YAML files each time is wasteful.
            if time.monotonic() - cached["at"] > 5:
                cached.update(
                    at=time.monotonic(), enabled=load_config(config_dir).knowledge.enabled
                )
            return bool(cached["enabled"])

        app.state.knowledge_auth_required = required
    app.state.workspace_sessions = Sessions()
    app.add_middleware(WorkspaceAuthentication)

    @app.post("/api/session")
    async def login(request: Request) -> Response:
        check_origin(request)
        sessions: Sessions = request.app.state.workspace_sessions
        client = request.client.host if request.client else "unknown"
        if sessions.throttled(client):
            raise HTTPException(429, "Too many failed attempts; try again later")
        try:
            payload = await request.json()
            authorize_knowledge(payload.get("token"))
        except (ValueError, PermissionError, AttributeError, TypeError):
            sessions.failed(client)
            raise HTTPException(401, "Access denied") from None
        response = JSONResponse({"authenticated": True}, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            COOKIE,
            sessions.create(),
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=SESSION_SECONDS,
        )
        return response

    @app.delete("/api/session")
    async def logout(request: Request) -> Response:
        check_origin(request)
        request.app.state.workspace_sessions.end(request.cookies.get(COOKIE, ""))
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE)
        return response
