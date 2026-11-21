"""Operations dashboard: uptime and health of production services, read from AWS CloudWatch.

On demand and read-only. Every refresh first validates the AWS CLI profile (STS); a
profile that cannot be used is reported with the command that fixes it instead of
silently serving old numbers. A successful reading is stored in ``activity.db`` so
reloading the page does not call AWS again.

What "uptime" means here, per service (one Application Load Balancer):

* by hosts: share of 5-minute intervals in which every target group had a healthy target;
* by requests: share of requests not answered with a 5xx (by the app or the balancer).

Hours are flagged from thresholds in ``ops:`` (error rate, p95 latency, no healthy host,
database CPU) and consecutive flagged hours are merged into incidents, because alarms
may not cover the service at all. Merged pull requests and production deploys are
attached to the incident they precede.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from executive_copilot.logging import get_logger
from executive_copilot.providers.cloud.aws import (
    Alarm,
    AwsCredentialsError,
    AwsMonitoringProvider,
    MetricQuery,
)
from executive_copilot.providers.code.github import GitHubProvider

logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB_PATH = _PROJECT_ROOT / "activity.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_snapshots (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    fetched_at TEXT NOT NULL,
    data TEXT NOT NULL
);
"""

LEVELS = ("ok", "warn", "bad")
MIN_ERRORS = 50  # an hour needs this many 5xx before its error rate counts
MIN_REQUESTS = 100  # ... and this many requests before its latency counts
CHANGE_LOOKBACK = timedelta(hours=6)  # changes this long before an incident are shown with it
STUCK_ALARM_DAYS = 3
Series = dict[str, list[tuple[datetime, float]]]


