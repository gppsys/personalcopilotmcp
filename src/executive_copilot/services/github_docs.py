"""Mirror the markdown docs of GitHub repositories so the assistant can search them.

Docs are written to a local cache (``github_docs.cache_dir``, gitignored) laid
out as ``<org>/<repo>/<path>.md`` and indexed by the vault index under the
``GitHub/`` prefix, so ``search_vault`` / ``read_vault_note`` cover them.

Each repository also gets a card (``<org>/<repo>/_repositorio.md``) with its
metadata, an optional AI summary of what it does, and the list of its docs.

Sync is incremental: repositories whose ``pushed_at`` did not change are not
read again, and inside a changed repository only files whose blob sha changed
are downloaded. State lives in ``manifest.json`` next to the mirror.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from executive_copilot.logging import get_logger
from executive_copilot.providers.code import GitHubProvider, RepoInfo, TreeEntry
from executive_copilot.services.vault_index import GITHUB_CARD

logger = get_logger(__name__)

MANIFEST = "manifest.json"
DOC_SUFFIXES = (".md", ".mdx", ".markdown")
# Third-party or generated content: READMEs of vendored plugins, build output...
SKIP_DIRS = {
    "node_modules", "vendor", "bower_components", "third_party", "dist", "build", "target",
    "out", "pods", ".github", ".venv", "venv", "site-packages", "public", "assets", "plugins",
    "static", "coverage", ".next", ".nuxt",
}
SKIP_FILES = ("changelog", "changes", "history", "license", "licence", "code_of_conduct")
SUMMARY_INPUT_CHARS = 16000
OUTLINE_CHARS = 3000
# Part of the summary hash: bump it when the prompt or context changes so cards regenerate.
SUMMARY_VERSION = 3
HEADING = re.compile(r"^#{1,4}\s+(.+?)\s*#*\s*$", re.MULTILINE)

SUMMARY_PROMPT = """Eres el asistente del CTO de {owner}. Explica qué es el repositorio
"{name}" a partir de su información y documentación.

