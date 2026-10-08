"""Tests for the Obsidian meeting-notes helpers."""

from datetime import date
from pathlib import Path

from executive_copilot.services.obsidian_notes import (
    MeetingNotesVault,
    MeetingSummary,
    add_to_index,
    clean_meeting_title,
    ensure_people_links,
    gemini_title_from_subject,
    has_section,
    html_to_text,
    insert_section,
    match_people,
    new_note_text,
    parse_ai_json,
    parse_gemini_text,
    render_section,
    resolve_note_name,
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