class OpsStore:
    """Last reading, kept next to the job run log."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.executescript(SCHEMA)
        return conn

    def load(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM ops_snapshots WHERE id = 1").fetchone()
        if row is None:
            return None
        try:
            data: dict[str, Any] = json.loads(row[0])
            return data
        except json.JSONDecodeError:
            return None

    def save(self, snapshot: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ops_snapshots (id, fetched_at, data) VALUES (1, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET fetched_at = excluded.fetched_at, data = excluded.data",
                (snapshot["fetched_at"], json.dumps(snapshot, ensure_ascii=False, default=str)),
            )


# --- pure derivations ---------------------------------------------------------


def worst(levels: list[str]) -> str:
    return max(levels, key=LEVELS.index, default="ok")


def _hourly(points: list[tuple[datetime, float]], start: datetime, count: int) -> list[float | None]:
    """Align datapoints to hour slots (``None`` where CloudWatch has no data)."""
    out: list[float | None] = [None] * count
    for ts, value in points:
        i = int((ts - start).total_seconds() // 3600)
        if 0 <= i < count:
            out[i] = value
    return out


def down_minutes_by_hour(
    host_series: list[list[tuple[datetime, float]]], start: datetime, count: int
) -> tuple[list[int], int]:
    """Minutes without a healthy host per hour, and how many 5-minute intervals were observed.

    An interval is down when any target group reports no healthy host in it.
    """
    lowest: dict[datetime, float] = {}
    for points in host_series:
        for ts, value in points:
            lowest[ts] = min(value, lowest.get(ts, value))
    down = [0] * count
    for ts, value in lowest.items():
        i = int((ts - start).total_seconds() // 3600)
        if value < 1 and 0 <= i < count:
            down[i] += 5
    return down, len(lowest)


def build_hours(series: Series, down: list[int], start: datetime, count: int, settings: Any) -> list[dict[str, Any]]:
    """One record per hour with traffic, errors, latency and why it is flagged (if it is)."""

    def col(key: str) -> list[float | None]:
        return _hourly(series.get(key, []), start, count)

    req, t5, e5 = col("req"), col("t5"), col("e5")
    e502, e503, e504 = col("e502"), col("e503"), col("e504")
    p95, p99, db_cpu = col("p95"), col("p99"), col("dbcpu")
    hours = []
    for i in range(count):
        requests = int(req[i] or 0)
        app_errors, lb_errors = int(t5[i] or 0), int(e5[i] or 0)
        errors = app_errors + lb_errors
        rate = round(100 * errors / requests, 3) if requests else 0.0
        p95_ms = round((p95[i] or 0) * 1000) if p95[i] is not None else None
        reasons: dict[str, str] = {}
        if down[i] >= 10:
            reasons["down"] = "bad"
        elif down[i] > 0:
            reasons["down"] = "warn"
        if errors >= MIN_ERRORS and rate >= settings.error_rate_warn:
            reasons["errors"] = "bad" if rate >= settings.error_rate_bad else "warn"
        if p95_ms is not None and requests >= MIN_REQUESTS and p95_ms >= settings.latency_warn_ms:
            reasons["latency"] = "bad" if p95_ms >= settings.latency_bad_ms else "warn"
        cpu = db_cpu[i]
        if cpu is not None and cpu >= settings.db_cpu_warn:
            reasons["db_cpu"] = "warn"
        hours.append({
            "t": (start + timedelta(hours=i)).isoformat(),
            "requests": requests,
            "app_5xx": app_errors,
            "lb_5xx": lb_errors,
            "lb_502": int(e502[i] or 0),
            "lb_503": int(e503[i] or 0),
            "lb_504": int(e504[i] or 0),
            "error_rate": rate,
            "p95_ms": p95_ms,
            "p99_ms": round((p99[i] or 0) * 1000) if p99[i] is not None else None,
            "down_minutes": down[i],
            "db_cpu": round(cpu, 1) if cpu is not None else None,
            "level": worst(list(reasons.values())),
            "reasons": sorted(reasons),
        })
    return hours


def build_days(hours: list[dict[str, Any]], tz: tzinfo, settings: Any) -> list[dict[str, Any]]:
    """Roll hours up to local calendar days (the 14-day strip)."""
    days: dict[str, dict[str, Any]] = {}
    for h in hours:
        key = datetime.fromisoformat(h["t"]).astimezone(tz).date().isoformat()
        day = days.setdefault(key, {
            "date": key, "hours": 0, "requests": 0, "errors": 0, "app_5xx": 0, "lb_5xx": 0,
            "down_minutes": 0, "p95_peak_ms": None, "flagged_hours": 0, "bad_hours": 0,
        })
        day["hours"] += 1
        day["requests"] += h["requests"]
        day["app_5xx"] += h["app_5xx"]
        day["lb_5xx"] += h["lb_5xx"]
        day["errors"] += h["app_5xx"] + h["lb_5xx"]
        day["down_minutes"] += h["down_minutes"]
        if h["p95_ms"] is not None and h["requests"] >= MIN_REQUESTS:
            day["p95_peak_ms"] = max(day["p95_peak_ms"] or 0, h["p95_ms"])
        day["flagged_hours"] += h["level"] != "ok"
        day["bad_hours"] += h["level"] == "bad"
    out = []
    for day in days.values():
        rate = round(100 * day["errors"] / day["requests"], 3) if day["requests"] else 0.0
        day["error_rate"] = rate
        day["availability"] = round(100 - rate, 3)
        if day["down_minutes"] >= 15 or rate >= settings.error_rate_bad or day["bad_hours"]:
            day["level"] = "bad"
        elif day["down_minutes"] > 0 or rate >= settings.error_rate_warn or day["flagged_hours"]:
            day["level"] = "warn"
        else:
            day["level"] = "ok"
        out.append(day)
    return out


def detect_incidents(hours: list[dict[str, Any]], changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge flagged hours (one clean hour of gap allowed) into incidents, newest first."""
    groups: list[list[int]] = []
    for i, h in enumerate(hours):
        if h["level"] == "ok":
            continue
        if groups and i - groups[-1][-1] <= 2:
            groups[-1].append(i)
        else:
            groups.append([i])
    incidents = []
    for group in groups:
        rows = [hours[i] for i in group]
        span = hours[group[0] : group[-1] + 1]
        begin = datetime.fromisoformat(rows[0]["t"])
        end = datetime.fromisoformat(rows[-1]["t"]) + timedelta(hours=1)
        nearby = [
            c for c in changes if begin - CHANGE_LOOKBACK <= datetime.fromisoformat(c["at"]) < end
        ]
        incidents.append({
            "start": begin.isoformat(),
            "end": end.isoformat(),
            "hours": len(span),
            "level": worst([r["level"] for r in rows]),
            "reasons": sorted({reason for r in rows for reason in r["reasons"]}),
            "errors": sum(r["app_5xx"] + r["lb_5xx"] for r in span),
            "peak_error_rate": max(r["error_rate"] for r in rows),
            "peak_p95_ms": max((r["p95_ms"] or 0) for r in rows) or None,
            "peak_db_cpu": max((r["db_cpu"] or 0) for r in rows) or None,
            "down_minutes": sum(r["down_minutes"] for r in span),
            "changes": nearby,
        })
    return incidents[::-1]


