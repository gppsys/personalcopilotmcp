"""Daily security review of the stack.

Three keyless public sources, all structured data (the model never decides what
is vulnerable):

* **OSV.dev** for the npm / Packagist packages pinned in the repositories' lock
  files (``package-lock.json``, ``composer.lock``), read through the same GitHub
  token as the docs mirror.
* **NVD** (NIST) for watched products that are not packages: PHP, Node.js,
  Apache httpd, MariaDB, PostgreSQL... matched by CPE against the release lines
  being watched.
* **endoflife.date** to know which release lines are the newest ones (we watch
  the last N, newest first) and when each one stops receiving fixes.

Findings are persisted by :class:`SecurityStore`; this module only produces them.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from executive_copilot.logging import get_logger
from executive_copilot.providers.code.github import GitHubProvider, RepoInfo
from executive_copilot.services.security_store import SecurityStore

logger = get_logger(__name__)

OSV_API = "https://api.osv.dev/v1"
NVD_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"
EOL_API = "https://endoflife.date/api"
NVD_PAUSE_SECONDS = 6.5  # public NVD limit: 5 requests per rolling 30 s
OSV_BATCH = 1000
HTTP_TIMEOUT = httpx.Timeout(15.0, read=90.0)

LOCKFILES = ("package-lock.json", "composer.lock")
PARSER_VERSION = 2  # bump when parse_lockfile output changes, to re-read cached lock files
SKIP_DIRS = {"node_modules", "vendor", "test", "tests", "fixtures", "fixture", "examples", "example", "dist", "build"}

# Products of the stack the UI offers by default. Keys are endoflife.date slugs.
# ``osv`` means the product itself is a package: its release line is checked
# against OSV instead of NVD (cleaner than the framework's CPE).
KNOWN_PRODUCTS: dict[str, dict[str, Any]] = {
    "php": {"name": "PHP", "cpe": "cpe:2.3:a:php:php"},
    "nodejs": {"name": "Node.js", "cpe": "cpe:2.3:a:nodejs:node.js", "lts_only": True},
    "apache-http-server": {"name": "Apache HTTP Server", "cpe": "cpe:2.3:a:apache:http_server"},
    "mariadb": {"name": "MariaDB", "cpe": "cpe:2.3:a:mariadb:mariadb", "lts_only": True},
    "postgresql": {"name": "PostgreSQL", "cpe": "cpe:2.3:a:postgresql:postgresql"},
    "mysql": {"name": "MySQL", "cpe": "cpe:2.3:a:oracle:mysql"},
    "laravel": {"name": "Laravel", "osv": ["Packagist", "laravel/framework"]},
    "symfony": {"name": "Symfony", "osv": ["Packagist", "symfony/symfony"]},
    "nginx": {"name": "nginx", "cpe": "cpe:2.3:a:f5:nginx"},
    "redis": {"name": "Redis", "cpe": "cpe:2.3:a:redis:redis"},
    "openssl": {"name": "OpenSSL", "cpe": "cpe:2.3:a:openssl:openssl"},
    "docker-engine": {"name": "Docker Engine", "cpe": "cpe:2.3:a:docker:docker"},
    "composer": {"name": "Composer", "cpe": "cpe:2.3:a:getcomposer:composer"},
    "ubuntu": {"name": "Ubuntu"},
    "debian": {"name": "Debian"},
    "react": {"name": "React", "osv": ["npm", "react"]},
    "vue": {"name": "Vue", "osv": ["npm", "vue"]},
    "angular": {"name": "Angular", "osv": ["npm", "@angular/core"]},
    "typescript": {"name": "TypeScript", "osv": ["npm", "typescript"]},
}

AI_NOTE_PROMPT = """Eres el asistente de seguridad del CTO de Paycode (stack: Node/TypeScript, PHP/Laravel, Apache, MariaDB, PostgreSQL).
Estos son los hallazgos {scope} (datos estructurados de OSV, NVD y endoflife.date; no inventes nada que no esté aquí):

{findings}

