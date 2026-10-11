"""Tests for the Obsidian meeting-notes helpers."""

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest

from executive_copilot.providers.calendar import google as google_calendar
from executive_copilot.providers.calendar.google import drive_unavailable_reason
from executive_copilot.services.obsidian_notes import (
    MeetingNotesSyncService,
    MeetingNotesVault,
    MeetingSource,
    MeetingSummary,
    add_to_index,
    catch_up_days,
    clean_meeting_title,
    ensure_frontmatter,
    ensure_people_links,
    gemini_title_from_subject,
    has_section,
    html_to_text,
    insert_section,
    match_people,
    meeting_tags,
    new_note_text,
    notes_text,
    organization_for_account,
    parse_ai_json,
    parse_gemini_text,
    render_section,
    resolve_note_name,
    strip_transcript,
    tag_slug,
)

NOTE = """# Josue / Gonzalo (1:1 técnico)

1:1 recurrente.

## 2026-08-31
- Error de tokenización.

## 2026-08-24
- Plugin de WooCommerce.

---
*Fuente: notas automáticas de Gemini (Google Meet).*

## Personas relacionadas
- [[Josue Hernandez Martinez]]
"""

EXISTING = ["Josue - Gonzalo (1a1)", "Seguimiento PCI DSS", "Bitácora diaria", "Ellucian"]


def test_gemini_subject_parsing() -> None:
    assert gemini_title_from_subject("Notes: “Weekly sync” Oct 6, 2026") == "Weekly sync"
    assert gemini_title_from_subject('Notas: "Reunión diaria" 6 oct 2026') == "Reunión diaria"


def test_clean_meeting_title_strips_gemini_suffix() -> None:
    title = "Josue / Gonzalo - 2026/08/31 10:00 CST - Notes by Gemini"
    assert clean_meeting_title(title) == "Josue / Gonzalo"


def test_resolve_note_series_map_wins() -> None:
    name, exists = resolve_note_name(
        "Reunión diaria equipo", EXISTING, {"(?i)reuni[oó]n diaria": "Bitácora diaria"}
    )
    assert (name, exists) == ("Bitácora diaria", True)


def test_resolve_note_accent_insensitive_exact() -> None:
    assert resolve_note_name("Bitacora diaria", EXISTING) == ("Bitácora diaria", True)


def test_resolve_note_token_subset() -> None:
    assert resolve_note_name("Seguimiento PCI", EXISTING) == ("Seguimiento PCI DSS", True)


def test_resolve_note_new_note_is_safe_filename() -> None:
    name, exists = resolve_note_name("Kickoff: Banco/Interred", EXISTING)
    assert exists is False
    assert "/" not in name and ":" not in name


def test_insert_section_newest_first() -> None:
    section = render_section(date(2026, 9, 7), MeetingSummary(bullets=["Nuevo"]))
    text, inserted = insert_section(NOTE, date(2026, 9, 7), section)
    assert inserted
    assert text.index("## 2026-09-07") < text.index("## 2026-08-31")


def test_insert_section_between_dates() -> None:
    section = render_section(date(2026, 8, 27), MeetingSummary(bullets=["Medio"]))
    text, _ = insert_section(NOTE, date(2026, 8, 27), section)
    assert text.index("## 2026-08-31") < text.index("## 2026-08-27") < text.index("## 2026-08-24")


def test_insert_section_oldest_goes_before_footer() -> None:
    section = render_section(date(2026, 8, 1), MeetingSummary(bullets=["Viejo"]))
    text, _ = insert_section(NOTE, date(2026, 8, 1), section)
    assert text.index("## 2026-08-24") < text.index("## 2026-08-01") < text.index("\n---")


def test_insert_section_is_idempotent() -> None:
    assert has_section(NOTE, date(2026, 8, 31))
    text, inserted = insert_section(NOTE, date(2026, 8, 31), "## 2026-08-31\n- dup\n")
    assert not inserted and text == NOTE


