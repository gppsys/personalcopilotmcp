"""PDF -> Markdown notes in the Obsidian vault, as searchable context.

Conversion is local and deterministic (pdfplumber; no model, no upload): the
body font size is the most common one, larger lines become headings, ruled
tables become GFM tables, bullets become lists and page numbers / running
headers are dropped. Scanned PDFs (no text layer) are rejected, never written
as empty notes.

Each PDF becomes one note in ``obsidian.documents_folder`` with frontmatter
carrying the file's sha256, so importing the same PDF twice is a no-op and a
note edited in Obsidian is never overwritten unless asked.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from executive_copilot.logging import get_logger
from executive_copilot.services.knowledge.locking import vault_lock

logger = get_logger(__name__)

PDF_MAGIC = b"%PDF-"
MIN_TEXT_CHARS = 20
HEADING_RATIO = 1.15
MAX_HEADING_CHARS = 120
MAX_NOTE_NAME = 120
PREVIEW_CHARS = 1500

PAGE_NUMBER = re.compile(
    r"^(?:\d{1,4}|(?:page|p[aá]gina|p[aá]g\.?)\s*\d+(?:\s*(?:of|de|/)\s*\d+)?|\d+\s*(?:/|of|de)\s*\d+)$",
    re.IGNORECASE,
)
LIST_ITEM = re.compile(r"^(?:([-*•▪◦·‣●○■□–—])|(\d{1,3})[.)])\s+(\S.*)$")
BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi", re.IGNORECASE)
MONO_FONT = re.compile(r"mono|courier|consolas|menlo", re.IGNORECASE)
SENTENCE_END = re.compile(r"[.!?:;,]$")
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]+')
JUNK_TITLE = re.compile(r"^(untitled|sin t[ií]tulo|microsoft word|document\d*)\b|\.(docx?|pdf|pptx?|xlsx?)$", re.IGNORECASE)
SHA_LINE = re.compile(r"^sha256:\s*([0-9a-f]{64})\s*$", re.MULTILINE)


class PdfConversionError(ValueError):
    """The file is not a PDF this converter can turn into text."""


@dataclass(frozen=True)
class Line:
    """One visual line of text with the layout facts the heuristics need."""

    text: str
    size: float
    bold: bool
    mono: bool
    x0: float
    top: float
    bottom: float
    page: int
    page_height: float


@dataclass(frozen=True)
class ConvertedPdf:
    markdown: str
    title: str | None
    pages: int
    tables: int

    @property
    def chars(self) -> int:
        return len(self.markdown)


# --- conversion -------------------------------------------------------------


def _in_box(word: dict[str, Any], box: tuple[float, float, float, float]) -> bool:
    x = (word["x0"] + word["x1"]) / 2
    y = (word["top"] + word["bottom"]) / 2
    return bool(box[0] <= x <= box[2] and box[1] <= y <= box[3])


def _build_line(words: list[dict[str, Any]], page: int, page_height: float) -> Line:
    words = sorted(words, key=lambda w: w["x0"])
    weight: Counter[float] = Counter()
    bold = mono = total = 0
    for w in words:
        n = len(w["text"])
        total += n
        weight[round(float(w.get("size") or 0) * 2) / 2] += n
        font = str(w.get("fontname") or "")
        bold += n if BOLD_FONT.search(font) else 0
        mono += n if MONO_FONT.search(font) else 0
    return Line(
        text=" ".join(w["text"] for w in words).strip(),
        size=weight.most_common(1)[0][0],
        bold=bold > total / 2,
        mono=mono > total / 2,
        x0=float(words[0]["x0"]),
        top=min(float(w["top"]) for w in words),
        bottom=max(float(w["bottom"]) for w in words),
        page=page,
        page_height=page_height,
    )


def _group_lines(words: list[dict[str, Any]], page: int, page_height: float) -> list[Line]:
    lines: list[Line] = []
    current: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if current:
            height = sum(w["bottom"] - w["top"] for w in current) / len(current)
            if abs(word["top"] - current[0]["top"]) > max(3.0, height * 0.35):
                lines.append(_build_line(current, page, page_height))
                current = []
        current.append(word)
    if current:
        lines.append(_build_line(current, page, page_height))
    return lines


def _cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).replace("|", "\\|").strip()


def render_table(rows: list[list[Any]]) -> str | None:
    """GFM table, or None when the grid is not really tabular (one row/column, empty)."""
    cleaned = [[_cell(c) for c in row] for row in rows if any(_cell(c) for c in row)]
    width = max((len(r) for r in cleaned), default=0)
    if len(cleaned) < 2 or width < 2:
        return None
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]
    out = ["| " + " | ".join(cleaned[0]) + " |", "|" + " --- |" * width]
    out += ["| " + " | ".join(r) + " |" for r in cleaned[1:]]
    return "\n".join(out)


def _running_lines(lines: list[Line], pages: int) -> set[str]:
    """Texts repeated at the top/bottom edge of most pages (running headers and footers)."""
    if pages < 3:
        return set()
    seen: dict[str, set[int]] = {}
    for line in lines:
        edge = line.top < line.page_height * 0.08 or line.bottom > line.page_height * 0.92
        if edge and len(line.text) < 100:
            seen.setdefault(re.sub(r"\d+", "#", line.text.lower()), set()).add(line.page)
    return {key for key, on in seen.items() if len(on) >= max(3, pages * 0.6)}


def _heading_levels(lines: list[Line], base: float) -> dict[float, int]:
    sizes = sorted(
        {ln.size for ln in lines if ln.size > base * HEADING_RATIO and len(ln.text) <= MAX_HEADING_CHARS},
        reverse=True,
    )
    # "#" is the note title, so the document's own headings start at "##".
    return {size: min(i + 2, 4) for i, size in enumerate(sizes)}


def _sane_title(value: Any) -> str | None:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    if not 3 <= len(title) <= 150 or JUNK_TITLE.search(title):
        return None
    return title


def _markdown(blocks: list[tuple[Line | None, str | None]], base: float, levels: dict[float, int]) -> str:
    """Blocks in reading order: (line, None) for text, (None, table_markdown) for tables."""
    out: list[str] = []
    para: list[str] = []
    in_code = False
    prev: Line | None = None
    list_x0: float | None = None

    def flush() -> None:
        if para:
            out.append(" ".join(para))
            para.clear()

    def close_code() -> None:
        nonlocal in_code
        if in_code:
            out[-1] += "\n```"
            in_code = False

    for line, table in blocks:
        if line is None:
            flush()
            close_code()
            out.append(table or "")
            prev, list_x0 = None, None
            continue
        text = line.text
        if not text:
            continue
        if line.mono:
            flush()
            if in_code:
                out[-1] += "\n" + text
            else:
                out.append("```\n" + text)
                in_code = True
            prev, list_x0 = line, None
            continue
        close_code()

        gap = prev is None or prev.page != line.page or line.top - prev.bottom > max(line.size, prev.size) * 0.6
        item = LIST_ITEM.match(text)
        level = levels.get(line.size) if len(text) <= MAX_HEADING_CHARS else None
        if level is None and not item and line.bold and len(text) < 80 and not SENTENCE_END.search(text):
            level = 4
        if level is not None and len(text) >= 2 and not item:
            flush()
            out.append(f"{'#' * level} {text.lstrip('# ')}")
            list_x0 = None
        elif item:
            flush()
            out.append(f"{item.group(2) + '.' if item.group(2) else '-'} {item.group(3)}")
            list_x0 = line.x0
        elif list_x0 is not None and not gap and line.x0 > list_x0 + 2:
            out[-1] += " " + text  # wrapped list item
        else:
            list_x0 = None
            same_page_break = prev is not None and prev.page == line.page and gap
            if same_page_break or (prev is not None and abs(prev.size - line.size) > 0.5):
                flush()
            if para and para[-1].endswith("-") and text[:1].islower():
                para[-1] = para[-1][:-1] + text  # word hyphenated across lines
            else:
                para.append(text)
        prev = line

    flush()
    close_code()
    text = "\n\n".join(block for block in out if block)
    # Consecutive list items read as one list, not one paragraph each.
    text = re.sub(r"(?m)^((?:-|\d+\.) .*)\n\n(?=(?:-|\d+\.) )", r"\1\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def convert_pdf(data: bytes, detect_tables: bool = True) -> ConvertedPdf:
    """Convert PDF bytes to Markdown. Raises PdfConversionError when there is no usable text."""
    import pdfplumber

    if PDF_MAGIC not in data[:1024]:
        raise PdfConversionError("Not a PDF file")
    lines: list[Line] = []
    tables: list[tuple[int, float, str]] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = len(pdf.pages)
            meta_title = _sane_title((pdf.metadata or {}).get("Title"))
            for number, page in enumerate(pdf.pages):
                boxes: list[tuple[float, float, float, float]] = []
                if detect_tables:
                    for found in page.find_tables():
                        rendered = render_table(found.extract())
                        if rendered:
                            boxes.append(found.bbox)
                            tables.append((number, float(found.bbox[1]), rendered))
                words = page.extract_words(extra_attrs=["fontname", "size"])
                words = [w for w in words if not any(_in_box(w, box) for box in boxes)]
                lines.extend(_group_lines(words, number, float(page.height)))
                page.flush_cache()
    except PdfConversionError:
        raise
    except Exception as e:  # pdfminer raises a zoo of errors (encrypted, truncated, malformed)
        raise PdfConversionError(f"Cannot read PDF: {type(e).__name__}: {e}".rstrip(": ")) from e

    if sum(len(ln.text) for ln in lines) + sum(len(t[2]) for t in tables) < MIN_TEXT_CHARS:
        raise PdfConversionError(
            "PDF has no extractable text (scanned or image-only); OCR is not supported"
        )

    running = _running_lines(lines, pages)
    lines = [
        ln for ln in lines
        if not PAGE_NUMBER.match(ln.text) and re.sub(r"\d+", "#", ln.text.lower()) not in running
    ]
    weight: Counter[float] = Counter()
    for ln in lines:
        weight[ln.size] += len(ln.text)
    base = weight.most_common(1)[0][0] if weight else 12.0
    levels = _heading_levels(lines, base)

    order: list[tuple[int, float, Line | None, str | None]] = [(ln.page, ln.top, ln, None) for ln in lines]
    order += [(page, top, None, rendered) for page, top, rendered in tables]
    order.sort(key=lambda b: (b[0], b[1]))

    title = meta_title
    if title is None:
        first_page = [ln for ln in lines if ln.page == 0 and ln.size in levels]
        if first_page:
            title = _sane_title(max(first_page, key=lambda ln: ln.size).text)
    markdown = _markdown([(b[2], b[3]) for b in order], base, levels)
    return ConvertedPdf(markdown=markdown, title=title, pages=pages, tables=len(tables))


# --- vault import -----------------------------------------------------------


def note_name(title: str) -> str:
    """A file-system and wikilink safe note name."""
    name = re.sub(r"\s+", " ", UNSAFE_NAME.sub(" ", title)).strip(" .")
    return name[:MAX_NOTE_NAME].rstrip(" .") or "Documento"


def render_note(
    converted: ConvertedPdf, title: str, filename: str, sha256: str, tags: list[str],
    source: str | None, today: date,
) -> str:
    front = [
        "---",
        f"tags: [{', '.join(tags)}]",
        "origen: pdf",
        f"archivo: {json.dumps(filename, ensure_ascii=False)}",
    ]
    if source:
        front.append(f"fuente: {json.dumps(source, ensure_ascii=False)}")
    front += [f"paginas: {converted.pages}", f"importado: {today.isoformat()}", f"sha256: {sha256}", "---"]
    body = converted.markdown
    # The document's own title heading would repeat the note's "# title".
    first, _, rest = body.partition("\n")
    if first.lstrip("# ").strip().lower() == title.lower():
        body = rest.lstrip()
    return "\n".join(front) + f"\n# {title}\n\n{body}\n"


class DocumentImportService:
    """Converts PDFs and files them as notes in the vault's documents folder."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.obsidian = config.obsidian
        knowledge = getattr(config, "knowledge", None)
        self.coordination_dir = knowledge.state_dir if knowledge is not None and knowledge.enabled else None

    # --- destination ---------------------------------------------------------

    @property
    def vault_path(self) -> Path:
        obs = self.obsidian
        if not obs.enabled or not obs.vault_path:
            raise ValueError("Obsidian vault not configured (OBSIDIAN_VAULT_PATH)")
        vault = Path(obs.vault_path)
        if not vault.is_dir():
            raise ValueError(f"Obsidian vault not found at {vault}")
        return vault

    @property
    def folder_rel(self) -> str:
        rel = (getattr(self.obsidian, "documents_folder", None) or "").strip("/")
        if not rel:
            raise ValueError("Documents folder not configured (obsidian.documents_folder)")
        return rel

    @property
    def folder(self) -> Path:
        return self.vault_path / self.folder_rel

    @property
    def max_bytes(self) -> int:
        return int(getattr(self.obsidian, "documents_max_mb", 40)) * 1024 * 1024

    def searchable(self) -> bool:
        """Whether notes in the documents folder are picked up by the vault index."""
        obs = self.obsidian
        folders = [f.strip("/") for f in (obs.index_folders or [obs.meetings_folder.split("/")[0]])]
        rel = self.folder_rel
        return any(rel == f or rel.startswith(f + "/") for f in folders)

    def _known_hashes(self) -> dict[str, str]:
        known: dict[str, str] = {}
        if not self.folder.is_dir():
            return known
        for path in self.folder.glob("*.md"):
            try:
                with path.open(encoding="utf-8", errors="replace") as handle:
                    head = handle.read(1200)
            except OSError:
                continue
            match = SHA_LINE.search(head)
            if match:
                known[match.group(1)] = path.stem
        return known

    def status(self) -> dict[str, Any]:
        folder = self.folder
        notes = sorted(p.stem for p in folder.glob("*.md")) if folder.is_dir() else []
        known = self._known_hashes()
        pending = [p.name for p in self._pdfs() if hashlib.sha256(p.read_bytes()).hexdigest() not in known]
        return {
            "folder": self.folder_rel,
            "exists": folder.is_dir(),
            "searchable": self.searchable(),
            "documents": len(notes),
            "pending_pdfs": pending,
            "max_mb": self.max_bytes // (1024 * 1024),
        }

    def _pdfs(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")

    # --- import --------------------------------------------------------------

    def import_pdf(
        self,
        data: bytes,
        filename: str,
        title: str | None = None,
        source: str | None = None,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Convert one PDF and write its note. Raises ValueError with a readable reason."""
        if len(data) > self.max_bytes:
            raise ValueError(f"PDF larger than {self.max_bytes // (1024 * 1024)} MB")
        filename = Path(filename or "documento.pdf").name
        sha = hashlib.sha256(data).hexdigest()
        folder = self.folder
        existing = self._known_hashes().get(sha)
        if existing and not overwrite:
            return {"status": "unchanged", "note": existing, "path": f"{self.folder_rel}/{existing}.md"}

        converted = convert_pdf(data)
        final_title = _sane_title(title) or converted.title or Path(filename).stem
        name = existing or note_name(final_title)
        path = folder / f"{name}.md"
        if path.exists() and not existing and not overwrite:
            raise ValueError(f"A different note named '{name}' already exists (pass a title or overwrite)")
        tags = list(getattr(self.obsidian, "document_tags", None) or ["documento"])
        text = render_note(converted, final_title, filename, sha, tags, source, date.today())
        result: dict[str, Any] = {
            "status": "preview" if dry_run else "written",
            "note": name,
            "path": f"{self.folder_rel}/{name}.md",
            "pages": converted.pages,
            "tables": converted.tables,
            "chars": len(text),
            "searchable": self.searchable(),
        }
        if dry_run:
            result["preview"] = converted.markdown[:PREVIEW_CHARS]
            return result
        with vault_lock(self.coordination_dir):
            folder.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        logger.info("PDF imported to vault", note=result["path"], pages=converted.pages, chars=len(text))
        return result

    def import_pending(self) -> dict[str, Any]:
        """Convert PDFs dropped into the documents folder that have no note yet."""
        imported: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        unchanged = 0
        for pdf in self._pdfs():
            try:
                result = self.import_pdf(pdf.read_bytes(), pdf.name)
            except (ValueError, OSError) as e:
                errors.append({"file": pdf.name, "error": str(e)})
                continue
            if result["status"] == "unchanged":
                unchanged += 1
            else:
                imported.append({k: result[k] for k in ("note", "pages", "chars")})
        return {"folder": self.folder_rel, "imported": imported, "unchanged": unchanged, "errors": errors}

    async def import_source(
        self, source: str, title: str | None = None, overwrite: bool = False, dry_run: bool = False
    ) -> dict[str, Any]:
        """Import from an http(s) URL or a PDF path inside the vault."""
        if re.match(r"^https?://", source, re.IGNORECASE):
            data, filename = await self._download(source)
            origin: str | None = source
        else:
            vault = self.vault_path.resolve()
            path = (vault / source).resolve()
            if not path.is_relative_to(vault):
                raise ValueError("Only PDFs inside the vault (or http(s) URLs) can be imported")
            if not path.is_file() or path.suffix.lower() != ".pdf":
                raise ValueError(f"PDF not found in the vault: {source}")
            data, filename, origin = path.read_bytes(), path.name, None
        return await asyncio.to_thread(
            self.import_pdf, data, filename, title=title, source=origin, overwrite=overwrite, dry_run=dry_run
        )

    async def _download(self, url: str) -> tuple[bytes, str]:
        chunks: list[bytes] = []
        size = 0
        async with (
            httpx.AsyncClient(follow_redirects=True, timeout=60) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code >= 400:
                raise ValueError(f"Download failed: HTTP {response.status_code}")
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self.max_bytes:
                    raise ValueError(f"PDF larger than {self.max_bytes // (1024 * 1024)} MB")
                chunks.append(chunk)
            final_url = str(response.url)
        name = Path(httpx.URL(final_url).path).name or "documento.pdf"
        return b"".join(chunks), name if name.lower().endswith(".pdf") else f"{name}.pdf"