Escribe en español, máximo 120 palabras, en prosa (uno o dos párrafos, sin listas ni encabezados): qué atender primero y por qué, agrupando por repositorio o componente cuando se repita. Si un hallazgo ya tiene versión corregida, dilo como "actualizar a X". No repitas la lista completa; menciona solo lo que cambia la prioridad."""


# --- versions -------------------------------------------------------------------


def version_key(version: str) -> tuple[Any, ...]:
    """Sortable key tolerant of 'v8.3.12', '10.11.9-MariaDB', '2.4.62-1ubuntu1'."""
    text = version.strip().lstrip("vV")
    main = re.split(r"[-+~ ]", text, maxsplit=1)[0]
    parts: list[Any] = []
    for piece in main.split("."):
        m = re.match(r"(\d+)(.*)", piece)
        if m:
            parts.append((int(m.group(1)), m.group(2)))
        else:
            parts.append((-1, piece))
    while len(parts) < 4:
        parts.append((0, ""))
    return tuple(parts)


def version_lt(a: str, b: str) -> bool:
    return version_key(a) < version_key(b)


def version_le(a: str, b: str) -> bool:
    return version_key(a) <= version_key(b)


def in_cycle(version: str, cycle: str) -> bool:
    """'8.3.12' is in cycle '8.3'; '24.1.0' is in cycle '24'."""
    v = version.strip().lstrip("vV")
    return v == cycle or v.startswith(cycle.rstrip(".") + ".")


def cpe_match_touches_cycle(match: dict[str, Any], cycle: str, latest: str) -> tuple[bool, bool, str | None]:
    """Does one NVD cpeMatch affect the release line ``cycle`` (whose newest patch is ``latest``)?

    Returns (affects_cycle, affects_latest, fixed_in).
    """
    criteria = str(match.get("criteria", ""))
    fields = criteria.split(":")
    explicit = fields[5] if len(fields) > 5 else "*"
    if explicit not in ("*", "-", ""):
        hit = in_cycle(explicit, cycle)
        return hit, hit and version_key(explicit) == version_key(latest), None

    start_inc = match.get("versionStartIncluding")
    start_exc = match.get("versionStartExcluding")
    end_inc = match.get("versionEndIncluding")
    end_exc = match.get("versionEndExcluding")
    if not any((start_inc, start_exc, end_inc, end_exc)):
        # Whole product, no version information: treat as affecting the line but unknown fix.
        return True, True, None

    def above_start(v: str) -> bool:
        if start_inc and version_lt(v, start_inc):
            return False
        return not (start_exc and version_le(v, start_exc))

    def below_end(v: str) -> bool:
        if end_inc and version_lt(end_inc, v):
            return False
        return not (end_exc and version_le(end_exc, v))

    cycle_min = cycle if "." in cycle else f"{cycle}.0"
    affects_latest = above_start(latest) and below_end(latest)
    # The range intersects the line if its start is not past the line's newest
    # patch and its end is not before the line's first release.
    affects_cycle = affects_latest or (above_start(latest) and below_end(cycle_min)) or (
        above_start(cycle_min) and below_end(cycle_min)
    )
    fixed_in = end_exc if end_exc and in_cycle(end_exc, cycle) else None
    return affects_cycle, affects_latest, fixed_in


def nvd_severity(cve: dict[str, Any]) -> tuple[str, float | None]:
    metrics = cve.get("metrics") or {}
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        for metric in metrics.get(key) or []:
            data = metric.get("cvssData") or {}
            sev = (data.get("baseSeverity") or metric.get("baseSeverity") or "").lower()
            score = data.get("baseScore")
            if sev or score is not None:
                return normalize_severity(sev, score), score
    return "unknown", None


def normalize_severity(label: str | None, score: float | None = None) -> str:
    label = (label or "").lower()
    if label == "moderate":
        return "medium"
    if label in ("critical", "high", "medium", "low"):
        return label
    if score is not None:
        if score >= 9:
            return "critical"
        if score >= 7:
            return "high"
        if score >= 4:
            return "medium"
        return "low"
    return "unknown"


# --- lock files -----------------------------------------------------------------


Package = tuple[str, str, str, bool]  # ecosystem, name, version, dev-only


def parse_package_lock(text: str) -> list[Package]:
    """Every installed npm package (lockfile v1, v2 and v3); ``dev`` when only a dev dependency."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    found: dict[tuple[str, str], bool] = {}

    def add(name: str, version: str, dev: bool) -> None:
        key = (name, version)
        found[key] = found.get(key, True) and dev

    packages = data.get("packages")
    if isinstance(packages, dict):
        for path, info in packages.items():
            if not path or not isinstance(info, dict) or info.get("link"):
                continue
            name = info.get("name") or path.rsplit("node_modules/", 1)[-1]
            version = info.get("version")
            if name and version:
                add(str(name), str(version), bool(info.get("dev")))
    else:

        def walk(deps: dict[str, Any]) -> None:
            for name, info in deps.items():
                if isinstance(info, dict) and info.get("version"):
                    add(name, str(info["version"]), bool(info.get("dev")))
                    if isinstance(info.get("dependencies"), dict):
                        walk(info["dependencies"])

        if isinstance(data.get("dependencies"), dict):
            walk(data["dependencies"])
    return [("npm", name, version, dev) for (name, version), dev in found.items()]