Responde en español con exactamente estas cuatro partes, cada una empezando
con su etiqueta en negritas (sin títulos con #, sin bloques de código):
**Qué hace:** 1 a 3 frases concretas (qué producto o proceso de negocio sirve y para quién).
**Componentes y stack:** viñetas cortas con piezas funcionales (servicios, apps, módulos,
frameworks, infraestructura). No listes archivos sueltos ni carpetas de configuración.
**Integraciones:** viñetas con los sistemas externos, bancos, procesadores, APIs u otros
repos de {owner} con los que se conecta. Revisa también los títulos de los documentos.
Si de verdad no hay evidencia, escribe "No se menciona".
**Estado:** activo, en desarrollo, legado o experimento, con la evidencia (p. ej. último push).

Usa solo lo que dice la información. Si algo no se sabe, dilo; no inventes.

Información:
{context}
"""


def is_doc_path(path: str, size: int, max_kb: int, extra_excludes: list[str] | None = None) -> bool:
    """Whether a repository file is first-party markdown documentation worth indexing."""
    if not path.lower().endswith(DOC_SUFFIXES) or size > max_kb * 1024:
        return False
    parts = path.split("/")
    if any(part.lower() in SKIP_DIRS or part.startswith(".") for part in parts[:-1]):
        return False
    if parts[-1].lower().startswith(SKIP_FILES):
        return False
    return not any(fnmatch(path, pattern) for pattern in extra_excludes or [])


def local_name(path: str) -> str:
    """Mirror file name: always ``.md`` so the vault index picks it up."""
    stem, _, _ = path.rpartition(".")
    return f"{stem}.md"


def clean_summary(text: str) -> str:
    """Keep the summary inside the card's "## Resumen" section.

    Models sometimes wrap the answer in code fences, repeat the title as an H1
    or turn the labels into headings; headings would split the card into
    separate index sections.
    """
    text = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text.strip())
    lines = []
    for line in text.splitlines():
        if re.match(r"^#\s", line):
            continue
        heading = re.match(r"^#{2,6}\s+\**(.+?)\**\s*:?\s*$", line)
        lines.append(f"**{heading.group(1).rstrip(':')}:**" if heading else line)
    return "\n".join(lines).strip()


def summary_part(summary: str, label: str) -> str:
    """The text after a bold label (``**Qué hace:**``) up to the next label."""
    match = re.search(rf"\*\*{re.escape(label)}:\*\*\s*(.*?)(?=\n\s*\*\*[^*\n]+:\*\*|\Z)", summary, re.DOTALL)
    return match.group(1).strip() if match else ""


def render_card(
    repo: RepoInfo,
    languages: dict[str, int],
    docs: list[str],
    summary: str | None,
    top_level: list[str],
) -> str:
    """The repository card: metadata, summary and the list of its docs."""
    total = sum(languages.values()) or 1
    langs = ", ".join(f"{name} {bytes_ * 100 / total:.0f}%" for name, bytes_ in sorted(languages.items(), key=lambda kv: -kv[1])[:6])
    lines = [
        f"# Repositorio {repo.full_name}",
        "",
        repo.description or "_Sin descripción en GitHub._",
        "",
        "## Ficha",
        f"- URL: {repo.url}",
        f"- Lenguajes: {langs or repo.language or 'sin datos'}",
        f"- Rama por defecto: {repo.default_branch or '–'}",
        f"- Último push: {repo.pushed_at.date().isoformat() if repo.pushed_at else '–'}",
        f"- Visibilidad: {'privado' if repo.private else 'público'}{' · archivado' if repo.archived else ''}",
    ]
    if repo.topics:
        lines.append(f"- Temas: {', '.join(repo.topics)}")
    if top_level:
        lines.append(f"- Estructura: {', '.join(top_level[:25])}")
    if summary:
        lines += ["", "## Resumen", summary.strip()]
    lines += ["", "## Documentos"]
    lines += [f"- {d}" for d in docs] or ["- (sin documentación en Markdown)"]
    return "\n".join(lines) + "\n"


def summary_context(repo: RepoInfo, languages: dict[str, int], top_level: list[str], docs: dict[str, str]) -> str:
    """What the model sees: metadata, structure, the README in full and the start of other docs."""
    parts = [
        f"Nombre: {repo.full_name}",
        f"Descripción: {repo.description or '-'}",
        f"Lenguajes: {', '.join(languages) or repo.language or '-'}",
        f"Último push: {repo.pushed_at.date().isoformat() if repo.pushed_at else '-'}",
        f"Carpetas y archivos de primer nivel: {', '.join(top_level[:40]) or '-'}",
    ]
    readme = next((p for p in docs if p.lower() in ("readme.md", "readme.mdx", "readme.markdown")), None)
    if readme is None:
        # No README: the biggest top-level doc (PROJECT.md, AGENTS.md...) usually plays its role.
        top = [p for p in docs if "/" not in p and p != GITHUB_CARD]
        readme = max(top, key=lambda p: len(docs[p]), default=None)
    outline = [
        f"- {path}: {' · '.join(HEADING.findall(text)[:12])}" for path, text in docs.items() if path != readme
    ]
    if outline:
        parts.append("\nTítulos de los documentos:\n" + "\n".join(outline)[:OUTLINE_CHARS])
    budget = SUMMARY_INPUT_CHARS - sum(len(p) for p in parts)
    if readme:
        text = docs[readme][: budget // 2]
        parts.append(f"\n--- {readme} ---\n{text}")
        budget -= len(text)
    for path, text in docs.items():
        if path == readme or budget <= 0:
            continue
        snippet = text[: min(1000, budget)]
        parts.append(f"\n--- {path} (inicio) ---\n{snippet}")
        budget -= len(snippet)
    return "\n".join(parts)


class GitHubDocsSync:
    """Keep the local mirror of repository docs up to date."""

    def __init__(self, config: Any, credential_manager: Any, ai_service: Any | None = None) -> None:
        self.config = config
        self.settings = config.github_docs
        self.provider = GitHubProvider(credential_manager, self.settings.account)
        self.root = Path(self.settings.cache_dir)
        self._ai = ai_service

    # --- state ------------------------------------------------------------------

    def _load_manifest(self) -> dict[str, Any]:
        try:
            data: dict[str, Any] = json.loads((self.root / MANIFEST).read_text(encoding="utf-8"))
            return data
        except (OSError, json.JSONDecodeError):
            return {"repos": {}}

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{MANIFEST}.tmp"
        tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.root / MANIFEST)

    def status(self) -> dict[str, Any]:
        """Mirror size and last sync, for the status console."""
        manifest = self._load_manifest()
        repos = manifest.get("repos", {})
        return {
            "repos": len(repos),
            "files": sum(len(r.get("files", {})) for r in repos.values()),
            "summaries": sum(1 for r in repos.values() if r.get("summary_hash")),
            "last_sync": manifest.get("last_sync"),
            "last_error": manifest.get("last_error"),
        }

    def profiles(self) -> list[dict[str, Any]]:
        """What the relationship map needs per mirrored repository (no API calls)."""
        out = []
        for full_name, state in self._load_manifest().get("repos", {}).items():
            owner, _, name = full_name.partition("/")
            card = self.root / owner / name / GITHUB_CARD
            text = card.read_text(encoding="utf-8") if card.exists() else ""
            language = re.search(r"^- Lenguajes: ([^,\n%]+?)(?: \d+%)?(?:,|$)", text, re.MULTILINE)
            summary = state.get("summary") or ""
            out.append({
                "owner": owner,
                "name": name,
                "description": state.get("description"),
                "pushed_at": state.get("pushed_at"),
                "language": language.group(1).strip() if language else None,
                "url": f"https://github.com/{full_name}",
                "what": summary_part(summary, "Qué hace"),
                "integrations": summary_part(summary, "Integraciones"),
                "docs": len(state.get("files", {})),
            })
        return out

    # --- sync -------------------------------------------------------------------

    def _wanted(self, repo: RepoInfo) -> bool:
        s = self.settings
        if repo.archived and not s.include_archived:
            return False
        if s.include_repos and repo.name not in s.include_repos:
            return False
        return repo.name not in s.exclude_repos

    async def sync(self, force: bool = False) -> dict[str, Any]:
        """Mirror new and changed docs of every wanted repository."""
        if not self.settings.enabled:
            return {"error": "GitHub docs sync is disabled (github_docs.enabled: false)"}
        if not self.settings.orgs:
            return {"error": "No organizations configured (github_docs.orgs)"}

        manifest = self._load_manifest()
        known: dict[str, Any] = manifest.setdefault("repos", {})
        stats: dict[str, Any] = {
            "repos_seen": 0, "repos_changed": 0, "files_written": 0, "files_removed": 0,
            "summaries": 0, "repos_removed": 0, "errors": [],
        }
        seen: set[str] = set()

        for org in self.settings.orgs:
            try:
                repos = [r for r in await self.provider.list_repos(org) if self._wanted(r)]
            except Exception as e:
                manifest["last_error"] = f"{org}: {e}"
                self._save_manifest(manifest)
                return {"error": f"Could not list repositories of {org}: {e}"}
            for repo in repos:
                seen.add(repo.full_name)
                stats["repos_seen"] += 1
                state = known.get(repo.full_name, {})
                pushed = repo.pushed_at.isoformat() if repo.pushed_at else None
                unchanged = state and state.get("pushed_at") == pushed and state.get("description") == repo.description
                if unchanged and not force:
                    continue
                try:
                    changed = await self._sync_repo(repo, state, stats)
                except Exception as e:  # one repository never stops the run
                    logger.warning("GitHub repo sync failed", repo=repo.full_name, error=str(e))
                    stats["errors"].append({"repo": repo.full_name, "error": str(e)[:300]})
                    continue
                known[repo.full_name] = changed | {"pushed_at": pushed, "description": repo.description}
                stats["repos_changed"] += 1

        for gone in set(known) - seen:  # deleted, archived or excluded since last time
            shutil.rmtree(self.root / gone, ignore_errors=True)
            known.pop(gone)
            stats["repos_removed"] += 1

        manifest["last_sync"] = datetime.now(UTC).isoformat()
        manifest["last_error"] = stats["errors"][0]["error"] if stats["errors"] else None
        self._save_manifest(manifest)
        return stats

    async def _sync_repo(self, repo: RepoInfo, state: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
        s = self.settings
        tree = await self.provider.get_tree(repo)
        docs: list[TreeEntry] = [e for e in tree if is_doc_path(e.path, e.size, s.max_file_kb, s.exclude_paths)]
        old_files: dict[str, str] = state.get("files", {})
        new_files = {e.path: e.sha for e in docs}
        base = self.root / repo.owner / repo.name

        texts: dict[str, str] = {}
        for entry in docs:
            target = base / local_name(entry.path)
            if old_files.get(entry.path) == entry.sha and target.exists():
                texts[entry.path] = target.read_text(encoding="utf-8")
                continue
            text = await self.provider.get_text(repo, entry.sha)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            texts[entry.path] = text
            stats["files_written"] += 1
        for path in set(old_files) - set(new_files):
            (base / local_name(path)).unlink(missing_ok=True)
            stats["files_removed"] += 1

        top_level = sorted({e.path.split("/")[0] + ("/" if "/" in e.path else "") for e in tree})
        languages = await self.provider.get_languages(repo)

        summary_hash = hashlib.sha256(
            json.dumps([SUMMARY_VERSION, repo.description, sorted(new_files.items())], ensure_ascii=False).encode()
        ).hexdigest()[:16]
        summary = state.get("summary")
        if s.summarize and self._ai is not None and (summary_hash != state.get("summary_hash") or not summary):
            summary = await self._summarize(repo, languages, top_level, texts)
            if summary:
                stats["summaries"] += 1
        base.mkdir(parents=True, exist_ok=True)
        (base / GITHUB_CARD).write_text(
            render_card(repo, languages, sorted(new_files), summary, top_level), encoding="utf-8"
        )
        return {
            "files": new_files,
            "summary": summary,
            "summary_hash": summary_hash if summary else None,
        }

    async def _summarize(
        self, repo: RepoInfo, languages: dict[str, int], top_level: list[str], texts: dict[str, str]
    ) -> str | None:
        from executive_copilot.services.ai import AITextRequest

        context = summary_context(repo, languages, top_level, texts)
        try:
            reply = await self._ai.generate_text(AITextRequest(  # type: ignore[union-attr]
                input=SUMMARY_PROMPT.format(owner=repo.owner, name=repo.name, context=context),
                max_output_tokens=700,
                temperature=0.2,
            ))
        except Exception as e:
            logger.warning("Repo summary failed", repo=repo.full_name, error=str(e))
            return None
        return clean_summary(reply.text or "") or None
