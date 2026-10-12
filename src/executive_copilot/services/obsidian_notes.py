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

import yaml

from executive_copilot.logging import get_logger

logger = get_logger(__name__)

GEMINI_SENDER = "gemini-notes@google.com"
DATE_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
ORGANIZATION_KEY = "organizacion"
PEOPLE_HEADING = "## Personas relacionadas"
FOOTER = "---\n*Fuente: notas automáticas de Gemini (Google Meet).*"
TRANSCRIPT_TAG = "transcripcion"
TRANSCRIPT_LINK = re.compile(r"^- Transcripci[oó]n: \[\[", re.MULTILINE)

SUMMARY_PROMPT = """Eres el asistente de un CTO. Resume estas notas automáticas de Gemini
de la reunión "{title}" ({day}) para su bóveda de Obsidian.

Devuelve SOLO un objeto JSON con estas claves:
- "resumen": 2 a 6 viñetas en español, una línea cada una, concretas (qué se
  trató, estado, bloqueos). Sin relleno ni frases genéricas.
- "decisiones": decisiones tomadas (puede ser []).
- "proximos_pasos": acciones con responsable, formato "Persona hace X" (puede ser []).
- "personas": nombres completos de las personas que participaron o se mencionan.

Usa solo lo que dicen las notas. Si no hay contenido sustantivo (solo
asistentes o saludos), devuelve listas vacías: no inventes temas.

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
    transcript: str | None = None  # cleaned verbatim transcript, when the Doc has one


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


def catch_up_days(latest: date | None, today: date, minimum: int, maximum: int = 60) -> int:
    """Days to look back so nothing is missed since the newest synced meeting.

    Never less than ``minimum`` (the normal window) and never more than
    ``maximum`` (Gemini e-mails / API cost). One extra day covers a meeting
    that was synced only partially on its last day.
    """
    if latest is None:
        return minimum
    return max(minimum, min(maximum, (today - latest).days + 1))


def has_section(note_text: str, day: date) -> bool:
    """Whether the note already has a section for this date."""
    return day.isoformat() in DATE_HEADING.findall(note_text)


def render_section(
    day: date,
    summary: MeetingSummary,
    source_url: str | None = None,
    transcript_note: str | None = None,
) -> str:
    """Render one dated section in the vault's style.

    ``transcript_note`` is the vault-relative path (without .md) of the note
    holding the verbatim transcript, linked from the section when given.
    """
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
    if transcript_note:
        lines.append(transcript_link_line(transcript_note))
    if len(lines) == 1:
        lines.append("- (Sin resumen disponible.)")
    return "\n".join(lines) + "\n"


def transcript_link_line(transcript_note: str) -> str:
    """The bullet that links a dated section to its transcript note."""
    return f"- Transcripción: [[{transcript_note}|Transcripción]]"


def section_span(note_text: str, day: date) -> tuple[int, int] | None:
    """(start, end) offsets of the body of the ``## day`` section, or None.

    The body ends at the next heading, the footer rule or the end of the note.
    """
    heading = re.compile(rf"^## {re.escape(day.isoformat())}\s*$", re.MULTILINE).search(note_text)
    if not heading:
        return None
    start = heading.end()
    stop = re.compile(r"^(#{1,2} |---\s*$)", re.MULTILINE).search(note_text, start)
    return start, (stop.start() if stop else len(note_text))


def add_transcript_link(note_text: str, day: date, transcript_note: str) -> tuple[str, bool]:
    """Append the transcript bullet to an existing dated section (backfill).

    Returns (text, changed); unchanged when the section is missing or already
    links a transcript.
    """
    span = section_span(note_text, day)
    if span is None:
        return note_text, False
    start, end = span
    body = note_text[start:end]
    if TRANSCRIPT_LINK.search(body):
        return note_text, False
    new_body = body.rstrip("\n") + "\n" + transcript_link_line(transcript_note) + "\n"
    if end < len(note_text):
        new_body += "\n"
    return note_text[:start] + new_body + note_text[end:], True


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
    body_start = frontmatter_end(note_text)
    footer = re.compile(r"^---\s*$", re.MULTILINE).search(note_text, body_start)
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


def frontmatter_end(note_text: str) -> int:
    """Offset where the body starts (0 when the note has no YAML frontmatter)."""
    match = FRONTMATTER.match(note_text)
    return match.end() if match else 0


def tag_slug(name: str) -> str:
    """Obsidian-friendly tag from a name: 'Paycode' -> 'paycode', 'Banco Azteca' -> 'banco-azteca'."""
    return re.sub(r"[^a-z0-9_/-]+", "-", normalize(name)).strip("-")


def organization_for_account(config: Any, account: str | None) -> str | None:
    """Name of the organization that owns ``account`` (explicit mapping first, then e-mail domain)."""
    if not account:
        return None
    orgs = getattr(config, "organizations", None) or []
    for org in orgs:
        if any(m.account_id.lower() == account.lower() for m in org.provider_accounts):
            return str(org.name)
    if "@" in account:
        domain = account.rsplit("@", 1)[1].lower()
        for org in orgs:
            if any(d.lower() == domain for d in org.domains):
                return str(org.name)
    return None


def meeting_tags(base_tags: list[str], organization: str | None) -> list[str]:
    """Tags of a meeting note: the configured base tags plus the organization's."""
    tags = [tag_slug(t) for t in base_tags if tag_slug(t)]
    if organization and tag_slug(organization) not in tags:
        tags.append(tag_slug(organization))
    return tags


