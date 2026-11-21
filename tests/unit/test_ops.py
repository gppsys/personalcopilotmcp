"""Operations dashboard: hour flags, day roll-up, incidents, coverage, credentials flow."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar
from zoneinfo import ZoneInfo

import httpx
import pytest

from executive_copilot.config.models import OpsConfig, OpsServiceConfig
from executive_copilot.providers.cloud.aws import Alarm, AwsCredentialsError, AwsMonitoringProvider
from executive_copilot.providers.code.github import GitHubProvider
from executive_copilot.services import ops
from executive_copilot.services.ops import (
    OpsService,
    OpsStore,
    build_days,
    build_hours,
    coverage,
    detect_incidents,
    down_minutes_by_hour,
    summarize,
)

if TYPE_CHECKING:
    from pathlib import Path

START = datetime(2026, 11, 10, 6, tzinfo=UTC)  # local midnight in Mexico City
SETTINGS = OpsConfig()
TZ = ZoneInfo("America/Mexico_City")


def at(hour: int, minute: int = 0) -> datetime:
    return START + timedelta(hours=hour, minutes=minute)


def hourly(values: dict[int, float]) -> list[tuple[datetime, float]]:
    return [(at(h), v) for h, v in values.items()]


def test_down_minutes_use_the_worst_target_group() -> None:
    healthy = [(at(0, m), 1.0) for m in range(0, 60, 5)]
    other = [(at(0, 0), 1.0), (at(0, 5), 0.0), (at(0, 10), 0.0), (at(1, 0), 1.0)]
    down, intervals = down_minutes_by_hour([healthy, other], START, 2)
    assert down == [10, 0]
    assert intervals == 13


def test_hours_are_flagged_by_errors_latency_downtime_and_db_cpu() -> None:
    series = {
        "req": hourly({0: 10_000, 1: 10_000, 2: 10_000, 3: 40, 4: 10_000, 5: 10_000}),
        "t5": hourly({1: 150, 2: 400, 3: 20}),
        "e5": hourly({1: 100, 2: 200}),
        "e502": hourly({2: 200}),
        "p95": hourly({0: 0.3, 1: 0.3, 2: 3.0, 3: 9.0, 4: 1.2, 5: 0.3}),
        "dbcpu": hourly({5: 91.0}),
    }
    hours = build_hours(series, [0, 0, 0, 0, 0, 5], START, 6, SETTINGS)
    assert [h["level"] for h in hours] == ["ok", "warn", "bad", "ok", "warn", "warn"]
    assert hours[1]["reasons"] == ["errors"] and hours[1]["error_rate"] == 2.5
    assert hours[2]["reasons"] == ["errors", "latency"] and hours[2]["lb_502"] == 200
    # Too little traffic to judge: neither the 50 % error rate nor the 9 s p95 count.
    assert hours[3]["reasons"] == []
    assert hours[4]["reasons"] == ["latency"]
    assert hours[5]["reasons"] == ["db_cpu", "down"]


def test_days_follow_the_local_calendar() -> None:
    series = {"req": hourly(dict.fromkeys(range(30), 1000)), "t5": hourly({2: 200, 3: 300, 26: 30})}
    hours = build_hours(series, [0] * 25 + [5] + [0] * 4, START, 30, SETTINGS)
    days = build_days(hours, TZ, SETTINGS)
    assert [d["date"] for d in days] == ["2026-11-10", "2026-11-11"]
    assert days[0]["hours"] == 24 and days[0]["errors"] == 500 and days[0]["level"] == "bad"
    assert days[1]["down_minutes"] == 5 and days[1]["level"] == "warn"
    assert days[0]["availability"] == pytest.approx(100 - 500 / 24_000 * 100, abs=0.001)


def test_incidents_merge_nearby_hours_and_carry_recent_changes() -> None:
    series = {"req": hourly(dict.fromkeys(range(24), 10000)), "t5": hourly({10: 250, 12: 3000, 20: 300})}
    hours = build_hours(series, [0] * 24, START, 24, SETTINGS)
    changes = [
        {"kind": "pr", "at": at(5).isoformat(), "ref": "#1", "title": "before"},
        {"kind": "pr", "at": at(1).isoformat(), "ref": "#2", "title": "too early"},
        {"kind": "deploy", "at": at(12, 30).isoformat(), "ref": "master", "title": "during"},
    ]
    incidents = detect_incidents(hours, changes)
    assert [(i["start"], i["hours"], i["level"]) for i in incidents] == [
        (at(20).isoformat(), 1, "warn"),
        (at(10).isoformat(), 3, "bad"),
    ]
    assert incidents[1]["errors"] == 3250 and incidents[1]["peak_error_rate"] == 30.0
    assert [c["ref"] for c in incidents[1]["changes"]] == ["#1", "master"]
    assert incidents[0]["changes"] == []
    summary = summarize(hours, 288, incidents, {})
    assert summary["incidents"] == 2 and summary["bad_incidents"] == 1
    assert summary["uptime_hosts"] == 100.0


def test_coverage_names_gaps_and_single_points_of_failure() -> None:
    now = datetime(2026, 11, 20, tzinfo=UTC)
    topology = {
        "load_balancer": {
            "name": "alb", "dimension": "app/alb/1",
            "target_groups": [{"targets": [{"id": "i-1", "state": "healthy"}]}],
        },
        "database": {"id": "db", "multi_az": False},
    }
    alarms = [
        Alarm("db-conns", "ALARM", "AWS/RDS", "DatabaseConnections", {"DBInstanceIdentifier": "db"},
              threshold=30, updated_at=now - timedelta(days=13), actions=1),
        Alarm("lb-5xx", "OK", "AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", {"LoadBalancer": "app/alb/1"}),
    ]
    service = OpsServiceConfig(name="API", load_balancer="alb", domain="api.example.com")
    items = coverage(service, topology, alarms, [], now)
    titles = [(i["level"], i["title"]) for i in items]
    assert ("gap", "Sin chequeo externo") in titles
    assert ("gap", "Balanceador sin alarmas de host caído, latencia") in titles
    assert ("gap", "Base de datos sin alarma de CPU ni espacio en disco") in titles
    assert ("gap", "Alarma disparada permanentemente") in titles
    assert ("gap", "Alarma que no notifica") in titles
    assert ("risk", "Un solo servidor") in titles and ("risk", "Base de datos en una sola zona") in titles
    assert [i["level"] for i in items] == sorted((i["level"] for i in items), key=("gap", "risk", "ok").index)

    covered = coverage(service, topology, alarms, [{"domain": "API.example.com"}], now)
    assert ("ok", "Chequeo externo") in [(i["level"], i["title"]) for i in covered]
    # Route 53 could not be read: say nothing rather than report a gap we did not verify.
    unknown = coverage(service, topology, alarms, None, now)
    assert not any("externo" in i["title"] for i in unknown)


# --- service flow ---------------------------------------------------------------


class FakeAws:
    """Stands in for AwsMonitoringProvider; ``broken`` makes the profile unusable."""

    broken: ClassVar[AwsCredentialsError | None] = None
    calls: ClassVar[list[str]] = []

    def __init__(self, profile: str, region: str) -> None:
        self.profile = profile

    async def identity(self) -> dict[str, str]:
        type(self).calls.append("identity")
        if self.broken:
            raise self.broken
        return {"account": "123", "arn": "arn:aws:iam::123:user/me", "name": "me"}

    async def alarms(self) -> list[Alarm]:
        return []

    async def health_checks(self) -> list[dict[str, Any]]:
        return []

    async def load_balancer(self, name: str) -> dict[str, Any]:
        type(self).calls.append("load_balancer")
        return {
            "name": name, "dimension": "app/alb/1", "state": "active", "scheme": "internet-facing",
            "target_groups": [{
                "name": "tg", "dimension": "targetgroup/tg/1", "target_type": "instance",
                "targets": [{"id": "i-1", "port": 80, "state": "healthy"}],
            }],
        }

    async def instances(self, ids: list[str]) -> list[dict[str, Any]]:
        return [{"id": i, "name": "web", "type": "t3.large", "state": "running"} for i in ids]

    async def database(self, identifier: str) -> dict[str, Any]:
        return {"id": identifier, "engine": "mariadb", "version": "10.11", "class": "db.m5", "multi_az": True,
                "status": "available", "storage_gb": 100}

    async def metric_data(self, queries: list[Any], start: datetime, end: datetime) -> dict[str, Any]:
        data: dict[str, Any] = {q.id: [] for q in queries}
        if queries[0].period == 3600:
            data["req"] = [(start + timedelta(hours=h), 1000.0) for h in range(48)]
            data["t5"] = [(start + timedelta(hours=5), 400.0)]
            data["hh0"] = [(start + timedelta(minutes=5 * i), 1.0) for i in range(48 * 12)]
            data["dbcpumax"] = [(start, 40.0)]
        else:  # whole-window totals: previous window, then this one
            data["req"] = [(start, 50_000.0), (start + timedelta(days=2), 48_000.0)]
            data["p95"] = [(start, 0.2), (start + timedelta(days=2), 0.4)]
        return data

    async def alarm_history(self, name: str, start: datetime) -> list[dict[str, Any]]:
        return []


def make_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> OpsService:
    FakeAws.broken, FakeAws.calls = None, []
    monkeypatch.setattr(ops, "AwsMonitoringProvider", FakeAws)
    settings = OpsConfig(
        enabled=True, days=2,
        services=[OpsServiceConfig(name="API", load_balancer="alb", database="db")], **overrides,
    )
    return OpsService(SimpleNamespace(ops=settings), None, store=OpsStore(tmp_path / "activity.db"), tz=UTC)


@pytest.mark.asyncio
async def test_refresh_reads_and_stores_a_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service(tmp_path, monkeypatch)
    result = await service.refresh()
    assert result["credentials"]["ok"] and result["credentials"]["identity"] == "me"
    assert result["stale"] is False
    api = result["snapshot"]["services"][0]
    assert api["summary"]["requests"] == 48_000 and api["summary"]["app_5xx"] == 400
    assert api["summary"]["availability"] == pytest.approx(99.167, abs=0.001)
    assert api["summary"]["p95_ms"] == 400
    assert api["summary"]["previous"] == {"requests": 50_000, "availability": 100.0, "p95_ms": 200}
    assert len(api["hours"]) == 48 and len(api["incidents"]) == 1
    assert [d["kind"] for d in api["dependencies"]] == ["database", "server"]
    json.dumps(result)  # the API response must be plain JSON

    # Reopening the view checks the session but does not read CloudWatch again.
    FakeAws.calls = []
    again = await make_service(tmp_path, monkeypatch).overview()
    assert again["snapshot"]["services"][0]["name"] == "API" and FakeAws.calls == ["identity"]


@pytest.mark.asyncio
async def test_unusable_credentials_ask_the_user_and_keep_the_old_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service(tmp_path, monkeypatch)
    await service.refresh()
    FakeAws.calls = []
    FakeAws.broken = AwsCredentialsError("La sesión venció", "aws sts get-caller-identity --profile default")
    result = await service.refresh()
    assert result["credentials"]["ok"] is False
    assert result["credentials"]["hint"] == "aws sts get-caller-identity --profile default"
    assert result["snapshot"] is not None  # shown as the stored reading, not as current
    assert FakeAws.calls == ["identity"]  # nothing was read with a bad session


@pytest.mark.asyncio
async def test_disabled_view_does_not_touch_aws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service(tmp_path, monkeypatch)
    service.settings.enabled = False
    result = await service.refresh()
    assert result["snapshot"] is None and result["credentials"] is None and FakeAws.calls == []


# --- providers ------------------------------------------------------------------


def write_login_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expires: datetime) -> None:
    from botocore.utils import generate_login_cache_key

    config = tmp_path / "config"
    config.write_text("[default]\nlogin_session = arn:aws:iam::123:user/me\nregion = us-east-1\n")
    cache = tmp_path / "cache"
    cache.mkdir()
    token = {"accessToken": {
        "accessKeyId": "AKIA_TEST", "secretAccessKey": "secret", "sessionToken": "token",
        "expiresAt": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }}
    (cache / f"{generate_login_cache_key('arn:aws:iam::123:user/me')}.json").write_text(json.dumps(token))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_LOGIN_CACHE_DIRECTORY", str(cache))
    for name in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_login_session_token_is_read_without_renewing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_login_profile(tmp_path, monkeypatch, datetime.now(UTC) + timedelta(minutes=10))
    token = AwsMonitoringProvider("default")._login_token()
    assert token is not None and token["accessKeyId"] == "AKIA_TEST"


def test_expiring_login_session_asks_for_a_cli_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_login_profile(tmp_path, monkeypatch, datetime.now(UTC) + timedelta(seconds=30))
    with pytest.raises(AwsCredentialsError) as error:
        AwsMonitoringProvider("default")._login_token()
    assert "por vencer" in str(error.value)
    assert error.value.hint == "aws sts get-caller-identity --profile default"


@pytest.mark.asyncio
async def test_github_merged_pulls_and_deploys(monkeypatch: pytest.MonkeyPatch) -> None:
    since = datetime(2026, 11, 6, tzinfo=UTC)

    def pull(number: int, merged: str | None, updated: str) -> dict[str, Any]:
        return {"number": number, "title": f"PR {number}", "user": {"login": "dev"}, "created_at": "2026-11-01T00:00:00Z",
                "merged_at": merged, "updated_at": updated, "html_url": f"https://github.test/{number}"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[
                pull(3, "2026-11-10T12:00:00Z", "2026-11-10T12:00:00Z"),
                pull(2, None, "2026-11-09T00:00:00Z"),  # closed without merging
                pull(1, "2026-11-01T00:00:00Z", "2026-11-08T00:00:00Z"),  # merged before the window
            ])
        assert request.url.params["created"] == ">=2026-11-06T00:00:00Z"
        return httpx.Response(200, json={"workflow_runs": [{
            "run_started_at": "2026-11-10T13:00:00Z", "created_at": "2026-11-10T13:00:00Z", "conclusion": "success",
            "actor": {"login": "dev"}, "head_branch": "master", "display_title": "Deploy", "html_url": "https://github.test/run",
        }]})

    provider = GitHubProvider(SimpleNamespace())  # type: ignore[arg-type]
    monkeypatch.setattr(
        provider, "_client",
        lambda: httpx.AsyncClient(base_url="https://api.github.test", transport=httpx.MockTransport(handler)),
    )
    merged = await provider.list_merged_pulls("org/api", "master", since)
    assert [p.number for p in merged] == [3]
    runs = await provider.list_workflow_runs("org/api", "deploy.yml", since)
    assert runs[0].conclusion == "success" and runs[0].branch == "master"