def test_render_section_style() -> None:
    out = render_section(
        date(2026, 10, 6),
        MeetingSummary(
            bullets=["Avance del plugin"],
            decisions=["Priorizar talento actual"],
            next_steps=["Josue entrega el plugin.", "Gonzalo revisa candidatos"],
        ),
        "https://docs.google.com/document/d/abc",
    )
    assert out.startswith("## 2026-10-06\n- Avance del plugin\n- Decisión: Priorizar talento actual\n")
    assert "- Próximos pasos: Josue entrega el plugin; Gonzalo revisa candidatos." in out
    assert "[Notas de Gemini](https://docs.google.com/document/d/abc)" in out


def test_match_people_by_name_and_email() -> None:
    people = ["Josue Hernandez Martinez", "Rafael Ayala (Rafa)", "Ellucian - Sreeharish Prabhakaran"]
    found = match_people(["josue.hernandez@paycode.com.mx", "Rafael Ayala"], people)
    assert found == ["Josue Hernandez Martinez", "Rafael Ayala (Rafa)"]


def test_ensure_people_links_appends_missing_only() -> None:
    out = ensure_people_links(NOTE, ["Josue Hernandez Martinez", "Raul Juarez"])
    assert out.count("[[Josue Hernandez Martinez]]") == 1
    assert "- [[Raul Juarez]]" in out


def test_new_note_has_footer_and_people() -> None:
    text = new_note_text("Kickoff BBVA", "## 2026-10-06\n- x\n", ["Raul Juarez"])
    assert text.startswith("# Kickoff BBVA\n")
    assert "*Fuente: notas automáticas de Gemini" in text
    assert text.rstrip().endswith("- [[Raul Juarez]]")


def test_add_to_index() -> None:
    readme = "# Reuniones\n\n## Índice\n- Bitácora diaria\n- Ellucian\n"
    out, changed = add_to_index(readme, "Kickoff BBVA")
    assert changed and out.rstrip().endswith("- Kickoff BBVA")
    _, again = add_to_index(out, "Kickoff BBVA")
    assert not again


def test_parse_gemini_text_fallback() -> None:
    text = (
        "Resumen\nSe revisó el avance del roadmap.\nSe acordó priorizar PCI.\n\n"
        "Detalles\n- detalle largo\n\nPróximos pasos sugeridos\n"
        "- [Josue] Entregar plugin\n- [Gonzalo] Revisar candidatos\n"
    )
    summary = parse_gemini_text(text)
    assert summary.bullets == ["Se revisó el avance del roadmap.", "Se acordó priorizar PCI."]
    assert summary.next_steps == ["[Josue] Entregar plugin", "[Gonzalo] Revisar candidatos"]


def test_parse_ai_json_tolerates_fences_and_prose() -> None:
    assert parse_ai_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_ai_json("Here you go: [1, 3]") == [1, 3]


def test_html_to_text() -> None:
    assert html_to_text("<p>Hola&nbsp;<b>mundo</b></p><ul><li>uno</li></ul>") == "Hola mundo\n- uno"


def test_vault_search_and_find(tmp_path: Path) -> None:
    folder = tmp_path / "Paycode" / "Reuniones"
    folder.mkdir(parents=True)
    (folder / "Josue - Gonzalo (1a1).md").write_text(NOTE, encoding="utf-8")
    (folder / "README.md").write_text("# Reuniones\n", encoding="utf-8")
    vault = MeetingNotesVault(tmp_path, "Paycode/Reuniones", None)

    assert vault.check() is None
    assert vault.note_names() == ["Josue - Gonzalo (1a1)"]
    assert vault.find("josue") == "Josue - Gonzalo (1a1)"

    hits = vault.search("tokenizacion")
    assert hits[0]["date"] == "2026-08-31"
    assert hits[0]["matches"] == ["- Error de tokenización."]
    assert vault.search("tokenizacion", since=date(2026, 9, 1)) == []

    listed = vault.list_notes()
    assert listed[0]["meetings"] == 2 and listed[0]["latest"] == "2026-08-31"


