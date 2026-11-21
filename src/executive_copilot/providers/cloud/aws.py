"""AWS monitoring provider: CloudWatch metrics and alarms plus the resource lookups
needed to address them (read-only).

Credentials are the AWS CLI profile found in ``~/.aws`` (mounted read-only in the
container). Nothing is cached or copied here: every call builds on the profile as
it is on disk, so an expired or rotated key shows up on the next request.

Profiles signed in with ``aws login`` hold a 15-minute access token that the CLI
renews with a rotating refresh token. This provider only *reads* the current access
token and never renews it: a renewal from a read-only mount would rotate the refresh
token without being able to save it, logging the user's own CLI out. When the token
is about to expire the user is asked to run an AWS CLI command, which renews it.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from executive_copilot.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)

_EXPIRED_CODES = {"ExpiredToken", "ExpiredTokenException", "RequestExpired"}
_INVALID_CODES = {"InvalidClientTokenId", "SignatureDoesNotMatch", "UnrecognizedClientException", "AuthFailure"}
_DENIED_CODES = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
LOGIN_MIN_SECONDS = 120  # a reading takes ~30 s: do not start one on a token about to expire


class AwsCredentialsError(Exception):
    """The profile cannot be used; ``hint`` says what to run to fix it."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class MetricQuery:
    """One CloudWatch time series."""

    id: str
    namespace: str
    metric: str
    dimensions: dict[str, str]
    stat: str
    period: int = 3600


@dataclass(frozen=True)
class Alarm:
    """A CloudWatch metric alarm."""

    name: str
    state: str
    namespace: str | None
    metric: str | None
    dimensions: dict[str, str] = field(default_factory=dict)
    threshold: float | None = None
    comparison: str | None = None
    updated_at: datetime | None = None
    actions: int = 0


