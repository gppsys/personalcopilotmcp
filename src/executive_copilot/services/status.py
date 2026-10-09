"""Service status snapshot for the workspace "Estado" console.

Everything here is local and cheap (credential file times, SQLite counts, the
job run log, config) so the page can refresh every few seconds. Live checks
against provider APIs are a separate call (``probe``) because they cost real
requests and take seconds.
"""

from __future__ import annotations

import asyncio
import platform
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from executive_copilot.admin.credentials import OAUTH_FAMILIES, CredentialManager
from executive_copilot.services.accounts import TOKEN_SERVICES, AccountsService

PROCESS_STARTED = datetime.now(UTC)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Component health, worst last.
OK, IDLE, PENDING, WARN, DOWN = "ok", "idle", "pending", "warn", "down"
_RANK = {OK: 0, IDLE: 0, PENDING: 1, WARN: 2, DOWN: 3}

STALE_MEETINGS_DAYS = 4  # a work week with no new meeting note means the sync is stuck
OVERDUE_GRACE = timedelta(minutes=20)
DAY_NAMES = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]


def _now() -> datetime:
    return datetime.now(UTC)


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for no data."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered) + 0.5) - 1))
    return ordered[index]


def describe_cron(expr: str) -> str:
    """Short Spanish description of the cron patterns used in automation.yaml."""
    parts = expr.split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*":
        return expr
    if minute.startswith("*/") and hour == "*" and dow == "*":
        return f"cada {minute[2:]} min"
    if minute == "0" and hour.startswith("*/") and dow == "*":
        return f"cada {hour[2:]} h"
    if not (minute.isdigit() and all(h.isdigit() for h in hour.split(","))):
        return expr
    times = ", ".join(f"{int(h):02d}:{int(minute):02d}" for h in hour.split(","))
    if dow == "*":
        days = "diario"
    elif dow == "1-5":
        days = "L–V"
    elif dow.isdigit():
        days = DAY_NAMES[int(dow) % 7]
    else:
        days = dow
    return f"{days} {times}"


def git_revision(root: Path = _PROJECT_ROOT) -> str | None:
    """Short commit of the checked-out code, read straight from .git."""
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head[:7]
        ref = head[5:]
        ref_file = git / ref
        if ref_file.exists():
            return ref_file.read_text().strip()[:7]
        for line in (git / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0][:7]
    except OSError:
        return None
    return None


def worst(states: list[str]) -> str:
    return max(states, key=lambda s: _RANK.get(s, 0)) if states else OK


