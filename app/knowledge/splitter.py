"""Section-aware, size-bounded text chunking.

The splitter keeps markdown headings as section metadata, packs paragraphs up
to a character budget and carries a small overlap between adjacent chunks. It
is intentionally deterministic so retrieval and evaluation results are
reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;])\s*")
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


def _sections(text: str) -> list[tuple[str | None, str]]:
    """Return ``(heading, body)`` pairs, preserving preamble text."""

    lines = text.split("\n")
    sections: list[tuple[str | None, str]] = []
    heading: str | None = None
    buffer: list[str] = []

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            body = "\n".join(buffer).strip()
            if body or heading is not None:
                sections.append((heading, body))
            heading = line.strip()
            buffer = []
        else:
            buffer.append(line)

    body = "\n".join(buffer).strip()
    if body or heading is not None:
        sections.append((heading, body))
    return sections or [(None, text.strip())]


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
    boundary = max(tail.rfind("。"), tail.rfind("！"), tail.rfind("？"), tail.rfind("\n"))
    if boundary >= 0:
        tail = tail[boundary + 1 :]
    return tail.lstrip()


def _emit_chunk(
    chunks: list[Chunk],
    current: list[str],
    ordinal: int,
    section_name: str | None,
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
            metadata={"char_count": len(chunk_text)},
        )
    )
    return ordinal + 1


def split_text(text: str, *, chunk_size: int = 760, overlap: int = 110) -> list[Chunk]:
    """Split normalized text into deterministic, bounded chunks."""

    if chunk_size < 50:
        raise ValueError("chunk_size must be at least 50 characters")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be between 0 and chunk_size - 1")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    chunks: list[Chunk] = []
    ordinal = 0

    for heading, body in _sections(normalized):
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
                ordinal = _emit_chunk(chunks, current, ordinal, section_name)
                current = []
                current_len = 0
                tail = _overlap_tail(previous_text, overlap)
                max_tail = max(0, chunk_size - len(piece) - 2)
                tail = tail[-max_tail:] if max_tail else ""
                if tail:
                    current = [tail]
                    current_len = len(tail)

            extra = 2 if current else 0
            if current and current_len + extra + len(piece) > chunk_size:
                # Defensive fallback: never emit a chunk larger than the budget.
                current = []
                current_len = 0
            current.append(piece)
            current_len += (2 if len(current) > 1 else 0) + len(piece)

        ordinal = _emit_chunk(chunks, current, ordinal, section_name)

    return chunks