def ensure_frontmatter(note_text: str, tags: list[str], organization: str | None) -> tuple[str, bool]:
    """Make sure the note's YAML frontmatter carries ``tags`` and the organization.

    Existing frontmatter keys are kept; unparsable frontmatter is left alone.
    Returns (text, changed).
    """
    if not tags and not organization:
        return note_text, False
    match = FRONTMATTER.match(note_text)
    data: dict[str, Any] = {}
    if match:
        try:
            loaded = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return note_text, False
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            return note_text, False
        data = dict(loaded)

    current = data.get("tags") or []
    if isinstance(current, str):
        current = [t for t in re.split(r"[,\s]+", current) if t]
    current = [str(t).lstrip("#") for t in current]
    merged = current + [t for t in tags if t not in current]
    changed = merged != current
    if merged:
        data["tags"] = merged
    if organization and data.get(ORGANIZATION_KEY) != organization:
        data[ORGANIZATION_KEY] = organization
        changed = True
    if not changed:
        return note_text, False

    block = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip("\n")
    body = note_text[match.end():] if match else note_text
    return f"---\n{block}\n---\n{body}", True


def new_note_text(
    title: str,
    section: str,
    people: list[str],
    tags: list[str] | None = None,
    organization: str | None = None,
) -> str:
    """Create a new series note."""
    text = (
        f"# {title}\n\n"
        f"Notas de la reunión «{title}», generadas a partir de las notas "
        f"automáticas de Gemini (Google Meet).\n\n"
        f"{section}\n{FOOTER}\n"
    )
    if people:
        text = ensure_people_links(text, people)
    text, _ = ensure_frontmatter(text, tags or [], organization)
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


TRANSCRIPT_HEADING = re.compile(r"^\W*(transcripci[oó]n|transcript)\s*$", re.IGNORECASE | re.MULTILINE)


def strip_transcript(text: str) -> str:
    """Drop the verbatim transcript tab of a Gemini notes Doc.

    The notes (summary, decisions, next steps, details) come first; the
    transcript is usually ~80% of the export and adds nothing to the summary.
    """
    match = TRANSCRIPT_HEADING.search(text)
    return text[: match.start()].rstrip() if match else text


NO_SUMMARY = re.compile(r"(?i)no se gener[oó] ning[uú]n resumen|no summary was generated")