class StatusService:
    """Builds the status snapshot shown in the workspace."""

    def __init__(self, config: Any, credential_manager: CredentialManager) -> None:
        self.config = config
        self.cm = credential_manager
        self.tz = ZoneInfo(config.calendar_defaults.timezone)

    # --- snapshot -------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        now = _now()
        accounts = self._accounts()
        jobs = self._jobs(now)
        data = self._data(now)
        mcp = self._mcp(now)

        issues: list[dict[str, str]] = []
        for acc in accounts:
            if acc["health"] in (WARN, DOWN):
                issues.append({"level": acc["health"], "component": acc["name"], "message": acc["status_label"]})
        for job in jobs:
            if job["health"] in (WARN, DOWN):
                issues.append({"level": job["health"], "component": job["name"], "message": job["problem"] or ""})
        for item in data["items"]:
            if item["health"] in (WARN, DOWN):
                issues.append({"level": item["health"], "component": item["name"], "message": item["detail"]})

        levels = [i["level"] for i in issues]
        overall = "outage" if DOWN in levels else "degraded" if WARN in levels else "operational"
        real_jobs = [j for j in jobs if not j["placeholder"] and j["enabled"]]
        runs_24h = [r for j in real_jobs for r in j["history"] if _parse(r["started_at"]) and now - _parse(r["started_at"]) < timedelta(hours=24)]  # type: ignore[operator]
        ok_24h = sum(1 for r in runs_24h if r["status"] == "completed")

        return {
            "generated_at": now.isoformat(),
            "overall": overall,
            "issues": issues,
            "server": self._server(now),
            "kpis": {
                "accounts_ok": sum(1 for a in accounts if a["health"] == OK),
                "accounts_total": len(accounts),
                "jobs_active": len(real_jobs),
                "job_runs_24h": len(runs_24h),
                "job_success_24h": round(ok_24h / len(runs_24h) * 100, 1) if runs_24h else None,
                "mcp_calls_24h": mcp["calls_24h"],
                "mcp_p95_ms": mcp["p95_ms"],
                "latest_meeting": data["latest_meeting"],
            },
            "accounts": accounts,
            "jobs": jobs,
            "data": data["items"],
            "mcp": mcp,
            "events": self._events(jobs, accounts, mcp),
        }

    # --- accounts ---------------------------------------------------------------

    def _cred_time(self, ptype: str, pname: str, account: str) -> datetime | None:
        key = self.cm._credential_key(ptype, pname, account)
        return _mtime(self.cm.credentials_dir / f"{key}.enc")

    def _accounts(self) -> list[dict[str, Any]]:
        listing = AccountsService(self.config, self.cm).list_accounts()
        labels = {
            "connected": "Conectada",
            "no_refresh": "Sin refresh token: caducará",
            "not_connected": "No conectada",
            "no_client": "Falta la app OAuth",
        }
        health = {"connected": OK, "no_refresh": WARN, "not_connected": DOWN, "no_client": DOWN}
        out: list[dict[str, Any]] = []
        for acc in listing["oauth"]:
            times = [
                t for ptype, pname in OAUTH_FAMILIES[acc["family"]]
                if (t := self._cred_time(ptype, pname, acc["account_id"]))
            ]
            out.append({
                "key": f"{acc['family']}:{acc['account_id']}",
                "kind": acc["family"],
                "provider": acc["provider"],
                "name": f"{acc['provider']} · {acc['account_id']}",
                "account_id": acc["account_id"],
                "services": acc["services"],
                "used_by": acc["used_by"],
                "status": acc["status"],
                "status_label": labels.get(acc["status"], acc["status"]),
                "health": health.get(acc["status"], WARN),
                "token_refreshed_at": _iso(max(times)) if times else None,
            })
        for tok in listing["tokens"]:
            ptype, pname, _ = TOKEN_SERVICES[tok["kind"]]
            connected = tok["status"] == "connected"
            required = tok["used_by"] or (tok["kind"] == "github" and self.config.github_docs.enabled)
            if not connected and not required:
                continue  # optional integration nobody configured: not a problem
            out.append({
                "key": f"{tok['kind']}:{tok['account_id']}",
                "kind": tok["kind"],
                "provider": tok["label"],
                "name": tok["label"] if tok["account_id"] == "default" else f"{tok['label']} · {tok['account_id']}",
                "account_id": tok["account_id"],
                "services": [],
                "used_by": tok["used_by"],
                "status": tok["status"],
                "status_label": "Token configurado" if connected else "Sin token",
                "health": OK if connected else DOWN,
                "token_refreshed_at": _iso(self._cred_time(ptype, pname, tok["account_id"])),
            })
        return out

    async def probe(self) -> dict[str, dict[str, Any]]:
        """One cheap real API call per account, with latency."""
        service = AccountsService(self.config, self.cm)
        targets = [a for a in self._accounts() if a["status"] in ("connected", "no_refresh")]

        async def check(acc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            started = time.perf_counter()
            try:
                result = await asyncio.wait_for(service.test(acc["kind"], acc["account_id"]), timeout=20)
            except TimeoutError:
                result = {"ok": False, "detail": "Sin respuesta en 20 s"}
            return acc["key"], {
                **result,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "checked_at": _now().isoformat(),
            }

        return dict(await asyncio.gather(*(check(a) for a in targets)))

    # --- jobs -------------------------------------------------------------------

    def _jobs(self, now: datetime) -> list[dict[str, Any]]:
        from apscheduler.triggers.cron import CronTrigger

        from executive_copilot.automation.jobs import JOB_TYPES
        from executive_copilot.automation.run_log import get_run_log

        logged = get_run_log().per_job(history=30)
        out: list[dict[str, Any]] = []
        for cfg in self.config.automation_jobs:
            job_cls = JOB_TYPES.get(cfg.job_type)
            placeholder = bool(job_cls and job_cls.placeholder)
            info = logged.get(cfg.job_name, {"runs": [], "last_success_at": None, "last_failure": None})
            runs = info["runs"]
            last = runs[0] if runs else None

            next_run = None
            overdue = False
            try:
                trigger = CronTrigger.from_crontab(cfg.schedule, timezone=self.tz)
                next_run = trigger.get_next_fire_time(None, now)
                if last and cfg.enabled:
                    due = trigger.get_next_fire_time(None, _parse(last["started_at"]) + timedelta(seconds=1))  # type: ignore[operator]
                    overdue = bool(due and due > PROCESS_STARTED and due + OVERDUE_GRACE < now)
            except ValueError:
                pass

            health, problem = self._job_health(cfg.enabled, placeholder, last, overdue)
            durations = [r["duration_seconds"] for r in runs if r["duration_seconds"] is not None]
            completed = sum(1 for r in runs if r["status"] == "completed")
            out.append({
                "name": cfg.job_name,
                "type": cfg.job_type,
                "enabled": cfg.enabled,
                "placeholder": placeholder,
                "schedule": cfg.schedule,
                "schedule_label": describe_cron(cfg.schedule),
                "next_run": _iso(next_run),
                "last_run": last,
                "last_success_at": info["last_success_at"],
                "last_failure": info["last_failure"],
                "success_rate": round(completed / len(runs) * 100, 1) if runs else None,
                "avg_duration": round(sum(durations) / len(durations), 2) if durations else None,
                "history": [
                    {
                        "status": r["status"],
                        "started_at": r["started_at"],
                        "duration_seconds": r["duration_seconds"],
                        "items": r["items_processed"],
                        "warn": bool(_run_errors(r)),
                    }
                    for r in reversed(runs)
                ],
                "health": health,
                "problem": problem,
            })
        out.sort(key=lambda j: (j["placeholder"] or not j["enabled"], -_RANK[j["health"]], j["name"]))
        return out

    @staticmethod
    def _job_health(
        enabled: bool, placeholder: bool, last: dict[str, Any] | None, overdue: bool
    ) -> tuple[str, str | None]:
        if not enabled or placeholder:
            return IDLE, None
        if last is None:
            return PENDING, "Sin ejecuciones registradas todavía"
        if last["status"] == "failed":
            return DOWN, last["error_message"] or "Falló la última ejecución"
        errors = _run_errors(last)
        if errors:
            return WARN, f"Completó con errores: {errors[0]}"
        if overdue:
            return WARN, "No corrió en su horario programado"
        return OK, None

    # --- data -------------------------------------------------------------------

    def _data(self, now: datetime) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        latest_meeting: str | None = None
        obs = self.config.obsidian

        if obs.enabled and obs.vault_path:
            from executive_copilot.services.obsidian_notes import MeetingNotesVault

            vault = MeetingNotesVault(Path(obs.vault_path), obs.meetings_folder, obs.people_folder)
            error = vault.check()
            if error:
                items.append({"key": "vault", "name": "Bóveda Obsidian", "health": DOWN, "detail": error, "metrics": {}})
            else:
                latest = vault.latest_date()
                latest_meeting = latest.isoformat() if latest else None
                age = (now.astimezone(self.tz).date() - latest).days if latest else None
                stale = age is not None and age > STALE_MEETINGS_DAYS
                items.append({
                    "key": "vault",
                    "name": "Bóveda Obsidian",
                    "health": WARN if stale else OK,
                    "detail": (
                        f"Sin reuniones nuevas desde hace {age} días" if stale
                        else f"Última reunión: {latest_meeting}" if latest_meeting else "Sin reuniones"
                    ),
                    "updated_at": _iso(_mtime(vault.meetings_dir)),
                    "metrics": {
                        "Notas de reuniones": len(vault.note_names()),
                        "Personas": len(vault.people_names()),
                        "Última reunión": latest_meeting,
                    },
                })

        index_path = Path(obs.index_path)
        if index_path.exists():
            counts: dict[str, Any] = {}
            try:
                with sqlite3.connect(f"file:{index_path}?mode=ro", uri=True, timeout=5) as conn:
                    for label, table in (("Archivos", "files"), ("Fragmentos", "chunks"), ("Embeddings", "vectors")):
                        counts[label] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                health, detail = OK, f"{counts['Archivos']} notas indexadas"
            except sqlite3.Error as e:
                health, detail = WARN, f"No se pudo leer el índice: {e}"
            items.append({
                "key": "index",
                "name": "Índice de búsqueda",
                "health": health,
                "detail": detail,
                "updated_at": _iso(_mtime(index_path)),
                "metrics": counts | {"Tamaño": _size(index_path)},
            })

        if self.config.github_docs.enabled:
            items.append(self._github_docs(now))

        for key, label, path in (
            ("db", "Base de datos", Path(self.config.database.name)),
            ("activity", "Registro de actividad", _PROJECT_ROOT / "activity.db"),
        ):
            if path.exists():
                items.append({
                    "key": key,
                    "name": label,
                    "health": OK,
                    "detail": path.name,
                    "updated_at": _iso(_mtime(path)),
                    "metrics": {"Tamaño": _size(path)},
                })
        return {"items": items, "latest_meeting": latest_meeting}

    def _github_docs(self, now: datetime) -> dict[str, Any]:
        from executive_copilot.services.github_docs import GitHubDocsSync

        info = GitHubDocsSync(self.config, self.cm).status()
        last = _parse(info["last_sync"])
        if last is None:
            health, detail = PENDING, "Aún no se sincroniza"
        elif info["last_error"]:
            health, detail = WARN, f"Último sync con errores: {info['last_error'][:160]}"
        elif now - last > timedelta(days=4):
            health, detail = WARN, f"Sin sincronizar desde hace {(now - last).days} días"
        else:
            health, detail = OK, f"{', '.join(self.config.github_docs.orgs)} · {info['repos']} repositorios"
        return {
            "key": "github",
            "name": "Docs de GitHub",
            "health": health,
            "detail": detail,
            "updated_at": info["last_sync"],
            "metrics": {"Repositorios": info["repos"], "Documentos": info["files"], "Resúmenes IA": info["summaries"]},
        }

    # --- MCP --------------------------------------------------------------------

    def _mcp(self, now: datetime) -> dict[str, Any]:
        from executive_copilot.mcp.activity import get_tracker

        try:
            rows = get_tracker().get_recent_activities(limit=500)
        except sqlite3.Error:
            rows = []
        day_ago = now - timedelta(hours=24)
        recent = [r for r in rows if (ts := _parse(r["timestamp"])) and ts >= day_ago]
        durations = [r["duration_ms"] for r in recent if r["duration_ms"] is not None]
        buckets = [0] * 24
        errors_by_hour = [0] * 24
        tools: dict[str, int] = {}
        for r in recent:
            hours_ago = int((now - _parse(r["timestamp"])).total_seconds() // 3600)  # type: ignore[operator]
            slot = 23 - min(23, hours_ago)
            buckets[slot] += 1
            if r["status"] == "error":
                errors_by_hour[slot] += 1
            tools[r["tool_name"]] = tools.get(r["tool_name"], 0) + 1
        errors = [r for r in rows if r["status"] == "error"][:5]
        return {
            "calls_24h": len(recent),
            "errors_24h": sum(errors_by_hour),
            "p50_ms": round(median(durations)) if durations else None,
            "p95_ms": round(p) if (p := percentile(durations, 95)) is not None else None,
            "last_call_at": rows[0]["timestamp"] if rows else None,
            "hourly": buckets,
            "hourly_errors": errors_by_hour,
            "top_tools": sorted(tools.items(), key=lambda kv: -kv[1])[:6],
            "recent": [
                {k: r[k] for k in ("timestamp", "tool_name", "status", "duration_ms", "error_message")}
                for r in rows[:12]
            ],
            "recent_errors": [
                {k: r[k] for k in ("timestamp", "tool_name", "error_message")} for r in errors
            ],
        }

    # --- server & events --------------------------------------------------------

    def _server(self, now: datetime) -> dict[str, Any]:
        return {
            "started_at": PROCESS_STARTED.isoformat(),
            "uptime_seconds": int((now - PROCESS_STARTED).total_seconds()),
            "revision": git_revision(),
            "python": platform.python_version(),
            "timezone": str(self.tz),
            "ai_model": getattr(self.config.ai, "default_model", None),
        }

    def _events(
        self, jobs: list[dict[str, Any]], accounts: list[dict[str, Any]], mcp: dict[str, Any]
    ) -> list[dict[str, Any]]:
        from executive_copilot.automation.run_log import get_run_log

        real = {j["name"] for j in jobs if not j["placeholder"]}
        events: list[dict[str, Any]] = []
        for run in get_run_log().recent(limit=120):
            if run["job_name"] not in real:
                continue
            errors = _run_errors(run)
            if run["status"] == "failed":
                level, message = "error", run["error_message"] or "falló"
            elif errors:
                level, message = "warn", f"{len(errors)} error(es): {errors[0]}"
            else:
                level = "ok"
                message = f"{run['items_processed']} elementos · {_fmt_seconds(run['duration_seconds'])}"
            events.append({"at": run["completed_at"] or run["started_at"], "level": level, "source": run["job_name"], "message": message})
        for acc in accounts:
            if acc["token_refreshed_at"]:
                events.append({"at": acc["token_refreshed_at"], "level": "info", "source": acc["name"], "message": "credenciales renovadas"})
        for call in mcp["recent"]:
            level = "error" if call["status"] == "error" else "info"
            message = call["error_message"] or (f"{call['duration_ms']:.0f} ms" if call["duration_ms"] is not None else call["status"])
            events.append({"at": call["timestamp"], "level": level, "source": f"mcp · {call['tool_name']}", "message": message})
        events.sort(key=lambda e: _parse(e["at"]) or PROCESS_STARTED, reverse=True)
        return events[:40]


def _run_errors(run: dict[str, Any]) -> list[str]:
    errors = (run.get("details") or {}).get("errors") or []
    out = []
    for e in errors:
        if isinstance(e, dict):
            e = e.get("error") or e.get("message") or str(e)
        out.append(str(e).split("\n")[0][:200])
    return out


def _fmt_seconds(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.1f} s"


def _size(path: Path) -> str:
    size = float(path.stat().st_size)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