def summarize(
    hours: list[dict[str, Any]], intervals: int, incidents: list[dict[str, Any]], totals: dict[str, Any]
) -> dict[str, Any]:
    requests = sum(h["requests"] for h in hours)
    app_errors, lb_errors = sum(h["app_5xx"] for h in hours), sum(h["lb_5xx"] for h in hours)
    down = sum(h["down_minutes"] for h in hours)
    return {
        "requests": requests,
        "errors": app_errors + lb_errors,
        "app_5xx": app_errors,
        "lb_5xx": lb_errors,
        "availability": round(100 * (1 - (app_errors + lb_errors) / requests), 3) if requests else None,
        "uptime_hosts": round(100 * (1 - (down / 5) / intervals), 3) if intervals else None,
        "down_minutes": down,
        "incidents": len(incidents),
        "bad_incidents": sum(i["level"] == "bad" for i in incidents),
        "p95_ms": totals.get("p95_ms"),
        "previous": totals.get("previous"),
    }


def related_alarms(alarms: list[Alarm], resource_ids: set[str]) -> list[Alarm]:
    """Alarms whose dimensions point at one of the service's resources."""
    return [a for a in alarms if resource_ids.intersection(a.dimensions.values())]


def coverage(
    service: Any,
    topology: dict[str, Any],
    alarms: list[Alarm],
    health_checks: list[dict[str, Any]] | None,
    now: datetime,
) -> list[dict[str, str]]:
    """What watches this service and what does not: monitoring gaps and single points of failure."""
    items: list[dict[str, str]] = []

    def add(level: str, title: str, detail: str) -> None:
        items.append({"level": level, "title": title, "detail": detail})

    lb = topology["load_balancer"]
    if health_checks is not None:
        domain = (service.domain or "").lower()
        matches = [c for c in health_checks if domain and (c.get("domain") or "").lower() == domain]
        if matches:
            add("ok", "Chequeo externo", f"Route 53 vigila {domain}")
        else:
            add(
                "gap", "Sin chequeo externo",
                f"Ningún health check de Route 53 apunta a {domain or 'este servicio'}: el uptime solo se mide"
                " desde dentro de AWS y no ve caídas de DNS o de red.",
            )

    on_lb = [a for a in alarms if a.dimensions.get("LoadBalancer") == lb["dimension"]]
    wanted = (
        ("5xx", {"HTTPCode_Target_5XX_Count", "HTTPCode_ELB_5XX_Count"}, "errores 5xx"),
        ("hosts", {"UnHealthyHostCount", "HealthyHostCount"}, "host caído"),
        ("latency", {"TargetResponseTime"}, "latencia"),
    )
    missing = [label for _, metrics, label in wanted if not any(a.metric in metrics for a in on_lb)]
    if missing:
        add(
            "gap", f"Balanceador sin alarmas de {', '.join(missing)}",
            f"{lb['name']} no avisa cuando esto ocurre; hoy solo se nota mirando este tablero.",
        )
    else:
        add("ok", "Alarmas del balanceador", f"{lb['name']} tiene alarmas de 5xx, host caído y latencia")

    database = topology.get("database")
    if database:
        on_db = {a.metric for a in alarms if a.dimensions.get("DBInstanceIdentifier") == database["id"]}
        absent = [label for metric, label in (("CPUUtilization", "CPU"), ("FreeStorageSpace", "espacio en disco")) if metric not in on_db]
        if absent:
            add("gap", f"Base de datos sin alarma de {' ni '.join(absent)}", f"{database['id']} puede saturarse sin aviso.")
        if not database["multi_az"]:
            add("risk", "Base de datos en una sola zona", f"{database['id']} no es Multi-AZ: una falla de zona la deja fuera.")

    for alarm in alarms:
        if alarm.state == "ALARM" and alarm.updated_at and now - alarm.updated_at >= timedelta(days=STUCK_ALARM_DAYS):
            add(
                "gap", "Alarma disparada permanentemente",
                f"{alarm.name} lleva {(now - alarm.updated_at).days} días en ALARM (umbral {alarm.threshold:g})."
                " Una alarma siempre encendida ya no avisa de nada: revisa el umbral.",
            )
        if alarm.actions == 0:
            add("gap", "Alarma que no notifica", f"{alarm.name} no tiene ninguna acción (SNS) configurada.")

    targets = [t for tg in lb["target_groups"] for t in tg["targets"]]
    if len({t["id"] for t in targets}) == 1:
        add("risk", "Un solo servidor", "Todo el tráfico va a una única instancia: cualquier reinicio o falla es una caída.")
    unhealthy = [t["id"] for t in targets if t["state"] not in ("healthy", "unused")]
    if unhealthy:
        add("gap", "Destinos no sanos ahora", ", ".join(unhealthy))
    return sorted(items, key=lambda item: ("gap", "risk", "ok").index(item["level"]))


