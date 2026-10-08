"""Obsidian meeting notes: Gemini meeting notes -> dated sections in the vault.

Layout follows the existing vault convention (``Paycode/Reuniones``): one note
per meeting *series*, an H1 title and short intro, then one ``## YYYY-MM-DD``
section per meeting, newest first, a ``---`` footer and a
``## Personas relacionadas`` list of ``[[Person]]`` links.

Sources (both optional, deduplicated by note + date):

1. Gemini "Notes"/"Notas" Google Docs attached to Google Calendar events.
   Reading them needs the Drive read-only OAuth scope.
2. The e-mail Gemini sends after each meeting (``gemini-notes@google.com``),
   read through Gmail with the existing scopes.

Idempotency is file based: a meeting is skipped when its note already has a
section for that date, so hand-written notes are never duplicated.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from executive_copilot.logging import get_logger

logger = get_logger(__name__)

GEMINI_SENDER = "gemini-notes@google.com"
DATE_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
PEOPLE_HEADING = "## Personas relacionadas"
FOOTER = "---\n*Fuente: notas automáticas de Gemini (Google Meet).*"

SUMMARY_PROMPT = """Eres el asistente de un CTO. Resume estas notas automáticas de Gemini
de la reunión "{title}" ({day}) para su bóveda de Obsidian.

Devuelve SOLO un objeto JSON con estas claves:
- "resumen": 2 a 6 viñetas en español, una línea cada una, concretas (qué se
  trató, estado, bloqueos). Sin relleno ni frases genéricas.
- "decisiones": decisiones tomadas (puede ser []).
- "proximos_pasos": acciones con responsable, formato "Persona hace X" (puede ser []).
- "personas": nombres completos de las personas que participaron o se mencionan.

Notas:
{text}
"""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class MeetingSource:
    """Raw meeting notes from one source."""

    key: str
    source: str  # "calendar_attachment" | "gemini_email"
    title: str
    day: date
    text: str
    url: str | None = None
    attendees: list[str] = field(default_factory=list)


@dataclass
class MeetingSummary:
    """Structured summary rendered into a dated section."""

    bullets: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-z0-9]+", " ", no_accents.lower())
    return cleaned.strip()


def html_to_text(value: str) -> str:
    """Very small HTML -> text conversion good enough for e-mail bodies."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_meeting_title(title: str) -> str:
    """Remove Gemini decorations from a meeting/doc title.

    "Josue / Gonzalo - 2026/08/31 10:00 CST - Notes by Gemini" -> "Josue / Gonzalo"
    """
    t = re.sub(r"(?i)\s*[-–]\s*(notes by gemini|notas de gemini|notas por gemini).*$", "", title)
    t = re.sub(r"\s*[-–]\s*\d{4}/\d{2}/\d{2}.*$", "", t)
    return t.strip(" -–\"“”") or title.strip()


def gemini_title_from_subject(subject: str) -> str | None:
    """Extract the meeting title from a Gemini notes e-mail subject.

    Examples: 'Notes: “Weekly sync” Oct 6, 2026', 'Notas: "Reunión diaria" 6 oct 2026'.
    """
    match = re.search(r"[“\"«](.+?)[”\"»]", subject)
    if match:
        return match.group(1).strip()
    stripped = re.sub(r"(?i)^\s*(notes|notas)\s*:\s*", "", subject).strip()
    return stripped or None