def test_vault_latest_date(tmp_path: Path) -> None:
    folder = tmp_path / "Reuniones"
    folder.mkdir()
    vault = MeetingNotesVault(tmp_path, "Reuniones", None)
    assert vault.latest_date() is None

    (folder / "A.md").write_text("# A\n\n## 2026-09-10\n- x\n\n## 2026-08-01\n- y\n", encoding="utf-8")
    (folder / "B.md").write_text("# B\n\n## 2026-09-02\n- z\n", encoding="utf-8")
    assert vault.latest_date() == date(2026, 9, 10)


def test_catch_up_days() -> None:
    today = date(2026, 10, 8)
    # Up to date: the normal window wins.
    assert catch_up_days(date(2026, 10, 7), today, minimum=3) == 3
    # Gap since 10 Sep: reach back to it (plus one day).
    assert catch_up_days(date(2026, 9, 10), today, minimum=3) == 29
    # Capped, and empty vaults use the normal window.
    assert catch_up_days(date(2026, 1, 1), today, minimum=3, maximum=60) == 60
    assert catch_up_days(None, today, minimum=3) == 3


def _service(tmp_path: Path) -> MeetingNotesSyncService:
    (tmp_path / "Reuniones").mkdir(exist_ok=True)
    config = SimpleNamespace(
        obsidian=SimpleNamespace(
            enabled=True,
            vault_path=str(tmp_path),
            meetings_folder="Reuniones",
            people_folder=None,
            calendar_account="me@example.com",
            skip_titles=[],
            series_map={},
            update_index=False,
        ),
        calendar_defaults=SimpleNamespace(timezone="America/Mexico_City", account_id="me@example.com"),
    )
    return MeetingNotesSyncService(config, credential_manager=None)