class AwsMonitoringProvider:
    """Read-only AWS client for one CLI profile and region."""

    def __init__(self, profile: str = "default", region: str = "us-east-1") -> None:
        self.profile = profile
        self.region = region
        self._session: Any = None

    # --- plumbing -----------------------------------------------------------

    def _client(self, service: str) -> Any:
        import boto3
        from botocore.config import Config

        if self._session is None:
            token = self._login_token()
            if token is None:
                self._session = boto3.Session(profile_name=self.profile, region_name=self.region)
            else:
                self._session = boto3.Session(
                    aws_access_key_id=token["accessKeyId"],
                    aws_secret_access_key=token["secretAccessKey"],
                    aws_session_token=token["sessionToken"],
                    region_name=self.region,
                )
        return self._session.client(
            service, config=Config(connect_timeout=8, read_timeout=30, retries={"max_attempts": 3})
        )

    def renew_hint(self) -> str:
        """Any CLI call renews an ``aws login`` session that is still alive."""
        return f"aws sts get-caller-identity --profile {self.profile}"

    def _login_token(self) -> dict[str, Any] | None:
        """Current access token of an ``aws login`` profile; ``None`` for any other kind of profile."""
        import botocore.session
        from botocore.utils import generate_login_cache_key, get_login_token_cache_directory

        scoped = botocore.session.Session(profile=self.profile).get_scoped_config()
        session_name = scoped.get("login_session")
        if not session_name:
            return None
        path = Path(get_login_token_cache_directory()) / f"{generate_login_cache_key(session_name)}.json"
        try:
            token: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["accessToken"]
            expires = datetime.fromisoformat(str(token["expiresAt"]).replace("Z", "+00:00"))
        except (OSError, KeyError, ValueError) as e:
            raise AwsCredentialsError(
                f"El perfil «{self.profile}» no tiene una sesión iniciada", f"aws login --profile {self.profile}"
            ) from e
        remaining = (expires - datetime.now(UTC)).total_seconds()
        if remaining < LOGIN_MIN_SECONDS:
            state = "venció" if remaining <= 0 else "está por vencer"
            raise AwsCredentialsError(f"La sesión de AWS del perfil «{self.profile}» {state}", self.renew_hint())
        return token

    async def _run(self, fn: Callable[[], Any]) -> Any:
        """boto3 is blocking: run the call in a thread and translate credential failures."""
        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            NoCredentialsError,
            PartialCredentialsError,
            ProfileNotFound,
        )

        login = f"aws configure --profile {self.profile}"
        try:
            return await asyncio.to_thread(fn)
        except ProfileNotFound as e:
            raise AwsCredentialsError(f"No existe el perfil «{self.profile}» en ~/.aws", login) from e
        except (NoCredentialsError, PartialCredentialsError) as e:
            raise AwsCredentialsError(f"El perfil «{self.profile}» no tiene credenciales", login) from e
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            if code in _EXPIRED_CODES:
                raise AwsCredentialsError(f"La sesión de AWS del perfil «{self.profile}» venció", self.renew_hint()) from e
            if code in _INVALID_CODES:
                raise AwsCredentialsError(f"AWS rechazó las llaves del perfil «{self.profile}» ({code})", login) from e
            raise
        except BotoCoreError as e:  # token refresh, endpoint or config problems
            name = type(e).__name__
            if "Token" in name or "SSO" in name or "Credential" in name:
                raise AwsCredentialsError(
                    f"No se pudo renovar la sesión del perfil «{self.profile}» ({name})",
                    f"aws sso login --profile {self.profile}",
                ) from e
            raise

    @staticmethod
    def is_access_denied(error: Exception) -> bool:
        response = getattr(error, "response", None) or {}
        return str(response.get("Error", {}).get("Code", "")) in _DENIED_CODES

    # --- identity -----------------------------------------------------------

    async def identity(self) -> dict[str, str]:
        """Who the profile is; raises :class:`AwsCredentialsError` when it cannot be used."""
        data = await self._run(lambda: self._client("sts").get_caller_identity())
        arn = str(data.get("Arn", ""))
        return {"account": str(data.get("Account", "")), "arn": arn, "name": arn.rsplit("/", 1)[-1]}

    # --- topology -----------------------------------------------------------

    async def load_balancer(self, name: str) -> dict[str, Any]:
        """A load balancer with its target groups and the health of each target."""

        def fetch() -> dict[str, Any]:
            elb = self._client("elbv2")
            lb = elb.describe_load_balancers(Names=[name])["LoadBalancers"][0]
            groups = []
            for tg in elb.describe_target_groups(LoadBalancerArn=lb["LoadBalancerArn"])["TargetGroups"]:
                health = elb.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])
                groups.append({
                    "name": tg["TargetGroupName"],
                    "dimension": tg["TargetGroupArn"].split(":")[-1],
                    "target_type": tg.get("TargetType"),
                    "targets": [
                        {
                            "id": t["Target"]["Id"],
                            "port": t["Target"].get("Port"),
                            "state": t.get("TargetHealth", {}).get("State"),
                        }
                        for t in health["TargetHealthDescriptions"]
                    ],
                })
            return {
                "name": lb["LoadBalancerName"],
                "dimension": lb["LoadBalancerArn"].split(":loadbalancer/")[-1],
                "state": lb.get("State", {}).get("Code"),
                "scheme": lb.get("Scheme"),
                "target_groups": groups,
            }

        result: dict[str, Any] = await self._run(fetch)
        return result

    async def instances(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []

        def fetch() -> list[dict[str, Any]]:
            out = []
            for res in self._client("ec2").describe_instances(InstanceIds=ids)["Reservations"]:
                for inst in res["Instances"]:
                    tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                    out.append({
                        "id": inst["InstanceId"],
                        "name": tags.get("Name") or inst["InstanceId"],
                        "type": inst.get("InstanceType"),
                        "state": inst.get("State", {}).get("Name"),
                        "launched_at": inst.get("LaunchTime"),
                    })
            return out

        result: list[dict[str, Any]] = await self._run(fetch)
        return result

    async def database(self, identifier: str) -> dict[str, Any]:
        def fetch() -> dict[str, Any]:
            db = self._client("rds").describe_db_instances(DBInstanceIdentifier=identifier)["DBInstances"][0]
            return {
                "id": db["DBInstanceIdentifier"],
                "engine": db.get("Engine"),
                "version": db.get("EngineVersion"),
                "class": db.get("DBInstanceClass"),
                "multi_az": bool(db.get("MultiAZ")),
                "status": db.get("DBInstanceStatus"),
                "storage_gb": db.get("AllocatedStorage"),
            }

        result: dict[str, Any] = await self._run(fetch)
        return result

    async def cache(self, group_id: str) -> dict[str, Any]:
        def fetch() -> dict[str, Any]:
            group = self._client("elasticache").describe_replication_groups(ReplicationGroupId=group_id)[
                "ReplicationGroups"
            ][0]
            return {
                "id": group["ReplicationGroupId"],
                "status": group.get("Status"),
                "multi_az": group.get("MultiAZ") == "enabled",
                "failover": group.get("AutomaticFailover") == "enabled",
                "nodes": list(group.get("MemberClusters", [])),
            }

        result: dict[str, Any] = await self._run(fetch)
        return result

    # --- metrics ------------------------------------------------------------

    async def metric_data(
        self, queries: list[MetricQuery], start: datetime, end: datetime
    ) -> dict[str, list[tuple[datetime, float]]]:
        """Datapoints per query id, oldest first. Queries without data return an empty list."""
        if not queries:
            return {}
        payload = [
            {
                "Id": q.id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": q.namespace,
                        "MetricName": q.metric,
                        "Dimensions": [{"Name": k, "Value": v} for k, v in q.dimensions.items()],
                    },
                    "Period": q.period,
                    "Stat": q.stat,
                },
                "ReturnData": True,
            }
            for q in queries
        ]

        def fetch() -> dict[str, list[tuple[datetime, float]]]:
            cw = self._client("cloudwatch")
            out: dict[str, list[tuple[datetime, float]]] = {q.id: [] for q in queries}
            for i in range(0, len(payload), 400):  # API limit: 500 queries per call
                kwargs: dict[str, Any] = {
                    "MetricDataQueries": payload[i : i + 400],
                    "StartTime": start,
                    "EndTime": end,
                    "ScanBy": "TimestampAscending",
                }
                while True:
                    page = cw.get_metric_data(**kwargs)
                    for series in page["MetricDataResults"]:
                        out[series["Id"]].extend(zip(series["Timestamps"], series["Values"], strict=False))
                    token = page.get("NextToken")
                    if not token:
                        break
                    kwargs["NextToken"] = token
            return out

        result: dict[str, list[tuple[datetime, float]]] = await self._run(fetch)
        return result

    async def list_metrics(self, namespace: str, names: list[str]) -> list[tuple[str, dict[str, str]]]:
        """Metric name + dimensions of every series of ``names`` in a namespace."""

        def fetch() -> list[tuple[str, dict[str, str]]]:
            out = []
            paginator = self._client("cloudwatch").get_paginator("list_metrics")
            for page in paginator.paginate(Namespace=namespace):
                for m in page["Metrics"]:
                    if m["MetricName"] in names:
                        out.append((m["MetricName"], {d["Name"]: d["Value"] for d in m["Dimensions"]}))
            return out

        result: list[tuple[str, dict[str, str]]] = await self._run(fetch)
        return result

    # --- alarms and external checks ------------------------------------------

    async def alarms(self) -> list[Alarm]:
        def fetch() -> list[Alarm]:
            out = []
            paginator = self._client("cloudwatch").get_paginator("describe_alarms")
            for page in paginator.paginate(AlarmTypes=["MetricAlarm"]):
                for a in page["MetricAlarms"]:
                    out.append(Alarm(
                        name=a["AlarmName"],
                        state=a.get("StateValue", ""),
                        namespace=a.get("Namespace"),
                        metric=a.get("MetricName"),
                        dimensions={d["Name"]: d["Value"] for d in a.get("Dimensions", [])},
                        threshold=a.get("Threshold"),
                        comparison=a.get("ComparisonOperator"),
                        updated_at=a.get("StateUpdatedTimestamp"),
                        actions=len(a.get("AlarmActions", [])) if a.get("ActionsEnabled", True) else 0,
                    ))
            return out

        result: list[Alarm] = await self._run(fetch)
        return result

    async def alarm_history(self, name: str, start: datetime) -> list[dict[str, Any]]:
        """State changes of one alarm since ``start`` (CloudWatch keeps about two weeks)."""

        def fetch() -> list[dict[str, Any]]:
            out = []
            paginator = self._client("cloudwatch").get_paginator("describe_alarm_history")
            for page in paginator.paginate(AlarmName=name, HistoryItemType="StateUpdate", StartDate=start):
                for item in page["AlarmHistoryItems"]:
                    out.append({"at": item["Timestamp"], "summary": item.get("HistorySummary", "")})
            return out

        result: list[dict[str, Any]] = await self._run(fetch)
        return result

    async def health_checks(self) -> list[dict[str, Any]]:
        """Route 53 health checks (the external view of a service)."""

        def fetch() -> list[dict[str, Any]]:
            out = []
            paginator = self._client("route53").get_paginator("list_health_checks")
            for page in paginator.paginate():
                for check in page["HealthChecks"]:
                    cfg = check.get("HealthCheckConfig", {})
                    out.append({
                        "id": check["Id"],
                        "type": cfg.get("Type"),
                        "domain": cfg.get("FullyQualifiedDomainName"),
                        "path": cfg.get("ResourcePath"),
                    })
            return out

        result: list[dict[str, Any]] = await self._run(fetch)
        return result