def safe_note_name(title: str) -> str:
    """Turn a meeting title into a valid note filename (without .md)."""
    name = title.replace("/", " - ").replace("\\", " - ")
    name = re.sub(r'[:*?"<>|#^\[\]]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return name[:120] or "Reunion"


def resolve_note_name(
    title: str,
    existing: list[str],
    series_map: dict[str, str] | None = None,
) -> tuple[str, bool]:
    """Pick the note a meeting belongs to.

    Order: explicit ``series_map`` regex, exact normalized match, token-subset
    match against existing note names, otherwise a new note named after the
    meeting.

    Returns:
        (note name without .md, whether that note already exists)
    """
    existing_by_norm = {normalize(n): n for n in existing}

    for pattern, target in (series_map or {}).items():
        try:
            if re.search(pattern, title):
                found = existing_by_norm.get(normalize(target))
                return (found or target), found is not None
        except re.error:
            logger.warning("Invalid series_map regex", pattern=pattern)

    norm_title = normalize(clean_meeting_title(title))
    if norm_title in existing_by_norm:
        return existing_by_norm[norm_title], True

    title_tokens = set(norm_title.split())
    best: tuple[int, str] | None = None
    if len(title_tokens) >= 2:
        for norm, original in existing_by_norm.items():
            note_tokens = set(norm.split())
            if len(note_tokens) < 2:
                continue
            if title_tokens <= note_tokens or note_tokens <= title_tokens:
                score = len(title_tokens & note_tokens)
                if best is None or score > best[0]:
                    best = (score, original)
    if best:
        return best[1], True

    return safe_note_name(clean_meeting_title(title)), False


def has_section(note_text: str, day: date) -> bool:
    """Whether the note already has a section for this date."""
    return day.isoformat() in DATE_HEADING.findall(note_text)


def render_section(day: date, summary: MeetingSummary, source_url: str | None = None) -> str:
    """Render one dated section in the vault's style."""
    lines = [f"## {day.isoformat()}"]
    lines.extend(f"- {b.strip()}" for b in summary.bullets if b.strip())
    for decision in summary.decisions:
        if decision.strip():
            lines.append(f"- Decisión: {decision.strip()}")
    steps = [s.strip().rstrip(".") for s in summary.next_steps if s.strip()]
    if steps:
        lines.append(f"- Próximos pasos: {'; '.join(steps)}.")
    if source_url:
        lines.append(f"- Fuente: [Notas de Gemini]({source_url})")
    if len(lines) == 1:
        lines.append("- (Sin resumen disponible.)")
    return "\n".join(lines) + "\n"


def insert_section(note_text: str, day: date, section: str) -> tuple[str, bool]:
    """Insert a dated section keeping sections newest-first.

    Returns:
        (new text, inserted). Not inserted when the date already exists.
    """
    if has_section(note_text, day):
        return note_text, False

    for match in DATE_HEADING.finditer(note_text):
        if match.group(1) < day.isoformat():
            pos = match.start()
            return note_text[:pos] + section + "\n" + note_text[pos:], True

    # Older than every existing section (or none yet): before the footer.
    footer = re.search(r"^---\s*$", note_text, re.MULTILINE)
    people = note_text.find(PEOPLE_HEADING)
    candidates = [p for p in (footer.start() if footer else -1, people) if p >= 0]
    if candidates:
        pos = min(candidates)
        before = note_text[:pos].rstrip("\n")
        return before + "\n\n" + section + "\n" + note_text[pos:], True

    return note_text.rstrip("\n") + "\n\n" + section, True


def match_people(candidates: list[str], people_notes: list[str]) -> list[str]:
    """Map names/e-mails to existing person notes.

    A person note matches when its first name and at least one more of its
    name tokens appear in a candidate (parenthetical nicknames ignored).
    """
    matched: list[str] = []
    for note in people_notes:
        base = re.sub(r"\(.*?\)", "", note)
        base = base.split(" - ")[-1] if " - " in base else base
        tokens = normalize(base).split()
        if len(tokens) < 2:
            continue
        for cand in candidates:
            cand_text = cand.split("@")[0] if "@" in cand else cand
            cand_tokens = set(normalize(cand_text.replace(".", " ").replace("_", " ")).split())
            if tokens[0] in cand_tokens and any(t in cand_tokens for t in tokens[1:]):
                matched.append(note)
                break
    return matched


def ensure_people_links(note_text: str, names: list[str]) -> str:
    """Add ``[[Name]]`` links that are missing from 'Personas relacionadas'."""
    missing = [n for n in names if f"[[{n}]]" not in note_text]
    if not missing:
        return note_text
    links = "".join(f"- [[{n}]]\n" for n in missing)
    idx = note_text.find(PEOPLE_HEADING)
    if idx < 0:
        return note_text.rstrip("\n") + f"\n\n{PEOPLE_HEADING}\n{links}"
    next_heading = re.search(r"^#{1,2} ", note_text[idx + len(PEOPLE_HEADING):], re.MULTILINE)
    end = idx + len(PEOPLE_HEADING) + next_heading.start() if next_heading else len(note_text)
    block = note_text[idx:end].rstrip("\n") + "\n" + links
    rest = note_text[end:]
    return note_text[:idx] + block + (("\n" + rest) if rest else "")


def new_note_text(title: str, section: str, people: list[str]) -> str:
    """Create a new series note."""
    text = (
        f"# {title}\n\n"
        f"Notas de la reunión «{title}», generadas a partir de las notas "
        f"automáticas de Gemini (Google Meet).\n\n"
        f"{section}\n{FOOTER}\n"
    )
    if people:
        text = ensure_people_links(text, people)
    return text


def add_to_index(readme_text: str, note_name: str) -> tuple[str, bool]:
    """Append a note to README's '## Índice' list if missing."""
    match = re.search(r"^## [ÍI]ndice\s*$", readme_text, re.MULTILINE)
    if not match:
        return readme_text, False
    start = match.end()
    next_heading = re.search(r"^#{1,2} ", readme_text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(readme_text)
    block = readme_text[start:end]
    listed = {normalize(line[2:]) for line in block.splitlines() if line.startswith("- ")}
    if normalize(note_name) in listed:
        return readme_text, False
    new_block = block.rstrip("\n") + f"\n- {note_name}\n"
    rest = readme_text[end:]
    return readme_text[:start] + new_block + (("\n" + rest) if rest else ""), True


def _bullets_after(text: str, headings: list[str], limit: int = 6) -> list[str]:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if normalize(line) in headings:
            out: list[str] = []
            for nxt in lines[i + 1 :]:
                stripped = nxt.strip()
                if not stripped:
                    continue
                if normalize(stripped) in KNOWN_HEADINGS:
                    break
                out.append(stripped.lstrip("-*•☐ ").strip())
                if len(out) >= limit:
                    break
            return [o for o in out if o]
    return []


SUMMARY_HEADINGS = ["summary", "resumen"]
STEPS_HEADINGS = [
    "suggested next steps",
    "next steps",
    "proximos pasos sugeridos",
    "pasos siguientes sugeridos",
    "proximos pasos",
    "pasos siguientes",
]
KNOWN_HEADINGS = set(
    SUMMARY_HEADINGS
    + STEPS_HEADINGS
    + ["details", "detalles", "transcript", "transcripcion", "attachments", "archivos adjuntos"]
)


def parse_gemini_text(text: str) -> MeetingSummary:
    """Heuristic summary when no AI is available: reuse Gemini's own sections."""
    bullets = _bullets_after(text, SUMMARY_HEADINGS, limit=4)
    steps = _bullets_after(text, STEPS_HEADINGS, limit=8)
    if not bullets:
        paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 40]
        bullets = [re.sub(r"\s+", " ", p)[:300] for p in paragraphs[:3]]
    return MeetingSummary(bullets=bullets, next_steps=steps)


def parse_ai_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating ``` fences and prose around it."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = min((i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        return json.loads(cleaned[start : end + 1])


# ---------------------------------------------------------------------------
# Vault access
# ---------------------------------------------------------------------------


class MeetingNotesVault:
    """File access to the meetings (and people) folders of an Obsidian vault."""

    def __init__(self, vault_path: Path, meetings_folder: str, people_folder: str | None) -> None:
        self.vault_path = vault_path
        self.meetings_dir = vault_path / meetings_folder
        self.people_dir = vault_path / people_folder if people_folder else None

    def check(self) -> str | None:
        """Return an error message when the vault is not usable."""
        if not self.vault_path.exists():
            return (
                f"Obsidian vault not found at {self.vault_path}. In Docker, set "
                "OBSIDIAN_VAULT_PATH in .env and recreate the container."
            )
        if not self.meetings_dir.exists():
            return f"Meetings folder not found: {self.meetings_dir}"
        return None

    def note_names(self) -> list[str]:
        return sorted(p.stem for p in self.meetings_dir.glob("*.md") if p.stem != "README")

    def people_names(self) -> list[str]:
        if not self.people_dir or not self.people_dir.exists():
            return []
        return sorted(p.stem for p in self.people_dir.glob("*.md"))

    def path(self, name: str) -> Path:
        return self.meetings_dir / f"{name}.md"

    def read(self, name: str) -> str | None:
        p = self.path(name)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def write(self, name: str, text: str) -> None:
        p = self.path(name)
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)

    def find(self, name: str) -> str | None:
        """Resolve a note by exact, normalized or partial name."""
        names = self.note_names()
        if name in names:
            return name
        norm = normalize(name)
        for n in names:
            if normalize(n) == norm:
                return n
        partial = [n for n in names if norm and norm in normalize(n)]
        return partial[0] if partial else None

    def list_notes(self) -> list[dict[str, Any]]:
        notes = []
        for name in self.note_names():
            text = self.read(name) or ""
            dates = DATE_HEADING.findall(text)
            notes.append({
                "name": name,
                "meetings": len(dates),
                "latest": max(dates) if dates else None,
                "modified": datetime.fromtimestamp(self.path(name).stat().st_mtime, UTC).isoformat(),
            })
        notes.sort(key=lambda n: n["latest"] or "", reverse=True)
        return notes

    def search(self, query: str, limit: int = 20, since: date | None = None) -> list[dict[str, Any]]:
        """Accent/case-insensitive search across dated meeting sections.

        Every query term must appear in a section for it to match.
        """
        terms = normalize(query).split()
        results: list[dict[str, Any]] = []
        for name in self.note_names():
            text = self.read(name) or ""
            for day, body in _split_sections(text):
                if since and day and day < since.isoformat():
                    continue
                norm_body = normalize(body)
                if terms and not all(t in norm_body or t in normalize(name) for t in terms):
                    continue
                hits = [
                    line.strip()
                    for line in body.splitlines()
                    if line.strip() and any(t in normalize(line) for t in terms)
                ]
                results.append({
                    "note": name,
                    "date": day,
                    "matches": hits[:5] or [line.strip() for line in body.splitlines() if line.strip()][:3],
                })
        results.sort(key=lambda r: r["date"] or "", reverse=True)
        return results[:limit]


def _split_sections(text: str) -> list[tuple[str | None, str]]:
    """Split a note into (date, body) pairs; content before the first date is (None, ...)."""
    out: list[tuple[str | None, str]] = []
    matches = list(DATE_HEADING.finditer(text))
    if not matches:
        return [(None, text)]
    if text[: matches[0].start()].strip():
        out.append((None, text[: matches[0].start()]))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end]
        body = body.split("\n---")[0]
        out.append((m.group(1), body))
    return out


# ---------------------------------------------------------------------------
# Sync service
# ---------------------------------------------------------------------------


class MeetingNotesSyncService:
    """Collect Gemini meeting notes and write them into the Obsidian vault."""

    def __init__(
        self,
        config: Any,
        credential_manager: Any,
        ai_service: Any | None = None,
    ) -> None:
        self.config = config
        self.credential_manager = credential_manager
        self._ai = ai_service
        obs = config.obsidian
        self.tz = ZoneInfo(config.calendar_defaults.timezone)
        self.account = obs.calendar_account or config.calendar_defaults.account_id
        self.vault = (
            MeetingNotesVault(Path(obs.vault_path), obs.meetings_folder, obs.people_folder)
            if obs.vault_path
            else None
        )

    def vault_error(self) -> str | None:
        if not self.config.obsidian.enabled:
            return "Obsidian sync is disabled (obsidian.enabled: false)"
        if self.vault is None:
            return "Obsidian vault path not configured (set OBSIDIAN_VAULT_PATH in .env)"
        return self.vault.check()

    def require_vault(self) -> MeetingNotesVault:
        """The usable vault, or ValueError explaining what is missing."""
        error = self.vault_error()
        if error or self.vault is None:
            raise ValueError(error or "Obsidian vault not configured")
        return self.vault

    async def sync(self, days_back: int = 7, dry_run: bool = False) -> dict[str, Any]:
        """Write new meeting sections for the last ``days_back`` days."""
        error = self.vault_error()
        if error:
            return {"error": error}
        if not self.account:
            return {"error": "No calendar account (obsidian.calendar_account / calendar_defaults.account_id)"}
        assert self.vault is not None

        sources, source_errors = await self.collect_sources(days_back)
        existing = self.vault.note_names()
        people_notes = self.vault.people_names()
        skip = [re.compile(p) for p in self.config.obsidian.skip_titles]

        result: dict[str, Any] = {
            "account": self.account,
            "days_back": days_back,
            "dry_run": dry_run,
            "sources_found": len(sources),
            "written": [],
            "skipped": [],
            "new_notes": [],
            "errors": source_errors,
        }
        seen: set[tuple[str, str]] = set()

        for src in sorted(sources, key=lambda s: (s.day, s.source != "calendar_attachment")):
            if any(p.search(src.title) for p in skip):
                result["skipped"].append({"title": src.title, "date": src.day.isoformat(), "reason": "skip_titles"})
                continue
            name, exists = resolve_note_name(src.title, existing, self.config.obsidian.series_map)
            key = (normalize(name), src.day.isoformat())
            if key in seen:
                continue
            seen.add(key)
            current = self.vault.read(name) if exists else None
            if current and has_section(current, src.day):
                result["skipped"].append({"title": src.title, "note": name, "date": src.day.isoformat(), "reason": "already in note"})
                continue

            try:
                summary = await self.summarize(src)
            except Exception as e:  # never lose the whole run for one meeting
                result["errors"].append({"title": src.title, "error": str(e)})
                continue

            people = match_people(summary.people + src.attendees, people_notes)
            section = render_section(src.day, summary, src.url)
            entry = {"title": src.title, "note": name, "date": src.day.isoformat(), "source": src.source, "people": people}

            if not dry_run:
                if current is None:
                    self.vault.write(name, new_note_text(name, section, people))
                    existing.append(name)
                    result["new_notes"].append(name)
                    if self.config.obsidian.update_index:
                        self._add_to_index(name)
                else:
                    text, _ = insert_section(current, src.day, section)
                    self.vault.write(name, ensure_people_links(text, people))
            else:
                entry["preview"] = section
                if current is None:
                    result["new_notes"].append(name)
            result["written"].append(entry)

        return result

    def _add_to_index(self, name: str) -> None:
        assert self.vault is not None
        readme = self.vault.meetings_dir / "README.md"
        if readme.exists():
            text, changed = add_to_index(readme.read_text(encoding="utf-8"), name)
            if changed:
                readme.write_text(text, encoding="utf-8")

    async def collect_sources(self, days_back: int) -> tuple[list[MeetingSource], list[dict[str, str]]]:
        """Gather Gemini notes from calendar attachments and Gemini e-mails."""
        sources: list[MeetingSource] = []
        errors: list[dict[str, str]] = []
        for label, fetch in (
            ("calendar_attachment", self._from_calendar),
            ("gemini_email", self._from_gmail),
        ):
            try:
                sources.extend(await fetch(days_back))
            except Exception as e:
                errors.append({"source": label, "error": str(e)})
        return sources, errors

    async def _from_calendar(self, days_back: int) -> list[MeetingSource]:
        from executive_copilot.providers.calendar.google import GoogleCalendarProvider
        from executive_copilot.providers.calendar.interface import EventQuery

        provider = GoogleCalendarProvider(self.credential_manager, self.account)
        now = datetime.now(UTC)
        events, _ = await provider.get_events(
            EventQuery(start_time=now - timedelta(days=days_back), end_time=now, limit=250)
        )
        out: list[MeetingSource] = []
        drive_denied = False
        for event in events:
            for att in event.transcript_attachments:
                if drive_denied:
                    break
                if att.mime_type and att.mime_type != "application/vnd.google-apps.document":
                    continue  # recordings etc. cannot be exported as text
                content = await provider.get_attachment_content(att.file_id)
                if content is None:
                    # Usually a missing Drive scope: stop hammering, fall back to e-mail.
                    drive_denied = True
                    break
                out.append(MeetingSource(
                    key=att.file_id,
                    source="calendar_attachment",
                    title=event.title,
                    day=event.start_time.astimezone(self.tz).date(),
                    text=content,
                    url=att.file_url,
                    attendees=[
                        a.display_name or a.email for a in getattr(event, "attendees", []) or []
                    ],
                ))
        if drive_denied and not out:
            raise RuntimeError(
                "Could not read Gemini notes from Google Drive (the Google OAuth app "
                "lacks the drive.readonly scope); using Gemini e-mails instead"
            )
        return out

    async def _from_gmail(self, days_back: int) -> list[MeetingSource]:
        from executive_copilot.providers.email.gmail import GmailProvider

        provider = GmailProvider(self.credential_manager, self.account)
        emails = await provider.search(f"from:{GEMINI_SENDER} newer_than:{days_back}d", max_results=100)
        out: list[MeetingSource] = []
        for email in emails:
            title = gemini_title_from_subject(email.subject or "")
            if not title:
                continue
            body = email.body_text or html_to_text(email.body_html or "") or email.snippet or ""
            out.append(MeetingSource(
                key=email.provider_ref.external_id if email.provider_ref else email.subject,
                source="gemini_email",
                title=title,
                day=email.received_at.astimezone(self.tz).date(),
                text=body,
                url=_first_doc_link(email.body_html or body),
            ))
        return out

    async def summarize(self, src: MeetingSource) -> MeetingSummary:
        """Summarize with the server AI, falling back to Gemini's own sections."""
        if self._ai is not None:
            from executive_copilot.services.ai import AITextRequest

            try:
                reply = await self._ai.generate_text(AITextRequest(
                    input=SUMMARY_PROMPT.format(title=src.title, day=src.day.isoformat(), text=src.text[:20000]),
                    max_output_tokens=1200,
                    temperature=0.2,
                ))
                data = parse_ai_json(reply.text)
                if isinstance(data, dict):
                    return MeetingSummary(
                        bullets=[str(x) for x in data.get("resumen", [])][:6],
                        decisions=[str(x) for x in data.get("decisiones", [])][:5],
                        next_steps=[str(x) for x in data.get("proximos_pasos", [])][:8],
                        people=[str(x) for x in data.get("personas", [])][:20],
                    )
            except Exception as e:
                logger.warning("AI summary failed; using Gemini sections", title=src.title, error=str(e))
        return parse_gemini_text(src.text)


def _first_doc_link(text: str) -> str | None:
    match = re.search(r"https://docs\.google\.com/document/d/[\w-]+[^\s\"'<>)]*", text)
    return match.group(0) if match else None
