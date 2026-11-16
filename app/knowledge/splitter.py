"""Section-aware, size-bounded text chunking.

The splitter keeps Markdown heading hierarchy as metadata, packs paragraphs up
to a character budget and carries a small overlap between adjacent chunks. It
ignores headings inside fenced code blocks and is deterministic so retrieval
and evaluation results are reproducible.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace

from app.knowledge.loader import LoadedDocument

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
# Chinese punctuation always ends a sentence; a Latin period ends one only
# when followed by whitespace, so decimals such as 3.5 are not split.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.!?])\s+")
_OVERLAP_BOUNDARY_RE = re.compile(r"[。！？!?；;]|(?<=[.!?])\s+|\n")
_PARAGRAPH_RE = re.compile(r"\n{2,}")


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    ordinal: int
    section: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    """Cheap token estimate that works for Chinese and Latin text."""

    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9]+", text))
    return max(1, cjk + int(latin * 1.3))


def _iter_markdown_lines(text: str) -> Iterator[tuple[str, bool]]:
    """Yield ``(line, is_inside_fence)`` without treating code comments as headings."""

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


def _sections(text: str) -> list[tuple[str | None, str, str | None]]:
    """Return ``(heading, body, heading_path)`` sections."""

    sections: list[tuple[str | None, str, str | None]] = []
    stack: list[tuple[int, str]] = []
    heading: str | None = None
    heading_path: str | None = None
    buffer: list[str] = []

    for line, is_fenced in _iter_markdown_lines(text):
        match = None if is_fenced else _HEADING_RE.match(line)
        if match:
            body = "\n".join(buffer).strip()
            if body or heading is not None:
                sections.append((heading, body, heading_path))
            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            heading = line.strip()
            heading_path = " > ".join(item for _, item in stack)
            buffer = []
        else:
            buffer.append(line)

    body = "\n".join(buffer).strip()
    if body or heading is not None:
        sections.append((heading, body, heading_path))
    return sections or [(None, text.strip(), None)]


def _split_long_block(block: str, limit: int) -> list[str]:
    """Split an oversized paragraph on sentence boundaries, then hard-cut."""

    if len(block) <= limit:
        return [block]

    pieces: list[str] = []
    sentences = [part.strip() for part in _SENTENCE_BOUNDARY_RE.split(block) if part.strip()]
    current = ""
    for sentence in sentences:
        while len(sentence) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if not sentence:
            continue
        candidate = f"{current}{sentence}" if current else sentence
        if len(candidate) > limit:
            if current:
                pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces or [block[:limit]]


def _overlap_tail(text: str, overlap: int) -> str:
    """Return a suffix of at most ``overlap`` characters starting at a boundary."""

    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    boundaries = [match.end() for match in _OVERLAP_BOUNDARY_RE.finditer(tail)]
    if boundaries:
        tail = tail[boundaries[-1] :]
    else:
        logger.debug("no sentence boundary found in overlap window; using suffix")
    return tail.lstrip()


def _emit_chunk(
    chunks: list[Chunk],
    current: list[str],
    ordinal: int,
    section_name: str | None,
    heading_path: str | None,
) -> int:
    """Append the current buffer as a chunk and return the next ordinal."""

    if not current:
        return ordinal
    chunk_text = "\n\n".join(current).strip()
    if not chunk_text:
        return ordinal
    chunks.append(
        Chunk(
            text=chunk_text,
            ordinal=ordinal,
            section=section_name,
            metadata={
                "char_count": len(chunk_text),
                "heading_path": heading_path,
            },
        )
    )
    return ordinal + 1


def split_text(text: str, *, chunk_size: int = 760, overlap: int = 110) -> list[Chunk]:
    """Split normalized text into deterministic, bounded chunks."""

    if chunk_size < 50:
        raise ValueError("chunk_size must be at least 50 characters")
    if overlap < 0 or overlap > chunk_size // 2:
        raise ValueError("overlap must be between 0 and half of chunk_size")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    chunks: list[Chunk] = []
    ordinal = 0

    for heading, body, heading_path in _sections(normalized):
        if not body:
            continue
        section_name = _HEADING_RE.match(heading).group(2).strip() if heading else None
        blocks = [block.strip() for block in _PARAGRAPH_RE.split(body) if block.strip()]
        expanded: list[str] = []
        for block in blocks:
            expanded.extend(_split_long_block(block, chunk_size))

        current: list[str] = []
        current_len = 0

        for piece in expanded:
            piece = piece.strip()
            if not piece:
                continue
            extra = 2 if current else 0
            if current and current_len + extra + len(piece) > chunk_size:
                previous_text = "\n\n".join(current)
                ordinal = _emit_chunk(
                    chunks,
                    current,
                    ordinal,
                    section_name,
                    heading_path,
                )
                current = []
                current_len = 0
                tail = _overlap_tail(previous_text, overlap)
                max_tail = max(0, chunk_size - len(piece) - 2)
                if len(tail) > max_tail:
                    tail = tail[-max_tail:] if max_tail else ""
                if tail:
                    current = [tail]
                    current_len = len(tail)

            extra = 2 if current else 0
            if current and current_len + extra + len(piece) > chunk_size:
                # Defensive fallback: never emit a chunk larger than the budget.
                logger.debug("dropping overlap to respect chunk_size")
                current = []
                current_len = 0
            current.append(piece)
            current_len += (2 if len(current) > 1 else 0) + len(piece)

        ordinal = _emit_chunk(chunks, current, ordinal, section_name, heading_path)

    return chunks


def split_document(
    document: LoadedDocument,
    *,
    chunk_size: int = 760,
    overlap: int = 110,
) -> list[Chunk]:
    """Split a loaded document and inherit its provenance into every chunk."""

    chunks = split_text(document.content, chunk_size=chunk_size, overlap=overlap)
    provenance: dict[str, object] = {
        "source": document.source,
        "title": document.title,
        "checksum": document.checksum,
        "document_metadata": dict(document.metadata),
        "effective_from": (
            document.effective_from.isoformat()
            if document.effective_from
            else document.metadata.get("effective_from")
        ),
        "effective_until": (
            document.effective_until.isoformat()
            if document.effective_until
            else document.metadata.get("effective_until")
        ),
    }
    for key in ("policy_key", "version", "relative_path"):
        if key in document.metadata:
            provenance[key] = document.metadata[key]
    return [
        replace(chunk, metadata={**chunk.metadata, **provenance})
        for chunk in chunks
    ]