def notes_text(content: str) -> str | None:
    """Text to summarize from a Gemini Doc export, or None when there is nothing to keep.

    - Gemini notes Doc: the notes, without the verbatim transcript.
    - Transcript-only Doc (no "Resumen"/"Summary" section): the transcript.
    - Gemini said it could not summarize (too little conversation): None.
    """
    if NO_SUMMARY.search(content):
        return None
    notes = strip_transcript(content)
    if any(normalize(line) in SUMMARY_HEADINGS for line in notes.splitlines()):
        return notes
    return content


TRANSCRIPT_NOISE = [
    re.compile(r"(?i)^.*[-–]\s*(transcripci[oó]n|transcript)\s*$"),  # "<title> - Transcripción"
    re.compile(r"(?i)^(la transcripci[oó]n finaliz[oó] despu[eé]s de|the transcript ended after)\b.*$"),
    re.compile(r"(?i)^(esta transcripci[oó]n editable|this editable transcript)\b.*$"),
    re.compile(r"(?i)^\s*\d{1,2} de \w+ de \d{4}\s*$|^\s*\w{3,5}\.? \d{1,2}, \d{4}\s*$"),  # date line
]
TIMESTAMP_LINE = re.compile(r"^\s*(\d{2}:\d{2}:\d{2})\s*$")


def clean_transcript(raw: str) -> str:
    """Tidy the transcript tab of a Gemini Doc for a vault note.

    Keeps the ``Speaker: text`` lines, turns the ``HH:MM:SS`` markers into
    headings (one section per block) and drops Gemini's own boilerplate
    (title line, date, "the transcript ended after…", disclaimer).
    """
    lines: list[str] = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.rstrip()
        if TRANSCRIPT_HEADING.match(line) or any(p.match(line) for p in TRANSCRIPT_NOISE):
            continue
        stamp = TIMESTAMP_LINE.match(line)
        if stamp:
            lines.append(f"\n## {stamp.group(1)}\n")
            continue
        lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return text.strip()


def transcript_text(content: str) -> str | None:
    """The cleaned verbatim transcript of a Gemini Doc export, or None when it has none."""
    match = TRANSCRIPT_HEADING.search(content)
    if not match:
        return None
    text = clean_transcript(content[match.start():])
    # A transcript with no spoken line (only boilerplate) is not worth a note.
    return text if re.search(r"^[^\n#]+: \S", text, re.MULTILINE) else None


def transcript_note_name(series: str, day: date) -> str:
    """Name (without .md) of the transcript note of one meeting."""
    return f"{series} {day.isoformat()}"


