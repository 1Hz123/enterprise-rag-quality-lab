"""Load trusted text documents and text-based PDFs from disk or an upload.

Frontmatter is parsed with a small, dependency-free ``key: value`` reader. Known
date fields (``effective_from`` / ``effective_until`` / ``published_at`` /
``updated_at``) are normalized to ISO-8601 and additionally promoted to typed
``date`` attributes on :class:`LoadedDocument`, so callers can filter by validity
window without re-parsing strings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf"}
_TEXT_CONTENT_TYPES = {"text/plain", "text/markdown", "text/x-markdown", "application/octet-stream"}
_PDF_CONTENT_TYPES = {"application/pdf", "application/octet-stream"}
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(?P<body>.*?)\n---[ \t]*(?:\n|\Z)",
    re.DOTALL,
)
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".audit",
    "dist",
    "build",
}

#: Frontmatter keys that are treated as dates and normalized to ISO-8601.
_DATE_KEYS = ("effective_from", "effective_until", "published_at", "updated_at")

#: Date keys additionally promoted to typed attributes on ``LoadedDocument``.
_PROMOTED_DATE_KEYS = frozenset({"effective_from", "effective_until"})

#: Fallback formats tried after the separator cleanup in :func:`_coerce_date`.
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


class DocumentLoadError(ValueError):
    """The supplied document could not be safely loaded."""


@dataclass(frozen=True, slots=True)
class LoadedDocument:
    title: str
    source: str
    content: str
    content_type: str = "text/markdown"
    effective_from: date | None = None
    effective_until: date | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            self.effective_from
            and self.effective_until
            and self.effective_from > self.effective_until
        ):
            logger.warning(
                "文档 %s 的生效区间倒置：effective_from=%s 晚于 effective_until=%s",
                self.source,
                self.effective_from,
                self.effective_until,
            )

    @property
    def checksum(self) -> str:
        """Content-only fingerprint (unchanged semantics from the original loader)."""

        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def fingerprint(self) -> str:
        """Identity that also distinguishes revised versions sharing the same body.

        Two revisions of a policy with identical prose but different validity
        windows produce the same :attr:`checksum`; dedupe on this instead.
        """

        payload = "|".join(
            (
                self.source,
                self.content,
                self.effective_from.isoformat() if self.effective_from else "",
                self.effective_until.isoformat() if self.effective_until else "",
                json.dumps(self.metadata, ensure_ascii=False, sort_keys=True, default=str),
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def is_effective_on(self, on: date | None = None) -> bool:
        """Return whether the document's validity window covers ``on`` (default today)."""

        on = on or date.today()
        if self.effective_from and on < self.effective_from:
            return False
        return not (self.effective_until and on > self.effective_until)


def normalize_text(raw: str) -> str:
    """Normalize line endings, BOMs and trailing whitespace."""

    raw = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in raw:
        raise DocumentLoadError("binary data is not supported")
    lines = [line.rstrip() for line in raw.split("\n")]
    return "\n".join(lines).strip()


def _iter_markdown_lines(text: str) -> Iterator[tuple[str, bool]]:
    """Yield ``(line, is_inside_or_part_of_fence)`` for safe heading detection."""

    fence: str | None = None
    for line in text.splitlines():
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            yield line, True
            continue
        yield line, fence is not None


