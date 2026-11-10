"""PDF -> Markdown conversion and import into the vault's documents folder."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest

from executive_copilot.services import pdf_markdown
from executive_copilot.services.pdf_markdown import (
    DocumentImportService,
    PdfConversionError,
    convert_pdf,
    note_name,
    render_table,
)

if TYPE_CHECKING:
    from pathlib import Path

FONTS = {"R": "Helvetica", "B": "Helvetica-Bold", "M": "Courier"}
Text = tuple[float, float, float, str, str]  # x, y from the top, size, font key, text


def make_pdf(pages: list[list[Text]], grid: tuple[list[float], list[float]] | None = None, title: str | None = None) -> bytes:
    """A minimal valid PDF (Letter pages) with positioned text and an optional ruled grid on page 1."""
    height = 792.0
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_ids = {key: add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} >>".encode()) for key, name in FONTS.items()}
    resources = "<< /Font << " + " ".join(f"/{k} {i} 0 R" for k, i in font_ids.items()) + " >> >>"
    pages_id = len(objects) + 2 * len(pages) + 1
    page_ids = []
    for number, texts in enumerate(pages):
        ops = []
        for x, y, size, font, text in texts:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /{font} {size} Tf {x} {height - y} Td ({escaped}) Tj ET")
        if grid and number == 0:
            xs, ys = grid
            for x in xs:
                ops.append(f"{x} {height - ys[0]} m {x} {height - ys[-1]} l S")
            for y in ys:
                ops.append(f"{xs[0]} {height - y} m {xs[-1]} {height - y} l S")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 {height}] "
            f"/Resources {resources} /Contents {content} 0 R >>".encode()
        ))
    assert add(
        f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] >>".encode()
    ) == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    info = add(f"<< /Title ({title}) >>".encode()) if title else None

    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    trailer = f"<< /Size {len(objects) + 1} /Root {catalog} 0 R" + (f" /Info {info} 0 R" if info else "") + " >>"
    return out + f"trailer\n{trailer}\nstartxref\n{xref}\n%%EOF\n".encode()


def report() -> bytes:
    return make_pdf([[
        (72, 80, 24, "R", "Informe de arquitectura"),
        (72, 120, 16, "R", "Resumen"),
        (72, 150, 11, "R", "La plataforma procesa pagos con tarjeta y dispersa fondos a comer-"),
        (72, 164, 11, "R", "cios afiliados en todo el pais."),
        (72, 200, 11, "R", "El segundo parrafo empieza despues de un espacio vertical."),
        (72, 240, 16, "R", "Riesgos"),
        (72, 270, 11, "R", "- Dependencia de un solo adquirente"),
        (72, 284, 11, "R", "- Certificacion PCI pendiente"),
        (72, 320, 11, "B", "Notas del autor"),
        (72, 350, 10, "M", "SELECT 1;"),
        (300, 760, 11, "R", "1"),
    ]])


def config(vault: Path, **obsidian: object) -> SimpleNamespace:
    obs = {
        "enabled": True, "vault_path": vault, "meetings_folder": "Work/Meetings",
        "documents_folder": "Work/Docs", "document_tags": ["documento"], "documents_max_mb": 1,
        "index_folders": ["Work"],
    } | obsidian
    return SimpleNamespace(obsidian=SimpleNamespace(**obs), knowledge=SimpleNamespace(enabled=False))


# --- conversion --------------------------------------------------------------


def test_headings_paragraphs_lists_and_code() -> None:
    converted = convert_pdf(report())
    md = converted.markdown

    assert converted.pages == 1
    assert converted.title == "Informe de arquitectura"
    assert "## Informe de arquitectura" in md
    assert "### Resumen" in md and "### Riesgos" in md
    # Wrapped lines join into one paragraph and the hyphenated word is healed.
    assert "dispersa fondos a comercios afiliados en todo el pais." in md
    assert "\n\nEl segundo parrafo empieza" in md
    assert "- Dependencia de un solo adquirente\n- Certificacion PCI pendiente" in md
    assert "#### Notas del autor" in md
    assert "```\nSELECT 1;\n```" in md
    # The page number at the bottom is not content.
    assert not md.rstrip().endswith("1")


def test_ruled_table_becomes_gfm_table() -> None:
    pdf = make_pdf(
        [[
            (72, 80, 11, "R", "Comisiones por producto vigentes este trimestre."),
            (110, 215, 11, "R", "Producto"), (310, 215, 11, "R", "Comision"),
            (110, 245, 11, "R", "Tarjeta"), (310, 245, 11, "R", "2.9"),
            (72, 320, 11, "R", "Texto despues de la tabla."),
        ]],
        grid=([100, 300, 500], [200, 230, 260]),
    )
    converted = convert_pdf(pdf)

    assert converted.tables == 1
    assert "| Producto | Comision |\n| --- | --- |\n| Tarjeta | 2.9 |" in converted.markdown
    # Cell text is not repeated as loose paragraphs, and order is preserved.
    assert converted.markdown.count("Producto") == 1
    assert converted.markdown.index("Comisiones") < converted.markdown.index("| Producto") < converted.markdown.index("Texto despues")


def test_running_headers_and_page_numbers_are_dropped() -> None:
    pages = [
        [
            (72, 30, 9, "R", "Paycode - Confidencial"),
            (72, 200, 11, "R", f"Contenido unico de la pagina numero {n} del documento."),
            (280, 770, 9, "R", f"Pagina {n} de 3"),
        ]
        for n in (1, 2, 3)
    ]
    md = convert_pdf(make_pdf(pages)).markdown

    assert "Confidencial" not in md and "de 3" not in md
    assert all(f"pagina numero {n}" in md for n in (1, 2, 3))


def test_metadata_title_wins_and_junk_titles_are_ignored() -> None:
    assert convert_pdf(make_pdf([[(72, 80, 11, "R", "Solo texto de cuerpo en el documento.")]], title="Contrato marco")).title == "Contrato marco"
    assert convert_pdf(make_pdf([[(72, 80, 11, "R", "Solo texto de cuerpo en el documento.")]], title="Microsoft Word - x.docx")).title is None


def test_rejects_non_pdf_and_pdf_without_text() -> None:
    with pytest.raises(PdfConversionError, match="Not a PDF"):
        convert_pdf(b"hello world")
    with pytest.raises(PdfConversionError, match="no extractable text"):
        convert_pdf(make_pdf([[]]))
    with pytest.raises(PdfConversionError, match="Cannot read PDF"):
        convert_pdf(b"%PDF-1.4\nbroken")


def test_render_table_and_note_name_helpers() -> None:
    assert render_table([["a", None], ["b|c", "d\ne"]]) == "| a |  |\n| --- | --- |\n| b\\|c | d e |"
    assert render_table([["only one row", "x"]]) is None
    assert render_table([["one"], ["column"]]) is None
    assert note_name('Plan: Q1/Q2 [draft] #1?') == "Plan Q1 Q2 draft 1"
    assert note_name("///") == "Documento"


# --- vault import -------------------------------------------------------------


def test_import_writes_note_with_frontmatter_and_is_idempotent(tmp_path: Path) -> None:
    service = DocumentImportService(config(tmp_path))
    data = report()

    preview = service.import_pdf(data, "informe.pdf", dry_run=True)
    assert preview["status"] == "preview" and "Resumen" in preview["preview"]
    assert not (tmp_path / "Work/Docs").exists()

    result = service.import_pdf(data, "../../informe.pdf", source="https://example.com/informe.pdf")
    assert result == {
        "status": "written", "note": "Informe de arquitectura", "path": "Work/Docs/Informe de arquitectura.md",
        "pages": 1, "tables": 0, "chars": result["chars"], "searchable": True,
    }
    text = (tmp_path / "Work/Docs/Informe de arquitectura.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntags: [documento]\norigen: pdf\narchivo: \"informe.pdf\"\nfuente: \"https://example.com/informe.pdf\"\npaginas: 1\n")
    assert f"sha256: {hashlib.sha256(data).hexdigest()}" in text
    # One title: the document's own top heading is not repeated under the note's H1.
    assert text.count("Informe de arquitectura") == 1 and "\n# Informe de arquitectura\n\n### Resumen" in text

    # A note edited in Obsidian survives a second import of the same PDF.
    path = tmp_path / "Work/Docs/Informe de arquitectura.md"
    path.write_text(text + "\nNota mia\n", encoding="utf-8")
    assert service.import_pdf(data, "otro-nombre.pdf")["status"] == "unchanged"
    assert "Nota mia" in path.read_text(encoding="utf-8")
    assert service.import_pdf(data, "informe.pdf", overwrite=True)["status"] == "written"
    assert "Nota mia" not in path.read_text(encoding="utf-8")


def test_import_refuses_name_clash_oversize_and_missing_config(tmp_path: Path) -> None:
    service = DocumentImportService(config(tmp_path))
    (tmp_path / "Work/Docs").mkdir(parents=True)
    (tmp_path / "Work/Docs/Informe de arquitectura.md").write_text("# mine\n", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        service.import_pdf(report(), "informe.pdf")
    assert service.import_pdf(report(), "informe.pdf", title="Informe v2")["note"] == "Informe v2"
    with pytest.raises(ValueError, match="larger than 1 MB"):
        service.import_pdf(b"%PDF-" + b"0" * (1024 * 1024), "big.pdf")
    with pytest.raises(ValueError, match="documents_folder"):
        DocumentImportService(config(tmp_path, documents_folder=None)).import_pdf(report(), "x.pdf")
    with pytest.raises(ValueError, match="vault not found"):
        DocumentImportService(config(tmp_path / "missing")).import_pdf(report(), "x.pdf")
    assert DocumentImportService(config(tmp_path, documents_folder="Other/Docs")).searchable() is False


def test_import_pending_converts_dropped_pdfs_and_reports_bad_ones(tmp_path: Path) -> None:
    service = DocumentImportService(config(tmp_path))
    folder = tmp_path / "Work/Docs"
    folder.mkdir(parents=True)
    (folder / "informe.pdf").write_bytes(report())
    (folder / "escaneado.pdf").write_bytes(make_pdf([[]]))

    assert service.status()["pending_pdfs"] == ["escaneado.pdf", "informe.pdf"]
    result = service.import_pending()
    assert [d["note"] for d in result["imported"]] == ["Informe de arquitectura"]
    assert result["errors"] == [{"file": "escaneado.pdf", "error": "PDF has no extractable text (scanned or image-only); OCR is not supported"}]

    again = service.import_pending()
    assert again["imported"] == [] and again["unchanged"] == 1
    status = service.status()
    assert status["documents"] == 1 and status["pending_pdfs"] == ["escaneado.pdf"] and status["searchable"] is True


@pytest.mark.asyncio
async def test_import_source_from_vault_path_and_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = DocumentImportService(config(tmp_path))
    (tmp_path / "Inbox").mkdir()
    (tmp_path / "Inbox/informe.pdf").write_bytes(report())
    (tmp_path.parent / "outside.pdf").write_bytes(report())

    with pytest.raises(ValueError, match="inside the vault"):
        await service.import_source("../outside.pdf")
    with pytest.raises(ValueError, match="not found"):
        await service.import_source("Inbox/nope.pdf")
    assert (await service.import_source("Inbox/informe.pdf"))["status"] == "written"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/missing.pdf":
            return httpx.Response(404)
        return httpx.Response(200, content=make_pdf([[(72, 80, 11, "R", "Documento descargado desde una URL publica.")]]))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        pdf_markdown.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(ValueError, match="HTTP 404"):
        await service.import_source("https://example.com/missing.pdf")
    result = await service.import_source("https://example.com/files/politica", title="Politica de seguridad")
    assert result["note"] == "Politica de seguridad"
    text = (tmp_path / "Work/Docs/Politica de seguridad.md").read_text(encoding="utf-8")
    assert 'archivo: "politica.pdf"' in text and 'fuente: "https://example.com/files/politica"' in text
