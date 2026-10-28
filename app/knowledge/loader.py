"""Load trusted text documents from disk or an upload."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}
_TEXT_CONTENT_TYPES = {"text/plain", "text/markdown", "text/x-markdown", "application/octet-stream"}
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(?P<body>.*?)\n---[ \t]*(?:\n|\Z)",
    re.DOTALL,
)


class DocumentLoadError(ValueError):
    """The supplied document could not be safely loaded."""


@dataclass(frozen=True, slots=True)
class LoadedDocument:
    title: str
    source: str
    content: str
    content_type: str = "text/markdown"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def normalize_text(raw: str) -> str:
    """Normalize line endings, BOMs and trailing whitespace."""

    raw = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in raw:
        raise DocumentLoadError("binary data is not supported")
    lines = [line.rstrip() for line in raw.split("\n")]
    return "\n".join(lines).strip()


def _extract_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse a small, dependency-free ``key: value`` frontmatter block."""

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

    body = text[match.end() :].lstrip("\n")
    return metadata, body


def _infer_title(filename: str, content: str) -> str:
    match = _H1_RE.search(content)
    if match:
        return match.group(1).strip()[:200]
    for line in content.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:200]
    fallback = Path(filename).stem.replace("_", " ").replace("-", " ").strip()[:200]
    return fallback or "Untitled document"


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentLoadError("document must be UTF-8 encoded text") from exc


def load_bytes(
    filename: str,
    data: bytes,
    *,
    content_type: str | None = None,
    max_bytes: int = 2_000_000,
) -> LoadedDocument:
    """Load an uploaded document while enforcing type and size limits."""

    clean_name = Path(filename or "upload.txt").name
    suffix = Path(clean_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentLoadError(f"unsupported file type: {suffix or 'unknown'}")
    if not data:
        raise DocumentLoadError("document is empty")
    if len(data) > max_bytes:
        raise DocumentLoadError(f"document exceeds the {max_bytes} byte limit")

    resolved_type = (content_type or "").split(";", 1)[0].strip().lower()
    if resolved_type and resolved_type not in _TEXT_CONTENT_TYPES:
        guessed = mimetypes.guess_type(clean_name)[0]
        if guessed not in _TEXT_CONTENT_TYPES:
            raise DocumentLoadError(f"unsupported content type: {resolved_type}")

    normalized = normalize_text(_decode(data))
    frontmatter, content = _extract_frontmatter(normalized)
    if not content:
        raise DocumentLoadError("document is empty after normalization")

    return LoadedDocument(
        title=str(frontmatter.get("title") or _infer_title(clean_name, content))[:200],
        source=clean_name,
        content=content,
        content_type="text/markdown" if suffix in {".md", ".markdown"} else "text/plain",
        metadata={
            **frontmatter,
            "filename": clean_name,
            "extension": suffix,
            "size_bytes": len(data),
            "origin": "upload",
        },
    )


def load_file(path: str | Path, *, max_bytes: int = 2_000_000) -> LoadedDocument:
    """Load one UTF-8 markdown or text file."""

    file_path = Path(path)
    if not file_path.is_file():
        raise DocumentLoadError(f"file not found: {file_path}")
    if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise DocumentLoadError(f"unsupported file type: {file_path.suffix or 'unknown'}")
    size = file_path.stat().st_size
    if size > max_bytes:
        raise DocumentLoadError(f"document exceeds the {max_bytes} byte limit")

    normalized = normalize_text(_decode(file_path.read_bytes()))
    frontmatter, content = _extract_frontmatter(normalized)
    if not content:
        raise DocumentLoadError(f"document is empty: {file_path.name}")

    return LoadedDocument(
        title=str(frontmatter.get("title") or _infer_title(file_path.name, content))[:200],
        source=file_path.name,
        content=content,
        content_type="text/markdown"
        if file_path.suffix.lower() in {".md", ".markdown"}
        else "text/plain",
        metadata={
            **frontmatter,
            "filename": file_path.name,
            "extension": file_path.suffix.lower(),
            "size_bytes": size,
            "origin": "disk",
        },
    )


def load_directory(
    path: str | Path,
    *,
    recursive: bool = True,
    max_bytes: int = 2_000_000,
) -> list[LoadedDocument]:
    """Load every supported document in deterministic filename order."""

    directory = Path(path)
    if not directory.is_dir():
        raise DocumentLoadError(f"directory not found: {directory}")

    iterator = directory.rglob("*") if recursive else directory.glob("*")
    files = sorted(
        (
            candidate
            for candidate in iterator
            if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES
        ),
        key=lambda candidate: candidate.relative_to(directory).as_posix(),
    )
    documents: list[LoadedDocument] = []
    for file_path in files:
        loaded = load_file(file_path, max_bytes=max_bytes)
        source = file_path.relative_to(directory).as_posix()
        metadata = {**loaded.metadata, "relative_path": source, "origin": "sample_corpus"}
        documents.append(
            LoadedDocument(
                title=loaded.title,
                source=source,
                content=loaded.content,
                content_type=loaded.content_type,
                metadata=metadata,
            )
        )
    return documents