@pytest.mark.asyncio
async def test_sync_reports_error_when_every_source_fails(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def boom(days_back: int) -> list[Any]:
        raise RuntimeError("403 Forbidden")

    service._from_calendar = boom  # type: ignore[method-assign]
    service._from_gmail = boom  # type: ignore[method-assign]

    result = await service.sync(days_back=3)
    assert "error" in result
    assert "gemini_email" in result["error"]


@pytest.mark.asyncio
async def test_sync_without_drive_still_uses_gemini_emails(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def no_drive(days_back: int) -> list[Any]:
        raise RuntimeError("drive.readonly scope missing")

    async def emails(days_back: int) -> list[MeetingSource]:
        return [MeetingSource(key="1", source="gemini_email", title="Weekly", day=date(2026, 10, 6), text="Resumen\n- algo importante que se trató")]

    service._from_calendar = no_drive  # type: ignore[method-assign]
    service._from_gmail = emails  # type: ignore[method-assign]

    result = await service.sync(days_back=3)
    assert "error" not in result
    assert [w["note"] for w in result["written"]] == ["Weekly"]
    assert has_section((tmp_path / "Reuniones" / "Weekly.md").read_text(encoding="utf-8"), date(2026, 10, 6))


def _doc(file_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        file_id=file_id,
        mime_type="application/vnd.google-apps.document",
        file_url=f"https://docs.google.com/document/d/{file_id}",
    )


class FakeCalendar:
    """Stands in for GoogleCalendarProvider in ``_from_calendar``."""

    contents: ClassVar[dict[str, str | None]] = {}
    unavailable_on: ClassVar[str | None] = None

    def __init__(self, *args: Any) -> None:
        self.drive_unavailable: str | None = None

    async def get_events(self, query: Any) -> tuple[list[Any], None]:
        event = SimpleNamespace(
            title="Weekly",
            start_time=datetime(2026, 10, 6, 17, 0, tzinfo=UTC),
            attendees=[],
            transcript_attachments=[_doc(k) for k in self.contents],
        )
        return [event], None

    async def get_attachment_content(self, file_id: str) -> str | None:
        if file_id == self.unavailable_on:
            self.drive_unavailable = "missing scope"
        return self.contents[file_id]


@pytest.mark.asyncio
async def test_calendar_skips_unshared_doc_and_keeps_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeCalendar.contents = {"private": None, "shared": "Resumen\n- notas"}
    FakeCalendar.unavailable_on = None
    monkeypatch.setattr(google_calendar, "GoogleCalendarProvider", FakeCalendar)

    sources = await _service(tmp_path)._from_calendar(days_back=3)
    assert [s.key for s in sources] == ["shared"]
    assert sources[0].day == date(2026, 10, 6)


@pytest.mark.asyncio
async def test_calendar_stops_when_drive_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeCalendar.contents = {"first": None, "second": "never read"}
    FakeCalendar.unavailable_on = "first"
    monkeypatch.setattr(google_calendar, "GoogleCalendarProvider", FakeCalendar)

    with pytest.raises(RuntimeError, match="missing scope"):
        await _service(tmp_path)._from_calendar(days_back=3)


def test_drive_unavailable_reason() -> None:
    scope = httpx.Response(403, text='{"error": {"details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}')
    disabled = httpx.Response(403, text='{"error": {"details": [{"reason": "SERVICE_DISABLED"}]}}')
    not_shared = httpx.Response(403, text='{"error": {"errors": [{"reason": "forbidden"}]}}')
    missing = httpx.Response(404, text="{}")

    assert "drive.readonly" in (drive_unavailable_reason(scope) or "")
    assert "disabled" in (drive_unavailable_reason(disabled) or "")
    assert drive_unavailable_reason(not_shared) is None
    assert drive_unavailable_reason(missing) is None


def test_strip_transcript_keeps_notes_only() -> None:
    doc = (
        "📝 Las notas\nago 24, 2026\nRegistros de la reunión Transcripción\n\n"
        "Resumen\nSe acordó X.\n\nDetalles\nMás contexto.\n\n"
        "📖 Transcripción\nago 24, 2026\nGonzalo P: Hola.\n"
    )
    stripped = strip_transcript(doc)
    assert stripped.endswith("Más contexto.")
    assert "Gonzalo P: Hola" not in stripped
    assert "Registros de la reunión Transcripción" in stripped
    assert strip_transcript("Summary\nx\n\nTranscript\nA: hi") == "Summary\nx"
    assert strip_transcript("Resumen\nsin transcripción") == "Resumen\nsin transcripción"


def test_notes_text_by_doc_kind() -> None:
    gemini = "📝 Las notas\nResumen\nSe acordó X.\n\n📖 Transcripción\nA: hola\n"
    assert notes_text(gemini) == "📝 Las notas\nResumen\nSe acordó X."

    transcript_only = "Reunión | A <> B\nAsistentes\nA, B\nTranscripción\nA: hola\nB: hola\n"
    assert notes_text(transcript_only) == transcript_only

    no_summary = "📝 Las notas\nResumen\nNo se generó ningún resumen para esta reunión porque no hubo suficiente conversación.\n"
    assert notes_text(no_summary) is None


@pytest.mark.asyncio
async def test_sync_skips_empty_summaries(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def nothing(days_back: int) -> list[Any]:
        return []

    async def emails(days_back: int) -> list[MeetingSource]:
        return [MeetingSource(key="1", source="gemini_email", title="Weekly", day=date(2026, 10, 6), text="")]

    service._from_calendar = nothing  # type: ignore[method-assign]
    service._from_gmail = emails  # type: ignore[method-assign]

    result = await service.sync(days_back=3)
    assert result["written"] == []
    assert result["skipped"][0]["reason"] == "empty summary"
    assert not (tmp_path / "Reuniones" / "Weekly.md").exists()


@pytest.mark.asyncio
async def test_attachment_timeout_skips_only_that_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow export", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        google_calendar.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    creds = SimpleNamespace(get_credential=lambda *_args: {"access_token": "t"})
    provider = google_calendar.GoogleCalendarProvider(creds, "me@example.com")  # type: ignore[arg-type]

    assert await provider.get_attachment_content("doc") is None
    assert provider.drive_unavailable is None


def test_new_note_has_frontmatter_tags_and_organization() -> None:
    text = new_note_text("Kickoff BBVA", "## 2026-10-06\n- x\n", [], ["reunion", "paycode"], "Paycode")
    assert text.startswith("---\ntags:\n- reunion\n- paycode\norganizacion: Paycode\n---\n# Kickoff BBVA\n")
    assert text.rstrip().endswith("*Fuente: notas automáticas de Gemini (Google Meet).*")


def test_ensure_frontmatter_adds_and_keeps_existing() -> None:
    plain = "# Nota\n\n## 2026-10-01\n- a\n"
    tagged, changed = ensure_frontmatter(plain, ["reunion", "paycode"], "Paycode")
    assert changed and tagged.endswith(plain)
    again, changed = ensure_frontmatter(tagged, ["reunion", "paycode"], "Paycode")
    assert not changed and again == tagged
    # Existing keys and tags survive; only the missing ones are added.
    custom = "---\ntags: [cliente]\nestado: activo\n---\n# Nota\n"
    merged, changed = ensure_frontmatter(custom, ["reunion"], "Paycode")
    assert changed
    assert "- cliente\n- reunion\n" in merged and "estado: activo" in merged and merged.endswith("# Nota\n")
    broken = "---\ntags: [unclosed\n---\n# Nota\n"
    assert ensure_frontmatter(broken, ["reunion"], "Paycode") == (broken, False)


def test_insert_section_skips_frontmatter_when_looking_for_footer() -> None:
    note = "---\ntags:\n- reunion\n---\n# Nota\n\n## 2026-10-05\n- a\n\n---\n*Fuente*\n"
    text, inserted = insert_section(note, date(2026, 10, 1), "## 2026-10-01\n- old\n")
    assert inserted and text.startswith("---\ntags:\n- reunion\n---\n# Nota\n")
    assert text.index("## 2026-10-05") < text.index("## 2026-10-01") < text.index("*Fuente*")


def test_meeting_tags_and_organization_lookup() -> None:
    assert tag_slug("Banco Azteca") == "banco-azteca"
    assert meeting_tags(["Reunión"], "Paycode") == ["reunion", "paycode"]
    assert meeting_tags(["reunion", "paycode"], "Paycode") == ["reunion", "paycode"]
    orgs = [
        SimpleNamespace(name="Paycode", domains=["paycode.com.mx"],
                        provider_accounts=[SimpleNamespace(account_id="gonzalo@paycode.com.mx")]),
        SimpleNamespace(name="Personal", domains=["gmail.com"], provider_accounts=[]),
    ]
    config = SimpleNamespace(organizations=orgs)
    assert organization_for_account(config, "Gonzalo@paycode.com.mx") == "Paycode"
    assert organization_for_account(config, "someone@gmail.com") == "Personal"
    assert organization_for_account(config, "x@other.com") is None
    assert organization_for_account(SimpleNamespace(), "x@paycode.com.mx") is None


def test_sync_tags_existing_notes(tmp_path: Path) -> None:
    service = _service(tmp_path)
    (tmp_path / "Reuniones" / "Vieja.md").write_text("# Vieja\n\n## 2026-09-01\n- a\n", encoding="utf-8")
    assert service.organization is None and service.tags == ["reunion"]
    service.organization, service.tags = "Paycode", ["reunion", "paycode"]
    assert service.tag_notes() == 1
    assert (tmp_path / "Reuniones" / "Vieja.md").read_text(encoding="utf-8").startswith(
        "---\ntags:\n- reunion\n- paycode\norganizacion: Paycode\n---\n# Vieja\n"
    )
    assert service.tag_notes() == 0