def parse_composer_lock(text: str) -> list[Package]:
    """Every Composer package; those under ``packages-dev`` are flagged dev."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    out: list[Package] = []
    for key, dev in (("packages", False), ("packages-dev", True)):
        for pkg in data.get(key) or []:
            name, version = pkg.get("name"), pkg.get("version")
            if name and version and not str(version).startswith("dev-"):
                out.append(("Packagist", str(name), str(version).lstrip("vV"), dev))
    return out


def parse_lockfile(path: str, text: str) -> list[Package]:
    base = path.rsplit("/", 1)[-1]
    if base == "package-lock.json":
        return parse_package_lock(text)
    if base == "composer.lock":
        return parse_composer_lock(text)
    return []


def is_lockfile(path: str) -> bool:
    parts = path.split("/")
    if parts[-1] not in LOCKFILES:
        return False
    return not any(p in SKIP_DIRS for p in parts[:-1])


# --- the scan -------------------------------------------------------------------


class SecurityScanService:
    """Build today's findings and store them."""

    def __init__(
        self,
        config: Any,
        credential_manager: Any,
        ai_service: Any | None = None,
        store: SecurityStore | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.config = config
        self.settings = config.security
        self.credential_manager = credential_manager
        self._ai = ai_service
        self.store = store or SecurityStore()
        self.state_path = state_path or Path(self.settings.state_file)

    # --- state (inventory cache + AI note) -------------------------------------

    def load_state(self) -> dict[str, Any]:
        try:
            data: dict[str, Any] = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    # --- entry point -------------------------------------------------------------

    async def scan(self) -> dict[str, Any]:
        started = datetime.now(UTC)
        state = self.load_state()
        stats: dict[str, Any] = {
            "repos": 0, "lockfiles": 0, "packages": 0, "osv_vulns": 0, "products": 0,
            "errors": [], "sources": {},
        }
        findings: list[dict[str, Any]] = []
        sources_ok = 0

        if self.settings.scan_repos and self.config.github_docs.enabled:
            try:
                inventory = await self._repo_inventory(state, stats)
                findings.extend(await self._osv_findings(inventory, state, stats))
                stats["sources"]["osv"] = "ok"
                sources_ok += 1
            except Exception as e:  # listing failures must not hide the watchlist results
                logger.warning("Repository scan failed", error=str(e))
                stats["errors"].append(f"repos: {e}")
                stats["sources"]["osv"] = "error"
        else:
            stats["sources"]["osv"] = "disabled"

        if self.settings.watchlist:
            products = await self._watchlist_findings(stats)
            findings.extend(products)
            if stats["sources"].get("endoflife") == "ok":
                sources_ok += 1
        else:
            stats["sources"]["endoflife"] = "empty"
            stats["sources"]["nvd"] = "empty"

        if sources_ok == 0 and stats["errors"]:
            stats["error"] = "; ".join(stats["errors"])[:500]
            self._save_state(state)
            return stats

        ignored = {i.id: i.reason or "ignorado en configuración" for i in self.settings.ignore if _ignore_active(i)}
        counts = self.store.upsert(findings, ignored)
        stats.update(counts)
        stats["findings"] = len(findings)
        by_sev: dict[str, int] = {}
        for f in findings:
            by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        stats["by_severity"] = by_sev

        # The note is rewritten when something new showed up, and refreshed weekly
        # otherwise so it never describes findings that were fixed long ago.
        note_at = (state.get("ai_note") or {}).get("at")
        stale = not note_at or note_at < (started - timedelta(days=7)).isoformat(timespec="seconds")
        if self._ai is not None and self.settings.use_ai and (counts["new"] or stale):
            note = await self._ai_note(started.date().isoformat(), only_new=bool(counts["new"]))
            if note:
                state["ai_note"] = {"at": started.isoformat(timespec="seconds"), "text": note}
                stats["ai_note"] = True
        state["last_scan"] = {"at": started.isoformat(timespec="seconds"), "stats": {k: v for k, v in stats.items() if k != "errors"}}
        self._save_state(state)
        return stats

    # --- repositories ----------------------------------------------------------------

    def _wanted(self, repo: RepoInfo) -> bool:
        gh = self.config.github_docs
        if repo.archived and not gh.include_archived:
            return False
        if gh.include_repos and repo.name not in gh.include_repos:
            return False
        return repo.name not in gh.exclude_repos

    async def _repo_inventory(self, state: dict[str, Any], stats: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, bool]]:
        """{(ecosystem, name, version): {repo: dev-only}} from every lock file, cached by blob sha."""
        gh = self.config.github_docs
        provider = GitHubProvider(self.credential_manager, gh.account)
        repos_state: dict[str, Any] = state.setdefault("repos", {})
        inventory: dict[tuple[str, str, str], dict[str, bool]] = {}
        seen_repos: set[str] = set()
        max_bytes = self.settings.max_lockfile_kb * 1024

        for org in gh.orgs:
            repos = await provider.list_repos(org)
            for repo in repos:
                if not self._wanted(repo):
                    continue
                seen_repos.add(repo.full_name)
                stats["repos"] += 1
                cached = repos_state.get(repo.full_name) or {}
                pushed = repo.pushed_at.isoformat() if repo.pushed_at else None
                lockfiles: dict[str, Any] = dict(cached.get("lockfiles") or {})
                stale_parser = any(lock.get("v") != PARSER_VERSION for lock in lockfiles.values())
                if cached.get("pushed_at") != pushed or not cached or stale_parser:
                    tree = await provider.get_tree(repo)
                    current = {e.path: e for e in tree if is_lockfile(e.path) and e.size <= max_bytes}
                    for path in list(lockfiles):
                        if path not in current:
                            del lockfiles[path]
                    for path, entry in current.items():
                        known = lockfiles.get(path, {})
                        if known.get("sha") == entry.sha and known.get("v") == PARSER_VERSION:
                            continue
                        try:
                            text = await provider.get_text(repo, entry.sha)
                        except httpx.HTTPError as e:
                            stats["errors"].append(f"{repo.full_name}/{path}: {e}")
                            continue
                        lockfiles[path] = {"sha": entry.sha, "v": PARSER_VERSION, "packages": parse_lockfile(path, text)}
                    repos_state[repo.full_name] = {"pushed_at": pushed, "lockfiles": lockfiles}
                for lock in lockfiles.values():
                    stats["lockfiles"] += 1
                    for entry in lock.get("packages") or []:
                        eco, name, version = entry[0], entry[1], entry[2]
                        dev = bool(entry[3]) if len(entry) > 3 else False
                        places = inventory.setdefault((eco, name, version), {})
                        places[repo.name] = places.get(repo.name, True) and dev
        for name in list(repos_state):
            if name not in seen_repos:
                del repos_state[name]
        stats["packages"] = len(inventory)
        return inventory

    async def _osv_findings(
        self, inventory: dict[tuple[str, str, str], dict[str, bool]], state: dict[str, Any], stats: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if not inventory:
            return []
        keys = sorted(inventory)
        hits: dict[tuple[str, str, str], list[str]] = {}
        async with httpx.AsyncClient(base_url=OSV_API, timeout=HTTP_TIMEOUT) as client:
            for i in range(0, len(keys), OSV_BATCH):
                chunk = keys[i : i + OSV_BATCH]
                body = {
                    "queries": [
                        {"package": {"name": name, "ecosystem": eco}, "version": version} for eco, name, version in chunk
                    ]
                }
                response = await client.post("/querybatch", json=body)
                response.raise_for_status()
                for key, result in zip(chunk, response.json().get("results", []), strict=False):
                    ids = [v["id"] for v in (result or {}).get("vulns") or [] if v.get("id")]
                    if ids:
                        hits[key] = ids

            vuln_ids = sorted({vid for ids in hits.values() for vid in ids})
            stats["osv_vulns"] = len(vuln_ids)
            cache: dict[str, Any] = state.setdefault("osv_cache", {})
            sem = asyncio.Semaphore(8)

            async def fetch(vid: str) -> None:
                if vid in cache:
                    return
                async with sem:
                    r = await client.get(f"/vulns/{vid}")
                if r.status_code == 404:
                    cache[vid] = {}
                    return
                r.raise_for_status()
                cache[vid] = _compact_osv(r.json())

            await asyncio.gather(*(fetch(v) for v in vuln_ids))
            # Keep the cache bounded to what is still referenced.
            for vid in list(cache):
                if vid not in vuln_ids:
                    del cache[vid]

        findings: list[dict[str, Any]] = []
        for (eco, name, version), ids in hits.items():
            for vid in ids:
                vuln = cache.get(vid) or {}
                if not vuln:
                    continue
                fixed = _fixed_version(vuln, eco, name, version)
                for repo, dev in sorted(inventory[(eco, name, version)].items()):
                    findings.append({
                        "id": f"osv:{vid}:{eco}:{name}@{version}:{repo}",
                        "dev": dev,
                        "source": "osv",
                        "kind": "package",
                        "vuln_id": vid,
                        "severity": vuln.get("severity", "unknown"),
                        "score": vuln.get("score"),
                        "component": name,
                        "ecosystem": eco,
                        "version": version,
                        "fixed_in": fixed,
                        "repo": repo,
                        "title": vuln.get("summary") or vid,
                        "url": f"https://osv.dev/vulnerability/{vid}",
                        "published": vuln.get("published"),
                        "details": {"aliases": vuln.get("aliases", [])},
                    })
        return findings

    # --- watched products ------------------------------------------------------------

    def resolve_watch(self, item: Any) -> dict[str, Any]:
        """Merge a watchlist entry with the known-products defaults."""
        known = KNOWN_PRODUCTS.get(item.product, {})
        return {
            "product": item.product,
            "name": item.name or known.get("name") or item.product,
            "cpe": item.cpe or known.get("cpe"),
            "osv": known.get("osv"),
            "cycles": item.cycles or self.settings.cycles,
            "lts_only": item.lts_only if item.lts_only is not None else bool(known.get("lts_only")),
            "where": list(item.where),
        }

    async def fetch_cycles(self, client: httpx.AsyncClient, product: str) -> list[dict[str, Any]]:
        response = await client.get(f"{EOL_API}/{product}.json")
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    @staticmethod
    def pick_cycles(cycles: list[dict[str, Any]], count: int, lts_only: bool, today: date) -> list[dict[str, Any]]:
        """Newest ``count`` release lines (endoflife.date lists newest first).

        Lines dead for over a year are skipped: "the last two" of Apache httpd
        would otherwise include 2.2 (EOL 2017) just because 2.4 is the only
        live one.
        """
        picked: list[dict[str, Any]] = []
        for c in cycles:
            if lts_only and not c.get("lts"):
                continue
            if not c.get("latest"):
                continue
            view = _cycle_view(c, today)
            if view["days_left"] is not None and view["days_left"] < -365:
                continue
            picked.append(view)
            if len(picked) >= count:
                break
        return picked

    async def watchlist_status(self) -> list[dict[str, Any]]:
        """Release lines per watched product, for the view (no NVD calls)."""
        today = datetime.now(UTC).date()
        out: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            for item in self.settings.watchlist:
                watch = self.resolve_watch(item)
                try:
                    cycles = await self.fetch_cycles(client, watch["product"])
                    watch["cycles_info"] = self.pick_cycles(cycles, watch["cycles"], watch["lts_only"], today)
                except httpx.HTTPError as e:
                    watch["error"] = str(e)
                    watch["cycles_info"] = []
                out.append(watch)
        return out

    async def _watchlist_findings(self, stats: dict[str, Any]) -> list[dict[str, Any]]:
        today = datetime.now(UTC).date()
        findings: list[dict[str, Any]] = []
        eol_ok = nvd_ok = True
        nvd_calls = 0
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            for item in self.settings.watchlist:
                watch = self.resolve_watch(item)
                stats["products"] += 1
                try:
                    cycles = self.pick_cycles(
                        await self.fetch_cycles(client, watch["product"]), watch["cycles"], watch["lts_only"], today
                    )
                except httpx.HTTPError as e:
                    stats["errors"].append(f"endoflife {watch['product']}: {e}")
                    eol_ok = False
                    continue
                findings.extend(self._eol_findings(watch, cycles, today))
                if watch.get("osv"):
                    try:
                        findings.extend(await self._osv_product_findings(client, watch, cycles))
                    except httpx.HTTPError as e:
                        stats["errors"].append(f"osv {watch['product']}: {e}")
                elif watch.get("cpe"):
                    try:
                        if nvd_calls:
                            await asyncio.sleep(NVD_PAUSE_SECONDS)
                        nvd_calls += 1
                        findings.extend(await self._nvd_findings(client, watch, cycles, today))
                    except httpx.HTTPError as e:
                        stats["errors"].append(f"nvd {watch['product']}: {e}")
                        nvd_ok = False
        stats["sources"]["endoflife"] = "ok" if eol_ok else "error"
        stats["sources"]["nvd"] = "ok" if nvd_ok else "error"
        return findings

    def _eol_findings(self, watch: dict[str, Any], cycles: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for c in cycles:
            days = c.get("days_left")
            if days is None or days > self.settings.eol_warning_days:
                continue
            if days < 0:
                severity, title = "high", f"{watch['name']} {c['cycle']} ya no recibe parches (fin: {c['eol']})"
            elif days <= 90:
                severity, title = "medium", f"{watch['name']} {c['cycle']} deja de recibir parches en {days} días ({c['eol']})"
            else:
                severity, title = "low", f"{watch['name']} {c['cycle']} termina soporte el {c['eol']}"
            out.append({
                "id": f"eol:{watch['product']}:{c['cycle']}",
                "source": "endoflife",
                "kind": "eol",
                "vuln_id": None,
                "severity": severity,
                "component": watch["name"],
                "ecosystem": "product",
                "version": c["cycle"],
                "fixed_in": None,
                "repo": None,
                "title": title,
                "url": f"https://endoflife.date/{watch['product']}",
                "published": c.get("eol"),
                "details": {"latest": c.get("latest"), "where": watch["where"], "days_left": days},
            })
        return out

    async def _osv_product_findings(
        self, client: httpx.AsyncClient, watch: dict[str, Any], cycles: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        eco, name = watch["osv"]
        out: list[dict[str, Any]] = []
        for c in cycles:
            response = await client.post(
                f"{OSV_API}/query", json={"package": {"name": name, "ecosystem": eco}, "version": c["latest"]}
            )
            response.raise_for_status()
            for raw in response.json().get("vulns") or []:
                vuln = _compact_osv(raw)
                out.append({
                    "id": f"osv:{vuln['id']}:{eco}:{name}:{c['cycle']}",
                    "source": "osv",
                    "kind": "product",
                    "vuln_id": vuln["id"],
                    "severity": vuln.get("severity", "unknown"),
                    "score": vuln.get("score"),
                    "component": watch["name"],
                    "ecosystem": eco,
                    "version": c["latest"],
                    "fixed_in": _fixed_version(vuln, eco, name, c["latest"]),
                    "repo": None,
                    "title": vuln.get("summary") or vuln["id"],
                    "url": f"https://osv.dev/vulnerability/{vuln['id']}",
                    "published": vuln.get("published"),
                    "details": {"cycle": c["cycle"], "affects_latest": True, "where": watch["where"]},
                })
        return out

    async def _nvd_findings(
        self, client: httpx.AsyncClient, watch: dict[str, Any], cycles: list[dict[str, Any]], today: date
    ) -> list[dict[str, Any]]:
        # NVD rejects (404) windows longer than 120 days or ending in the future.
        end = datetime.now(UTC).replace(tzinfo=None)
        start = end - timedelta(days=min(self.settings.lookback_days, 119))
        params = {
            "virtualMatchString": watch["cpe"],
            "pubStartDate": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
            "pubEndDate": end.strftime("%Y-%m-%dT%H:%M:%S.000"),
            "resultsPerPage": 200,
        }
        response = await client.get(NVD_API, params=params)
        response.raise_for_status()
        out: list[dict[str, Any]] = []
        for entry in response.json().get("vulnerabilities") or []:
            cve = entry.get("cve") or {}
            out.extend(self._nvd_cve_findings(cve, watch, cycles))
        return out

    @staticmethod
    def _nvd_cve_findings(cve: dict[str, Any], watch: dict[str, Any], cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cve_id = cve.get("id")
        if not cve_id:
            return []
        severity, score = nvd_severity(cve)
        description = next((d.get("value") for d in cve.get("descriptions") or [] if d.get("lang") == "en"), "")
        out: list[dict[str, Any]] = []
        for c in cycles:
            affects = affects_latest = False
            fixed_in: str | None = None
            for conf in cve.get("configurations") or []:
                for node in conf.get("nodes") or []:
                    for match in node.get("cpeMatch") or []:
                        if not match.get("vulnerable") or not str(match.get("criteria", "")).startswith(watch["cpe"]):
                            continue
                        hit, latest_hit, fixed = cpe_match_touches_cycle(match, c["cycle"], c["latest"])
                        affects = affects or hit
                        affects_latest = affects_latest or latest_hit
                        fixed_in = fixed_in or fixed
            if not affects:
                continue
            out.append({
                "id": f"nvd:{cve_id}:{watch['product']}:{c['cycle']}",
                "source": "nvd",
                "kind": "product",
                "vuln_id": cve_id,
                "severity": severity,
                "score": score,
                "component": watch["name"],
                "ecosystem": "product",
                "version": c["cycle"] if affects_latest else f"{c['cycle']} < {fixed_in or c['latest']}",
                "fixed_in": None if affects_latest else (fixed_in or c["latest"]),
                "repo": None,
                "title": (description or cve_id)[:300],
                "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                "published": (cve.get("published") or "")[:10] or None,
                "details": {"cycle": c["cycle"], "affects_latest": affects_latest, "latest": c["latest"], "where": watch["where"]},
            })
        return out

    # --- AI note -----------------------------------------------------------------------

    async def _ai_note(self, today: str, only_new: bool = True) -> str | None:
        from executive_copilot.services.ai import AITextRequest

        rows = self.store.findings(states=("open",), limit=30, since=today if only_new else None, include_dev=False)
        if not rows:
            return None
        lines = []
        for f in rows:
            place = f" en {f['repo']}" if f.get("repo") else ""
            fix = f" → corregido en {f['fixed_in']}" if f.get("fixed_in") else ""
            lines.append(f"- [{f['severity']}] {f['component']} {f.get('version') or ''}{place}: {(f.get('title') or '')[:120]}{fix}")
        try:
            reply = await self._ai.generate_text(AITextRequest(  # type: ignore[union-attr]
                input=AI_NOTE_PROMPT.format(
                    findings="\n".join(lines), scope="nuevos de hoy" if only_new else "abiertos más graves"
                ),
                max_output_tokens=300,
                temperature=0.2,
            ))
        except Exception as e:
            logger.warning("Security AI note failed", error=str(e))
            return None
        return (reply.text or "").strip() or None

    # --- view ---------------------------------------------------------------------------

    async def overview(self, with_watchlist: bool = True) -> dict[str, Any]:
        from executive_copilot.automation.run_log import get_run_log

        state = self.load_state()
        runs = get_run_log().recent("security_scan", limit=1)
        data: dict[str, Any] = {
            "enabled": self.settings.enabled,
            "scan_repos": self.settings.scan_repos and self.config.github_docs.enabled,
            "cycles": self.settings.cycles,
            "use_ai": self.settings.use_ai,
            "summary": self.store.summary(),
            "findings": self.store.findings(states=("open", "reviewed"), limit=400),
            "ignored": self.store.findings(states=("ignored",), limit=100),
            "last_run": runs[0] if runs else None,
            "last_scan": state.get("last_scan"),
            "ai_note": state.get("ai_note"),
            "known_products": [
                {"product": key, "name": value["name"]} for key, value in KNOWN_PRODUCTS.items()
            ],
            "watchlist": [],
        }
        if with_watchlist:
            data["watchlist"] = await self.watchlist_status()
        else:
            data["watchlist"] = [self.resolve_watch(i) for i in self.settings.watchlist]
        return data


# --- helpers --------------------------------------------------------------------------


def _ignore_active(item: Any) -> bool:
    if not item.until:
        return True
    try:
        return date.fromisoformat(item.until) >= datetime.now(UTC).date()
    except ValueError:
        return True


def _cycle_view(c: dict[str, Any], today: date) -> dict[str, Any]:
    eol = c.get("eol")
    days_left: int | None = None
    eol_text: str | None = None
    if isinstance(eol, str):
        try:
            days_left = (date.fromisoformat(eol) - today).days
            eol_text = eol
        except ValueError:
            eol_text = eol
    elif eol is True:
        days_left = -1
        eol_text = "sí"
    return {
        "cycle": str(c.get("cycle")),
        "latest": str(c.get("latest") or ""),
        "latest_date": c.get("latestReleaseDate"),
        "eol": eol_text,
        "days_left": days_left,
        "lts": bool(c.get("lts")),
    }


def _compact_osv(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only what findings need from an OSV record."""
    db = raw.get("database_specific") or {}
    severity = normalize_severity(db.get("severity"))
    score: float | None = None
    if severity == "unknown":
        for aff in raw.get("affected") or []:
            eco_sev = (aff.get("ecosystem_specific") or {}).get("severity")
            if eco_sev:
                severity = normalize_severity(eco_sev)
                break
    affected = []
    for aff in raw.get("affected") or []:
        pkg = aff.get("package") or {}
        fixed = [
            ev["fixed"] for rng in aff.get("ranges") or [] for ev in rng.get("events") or [] if ev.get("fixed")
        ]
        affected.append({"ecosystem": pkg.get("ecosystem"), "name": pkg.get("name"), "fixed": fixed})
    return {
        "id": raw.get("id"),
        "summary": (raw.get("summary") or (raw.get("details") or "").split("\n", 1)[0])[:300],
        "severity": severity,
        "score": score,
        "aliases": raw.get("aliases") or [],
        "published": (raw.get("published") or "")[:10] or None,
        "affected": affected,
    }


def _fixed_version(vuln: dict[str, Any], eco: str, name: str, version: str) -> str | None:
    """Smallest fixed version above the installed one (else the newest fix)."""
    fixes: list[str] = []
    for aff in vuln.get("affected") or []:
        if aff.get("ecosystem") == eco and aff.get("name") == name:
            fixes.extend(aff.get("fixed") or [])
    if not fixes:
        return None
    above = sorted((f for f in fixes if version_lt(version, f)), key=version_key)
    return above[0] if above else sorted(fixes, key=version_key)[-1]
