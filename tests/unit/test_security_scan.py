"""Security scan: lock file parsing, NVD range matching, findings store, scan flow."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from executive_copilot.services import security_scan
from executive_copilot.services.security_scan import (
    SecurityScanService,
    cpe_match_touches_cycle,
    is_lockfile,
    normalize_severity,
    nvd_severity,
    parse_composer_lock,
    parse_package_lock,
    version_key,
    version_lt,
)
from executive_copilot.services.security_store import SecurityStore

if TYPE_CHECKING:
    from pathlib import Path

# --- versions ---------------------------------------------------------------------


def test_version_ordering_tolerates_prefixes_and_suffixes() -> None:
    assert version_lt("8.3.9", "8.3.12")
    assert version_lt("v10.11.9", "10.11.10-MariaDB")
    assert version_lt("2.4.9", "2.4.62")
    assert not version_lt("24.1.0", "24.1.0")
    assert version_key("22") < version_key("24.0.0")


def test_cpe_range_matching_against_cycle() -> None:
    # Fixed in 2.4.63: the line 2.4 is affected and its newest patch 2.4.62 too.
    m = {"criteria": "cpe:2.3:a:apache:http_server:*:*:*:*:*:*:*:*", "versionEndExcluding": "2.4.63"}
    assert cpe_match_touches_cycle(m, "2.4", "2.4.62") == (True, True, "2.4.63")
    # Already fixed: the newest patch is 2.4.63, older patches of the line were affected.
    assert cpe_match_touches_cycle(m, "2.4", "2.4.63") == (True, False, "2.4.63")
    # Range entirely below the line (PHP 8.1 only) does not touch 8.3.
    m = {"criteria": "cpe:2.3:a:php:php:*:*:*:*:*:*:*:*", "versionStartIncluding": "8.1.0", "versionEndExcluding": "8.1.30"}
    assert cpe_match_touches_cycle(m, "8.3", "8.3.12")[0] is False
    # Explicit version in the criteria.
    m = {"criteria": "cpe:2.3:a:php:php:8.3.11:*:*:*:*:*:*:*"}
    assert cpe_match_touches_cycle(m, "8.3", "8.3.12") == (True, False, None)
    assert cpe_match_touches_cycle(m, "8.4", "8.4.2")[0] is False
    # Range spanning both lines affects both.
    m = {"criteria": "cpe:2.3:a:nodejs:node.js:*:*:*:*:*:*:*:*", "versionStartIncluding": "20.0.0", "versionEndExcluding": "24.2.0"}
    assert cpe_match_touches_cycle(m, "22", "22.10.0") == (True, True, None)
    assert cpe_match_touches_cycle(m, "24", "24.1.0") == (True, True, "24.2.0")


def test_severity_normalization() -> None:
    assert normalize_severity("MODERATE") == "medium"
    assert normalize_severity(None, 9.8) == "critical"
    assert normalize_severity("", 5.0) == "medium"
    assert normalize_severity(None) == "unknown"
    cve = {"metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 7.5, "baseSeverity": "HIGH"}}]}}
    assert nvd_severity(cve) == ("high", 7.5)


# --- lock files -------------------------------------------------------------------


def test_parse_package_lock_v3_and_v1() -> None:
    v3 = json.dumps({
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "app", "version": "1.0.0"},
            "node_modules/lodash": {"version": "4.17.15"},
            "node_modules/a/node_modules/lodash": {"version": "4.17.21", "dev": True},
            "node_modules/@scope/pkg": {"version": "2.0.0", "dev": True},
            "packages/local": {"link": True},
        },
    })
    pkgs = set(parse_package_lock(v3))
    assert ("npm", "lodash", "4.17.15", False) in pkgs
    assert ("npm", "lodash", "4.17.21", True) in pkgs
    assert ("npm", "@scope/pkg", "2.0.0", True) in pkgs
    assert len(pkgs) == 3

    v1 = json.dumps({"lockfileVersion": 1, "dependencies": {"express": {"version": "4.17.1", "dependencies": {"qs": {"version": "6.7.0"}}}}})
    assert set(parse_package_lock(v1)) == {("npm", "express", "4.17.1", False), ("npm", "qs", "6.7.0", False)}
    assert parse_package_lock("not json") == []


def test_parse_composer_lock_strips_v_and_skips_dev_branches() -> None:
    lock = json.dumps({
        "packages": [{"name": "laravel/framework", "version": "v11.30.0"}],
        "packages-dev": [{"name": "phpunit/phpunit", "version": "11.4.3"}, {"name": "x/y", "version": "dev-main"}],
    })
    assert parse_composer_lock(lock) == [("Packagist", "laravel/framework", "11.30.0", False), ("Packagist", "phpunit/phpunit", "11.4.3", True)]


def test_is_lockfile_skips_vendored_and_test_trees() -> None:
    assert is_lockfile("package-lock.json")
    assert is_lockfile("apps/web/composer.lock")
    assert not is_lockfile("node_modules/x/package-lock.json")
    assert not is_lockfile("tests/fixtures/package-lock.json")
    assert not is_lockfile("package.json")


# --- store ------------------------------------------------------------------------


def _finding(fid: str, severity: str = "high", **extra: Any) -> dict[str, Any]:
    return {"id": fid, "source": "osv", "kind": "package", "vuln_id": fid.split(":")[1], "severity": severity,
            "component": "lodash", "ecosystem": "npm", "version": "4.17.15", "repo": "api", "title": "Prototype pollution",
            "url": "https://osv.dev/x", **extra}


def test_store_tracks_new_resolved_and_review_state(tmp_path: Path) -> None:
    store = SecurityStore(tmp_path / "activity.db")
    stats = store.upsert([_finding("osv:GHSA-1:npm:lodash:api"), _finding("osv:GHSA-2:npm:lodash:api", "critical")])
    assert stats["new"] == 2 and stats["resolved"] == 0
    assert store.summary()["open"] == 2 and store.summary()["critical"] == 1 and store.summary()["new_today"] == 2

    reviewed = store.set_state("osv:GHSA-1:npm:lodash:api", "reviewed", "parche programado")
    assert reviewed and reviewed["state"] == "reviewed" and reviewed["note"] == "parche programado"
    with pytest.raises(ValueError):
        store.set_state("osv:GHSA-1:npm:lodash:api", "bogus")

    # Second run: GHSA-2 disappeared (fixed), GHSA-1 still there and keeps its review state.
    stats = store.upsert([_finding("osv:GHSA-1:npm:lodash:api")])
    assert stats["resolved"] == 1 and stats["new"] == 0 and stats["updated"] == 1
    rows = store.findings()
    assert [r["id"] for r in rows] == ["osv:GHSA-1:npm:lodash:api"]
    assert rows[0]["state"] == "reviewed"
    assert store.findings(states=("resolved",))[0]["id"] == "osv:GHSA-2:npm:lodash:api"

    # It comes back: reopened. Config ignore list applies by advisory id.
    stats = store.upsert([_finding("osv:GHSA-1:npm:lodash:api"), _finding("osv:GHSA-2:npm:lodash:api")], {"GHSA-2": "no aplica"})
    assert stats["reopened"] == 1
    ignored = store.findings(states=("ignored",))
    assert [r["id"] for r in ignored] == ["osv:GHSA-2:npm:lodash:api"] and ignored[0]["note"] == "no aplica"
    assert store.summary()["ignored"] == 1


def test_dev_dependencies_are_kept_out_of_the_headline_counts(tmp_path: Path) -> None:
    store = SecurityStore(tmp_path / "a.db")
    store.upsert([_finding("osv:A:npm:x:r", "critical", dev=True), _finding("osv:B:npm:y:r", "high")])
    assert store.summary()["critical"] == 0 and store.summary()["high"] == 1 and store.summary()["dev_open"] == 1
    assert store.summary(include_dev=True)["critical"] == 1
    assert [r["id"] for r in store.findings(include_dev=False)] == ["osv:B:npm:y:r"]
    assert len(store.findings()) == 2


def test_findings_sorted_worst_first(tmp_path: Path) -> None:
    store = SecurityStore(tmp_path / "a.db")
    store.upsert([_finding("osv:A:npm:x:r", "low"), _finding("osv:B:npm:x:r", "critical"), _finding("osv:C:npm:x:r", "unknown"), _finding("osv:D:npm:x:r", "medium")])
    assert [r["severity"] for r in store.findings()] == ["critical", "medium", "low", "unknown"]


# --- cycles -----------------------------------------------------------------------


def test_pick_cycles_newest_first_with_lts_filter() -> None:
    today = date(2026, 10, 24)
    cycles = [
        {"cycle": "25", "latest": "25.0.1", "lts": False, "eol": "2026-06-01"},
        {"cycle": "24", "latest": "24.9.0", "lts": "2025-10-28", "eol": "2028-04-30"},
        {"cycle": "23", "latest": "23.11.0", "lts": False, "eol": "2026-06-01"},
        {"cycle": "22", "latest": "22.20.0", "lts": "2024-10-29", "eol": "2027-04-30"},
    ]
    picked = SecurityScanService.pick_cycles(cycles, 2, True, today)
    assert [c["cycle"] for c in picked] == ["24", "22"]
    assert picked[1]["days_left"] == (date(2027, 4, 30) - today).days
    picked = SecurityScanService.pick_cycles(cycles, 2, False, today)
    assert [c["cycle"] for c in picked] == ["25", "24"]
    assert picked[0]["days_left"] < 0
    # A line dead for years is not "one of the last two".
    apache = [{"cycle": "2.4", "latest": "2.4.62", "eol": False}, {"cycle": "2.2", "latest": "2.2.34", "eol": "2017-07-11"}]
    assert [c["cycle"] for c in SecurityScanService.pick_cycles(apache, 2, False, today)] == ["2.4"]


# --- scan flow (mocked HTTP + GitHub) ------------------------------------------------


class FakeRepo(SimpleNamespace):
    pass


class FakeGitHub:
    """One repo with a lock file; counts blob downloads to prove the sha cache works."""

    downloads = 0

    def __init__(self, credential_manager: Any, account_id: str = "default") -> None:
        pass

    async def list_repos(self, org: str) -> list[Any]:
        return [FakeRepo(full_name=f"{org}/api", name="api", owner=org, archived=False, default_branch="main",
                         pushed_at=datetime(2026, 10, 20, tzinfo=UTC), description="")]

    async def get_tree(self, repo: Any) -> list[Any]:
        return [SimpleNamespace(path="package-lock.json", sha="abc", size=100),
                SimpleNamespace(path="node_modules/x/package-lock.json", sha="zzz", size=100)]

    async def get_text(self, repo: Any, sha: str) -> str:
        FakeGitHub.downloads += 1
        return json.dumps({"lockfileVersion": 3, "packages": {"node_modules/lodash": {"version": "4.17.15"}}})


def _config(tmp_path: Path, watchlist: list[Any] | None = None) -> Any:
    return SimpleNamespace(
        security=SimpleNamespace(enabled=True, cycles=2, lookback_days=120, eol_warning_days=180, scan_repos=True,
                                 max_lockfile_kb=3000, use_ai=False, state_file=str(tmp_path / "state.json"),
                                 watchlist=watchlist or [], ignore=[]),
        github_docs=SimpleNamespace(enabled=True, orgs=["PayCode"], account="default", include_repos=[],
                                    exclude_repos=[], include_archived=False),
    )


def _osv_transport(calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v1/querybatch":
            body = json.loads(request.content)
            assert body["queries"][0]["package"] == {"name": "lodash", "ecosystem": "npm"}
            return httpx.Response(200, json={"results": [{"vulns": [{"id": "GHSA-p6mc-m468-83gw", "modified": "x"}]}]})
        if request.url.path == "/v1/vulns/GHSA-p6mc-m468-83gw":
            return httpx.Response(200, json={
                "id": "GHSA-p6mc-m468-83gw", "summary": "Prototype Pollution in lodash", "published": "2020-07-15T00:00:00Z",
                "aliases": ["CVE-2020-8203"], "database_specific": {"severity": "HIGH"},
                "affected": [{"package": {"ecosystem": "npm", "name": "lodash"},
                              "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "4.17.19"}]}]}],
            })
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_scan_repos_finds_osv_vulns_and_caches_lockfiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security_scan, "GitHubProvider", FakeGitHub)
    calls: list[str] = []
    real_client = httpx.AsyncClient
    monkeypatch.setattr(security_scan.httpx, "AsyncClient", lambda **kw: real_client(transport=_osv_transport(calls), **kw))
    FakeGitHub.downloads = 0

    service = SecurityScanService(_config(tmp_path), credential_manager=None, store=SecurityStore(tmp_path / "a.db"))
    stats = await service.scan()
    assert stats["repos"] == 1 and stats["lockfiles"] == 1 and stats["packages"] == 1
    assert stats["new"] == 1 and stats["by_severity"] == {"high": 1}
    assert stats["sources"] == {"osv": "ok", "endoflife": "empty", "nvd": "empty"}
    rows = service.store.findings()
    assert rows[0]["id"] == "osv:GHSA-p6mc-m468-83gw:npm:lodash@4.17.15:api"
    assert rows[0]["dev"] == 0
    assert rows[0]["fixed_in"] == "4.17.19" and rows[0]["repo"] == "api" and rows[0]["details"]["aliases"] == ["CVE-2020-8203"]
    assert FakeGitHub.downloads == 1

    # Second run: same pushed_at, so no tree/blob reads; OSV details come from the cache.
    calls.clear()
    stats = await service.scan()
    assert FakeGitHub.downloads == 1
    assert stats["new"] == 0 and stats["updated"] == 1
    assert calls == ["POST /v1/querybatch"]

    # A lock file cached by an older parser is read again.
    state = service.load_state()
    state["repos"]["PayCode/api"]["lockfiles"]["package-lock.json"]["v"] = 1
    service._save_state(state)
    await service.scan()
    assert FakeGitHub.downloads == 2


@pytest.mark.asyncio
async def test_scan_fails_when_every_source_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Denied(FakeGitHub):
        async def list_repos(self, org: str) -> list[Any]:
            raise httpx.HTTPError("403 Forbidden")

    monkeypatch.setattr(security_scan, "GitHubProvider", Denied)
    service = SecurityScanService(_config(tmp_path), credential_manager=None, store=SecurityStore(tmp_path / "a.db"))
    stats = await service.scan()
    assert "error" in stats and "403" in stats["error"]


@pytest.mark.asyncio
async def test_watchlist_eol_and_nvd_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    today = datetime.now(UTC).date()
    soon = date.fromordinal(today.toordinal() + 60).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "endoflife.date":
            assert request.url.path == "/api/apache-http-server.json"
            return httpx.Response(200, json=[
                {"cycle": "2.4", "latest": "2.4.62", "eol": soon, "lts": False, "latestReleaseDate": "2026-09-01"},
                {"cycle": "2.2", "latest": "2.2.34", "eol": "2017-07-11", "lts": False},
            ])
        if request.url.host == "services.nvd.nist.gov":
            assert request.url.params["virtualMatchString"] == "cpe:2.3:a:apache:http_server"
            return httpx.Response(200, json={"vulnerabilities": [
                {"cve": {"id": "CVE-2026-1111", "published": "2026-10-01T10:00:00.000",
                         "descriptions": [{"lang": "en", "value": "Request smuggling in mod_proxy"}],
                         "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.1, "baseSeverity": "CRITICAL"}}]},
                         "configurations": [{"nodes": [{"cpeMatch": [
                             {"vulnerable": True, "criteria": "cpe:2.3:a:apache:http_server:*:*:*:*:*:*:*:*", "versionEndExcluding": "2.4.63"}]}]}]}},
                {"cve": {"id": "CVE-2026-2222", "published": "2026-10-02T10:00:00.000",
                         "descriptions": [{"lang": "en", "value": "Only 2.2"}],
                         "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 5.0, "baseSeverity": "MEDIUM"}}]},
                         "configurations": [{"nodes": [{"cpeMatch": [
                             {"vulnerable": True, "criteria": "cpe:2.3:a:apache:http_server:*:*:*:*:*:*:*:*",
                              "versionStartIncluding": "2.2.0", "versionEndExcluding": "2.2.35"}]}]}]}},
            ]})
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(security_scan.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    watch = SimpleNamespace(product="apache-http-server", name=None, cpe=None, cycles=1, lts_only=None, where=["web-01"])
    config = _config(tmp_path, [watch])
    config.security.scan_repos = False
    service = SecurityScanService(config, credential_manager=None, store=SecurityStore(tmp_path / "a.db"))
    stats = await service.scan()
    assert stats["products"] == 1 and stats["sources"]["nvd"] == "ok" and stats["sources"]["endoflife"] == "ok"
    rows = {r["id"]: r for r in service.store.findings()}
    assert set(rows) == {"nvd:CVE-2026-1111:apache-http-server:2.4", "eol:apache-http-server:2.4"}
    cve = rows["nvd:CVE-2026-1111:apache-http-server:2.4"]
    assert cve["severity"] == "critical" and cve["fixed_in"] is None and cve["details"]["affects_latest"] is True
    assert cve["component"] == "Apache HTTP Server" and cve["details"]["where"] == ["web-01"]
    eol = rows["eol:apache-http-server:2.4"]
    assert eol["severity"] == "medium" and "60 días" in eol["title"]


def test_known_products_have_a_source() -> None:
    for key, info in security_scan.KNOWN_PRODUCTS.items():
        assert info["name"], key
        assert "cpe" in info or "osv" in info or key in ("ubuntu", "debian")