def _extract_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse a small, dependency-free ``key: value`` frontmatter block.

    A leading ``---`` is only treated as frontmatter when the block contains at
    least one real colon-separated scalar. This avoids swallowing a document
    that starts with a Markdown horizontal rule.
    """

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    metadata: dict[str, str] = {}
    for line in match.group("body").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        parsed = value.strip()
        if len(parsed) >= 2 and parsed[0] == parsed[-1] and parsed[0] in {'"', "'"}:
            parsed = parsed[1:-1]
        metadata[key] = parsed

    if not metadata:
        logger.debug("leading --- block has no key: value lines; treating it as body")
        return {}, text

    body = text[match.end() :].lstrip("\n")
    return metadata, body


def _coerce_date(value: str) -> date | None:
    """Parse a date written in any of the common frontmatter styles.

    Accepts ISO-8601 (optionally with time and offset), ``2026/1/1``,
    ``2026.1.1``, ``20260101`` and Chinese ``2026年1月1日``.
    """

    raw = value.strip()
    if not raw:
        return None

    # 1) Strict ISO-8601 first, so offsets and timestamps are handled properly.
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    # 2) Collapse Chinese units and alternative separators down to "-".
    cleaned = raw.replace("年", "-").replace("月", "-").replace("日", "")
    cleaned = re.sub(r"[/._]", "-", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 3) Bare 8-digit form: 20260101 -> 2026-01-01.
    if re.fullmatch(r"\d{8}", cleaned):
        cleaned = f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:]}"

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_dates(
    frontmatter: dict[str, str],
) -> tuple[dict[str, Any], date | None, date | None]:
    """Normalize known date fields.

    Returns ``(metadata, effective_from, effective_until)``. Parseable values are
    rewritten to ISO-8601 in ``metadata`` (which also makes plain string sorting
    chronological). Unparseable values keep their raw text and are left out of
    the typed attributes, with a warning so data-quality issues stay visible.
    """

    meta: dict[str, Any] = dict(frontmatter)
    if "effective_until" not in meta and "effective_to" in meta:
        meta["effective_until"] = meta["effective_to"]
    meta.pop("effective_to", None)
    promoted: dict[str, date] = {}

    for key in _DATE_KEYS:
        if key not in meta:
            continue
        parsed = _coerce_date(str(meta[key]))
        if parsed is None:
            logger.warning("日期字段 %s=%r 无法解析，保留原值", key, meta[key])
            continue
        meta[key] = parsed.isoformat()
        if key in _PROMOTED_DATE_KEYS:
            promoted[key] = parsed

    return meta, promoted.get("effective_from"), promoted.get("effective_until")


def _infer_title(filename: str, content: str) -> str:
    for line, is_fenced in _iter_markdown_lines(content):
        if is_fenced:
            continue
        match = _H1_RE.match(line)
        if match:
            return match.group(1).strip()[:200]
    for line, is_fenced in _iter_markdown_lines(content):
        if is_fenced:
            continue
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:200]
    fallback = Path(filename).stem.replace("_", " ").replace("-", " ").strip()[:200]
    return fallback or "Untitled document"


def _decode(data: bytes) -> tuple[str, str]:
    """Decode UTF-8 first, then common Chinese enterprise fallback GB18030."""

    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise DocumentLoadError("document must be UTF-8 or GB18030 encoded text")


_CJK_CHAR_RE = re.compile(r"[\u3400-\u9fff]")


def _join_pdf_lines(text: str) -> str:
    """Rejoin PDF hard-wrapped lines into paragraphs before sentence splitting.

    PDF extractors often break a Chinese sentence at an arbitrary column. Keeping
    those newlines makes the extractive generator select fragments such as
    “人，跟踪了 4.74 年” instead of the full study sentence.
    """

    paragraphs: list[str] = []
    buffer = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if buffer:
                paragraphs.append(buffer)
                buffer = ""
            paragraphs.append("")
            continue

        starts_block = line.startswith(("#", "- ", "•", "* ", "|"))
        if starts_block:
            if buffer:
                paragraphs.append(buffer)
                buffer = ""
            buffer = line
            continue

        if not buffer:
            buffer = line
            continue

        separator = "" if (
            _CJK_CHAR_RE.search(buffer[-1]) and _CJK_CHAR_RE.search(line[0])
        ) else " "
        buffer = f"{buffer}{separator}{line}"

    if buffer:
        paragraphs.append(buffer)
    return "\n".join(paragraphs)


def _extract_pdf_text(
    data: bytes,
    *,
    max_pages: int,
) -> tuple[str, int, str | None]:
    """Extract selectable text from a PDF; scanned PDFs must use OCR upstream."""

    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise DocumentLoadError("PDF could not be parsed") from exc

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise DocumentLoadError("encrypted PDF is not supported") from exc
        if reader.is_encrypted:
            raise DocumentLoadError("encrypted PDF is not supported")

    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise DocumentLoadError("PDF page list could not be read") from exc
    if page_count > max_pages:
        raise DocumentLoadError(
            f"PDF has {page_count} pages, exceeding the {max_pages} page limit"
        )

    page_texts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            raise DocumentLoadError("PDF page text extraction failed") from exc
        text = text.strip()
        if text:
            page_texts.append(text)

    if not page_texts:
        raise DocumentLoadError(
            "PDF contains no extractable text; scanned PDFs require OCR"
        )

    pdf_title: str | None = None
    try:
        raw_title = reader.metadata.title if reader.metadata else None
        if raw_title:
            pdf_title = str(raw_title).strip()[:200] or None
    except Exception:
        pdf_title = None

    return "\n\n".join(page_texts), page_count, pdf_title


def _pdf_loaded_document(
    *,
    filename: str,
    data: bytes,
    max_pages: int,
    origin: str,
) -> LoadedDocument:
    content, page_count, pdf_title = _extract_pdf_text(data, max_pages=max_pages)
    normalized = normalize_text(_join_pdf_lines(content))
    if not normalized:
        raise DocumentLoadError("PDF is empty after normalization")
    title = pdf_title or _infer_title(filename, normalized)
    return LoadedDocument(
        title=title,
        source=filename,
        content=normalized,
        content_type="application/pdf",
        metadata={
            "filename": filename,
            "extension": ".pdf",
            "size_bytes": len(data),
            "encoding": "pdf-text",
            "page_count": page_count,
            "pdf_title": pdf_title,
            "origin": origin,
        },
    )


def load_bytes(
    filename: str,
    data: bytes,
    *,
    content_type: str | None = None,
    max_bytes: int = 2_000_000,
    max_pdf_bytes: int = 20_000_000,
    max_pdf_pages: int = 500,
) -> LoadedDocument:
    """Load an uploaded document while enforcing type and size limits."""

    clean_name = Path(filename or "upload.txt").name
    suffix = Path(clean_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentLoadError(f"unsupported file type: {suffix or 'unknown'}")
    if not data:
        raise DocumentLoadError("document is empty")
    size_limit = max_pdf_bytes if suffix == ".pdf" else max_bytes
    if len(data) > size_limit:
        raise DocumentLoadError(f"document exceeds the {size_limit} byte limit")

    allowed_types = _PDF_CONTENT_TYPES if suffix == ".pdf" else _TEXT_CONTENT_TYPES
    resolved_type = (content_type or "").split(";", 1)[0].strip().lower()
    if resolved_type and resolved_type not in allowed_types:
        guessed = mimetypes.guess_type(clean_name)[0]
        if guessed not in allowed_types:
            raise DocumentLoadError(f"unsupported content type: {resolved_type}")

    if suffix == ".pdf":
        return _pdf_loaded_document(
            filename=clean_name,
            data=data,
            max_pages=max_pdf_pages,
            origin="upload",
        )

    decoded, encoding = _decode(data)
    normalized = normalize_text(decoded)
    frontmatter, content = _extract_frontmatter(normalized)
    if not content:
        raise DocumentLoadError("document is empty after normalization")

    meta, effective_from, effective_until = _normalize_dates(frontmatter)

    return LoadedDocument(
        title=str(meta.get("title") or _infer_title(clean_name, content))[:200],
        source=clean_name,
        content=content,
        content_type="text/markdown" if suffix in {".md", ".markdown"} else "text/plain",
        effective_from=effective_from,
        effective_until=effective_until,
        metadata={
            **meta,
            "filename": clean_name,
            "extension": suffix,
            "size_bytes": len(data),
            "encoding": encoding,
            "origin": "upload",
        },
    )


def load_file(
    path: str | Path,
    *,
    max_bytes: int = 2_000_000,
    max_pdf_bytes: int = 20_000_000,
    max_pdf_pages: int = 500,
) -> LoadedDocument:
    """Load one supported markdown, text or PDF file."""

    file_path = Path(path)
    if not file_path.is_file():
        raise DocumentLoadError(f"file not found: {file_path}")
    if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise DocumentLoadError(f"unsupported file type: {file_path.suffix or 'unknown'}")
    size = file_path.stat().st_size
    size_limit = max_pdf_bytes if file_path.suffix.lower() == ".pdf" else max_bytes
    if size > size_limit:
        raise DocumentLoadError(f"document exceeds the {size_limit} byte limit")

    if file_path.suffix.lower() == ".pdf":
        return _pdf_loaded_document(
            filename=file_path.name,
            data=file_path.read_bytes(),
            max_pages=max_pdf_pages,
            origin="disk",
        )

    decoded, encoding = _decode(file_path.read_bytes())
    normalized = normalize_text(decoded)
    frontmatter, content = _extract_frontmatter(normalized)
    if not content:
        raise DocumentLoadError(f"document is empty: {file_path.name}")

    meta, effective_from, effective_until = _normalize_dates(frontmatter)

    return LoadedDocument(
        title=str(meta.get("title") or _infer_title(file_path.name, content))[:200],
        source=file_path.name,
        content=content,
        content_type="text/markdown"
        if file_path.suffix.lower() in {".md", ".markdown"}
        else "text/plain",
        effective_from=effective_from,
        effective_until=effective_until,
        metadata={
            **meta,
            "filename": file_path.name,
            "extension": file_path.suffix.lower(),
            "size_bytes": size,
            "encoding": encoding,
            "origin": "disk",
        },
    )


def _is_ignored_relative(relative: Path) -> bool:
    parts = relative.parts
    if any(part.startswith(".") for part in parts):
        return True
    return any(part in _IGNORED_DIRS for part in parts)


def load_directory(
    path: str | Path,
    *,
    recursive: bool = True,
    max_bytes: int = 2_000_000,
    max_pdf_bytes: int = 20_000_000,
    max_pdf_pages: int = 500,
    strict: bool = False,
) -> list[LoadedDocument]:
    """Load every supported document in deterministic filename order.

    Hidden/VCS/virtualenv/build directories are skipped. A broken file does not
    abort the batch unless ``strict=True``; skipped files are logged with their
    relative path and reason.
    """

    directory = Path(path)
    if not directory.is_dir():
        raise DocumentLoadError(f"directory not found: {directory}")

    iterator = directory.rglob("*") if recursive else directory.glob("*")
    files = sorted(
        (
            candidate
            for candidate in iterator
            if candidate.is_file()
            and candidate.suffix.lower() in SUPPORTED_SUFFIXES
            and not _is_ignored_relative(candidate.relative_to(directory))
        ),
        key=lambda candidate: candidate.relative_to(directory).as_posix(),
    )
    documents: list[LoadedDocument] = []
    for file_path in files:
        source = file_path.relative_to(directory).as_posix()
        try:
            loaded = load_file(
                file_path,
                max_bytes=max_bytes,
                max_pdf_bytes=max_pdf_bytes,
                max_pdf_pages=max_pdf_pages,
            )
        except DocumentLoadError as exc:
            if strict:
                raise
            logger.warning("skipping unreadable document %s: %s", source, exc)
            continue
        metadata = {**loaded.metadata, "relative_path": source, "origin": "sample_corpus"}
        documents.append(
            LoadedDocument(
                title=loaded.title,
                source=source,
                content=loaded.content,
                content_type=loaded.content_type,
                effective_from=loaded.effective_from,
                effective_until=loaded.effective_until,
                metadata=metadata,
            )
        )
    return documents