def transcript_note_text(
    series: str,
    day: date,
    transcript: str,
    source_url: str | None,
    tags: list[str],
    organization: str | None,
) -> str:
    """Create the note holding one meeting's transcript."""
    data: dict[str, Any] = {"tags": tags}
    if organization:
        data[ORGANIZATION_KEY] = organization
    data["reunion"] = f"[[{series}]]"
    data["fecha"] = day.isoformat()
    block = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip("\n")
    source = f" Fuente: [Notas de Gemini]({source_url})." if source_url else ""
    return (
        f"---\n{block}\n---\n"
        f"# {series} — {day.isoformat()} (transcripción)\n\n"
        f"Transcripción automática de Gemini (Google Meet); puede contener errores. "
        f"Resumen en [[{series}#{day.isoformat()}]].{source}\n\n"
        f"{transcript}\n"
    )


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

    def __init__(
        self,
        vault_path: Path,
        meetings_folder: str,
        people_folder: str | None,
        transcripts_folder: str | None = None,
    ) -> None:
        self.vault_path = vault_path
        self.meetings_dir = vault_path / meetings_folder
        self.people_dir = vault_path / people_folder if people_folder else None
        # Subfolder of the meetings folder: one note per meeting transcript.
        self.transcripts_dir = self.meetings_dir / transcripts_folder if transcripts_folder else None

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

    def transcript_note(self, series: str, day: date) -> str | None:
        """Vault-relative path (without .md) of a meeting's transcript note, or None when disabled."""
        if self.transcripts_dir is None:
            return None
        rel = self.transcripts_dir.relative_to(self.vault_path).as_posix()
        return f"{rel}/{transcript_note_name(series, day)}"

    def has_transcript(self, series: str, day: date) -> bool:
        if self.transcripts_dir is None:
            return False
        return (self.transcripts_dir / f"{transcript_note_name(series, day)}.md").exists()

    def write_transcript(self, series: str, day: date, text: str) -> None:
        assert self.transcripts_dir is not None
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)
        p = self.transcripts_dir / f"{transcript_note_name(series, day)}.md"
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

    def latest_date(self) -> date | None:
        """Newest ``## YYYY-MM-DD`` section across all meeting notes."""
        dates = [d for name in self.note_names() for d in DATE_HEADING.findall(self.read(name) or "")]
        return date.fromisoformat(max(dates)) if dates else None

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

    SOURCES = ("calendar_attachment", "gemini_email")

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
        self.organization = getattr(obs, "organization", None) or organization_for_account(config, self.account)
        self.tags = meeting_tags(list(getattr(obs, "note_tags", None) or ["reunion"]), self.organization)
        self.transcript_tags = meeting_tags([TRANSCRIPT_TAG], self.organization)
        self.vault = (
            MeetingNotesVault(
                Path(obs.vault_path),
                obs.meetings_folder,
                obs.people_folder,
                getattr(obs, "transcripts_folder", None),
            )
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

    def catch_up_days_back(self, minimum: int, maximum: int = 60) -> int:
        """``days_back`` that reaches back to the newest meeting already in the vault."""
        if self.vault is None or self.vault_error():
            return minimum
        today = datetime.now(self.tz).date()
        return catch_up_days(self.vault.latest_date(), today, minimum, maximum)

    async def sync(self, days_back: int = 7, dry_run: bool = False) -> dict[str, Any]:
        """Write new meeting sections for the last ``days_back`` days."""
        error = self.vault_error()
        if error:
            return {"error": error}
        if not self.account:
            return {"error": "No calendar account (obsidian.calendar_account / calendar_defaults.account_id)"}
        assert self.vault is not None

        sources, source_errors = await self.collect_sources(days_back)
        if not sources and len(source_errors) == len(self.SOURCES):
            # Both sources failed: report it instead of a quiet "nothing new".
            details = "; ".join(f"{e['source']}: {e['error'][:200]}" for e in source_errors)
            return {"error": f"Could not read any Gemini notes source ({details})", "errors": source_errors}
        existing = self.vault.note_names()
        people_notes = self.vault.people_names()
        skip = [re.compile(p) for p in self.config.obsidian.skip_titles]

        result: dict[str, Any] = {
            "account": self.account,
            "organization": self.organization,
            "tags": self.tags,
            "tagged": 0 if dry_run else self.tag_notes(),
            "days_back": days_back,
            "dry_run": dry_run,
            "sources_found": len(sources),
            "written": [],
            "skipped": [],
            "new_notes": [],
            "transcripts": [],
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
                if self._backfill_transcript(name, src, current, dry_run):
                    result["transcripts"].append(f"{name} {src.day.isoformat()}")
                result["skipped"].append({"title": src.title, "note": name, "date": src.day.isoformat(), "reason": "already in note"})
                continue

            try:
                summary = await self.summarize(src)
            except Exception as e:  # never lose the whole run for one meeting
                result["errors"].append({"title": src.title, "error": str(e)})
                continue

            if not (summary.bullets or summary.decisions or summary.next_steps):
                result["skipped"].append({"title": src.title, "note": name, "date": src.day.isoformat(), "reason": "empty summary"})
                continue

            people = match_people(summary.people + src.attendees, people_notes)
            transcript_note = self.vault.transcript_note(name, src.day) if src.transcript else None
            section = render_section(src.day, summary, src.url, transcript_note)
            entry = {"title": src.title, "note": name, "date": src.day.isoformat(), "source": src.source, "people": people}

            if transcript_note and src.transcript:
                entry["transcript"] = transcript_note
                result["transcripts"].append(f"{name} {src.day.isoformat()}")
                if not dry_run:
                    self.vault.write_transcript(
                        name, src.day, self._transcript_note_text(name, src, src.transcript)
                    )
            if not dry_run:
                if current is None:
                    self.vault.write(name, new_note_text(name, section, people, self.tags, self.organization))
                    existing.append(name)
                    result["new_notes"].append(name)
                    if self.config.obsidian.update_index:
                        self._add_to_index(name)
                else:
                    text, _ = insert_section(current, src.day, section)
                    text, _ = ensure_frontmatter(ensure_people_links(text, people), self.tags, self.organization)
                    self.vault.write(name, text)
            else:
                entry["preview"] = section
                if current is None:
                    result["new_notes"].append(name)
            result["written"].append(entry)

        return result

    def _transcript_note_text(self, series: str, src: MeetingSource, transcript: str) -> str:
        return transcript_note_text(series, src.day, transcript, src.url, self.transcript_tags, self.organization)

    def _backfill_transcript(self, name: str, src: MeetingSource, current: str, dry_run: bool) -> bool:
        """Save the transcript of a meeting whose section already exists; returns whether it did."""
        assert self.vault is not None
        if not src.transcript or self.vault.has_transcript(name, src.day):
            return False
        transcript_note = self.vault.transcript_note(name, src.day)
        if transcript_note is None:
            return False
        if dry_run:
            return True
        self.vault.write_transcript(name, src.day, self._transcript_note_text(name, src, src.transcript))
        text, changed = add_transcript_link(current, src.day, transcript_note)
        if changed:
            self.vault.write(name, text)
        return True

    def tag_notes(self) -> int:
        """Add the meeting/organization frontmatter to every note missing it; returns how many changed."""
        if self.vault is None or self.vault_error():
            return 0
        changed = 0
        for name in self.vault.note_names():
            text = self.vault.read(name)
            if text is None:
                continue
            new_text, did_change = ensure_frontmatter(text, self.tags, self.organization)
            if did_change:
                self.vault.write(name, new_text)
                changed += 1
        return changed

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
        for label in self.SOURCES:
            fetch = self._from_calendar if label == "calendar_attachment" else self._from_gmail
            try:
                sources.extend(await fetch(days_back))
            except Exception as e:
                errors.append({"source": label, "error": str(e) or type(e).__name__})
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
        for event in events:
            for att in event.transcript_attachments:
                if provider.drive_unavailable:
                    break
                if att.mime_type and att.mime_type != "application/vnd.google-apps.document":
                    continue  # recordings etc. cannot be exported as text
                content = await provider.get_attachment_content(att.file_id)
                if content is None:
                    # Not shared with this account: the Gemini e-mail may still cover it.
                    continue
                text = notes_text(content)
                if text is None:
                    continue  # Gemini produced no summary: nothing worth a section
                out.append(MeetingSource(
                    key=att.file_id,
                    source="calendar_attachment",
                    title=event.title,
                    day=event.start_time.astimezone(self.tz).date(),
                    text=text,
                    url=att.file_url,
                    attendees=[
                        a.display_name or a.email for a in getattr(event, "attendees", []) or []
                    ],
                    transcript=transcript_text(content),
                ))
        if provider.drive_unavailable:
            # Stop hammering Drive and fall back to the Gemini e-mails.
            raise RuntimeError(
                f"Could not read Gemini notes from Google Drive ({provider.drive_unavailable}); "
                "using Gemini e-mails instead"
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