def metric_card(key: str, label: str, unit: str, values: list[float | None], scale: float = 1.0) -> dict[str, Any]:
    """An hourly series with its latest, peak and lowest value."""
    scaled = [round(v * scale, 2) if v is not None else None for v in values]
    present = [v for v in scaled if v is not None]
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "latest": present[-1] if present else None,
        "max": max(present) if present else None,
        "min": min(present) if present else None,
        "series": scaled,
    }


# --- service ------------------------------------------------------------------


class OpsService:
    """Reads AWS and builds the Operación snapshot."""

    def __init__(
        self,
        config: Any,
        credential_manager: Any,
        store: OpsStore | None = None,
        tz: tzinfo | None = None,
    ) -> None:
        self.config = config
        self.settings = config.ops
        self.credential_manager = credential_manager
        self.store = store or OpsStore()
        self.tz = tz or ZoneInfo(os.environ.get("TZ") or "America/Mexico_City")
        self.aws = AwsMonitoringProvider(self.settings.profile, self.settings.region)

    # --- credentials ----------------------------------------------------------

    async def check_credentials(self) -> dict[str, Any]:
        """Validate the AWS profile now. Never raises: the UI shows what to run."""
        result: dict[str, Any] = {
            "ok": False,
            "profile": self.settings.profile,
            "region": self.settings.region,
            "checked_at": datetime.now(UTC).isoformat(),
        }
        try:
            identity = await self.aws.identity()
        except AwsCredentialsError as e:
            return {**result, "error": str(e), "hint": e.hint}
        except Exception as e:  # network down, AWS unreachable
            logger.warning("AWS identity check failed", error=str(e))
            return {**result, "error": f"No se pudo contactar a AWS: {e}", "hint": "Revisa la conexión y reintenta"}
        return {**result, "ok": True, "account": identity["account"], "identity": identity["name"]}

    # --- entry points -----------------------------------------------------------

    def _base(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.enabled,
            "configured": bool(self.settings.services),
            "profile": self.settings.profile,
            "region": self.settings.region,
            "days": self.settings.days,
            "cache_minutes": self.settings.cache_minutes,
            "thresholds": {
                "error_rate_warn": self.settings.error_rate_warn,
                "error_rate_bad": self.settings.error_rate_bad,
                "latency_warn_ms": self.settings.latency_warn_ms,
                "latency_bad_ms": self.settings.latency_bad_ms,
            },
        }

    def _with_snapshot(self, credentials: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.store.load()
        stale = True
        if snapshot:
            age = datetime.now(UTC) - datetime.fromisoformat(snapshot["fetched_at"])
            stale = age > timedelta(minutes=self.settings.cache_minutes)
        return {**self._base(), "credentials": credentials, "snapshot": snapshot, "stale": stale}

    async def overview(self) -> dict[str, Any]:
        """Credentials as they are now plus the last stored reading (no CloudWatch calls)."""
        if not self.settings.enabled or not self.settings.services:
            return {**self._base(), "credentials": None, "snapshot": None, "stale": False}
        return self._with_snapshot(await self.check_credentials())

    async def refresh(self) -> dict[str, Any]:
        """Validate credentials, read CloudWatch and store the new snapshot."""
        if not self.settings.enabled or not self.settings.services:
            return {**self._base(), "credentials": None, "snapshot": None, "stale": False}
        credentials = await self.check_credentials()
        if not credentials["ok"]:
            return self._with_snapshot(credentials)

        end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=self.settings.days)
        errors: list[dict[str, str]] = []
        alarms, health_checks = await self._shared(errors)
        services = []
        for service in self.settings.services:
            try:
                services.append(await self._read_service(service, start, end, alarms, health_checks, errors))
            except AwsCredentialsError as e:
                return self._with_snapshot({**credentials, "ok": False, "error": str(e), "hint": e.hint})
            except Exception as e:
                logger.exception("Ops service read failed", service=service.name)
                services.append({"name": service.name, "error": _aws_error(e)})
        if all("error" in s for s in services):
            # Nothing could be read: keep the previous snapshot and say why.
            result = self._with_snapshot(credentials)
            result["error"] = "; ".join(f"{s['name']}: {s['error']}" for s in services)
            return result
        self.store.save({
            "fetched_at": datetime.now(UTC).isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": self.settings.days,
            "account": credentials.get("account"),
            "services": services,
            "errors": errors,
        })
        return self._with_snapshot(credentials)

    # --- reading ----------------------------------------------------------------

    async def _shared(self, errors: list[dict[str, str]]) -> tuple[list[Alarm], list[dict[str, Any]] | None]:
        """Account-wide lookups used by every service; a missing permission is reported, not fatal."""
        alarms: list[Alarm] = []
        health_checks: list[dict[str, Any]] | None = None
        try:
            alarms = await self.aws.alarms()
        except AwsCredentialsError:
            raise
        except Exception as e:
            errors.append({"source": "Alarmas de CloudWatch", "error": _aws_error(e)})
        try:
            health_checks = await self.aws.health_checks()
        except AwsCredentialsError:
            raise
        except Exception as e:
            errors.append({"source": "Health checks de Route 53", "error": _aws_error(e)})
        return alarms, health_checks

    async def _read_service(
        self,
        service: Any,
        start: datetime,
        end: datetime,
        alarms: list[Alarm],
        health_checks: list[dict[str, Any]] | None,
        errors: list[dict[str, str]],
    ) -> dict[str, Any]:
        count = int((end - start).total_seconds() // 3600)
        lb = await self.aws.load_balancer(service.load_balancer)
        groups = [tg for tg in lb["target_groups"] if tg["targets"]]
        instance_ids = sorted({
            t["id"] for tg in groups if tg["target_type"] == "instance" for t in tg["targets"]
        })
        topology: dict[str, Any] = {"load_balancer": lb, "instances": await self.aws.instances(instance_ids)}
        if service.database:
            topology["database"] = await self.aws.database(service.database)
        if service.cache:
            topology["cache"] = await self.aws.cache(service.cache)

        lb_dim = {"LoadBalancer": lb["dimension"]}
        aws_lb = "AWS/ApplicationELB"
        queries = [
            MetricQuery("req", aws_lb, "RequestCount", lb_dim, "Sum"),
            MetricQuery("t5", aws_lb, "HTTPCode_Target_5XX_Count", lb_dim, "Sum"),
            MetricQuery("e5", aws_lb, "HTTPCode_ELB_5XX_Count", lb_dim, "Sum"),
            MetricQuery("e502", aws_lb, "HTTPCode_ELB_502_Count", lb_dim, "Sum"),
            MetricQuery("e503", aws_lb, "HTTPCode_ELB_503_Count", lb_dim, "Sum"),
            MetricQuery("e504", aws_lb, "HTTPCode_ELB_504_Count", lb_dim, "Sum"),
            MetricQuery("p95", aws_lb, "TargetResponseTime", lb_dim, "p95"),
            MetricQuery("p99", aws_lb, "TargetResponseTime", lb_dim, "p99"),
        ]
        for i, tg in enumerate(groups):
            queries.append(MetricQuery(
                f"hh{i}", aws_lb, "HealthyHostCount", {**lb_dim, "TargetGroup": tg["dimension"]}, "Minimum", 300
            ))
        for i, instance_id in enumerate(instance_ids):
            dims = {"InstanceId": instance_id}
            queries.append(MetricQuery(f"ec2cpu{i}", "AWS/EC2", "CPUUtilization", dims, "Maximum"))
        if service.database:
            dims = {"DBInstanceIdentifier": service.database}
            queries += [
                MetricQuery("dbcpu", "AWS/RDS", "CPUUtilization", dims, "Average"),
                MetricQuery("dbcpumax", "AWS/RDS", "CPUUtilization", dims, "Maximum"),
                MetricQuery("dbconn", "AWS/RDS", "DatabaseConnections", dims, "Maximum"),
                MetricQuery("dbfree", "AWS/RDS", "FreeStorageSpace", dims, "Minimum"),
                MetricQuery("dbmem", "AWS/RDS", "FreeableMemory", dims, "Minimum"),
            ]
        cache_nodes = topology.get("cache", {}).get("nodes", [])
        for i, node in enumerate(cache_nodes):
            dims = {"CacheClusterId": node}
            queries.append(MetricQuery(f"cachecpu{i}", "AWS/ElastiCache", "EngineCPUUtilization", dims, "Maximum"))
            queries.append(
                MetricQuery(f"cachemem{i}", "AWS/ElastiCache", "DatabaseMemoryUsagePercentage", dims, "Maximum")
            )
        host_metrics: list[tuple[str, dict[str, str]]] = []
        if service.host_metrics_namespace:
            try:
                host_metrics = await self.aws.list_metrics(
                    service.host_metrics_namespace, ["mem_used_percent", "disk_used_percent"]
                )
            except AwsCredentialsError:
                raise
            except Exception as e:
                errors.append({"source": f"Métricas del servidor ({service.name})", "error": _aws_error(e)})
        for i, (metric, dims) in enumerate(host_metrics):
            queries.append(MetricQuery(f"host{i}", service.host_metrics_namespace, metric, dims, "Maximum"))

        series = await self.aws.metric_data(queries, start, end)
        down, intervals = down_minutes_by_hour([series.get(f"hh{i}", []) for i in range(len(groups))], start, count)
        hours = build_hours(series, down, start, count, self.settings)
        changes, github = await self._changes(service, start, errors)
        incidents = detect_incidents(hours, changes)

        resource_ids = {lb["dimension"], *instance_ids, *cache_nodes, *(tg["dimension"] for tg in lb["target_groups"])}
        if service.database:
            resource_ids.add(service.database)
        mine = related_alarms(alarms, resource_ids)
        now = datetime.now(UTC)
        alarm_rows = []
        for alarm in mine:
            history = await self.aws.alarm_history(alarm.name, start)
            alarm_rows.append({
                "name": alarm.name,
                "state": alarm.state,
                "metric": alarm.metric,
                "threshold": alarm.threshold,
                "since": alarm.updated_at.isoformat() if alarm.updated_at else None,
                "fired": sum("to ALARM" in h["summary"] for h in history),
                "notifies": alarm.actions > 0,
            })

        def col(key: str) -> list[float | None]:
            return _hourly(series.get(key, []), start, count)

        dependencies = []
        if service.database:
            dependencies.append({
                "kind": "database",
                "name": service.database,
                "facts": topology["database"],
                "metrics": [
                    metric_card("cpu", "CPU (máx. por hora)", "%", col("dbcpumax")),
                    metric_card("connections", "Conexiones", "", col("dbconn")),
                    metric_card("free_storage", "Espacio libre", "GB", col("dbfree"), 1 / 1e9),
                    metric_card("free_memory", "Memoria libre", "GB", col("dbmem"), 1 / 1e9),
                ],
            })
        names = {i["id"]: i for i in topology["instances"]}
        for i, instance_id in enumerate(instance_ids):
            metrics = [metric_card("cpu", "CPU (máx. por hora)", "%", col(f"ec2cpu{i}"))]
            for j, (metric, dims) in enumerate(host_metrics):
                label = "Memoria usada" if metric.startswith("mem") else f"Disco usado {dims.get('path', '')}".strip()
                metrics.append(metric_card(metric, label, "%", col(f"host{j}")))
            dependencies.append({
                "kind": "server",
                "name": names.get(instance_id, {}).get("name", instance_id),
                "facts": _jsonable(names.get(instance_id, {"id": instance_id})),
                "metrics": metrics,
            })
        for i, node in enumerate(cache_nodes):
            dependencies.append({
                "kind": "cache",
                "name": node,
                "facts": {k: v for k, v in topology["cache"].items() if k != "nodes"},
                "metrics": [
                    metric_card("cpu", "CPU del motor (máx.)", "%", col(f"cachecpu{i}")),
                    metric_card("memory", "Memoria usada", "%", col(f"cachemem{i}")),
                ],
            })

        return {
            "name": service.name,
            "load_balancer": {k: lb[k] for k in ("name", "state", "scheme")},
            "targets": [
                {"group": tg["name"], **t, "name": names.get(t["id"], {}).get("name", t["id"])}
                for tg in groups for t in tg["targets"]
            ],
            "summary": summarize(hours, intervals, incidents, await self._totals(lb_dim, start, end)),
            "days": build_days(hours, self.tz, self.settings),
            "hours": hours,
            "incidents": incidents,
            "dependencies": dependencies,
            "alarms": alarm_rows,
            "coverage": coverage(service, topology, mine, health_checks, now),
            "changes": changes,
            "github": github,
        }

    async def _totals(self, lb_dim: dict[str, str], start: datetime, end: datetime) -> dict[str, Any]:
        """Exact p95 of the whole window, and the same figures for the window before it."""
        window = int((end - start).total_seconds())
        aws_lb = "AWS/ApplicationELB"
        queries = [
            MetricQuery("req", aws_lb, "RequestCount", lb_dim, "Sum", window),
            MetricQuery("t5", aws_lb, "HTTPCode_Target_5XX_Count", lb_dim, "Sum", window),
            MetricQuery("e5", aws_lb, "HTTPCode_ELB_5XX_Count", lb_dim, "Sum", window),
            MetricQuery("p95", aws_lb, "TargetResponseTime", lb_dim, "p95", window),
        ]
        try:
            data = await self.aws.metric_data(queries, start - timedelta(seconds=window), end)
        except AwsCredentialsError:
            raise
        except Exception as e:
            logger.warning("Ops totals unavailable", error=str(e))
            return {}

        def at(key: str, when: datetime) -> float | None:
            return next((v for ts, v in data.get(key, []) if abs((ts - when).total_seconds()) < 3600), None)

        previous_start = start - timedelta(seconds=window)
        p95, before = at("p95", start), at("p95", previous_start)
        requests = at("req", previous_start)
        previous = None
        if requests:
            failed = (at("t5", previous_start) or 0) + (at("e5", previous_start) or 0)
            previous = {
                "requests": int(requests),
                "availability": round(100 * (1 - failed / requests), 3),
                "p95_ms": round(before * 1000) if before is not None else None,
            }
        return {"p95_ms": round(p95 * 1000) if p95 is not None else None, "previous": previous}

    async def _changes(
        self, service: Any, start: datetime, errors: list[dict[str, str]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Merged pull requests and production deploys in the window, plus what is open."""
        if not service.repo:
            return [], None
        provider = GitHubProvider(self.credential_manager, self.settings.github_account)
        changes: list[dict[str, Any]] = []
        github: dict[str, Any] = {"repo": service.repo, "open": None, "merged": 0, "deploys": 0}
        try:
            for pull in await provider.list_merged_pulls(service.repo, None, start):
                github["merged"] += 1
                changes.append({
                    "kind": "pr", "at": (pull.merged_at or start).isoformat(), "title": pull.title,
                    "ref": f"#{pull.number}", "author": pull.author, "url": pull.url,
                })
            github["open"] = [
                {
                    "number": p.number, "title": p.title, "author": p.author, "draft": p.draft,
                    "created_at": p.created_at.isoformat(), "url": p.url,
                }
                for p in await provider.list_open_pulls(service.repo)
            ]
        except Exception as e:
            errors.append({"source": f"Pull requests de {service.repo}", "error": _github_error(e, "Pull requests")})
        if service.deploy_workflow:
            try:
                for run in await provider.list_workflow_runs(service.repo, service.deploy_workflow, start):
                    github["deploys"] += 1
                    changes.append({
                        "kind": "deploy", "at": run.started_at.isoformat(), "title": run.title,
                        "ref": run.branch or "", "author": run.actor, "url": run.url, "result": run.conclusion,
                    })
            except Exception as e:
                errors.append({"source": f"Despliegues de {service.repo}", "error": _github_error(e, "Actions")})
        return sorted(changes, key=lambda c: c["at"], reverse=True), github


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in data.items()}


def _aws_error(error: Exception) -> str:
    if AwsMonitoringProvider.is_access_denied(error):
        return "El usuario de AWS no tiene permiso de lectura para esto"
    return str(error)[:300]


def _github_error(error: Exception, permission: str) -> str:
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status in (403, 404):
        return f"El token de GitHub no puede leer esto: agrégale lectura de «{permission}» para este repositorio"
    return str(error)[:300]
